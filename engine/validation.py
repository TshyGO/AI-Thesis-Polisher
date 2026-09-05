"""Conservative P0 lexical guards, not a semantic fact checker."""
import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedSpan:
    kind: str
    text: str
    start: int
    end: int


class ProtectedSpanExtractor:
    PATTERNS = {
        "number": r"[−+-]?\d+(?:[.,]\d+)*(?:[eE][−+-]?\d+)?",
        "citation": r"\[[\d\s,，;；–—-]+\]",
        "abbreviation_formula": r"(?<![A-Za-z])(?:[A-Z]{2,}[A-Za-z0-9₀-₉]*|(?:[A-Z][a-z]?[0-9₀-₉]*){2,})(?![A-Za-z])",
        "single_element_formula": r"(?<![A-Za-z])(?:[A-Z][a-z]?(?:[0-9₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹]+[+−⁺⁻-]?|[+−⁺⁻-]))(?![A-Za-z])",
        "unit": r"(?<![A-Za-z])(?:℃|°C|%|wt%|mol|mm|cm|nm|μm|µm|mg|kg|mL|kPa|MPa|GPa|Hz|kHz|K|W|h|min|s|m|g|L)(?![A-Za-z])",
        "compound_unit": r"(?<![A-Za-z])(?:W|mol|g|kg|m|cm|mm|s|K|L)(?:[· /]*[A-Za-zμµ]+)?(?:[−-]\d+|[⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+|\^[-−]?\d+)",
        "reference": r"(?:Fig\.|Eq\.|Table|图|表|式)\s*\d+(?:[.-]\d+)*",
    }

    def __init__(self, terms=()):
        self.terms = tuple(terms)

    def extract(self, text):
        spans = [ProtectedSpan(kind, match.group(), *match.span())
                 for kind, pattern in self.PATTERNS.items()
                 for match in re.finditer(pattern, text)]
        for term in self.terms:
            if term:
                spans.extend(ProtectedSpan("term", m.group(), *m.span())
                             for m in re.finditer(re.escape(term), text))
        return sorted(spans, key=lambda span: (span.start, span.end, span.kind))


class Validator:
    def __init__(self, terms=()):
        self.extractor = ProtectedSpanExtractor(terms)

    def validate(self, original, revised):
        before = Counter((s.kind, s.text) for s in self.extractor.extract(original))
        after = Counter((s.kind, s.text) for s in self.extractor.extract(revised))
        if before != after:
            return "protected spans changed (number/unit/citation/term)"
        # Preserve order as well as multiplicity: swapping two values is unsafe.
        if [(s.kind, s.text) for s in self.extractor.extract(original)] != [
            (s.kind, s.text) for s in self.extractor.extract(revised)
        ]:
            return "protected spans reordered"
        return ""

    def validate_fragment(self, sentence, old, new):
        if not old or sentence.count(old) != 1:
            return "source fragment missing or ambiguous"
        if old == new:
            return "no effective change"
        return self.validate(sentence, sentence.replace(old, new, 1))
