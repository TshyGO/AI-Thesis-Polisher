"""Offline tests for the cost-profiling harness. No network, no Word."""
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.cost_profile import (BudgetExceeded, Recorder, paragraph_outcomes,
                                     payload_shape, reference_agreement, summarize)
from benchmarks.textdoc import MemoryDocument, load_corpus
from engine.revision_contract import SentenceDecision
from engine.sentences import ParagraphSnapshot


CORPUS = Path(__file__).parent / 'benchmarks' / 'chapter_corpus.jsonl'


def write_corpus(rows):
    handle = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8')
    handle.write('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows))
    handle.close()
    return handle.name


class CorpusTests(unittest.TestCase):
    def test_checked_in_corpus_is_contiguous_and_labelled(self):
        texts, chapters, rows = load_corpus(CORPUS)
        self.assertEqual(len(texts), len(rows))
        self.assertEqual([c['start'] for c in chapters], sorted(c['start'] for c in chapters))
        self.assertEqual(chapters[0]['start'], 1)
        self.assertEqual(chapters[-1]['end'], len(texts))
        for left, right in zip(chapters, chapters[1:]):
            self.assertEqual(left['end'] + 1, right['start'])
        self.assertTrue(any(row['seeded'] for row in rows))
        self.assertTrue(any(not row['seeded'] for row in rows))

    def test_seeded_and_protected_strings_are_present_in_source(self):
        _, _, rows = load_corpus(CORPUS)
        for row in rows:
            for term in row['must_keep']:
                self.assertIn(term, row['text'], row['order'])

    def test_out_of_order_paragraphs_are_rejected(self):
        path = write_corpus([{'chapter': 'A', 'order': 2, 'text': 'x', 'seeded': [], 'must_keep': []}])
        with self.assertRaises(ValueError):
            load_corpus(path)

    def test_split_chapter_is_rejected(self):
        path = write_corpus([{'chapter': 'A', 'order': 1, 'text': 'x', 'seeded': [], 'must_keep': []},
                             {'chapter': 'B', 'order': 2, 'text': 'y', 'seeded': [], 'must_keep': []},
                             {'chapter': 'A', 'order': 3, 'text': 'z', 'seeded': [], 'must_keep': []}])
        with self.assertRaises(ValueError):
            load_corpus(path)


class MemoryDocumentTests(unittest.TestCase):
    SOURCE = 'The results shows that the capacity increased. A second sentence follows.'

    def build(self, tracking=True):
        document = MemoryDocument([self.SOURCE], [{'name': 'A', 'start': 1, 'end': 1}])
        document.open_document('corpus', track_revisions=tracking)
        return document

    def decision(self, revised, sentence_id='P1:S1'):
        return SentenceDecision(sentence_id, 'edit', 'agreement', revised, 'grammar', 0.9)

    def test_applies_validated_edit_to_the_in_memory_text(self):
        document = self.build()
        snapshot = document.snapshot_paragraph(1)
        result = document.apply_sentence_revisions(
            snapshot, [self.decision('The results show that the capacity increased.')])
        self.assertTrue(result.ok, result.reason)
        self.assertTrue(document.current[0].startswith('The results show that'))
        self.assertEqual(document.original[0], self.SOURCE)

    def test_refuses_when_revision_tracking_was_never_requested(self):
        document = self.build(tracking=False)
        result = document.apply_sentence_revisions(
            document.snapshot_paragraph(1), [self.decision('The results show that the capacity increased.')])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, 'TRACKING_DISABLED')

    def test_refuses_a_stale_snapshot(self):
        document = self.build()
        snapshot = document.snapshot_paragraph(1)
        document.current[0] = 'Something else entirely happened here.'
        result = document.apply_sentence_revisions(snapshot, [self.decision('The results show that.')])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, 'STALE_SOURCE')

    def test_production_validator_still_rejects_a_number_change(self):
        document = MemoryDocument(['The capacity reached 12.5 mg/g in this run.'],
                                  [{'name': 'A', 'start': 1, 'end': 1}])
        document.open_document('corpus', track_revisions=True)
        snapshot = document.snapshot_paragraph(1)
        result = document.apply_sentence_revisions(
            snapshot, [self.decision('The capacity reached 21.5 mg/g in this run.')])
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith('VALIDATION_REJECTED'), result.reason)
        self.assertEqual(document.current[0], 'The capacity reached 12.5 mg/g in this run.')


class RecorderTests(unittest.TestCase):
    def test_cap_raises_before_any_further_request(self):
        recorder = Recorder(1)
        recorder.reserve('editor')
        recorder.add({'stage': 'editor'})
        with self.assertRaises(BudgetExceeded):
            recorder.reserve('editor')
        self.assertEqual(len(recorder.rows), 1)


class PayloadShapeTests(unittest.TestCase):
    def test_splits_target_context_and_memory_characters(self):
        body = {'chapter_memory': {'terms': ['APTES']}, 'context': [{'paragraph_index': 1, 'text': 'abcde'}],
                'sentences': [{'sentence_id': 'P2:S1', 'text': 'xyz'}]}
        shape = payload_shape([{'role': 'system', 'content': 'policy'},
                               {'role': 'user', 'content': json.dumps(body, ensure_ascii=False)}])
        self.assertEqual(shape['target_chars'], 3)
        self.assertEqual(shape['context_chars'], 5)
        self.assertEqual(shape['source_chars'], 8)
        self.assertGreater(shape['memory_chars'], 0)
        self.assertGreater(shape['prompt_chars'], shape['source_chars'])

    def test_marks_a_repair_turn_and_keeps_the_original_payload_shape(self):
        body = {'sentences': [{'sentence_id': 'P7:S1', 'text': 'xyz'}]}
        messages = [{'role': 'system', 'content': 'policy'},
                    {'role': 'user', 'content': json.dumps(body)},
                    {'role': 'assistant', 'content': 'broken'},
                    {'role': 'user', 'content': 'Invalid contract'}]
        shape = payload_shape(messages)
        self.assertTrue(shape['is_repair'])
        self.assertEqual(shape['target_chars'], 3)
        self.assertEqual(shape['paragraphs'], ['P7'])
        self.assertFalse(payload_shape(messages[:2])['is_repair'])

    def test_reviewer_payload_reports_its_paragraph(self):
        body = {'source': {'P4:S2': 'abc'}, 'proposals': [{'sentence_id': 'P4:S2'}]}
        shape = payload_shape([{'role': 'user', 'content': json.dumps(body)}])
        self.assertEqual(shape['paragraphs'], ['P4'])
        self.assertEqual(shape['target_chars'], 3)

    def test_non_json_prompt_still_reports_prompt_characters(self):
        shape = payload_shape([{'role': 'user', 'content': 'plain chapter text'}])
        self.assertEqual(shape['prompt_chars'], len('plain chapter text'))
        self.assertEqual(shape['source_chars'], 0)


class SummaryTests(unittest.TestCase):
    ROWS = [{'stage': 'editor', 'status': 'OK', 'seconds': 1.0, 'prompt_tokens': 100,
             'completion_tokens': 20, 'prompt_chars': 900, 'source_chars': 300, 'memory_chars': 100},
            {'stage': 'editor', 'status': 'MODEL_TIMEOUT', 'seconds': 2.0, 'prompt_tokens': None,
             'completion_tokens': None, 'prompt_chars': 900, 'source_chars': 300, 'memory_chars': 100},
            {'stage': 'reviewer', 'status': 'OK', 'seconds': 0.5, 'prompt_tokens': 50,
             'completion_tokens': 10, 'prompt_chars': 400, 'source_chars': 100, 'memory_chars': 50}]
    RECORDS = [{'paragraph_idx': 1, 'status': 'EDIT_WRITTEN'}, {'paragraph_idx': 1, 'status': 'KEEP'},
               {'paragraph_idx': 2, 'status': 'KEEP'}, {'paragraph_idx': 3, 'status': 'REVIEW_REJECTED'}]

    def test_stage_totals_keep_failed_attempts_and_unknown_tokens_visible(self):
        summary = summarize(self.ROWS, self.RECORDS, corpus_chars=300, paragraph_count=3)
        self.assertEqual(summary['stages']['editor']['attempts'], 2)
        self.assertEqual(summary['stages']['editor']['ok'], 1)
        self.assertEqual(summary['stages']['editor']['unknown_token_attempts'], 1)
        self.assertEqual(summary['totals']['attempts'], 3)
        self.assertEqual(summary['totals']['prompt_tokens'], 150)

    def test_repair_attempts_are_counted_even_though_transport_status_is_ok(self):
        rows = self.ROWS + [{'stage': 'understanding', 'status': 'OK', 'seconds': 1.0, 'is_repair': True,
                             'prompt_tokens': 10, 'completion_tokens': 1, 'prompt_chars': 100,
                             'source_chars': 0, 'memory_chars': 0}]
        summary = summarize(rows, self.RECORDS, corpus_chars=300, paragraph_count=3)
        self.assertEqual(summary['stages']['understanding']['repairs'], 1)
        self.assertEqual(summary['stages']['understanding']['ok'], 1)
        self.assertEqual(summary['stages']['editor']['repairs'], 0)

    def test_zero_edit_paragraphs_count_paragraphs_not_sentence_rows(self):
        summary = summarize(self.ROWS, self.RECORDS, corpus_chars=300, paragraph_count=3)
        self.assertEqual(summary['paragraphs']['reported'], 3)
        self.assertEqual(summary['paragraphs']['with_edit_written'], 1)
        self.assertEqual(summary['paragraphs']['zero_edit'], 2)
        self.assertAlmostEqual(summary['paragraphs']['zero_edit_ratio'], 2 / 3, places=3)

    def test_amplification_counts_only_editor_source_text(self):
        summary = summarize(self.ROWS, self.RECORDS, corpus_chars=300, paragraph_count=3)
        self.assertEqual(summary['amplification']['editor_source_chars'], 600)
        self.assertEqual(summary['amplification']['source_text_factor'], 2.0)


class OutcomeTests(unittest.TestCase):
    RECORDS = [{'paragraph_idx': 1, 'sentence_id': 'P1:S1', 'status': 'EDIT_WRITTEN'},
               {'paragraph_idx': 1, 'sentence_id': 'P1:S2', 'status': 'KEEP'},
               {'paragraph_idx': 2, 'sentence_id': 'P2:S1', 'status': 'KEEP'}]
    ROWS = [{'chapter': 'A', 'order': 1, 'text': 'x', 'seeded': ['agreement:a'], 'must_keep': []},
            {'chapter': 'A', 'order': 2, 'text': 'y', 'seeded': [], 'must_keep': []}]

    def test_outcomes_record_which_sentences_were_written(self):
        outcomes = paragraph_outcomes(self.RECORDS, self.ROWS)
        self.assertEqual([o['paragraph'] for o in outcomes], [1, 2])
        self.assertEqual(outcomes[0]['edited_sentences'], ['P1:S1'])
        self.assertEqual(outcomes[0]['edit_written'], 1)
        self.assertEqual(outcomes[0]['seeded'], ['agreement:a'])
        self.assertEqual(outcomes[1]['edit_written'], 0)

    def test_reference_agreement_counts_both_directions(self):
        agreement = reference_agreement(paragraph_outcomes(self.RECORDS, self.ROWS))
        self.assertEqual(agreement, {'seeded_paragraphs': 1, 'seeded_without_edit': 0,
                                     'clean_paragraphs': 1, 'clean_with_edit': 0})

    def test_reference_agreement_is_none_without_corpus_labels(self):
        self.assertIsNone(reference_agreement(paragraph_outcomes(self.RECORDS)))


class SegmentationTests(unittest.TestCase):
    def test_corpus_sentences_are_recoverable_from_offsets(self):
        texts, _, _ = load_corpus(CORPUS)
        for index, text in enumerate(texts, 1):
            snapshot = ParagraphSnapshot(index, text)
            self.assertTrue(snapshot.sentences)
            for sentence in snapshot.sentences:
                self.assertEqual(text[sentence.start:sentence.end], sentence.text)


if __name__ == '__main__':
    unittest.main()
