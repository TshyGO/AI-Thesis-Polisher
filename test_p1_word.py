"""Explicit real Word tests. Only disposable synthetic documents are opened."""
import unittest
import uuid
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock
from engine.document import DocumentProcessor
from engine.revision_contract import SentenceDecision
from engine.chapter_memory import build_memory, MemoryValidationError


class WordSentenceTests(unittest.TestCase):
    def test_real_heading_scopes_keep_memory_sources_separate(self):
        root = Path(__file__).parent / 'cache' / ('word-memory-' + uuid.uuid4().hex)
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = ('Experiment A\rAPTES is reagent A.\rThe APTES samples was cured.\r'
                                          'Experiment B\rAPTES is reagent B.\rThe APTES sample were cured.\r')
            processor.doc.Paragraphs(1).OutlineLevel = 1
            processor.doc.Paragraphs(4).OutlineLevel = 1
            chapters = processor.parse_chapters()
            self.assertEqual([c['start'] for c in chapters], [1, 4])
            snapshots = [processor.snapshot_paragraph(n) for n in range(1, processor.get_total_paragraphs()+1)]
            client = Mock()
            client.call_memory_api.side_effect = lambda messages, sources: ({
                'terms': [{'source_id': s.id, 'text': 'APTES', 'kind': 'abbreviation'} for s in sources if s.text.startswith('APTES')],
                'facts': [s.id for s in sources]}, [])
            digest = hashlib.sha256(processor.doc.Content.Text.encode('utf-8')).hexdigest()
            memories = [build_memory(c, [s for s in snapshots if c['start'] <= s.index <= c['end']],
                                     digest, client, 'fake', root) for c in chapters]
            context = json.dumps(memories[1].context_for(snapshots[5]))
            self.assertIn('reagent B', context)
            self.assertNotIn('reagent A', context)
            with self.assertRaises(MemoryValidationError):
                memories[0].context_for(snapshots[5])

    def test_mixed_highlight_and_language_are_not_flattened(self):
        for property_name, value in [('HighlightColorIndex', 7), ('LanguageID', 1036)]:
            with self.subTest(property_name=property_name), DocumentProcessor() as processor:
                processor.doc = processor.word.Documents.Add()
                processor.doc.Content.Text = 'abcdef.\r'
                setattr(processor.doc.Range(3, 6), property_name, value)
                snapshot = processor.snapshot_paragraph(1)
                before = processor.doc.Content.Text
                processor.doc.TrackRevisions = True
                result = processor.apply_sentence_revisions(snapshot, [
                    SentenceDecision('P1:S1', 'edit', 'clarity', 'uvwxyz.', 'clarity', 1)])
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, 'UNSAFE_OR_MIXED_FORMATTING')
                self.assertEqual(processor.doc.Content.Text, before)
                self.assertEqual(processor.doc.Revisions.Count, 0)

    def test_saved_unicode_insertions_preserve_bold_and_revisions(self):
        path = Path(__file__).parent / 'cache' / ('p1-unicode-' + uuid.uuid4().hex + '.docx')
        path.parent.mkdir(exist_ok=True)
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = '😀 The result is good.\r'
            processor.doc.Content.Font.Bold = True
            snapshot = processor.snapshot_paragraph(1)
            processor.doc.TrackRevisions = True
            result = processor.apply_sentence_revisions(snapshot, [
                SentenceDecision('P1:S1', 'edit', 'grammar', '😀 The result was very good.', 'grammar', 1)])
            self.assertTrue(result.ok, result.reason)
            processor.doc.SaveAs2(str(path.resolve()), FileFormat=16)
        with DocumentProcessor() as processor:
            processor.open_document(str(path), read_only=True)
            self.assertEqual(processor._visible_paragraph_text(1), '😀 The result was very good.')
            self.assertGreater(processor.doc.Revisions.Count, 0)
            self.assertEqual(processor.doc.Range(3, 6).Font.Bold, -1)

    def test_stale_source_and_bookmarks_fail_without_mutation(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = 'Alpha is slow.\r'
            stale = processor.snapshot_paragraph(1)
            processor.doc.Range(0, 5).Text = 'Other'
            processor.doc.TrackRevisions = True
            edit = SentenceDecision('P1:S1', 'edit', 'grammar', 'Alpha was fast.', 'grammar', 1)
            self.assertEqual(processor.apply_sentence_revisions(stale, [edit]).reason, 'STALE_SOURCE')
            processor.doc.TrackRevisions = False
            processor.doc.Bookmarks.Add('protected', processor.doc.Range(0, 5))
            snapshot = processor.snapshot_paragraph(1)
            processor.doc.TrackRevisions = True
            result = processor.apply_sentence_revisions(snapshot, [SentenceDecision('P1:S1', 'edit', 'grammar', 'Other was fast.', 'grammar', 1)])
            self.assertTrue(result.reason.startswith('STRUCTURAL_CONTENT'), result.reason)
            self.assertEqual(processor.doc.Revisions.Count, 0)

    def test_final_text_verification_failure_rolls_back(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = 'Alpha is slow.\r'
            before = processor.doc.Content.Text
            snapshot = processor.snapshot_paragraph(1)
            processor.doc.TrackRevisions = True
            processor._visible_paragraph_text = lambda index: 'incorrect projection'
            result = processor.apply_sentence_revisions(snapshot, [
                SentenceDecision('P1:S1', 'edit', 'grammar', 'Alpha was fast.', 'grammar', 1)])
            self.assertFalse(result.ok)
            self.assertTrue(result.reason.startswith('ROLLED_BACK'), result.reason)
            self.assertEqual(processor.doc.Content.Text, before)
            self.assertEqual(processor.doc.Revisions.Count, 0)

    def test_multi_patch_repeated_sentence_and_format_preservation(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = 'Alpha is slow. Alpha is slow.\r'
            processor.doc.Range(0, 5).Font.Bold = True
            snapshot = processor.snapshot_paragraph(1)
            processor.doc.TrackRevisions = True
            result = processor.apply_sentence_revisions(snapshot, [
                SentenceDecision('P1:S2', 'edit', 'grammar', 'Alpha was fast.', 'grammar', 1)])
            self.assertTrue(result.ok, result.reason)
            self.assertGreater(result.patch_count, 1)
            self.assertEqual(processor._visible_paragraph_text(1), 'Alpha is slow. Alpha was fast.')
            self.assertEqual(processor.doc.Range(0, 5).Font.Bold, -1)
            self.assertGreater(processor.doc.Revisions.Count, 0)

    def test_mid_transaction_failure_rolls_back_all_patches(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = 'Alpha is slow.\r'
            before_text = processor.doc.Content.Text
            snapshot = processor.snapshot_paragraph(1)
            processor.doc.TrackRevisions = True
            original_assign = processor._assign_patch
            calls = []
            def fail_second(range_, text):
                calls.append(text)
                if len(calls) == 2:
                    raise RuntimeError('injected assignment failure')
                original_assign(range_, text)
            processor._assign_patch = fail_second
            result = processor.apply_sentence_revisions(snapshot, [
                SentenceDecision('P1:S1', 'edit', 'grammar', 'Alpha was fast.', 'grammar', 1)])
            self.assertFalse(result.ok)
            self.assertTrue(result.reason.startswith('ROLLED_BACK'), result.reason)
            self.assertEqual(processor.doc.Content.Text, before_text)
            self.assertEqual(processor.doc.Revisions.Count, 0)

    def test_table_paragraph_is_explicitly_blocked(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            table = processor.doc.Tables.Add(processor.doc.Range(0, 0), 1, 1)
            table.Cell(1, 1).Range.Text = 'A cell.'
            self.assertTrue(processor.snapshot_paragraph(1).blocked_reason.startswith('TABLE_PARAGRAPH'))

    def test_exact_word_ranges_and_existing_revision_detection(self):
        with DocumentProcessor() as processor:
            processor.doc = processor.word.Documents.Add()
            processor.doc.Content.Text = '  😀 Fig. 2 holds.\t另一句话。\r'
            snapshot = processor.snapshot_paragraph(1)
            self.assertTrue(snapshot.text.startswith('  😀'))
            self.assertFalse(snapshot.blocked_reason)
            for sentence in snapshot.sentences:
                bounds = sentence.word_bounds(snapshot.text, snapshot.word_start)
                self.assertEqual(processor.doc.Range(*bounds).Text, sentence.text)
            processor.doc.TrackRevisions = True
            processor.doc.Range(0, 0).Text = 'new '
            self.assertTrue(processor.snapshot_paragraph(1).blocked_reason.startswith('EXISTING_REVISIONS'))


if __name__ == '__main__':
    unittest.main()
