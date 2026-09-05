# P0 offline baseline

`synthetic.jsonl` contains 16 **synthetic**, unreviewed smoke samples, not a real
paper benchmark and not evidence of improved editing quality. Real-paper samples
and annotations must be supplied/approved by the author; keep them in ignored
`benchmarks/private/`. Never send them to an API as part of offline evaluation.

Schema: unique `id`, `original` string, `should_edit` boolean, `accepted` list of
accepted full sentences, `tags` list, `protected_terms` list. Add allowed equivalent
rewrites to `accepted`. A KEEP case has an empty accepted list.

Predictions: one row per sample, `id`, `revised` full sentence (original for KEEP),
optional human-labelled `human_accepted` boolean. Missing/duplicate/extra IDs fail.

Run each fixed model/prompt/pipeline prediction file against the same samples:

```powershell
python -m benchmarks.evaluate --samples benchmarks/synthetic.jsonl --predictions cache/predictions-a.jsonl
python -m benchmarks.evaluate --samples benchmarks/synthetic.jsonl --predictions cache/predictions-b.jsonl
```

Record provider/model, prompt version, stage settings and sample version alongside
prediction files. The runner is offline: it does not generate model predictions.
Output is a readable JSON report with denominators and per-sample results.
Reference precision/recall use exact accepted matches, **not semantic quality**.
Unnecessary-edit rate uses proposed edits as denominator. Lexical fact damage is
only a rule-based proxy; it cannot detect all factual changes. Human acceptance is
null until explicitly labelled, never inferred from reference matching.
Word patch success must be measured separately with real Word; this runner does
not pretend a text prediction tests COM or formatting.

`public_derived.jsonl` adds ten CC BY 4.0 published sentences and ten deliberately
corrupted counterparts. Attribution, modifications and labelling limitations are
in [SOURCES.md](SOURCES.md). These are controlled agent-labelled cases, not human
acceptance data. Failed predictions can carry a non-OK `status`: they count as
failures, never KEEP; completion rate is reported and failed needed edits remain
in the recall denominator.

## Explicit live check

`python -m benchmarks.live --model MODEL --corpus public_derived --output cache/NEW-RUN`
reads a SiliconFlow credential only from standard input (use your existing secret
loader, do not paste it into a command or file). The endpoint is pinned to
`https://api.siliconflow.cn/v1`. Input is limited to the two checked-in corpora;
each case is isolated as a separate chapter. The runner caps the run at 80 calls,
one HTTP attempt per call, 90 seconds per attempt, and records synthetic/public
responses locally for diagnostics. JSON format correction uses one additional
explicit request, then fails closed. The cap is a request cap, not a currency cap.
The runner never calls Word and reports that limitation explicitly.

Remaining quality acceptance: obtain 30–50 authorised real examples, human-review
labels, capture fixed baseline/candidate predictions, compare reports and manually
adjudicate alternate valid rewrites before expanding to 100–300 examples.
