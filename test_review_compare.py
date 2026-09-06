import unittest
from engine.stage_models import StageClient
from engine.revision_contract import SentenceDecision
from benchmarks.review_compare import load_cases, prepare, score, run


class RejectAllClient(StageClient):
    def __init__(self, config):
        self.stage_config = config
        self.model = config.model
        self.base_url = config.base_url
        self.telemetry = []

    def call_sentence_api(self, messages, expected_ids):
        text = str(messages)
        assert 'expected_accept' not in text and 'technical_risk' not in text and 'good01' not in text
        self.telemetry.append({'prompt_tokens': 10, 'completion_tokens': 3})
        return [SentenceDecision(sid, 'keep', 'test rejection') for sid in expected_ids]


class ComparisonTests(unittest.TestCase):
    def test_guarded_candidates_are_removed_equally_before_review(self):
        cases, _ = load_cases()
        _, candidates, labels, guarded = prepare(cases)
        self.assertEqual(len(guarded), 3)
        self.assertEqual(len(candidates), 9)
        baseline = score(candidates, labels)
        self.assertEqual(baseline['accepted_good'], 5)
        self.assertEqual(baseline['retained_bad'], 4)

    def test_reject_everything_is_not_perfect_quality(self):
        report = run('test-key', factory=RejectAllClient, trials=1)
        off, same, independent = report['rows']
        self.assertEqual(off['requests'], 0)
        for row in (same, independent):
            self.assertEqual(row['requests'], 1)
            self.assertEqual(row['rejected_good'], 5)
            self.assertEqual(row['good_retention_rate'], 0)
            self.assertIsNone(row['edit_precision'])

    def test_missing_outputs_are_not_scored_as_rejection(self):
        cases, _ = load_cases()
        _, _, labels, _ = prepare(cases)
        with self.assertRaises(ValueError):
            score([], labels)


if __name__ == '__main__':
    unittest.main()
