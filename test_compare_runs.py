"""Offline tests for the per-sentence run comparison."""
import unittest

from benchmarks.compare_runs import divergence, edit_frequency, lost_a_chapter, seeded_sentences


def report(*outcomes):
    return {'paragraph_outcomes': [
        {'paragraph': paragraph, 'seeded': seeded, 'edited_sentences': list(edited), 'edit_written': len(edited)}
        for paragraph, seeded, edited in outcomes]}


class CompareTests(unittest.TestCase):
    A = [report((1, ['agreement'], ['P1:S1']), (2, [], [])),
         report((1, ['agreement'], ['P1:S1']), (2, [], []))]
    B = [report((1, ['agreement'], ['P1:S1']), (2, [], ['P2:S3'])),
         report((1, ['agreement'], ['P1:S1']), (2, [], ['P2:S3'])),
         report((1, ['agreement'], []), (2, [], []))]

    def test_counts_edits_per_sentence_across_runs(self):
        self.assertEqual(edit_frequency(self.A), {'P1:S1': 2})
        self.assertEqual(edit_frequency(self.B), {'P1:S1': 2, 'P2:S3': 2})

    def test_seeded_paragraphs_come_from_the_reports(self):
        self.assertEqual(seeded_sentences(self.A), {1})

    def test_divergence_reports_rate_difference_with_both_denominators(self):
        rows = {row['sentence_id']: row for row in divergence(self.A, self.B)}
        self.assertEqual(rows['P2:S3']['a_edits'], 0)
        self.assertEqual(rows['P2:S3']['b_edits'], 2)
        self.assertEqual(rows['P2:S3']['a_runs'], 2)
        self.assertEqual(rows['P2:S3']['b_runs'], 3)
        self.assertAlmostEqual(rows['P2:S3']['rate_difference'], 0.667, places=3)
        self.assertFalse(rows['P2:S3']['in_seeded_paragraph'])
        self.assertTrue(rows['P1:S1']['in_seeded_paragraph'])

    def test_largest_divergence_is_listed_first(self):
        rows = divergence(self.A, self.B)
        self.assertEqual(rows[0]['sentence_id'], 'P2:S3')

    def test_a_sentence_neither_side_edited_is_absent(self):
        self.assertNotIn('P2:S1', {row['sentence_id'] for row in divergence(self.A, self.B)})


class IncompleteRunTests(unittest.TestCase):
    def test_a_run_that_lost_a_chapter_is_recognised(self):
        self.assertTrue(lost_a_chapter({'run_summary': {'memory': {'failed_chapters': 1}}}))
        self.assertTrue(lost_a_chapter({'failure': 'BudgetExceeded'}))
        self.assertFalse(lost_a_chapter({'run_summary': {'memory': {'failed_chapters': 0}}, 'failure': None}))
        self.assertFalse(lost_a_chapter({}))


if __name__ == '__main__':
    unittest.main()
