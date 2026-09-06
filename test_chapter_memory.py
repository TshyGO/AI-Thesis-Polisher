import json
import unittest
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch
import test_p1 as fixtures
from engine.sentences import ParagraphSnapshot
from engine.chapter_memory import build_memory, parse_selection, MemoryValidationError
from engine.llm_client import LLMClient


class ChapterMemoryTests(unittest.TestCase):
    def fixture(self):
        root = Path(__file__).parent / 'cache' / 'memory-tests' / uuid.uuid4().hex
        root.mkdir(parents=True)
        snapshots = [ParagraphSnapshot(1, 'APTES denotes reagent A. Curing took place at 80 ℃.'),
                     ParagraphSnapshot(2, 'The APTES samples was cured.')]
        chapter = {'name': 'Experiment A', 'start': 1, 'end': 2}
        client = Mock()
        client.call_memory_api.return_value = {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'}],
                                               'facts': ['P1:S2', 'P2:S1']}
        return root, snapshots, chapter, client

    def test_quotes_positions_and_protection_are_derived_from_source(self):
        root, snapshots, chapter, client = self.fixture()
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root, user_terms=['APTES'])
        data = memory.to_dict()
        self.assertEqual(data['facts'][0]['quote'], snapshots[0].sentences[1].text)
        term = data['terms'][0]
        self.assertEqual(term['protection'], 'user')
        self.assertEqual(snapshots[0].text[term['occurrences'][0][0]:term['occurrences'][0][1]], 'APTES')
        with self.assertRaises(FrozenInstanceError):
            memory.title = 'fake'
        data['terms'][0]['text'] = 'fake'
        self.assertEqual(memory.to_dict()['terms'][0]['text'], 'APTES')

    def test_retrieval_is_local_scoped_and_does_not_copy_unrelated_values(self):
        root, snapshots, chapter, client = self.fixture()
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        context = memory.context_for(snapshots[1])
        self.assertEqual(context['fact_refs'], ['P2:S1'])
        self.assertNotIn('80', json.dumps(context))
        self.assertEqual(context['terms'][0]['source']['source_id'], 'P1:S1')
        with self.assertRaises(MemoryValidationError):
            memory.context_for(ParagraphSnapshot(3, 'The APTES samples was cured.'))
        with self.assertRaises(MemoryValidationError):
            memory.context_for(ParagraphSnapshot(2, 'Changed source.'))

    def test_unrelated_term_prefix_and_context_budget(self):
        root, snapshots, chapter, client = self.fixture()
        snapshots.append(ParagraphSnapshot(3, 'APTESX is stable.'))
        chapter['end'] = 3
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        self.assertEqual(memory.context_for(snapshots[2])['terms'], [])
        context = memory.context_for(snapshots[1], budget=160)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), 160)
        self.assertTrue(context['selection_truncated'])

    def test_invented_quotes_sources_and_protection_claims_are_rejected(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        invalid = [
            {'terms': [], 'facts': ['P99:S1']},
            {'terms': [], 'facts': [{'source_id': 'P1:S1', 'quote': 'Invented 999'}]},
            {'terms': [{'source_id': 'P1:S1', 'text': 'TES', 'kind': 'abbreviation'}], 'facts': []},
            {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'term', 'protected': True}], 'facts': []},
            {'terms': [], 'facts': [], 'summary': 'new fact'},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(MemoryValidationError):
                parse_selection(json.dumps(item), sources)
        with self.assertRaises(MemoryValidationError):
            parse_selection('{"terms":[],"terms":[],"facts":[]}', sources)

    def test_cache_is_revalidated_and_invalidated_by_source_and_model(self):
        root, snapshots, chapter, client = self.fixture()
        build_memory(chapter, snapshots, 'doc', client, 'model', root)
        build_memory(chapter, snapshots, 'doc', client, 'model', root)
        self.assertEqual(client.call_memory_api.call_count, 1)
        next(root.glob('memory-*.json')).write_text('[{"terms":[],"facts":["P99:S1"]}]', encoding='utf-8')
        build_memory(chapter, snapshots, 'doc', client, 'model', root)
        self.assertEqual(client.call_memory_api.call_count, 2)
        build_memory(chapter, snapshots, 'other-doc', client, 'model', root)
        build_memory(chapter, snapshots, 'doc', client, 'other-model', root)
        self.assertEqual(client.call_memory_api.call_count, 4)

    def test_chunk_budget_reports_omissions_without_truncating_quotes(self):
        root, snapshots, chapter, client = self.fixture()
        client.call_memory_api.side_effect = lambda messages, sources: {'terms': [], 'facts': [s.id for s in sources]}
        with patch('engine.chapter_memory.MAX_CHUNK_SENTENCES', 1), patch('engine.chapter_memory.MAX_CHUNKS', 1):
            memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        self.assertEqual(memory.omitted_ids, ('P1:S2', 'P2:S1'))
        self.assertEqual(memory.to_dict()['facts'][0]['quote'], snapshots[0].sentences[0].text)
        self.assertEqual(client.call_memory_api.call_count, 1)

    def test_model_repair_is_bounded(self):
        _, snapshots, _, _ = self.fixture()
        client = object.__new__(LLMClient)
        client.call_api = Mock(return_value='{"terms":[],"facts":["wrong"]}')
        with self.assertRaises(MemoryValidationError):
            client.call_memory_api([], snapshots[0].sentences)
        self.assertEqual(client.call_api.call_count, 2)

    def test_pipeline_never_passes_neighbors_across_chapter_boundary(self):
        source, pipeline, client, document = fixtures.SentencePipelineTests().make_pipeline()
        document.parse_chapters = lambda: [{'name': 'A', 'start': 1, 'end': 1}, {'name': 'B', 'start': 2, 'end': 2}]
        pipeline.process_document(str(source))
        prompts = [json.loads(call.args[0][-1]['content']) for call in client.call_sentence_api.call_args_list
                   if 'sentences' in json.loads(call.args[0][-1]['content'])]
        self.assertTrue(all(prompt['context'] == [] for prompt in prompts))
        self.assertEqual(prompts[0]['chapter_memory']['chapter_id'], 'C1:1')
        self.assertEqual(prompts[1]['chapter_memory']['chapter_id'], 'C2:2')

    def test_failed_chapter_memory_is_not_retried_per_paragraph_or_treated_as_keep(self):
        source, pipeline, client, document = fixtures.SentencePipelineTests().make_pipeline()
        client.call_memory_api.side_effect = MemoryValidationError('ungrounded')
        pipeline.process_document(str(source))
        self.assertEqual(client.call_memory_api.call_count, 1)
        self.assertTrue(all(r['status'] == 'MEMORY_VALIDATION_ERROR' for r in pipeline.last_records))
        self.assertEqual(document.current, document.original)


if __name__ == '__main__':
    unittest.main()
