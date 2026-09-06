import unittest
from dataclasses import FrozenInstanceError
from engine.sentences import SentenceSegmenter, ParagraphSnapshot, utf16_length


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


if __name__ == '__main__':
    unittest.main()
