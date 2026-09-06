# Editor paragraph packing — 2026-09-06

## What changed

One editor call may now cover several consecutive paragraphs of the same
chapter. `editor_batch_size` controls how many; the default is **1**, which is
byte-identical to the previous payload.

Everything downstream is unchanged. Decisions are split back per paragraph, each
paragraph is still patched as its own atomic Word transaction, and the cache is
still keyed per paragraph so a resumed run reuses exactly what it had. Review is
packed over the same group (see below) and still judges each candidate on its own.

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
19% less stage time, and **no quality difference on this fixture** — in every run
of every group, each seeded paragraph was edited and no clean paragraph was
touched.

No batch fell back to single paragraphs in any run. The extra editor calls seen
in some runs (size 3: 3 repairs in 3 runs; size 6: 3 repairs in 7 runs) are the
contract's one explicit format repair, which existed before packing.

## Review packing

After the editor was packed, the reviewer became the dominant call count: 7 of 11
attempts, each re-sending the whole review policy and a fresh copy of chapter
memory for roughly 430 characters of actual source text. Candidates from one
editor group are now reviewed in a single call, under the same rules — the group
never crosses a chapter, never includes a paragraph whose review is already
cached, and never triggers an editor call of its own. A failed review batch is
retried one paragraph at a time and is never recorded as KEEP.

| Configuration | Runs | Attempts | Understanding | Editor | Reviewer | Prompt tok | Completion tok | Total tok | Stage s | Missed | False |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| No packing | 5 | 21 | 2 | 12 | 6 | 14,544 | 11,689 | 25,417 | 86.5 | 0 | 0 |
| Editor only | 7 | 11 | 2 | 2 | 7 | 10,323 | 9,530 | 19,853 | 69.9 | 0 | 0 |
| Editor + review | 5 | 7 | 2 | 2 | 2 | 9,924 | 9,010 | 17,542 | 65.8 | 0 | 0 |

Against the unpacked control: **67% fewer HTTP attempts, 31% fewer tokens, 24%
less stage time**, with no quality difference in any of the 17 packed runs.

Reviewer tokens fell from 4,130+2,417 to 2,160+1,579 while covering the same
candidates. Editor completion tokens rose slightly (6,308 to 7,085); one packed
editor call now carries every decision, and run-to-run variance at this sample
size is larger than that difference.

### Size 3 saved calls but not tokens

Its median total token count is slightly **above** the control. Three format
repairs in three runs is enough to erase the prompt saving at this scale, and
three runs cannot separate that from noise. Fewer calls is not automatically
cheaper; only the size-6 group showed a token saving worth the name.

## Long chapter: a real quality difference, and a correction

The claim above — no quality difference — was measured at **paragraph**
granularity on a corpus whose chapters a size-6 batch swallows whole. Both
limits mattered. [long_chapter_corpus.jsonl](long_chapter_corpus.jsonl) is one
chapter of 18 contiguous paragraphs, 43 sentences, 7 of them carrying seeded
defects, so a size-6 batch has real neighbours outside it and paragraph-level
counting can no longer hide a missed sentence inside an edited paragraph.

| Configuration | Runs | Attempts | Tokens | Text factor | Stage s | Seeded-sentence edits per run | Other edits per run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Size 1 | 7 | 27 | 35,329 | 3.76 | 164.4 | 13.71 | 0.14 |
| Size 6 | 11 | 8 | 26,467 | 1.30 | 115.0 | 13.36 | 0.55 |

70% fewer attempts, 25% fewer tokens, 30% less time — and the text factor is
**1.30**, not 0.99, exactly as the caveat below predicted.

Per-sentence comparison ([compare_runs.py](compare_runs.py)) shows the difference
is not uniform. Three sentences diverge:

| Sentence | Kind | Size 1 | Size 6 |
| --- | --- | --- | --- |
| P17:S2 | seeded verbosity ("It should be pointed out that…") | 7/7 | 5/11 |
| P2:S2 | seeded agreement ("Aliquots was withdrawn") | 5/7 | 11/11 |
| P3:S3 | clean; tense ("the mean is reported" → "was reported") | 1/7 | 6/11 |

So packing became **more** reliable on a plain agreement error and **less**
reliable on a verbosity fix, and it made a borderline tense overcorrection four
times as often. Net seeded-sentence recall fell 2.6% (13.71 to 13.36 per run)
while unlabelled edits rose from 0.14 to 0.55 per run.

That is a real difference, not noise at the level of a single run: P17:S2 was
edited in every unpacked run and in fewer than half the packed ones. The
hypothesis — untested — is that with sixteen sentences in one call the editor
attends to unambiguous grammatical errors and treats style more loosely. The
tense edit is arguably wrong on its own terms: "was repeated … is reported" is
normal academic usage, and "consistency" is the overcorrection reflex.

None of this changes facts, numbers, citations or modality; the validators and
the reviewer still gate every edit. But "no quality difference" was true only of
the smaller fixture at coarser granularity, and it is retracted here.

### The 0.99 factor is specific to this fixture

Both corpus chapters are exactly six paragraphs, so a size-6 batch covers a whole
chapter and has no neighbours left to send. A real chapter of, say, thirty
paragraphs batched six at a time still sends three neighbour paragraphs per
batch, giving roughly 1.5x rather than 1.0x. Expect the real saving to sit
between the size-3 and size-6 rows here, not at the bottom.

## Default stays at 1

The long-chapter measurement is the reason this is not just caution. Packing
buys a 70% cut in attempts and a 25% cut in tokens, and costs a small, real
shift in which defects get caught. Whether that trade is worth taking depends on
the document and the author, so it stays a decision the user makes, not one the
tool makes for them. Raising the default needs a real document and a human
reading the diff.

The UI exposes “每次编辑调用打包的段落数”. Changing it invalidates cached
suggestions, because it changes what the editor was asked.

## Boundaries

- Synthetic, agent-labelled corpora. Recall figures are label agreement, not an
  editing-quality result, and 7 and 11 runs cannot resolve small differences.
- Paragraph-level counting hides a missed sentence inside an edited paragraph.
  Compare configurations per sentence with `compare_runs.py`.
- A run that lost a chapter is excluded from comparisons by default; including
  it would blame packing for a chapter-memory failure.
- Longer batches mean a longer single response. The contract still requires a
  decision for every supplied sentence, so a truncated answer fails the whole
  batch and falls back; it is never silently partial.
- Review packing reuses `editor_batch_size`; there is no separate knob.
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
