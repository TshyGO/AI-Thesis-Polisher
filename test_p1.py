import unittest
import json
from unittest.mock import Mock
from dataclasses import FrozenInstanceError
from engine.sentences import SentenceSegmenter, ParagraphSnapshot, utf16_length
from engine.revision_contract import parse_decisions, validate_review
from engine.llm_client import LLMClient, ModelFormatError
from engine.patches import sentence_patch_plan
from engine.revision_contract import SentenceDecision


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
                        [self.edit(confidence=True)], [self.edit(confidence=float('nan'))],
                        [self.edit(old='copied')], [self.edit(revised_sentence='new\rparagraph')]):
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


if __name__ == '__main__':
    unittest.main()
