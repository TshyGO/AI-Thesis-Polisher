"""Explicit opt-in real Word smoke test. Creates synthetic files only."""
import unittest
import uuid
import shutil
import hashlib
from pathlib import Path
from engine.document import DocumentProcessor
from engine.pipelines import PolishingPipeline
from test_pipeline_regressions import FakeLLMClient
from engine.environment import word_available


@unittest.skipUnless(word_available(), 'desktop Microsoft Word is not available')
class WordSmoke(unittest.TestCase):
    def test_pipeline_cache_replay_into_fresh_word(self):
        root = Path(__file__).parent / "cache" / "test_artifacts" / uuid.uuid4().hex
        root.mkdir(parents=True)
        source = root / "source.docx"
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = "值得注意的是，本研究聚焦APTES和PMSA协同改性体系。\r"
            processor.doc.SaveAs2(str(source.resolve()), FileFormat=16)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        client = FakeLLMClient()
        for run in (1, 2):
            output = root / f"run-{run}.docx"
            shutil.copy2(source, output)
            with DocumentProcessor() as processor:
                pipeline = PolishingPipeline(client, processor, {
                    "language": "chinese", "min_chars": 5,
                    "original_filename": "source.docx", "output_dir": str(root),
                    "excel_output_filename": f"run-{run}.xlsx",
                })
                pipeline.cache_file = root / "suggestions.json"
                pipeline.chapter_notes_dir = root / "notes"
                pipeline.chapter_notes_dir.mkdir(exist_ok=True)
                changes, report = pipeline.process_document(str(output))
                self.assertEqual(changes, 1)
                self.assertTrue(Path(report).exists())
                self.assertEqual(pipeline.last_records[0]["status"], "✅保留")
            with DocumentProcessor() as processor:
                processor.open_document(str(output), read_only=False)
                self.assertGreater(processor.doc.Revisions.Count, 0)
                processor.doc.Revisions.AcceptAll()
                self.assertNotIn("值得注意的是", processor.doc.Content.Text)
                self.assertIn("APTES", processor.doc.Content.Text)
        self.assertEqual(len(client.stage1_prompts), 1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_synthetic_tracked_deletion_and_format_guard(self):
        root = Path(__file__).parent / "cache" / "test_artifacts" / uuid.uuid4().hex
        root.mkdir(parents=True)
        path = root / "synthetic.docx"
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = "值得注意的是，APTES含量保持不变。\rH2O保持不变。\r"
            processor.doc.Paragraphs(2).Range.Font.Subscript = True
            processor.doc.SaveAs2(str(path.resolve()), FileFormat=16)
            processor.doc.TrackRevisions = True
            self.assertTrue(processor.apply_tracked_revision(1, "值得注意的是", ""))
            self.assertFalse(processor.apply_tracked_revision(2, "H2O", "H2SO4"))
            self.assertGreater(processor.doc.Revisions.Count, 0)
            processor.save()
        with DocumentProcessor() as processor:
            processor.open_document(str(path), read_only=True)
            self.assertGreater(processor.doc.Revisions.Count, 0)
            self.assertIn("APTES", processor.doc.Content.Text)
            self.assertIn("H2O", processor.doc.Content.Text)


if __name__ == "__main__":
    unittest.main()
