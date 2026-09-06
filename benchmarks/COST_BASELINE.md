# Cost baseline of the current P1 pipeline — 2026-09-06

## Why this exists

The batch-triage work must be accepted on measured request/token/latency cost, not
on the intuition that "fewer editor calls" is cheaper. This is the fixed reference
that later stages are compared against. Nothing in the production pipeline was
changed to produce it.

## What this is not

Not a quality benchmark, not human acceptance, and not a Word patch test. The
corpus is **synthetic and agent-labelled**; the seeded/clean labels say what the
author of the fixture intended, not what a human editor would accept. The runner
uses an in-memory text document ([textdoc.py](textdoc.py)) that reuses the real
sentence patch planner and validators but has no COM range, formatting guard,
undo record or revision mark. Token counts are provider usage observations, not
currency costs. Latency includes service variability.

## Corpus

[chapter_corpus.jsonl](chapter_corpus.jsonl), SHA-256
`4439cbdee80a9579a726e149729b38dc467eefb21e0190590333ec15beb8ff5d`: 12 contiguous
paragraphs, 32 sentences, 2,509 characters, in two chapters of six paragraphs.
Six paragraphs carry seeded defects (subject-verb agreement, tense drift,
verbosity); six are clean. Every paragraph also lists strings that must survive
untouched (numbers, units, citations, abbreviations, modality, negation).

Contiguity is the point: isolated one-sentence samples like `synthetic.jsonl`
cannot show neighbour-window or chapter-memory cost, because each sample is its
own chapter with empty neighbours.

## Configuration

Understanding `Qwen/Qwen3-30B-A3B-Instruct-2507`, editor and same-model reviewer
`deepseek-ai/DeepSeek-V4-Flash`, official SiliconFlow endpoint, structured memory,
English mode, standard intensity, one HTTP attempt per call, cold cache per run.
Five runs, no configuration change between them.

## Result

| Run | Attempts | Editor | Reviewer | Underst. | Repairs | Prompt tok | Compl. tok | Stage s | Text factor | Chapters failed | Seeded missed | Clean edited |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | 21 | 12 | 6 | 3 | 0 | 15,253 | 11,325 | 99.2 | 3.305 | 0 | 0 | 0 |
| v2 | 12 | 6 | 3 | 3 | 1 | 9,613 | 5,494 | 76.3 | 1.486 | **1** | 3 | 0 |
| v3 | 20 | 12 | 6 | 2 | 0 | 13,984 | 9,661 | 82.5 | 3.305 | 0 | 0 | 0 |
| v4 | 12 | 6 | 3 | 3 | 1 | 10,051 | 5,451 | 93.1 | 1.486 | **1** | 3 | 0 |
| v5 | 20 | 12 | 6 | 2 | 0 | 13,985 | 9,567 | 120.5 | 3.305 | 0 | 0 | 0 |

Raw per-attempt records: [cost-baseline-2026-09-06.json](results/cost-baseline-2026-09-06.json).
"Repairs" counts the one allowed explicit format/source correction request, whose
transport status is `OK` even though the previous answer was rejected. The v1
per-paragraph outcomes were derived after that run from its own `Report.xlsx`;
the harness emits them directly from v2 onwards.

### Chapter-memory failure blocks a whole chapter (2 of 5 runs)

In v2 and v4 the understanding model selected a term that does not occur literally
in the cited source. The memory validator rejected it, the single repair request
did not fix it, and by design the chapter failed closed: all six paragraphs of
`2. Materials and Methods` were reported as `MEMORY_VALIDATION_ERROR` and **never
reached the editor**. Three seeded defects went unedited as a direct consequence.

This is correct fail-closed behaviour, not a silent "no changes needed", and the
Excel report names it. But on this fixture it costs an entire chapter in 2 of 5
runs (2 of 10 chapter builds). Any later cost comparison must exclude or flag
runs with a failed chapter — the lower attempt counts in v2/v4 are lost work, not
savings.

### Complete runs are stable

The three complete runs are nearly identical: 12 editor calls, 6 reviewer calls,
13,984–15,253 prompt tokens, 9,567–11,325 completion tokens, text factor 3.305,
and exact label agreement — every seeded paragraph edited, no clean paragraph
touched. Wall time varied 82.5–120.5 s at the same request count, so latency
comparisons need repeats.

### Where the tokens go (v1, a complete run)

| Stage | Attempts | Prompt tok | Completion tok | Share of total |
| --- | --- | --- | --- | --- |
| Editor | 12 | 8,265 | 7,705 | 60.1% |
| Reviewer | 6 | 3,509 | 3,133 | 25.0% |
| Understanding | 3 | 3,479 | 487 | 14.9% |

Editor completions alone are 29% of all tokens. The editor returns a decision for
every sentence it is shown, including a KEEP for each of the 32 sentences, so the
six clean paragraphs still pay full output tokens.

### One editor call per paragraph, source text sent 3.3 times

Editor attempts equalled eligible paragraphs exactly (12 of 12) in every complete
run. Sentence-level filtering inside a paragraph therefore cannot remove a call;
only skipping a whole paragraph can.

The editor received 8,291 characters of source text for a 2,509-character corpus:
a factor of **3.305**. Each paragraph is sent once as the target and again inside
the `index-2, index-1, index+1` neighbour window of other paragraphs. Chapter
memory added a further 4,917 characters across the 12 calls, re-sent per paragraph.

## What this implies for batch triage

- Triage's ceiling on this fixture is the 50% zero-edit paragraph share, and it
  must pay a screening pass over 100% of paragraphs to find them. Screening that
  batches many paragraphs per call is the only shape that can win on call count.
- The clearer prize is editor **completion** tokens on zero-edit paragraphs, not
  the calls themselves.
- Neither triage nor packing is worth measuring until the chapter-memory failure
  is understood, because it moves 6 of 12 paragraphs and half the token budget.
- 50% zero-edit is a property of this deliberately half-clean fixture. The one
  real-thesis report available (77 paragraphs, older P0 pipeline) had 21%
  zero-edit paragraphs. Neither number is a general rate.

## Reproduce

```powershell
python -m unittest test_cost_profile
```

The paid run reads the credential from stdin, never from a command-line argument
or a file the runner writes. The endpoint is pinned; only the checked-in synthetic
corpus is transmitted; the run stops at `--max-requests` attempts.

```powershell
$c = Import-Clixml <your DPAPI credential path>
$c.GetNetworkCredential().Password | python -m benchmarks.cost_profile --output cache/cost-baseline --label baseline --max-requests 40
```

Each run writes `cost-profile.json`, `Report.xlsx`, `Run.json` and
`ChapterMemory.json` into its own directory under the output path, with an
isolated cold cache so a warm cache cannot report zero requests.
