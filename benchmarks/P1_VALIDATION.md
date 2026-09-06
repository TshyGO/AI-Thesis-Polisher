# P1 source/contract/writeback validation — 2026-09-06

## Scope

Three dependent slices: lossless sentence spans, full-sentence KEEP/EDIT protocol,
then production UI integration with position-addressed Word patches. The previous
P0 pipeline is retained for historical comparison, but the UI now invokes
`SentencePolishingPipeline`.

Source spans use Python code-point indices and explicit UTF-16 conversion for
Word ranges. Stable IDs are scoped to the immutable input and current run, not
guaranteed across arbitrary document edits. Whitespace remains in source gaps.
Abbreviations, decimals, mixed punctuation, initials and sentence-final labels
have regression cases; segmentation is conservative, not a complete NLP parser.

## Checks

- 36 offline tests pass: P0 regressions plus segmentation, immutable mapping,
  coverage/schema errors, review integrity, deterministic patch plans, replay,
  actual-result reporting and fatal-rollback no-save behavior.
- 7 real Word tests pass: exact UTF-16 ranges, tables/existing revisions,
  repeated sentences/multiple changes, saved Unicode text and bold formatting,
  stale snapshots and bookmarks, injected mutation failure, injected final-text
  verification failure. Undo is verified against content/format XML after
  excluding volatile editor IDs, spellcheck annotations and rendered page breaks.
- SiliconFlow `deepseek-ai/DeepSeek-V4-Flash` protocol smoke: 2 calls passed
  nomination and exact-proposal review on three synthetic sentences.
- Full production pipeline + real Word: 3 API calls, 2 sentences edited, saved
  revision records and bold formatting verified after reopening. Repeating from
  the untouched source produced the same edits with **0 additional API calls**;
  source SHA-256 was unchanged. The test uses only fixed synthetic text.

Initial protocol diagnostics failed closed when the reviewer returned unrelated
IDs from context. Explicit expected IDs and candidate-only reviewer input fixed
that behavior. Structural Unicode separators, tabs and oversized confidence
integers are rejected as format errors, with one bounded format repair request.

## Safety boundaries

- Diffs use deterministic SequenceMatcher spans, not a claim of mathematically
  optimal edit distance. Patches apply right-to-left against verified source text.
- A paragraph is one Word Undo transaction. Final text is projected by excluding
  tracked deletions without accepting revisions. Failure rolls back the entire
  group. Unverifiable rollback aborts the run and the document is not saved;
  previously reported in-memory edits are marked RUN_ABORTED.
- Existing revisions and table paragraphs are explicitly skipped. Paragraphs
  containing fields, equations, inline objects, bookmarks, comments, references,
  or content controls are conservatively refused at patch preflight. Mixed or
  protected formatting in an affected range is not flattened.
- No paragraph/control characters may be inserted, removed or changed by a patch.
  Complex-document coverage is deliberately limited; the tool never auto-accepts
  pre-existing user revisions to work around that boundary.
- Numeric/term protection is not semantic fact verification. These checks prove
  placement, persistence and recovery, not human acceptance or thesis-wide quality.

## Reproduce

```powershell
python -m unittest test_pipeline_regressions test_p0 test_p1
python -m unittest test_p1_word
```

The second command requires local Microsoft Word. GitHub-hosted Windows CI runs
the first command only. For explicitly authorised paid checks, pipe the existing
secret loader into `python -m benchmarks.contract_live` (6 calls maximum) or
`python -m benchmarks.p1_word_live` (8 calls maximum). Both pin the official
SiliconFlow endpoint and use only checked-in synthetic text. Never place a key on
the command line or in the repository. Local artifacts are under ignored cache.

`benchmarks.live` remains the legacy P0 text-adapter evaluator, not the P1 Word
acceptance path. No private paper or credential was committed or transmitted.
