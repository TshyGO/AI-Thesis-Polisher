# Editor paragraph packing — 2026-09-06

## What changed

One editor call may now cover several consecutive paragraphs of the same
chapter. `editor_batch_size` controls how many; the default is **1**, which is
byte-identical to the previous payload.

Everything downstream is unchanged. Decisions are split back per paragraph,
each paragraph is still patched as its own atomic Word transaction, review still
runs per paragraph on its own candidates, and the cache is still keyed per
paragraph so a resumed run reuses exactly what it had.

A batch that fails for any reason — format error after its one repair, timeout,
HTTP error — is retried **paragraph by paragraph**, never recorded as KEEP. Each
member is retried once on its own, so a failed batch cannot cascade into
repeated batch attempts. Batches never cross a chapter boundary, never include a
paragraph that already has a cached suggestion, and stay contiguous so the
neighbour window remains exact.

## Why: the baseline sent every paragraph 3.3 times

Each paragraph was its own call carrying the `index-2, index-1, index+1`
neighbour window and a fresh copy of chapter memory, so the editor received
3.305x the corpus text ([COST_BASELINE.md](COST_BASELINE.md)). Packing turns
that overlap into ordinary in-batch context.

## Measured

Same fixed corpus, same models, cold cache, medians per group. Quality columns
count every run in the group.

| Batch size | Runs | Attempts | Editor calls | Prompt tok | Completion tok | Total tok | Text factor | Stage s | Seeded missed | Clean edited |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (control) | 5 | 21 | 12 | 14,544 | 11,689 | 25,417 | 3.31 | 86.5 | 0 | 0 |
| 3 | 3 | 15 | 5 | 14,816 | 10,934 | 25,750 | 1.85 | 97.1 | 0 | 0 |
| 6 | 7 | 11 | 2 | 10,323 | 9,530 | 19,853 | 0.99 | 69.9 | 0 | 0 |

Raw per-attempt records: [packing-2026-09-06.json](results/packing-2026-09-06.json).

At size 6: 48% fewer HTTP attempts, 83% fewer editor calls, 22% fewer tokens,
19% less stage time, and **no quality difference on this fixture** — in all 15
runs across the three groups, every seeded paragraph was edited and no clean
paragraph was touched.

No batch fell back to single paragraphs in any run. The extra editor calls seen
in some runs (size 3: 3 repairs in 3 runs; size 6: 3 repairs in 7 runs) are the
contract's one explicit format repair, which existed before packing.

### Size 3 saved calls but not tokens

Its median total token count is slightly **above** the control. Three format
repairs in three runs is enough to erase the prompt saving at this scale, and
three runs cannot separate that from noise. Fewer calls is not automatically
cheaper; only the size-6 group showed a token saving worth the name.

### The 0.99 factor is specific to this fixture

Both corpus chapters are exactly six paragraphs, so a size-6 batch covers a whole
chapter and has no neighbours left to send. A real chapter of, say, thirty
paragraphs batched six at a time still sends three neighbour paragraphs per
batch, giving roughly 1.5x rather than 1.0x. Expect the real saving to sit
between the size-3 and size-6 rows here, not at the bottom.

## Default stays at 1

The evidence is 15 runs on a 12-paragraph synthetic fixture with clean prose and
no tables, footnotes or complex formatting. That is enough to expose the control
and recommend trying it; it is not enough to change what happens by default to
someone's thesis. Raising the default needs a real document, long chapters and a
human reading the diff.

The UI exposes “每次编辑调用打包的段落数”. Changing it invalidates cached
suggestions, because it changes what the editor was asked.

## Boundaries

- Synthetic, agent-labelled corpus. Zero missed defects here is label agreement
  on 12 paragraphs, not an editing-quality result.
- Longer batches mean a longer single response. The contract still requires a
  decision for every supplied sentence, so a truncated answer fails the whole
  batch and falls back; it is never silently partial.
- Review batching was deliberately left out of this change. The reviewer is
  still one call per paragraph with candidates, roughly a quarter of the tokens.
- A failed batch costs its own attempt plus one per member, so a run against a
  flaky endpoint can be more expensive than no packing at all.

## Reproduce

```powershell
python -m unittest test_batching test_p1 test_chapter_memory test_cost_profile
python -m unittest test_p1_word
```

`test_batching` covers packing, fallback, chapter boundaries, cache exclusion and
per-paragraph decision isolation offline; `test_p1_word.WordBatchTests` writes a
packed batch into real Word and checks every paragraph's tracked result.

```powershell
$c = Import-Clixml <your DPAPI credential path>
$c.GetNetworkCredential().Password | python -m benchmarks.cost_profile --output cache/pack --label pack6 --batch-size 6 --max-requests 40
```
