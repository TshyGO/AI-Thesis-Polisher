"""Strict full-sentence contract. A missing result is not a KEEP decision."""
import json
import math
from dataclasses import dataclass
from typing import Optional
from engine.llm_client import ModelFormatError


SENTENCE_CONTRACT = '''Return ONLY a JSON array with exactly one result for every supplied sentence_id.
KEEP: {"sentence_id":"P1:S1","decision":"keep","reason":"already correct"}
EDIT: {"sentence_id":"P1:S1","decision":"edit","revised_sentence":"Complete revised sentence.","reason":"fix grammar","category":"grammar","confidence":0.9}
Preserve the exact IDs. Do not include old/new fragments, explanations outside JSON,
markdown or thinking tags. revised_sentence is the complete replacement, including
punctuation; an empty string explicitly deletes the sentence. confidence is a finite
number from 0 to 1. category is grammar, wordiness, clarity or style.
Do not insert line breaks, paragraph markers or other document structure.
For KEEP omit revised_sentence, category and confidence. An empty array is NOT KEEP.'''


@dataclass(frozen=True)
class SentenceDecision:
    sentence_id: str
    decision: str
    reason: str
    revised_sentence: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = None


def parse_decisions(content: str, expected_ids):
    text = content.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0] not in ('```', '```json') or lines[-1] != '```':
            raise ModelFormatError("Incomplete JSON fence")
        text = '\n'.join(lines[1:-1])
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise ModelFormatError("Invalid sentence decision JSON") from None
    expected = set(expected_ids)
    if not isinstance(payload, list):
        raise ModelFormatError("Sentence decisions must be an array")
    result = []
    seen = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ModelFormatError("Sentence decision must be an object")
        sid, decision, reason = (item.get(key) for key in ('sentence_id', 'decision', 'reason'))
        if not isinstance(sid, str) or sid not in expected or sid in seen:
            raise ModelFormatError("Unknown or duplicate sentence id")
        if decision not in ('keep', 'edit') or not isinstance(reason, str) or not reason.strip():
            raise ModelFormatError("Invalid sentence decision/reason")
        allowed = {'sentence_id', 'decision', 'reason'}
        if decision == 'edit':
            allowed |= {'revised_sentence', 'category', 'confidence'}
            revised, category, confidence = (item.get(key) for key in ('revised_sentence', 'category', 'confidence'))
            if (not isinstance(revised, str) or category not in ('grammar', 'wordiness', 'clarity', 'style')
                    or isinstance(confidence, bool) or not isinstance(confidence, (float, int))
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ModelFormatError("Invalid revision fields")
            if any(ord(c) < 32 and c != '\t' for c in revised):
                raise ModelFormatError("Revision introduces structural characters")
        else:
            revised = category = confidence = None
        if set(item) - allowed:
            raise ModelFormatError("Unexpected decision fields")
        result.append(SentenceDecision(sid, decision, reason, revised, category, confidence))
        seen.add(sid)
    if seen != expected:
        raise ModelFormatError("Missing sentence decisions")
    return result


def validate_review(proposed, reviewed):
    by_id = {item.sentence_id: item for item in proposed if item.decision == 'edit'}
    if {item.sentence_id for item in reviewed} != set(by_id) or len(reviewed) != len(by_id):
        raise ModelFormatError("Reviewer must cover exactly the nominated sentences")
    for item in reviewed:
        if item.decision == 'edit' and item.revised_sentence != by_id[item.sentence_id].revised_sentence:
            raise ModelFormatError("Reviewer introduced a different revision")
    return reviewed
