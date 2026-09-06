"""Lossless sentence spans. Offsets are Python indices; Word uses UTF-16 units."""
import re
from dataclasses import dataclass
from typing import Tuple


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class Sentence:
    id: str
    paragraph_index: int
    start: int
    end: int
    text: str

    def word_bounds(self, paragraph_text: str, word_start: int):
        if paragraph_text[self.start:self.end] != self.text:
            raise ValueError("Sentence no longer matches its source")
        return (word_start + utf16_length(paragraph_text[:self.start]),
                word_start + utf16_length(paragraph_text[:self.end]))


@dataclass(frozen=True)
class ParagraphSnapshot:
    index: int
    text: str
    word_start: int = 0
    blocked_reason: str = ""

    @property
    def sentences(self) -> Tuple[Sentence, ...]:
        return tuple(SentenceSegmenter().segment(self.text, self.index))


class SentenceSegmenter:
    # Abbreviations that normally introduce a following token. Terminal ambiguity
    # is deliberately handled conservatively by keeping the larger span.
    ABBREVIATIONS = re.compile(
        r"(?:\b(?:Fig|Figs|Eq|Eqs|Dr|Prof|Mr|Mrs|Ms|No|Nos|Vol|vs|cf|al)|\be\.g|\bi\.e)\.$",
        re.IGNORECASE,
    )
    CLOSERS = '\"\'”’）)]』」'

    def segment(self, text: str, paragraph_index: int = 1):
        spans = []
        start = 0
        i = 0
        while i < len(text):
            character = text[i]
            boundary = character in "。！？!?"
            if character == ".":
                prefix = text[start:i + 1]
                decimal = i > 0 and i + 1 < len(text) and text[i-1].isdigit() and text[i+1].isdigit()
                initial = bool(re.search(r"(?:\b[A-Z]|(?:\b[A-Za-z]\.)+[A-Za-z])\.$", prefix))
                boundary = not (decimal or initial or self.ABBREVIATIONS.search(prefix))
            end = i + 1
            if boundary:
                while end < len(text) and text[end] in self.CLOSERS + ".!?。！？":
                    end += 1
                # English punctuation in URLs, filenames and identifiers is not a boundary.
                if character in ".!?" and end < len(text) and not text[end].isspace():
                    boundary = False
            if boundary:
                self._append(spans, text, start, end, paragraph_index)
                start = end
                i = end
            else:
                i += 1
        self._append(spans, text, start, len(text), paragraph_index)
        return spans

    @staticmethod
    def _append(spans, text, start, end, index):
        # Whitespace remains in the source gaps, never normalised away.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if start < end:
            spans.append(Sentence(f"P{index}:S{len(spans)+1}", index, start, end, text[start:end]))
