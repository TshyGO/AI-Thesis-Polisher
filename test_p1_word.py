"""Explicit real Word tests. Only disposable synthetic documents are opened."""
import unittest
from engine.document import DocumentProcessor


class WordSentenceTests(unittest.TestCase):
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
