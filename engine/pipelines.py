import os
import re
import json
import hashlib
import logging
from pathlib import Path

from engine.llm_client import LLMClient, ModelError, ModelFormatError
from engine.validation import Validator
from engine.document import DocumentProcessor


class PolishingPipeline:
    PROMPT_VERSION = "p0-suggestions-v4"

    def __init__(self, llm_client: LLMClient, doc_parser: DocumentProcessor, config: dict):
        self.client = llm_client
        self.doc_parser = doc_parser
        self.config = config
        self.logger = logging.getLogger("PolishingPipeline")

        self.cache_dir = Path(__file__).parent.parent / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "workflow_cache.json"
        self.chapter_notes_dir = self.cache_dir / "chapter_notes"
        self.chapter_notes_dir.mkdir(exist_ok=True)

        if not self.cache_file.exists():
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def load_cache(self) -> dict:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_cache(self, cache_data: dict):
        temporary = self.cache_file.with_suffix(".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        os.replace(temporary, self.cache_file)

    def _model_identity(self):
        return f"{getattr(self.client, 'base_url', '')}|{getattr(self.client, 'model', '')}"

    def _prompt_customization(self) -> dict:
        return self.config.get("prompt_customization", {}) or {}

    def _prompt_customization_hash(self) -> str:
        raw = json.dumps(self._prompt_customization(), ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    def _get_prompt_extra(self, stage_name: str) -> str:
        value = self._prompt_customization().get(stage_name, "")
        return str(value).strip()

    @staticmethod
    def _append_prompt_extra(prompt: str, extra: str, language: str) -> str:
        if not extra:
            return prompt

        label = "Additional user instructions" if language in ("english", "mixed") else "用户自定义附加要求"
        return f"{prompt}\n\n---\n{label}：\n{extra}"

    def get_cache_key(self, doc_path: str, paragraph_idx: int, text: str = "") -> str:
        """根据文档、段落号和当前 prompt 版本生成唯一缓存键。"""
        base_name = self.config.get("original_filename", os.path.basename(doc_path))
        model_name = getattr(self.client, "model", "")
        key_str = "|".join([
            self.PROMPT_VERSION,
            base_name,
            str(paragraph_idx),
            self.config.get("language", "chinese"),
            self.config.get("intensity", "standard"),
            str(self.config.get("use_cross_review", True)),
            model_name,
            self._prompt_customization_hash(),
            getattr(self, "document_hash", None) or hashlib.sha256(Path(doc_path).read_bytes()).hexdigest(),
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            self._model_identity(),
            json.dumps({k: self.config.get(k) for k in
                        ("min_chars", "skipped_chapters", "protected_terms")}, sort_keys=True),
        ])
        return hashlib.md5(key_str.encode("utf-8")).hexdigest()

    def _get_chapter_notes_cache_path(self, chapter_name: str, chapter_text: str, language: str) -> Path:
        base_name = self.config.get("original_filename", "document")
        text_hash = hashlib.md5(chapter_text.encode("utf-8")).hexdigest()[:12]
        key = hashlib.md5(
            (
                f"{self.PROMPT_VERSION}|{base_name}|{chapter_name}|{language}|"
                f"{text_hash}|{self._prompt_customization_hash()}|{self._model_identity()}"
            ).encode("utf-8")
        ).hexdigest()
        return self.chapter_notes_dir / f"{key}.txt"

    def should_skip_paragraph(self, text: str, min_chars: int, language: str) -> bool:
        """根据阈值和语言过滤段落"""
        stripped = text.strip()
        if len(stripped) < min_chars:
            return True

        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", stripped))

        if language == "chinese":
            if zh_chars < 15 or zh_chars / len(stripped) < 0.15:
                return True
        elif language == "english":
            if zh_chars / len(stripped) > 0.5:
                return True

        return False

    @staticmethod
    def split_sentences(text: str, language: str) -> list:
        """按语言分句，返回句子列表"""
        if language in ("english", "mixed"):
            parts = re.split(r"(?<=[.?!])\s+", text.strip())
            return [p.strip() for p in parts if p.strip()]

        if "。" not in text:
            return [text.strip()] if text.strip() else []

        parts = text.split("。")
        sentences = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            sentences.append(part + "。" if i < len(parts) - 1 else part)
        return [s for s in sentences if s]

    @staticmethod
    def label_sentences(sentences: list) -> str:
        """将句子列表标注为 [S1] 句子1  [S2] 句子2 ..."""
        return "  ".join(f"[S{i + 1}] {s}" for i, s in enumerate(sentences))

    @staticmethod
    def _build_chapter_text(chapter: dict, paragraph_texts: dict) -> str:
        texts = []
        for idx in range(chapter["start"], chapter["end"] + 1):
            text = paragraph_texts.get(idx, "").strip()
            if text:
                texts.append(text)
        return "\n".join(texts)

    @staticmethod
    def _normalize_revision(item: dict) -> dict:
        priority = str(item.get("priority", "")).strip()
        priority_lower = priority.lower()
        if priority_lower in ("high", "高"):
            priority = "高"
        elif priority_lower in ("medium", "med", "中"):
            priority = "中"

        return {
            "sentence_id": str(item.get("sentence_id", "")).strip(),
            "old": str(item.get("old", "")).strip(),
            "new": "" if item.get("new", "") is None else str(item.get("new", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
            "priority": priority,
        }

    def _enrich_reviewed_revisions(self, raw_revisions: list, reviewed_revisions: list) -> list:
        """Stage 2 有时会丢字段，这里尽量补回 sentence_id / reason / priority。"""
        if not reviewed_revisions:
            return []

        raw_pool = [self._normalize_revision(item) for item in raw_revisions]
        normalized = []
        used_indexes = set()

        for item in reviewed_revisions:
            reviewed = self._normalize_revision(item)
            matched_index = None

            for idx, raw in enumerate(raw_pool):
                if idx in used_indexes:
                    continue

                same_sentence = (
                    reviewed["sentence_id"]
                    and reviewed["sentence_id"] == raw["sentence_id"]
                )
                same_old = reviewed["old"] and reviewed["old"] == raw["old"]
                same_new = reviewed["new"] == raw["new"]

                if same_sentence and same_old:
                    matched_index = idx
                    break
                if same_old and same_new:
                    matched_index = idx
                    break
                if same_old and not reviewed["sentence_id"]:
                    matched_index = idx
                    break

            if matched_index is not None:
                used_indexes.add(matched_index)
                matched = raw_pool[matched_index]
                # Empty new is an intentional deletion, never fill it back in.
                for key in ("sentence_id", "reason", "priority"):
                    if not reviewed.get(key):
                        reviewed[key] = matched.get(key, "")

            normalized.append(reviewed)

        return normalized

    @staticmethod
    def _index_revisions_by_sentence(revisions: list) -> dict:
        indexed = {}
        for rev in revisions:
            sid = rev.get("sentence_id", "")
            indexed.setdefault(sid, []).append(rev)
        return indexed

    @staticmethod
    def _attach_missing_sentence_ids(revisions: list, sentences: list) -> list:
        if not revisions:
            return revisions

        for rev in revisions:
            if rev.get("sentence_id"):
                continue

            old_text = rev.get("old", "")
            if len(sentences) == 1:
                rev["sentence_id"] = "S1"
                continue

            for idx, sentence in enumerate(sentences, start=1):
                if old_text and old_text in sentence:
                    rev["sentence_id"] = f"S{idx}"
                    break

        return revisions

    def run_stage_0_chapter_understanding(self, chapter_name: str, chapter_text: str, language: str) -> str:
        """对整章文本做一次 API 调用，生成章节工作笔记。"""
        if not chapter_text.strip():
            return f"[章节笔记为空：{chapter_name}]"

        notes_path = self._get_chapter_notes_cache_path(chapter_name, chapter_text, language)
        if notes_path.exists():
            return notes_path.read_text(encoding="utf-8")

        system_prompt = (
            "You are an expert academic thesis editor. Read the full chapter carefully and prepare "
            "working notes for later paragraph-level review. Use the same primary language as the chapter."
        )
        prompt = f"""
请完整阅读以下论文章节，并输出一份供后续逐段审阅使用的工作笔记。
仅依据原文，不推断或补充缺失的实验数值、方法、结论。信息不足时明确写“未提供”。
实验操作中的合理被动句（如“样品被加热”“was heated”）不属于文风问题，不建议仅为换语态而改写。

请覆盖这 4 部分：
1. 本章核心研究内容与论证主线（3-5 句）
2. 关键术语、缩写、材料名、方法名、化学体系清单
3. 本章哪些段落更容易出现文风问题，主要风险是什么
4. 哪些表达属于本章语境下的正常专业写法，不要误判为 AI 套话或翻译腔

章节名称：
{chapter_name}

章节全文：
{chapter_text}
        """.strip()
        prompt = self._append_prompt_extra(prompt, self._get_prompt_extra("stage0"), language)

        content = self.client.call_api(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            timeout=180,
        )
        notes_path.write_text(content, encoding="utf-8")
        return content

    def run_stage_1_nomination(
        self,
        prev_text: str,
        labeled_text: str,
        next_text: str,
        intensity: str,
        language: str,
        chapter_notes: str,
    ) -> list:
        """第一轮发现和提名，结合章节笔记逐句提取候选修改。"""
        if language in ("english", "mixed"):
            system_prompt = (
                "You are an expert academic editor for SCI journal papers. Review every labeled sentence "
                "carefully and only nominate edits that create a real improvement."
            )
            intensity_rule = "[Standard]: Flag unnatural phrasing, wordiness, and AI-generated clichés only when there is a genuine gain."
            if intensity == "heavy":
                intensity_rule = "[Aggressive]: Be more proactive in restructuring awkward academic prose, but still preserve meaning."
            elif intensity == "light":
                intensity_rule = "[Light]: Only fix clear grammar problems and obvious filler."

            prompt = f"""
{intensity_rule}

[Chapter Notes]:
{chapter_notes}

Review EACH labeled sentence [S1], [S2]... in the [Current Paragraph].
Return a JSON array only:
[{{"sentence_id":"S1","old":"exact phrase to replace (<=20 words)","new":"improved phrase","reason":"brief reason","priority":"high or medium"}}]

Rules:
- Do not edit citations like [1] or [2-5].
- Do not edit abbreviations, chemical formulas, species names, model names, numerical values, or units.
- Do not rewrite a sentence that is already natural enough.
- Reasonable passive voice used for methods/process description should usually be kept.
- If nothing needs changing, output [].

[Previous Context]:
{prev_text}

[Current Paragraph]:
{labeled_text}

[Next Context]:
{next_text}
            """.strip()
        else:
            system_prompt = "你是一位资深学术论文文字编辑，专门识别翻译腔、AI套话、啰嗦和不精确措辞，但绝不为改而改。"
            intensity_rule = "【宁缺毋滥】只有明确存在表达问题，且修改后能带来实质提升时才提名。"
            if intensity == "heavy":
                intensity_rule = "【积极修改】请主动处理明显别扭、堆砌、欧化或学术表达不稳的句子，但仍必须保留原意和术语。"
            elif intensity == "light":
                intensity_rule = "【极简修改】只处理明确的语病、翻译腔和套话，边界稍有不确定就不要改。"

            prompt = f"""
{intensity_rule}

章节工作笔记（整章上下文记忆）：
{chapter_notes}

上文（参考背景）：
{prev_text}

当前段落（逐句审阅目标）：
{labeled_text}

下文（参考背景）：
{next_text}

请逐句检查 [S1]、[S2]... 是否存在以下问题：
1. 翻译腔：欧化结构、多重定语堆叠、僵硬名词化、机械直译。
2. AI 套话：如“值得注意的是”“不可忽视的是”“需要指出的是”等空泛起手式。
3. 啰嗦重复：同义反复、副词堆叠、无信息量短语。
4. 用词不精：措辞含混、搭配生硬、和本章学科语境不匹配。
5. 不自然被动句：只有在被动写法明显生硬、拖沓、像英文直译时才改。

关于被动句，请严格把握边界：
- 需要改的例子：该方法被广泛应用于多孔材料的制备。 -> 该方法广泛应用于多孔材料制备。
- 不需要改的例子：样品在 80 ℃ 下被加热 2 h。 这是实验过程描述，属于合理被动，不要改。
- 不需要改的例子：所得气凝胶被用于后续力学测试。 若重点在处理对象或流程承接，也不要强行改主动。

【绝对禁区】
- 文献引用 [1]、[2-5]
- 专业术语、英文缩写、材料名、化学式、数值、单位
- 维持逻辑连贯所需的连接词：因此、此外、然而、其中、由此、综上等
- 原句已经自然通顺、只是和你的个人偏好不同的表达

只输出 JSON 数组，每项格式如下：
[{{"sentence_id":"S1","old":"需替换的原文片段（<=60字）","new":"替换后的片段，可为空字符串表示删除","reason":"一句话说明原因","priority":"高或中"}}]

若无需修改，输出 []，不要输出任何额外说明。
            """.strip()

        prompt = self._append_prompt_extra(prompt, self._get_prompt_extra("stage1"), language)

        return self.client.call_json_api(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            timeout=60,
        )

    def run_stage_2_recheck(self, labeled_text: str, revisions: list, language: str, chapter_notes: str) -> list:
        """第二轮复审，筛掉无必要或边界不稳的修改。"""
        if not revisions:
            return []

        revisions_json = json.dumps(revisions, ensure_ascii=False, indent=2)

        if language == "english":
            system_prompt = "You are a strict final reviewer. Reject any revision that is not clearly better."
            prompt = f"""
[Chapter Notes]:
{chapter_notes}

Review the proposed edits below and keep only the ones that should survive final review.
Return a JSON array in the SAME format as the input items. If none should remain, output [].

Reject any edit that:
1. does not materially improve the sentence,
2. weakens or changes the original meaning,
3. removes essential transition words,
4. touches technical terms, formulas, abbreviations, citations, numbers, or units,
5. rewrites a reasonable passive sentence used for methods/process description.

[Current Paragraph]:
{labeled_text}

[Proposed Revisions]:
{revisions_json}
            """.strip()
        else:
            system_prompt = "你是一位严谨的学术论文终审编辑，负责最终把关，原则是宁少勿滥。"
            prompt = f"""
章节工作笔记（整章上下文记忆）：
{chapter_notes}

当前段落：
{labeled_text}

Stage 1 提名的修改建议：
{revisions_json}

请逐条复审。满足以下任一条件就【驳回】：
1. 原句本身已经通顺自然，修改没有实质提升。
2. 改后效果与原文相当，甚至只是换个说法，不值得动。
3. 删除或弱化了原文维持逻辑衔接所需的连接词。
4. 触碰了专业术语、缩写、材料名、化学式、数值、单位、引用或固定格式。
5. 原句属于合理被动句，不应硬改。

“合理被动句不改”的判断标准：
- 用于描述实验步骤、处理流程、表征过程时，通常保留。
- 主语不重要、施动者未知，或句子重点在受事对象时，通常保留。
- 只有明显欧化、拖沓、生硬，改成主动后确有提升，才保留该修改。

请返回【通过复审】的建议，且格式必须与输入保持一致：
[{{"sentence_id":"S1","old":"...","new":"...","reason":"...","priority":"高"}}]

若全部驳回，输出 []。只输出 JSON，不要解释。
            """.strip()

        prompt = self._append_prompt_extra(prompt, self._get_prompt_extra("stage2"), language)

        reviewed = self.client.call_json_api(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            timeout=40,
        )
        return self._enrich_reviewed_revisions(revisions, reviewed)

    def process_document(self, doc_path: str, progress_callback=None):
        """主入口流：读取原 Word，处理每一个段落，写入原 Word。"""
        min_chars = self.config.get("min_chars", 20)
        language = self.config.get("language", "chinese")
        intensity = self.config.get("intensity", "standard")
        use_cross_review = self.config.get("use_cross_review", True)
        skipped_chapters = set(self.config.get("skipped_chapters", []))

        cache = self.load_cache()
        self.document_hash = hashlib.sha256(Path(doc_path).read_bytes()).hexdigest()
        self.last_records = []
        validator = Validator(self.config.get("protected_terms", []))
        self.doc_parser.open_document(doc_path, read_only=False, track_revisions=True)

        total = self.doc_parser.get_total_paragraphs()
        chapters = self.doc_parser.parse_chapters()
        if not chapters:
            chapters = [{"name": "全文", "start": 1, "end": total}]

        paragraph_texts = {}
        for idx in range(1, total + 1):
            paragraph_texts[idx] = self.doc_parser.get_paragraph_text(idx)

        chapter_index = 0
        chapter_notes_map = {}
        changes_count = 0
        all_excel_records = []
        output_dir = self.config.get("output_dir") or os.path.dirname(doc_path)
        os.makedirs(output_dir, exist_ok=True)
        excel_filename = self.config.get(
            "excel_output_filename",
            f"Excel_{os.path.splitext(os.path.basename(doc_path))[0]}.xlsx",
        )
        export_path = os.path.join(
            output_dir,
            excel_filename,
        )

        try:
            for i in range(1, total + 1):
                if progress_callback:
                    progress_callback(i, total)

                while chapter_index + 1 < len(chapters) and i > chapters[chapter_index]["end"]:
                    chapter_index += 1

                chapter = chapters[min(chapter_index, len(chapters) - 1)]
                chapter_name = chapter["name"]
                if chapter_name in skipped_chapters:
                    continue

                curr_text = paragraph_texts.get(i, "")
                if self.should_skip_paragraph(curr_text, min_chars, language):
                    continue

                cache_key = self.get_cache_key(doc_path, i, curr_text)

                try:
                    if chapter_name not in chapter_notes_map:
                        chapter_text = self._build_chapter_text(chapter, paragraph_texts)
                        chapter_notes_map[chapter_name] = self.run_stage_0_chapter_understanding(
                            chapter_name, chapter_text, language
                        )
                    chapter_notes = chapter_notes_map[chapter_name]

                    sentences = self.split_sentences(curr_text, language)
                    labeled_text = self.label_sentences(sentences)
                    protected = {f"S{n}": [{"type": span.kind, "text": span.text}
                                           for span in validator.extractor.extract(sentence)]
                                 for n, sentence in enumerate(sentences, 1)}
                    labeled_text += "\n[Protected spans - do not alter]:\n" + json.dumps(protected, ensure_ascii=False)
                    prev_text = self.doc_parser.get_neighbor_text(i, direction="prev", limit=2)
                    next_text = self.doc_parser.get_neighbor_text(i, direction="next", limit=1)

                    cached = cache.get(cache_key, {})
                    if not isinstance(cached, dict):
                        cached = {}
                    raw_revisions = [
                        self._normalize_revision(item)
                        for item in (cached["raw"] if "raw" in cached else self.run_stage_1_nomination(
                            prev_text,
                            labeled_text,
                            next_text,
                            intensity,
                            language,
                            chapter_notes,
                        ))
                    ]
                    raw_revisions = self._attach_missing_sentence_ids(raw_revisions, sentences)

                    if "kept" in cached:
                        kept_revisions = cached["kept"]
                    elif use_cross_review and raw_revisions:
                        kept_revisions = self.run_stage_2_recheck(
                            labeled_text,
                            raw_revisions,
                            language,
                            chapter_notes,
                        )
                    else:
                        kept_revisions = raw_revisions

                    kept_revisions = [self._normalize_revision(item) for item in kept_revisions]
                    kept_revisions = self._attach_missing_sentence_ids(kept_revisions, sentences)
                    valid_ids = {f"S{n}" for n in range(1, len(sentences) + 1)}
                    if any(r["sentence_id"] not in valid_ids for r in raw_revisions + kept_revisions):
                        raise ModelFormatError("模型返回未知或无法定位的句子编号")
                    # Cache suggestions only, never a 'done' flag. Replay against a fresh source.
                    cache[cache_key] = {"raw": raw_revisions, "kept": kept_revisions}
                    raw_keys = {
                        (item.get("sentence_id", ""), item.get("old", ""), item.get("new", ""))
                        for item in raw_revisions
                    }
                    kept_keys = {
                        (item.get("sentence_id", ""), item.get("old", ""), item.get("new", ""))
                        for item in kept_revisions
                    }
                    rejected_keys = raw_keys - kept_keys

                    kept_by_sid = self._index_revisions_by_sentence(kept_revisions)
                    rejected_by_sid = {}
                    for rev in raw_revisions:
                        key = (
                            rev.get("sentence_id", ""),
                            rev.get("old", ""),
                            rev.get("new", ""),
                        )
                        if key in rejected_keys:
                            rejected_by_sid.setdefault(rev.get("sentence_id", ""), []).append(rev)

                    for s_idx, sentence in enumerate(sentences, start=1):
                        working_sentence = sentence
                        sentence_id = f"S{s_idx}"
                        kept_items = kept_by_sid.get(sentence_id, [])
                        rejected_items = rejected_by_sid.get(sentence_id, [])

                        if kept_items:
                            for rev in kept_items:
                                old_txt = rev.get("old", "")
                                new_txt = rev.get("new", "")
                                status = "⚠️保留但写入失败"
                                rejection = validator.validate_fragment(working_sentence, old_txt, new_txt)
                                if curr_text.count(old_txt) != 1:
                                    rejection = "source fragment is ambiguous within paragraph"
                                if rejection:
                                    status = "VALIDATION_REJECTED"
                                elif old_txt and old_txt != new_txt:
                                    success = self.doc_parser.apply_tracked_revision(i, old_txt, new_txt)
                                    if success:
                                        changes_count += 1
                                        status = "✅保留"
                                        working_sentence = working_sentence.replace(old_txt, new_txt, 1)
                                    else:
                                        status = "PATCH_FAILED"

                                all_excel_records.append({
                                    "chapter": chapter_name,
                                    "paragraph_idx": i,
                                    "sentence_id": sentence_id,
                                    "sentence": sentence,
                                    "old": old_txt,
                                    "new": new_txt,
                                    "reason": rejection or rev.get("reason", ""),
                                    "status": status,
                                    "priority": rev.get("priority", ""),
                                })
                        if rejected_items:
                            for rev in rejected_items:
                                all_excel_records.append({
                                    "chapter": chapter_name,
                                    "paragraph_idx": i,
                                    "sentence_id": sentence_id,
                                    "sentence": sentence,
                                    "old": rev.get("old", ""),
                                    "new": rev.get("new", ""),
                                    "reason": rev.get("reason", ""),
                                    "status": "❌驳回",
                                    "priority": rev.get("priority", ""),
                                })
                        if not kept_items and not rejected_items:
                            all_excel_records.append({
                                "chapter": chapter_name,
                                "paragraph_idx": i,
                                "sentence_id": sentence_id,
                                "sentence": sentence,
                                "old": "",
                                "new": "",
                                "reason": "",
                                "status": "无需修改",
                                "priority": "",
                            })

                    if i % 5 == 0:
                        self.save_cache(cache)
                        self.doc_parser.save()

                except Exception as e:
                    status = e.status if isinstance(e, ModelError) else "PROCESSING_ERROR"
                    all_excel_records.append({
                        "chapter": chapter_name, "paragraph_idx": i,
                        "sentence": curr_text, "status": status,
                        "reason": str(e) if isinstance(e, ModelError) else "段落处理失败，请检查运行日志",
                    })
                    self.logger.error("处理段落 %s 出错: %s", i, status)

        finally:
            self.save_cache(cache)
            self.doc_parser.save()

            from engine.excel_exporter import ExcelExporter

            self.last_records = all_excel_records
            if not ExcelExporter().export(export_path, all_excel_records, chapters=chapters):
                raise RuntimeError("Excel 报告导出失败，Word 已尝试保存，请检查输出目录")

        return changes_count, export_path
