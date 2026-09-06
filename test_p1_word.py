"""Explicit real Word tests. Only disposable synthetic documents are opened."""
import unittest
from engine.document import DocumentProcessor
from engine.revision_contract import SentenceDecision


class WordSentenceTests(unittest.TestCase):
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
