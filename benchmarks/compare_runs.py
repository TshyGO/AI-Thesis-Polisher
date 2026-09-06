"""Per-sentence edit frequency across repeated runs of two configurations.

python -m benchmarks.compare_runs --a "cache/long-size1/run-*" --b "cache/long-size6/run-*"

Offline: it reads cost-profile.json reports that already exist and sends nothing.
Frequencies are how often each configuration wrote an edit for a sentence, which
exposes behaviour differences that a single run hides. It is not a quality
judgement: the corpus labels are agent-authored, and a sentence both
configurations edit may still be edited wrongly.
"""
import argparse
import glob
import json
from pathlib import Path


def lost_a_chapter(report):
    """A run that never edited part of the document cannot be compared on edits."""
    memory = (report.get('run_summary') or {}).get('memory') or {}
    return bool(memory.get('failed_chapters')) or bool(report.get('failure'))


def load_reports(pattern, include_incomplete=False):
    reports, skipped = [], []
    for path in sorted(glob.glob(pattern), key=lambda p: Path(p).stat().st_mtime):
        report = Path(path) / 'cost-profile.json'
        if not report.exists():
            continue
        loaded = json.loads(report.read_text(encoding='utf-8'))
        if lost_a_chapter(loaded) and not include_incomplete:
            skipped.append(loaded.get('label'))
            continue
        reports.append(loaded)
    if skipped:
        print('skipped %d incomplete run(s): %s' % (len(skipped), ', '.join(map(str, skipped))))
    if not reports:
        raise ValueError('No comparable cost-profile.json found for ' + pattern)
    return reports


def edit_frequency(reports):
    """sentence_id -> number of runs whose pipeline wrote an edit for it."""
    counts = {}
    for report in reports:
        for outcome in report.get('paragraph_outcomes') or []:
            for sentence_id in outcome.get('edited_sentences') or []:
                counts[sentence_id] = counts.get(sentence_id, 0) + 1
    return counts


def seeded_sentences(reports):
    """Paragraph indices the corpus labelled as carrying a seeded defect."""
    seeded = set()
    for report in reports:
        for outcome in report.get('paragraph_outcomes') or []:
            if outcome.get('seeded'):
                seeded.add(outcome['paragraph'])
    return seeded


def divergence(reports_a, reports_b):
    counts_a, counts_b = edit_frequency(reports_a), edit_frequency(reports_b)
    seeded = seeded_sentences(reports_a) | seeded_sentences(reports_b)
    runs_a, runs_b = len(reports_a), len(reports_b)
    rows = []
    for sentence_id in sorted(set(counts_a) | set(counts_b)):
        first, second = counts_a.get(sentence_id, 0), counts_b.get(sentence_id, 0)
        rows.append({'sentence_id': sentence_id,
                     'paragraph': int(sentence_id.split(':')[0][1:]),
                     'in_seeded_paragraph': int(sentence_id.split(':')[0][1:]) in seeded,
                     'a_edits': first, 'a_runs': runs_a,
                     'b_edits': second, 'b_runs': runs_b,
                     'rate_difference': round(second / runs_b - first / runs_a, 3)})
    rows.sort(key=lambda row: (-abs(row['rate_difference']), row['sentence_id']))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a', required=True, help='glob for the baseline run directories')
    parser.add_argument('--b', required=True, help='glob for the candidate run directories')
    parser.add_argument('--label-a', default='A')
    parser.add_argument('--label-b', default='B')
    parser.add_argument('--include-incomplete', action='store_true',
                        help='keep runs that lost a chapter; excluded by default')
    args = parser.parse_args()
    rows = divergence(load_reports(args.a, args.include_incomplete),
                      load_reports(args.b, args.include_incomplete))
    print(f"{'sentence':<12}{'seeded':>7}{args.label_a:>12}{args.label_b:>12}{'diff':>8}")
    for row in rows:
        print(f"{row['sentence_id']:<12}{str(row['in_seeded_paragraph']):>7}"
              f"{row['a_edits']:>7}/{row['a_runs']:<4}{row['b_edits']:>7}/{row['b_runs']:<4}"
              f"{row['rate_difference']:>8.2f}")


if __name__ == '__main__':
    main()
