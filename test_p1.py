import unittest
import json
import uuid
from pathlib import Path
from unittest.mock import Mock
from dataclasses import FrozenInstanceError
from engine.sentences import SentenceSegmenter, ParagraphSnapshot, utf16_length
from engine.revision_contract import parse_decisions, validate_review
from engine.llm_client import LLMClient, ModelFormatError
from engine.patches import sentence_patch_plan
from engine.patches import PatchResult, PatchRollbackError
from engine.revision_contract import SentenceDecision
from engine.sentence_pipeline import SentencePolishingPipeline


class SentenceTests(unittest.TestCase):
    def test_abbreviations_decimals_and_mixed_text(self):
        text = 'Fig. 2 shows 0.021 W m−1 K−1. Smith et al. agree, e.g. in Eq. 3. 结果稳定。继续测试！'
        nodes = SentenceSegmenter().segment(text, 7)
        self.assertEqual([n.text for n in nodes], [
            'Fig. 2 shows 0.021 W m−1 K−1.', 'Smith et al. agree, e.g. in Eq. 3.', '结果稳定。', '继续测试！'])
        self.assertEqual(nodes[0].id, 'P7:S1')
        self.assertEqual(nodes, SentenceSegmenter().segment(text, 7))

    def test_lossless_offsets_whitespace_quotes_and_emoji(self):
        text = '  😀 Results hold.”\t\tNext sentence.  '
        nodes = SentenceSegmenter().segment(text)
        self.assertEqual(len(nodes), 2)
        for node in nodes:
            self.assertEqual(text[node.start:node.end], node.text)
            left, right = node.word_bounds(text, 100)
            self.assertEqual(right-left, utf16_length(node.text))
        self.assertEqual(nodes[1].word_bounds(text, 100)[0], 100 + nodes[1].start + 1)

    def test_initials_urls_and_no_forced_semicolon_split(self):
        text = 'A. Smith used https://example.org/a; B. Jones agreed. Done.'
        self.assertEqual(len(SentenceSegmenter().segment(text)), 2)
        self.assertEqual(len(SentenceSegmenter().segment('温度稳定；湿度不变：继续观察。')), 1)

    def test_stale_source_rejected_and_snapshot_immutable(self):
        snapshot = ParagraphSnapshot(2, 'A sentence.')
        with self.assertRaises(ValueError):
            snapshot.sentences[0].word_bounds('Different.', 0)
        with self.assertRaises(FrozenInstanceError):
            snapshot.text = 'Changed'
        with self.assertRaises(FrozenInstanceError):
            snapshot.sentences[0].start = 99

    def test_degree_abbreviations(self):
        self.assertEqual([s.text for s in SentenceSegmenter().segment('She has an M.Sc. degree. Next.')],
                         ['She has an M.Sc. degree.', 'Next.'])
        self.assertEqual(len(SentenceSegmenter().segment('Samples were assigned to group A. Results were recorded.')), 2)


class ContractTests(unittest.TestCase):
    def edit(self, **extra):
        result = dict(sentence_id='P1:S1', decision='edit', reason='grammar',
                      revised_sentence='A sentence.', category='grammar', confidence=0.9)
        result.update(extra)
        return result

    def test_keep_edit_and_explicit_deletion(self):
        self.assertEqual(parse_decisions(json.dumps([self.edit(revised_sentence='')]), ['P1:S1'])[0].revised_sentence, '')
        self.assertEqual(parse_decisions('[{"sentence_id":"P1:S1","decision":"keep","reason":"fine"}]', ['P1:S1'])[0].decision, 'keep')

    def test_invalid_coverage_and_fields_fail_closed(self):
        for payload in ([], [self.edit(), self.edit()], [self.edit(sentence_id='S1')],
                        [self.edit(confidence=True)], [self.edit(confidence=float('nan'))], [self.edit(confidence=10**400)],
                        [self.edit(old='copied')], [self.edit(revised_sentence='new\rparagraph')],
                        [self.edit(revised_sentence='new\ttext')], [self.edit(revised_sentence='new\u2028line')],
                        [self.edit(revised_sentence='new\u2029paragraph')]):
            with self.subTest(payload=payload), self.assertRaises(ModelFormatError):
                parse_decisions(json.dumps(payload), ['P1:S1'])

    def test_reviewer_must_not_rewrite(self):
        proposed = parse_decisions(json.dumps([self.edit()]), ['P1:S1'])
        reviewed = parse_decisions(json.dumps([self.edit(revised_sentence='Different.')]), ['P1:S1'])
        with self.assertRaises(ModelFormatError):
            validate_review(proposed, reviewed)

    def test_live_protocol_retries_once_not_silent_keep(self):
        client = object.__new__(LLMClient)
        client.call_api = Mock(return_value='[]')
        with self.assertRaises(ModelFormatError):
            client.call_sentence_api([], ['P1:S1'])
        self.assertEqual(client.call_api.call_count, 2)


class PatchPlanTests(unittest.TestCase):
    def test_repeated_sentence_is_position_addressed(self):
        snapshot = ParagraphSnapshot(1, 'Alpha is slow. Alpha is slow.')
        edit = SentenceDecision('P1:S2', 'edit', 'grammar', 'Alpha was fast.', 'grammar', 0.9)
        patches, expected = sentence_patch_plan(snapshot, [edit])
        self.assertEqual(expected, 'Alpha is slow. Alpha was fast.')
        self.assertTrue(all(p.start >= snapshot.sentences[1].start for p in patches))
        self.assertGreater(len(patches), 1)

    def test_protected_span_and_structure_rejected(self):
        snapshot = ParagraphSnapshot(1, 'O2 was stable.')
        for revised in ('N2 was stable.', 'O2\rwas stable.'):
            with self.assertRaises(ValueError):
                sentence_patch_plan(snapshot, [SentenceDecision('P1:S1', 'edit', 'x', revised, 'grammar', 1)])

    def test_insert_delete_unicode(self):
        snapshot = ParagraphSnapshot(1, '😀 Good result. Bad filler.')
        patches, expected = sentence_patch_plan(snapshot, [
            SentenceDecision('P1:S1', 'edit', 'x', '😀 A good result.', 'grammar', 1),
            SentenceDecision('P1:S2', 'edit', 'x', '', 'wordiness', 1)])
        self.assertEqual(expected, '😀 A good result. ')


class MemoryDocument:
    def __init__(self):
        self.original = {1: 'Alpha is slow. Alpha is slow.', 2: 'O2 was stable.'}
        self.current = self.original.copy()
        self.save_calls = 0
    def open_document(self, *args, **kwargs):
        pass
    def get_total_paragraphs(self):
        return len(self.original)
    def parse_chapters(self):
        return [{'name': 'test', 'start': 1, 'end': 2}]
    def snapshot_paragraph(self, index):
        return ParagraphSnapshot(index, self.original[index])
    def save(self):
        self.save_calls += 1
    def apply_sentence_revisions(self, snapshot, decisions, protected_terms=()):
        patches, expected = sentence_patch_plan(snapshot, decisions, protected_terms)
        self.current[snapshot.index] = expected
        return PatchResult(True, patch_count=len(patches))


class SentencePipelineTests(unittest.TestCase):
    def make_pipeline(self):
        root = Path(__file__).parent / 'cache' / 'p1-tests' / uuid.uuid4().hex
        root.mkdir(parents=True)
        source = root / 'synthetic.docx'
        source.write_bytes(b'synthetic source')
        client = Mock(model='fake', base_url='fake')
        client.call_api.return_value = 'Synthetic grammar examples.'
        client.call_memory_api.return_value = ({'terms': [], 'facts': []}, [])
        def decide(messages, expected_ids, validator=None):
            result = [SentenceDecision(sid, 'edit', 'fix', 'Alpha was fast.', 'grammar', 1)
                    if sid == 'P1:S2' else SentenceDecision(sid, 'keep', 'fine') for sid in expected_ids]
            if validator is not None:
                validator(result)
            return result
        client.call_sentence_api.side_effect = decide
        document = MemoryDocument()
        pipeline = SentencePolishingPipeline(client, document, {'language': 'english', 'min_chars': 1, 'output_dir': str(root), 'use_cross_review': True})
        pipeline.cache_file = root / 'cache.json'
        pipeline.chapter_notes_dir = root / 'notes'
        pipeline.chapter_notes_dir.mkdir()
        return source, pipeline, client, document

    def test_pipeline_replays_full_sentence_suggestions_and_actual_report(self):
        source, pipeline, client, document = self.make_pipeline()
        changes, _ = pipeline.process_document(str(source))
        self.assertEqual(changes, 1)
        self.assertEqual(document.current[1], 'Alpha is slow. Alpha was fast.')
        self.assertEqual(pipeline.last_records[1]['result_sentence'], 'Alpha was fast.')
        calls = client.call_sentence_api.call_count
        document.current = document.original.copy()
        self.assertEqual(pipeline.process_document(str(source))[0], 1)
        self.assertEqual(client.call_sentence_api.call_count, calls)
        self.assertEqual(len(pipeline.last_records), 3)

    def test_format_error_is_not_keep(self):
        source, pipeline, client, document = self.make_pipeline()
        client.call_sentence_api.side_effect = None
        client.call_sentence_api.return_value = []
        pipeline.process_document(str(source))
        self.assertTrue(all(r['status'] == 'MODEL_FORMAT_ERROR' for r in pipeline.last_records))
        self.assertEqual(document.current, document.original)

    def test_unverified_rollback_never_saves(self):
        source, pipeline, _, document = self.make_pipeline()
        document.apply_sentence_revisions = Mock(side_effect=PatchRollbackError('injected'))
        with self.assertRaises(PatchRollbackError):
            pipeline.process_document(str(source))
        self.assertEqual(document.save_calls, 0)

    def test_patch_failure_report_contains_original_not_proposal(self):
        source, pipeline, _, document = self.make_pipeline()
        document.apply_sentence_revisions = Mock(return_value=PatchResult(False, 'injected'))
        pipeline.process_document(str(source))
        record = pipeline.last_records[1]
        self.assertEqual(record['status'], 'PATCH_FAILED')
        self.assertEqual(record['result_sentence'], 'Alpha is slow.')


if __name__ == '__main__':
    unittest.main()
