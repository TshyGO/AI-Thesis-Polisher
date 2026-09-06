"""Editor paragraph packing: fewer calls, identical per-paragraph guarantees."""
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from engine.patches import PatchResult, sentence_patch_plan
from engine.revision_contract import SentenceDecision
from engine.sentence_pipeline import SentencePolishingPipeline
from engine.sentences import ParagraphSnapshot


PARAGRAPHS = {1: 'Alpha is slow. Alpha is slow.', 2: 'Beta was stable. Beta was stable.',
              3: 'Gamma is slow. Gamma is slow.', 4: 'Delta was stable. Delta was stable.'}


class BatchDocument:
    def __init__(self, chapters=None):
        self.original = dict(PARAGRAPHS)
        self.current = self.original.copy()
        self.chapters = chapters or [{'name': 'test', 'start': 1, 'end': 4}]
        self.save_calls = 0

    def open_document(self, *args, **kwargs):
        pass

    def get_total_paragraphs(self):
        return len(self.original)

    def parse_chapters(self):
        return [dict(chapter) for chapter in self.chapters]

    def snapshot_paragraph(self, index):
        return ParagraphSnapshot(index, self.current[index])

    def apply_sentence_revisions(self, snapshot, decisions, protected_terms=()):
        patches, expected = sentence_patch_plan(snapshot, decisions, protected_terms)
        self.current[snapshot.index] = expected
        return PatchResult(True, patch_count=len(patches))

    def save(self):
        self.save_calls += 1


def edit_second_sentence(messages, expected_ids, validator=None):
    result = [SentenceDecision(sid, 'edit', 'fix', 'Edited sentence.', 'grammar', 1)
              if sid.endswith(':S2') else SentenceDecision(sid, 'keep', 'fine') for sid in expected_ids]
    if validator is not None:
        validator(result)
    return result


class BatchingTests(unittest.TestCase):
    def make_pipeline(self, batch_size, chapters=None, review=False):
        root = Path(__file__).parent / 'cache' / 'batch-tests' / uuid.uuid4().hex
        root.mkdir(parents=True)
        source = root / 'synthetic.docx'
        source.write_bytes(b'synthetic source')
        clients = {}
        for stage in ('understanding', 'editor', 'reviewer'):
            client = Mock(model='fake-' + stage, base_url='fake')
            client.call_memory_api.return_value = ({'terms': [], 'facts': []}, [])
            client.call_sentence_api.side_effect = edit_second_sentence
            clients[stage] = client
        document = BatchDocument(chapters)
        pipeline = SentencePolishingPipeline(
            clients['editor'], document,
            {'language': 'english', 'min_chars': 1, 'output_dir': str(root),
             'use_cross_review': review, 'editor_batch_size': batch_size},
            stage_clients=clients)
        pipeline.cache_file = root / 'cache.json'
        pipeline.chapter_notes_dir = root / 'notes'
        pipeline.chapter_notes_dir.mkdir()
        return source, pipeline, clients, document

    def editor_payloads(self, clients):
        return [json.loads(call.args[0][-1]['content'])
                for call in clients['editor'].call_sentence_api.call_args_list]

    def test_one_call_covers_the_whole_batch(self):
        source, pipeline, clients, document = self.make_pipeline(4)
        changes, _ = pipeline.process_document(str(source))
        self.assertEqual(clients['editor'].call_sentence_api.call_count, 1)
        self.assertEqual(changes, 4)
        self.assertEqual([len(p['sentences']) for p in self.editor_payloads(clients)], [8])
        for index in PARAGRAPHS:
            self.assertTrue(document.current[index].endswith('Edited sentence.'), index)

    def test_default_is_one_paragraph_per_call_with_an_unchanged_payload(self):
        source, pipeline, clients, _ = self.make_pipeline(1)
        pipeline.process_document(str(source))
        self.assertEqual(clients['editor'].call_sentence_api.call_count, 4)
        payload = self.editor_payloads(clients)[0]
        self.assertEqual(set(payload), {'chapter_memory', 'context', 'sentences', 'protected_terms', 'intensity'})
        self.assertEqual(set(payload['sentences'][0]), {'sentence_id', 'text'})

    def test_a_packed_payload_labels_every_sentence_with_its_paragraph(self):
        source, pipeline, clients, _ = self.make_pipeline(2)
        pipeline.process_document(str(source))
        payload = self.editor_payloads(clients)[0]
        self.assertEqual([s['paragraph_index'] for s in payload['sentences']], [1, 1, 2, 2])

    def test_batch_members_are_not_also_sent_as_neighbour_context(self):
        source, pipeline, clients, _ = self.make_pipeline(2)
        pipeline.process_document(str(source))
        first, second = self.editor_payloads(clients)
        self.assertEqual([n['paragraph_index'] for n in first['context']], [3])
        self.assertEqual([n['paragraph_index'] for n in second['context']], [1, 2])

    def test_packing_stops_at_a_chapter_boundary(self):
        source, pipeline, clients, _ = self.make_pipeline(
            4, chapters=[{'name': 'A', 'start': 1, 'end': 2}, {'name': 'B', 'start': 3, 'end': 4}])
        pipeline.process_document(str(source))
        payloads = self.editor_payloads(clients)
        self.assertEqual(len(payloads), 2)
        self.assertEqual([sorted({s['paragraph_index'] for s in p['sentences']}) for p in payloads],
                         [[1, 2], [3, 4]])
        for payload in payloads:
            self.assertEqual(payload['context'], [])

    def test_a_failed_batch_falls_back_to_single_paragraphs_and_never_to_keep(self):
        source, pipeline, clients, document = self.make_pipeline(4)
        calls = []

        def flaky(messages, expected_ids, validator=None):
            calls.append(list(expected_ids))
            if len(expected_ids) > 2:
                raise RuntimeError('batch exploded')
            return edit_second_sentence(messages, expected_ids, validator)

        clients['editor'].call_sentence_api.side_effect = flaky
        changes, _ = pipeline.process_document(str(source))
        self.assertEqual(len(calls[0]), 8)
        self.assertEqual([len(ids) for ids in calls[1:]], [2, 2, 2, 2])
        self.assertEqual(changes, 4)
        self.assertFalse(any(r['status'] == 'KEEP' and r['sentence_id'].endswith(':S2')
                             for r in pipeline.last_records))

    def test_each_paragraph_only_receives_its_own_decisions(self):
        source, pipeline, clients, document = self.make_pipeline(4)
        pipeline.process_document(str(source))
        for record in pipeline.last_records:
            self.assertTrue(record['sentence_id'].startswith('P%d:' % record['paragraph_idx']))
            self.assertIn(record['sentence'], PARAGRAPHS[record['paragraph_idx']])

    def test_cached_paragraphs_are_not_re_sent_in_a_later_batch(self):
        source, pipeline, clients, document = self.make_pipeline(4)
        pipeline.process_document(str(source))
        document.current = document.original.copy()
        pipeline.process_document(str(source))
        self.assertEqual(clients['editor'].call_sentence_api.call_count, 1)

    def test_batch_size_is_part_of_the_cache_identity(self):
        source, pipeline, _, _ = self.make_pipeline(2)
        first = pipeline.get_cache_key(str(source), 1, PARAGRAPHS[1])
        pipeline.config['editor_batch_size'] = 4
        self.assertNotEqual(first, pipeline.get_cache_key(str(source), 1, PARAGRAPHS[1]))

    def test_review_still_runs_per_paragraph_on_its_own_candidates(self):
        source, pipeline, clients, _ = self.make_pipeline(4, review=True)
        pipeline.process_document(str(source))
        self.assertEqual(clients['reviewer'].call_sentence_api.call_count, 4)
        for call in clients['reviewer'].call_sentence_api.call_args_list:
            payload = json.loads(call.args[0][-1]['content'])
            self.assertEqual(len({sid.split(':')[0] for sid in payload['source']}), 1)


if __name__ == '__main__':
    unittest.main()
