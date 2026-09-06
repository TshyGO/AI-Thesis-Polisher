import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_p1 as fixtures
from engine.llm_client import LLMClient
from engine.llm_client import ModelFormatError
from engine.stage_models import resolve_configs, build_stage_clients, StageClient, persistable_overrides, normalized_endpoint, saved_http_opt_in


class StageModelTests(unittest.TestCase):
    def base(self):
        return {'base_url': 'https://example.invalid/v1/', 'model': 'editor', 'api_key': 'PRIVATE_KEY'}

    def test_other_endpoint_never_inherits_key(self):
        factory = Mock()
        with self.assertRaises(ValueError):
            build_stage_clients(self.base(), {'editor': {'base_url': 'https://other.invalid/v1'}}, factory=factory)
        factory.assert_not_called()

    def test_same_model_binds_reviewer_to_editor_and_off_ignores_profile(self):
        configs = resolve_configs(self.base(), {'editor': {'base_url': 'https://other.invalid/v1', 'api_key': 'OTHER_KEY'}})
        self.assertEqual(configs['reviewer'].api_key, 'OTHER_KEY')
        self.assertEqual(configs['reviewer'].base_url, configs['editor'].base_url)
        resolve_configs(self.base(), {'reviewer': {'base_url': 'invalid', 'timeout': -1}}, 'off')

    def test_independent_requires_distinct_model_or_endpoint(self):
        with self.assertRaises(ValueError):
            resolve_configs(self.base(), review_mode='independent')
        configs = resolve_configs(self.base(), {'reviewer': {'model': 'other-family'}}, 'independent')
        self.assertEqual(configs['reviewer'].model, 'other-family')
        self.assertEqual(configs['reviewer'].api_key, 'PRIVATE_KEY')

    def test_no_secret_in_metadata_repr_or_saved_overrides(self):
        configs = resolve_configs(self.base())
        self.assertNotIn('PRIVATE_KEY', json.dumps({s: c.public() for s, c in configs.items()}))
        self.assertNotIn('PRIVATE_KEY', repr(configs))
        self.assertEqual(persistable_overrides({'editor': {'model': 'x', 'api_key': 'PRIVATE_KEY'}}), {'editor': {'model': 'x'}})

    def test_parameters_and_explicit_repair_retry_limit(self):
        config = replace(resolve_configs(self.base())['editor'], temperature=0.7, timeout=123, max_retries=5)
        client = StageClient(config)
        with patch.object(LLMClient, 'call_api', return_value='ok') as call:
            client.call_api([])
            call.assert_called_with([], 0.7, 123, 5)
            client.call_api([], max_retries=1)
            call.assert_called_with([], 0.7, 123, 1)

    def test_cache_tracks_all_routes_but_notes_only_understanding(self):
        source, pipeline, _, _ = fixtures.SentencePipelineTests().make_pipeline()
        clients = build_stage_clients(self.base(), {'reviewer': {'model': 'other'}}, 'independent')
        pipeline.stage_clients = clients
        first = pipeline.get_cache_key(str(source), 1, 'text')
        notes = pipeline._get_chapter_notes_cache_path('chapter', 'text', 'english')
        clients['reviewer'] = StageClient(replace(clients['reviewer'].stage_config, model='changed'))
        self.assertNotEqual(first, pipeline.get_cache_key(str(source), 1, 'text'))
        self.assertEqual(notes, pipeline._get_chapter_notes_cache_path('chapter', 'text', 'english'))
        clients['understanding'] = StageClient(replace(clients['understanding'].stage_config, temperature=0.8))
        self.assertNotEqual(notes, pipeline._get_chapter_notes_cache_path('chapter', 'text', 'english'))

    def test_pipeline_uses_three_clients_and_off_makes_no_reviewer_call(self):
        source, pipeline, old_client, _ = fixtures.SentencePipelineTests().make_pipeline()
        clients = build_stage_clients(self.base(), {'reviewer': {'model': 'other'}}, 'independent')
        for client in clients.values():
            client.call_api = Mock(return_value='notes')
            client.call_memory_api = Mock(return_value=({'terms': [], 'facts': []}, []))
            client.call_sentence_api = Mock(side_effect=old_client.call_sentence_api.side_effect)
        pipeline.stage_clients = clients
        pipeline.process_document(str(source))
        clients['understanding'].call_memory_api.assert_called_once()
        self.assertEqual(clients['editor'].call_sentence_api.call_count, 2)
        clients['reviewer'].call_sentence_api.assert_called_once()
        self.assertNotIn('PRIVATE_KEY', (source.parent / 'Run.json').read_text(encoding='utf-8'))
        clients['reviewer'].call_sentence_api.reset_mock()
        pipeline.config['use_cross_review'] = False
        pipeline.process_document(str(source))
        clients['reviewer'].call_sentence_api.assert_not_called()

    def test_telemetry_records_attempts_tokens_not_text_or_key(self):
        client = StageClient(resolve_configs(self.base())['editor'])
        client.client = Mock()
        client.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='answer'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
        client.call_api([{'role': 'user', 'content': 'PRIVATE_TEXT'}])
        self.assertEqual(client.telemetry[0]['prompt_tokens'], 10)
        self.assertEqual(client.telemetry[0]['completion_tokens'], 2)
        self.assertNotIn('PRIVATE', json.dumps(client.telemetry))

    def test_invalid_endpoint_and_parameter_ranges(self):
        for url in ('https://user:pass@example.invalid/v1', 'https://example.invalid/v1?key=x', 'file:///x', 'http://remote.invalid/v1', 'https://example.invalid:invalid/v1'):
            with self.assertRaises(ValueError):
                resolve_configs(dict(self.base(), base_url=url))
        with self.assertRaises(ValueError):
            resolve_configs(self.base(), {'editor': {'temperature': 10**400}})
        self.assertEqual(normalized_endpoint('http://localhost:8000/v1/'), 'http://localhost:8000/v1')
        self.assertEqual(normalized_endpoint('http://[::1]:8000/v1'), 'http://[::1]:8000/v1')
        with self.assertRaises(ValueError):
            StageClient(replace(resolve_configs(self.base())['editor'], base_url='http://remote.invalid/v1'))

    def test_single_system_message_keeps_contract_and_review_policy(self):
        messages = [{'role': 'system', 'content': 'Preserve facts.'}, {'role': 'user', 'content': 'sample'}]
        request = LLMClient._with_contract(messages, 'Strict JSON.')
        self.assertEqual([m['role'] for m in request], ['system', 'user'])
        self.assertIn('Strict JSON.', request[0]['content'])
        self.assertIn('Preserve facts.', request[0]['content'])
        self.assertEqual(messages[0]['content'], 'Preserve facts.')

    def test_legacy_http_permission_does_not_spread_to_new_addresses(self):
        saved = dict(self.base(), base_url='http://old.invalid/v1')
        self.assertTrue(saved_http_opt_in(saved, saved['base_url']))
        self.assertFalse(saved_http_opt_in(saved, 'http://new.invalid/v1'))
        permitted = dict(saved, allow_insecure_http=True)
        resolve_configs(permitted)
        with self.assertRaises(ValueError):
            resolve_configs(permitted, {'editor': {'base_url': 'http://new.invalid/v1', 'api_key': 'NEW'}})
        resolve_configs(permitted, {'editor': {'base_url': 'http://new.invalid/v1', 'api_key': 'NEW', 'allow_insecure_http': True}})

    def test_review_integrity_uses_the_same_single_repair_budget(self):
        client = object.__new__(LLMClient)
        client.call_api = Mock(return_value='[{"sentence_id":"P1:S1","decision":"keep","reason":"test"}]')
        validator = Mock(side_effect=[ModelFormatError('Reviewer introduced a different revision'), None])
        client.call_sentence_api([], ['P1:S1'], validator=validator)
        self.assertEqual(client.call_api.call_count, 2)
        client.call_api.reset_mock()
        validator.side_effect = ModelFormatError('invalid review')
        with self.assertRaises(ModelFormatError):
            client.call_sentence_api([], ['P1:S1'], validator=validator)
        self.assertEqual(client.call_api.call_count, 2)


if __name__ == '__main__':
    unittest.main()
