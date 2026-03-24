import os
import re
import json
import time
import hashlib
import traceback
import logging
from pathlib import Path
from engine.llm_client import LLMClient
from engine.document import DocumentProcessor

class PolishingPipeline:
    def __init__(self, llm_client: LLMClient, doc_parser: DocumentProcessor, config: dict):
        self.client = llm_client
        self.doc_parser = doc_parser
        self.config = config
        self.logger = logging.getLogger("PolishingPipeline")
        
        # 缓存机制: 以传入的文件路径哈希 + 参数哈希作为 checkpoint key
        self.cache_dir = Path(__file__).parent.parent / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "workflow_cache.json"
        
        # 如果缓存文件不存在则创建空缓存
        if not self.cache_file.exists():
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def load_cache(self) -> dict:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def save_cache(self, cache_data: dict):
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

    def get_cache_key(self, doc_path: str, paragraph_idx: int) -> str:
        """根据文档路径、段落号、配置生成唯一缓存键"""
        base_name = self.config.get("original_filename", os.path.basename(doc_path))
        key_str = f"{base_name}_{paragraph_idx}_{self.config.get('language', 'chinese')}_{self.config.get('intensity', 'standard')}"
        return hashlib.md5(key_str.encode()).hexdigest()



    def should_skip_paragraph(self, text: str, min_chars: int, language: str) -> bool:
        """根据阈值和语言过滤段落"""
        if len(text.strip()) < min_chars:
            return True
        
        zh_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        
        if language == "chinese":
            # 纯中文模式：汉字少于 15 或者 汉字比例低于 15%
            if zh_chars < 15 or zh_chars / len(text) < 0.15:
                return True
        elif language == "english":
            # 纯英文模式：汉字比例过高则跳过 (例如中文摘要)
            if zh_chars / len(text) > 0.5:
                return True
                
        # 混合模式基本不过滤语言，只靠长度过滤
        return False

    def run_stage_1_nomination(self, prev_text: str, curr_text: str, next_text: str, intensity: str, language: str) -> list:
        """第一轮发现和提名，利用大模型能力提取需要修改的内容"""
        if language == "english":
            system_prompt = "You are an expert academic editor checking for unnatural phrasing, wordiness, and AI-generated clichés."
            intensity_rule = "[Strict Review]: Only modify if you are absolutely sure it is unnatural."
            if intensity == "heavy":
                intensity_rule = "[Aggressive Rewrite]: Focus on restructuring for academic flow."
            elif intensity == "light":
                intensity_rule = "[Light Grammar Only]: Only fix obvious typos or basic grammar."
            prompt = f"""
{intensity_rule}
Only output a JSON array of revisions: [ {{"old": "exact phrase from text (<=15 words)", "new": "improved phrase", "reason": "reason"}} ]. Do not change citations like [1] or specific acronyms.

[Previous Context]:
{prev_text}

[Current Paragraph]:
{curr_text}

[Next Context]:
{next_text}
            """
        else:
            system_prompt = "你是一位资深学术论文文字编辑，专门检查翻译腔、AI套话、啰嗦和用词不精问题。"
            
            intensity_rule = "【宁缺毋滥】：只有明确存在表达问题才修改。"
            if intensity == "heavy":
                intensity_rule = "【积极修改】：请重点针对句子结构重写，提升学术性，哪怕只是稍微别扭也需要整改。"
            elif intensity == "light":
                intensity_rule = "【极简修改】：仅仅纠错拼写和语法，只要逻辑上能读通顺就千万不要改。"

            prompt = f"""
{intensity_rule}
请针对"本文"部分提出修改，结合上下文判断逻辑衔接。只输出 JSON 数组，包含 [ {{"old": "原文某短语(纯汉字 <=50字)", "new": "改后短语", "reason": "修改原因"}} ]。

【绝对禁区】：不能修改文献引用 [1]、化学元素、特定专业术语英文缩写。

[前文（参考）]：
{prev_text}

[本文（审阅目标）]：
{curr_text}

[后文（参考）]：
{next_text}
            """
        revisions = self.client.call_json_api(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            timeout=50
        )
        return revisions

    def run_stage_2_recheck(self, curr_text: str, revisions: list, language: str) -> list:
        """第二轮复审限制过度改写，AI 交叉审核"""
        if not revisions:
            return []
            
        revisions_json = json.dumps(revisions, ensure_ascii=False, indent=2)
        
        if language == "english":
            system_prompt = "You are a strict final reviewer. Your goal is to reject unnecessary modifications."
            prompt = f"""
A junior editor proposed the following revisions for a paragraph in a PhD thesis. Please review and reject any modification that is unnecessary, incorrectly changes the original meaning, or removes essential transition words.
Output only a JSON array of accepted revisions: [ {{"old": "...", "new": "..."}} ]. If none are accepted, output [].

[Original text]:
{curr_text}

[Proposed revisions]:
{revisions_json}
            """
        else:
            system_prompt = "你是一位严谨的终审编辑，你的目标是宁少改，不可滥改。"
            prompt = f"""
以下是一段源自博士论文的文本，另一位编辑提出了一些修改建议。请你复审并筛除“过度修改”。
如果发现以下瑕疵一律驳回该修改：
1. 删除了原本为了逻辑连贯需要的（因此、此外、基于此）等连接词。
2. 将原文已经很贴切的地道表达强行硬改掉，并没有实质提升。
3. 试图改写专属名词或格式。

只输出 JSON 数组保留认可的修改：[ {{"old": "...", "new": "..."}} ]。如果没有保留项，输出 []。

[原文本]：
{curr_text}

[修改建议]：
{revisions_json}
            """
        filtered_revisions = self.client.call_json_api(
            messages=[
                { "role": "system", "content": system_prompt },
                { "role": "user", "content": prompt }
            ],
            temperature=0.1,
            timeout=40
        )
        return filtered_revisions

    def process_document(self, doc_path: str, progress_callback=None):
        """主入口流：读取原 Word，处理每一个段落，写入原 Word。"""
        min_chars = self.config.get("min_chars", 20)
        language = self.config.get("language", "chinese")
        intensity = self.config.get("intensity", "standard")
        use_cross_review = self.config.get("use_cross_review", True)
        skipped_chapters = self.config.get("skipped_chapters", [])
        
        cache = self.load_cache()
        self.doc_parser.open_document(doc_path, read_only=False, track_revisions=True)
        
        total = self.doc_parser.get_total_paragraphs()
        chapters = self.doc_parser.parse_chapters()
        
        # 预计算被跳过的真实段落区间
        skip_ranges = []
        for ch in chapters:
            if ch["name"] in skipped_chapters:
                skip_ranges.append((ch["start"], ch["end"]))

        def is_skipped(idx):
            for s, e in skip_ranges:
                if s <= idx <= e:
                    return True
            return False

        changes_count = 0
        all_excel_records = []

        try:
            # 开始逐段遍历
            for i in range(1, total + 1):
                if progress_callback:
                    progress_callback(i, total)
                    
                if is_skipped(i):
                    continue

                curr_text = self.doc_parser.get_paragraph_text(i)
                
                # 第一重门：硬件规则跳过
                if self.should_skip_paragraph(curr_text, min_chars, language):
                    continue
                    
                # 第二重门：断点缓存机制
                cache_key = self.get_cache_key(doc_path, i)
                if cache_key in cache:
                    continue

                # 开始处理
                prev_text = self.doc_parser.get_neighbor_text(i, direction="prev", limit=2)
                next_text = self.doc_parser.get_neighbor_text(i, direction="next", limit=1)

                try:
                    # Stage 1: Nomination
                    revisions = self.run_stage_1_nomination(prev_text, curr_text, next_text, intensity, language)
                    
                    # Stage 2: Cross Review (可选)
                    if use_cross_review and revisions:
                        revisions = self.run_stage_2_recheck(curr_text, revisions, language)

                    # 将最终的修改建议打入 Word
                    if revisions:
                        for rev in revisions:
                            old_txt = rev.get("old", "")
                            new_txt = rev.get("new", "")
                            status = "❌驳回"
                            
                            if old_txt and new_txt and old_txt != new_txt:
                                success = self.doc_parser.apply_tracked_revision(i, old_txt, new_txt)
                                if success:
                                    changes_count += 1
                                    status = "✅保留"
                                    
                            all_excel_records.append({
                                "paragraph_idx": i,
                                "old": old_txt,
                                "new": new_txt,
                                "reason": rev.get("reason", ""),
                                "status": status
                            })
                                    
                    # 记录缓存并偶尔存盘以避免前功尽弃
                    cache[cache_key] = {"status": "done", "changes": changes_count}
                    if i % 5 == 0:
                        self.save_cache(cache)
                        self.doc_parser.save()
                        
                except Exception as e:
                    self.logger.error(f"处理段落 {i} 出错: {e}", exc_info=True)

        finally:
            # 最后无论成功、失败均扫尾存盘
            self.save_cache(cache)
            self.doc_parser.save()
            
            # 导出 Excel 分析报告
            stem = os.path.splitext(os.path.basename(doc_path))[0]
            export_path = os.path.join(os.path.dirname(doc_path), f"Excel_{stem}.xlsx")
            from engine.excel_exporter import ExcelExporter
            ExcelExporter().export(export_path, all_excel_records)
            
        return changes_count, export_path
