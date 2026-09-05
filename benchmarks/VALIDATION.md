# P0 validation — 2026-09-06

## Engineering checks

- 20 unique offline regression tests pass (`test_pipeline_regressions test_p0`).
- Two real Microsoft Word COM tests pass: saved tracked deletion/format guard;
  two fresh output runs with cache replay, full reports, unchanged source hash,
  and verification of final text after accepting changes in a disposable copy.
- Windows GitHub Actions runs offline tests on each PR. Word and paid API checks
  are local-only and are never simulated as CI Word acceptance.

## Real SiliconFlow check

Provider: `https://api.siliconflow.cn/v1`; model:
`deepseek-ai/DeepSeek-V4-Flash`; prompt version `p0-suggestions-v4`;
intensity `light`; same-model reviewer enabled. Each case has its own chapter.

Corpus: `public_derived.jsonl`, SHA-256
`4f6d39e5df6e8419f31e94f658bbdca04499a2f9df030f7c7e359f0a014b34ff`.
See SOURCES.md for CC BY 4.0 attribution and the construction/label policy.

| Measurement | Initial run | Recovery run |
| --- | --- | --- |
| New API calls | 51 | 2 |
| Duration | 557.28 s | 16.42 s |
| Completed cases | 19/20 | 20/20 |
| Timeout errors | 1 | 0 |
| Final edits | 10 | 10 |
| Exact reference restorations | 9/10 | 9/10 |
| Edits to completed KEEP cases | 0 | 0 |
| Lexical fact-damage flags in final text | 0 | 0 |
| Human acceptance | unavailable | unavailable |

Recovery replayed saved suggestions into a fresh text adapter and retried only
the missing case. A malformed `[]</think>[]` response in the first run was repaired
by an explicit additional request, not silently parsed as KEEP.

The non-matching edit (`fair07-error`) changed `could is imagined` to `can be
imagined`, rather than restoring `could be imagined`. This shifts modality and
demonstrates why zero lexical flags is NOT zero semantic damage. No reference
was changed to inflate the score. This result is not a human acceptance score,
representative thesis-quality claim, or controlled old/new pipeline A/B result.

Early synthetic diagnostics grouped unrelated cases as one chapter and were
discarded as comparative quality evidence. They still helped expose a numeric
hallucination, which the validator rejected. Subsequent cases were isolated.

Historical local report `mode` labels still say synthetic despite the public
corpus hash above; the runner now labels the selected corpus correctly. Reviewer
subset and additional chemical-formula guards were added after the live run;
the final pipeline was checked by offline replay of all twenty saved predictions.
No private paper, DPAPI file, key, raw credential, or local configuration is
included in these artifacts. Raw API diagnostics remain in ignored local cache.

## Remaining v2 scope

Broader domain/language coverage, naturally occurring errors, independent human
acceptance, semantic-fact validation, P1 sentence-range patching, and product UI
remain follow-up work in #1. This P0 is an engineering baseline, not a claim that
all quality goals of the v2 roadmap have been reached.
