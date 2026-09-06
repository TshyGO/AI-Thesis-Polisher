"""Deterministic code-point diffs against immutable sentence source ranges."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from engine.validation import Validator


@dataclass(frozen=True)
class TextPatch:
    start: int
    end: int
    old: str
    new: str


def sentence_patch_plan(snapshot, decisions, protected_terms=()):
    sentences = {s.id: s for s in snapshot.sentences}
    seen = set()
    patches = []
    validator = Validator(protected_terms)
    for decision in decisions:
        if decision.sentence_id not in sentences or decision.sentence_id in seen or decision.decision != 'edit':
            raise ValueError('Invalid/duplicate edit target')
        seen.add(decision.sentence_id)
        sentence = sentences[decision.sentence_id]
        revised = decision.revised_sentence
        if not isinstance(revised, str) or revised == sentence.text:
            raise ValueError('Empty effect or invalid revised sentence')
        rejection = validator.validate(sentence.text, revised)
        if rejection:
            raise ValueError(rejection)
        protected = validator.extractor.extract(sentence.text)
        for operation, i, j, a, b in SequenceMatcher(None, sentence.text, revised, autojunk=False).get_opcodes():
            if operation == 'equal':
                continue
            old, new = sentence.text[i:j], revised[a:b]
            if any(ord(c) < 32 or c in '\x7f\x85\u2028\u2029' for c in old + new):
                raise ValueError('Patch touches document structure/control characters')
            if any((i < span.end and j > span.start) or (i == j and span.start < i < span.end) for span in protected):
                raise ValueError('Diff touches a protected span')
            patches.append(TextPatch(sentence.start + i, sentence.start + j, old, new))
    patches.sort(key=lambda patch: (patch.start, patch.end))
    for left, right in zip(patches, patches[1:]):
        if left.end > right.start:
            raise ValueError('Overlapping patches')
    expected = snapshot.text
    for patch in reversed(patches):
        if expected[patch.start:patch.end] != patch.old:
            raise ValueError('Stale source range')
        expected = expected[:patch.start] + patch.new + expected[patch.end:]
    return patches, expected


@dataclass(frozen=True)
class PatchResult:
    ok: bool
    reason: str = ''
    patch_count: int = 0


class PatchRollbackError(RuntimeError):
    """Fatal: caller must discard the open document without saving it."""
