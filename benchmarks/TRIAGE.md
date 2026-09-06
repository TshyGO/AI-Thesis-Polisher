# Batch triage — shadow measurement and why it is not enabled — 2026-09-06

## Outcome

Triage screening was built, run against the real API in shadow mode, and **did
not pass its gate**. It is available as `triage_mode=shadow` for further
measurement. Skip mode was deliberately not implemented: on this evidence it
would drop real corrections in exchange for a saving that is, at best, close to
zero.

## What shadow mode does

Before every editor call, the same sentences go to a screening model that returns
one boolean per sentence. **Nothing is skipped.** The editor still receives every
sentence; verdicts are recorded in `Run.json` and compared afterwards against
what the pipeline actually wrote. A screening failure or contract violation is
recorded as a failure and never as "no edit needed"; the run is unaffected.

Default is `off`. Shadow mode costs real requests, so it is a measurement tool,
not something to leave on.

## Result

Twelve-paragraph corpus, 32 sentences, packing at 6, same-model review. The
control is the packed configuration with triage off.

| Screening model | Runs | Edits written | Edits screened out | Miss rate | Sentences screened out | Triage cost |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen/Qwen3-30B-A3B-Instruct-2507 | 5 | 45 | 7 | **15.6%** | 24 of 32 | 3,990 tok, 2 calls, 15.5 s |
| deepseek-ai/DeepSeek-V4-Flash | 3 | 29 | 1 | **3.4%** | 20 of 32 | 5,279 tok, 2 calls, 10.9 s |

Control: 17,542 tokens, 7 attempts, editor completion 7,085 tokens.

Raw records: [triage-shadow-2026-09-06.json](results/triage-shadow-2026-09-06.json).

Every single Qwen run missed at least one real correction, and its verdicts were
not even self-consistent: on identical input it marked 8 sentences in three runs
and 21 in the other two. The stronger model missed less but still missed, and
cost more than the weaker one because it writes longer answers for the same
booleans.

## Why the saving cannot cover that

Screening has to read the same sentences the editor reads, so **it cannot save
input tokens** — it only avoids editor *output* for the sentences it drops. And
after [paragraph packing](PACKING.md) the editor makes 2 calls for this corpus,
so there is no call-count win left to take either; triage adds 2 calls of its own.

Editor completion in the control is 7,085 tokens. Screening out roughly 20 of 32
sentences might remove on the order of 4,000–4,500 of those, against a triage
cost of 3,990–5,279. That estimate is not measured — skip mode was not built —
but the two figures are the same size, and the arithmetic does not change with a
better prompt: the input pass is paid either way.

So the trade on offer is a 3.4%–15.6% chance of dropping each real correction in
exchange for roughly nothing. For a tool whose entire purpose is finding the
defects an author missed, that is not a trade worth making.

## Boundaries

- One screening prompt, two models, 32 synthetic agent-labelled sentences,
  8 runs. A different prompt could shift the miss rate; it cannot remove the
  input pass that makes the economics fail.
- The saving figure is an estimate. Measuring it exactly requires implementing
  skip mode and running it, which is only worth doing if the miss rate is first
  brought near zero.
- Miss rate here counts sentences the full pipeline actually wrote into Word.
  It says nothing about defects that neither path found.
- `P10:S1` was missed by both models in several runs — a verbose
  "Owing to the fact that…" opener. Wordiness reads as acceptable prose to a
  screener that is not being asked to rewrite it.

## If this is revisited

The gate to clear before skip mode is worth building: zero missed written edits
across at least 20 runs, and a measured net token saving after triage's own cost.
Deterministic pre-filters (paragraphs already cached, below `min_chars`, blocked
structures) already run and cost nothing; they are where free savings live.

## Reproduce

```powershell
python -m unittest test_triage test_cost_profile
```

```powershell
$c = Import-Clixml <your DPAPI credential path>
$c.GetNetworkCredential().Password | python -m benchmarks.cost_profile --output cache/triage --label shadow --batch-size 6 --triage shadow --triage-model Qwen/Qwen3-30B-A3B-Instruct-2507 --max-requests 40
```

The report's `triage_agreement` block carries the verdict counts, the miss count
and the exact sentence IDs that screening would have dropped.
