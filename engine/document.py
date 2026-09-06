import os
import re
import logging
import xml.etree.ElementTree as ET
import pythoncom
import win32com.client
from engine.sentences import ParagraphSnapshot, utf16_length
from engine.patches import sentence_patch_plan, PatchResult, PatchRollbackError

class DocumentProcessor:
    """
    负责封装使用 win32com 操作 Word 文档的底座。
    处理安全读取、修订写入、章节结构提取（解析 Heading）。
    """
    def __init__(self, debug: bool = False):
        pythoncom.CoInitialize()
        self.doc = None
        self.logger = logging.getLogger("DocumentProcessor")
        self.word = None
        self._cached_texts = None
        self._init_word(debug)

    def _init_word(self, debug: bool):
        """初始化 Word.Application 进程"""
        self.logger.info("正在启动 Word 进程...")
        try:
            # 使用 DispatchEx 保证我们在独立进程中操作，避免互相干扰
            self.word = win32com.client.DispatchEx("Word.Application")
            self.word.Visible = debug
            self.word.DisplayAlerts = 0
            
            # 极速优化：关闭所有在后台卡死 COM 的微软原生扫描守护进程 (拼写检查、语法检查、强制分页渲染)
            try:
                self.word.Options.CheckSpellingAsYouType = False
                self.word.Options.CheckGrammarAsYouType = False
                self.word.Options.Pagination = False
                self.word.ScreenUpdating = False
            except:
                pass
                
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
            self._cached_texts = None
        if self.word:
            try:
                self.word.Quit()
                self.word = None
            except:
                pass
        try:
            pythoncom.CoUninitialize()
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_total_paragraphs(self) -> int:
        if not self.doc:
            return 0
        # 不要使用 ComputeStatistics，因为它会强制触发阻塞性质的全局版面编排计算
        try:
            return self.doc.Paragraphs.Count
        except:
            return 0

    def snapshot_paragraph(self, index: int) -> ParagraphSnapshot:
        """Read exact source text; no strip/control-character normalisation."""
        paragraph_range = self.doc.Paragraphs(index).Range.Duplicate
        text = paragraph_range.Text
        if text.endswith("\r\x07"):
            text = text[:-2]
        elif text.endswith("\r"):
            text = text[:-1]
        blocked = ""
        if paragraph_range.Revisions.Count:
            blocked = "EXISTING_REVISIONS: accept/reject existing revisions in a separate copy first"
        if paragraph_range.Information(12):  # wdWithInTable
            blocked = "TABLE_PARAGRAPH: table cell patching is not supported yet"
        return ParagraphSnapshot(index, text, paragraph_range.Start, blocked)

    def _visible_paragraph_text(self, index):
        """Project final text by excluding tracked deletions, without accepting them."""
        paragraph = self.doc.Paragraphs(index).Range.Duplicate
        raw = paragraph.Text
        data = raw.encode('utf-16-le')
        deletions = []
        for revision in paragraph.Revisions:
            if revision.Type == 2:  # wdRevisionDelete
                deletions.append((revision.Range.Start - paragraph.Start, revision.Range.End - paragraph.Start))
            elif revision.Type != 1:  # only insert/delete are produced by this writer
                raise ValueError('Unexpected revision type during verification')
        for start, end in sorted(deletions, reverse=True):
            if start < 0 or end * 2 > len(data):
                raise ValueError('Invalid deletion projection')
            data = data[:start*2] + data[end*2:]
        text = data.decode('utf-16-le')
        return text[:-1] if text.endswith('\r') else text

    @staticmethod
    def _safe_patch_font(range_):
        font = range_.Font
        if font.Superscript != 0 or font.Subscript != 0 or font.Hidden != 0:
            return False
        return all(getattr(font, name) not in (9999999, '', None)
                   for name in ('Bold', 'Italic', 'Underline', 'Name', 'Size', 'Color',
                                'StrikeThrough', 'DoubleStrikeThrough', 'SmallCaps',
                                'AllCaps', 'Spacing', 'Position', 'Scaling'))

    def _assign_patch(self, range_, text):
        """Single mutation hook, also used to inject failures in real Word tests."""
        range_.Text = text

    @staticmethod
    def _paragraph_fingerprint(range_):
        """Compare document content/formatting, excluding volatile editor metadata."""
        root = ET.fromstring(range_.WordOpenXML)
        body = root.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body')
        if body is None:
            raise ValueError('Missing Word XML body')
        for node in body.iter():
            for key in list(node.attrib):
                name = key.rsplit('}', 1)[-1]
                if name.startswith('rsid') or name in ('paraId', 'textId'):
                    del node.attrib[key]
            for child in list(node):
                if child.tag.rsplit('}', 1)[-1] in ('proofErr', 'lastRenderedPageBreak'):
                    node.remove(child)
        return ET.tostring(body, encoding='unicode')

    def apply_sentence_revisions(self, snapshot, decisions, protected_terms=()):
        """Apply a whole paragraph atomically; verify final text or Undo the record."""
        try:
            current = self.snapshot_paragraph(snapshot.index)
            if current.blocked_reason or snapshot.blocked_reason:
                return PatchResult(False, current.blocked_reason or snapshot.blocked_reason)
            if current.text != snapshot.text:
                return PatchResult(False, 'STALE_SOURCE')
            if not self.doc.TrackRevisions:
                return PatchResult(False, 'TRACKING_DISABLED')
            paragraph = self.doc.Paragraphs(snapshot.index).Range.Duplicate
            # Rich structures require a future structure-aware patcher. Never flatten them.
            for collection in ('Fields', 'OMaths', 'InlineShapes', 'ContentControls', 'Hyperlinks', 'Footnotes', 'Endnotes', 'Bookmarks', 'Comments'):
                if getattr(paragraph, collection).Count:
                    return PatchResult(False, 'STRUCTURAL_CONTENT: ' + collection)
            for control in self.doc.ContentControls:
                if control.Range.Start < paragraph.End and control.Range.End > paragraph.Start:
                    return PatchResult(False, 'STRUCTURAL_CONTENT: enclosing content control')
            patches, expected = sentence_patch_plan(snapshot, decisions, protected_terms)
            if not patches:
                return PatchResult(False, 'NO_PATCHES')
            base = paragraph.Start
            for patch in patches:
                start = base + utf16_length(snapshot.text[:patch.start])
                end = base + utf16_length(snapshot.text[:patch.end])
                target = self.doc.Range(start, end)
                if target.Text != patch.old:
                    return PatchResult(False, 'STALE_RANGE')
                # For insertions inspect both adjacent characters, not a collapsed Font.
                check = target if start != end else self.doc.Range(max(base, start-1), min(paragraph.End-1, end+1))
                if not self._safe_patch_font(check):
                    return PatchResult(False, 'UNSAFE_OR_MIXED_FORMATTING')
            undo = self.word.UndoRecord
            if undo.CustomRecordLevel != 0:
                return PatchResult(False, 'NESTED_UNDO_RECORD')
            before_xml = self._paragraph_fingerprint(paragraph)
        except Exception as error:
            return PatchResult(False, 'PREFLIGHT_FAILED: ' + type(error).__name__)

        started = False
        try:
            undo.StartCustomRecord('Academic sentence edits')
            started = True
            for patch in reversed(patches):
                start = base + utf16_length(snapshot.text[:patch.start])
                end = base + utf16_length(snapshot.text[:patch.end])
                target = self.doc.Range(start, end)
                if target.Text != patch.old:
                    raise ValueError('Source shifted during patch transaction')
                self._assign_patch(target, patch.new)
            if self._visible_paragraph_text(snapshot.index) != expected:
                raise ValueError('Final sentence verification failed')
            undo.EndCustomRecord()
            started = False
            self._cached_texts = None
            return PatchResult(True, patch_count=len(patches))
        except Exception as error:
            try:
                if started:
                    undo.EndCustomRecord()
                if self._paragraph_fingerprint(self.doc.Paragraphs(snapshot.index).Range) != before_xml:
                    if not self.doc.Undo(1):
                        raise RuntimeError('Word Undo returned false')
                    if self._paragraph_fingerprint(self.doc.Paragraphs(snapshot.index).Range) != before_xml:
                        raise RuntimeError('Rollback verification failed')
                self._cached_texts = None
            except Exception as rollback:
                raise PatchRollbackError('Rollback could not be verified; discard this output without saving') from rollback
            return PatchResult(False, 'ROLLED_BACK: ' + type(error).__name__)

    def _load_text_cache(self):
        """一次性 O(N) 读取所有段落文本到内存，替代无数次 O(N^2) COM 跨进程索引通讯"""
        self.logger.info("⚡ 正在执行 O(N) 全文缓存预载，彻底绕过 COM 读取瓶颈...")
        self._cached_texts = []
        if not self.doc:
            return
            
        for p in self.doc.Paragraphs:
            try:
                text = p.Range.Text.strip()
                # 过滤控制字符
                text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                text = text.replace('\x07', '').replace('\r', '').replace('\n', '')
                self._cached_texts.append(text)
            except:
                self._cached_texts.append("")
        self.logger.info(f"✅ 全文缓存预载完毕，共缓存 {len(self._cached_texts)} 个文字块。")

    def get_paragraph_text(self, global_idx: int) -> str:
        """从 1-based 的索引获取段落干净文本"""
        if not self.doc or global_idx < 1 or global_idx > self.get_total_paragraphs():
            return ""
            
        if self._cached_texts is None and not getattr(self, "_cache_failed", False):
            try:
                self._load_text_cache()
            except Exception as e:
                self.logger.warning(f"预载缓存中断: {e}")
                setattr(self, "_cache_failed", True)
                
        # 优先防越界查缓存
        if self._cached_texts is not None:
            if (global_idx - 1) < len(self._cached_texts):
                text = self._cached_texts[global_idx - 1]
                if text != "": # 非空段落代表缓存可靠拿到数据
                    return text
                    
        # 兜底降级：缓存没存到（COM越界/迭代崩溃），走最原生直接调用
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
        解析文档章节结构，收集 OutlineLevel 1 和 2 的标题段落。
        使用迭代器一次遍历，直接读取每段落的 OutlineLevel 属性，
        兼容中文/英文/自定义模板，无需依赖样式名字符串匹配。
        """
        if not self.doc:
            return []

        total = self.get_total_paragraphs()
        chapters = []
        current_chapter_name = "引言/前言(未命名的初始章节)"
        current_start = 1

        self.logger.info(f"正在扫描文档章节结构（共 {total} 段落）...")

        i = 1
        for p in self.doc.Paragraphs:
            try:
                # 直接读取段落的 OutlineLevel 属性，比 Find API 可靠
                # wdOutlineLevel1=1, wdOutlineLevel2=2, wdOutlineLevelBodyText=10
                level = p.OutlineLevel
                if level in (1, 2):
                    text = p.Range.Text.strip()
                    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                    text = text.replace('\x07', '').replace('\r', '').replace('\n', '')
                    text = text[:50]
                    if not text:
                        text = f"章节_{i}"

                    if i > 1:
                        chapters.append({
                            "name": current_chapter_name,
                            "start": current_start,
                            "end": i - 1
                        })
                    current_chapter_name = text
                    current_start = i
            except Exception:
                pass
            i += 1

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
            
        # new_text 为空字符串是合法的（代表"删除该短语"，Word Track Changes 会记录为删除线）
        if not old_text or len(old_text) < 2 or len(old_text) > 240:
            return False
        if old_text == new_text:  # 没有实质改动
            return False
            
        try:
            p = self.doc.Paragraphs(global_idx)
            rng = p.Range.Duplicate
            rng.Find.ClearFormatting()
            rng.Find.Text = old_text
            rng.Find.Forward = True
            rng.Find.Wrap = 0 # wdFindStop (只在本段落中找)
            rng.Find.MatchCase = True
            rng.Find.MatchWholeWord = False
            rng.Find.MatchWildcards = False
            rng.Find.MatchSoundsLike = False
            rng.Find.MatchAllWordForms = False
            
            found = rng.Find.Execute()
            if found and rng.End <= p.Range.End:
                # 检查特殊的格式（如果包含上下标，可能会破坏化学式或引用）
                font_super = getattr(rng.Font, 'Superscript', 0)
                font_sub = getattr(rng.Font, 'Subscript', 0)
                # 9999999 或 True 代表有包含
                if font_super in (9999999, -1, True, 1) or font_sub in (9999999, -1, True, 1):
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
