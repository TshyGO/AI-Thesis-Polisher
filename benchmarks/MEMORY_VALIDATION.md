# Structured chapter memory — 2026-09-06

## Scope and guarantees

The production pipeline now builds extractive, source-addressed chapter memory.
The model can select literal terms and source sentence IDs, but cannot write a
fact paraphrase, expansion or summary. Code supplies the full original quotation,
paragraph index and character offsets. Term protection is derived from explicit
user terms or existing lexical guards, never a model-generated lock flag.
The advanced prompt panel accepts optional user-protected terms, one per line.
Their source occurrences are located deterministically even if the selector omits
them; these user-policy mentions do not imply that the model found every term.

These checks establish **textual provenance**, not scientific truth or complete
semantic understanding. Term kinds and source selection remain model judgements.
A valid quotation can still be irrelevant or misinterpreted by an editor. The
existing edit validators and exact-proposal review remain active independently
of what memory selected. Empty selections are not permission to change facts.

## Boundaries and budgets

- Source IDs are local to the original input snapshot. Quote offsets refer to
  Python character positions in that original paragraph, not the modified Word.
- Chapter ID uses source paragraph bounds, including when headings have identical
  names. Targets are checked against the memory's exact source sentences.
  Boundaries follow the existing Word OutlineLevel 1/2 parser; manually styled
  headings without outline metadata may remain in one scope. This is not yet a
  complete semantic chapter/section tree.
- Fact references in prompts point only to supplied current target sentences;
  their text is already in the editor/reviewer payload. Other paragraphs' facts
  are not added as factual instructions. Terms are retrieved only when they occur
  literally in target text (with Latin-token boundaries).
- Neighbor paragraphs are clamped to the same chapter. Table/pre-existing-revision
  snapshots are excluded from memory sources. Mathematical/format semantics are
  not reconstructed from plain source text in this slice.
- Selector input: up to 12,000 serialized source characters or 80 sentences per
  chunk, at most 8 chunks per chapter. Oversized sentences and later chunks are
  explicitly listed as omitted; original target editing is not silently skipped.
- Retrieved memory budget: 2,400 JSON characters, without clipping quotations.
  A truncated selection is marked. `coverage=complete` means eligible sources
  were submitted to selection, **not that every fact was identified**; source
  counts and coverage basis are recorded.
- Cache identity includes document hash, chapter bounds/title/text, understanding
  model/settings, memory schema/budgets, user terms and stage-0 preferences.
  Cached selections are revalidated against current sources. Unique temporary
  files and bounded Windows sharing retries protect memory-cache publication;
  this does not make all legacy workflow caches safe for multi-process use.
- Invalid memory receives one format/source correction request. If still invalid,
  the chapter reports an error; it does not fall back to free-form notes or KEEP.
  A failed chapter is not repeatedly sent to the selector for every paragraph.

`ChapterMemory.json` contains local quotes, references, user policy and coverage.
It can contain private document text in actual use: keep it local, like the input
and Word output. This development used only fixed synthetic text and no private
paper was transmitted or committed. Existing model/API configuration was not changed.

## Live representation comparison

Two independent synthetic chapters define APTES differently, with separate 80 ℃
and 120 ℃ conditions. Each has one simple agreement-error target. Both modes use
the same current editor policy and target/neighbor text; only memory construction
and representation differ. This is not an old-release A/B or a representative
academic-editing benchmark. Mode order alternates between chapters.

Understanding: Qwen/Qwen3-30B-A3B-Instruct-2507; editor:
deepseek-ai/DeepSeek-V4-Flash; official SiliconFlow endpoint. Eight API attempts,
all completed. Source-validation and cache-reuse checks also passed.

| Chapter | Memory | Memory JSON characters | Editor input tokens | Expected correction |
| --- | --- | --- | --- | --- |
| A | Free text | 1359 | 1104 | matched |
| A | Structured | 321 | 489 | matched |
| B | Structured | 321 | 489 | matched |
| B | Free text | 1297 | 1082 | matched |

In this tiny fixture, memory context characters fell 75.8% in aggregate and editor
input tokens fell 55.3%. All four edits matched their references and no extra
numeric values were introduced. Structured selector input was slightly larger
because it carries source IDs and offsets; downstream savings are not a claim of
universal cost reduction. Service latency and two grammar cases cannot establish
thesis-wide quality or speed improvement.

Raw observations: [memory-v1-smoke.json](results/memory-v1-smoke.json). Later test
runner changes add per-row event association and a canonical synthetic document
hash; neither changes the tested prompts or edits. The recorded smoke was run
without Python optimization; its assertions are now explicit runtime checks.

## Engineering and Word verification

- 67 offline/UI tests pass, including invented/missing citations, term-prefix
  confusion, immutable memory, source/model cache invalidation, corrupt-cache
  rejection, cross-chapter targets/neighbors, bounded omissions, error memoization
  and simultaneous memory-cache writers.
- Real Word suite: 9 tests, including actual outline-heading scopes and refusal
  to use one Word chapter's memory for another chapter's target.
- A full structured-memory Word run used 3 API attempts, wrote 2 sentences,
  preserved saved revisions, and verified the original source hash was unchanged.
  Replaying into a fresh output used 0 additional API attempts.

```powershell
python -m unittest test_pipeline_regressions test_p0 test_p1 test_stage_models test_review_compare test_stage_ui test_chapter_memory
python -m unittest test_p1_word
```

Paid synthetic checks read the existing key from stdin, never from a command-line
argument: `python -m benchmarks.memory_compare` (at most 14 attempts), or the
existing `benchmarks.stage_word_live` helper (at most 8 attempts). The UI defaults
to structured memory; `memory_mode=legacy` is an explicit developer comparison
option, not an automatic fallback. Batch triage, a complete hierarchical document
tree, semantic-fact verification and UI redesign remain outside this change.
