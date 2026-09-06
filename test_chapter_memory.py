import json
import unittest
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
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
        client.call_memory_api.return_value = ({'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'}],
                                                'facts': ['P1:S2', 'P2:S1']}, [])
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

    def test_shorter_term_source_can_fit_after_long_source_is_omitted(self):
        root, snapshots, chapter, client = self.fixture()
        snapshots[0] = ParagraphSnapshot(1, 'APTES ' + 'long ' * 400 + '.')
        client.call_memory_api.return_value = ({'terms': [
            {'source_id': sid, 'text': 'APTES', 'kind': 'abbreviation'} for sid in ('P1:S1', 'P2:S1')], 'facts': []}, [])
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        context = memory.context_for(snapshots[1], budget=500)
        self.assertEqual(context['terms'][0]['source']['source_id'], 'P2:S1')
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), 500)

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

    def test_one_ungrounded_term_is_dropped_instead_of_losing_the_chapter(self):
        root, snapshots, chapter, client = self.fixture()
        client.call_memory_api.return_value = (
            {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'}], 'facts': ['P1:S2']},
            [{'kind': 'term', 'reason': 'Term does not occur literally in the cited source',
              'source_id': 'P1:S1', 'text': 'Invented'}])
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        data = memory.to_dict()
        self.assertEqual([term['text'] for term in data['terms']], ['APTES'])
        self.assertEqual(data['rejected_selection_count'], 1)
        self.assertEqual(data['selection_rejections'][0]['text'], 'Invented')

    def test_a_rejected_term_never_reaches_the_editor_context(self):
        root, snapshots, chapter, client = self.fixture()
        client.call_memory_api.return_value = (
            {'terms': [], 'facts': ['P2:S1']},
            [{'kind': 'term', 'reason': 'Term does not occur literally in the cited source',
              'source_id': 'P2:S1', 'text': 'Invented'}])
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        context = memory.context_for(snapshots[1])
        self.assertNotIn('Invented', json.dumps(context, ensure_ascii=False))
        self.assertEqual(context['terms'], [])

    def test_rejections_survive_a_cache_hit(self):
        root, snapshots, chapter, client = self.fixture()
        client.call_memory_api.return_value = (
            {'terms': [], 'facts': ['P1:S2']},
            [{'kind': 'fact', 'reason': 'Fact must reference a source in this chapter chunk',
              'source_id': 'P99:S1', 'text': None}])
        first = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        second = build_memory(chapter, snapshots, 'doc', client, 'model', root)
        self.assertEqual(client.call_memory_api.call_count, 1)
        self.assertEqual(second.to_dict()['selection_rejections'], first.to_dict()['selection_rejections'])

    def test_salvage_keeps_grounded_items_and_reports_the_rest(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        payload = {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'},
                             {'source_id': 'P1:S1', 'text': 'Invented', 'kind': 'term'}],
                   'facts': ['P1:S2', 'P99:S1', 'P1:S2']}
        selection, rejections = parse_selection(json.dumps(payload), sources, salvage=True)
        self.assertEqual([term['text'] for term in selection['terms']], ['APTES'])
        self.assertEqual(selection['facts'], ['P1:S2'])
        self.assertEqual([r['reason'] for r in rejections],
                         ['Term does not occur literally in the cited source',
                          'Fact must reference a source in this chapter chunk',
                          'Duplicate fact source'])

    def test_an_over_budget_selection_is_trimmed_instead_of_failing_the_chapter(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        payload = {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'}]
                            + [{'source_id': 'P1:S1', 'text': 'reagent', 'kind': 'term'}] * 40,
                   'facts': ['P1:S2'] * 70}
        selection, rejections = parse_selection(json.dumps(payload), sources, salvage=True)
        self.assertLessEqual(len(selection['terms']), 32)
        self.assertLessEqual(len(selection['facts']), 64)
        self.assertEqual(selection['facts'], ['P1:S2'])
        self.assertEqual([term['text'] for term in selection['terms']], ['APTES', 'reagent'])
        overflow = [r['reason'] for r in rejections if r['reason'].startswith('Selection budget exceeded')]
        self.assertEqual(overflow, ['Selection budget exceeded: 9 terms dropped',
                                    'Selection budget exceeded: 6 facts dropped'])

    def test_a_cached_over_budget_selection_is_still_refused(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        payload = {'terms': [{'source_id': 'P1:S1', 'text': 'APTES', 'kind': 'abbreviation'}] * 40, 'facts': []}
        with self.assertRaises(MemoryValidationError):
            parse_selection(json.dumps(payload), sources)

    def test_salvage_still_fails_when_nothing_proposed_is_grounded(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        for payload in ({'terms': [{'source_id': 'P1:S1', 'text': 'TES', 'kind': 'term'}], 'facts': []},
                        {'terms': [], 'facts': ['P99:S1']},
                        {'terms': [], 'facts': [], 'summary': 'new fact'}):
            with self.subTest(payload=payload), self.assertRaises(MemoryValidationError):
                parse_selection(json.dumps(payload), sources, salvage=True)

    def test_salvage_accepts_a_deliberately_empty_selection(self):
        _, snapshots, _, _ = self.fixture()
        sources = tuple(s for p in snapshots for s in p.sentences)
        selection, rejections = parse_selection('{"terms":[],"facts":[]}', sources, salvage=True)
        self.assertEqual((selection, rejections), ({'terms': [], 'facts': []}, []))

    def test_repair_is_requested_once_for_rejections_and_the_better_answer_wins(self):
        _, snapshots, _, _ = self.fixture()
        sources = snapshots[0].sentences
        client = object.__new__(LLMClient)
        client.call_api = Mock(side_effect=[
            '{"terms":[{"source_id":"P1:S1","text":"APTES","kind":"abbreviation"},'
            '{"source_id":"P1:S1","text":"Invented","kind":"term"}],"facts":[]}',
            '{"terms":[{"source_id":"P1:S1","text":"APTES","kind":"abbreviation"}],"facts":[]}'])
        selection, rejections = client.call_memory_api([], sources)
        self.assertEqual(client.call_api.call_count, 2)
        self.assertEqual([term['text'] for term in selection['terms']], ['APTES'])
        self.assertEqual(rejections, [])

    def test_an_empty_repair_never_erases_grounded_selections(self):
        _, snapshots, _, _ = self.fixture()
        sources = snapshots[0].sentences
        client = object.__new__(LLMClient)
        client.call_api = Mock(side_effect=[
            '{"terms":[{"source_id":"P1:S1","text":"APTES","kind":"abbreviation"},'
            '{"source_id":"P1:S1","text":"Invented","kind":"term"}],"facts":["P1:S2"]}',
            '{"terms":[],"facts":[]}'])
        selection, rejections = client.call_memory_api([], sources)
        self.assertEqual([term['text'] for term in selection['terms']], ['APTES'])
        self.assertEqual(selection['facts'], ['P1:S2'])
        self.assertEqual(len(rejections), 1)

    def test_a_deliberately_empty_first_answer_costs_no_repair(self):
        _, snapshots, _, _ = self.fixture()
        client = object.__new__(LLMClient)
        client.call_api = Mock(return_value='{"terms":[],"facts":[]}')
        selection, rejections = client.call_memory_api([], snapshots[0].sentences)
        self.assertEqual((selection, rejections), ({'terms': [], 'facts': []}, []))
        self.assertEqual(client.call_api.call_count, 1)

    def test_a_worse_repair_does_not_replace_the_salvaged_first_answer(self):
        _, snapshots, _, _ = self.fixture()
        sources = snapshots[0].sentences
        client = object.__new__(LLMClient)
        client.call_api = Mock(side_effect=[
            '{"terms":[{"source_id":"P1:S1","text":"APTES","kind":"abbreviation"},'
            '{"source_id":"P1:S1","text":"Invented","kind":"term"}],"facts":[]}',
            'not json at all'])
        selection, rejections = client.call_memory_api([], sources)
        self.assertEqual(client.call_api.call_count, 2)
        self.assertEqual([term['text'] for term in selection['terms']], ['APTES'])
        self.assertEqual(len(rejections), 1)

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
        client.call_memory_api.side_effect = lambda messages, sources: ({'terms': [], 'facts': [s.id for s in sources]}, [])
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

    def test_declared_terms_keep_source_locations_even_if_model_omits_them(self):
        root, snapshots, chapter, client = self.fixture()
        client.call_memory_api.return_value = ({'terms': [], 'facts': []}, [])
        memory = build_memory(chapter, snapshots, 'doc', client, 'model', root, user_terms=['APTES'])
        self.assertEqual([term['source']['paragraph_index'] for term in memory.to_dict()['terms']], [1, 2])
        self.assertTrue(all(term['protection'] == 'user' for term in memory.to_dict()['terms']))

    def test_concurrent_memory_writers_publish_valid_complete_cache(self):
        root, snapshots, chapter, client = self.fixture()
        barrier = threading.Barrier(2, timeout=10)
        selection = client.call_memory_api.return_value
        def select(messages, sources):
            barrier.wait()
            return selection
        client.call_memory_api.side_effect = select
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(build_memory, chapter, snapshots, 'doc', client, 'model', root) for _ in range(2)]
            memories = [future.result() for future in futures]
        self.assertEqual(memories[0].to_dict(), memories[1].to_dict())
        self.assertEqual(len(list(root.glob('memory-*.json'))), 1)
        self.assertFalse(list(root.glob('*.tmp')))
        published = json.loads(next(root.glob('memory-*.json')).read_text(encoding='utf-8'))
        self.assertEqual(set(published), {'selections', 'rejections'})
        self.assertIsInstance(published['selections'], list)

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
