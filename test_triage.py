"""Shadow triage: screening verdicts are recorded, never acted on."""
import json
import unittest
from unittest.mock import Mock

from engine.llm_client import LLMClient, ModelFormatError
from engine.revision_contract import parse_triage
from test_batching import BatchingTests, PARAGRAPHS


class TriageContractTests(unittest.TestCase):
    IDS = ['P1:S1', 'P1:S2']

    def test_accepts_a_complete_verdict_array(self):
        content = json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': True},
                              {'sentence_id': 'P1:S2', 'needs_edit': False}])
        self.assertEqual(parse_triage(content, self.IDS), {'P1:S1': True, 'P1:S2': False})

    def test_accepts_a_fenced_array(self):
        content = '```json\n[{"sentence_id":"P1:S1","needs_edit":true},{"sentence_id":"P1:S2","needs_edit":true}]\n```'
        self.assertEqual(parse_triage(content, self.IDS), {'P1:S1': True, 'P1:S2': True})

    def test_a_missing_verdict_is_an_error_not_an_implicit_skip(self):
        content = json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': True}])
        with self.assertRaises(ModelFormatError):
            parse_triage(content, self.IDS)

    def test_rejects_malformed_verdicts(self):
        invalid = [
            '[]',
            '{"P1:S1": true}',
            json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': 'yes'}, {'sentence_id': 'P1:S2', 'needs_edit': True}]),
            json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': True, 'reason': 'x'},
                        {'sentence_id': 'P1:S2', 'needs_edit': True}]),
            json.dumps([{'sentence_id': 'P9:S1', 'needs_edit': True}, {'sentence_id': 'P1:S2', 'needs_edit': True}]),
            json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': True}, {'sentence_id': 'P1:S1', 'needs_edit': True}]),
            'not json',
        ]
        for content in invalid:
            with self.subTest(content=content[:40]), self.assertRaises(ModelFormatError):
                parse_triage(content, self.IDS)

    def test_repair_is_bounded_to_one_extra_request(self):
        client = object.__new__(LLMClient)
        client.call_api = Mock(return_value='[]')
        with self.assertRaises(ModelFormatError):
            client.call_triage_api([], self.IDS)
        self.assertEqual(client.call_api.call_count, 2)

    def test_a_repaired_answer_is_accepted(self):
        client = object.__new__(LLMClient)
        client.call_api = Mock(side_effect=[
            'sorry, here you go',
            json.dumps([{'sentence_id': 'P1:S1', 'needs_edit': False},
                        {'sentence_id': 'P1:S2', 'needs_edit': True}])])
        self.assertEqual(client.call_triage_api([], self.IDS), {'P1:S1': False, 'P1:S2': True})
        self.assertEqual(client.call_api.call_count, 2)


class ShadowTriageTests(unittest.TestCase):
    # Reuse the batching fixture without re-running its assertions in this module.
    make_pipeline = BatchingTests.make_pipeline

    def make_shadow(self, batch_size=4, verdicts=None, error=None):
        source, pipeline, clients, document = self.make_pipeline(batch_size)
        pipeline.config['triage_mode'] = 'shadow'
        triage = Mock(model='fake-triage', base_url='fake')
        if error is not None:
            triage.call_triage_api.side_effect = error
        else:
            triage.call_triage_api.side_effect = lambda messages, ids: {
                sid: (verdicts.get(sid, True) if verdicts else True) for sid in ids}
        pipeline.stage_clients['triage'] = triage
        return source, pipeline, clients, document, triage

    def test_no_triage_call_when_the_mode_is_off(self):
        source, pipeline, clients, _ = self.make_pipeline(4)
        triage = Mock(model='fake-triage', base_url='fake')
        pipeline.stage_clients['triage'] = triage
        pipeline.process_document(str(source))
        triage.call_triage_api.assert_not_called()

    def test_shadow_screens_the_same_sentences_and_skips_nothing(self):
        source, pipeline, clients, document, triage = self.make_shadow(
            verdicts={'P%d:S2' % n: False for n in PARAGRAPHS})
        changes, _ = pipeline.process_document(str(source))
        self.assertEqual(triage.call_triage_api.call_count, 1)
        self.assertEqual(len(triage.call_triage_api.call_args.args[1]), 8)
        editor_ids = clients['editor'].call_sentence_api.call_args.args[1]
        self.assertEqual(len(editor_ids), 8)
        self.assertEqual(changes, 4)  # every "skip" verdict was still edited
        self.assertEqual(pipeline.run_summary['triage']['batches'], 1)
        self.assertFalse(pipeline.run_summary['triage']['failures'])
        self.assertEqual(pipeline.run_summary['triage']['verdicts']['P1:S2'], False)

    def test_a_failed_screening_is_logged_and_changes_nothing(self):
        source, pipeline, clients, document, triage = self.make_shadow(error=RuntimeError('triage down'))
        changes, _ = pipeline.process_document(str(source))
        self.assertEqual(changes, 4)
        log = pipeline.run_summary['triage']
        self.assertEqual(log['verdicts'], {})
        self.assertEqual([f['error'] for f in log['failures']], ['RuntimeError'])
        self.assertEqual(log['batches'], 1)

    def test_screening_covers_every_editor_call_including_unpacked_runs(self):
        source, pipeline, clients, document, triage = self.make_shadow(batch_size=1)
        pipeline.process_document(str(source))
        self.assertEqual(triage.call_triage_api.call_count, 4)
        self.assertEqual(len(pipeline.run_summary['triage']['verdicts']), 8)

    def test_triage_mode_is_part_of_the_cache_identity(self):
        source, pipeline, _, _ = self.make_pipeline(1)
        first = pipeline.get_cache_key(str(source), 1, PARAGRAPHS[1])
        pipeline.config['triage_mode'] = 'shadow'
        self.assertNotEqual(first, pipeline.get_cache_key(str(source), 1, PARAGRAPHS[1]))

    def test_an_unknown_triage_mode_is_refused(self):
        source, pipeline, clients, document = self.make_pipeline(1)
        config = dict(pipeline.config, triage_mode='enabled')
        with self.assertRaises(ValueError):
            type(pipeline)(clients['editor'], document, config, stage_clients=pipeline.stage_clients)


if __name__ == '__main__':
    unittest.main()
