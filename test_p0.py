import hashlib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import openai

from engine.llm_client import LLMClient, ModelFormatError
from engine.uploads import upload_identity
from engine.validation import Validator
from benchmarks.evaluate import evaluate, read_rows
import test_pipeline_regressions as fixtures


class P0Tests(unittest.TestCase):
    _workspace_tempdir = fixtures.PipelineRegressionTests._workspace_tempdir
    _build_pipeline = fixtures.PipelineRegressionTests._build_pipeline
    def make_run(self):
        root = self._workspace_tempdir()
        doc = root / "source.docx"
        doc.write_bytes(b"immutable source")
        pipeline, client, parser = self._build_pipeline(root / "cache")
        return doc, pipeline, client, parser

    def test_cache_replays_patches_and_report(self):
        doc, pipeline, client, parser = self.make_run()
        pipeline.process_document(str(doc))
        first_records = pipeline.last_records.copy()
        calls = len(client.stage1_prompts)
        parser.revisions.clear()
        count, _ = pipeline.process_document(str(doc))
        self.assertEqual(count, 1)
        self.assertEqual(len(client.stage1_prompts), calls)
        self.assertEqual(pipeline.last_records, first_records)
        self.assertEqual(len(parser.revisions), 1)

    def test_content_and_model_invalidate_cache(self):
        doc, pipeline, client, _ = self.make_run()
        original = pipeline.get_cache_key(str(doc), 1, "one")
        self.assertNotEqual(original, pipeline.get_cache_key(str(doc), 1, "two"))
        doc.write_bytes(b"changed")
        self.assertNotEqual(original, pipeline.get_cache_key(str(doc), 1, "one"))
        before = pipeline._get_chapter_notes_cache_path("x", "text", "english")
        client.base_url = "https://example.invalid/v1"
        self.assertNotEqual(before, pipeline._get_chapter_notes_cache_path("x", "text", "english"))

    def test_upload_same_name_new_content(self):
        doc, _, _, _ = self.make_run()
        state = {"last_uploaded_name": "x.docx", "last_uploaded_hash": hashlib.sha256(b"a").hexdigest(), "work_copy_path": str(doc)}
        self.assertFalse(upload_identity(b"a", "x.docx", state)[1])
        self.assertTrue(upload_identity(b"b", "x.docx", state)[1])
        self.assertTrue(upload_identity(b"a", "y.docx", state)[1])

    def test_format_error_is_recorded_and_not_cached(self):
        doc, pipeline, client, parser = self.make_run()
        client.call_json_api = Mock(side_effect=ModelFormatError())
        pipeline.process_document(str(doc))
        self.assertTrue(pipeline.last_records)
        self.assertTrue(all(r["status"] == "MODEL_FORMAT_ERROR" for r in pipeline.last_records))
        self.assertFalse(pipeline.load_cache())
        self.assertFalse(parser.revisions)

    def test_fact_damage_never_reaches_word(self):
        doc, pipeline, client, parser = self.make_run()
        pipeline.config["use_cross_review"] = False
        client.call_json_api = Mock(return_value=[{"sentence_id": "S1", "old": "APTES", "new": "PMSA"}])
        pipeline.process_document(str(doc))
        self.assertFalse(parser.revisions)
        self.assertTrue(all(r["status"] == "VALIDATION_REJECTED" for r in pipeline.last_records))

    def test_unknown_sentence_is_error(self):
        doc, pipeline, client, parser = self.make_run()
        pipeline.config["use_cross_review"] = False
        client.call_json_api = Mock(return_value=[{"sentence_id": "S99", "old": "APTES", "new": "x"}])
        pipeline.process_document(str(doc))
        self.assertFalse(parser.revisions)
        self.assertTrue(all(r["status"] == "MODEL_FORMAT_ERROR" for r in pipeline.last_records))

    def test_patch_failure_is_not_keep(self):
        doc, pipeline, _, parser = self.make_run()
        parser.apply_tracked_revision = Mock(return_value=False)
        count, _ = pipeline.process_document(str(doc))
        self.assertEqual(count, 0)
        self.assertEqual(pipeline.last_records[0]["status"], "PATCH_FAILED")

    def test_real_empty_array_is_keep(self):
        doc, pipeline, client, parser = self.make_run()
        client.call_json_api = Mock(return_value=[])
        pipeline.process_document(str(doc))
        self.assertTrue(all(r["status"] == "无需修改" for r in pipeline.last_records))
        self.assertFalse(parser.revisions)

    def test_reviewer_deletion_stays_empty(self):
        _, pipeline, _, _ = self.make_run()
        raw = [{"sentence_id": "S1", "old": "短语", "new": "替换"}]
        reviewed = [{"sentence_id": "S1", "old": "短语", "new": ""}]
        self.assertEqual(pipeline._enrich_reviewed_revisions(raw, reviewed)[0]["new"], "")

    def test_report_keeps_rejected_and_accepted_for_same_sentence(self):
        doc, pipeline, client, _ = self.make_run()
        client.call_json_api = Mock(side_effect=[
            [{"sentence_id": "S1", "old": "值得注意的是", "new": ""},
             {"sentence_id": "S1", "old": "聚焦", "new": "关注"}],
            [{"sentence_id": "S1", "old": "值得注意的是", "new": ""}], []])
        pipeline.process_document(str(doc))
        self.assertEqual([r["status"] for r in pipeline.last_records][:2], ["✅保留", "❌驳回"])

    def test_non_object_cache_is_ignored(self):
        _, pipeline, _, _ = self.make_run()
        pipeline.cache_file.write_text("[]", encoding="utf-8")
        self.assertEqual(pipeline.load_cache(), {})

    def test_reviewer_cannot_introduce_new_edits(self):
        doc, pipeline, client, parser = self.make_run()
        client.call_json_api = Mock(side_effect=[
            [{"sentence_id": "S1", "old": "值得注意的是", "new": ""}],
            [{"sentence_id": "S1", "old": "聚焦", "new": "关注"}], []])
        pipeline.process_document(str(doc))
        self.assertFalse(parser.revisions)
        self.assertEqual(pipeline.last_records[0]["status"], "MODEL_FORMAT_ERROR")


class PureTests(unittest.TestCase):
    def test_strict_parser(self):
        client = object.__new__(LLMClient)
        self.assertEqual(client._extract_json("[]"), [])
        self.assertEqual(client._extract_json('```json\n[]\n```'), [])
        for invalid in ("", "error []", "{}", "[1]", "```json\n[]", "[]```", '[{"old":"x"}]', '[{"old":"x","new":null}]'):
            with self.subTest(invalid=invalid), self.assertRaises(ModelFormatError):
                client._extract_json(invalid)

    def test_guardrails(self):
        validator = Validator(["二氧化硅", "pH"])
        for old, new in [("80 ℃", "90 ℃"), ("2 h", "2 s"), ("[1]", "[2]"),
                         ("APTES", "PMSA"), ("二氧化硅", "氧化铝"),
                         ("20 ℃和80 ℃", "80 ℃和20 ℃"), ("0.021 W m−1 K−1", "0.021 W m−1"),
                         ("H2SO4", "H2O"), ("NaCl", "KCl"), ("O2", "N2"),
                         ("Fe3+", "Fe2+"), ("Fe³⁺", "Fe²⁺"), ("Cl-", "F-"), ("pH", "PH")]:
            with self.subTest(old=old):
                self.assertTrue(validator.validate(old, new))
        self.assertTrue(validator.validate("W m⁻² K⁻¹", "W m² K⁻¹"))
        self.assertFalse(validator.validate("值得注意的是，APTES含量不变。", "APTES含量不变。"))
        self.assertTrue(validator.validate_fragment("重复重复", "重复", "改写"))

    def test_evaluation_denominators_and_missing_human_labels(self):
        samples = read_rows(Path(__file__).parent / "benchmarks" / "synthetic.jsonl")
        predictions = [{"id": s["id"], "revised": s["accepted"][0] if s["should_edit"] else s["original"]} for s in samples]
        report = evaluate(samples, predictions)
        self.assertEqual(report["reference_precision"], 1)
        self.assertEqual(report["reference_recall"], 1)
        self.assertEqual(report["lexical_fact_damage_rate"], 0)
        self.assertIsNone(report["human_accept_rate"])
        with self.assertRaises(ValueError):
            evaluate(samples, predictions[:-1])
        failures = evaluate(samples, [dict(p, status="MODEL_FORMAT_ERROR") for p in predictions])
        self.assertEqual(failures["completion_rate"], 0)
        self.assertEqual(failures["failures"], len(samples))
        self.assertIsNone(failures["reference_precision"])

    def test_truncated_response_is_format_error(self):
        client = object.__new__(LLMClient)
        client.client = Mock()
        client.model = "fake"
        client.logger = Mock()
        client.client.chat.completions.create.return_value.choices = [Mock(finish_reason="length")]
        with self.assertRaises(ModelFormatError):
            client.call_api([], max_retries=1)

    def test_timeout_is_distinct(self):
        from engine.llm_client import ModelError
        client = object.__new__(LLMClient)
        client.client = Mock()
        client.logger = Mock()
        client.model = "fake"
        class FakeTimeout(Exception):
            pass
        client.client.chat.completions.create.side_effect = FakeTimeout()
        with patch.object(openai, "APITimeoutError", FakeTimeout), self.assertRaises(ModelError) as error:
            client.call_api([], max_retries=1)
        self.assertEqual(error.exception.status, "MODEL_TIMEOUT")

    def test_format_repair_is_bounded_and_explicit(self):
        client = object.__new__(LLMClient)
        client.call_api = Mock(side_effect=["[] explanation", "[]"])
        self.assertEqual(client.call_json_api([]), [])
        self.assertEqual(client.call_api.call_count, 2)
        client.call_api = Mock(return_value="[] explanation")
        with self.assertRaises(ModelFormatError):
            client.call_json_api([])
        self.assertEqual(client.call_api.call_count, 2)


if __name__ == "__main__":
    unittest.main()
