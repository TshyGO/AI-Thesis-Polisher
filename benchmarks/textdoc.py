"""In-memory P1 document adapter for cost measurement.

Deliberately NOT a Word acceptance test. It reuses the production sentence
patch planner, so validator rejections are real, but it has no COM range,
formatting guard, undo record or revision mark. Patch safety in Word must
still be measured by the real Word suites; this adapter measures the shape
and cost of the model traffic only.
"""
import json
from pathlib import Path

from engine.sentences import ParagraphSnapshot
from engine.patches import sentence_patch_plan, PatchResult


def load_corpus(path):
    """Return (paragraph texts, chapter ranges, rows) from a contiguous corpus file."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        raise ValueError('Empty corpus')
    if [row['order'] for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Corpus paragraphs must be contiguous and 1-indexed')
    chapters = []
    for index, row in enumerate(rows, 1):
        if chapters and chapters[-1]['name'] == row['chapter']:
            chapters[-1]['end'] = index
        else:
            if any(chapter['name'] == row['chapter'] for chapter in chapters):
                raise ValueError('Chapter paragraphs must be contiguous')
            chapters.append({'name': row['chapter'], 'start': index, 'end': index})
    return [row['text'] for row in rows], chapters, rows


class MemoryDocument:
    """Text-only stand-in for DocumentProcessor's P1 surface."""

    def __init__(self, paragraphs, chapters):
        self.original = list(paragraphs)
        self.current = list(paragraphs)
        self.chapters = [dict(chapter) for chapter in chapters]
        self.tracking = False
        self.saved = False
        self.applied = []

    def open_document(self, path, read_only=True, track_revisions=False):
        self.tracking = bool(track_revisions)

    def get_total_paragraphs(self):
        return len(self.current)

    def parse_chapters(self):
        return [dict(chapter) for chapter in self.chapters]

    def snapshot_paragraph(self, index):
        return ParagraphSnapshot(index, self.current[index - 1], 0, '')

    def apply_sentence_revisions(self, snapshot, decisions, protected_terms=()):
        if not self.tracking:
            return PatchResult(False, 'TRACKING_DISABLED')
        if self.current[snapshot.index - 1] != snapshot.text:
            return PatchResult(False, 'STALE_SOURCE')
        try:
            patches, expected = sentence_patch_plan(snapshot, decisions, protected_terms)
        except ValueError as rejection:
            return PatchResult(False, 'VALIDATION_REJECTED: ' + str(rejection))
        if not patches:
            return PatchResult(False, 'NO_PATCHES')
        self.current[snapshot.index - 1] = expected
        self.applied.append((snapshot.index, len(patches)))
        return PatchResult(True, patch_count=len(patches))

    def save(self):
        self.saved = True
