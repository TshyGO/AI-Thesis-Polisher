import os
import re
import logging
import win32com.client

class DocumentProcessor:
    """
    负责封装使用 win32com 操作 Word 文档的底座。
    处理安全读取、修订写入、章节结构提取（解析 Heading）。
    """
    def __init__(self, debug: bool = False):
        self.doc = None
        self.logger = logging.getLogger("DocumentProcessor")
        self.word = None
        self._init_word(debug)

    def _init_word(self, debug: bool):
        """初始化 Word.Application 进程"""
        self.logger.info("正在启动 Word 进程...")
        try:
            # 使用 DispatchEx 保证我们在独立进程中操作，避免互相干扰
            self.word = win32com.client.DispatchEx("Word.Application")
            self.word.Visible = debug
            self.word.DisplayAlerts = 0
        except Exception as e:
            self.logger.error(f"启动 Word 进程失败: {e}")
            raise RuntimeError(f"无法启动 Microsoft Word, 请确保本机已合法安装且可用。异常: {e}")

    def open_document(self, docx_path: str, read_only: bool = True, track_revisions: bool = False):
        """打开文档，可配置模式和修订追踪"""
        abs_path = os.path.abspath(docx_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"找不到文档: {abs_path}")

        try:
            self.doc = self.word.Documents.Open(
                abs_path,
                ReadOnly=read_only,
                AddToRecentFiles=False
            )
            # 设置修订开关
            self.doc.TrackRevisions = track_revisions
            self.logger.info(f"已成功打开文档: {os.path.basename(abs_path)}")
        except Exception as e:
            self.logger.error(f"无法打开文档: {e}")
            raise RuntimeError(f"无法打开文档，可能被占用或损坏。异常: {e}")

    def save(self):
        """保存文档"""
        if self.doc:
            self.doc.Save()

    def close(self):
        """
        关闭并退出。
        【注意】：这里默认 SaveChanges=False。
        如果使用 with context manager 写修订，务必在退出前手动调用 save()，否则所有未保存的修订会静默丢失！
        """
        if self.doc:
            try:
                self.doc.Close(SaveChanges=False)
                self.doc = None
            except:
                pass
        if self.word:
            try:
                self.word.Quit()
                self.word = None
            except:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_total_paragraphs(self) -> int:
        if not self.doc:
            return 0
        return self.doc.Paragraphs.Count

    def get_paragraph_text(self, global_idx: int) -> str:
        """从 1-based 的索引获取段落干净文本"""
        if not self.doc or global_idx < 1 or global_idx > self.get_total_paragraphs():
            return ""
        try:
            text = self.doc.Paragraphs(global_idx).Range.Text
            text = text.strip()
            # 过滤不可见控制字符和回车换行等
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            text = text.replace('\x07', '').replace('\r', '').replace('\n', '')
            return text
        except:
            return ""

    def get_neighbor_text(self, center_idx: int, direction: str = "prev", limit: int = 2) -> str:
        """
        获取当前段落的相邻上下文（前文或后文），过滤掉字数较少的无关段落。
        用于在给大模型提示时提供上下文窗口。
        """
        if not self.doc:
            return ""
        
        paragraphs = []
        total = self.get_total_paragraphs()
        
        if direction == "prev":
            # 往前找，最多找前5个段落
            rng = range(center_idx - 1, max(0, center_idx - 6), -1)
        else:
            # 往后找，最多找后5个段落
            rng = range(center_idx + 1, min(total + 1, center_idx + 6))
            
        for j in rng:
            t = self.get_paragraph_text(j)
            if len(t) >= 15:  # 过滤掉字数过少的标题或空行
                paragraphs.append(t)
                if len(paragraphs) >= limit:
                    break
                    
        if direction == "prev":
            paragraphs.reverse()
            
        return "\n".join(paragraphs)
            
    def get_paragraph_style_name(self, global_idx: int) -> str:
        """获取段落样式名称（如 Heading 1, 标题 1 等）"""
        if not self.doc or global_idx < 1 or global_idx > self.get_total_paragraphs():
            return ""
        try:
            style = self.doc.Paragraphs(global_idx).Style
            return getattr(style, "NameLocal", getattr(style, "Name", ""))
        except:
            return ""

    def parse_chapters(self) -> list:
        """
        自动解析文档中的大纲（Heading 层级），替代硬编码段落逻辑。
        返回列表: [{"name": "绪论", "start": 1, "end": 200}, ...]
        """
        if not self.doc:
            return []
            
        total = self.get_total_paragraphs()
        chapters = []
        current_chapter_name = "引言/前言(未命名的初始章节)"
        current_start = 1
        
        self.logger.info("正在扫描文档章节结构...")
        
        for i in range(1, total + 1):
            style_name = self.get_paragraph_style_name(i).strip().lower()
            # 严格匹配一级标题，防止匹配到 Heading 10, Heading 11
            valid_headings = ("heading 1", "标题 1", "heading1", "标题1")
            if style_name in valid_headings:
                text = self.get_paragraph_text(i)[:50] # 截取前 50 字作为章节名
                if not text:
                    text = f"章节_{i}"
                
                # 关闭上一个章节
                if i > 1:
                    chapters.append({
                        "name": current_chapter_name,
                        "start": current_start,
                        "end": i - 1
                    })
                
                # 开启新章节
                current_chapter_name = text
                current_start = i
                
        # 收尾最后一个章节
        chapters.append({
            "name": current_chapter_name,
            "start": current_start,
            "end": total
        })
        
        return chapters

    def apply_tracked_revision(self, global_idx: int, old_text: str, new_text: str) -> bool:
        """
        在开启了 修订追踪 的情况下，将段落里的 old_text 替换为 new_text。
        由于修改后可能会造成 Range.End 变动，安全起见我们会在指定段落(1-based)内使用 Find 替换一次。
        如果有上下标公式等特征，则进行拦截。
        返回是否替换成功。
        """
        if not self.doc:
            return False
            
        if not old_text or len(old_text) < 2 or len(old_text) > 240 or old_text == new_text:
            return False
            
        try:
            p = self.doc.Paragraphs(global_idx)
            rng = p.Range.Duplicate
            rng.Find.ClearFormatting()
            rng.Find.Text = old_text
            rng.Find.Forward = True
            rng.Find.Wrap = 0 # wdFindStop (只在本段落中找)
            
            found = rng.Find.Execute()
            if found and rng.End <= p.Range.End:
                # 检查特殊的格式（如果包含上下标，可能会破坏化学式或引用）
                font_super = getattr(rng.Font, 'Superscript', 0)
                font_sub = getattr(rng.Font, 'Subscript', 0)
                # 9999999 或 True 代表有包含
                if font_super in (9999999, True, 1) or font_sub in (9999999, True, 1):
                    self.logger.info(f"    [格式拦截] 含有上下标跳过: {old_text[:15]}")
                    return False
                    
                if rng.OMaths.Count > 0:
                    self.logger.info(f"    [格式拦截] 含有公式跳过: {old_text[:15]}")
                    return False
                
                # 执行写入 (Word 会自动打上修订标记)
                rng.Text = new_text
                return True
                
            return False
        except Exception as e:
            self.logger.warning(f"  [写入异常] 段落 {global_idx}: {e}")
            return False
