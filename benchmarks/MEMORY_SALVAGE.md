# Chapter-memory failure radius — 2026-09-06

## What changed

An unverifiable memory selection used to fail the whole chapter. One term that
does not occur literally in its cited source meant every paragraph of that
chapter was reported `MEMORY_VALIDATION_ERROR` and never reached the editor.

Now each selection is judged on its own. Items that cannot be grounded are
dropped and reported; the chapter proceeds with what is left. The chapter still
fails when the model proposed selections and **not one** of them survived,
because that is indistinguishable from a selector that ignored the sources.

The provenance guarantee is unchanged: a dropped item never enters memory, never
reaches the editor or reviewer prompt, and is never repaired into something
valid. Cached selections are still validated strictly — leniency applies only to
a fresh model answer. The single repair request is still the whole budget.

## Why

Five baseline runs of the same fixture ([COST_BASELINE.md](COST_BASELINE.md))
lost an entire chapter in two of them: six paragraphs, three seeded defects, no
edit attempted. The loss was correctly reported and never disguised as "no
changes needed", but the blast radius was disproportionate to one ungrounded
term among otherwise valid selections.

## What the live runs do and do not show

Five runs on the new code with the same understanding model completed all ten
chapter builds with zero failures — and also **zero rejections and zero repair
requests**. The selector simply produced fully grounded selections on the first
attempt every time, so **these runs did not exercise the salvage path at all**
and are not evidence that it works in production. Compared with the earlier
baseline (3 of 5 runs needed a repair, 2 lost a chapter), the failure mode did
not recur; that shift is unexplained and is not attributable to this change,
which cannot make a first answer better. Treat it as sampling or provider-side
variation, not as a fix that was demonstrated live.

The salvage behaviour itself is covered by unit tests: a grounded item survives
alongside a rejected one, an all-invalid payload still raises, rejections survive
a cache hit, a rejected term never appears in the editor context, the repair is
requested exactly once, and a broken repair never discards an already grounded
first answer.

| Run | Understanding | Attempts | Chapters failed | Rejected items | Repairs | Seeded missed |
| --- | --- | --- | --- | --- | --- | --- |
| salvage-qwen v1–v5 | Qwen3-30B | 20–22 | 0 (10 builds) | 0 | 0 | 0 |
| baseline v1–v5 (old code) | Qwen3-30B | 12–21 | 2 (10 builds) | n/a | 3 | 6 |

## Live evidence, from a longer chapter

The short fixture never exercised the salvage path. An 18-paragraph single
chapter does. Across five runs on it, three dropped ungrounded selections — six
items in total — and **no chapter was lost**. That is the live confirmation the
runs above could not give.

The same corpus also exposed a second failure with the same disproportionate
blast radius. One run lost all eighteen paragraphs to
`Invalid memory arrays or output budget`: the selector returned more than the
32 permitted terms for a 43-sentence chapter. That is a long chapter, not a
selector ignoring its sources, so overflow is now trimmed to the budget and
recorded as a rejection with the dropped count, exactly like an ungrounded item.
The strict path used for cached selections still refuses an over-budget array,
because we never write one.

The overflow is more likely the longer a chapter is — that is, on real theses
rather than on fixtures. It did not recur in the five runs after the fix, so
that fix is unit-tested and not yet demonstrated live either.

## Understanding-model comparison

Five runs routed the understanding stage to `deepseek-ai/DeepSeek-V4-Flash`
instead (the first two on the old code, which does not affect what the selector
emits). No chapter failed, but the cost is very different:

| Understanding model | Understanding completion tokens | Total seconds |
| --- | --- | --- |
| Qwen/Qwen3-30B-A3B-Instruct-2507 | 324–495 | 72–98 |
| deepseek-ai/DeepSeek-V4-Flash | 6,199–12,035 | 119–180 |

The selector emits source IDs and literal substrings, so a 12,000-token selection
is not more information — it is the same job done far more expensively, for the
same zero failures on this fixture. **The default understanding model is not
changed.** Ten chapter builds per model cannot rank model reliability anyway.

Raw observations: [memory-salvage-2026-09-06.json](results/memory-salvage-2026-09-06.json),
with each run labelled by the engine code it ran against.

## Boundaries

- Ten chapter builds per configuration is a smoke sample, not a failure-rate
  measurement. The 2-in-10 baseline failures and the 0-in-10 later runs are both
  compatible with a wide range of true rates.
- The corpus is synthetic and agent-labelled. Nothing here measures editing
  quality or human acceptance.
- `ChapterMemory.json` now records dropped selections, including the model's
  rejected text. It stays local, like every other run artifact.
- Memory cache files use a new shape and version, so previously cached chapter
  selections are simply not reused; they are re-requested once.
- Budget overflow keeps the first 32 terms and first 64 facts. That choice is
  deterministic, not a relevance ranking.

## Reproduce

```powershell
python -m unittest test_chapter_memory test_p1 test_stage_models test_cost_profile
```

Paid checks read the credential from stdin and stop at the request cap:

```powershell
$c = Import-Clixml <your DPAPI credential path>
$c.GetNetworkCredential().Password | python -m benchmarks.cost_profile --output cache/salvage --label salvage --max-requests 40
$c.GetNetworkCredential().Password | python -m benchmarks.cost_profile --output cache/ds --label ds --understanding-model deepseek-ai/DeepSeek-V4-Flash --max-requests 40
```
