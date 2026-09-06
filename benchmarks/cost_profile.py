"""Cold-cache cost profile of the current P1 pipeline on a fixed contiguous corpus.

Explicit paid run: pipe a SiliconFlow key into `python -m benchmarks.cost_profile`.
The endpoint is pinned to the official SiliconFlow address and only the checked-in
synthetic corpus is transmitted.

This measures the SHAPE AND COST of model traffic: attempts, tokens, latency and
how many times the same source text is re-sent. It is NOT a quality benchmark, NOT
a human acceptance measure, and NOT a Word patch test (see benchmarks/textdoc.py).
Token counts are provider usage observations, not currency costs.
"""
import argparse
import hashlib
import json
import logging
import sys
import time
import uuid
from pathlib import Path

from engine.sentence_pipeline import SentencePolishingPipeline
from engine.stage_models import StageClient, StageConfig, build_stage_clients
from benchmarks.textdoc import MemoryDocument, load_corpus


ENDPOINT = 'https://api.siliconflow.cn/v1'
DEFAULTS = {'editor': 'deepseek-ai/DeepSeek-V4-Flash',
            'understanding': 'Qwen/Qwen3-30B-A3B-Instruct-2507',
            'reviewer': 'deepseek-ai/DeepSeek-V4-Flash'}


class BudgetExceeded(RuntimeError):
    """Hard request cap; a profiling run must never spend without a ceiling."""


class Recorder:
    """One row per actual HTTP attempt, including explicit format repairs."""

    def __init__(self, limit):
        self.limit = limit
        self.rows = []

    def reserve(self, stage):
        if len(self.rows) >= self.limit:
            raise BudgetExceeded('Request cap %d reached' % self.limit)
        print('%s attempt %d/%d' % (stage, len(self.rows) + 1, self.limit), file=sys.stderr, flush=True)

    def add(self, row):
        self.rows.append(row)


def payload_shape(messages):
    """Chars actually sent, split into source text vs. everything else."""
    # An assistant turn in the request means this is the one explicit format repair,
    # whose transport status is OK even though the previous answer was rejected.
    shape = {'prompt_chars': sum(len(m.get('content') or '') for m in messages),
             'is_repair': any(m.get('role') == 'assistant' for m in messages),
             'paragraphs': [], 'target_chars': 0, 'context_chars': 0, 'memory_chars': 0, 'source_chars': 0}
    paragraphs = set()
    for message in messages:
        if message.get('role') != 'user':
            continue
        try:
            body = json.loads(message.get('content') or '')
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(body, dict):
            continue
        for sentence in body.get('sentences') or []:
            shape['target_chars'] += len(sentence.get('text') or '')
            paragraphs.add(str(sentence.get('sentence_id') or '').split(':')[0])
        source = body.get('source')
        if isinstance(source, dict):
            for sentence_id, item in source.items():
                shape['target_chars'] += len(item or '')
                paragraphs.add(str(sentence_id).split(':')[0])
        for neighbor in body.get('context') or []:
            shape['context_chars'] += len(neighbor.get('text') or '')
        if 'chapter_memory' in body:
            shape['memory_chars'] += len(json.dumps(body['chapter_memory'], ensure_ascii=False))
        break  # only the first structured user payload; repair turns echo it
    shape['source_chars'] = shape['target_chars'] + shape['context_chars']
    shape['paragraphs'] = sorted(p for p in paragraphs if p)
    return shape


def profiling_factory(recorder):
    class ProfilingClient(StageClient):
        def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
            stage = self.stage_config.stage
            recorder.reserve(stage)
            before = len(self.telemetry)
            started = time.monotonic()
            try:
                return super().call_api(messages, temperature, timeout, max_retries=1)
            finally:
                attempts = self.telemetry[before:]
                last = attempts[-1] if attempts else {'status': 'NO_TELEMETRY'}
                row = {'stage': stage, 'model': self.stage_config.model,
                       'status': last.get('status'), 'seconds': round(time.monotonic() - started, 3),
                       'prompt_tokens': last.get('prompt_tokens'),
                       'completion_tokens': last.get('completion_tokens'),
                       'attempts_recorded': len(attempts)}
                row.update(payload_shape(messages))
                recorder.add(row)

    return ProfilingClient


class IsolatedPipeline(SentencePolishingPipeline):
    """Cold cache per run: a warm shared cache would report zero requests."""

    def isolate(self, root):
        self.cache_file = Path(root) / 'workflow_cache.json'
        self.chapter_notes_dir = Path(root) / 'chapter_notes'
        self.chapter_notes_dir.mkdir(parents=True, exist_ok=True)


def paragraph_outcomes(records, corpus_rows=None):
    """Per-paragraph reference outcome; later stages diff against exactly this."""
    labels = {row['order']: row for row in (corpus_rows or [])}
    outcomes = {}
    for record in records:
        index = record.get('paragraph_idx')
        outcome = outcomes.setdefault(index, {'paragraph': index, 'statuses': {},
                                              'edited_sentences': [], 'edit_written': 0})
        status = record.get('status', '')
        outcome['statuses'][status] = outcome['statuses'].get(status, 0) + 1
        if status == 'EDIT_WRITTEN':
            outcome['edit_written'] += 1
            outcome['edited_sentences'].append(record.get('sentence_id'))
    for index, outcome in outcomes.items():
        row = labels.get(index)
        if row is not None:
            outcome['chapter'] = row['chapter']
            outcome['seeded'] = list(row.get('seeded') or [])
    return [outcomes[index] for index in sorted(outcomes)]


def reference_agreement(outcomes):
    """Corpus labels are agent-authored, so this is label agreement, not accuracy."""
    if any('seeded' not in outcome for outcome in outcomes):
        return None
    seeded = [o for o in outcomes if o['seeded']]
    clean = [o for o in outcomes if not o['seeded']]
    return {'seeded_paragraphs': len(seeded),
            'seeded_without_edit': sum(1 for o in seeded if not o['edit_written']),
            'clean_paragraphs': len(clean),
            'clean_with_edit': sum(1 for o in clean if o['edit_written'])}


def triage_agreement(triage_log, records):
    """Shadow comparison: what would screening have dropped that editing wrote?"""
    verdicts = (triage_log or {}).get('verdicts') or {}
    if not verdicts:
        return None
    written = {r.get('sentence_id') for r in records if r.get('status') == 'EDIT_WRITTEN'}
    screened_out = {sid for sid, needs in verdicts.items() if not needs}
    missed = sorted(written & screened_out)
    return {'sentences_screened': len(verdicts),
            'marked_for_edit': sum(1 for needs in verdicts.values() if needs),
            'screened_out': len(screened_out),
            'edits_written': len(written),
            'edits_screened_out': len(missed),
            'missed_sentence_ids': missed,
            'screening_failures': len((triage_log or {}).get('failures') or []),
            'unscreened_written_edits': len(written - set(verdicts))}


def summarize(recorder_rows, records, corpus_chars, paragraph_count, corpus_rows=None):
    stages = {}
    for row in recorder_rows:
        bucket = stages.setdefault(row['stage'], {'attempts': 0, 'ok': 0, 'repairs': 0, 'seconds': 0.0,
                                                  'prompt_tokens': 0, 'completion_tokens': 0,
                                                  'unknown_token_attempts': 0, 'prompt_chars': 0,
                                                  'source_chars': 0, 'memory_chars': 0})
        bucket['attempts'] += 1
        bucket['ok'] += 1 if row.get('status') == 'OK' else 0
        bucket['repairs'] += 1 if row.get('is_repair') else 0
        bucket['seconds'] = round(bucket['seconds'] + (row.get('seconds') or 0), 3)
        for name in ('prompt_tokens', 'completion_tokens'):
            value = row.get(name)
            if isinstance(value, int):
                bucket[name] += value
            elif name == 'prompt_tokens':
                bucket['unknown_token_attempts'] += 1
        for name in ('prompt_chars', 'source_chars', 'memory_chars'):
            bucket[name] += row.get(name) or 0

    edited = {r['paragraph_idx'] for r in records if r.get('status') == 'EDIT_WRITTEN'}
    seen = {r['paragraph_idx'] for r in records}
    statuses = {}
    for record in records:
        status = record.get('status', '')
        statuses[status] = statuses.get(status, 0) + 1
    editor_source = sum(row.get('source_chars') or 0 for row in recorder_rows if row['stage'] == 'editor')
    outcomes = paragraph_outcomes(records, corpus_rows)
    return {
        'stages': stages,
        'paragraph_outcomes': outcomes,
        'reference_agreement': reference_agreement(outcomes),
        'totals': {'attempts': len(recorder_rows),
                   'prompt_tokens': sum(b['prompt_tokens'] for b in stages.values()),
                   'completion_tokens': sum(b['completion_tokens'] for b in stages.values()),
                   'seconds': round(sum(b['seconds'] for b in stages.values()), 3)},
        'paragraphs': {'in_corpus': paragraph_count, 'reported': len(seen),
                       'with_edit_written': len(edited), 'zero_edit': len(seen) - len(edited),
                       'zero_edit_ratio': round((len(seen) - len(edited)) / len(seen), 4) if seen else None},
        'sentence_rows_by_status': statuses,
        'amplification': {'corpus_chars': corpus_chars, 'editor_source_chars': editor_source,
                          'source_text_factor': round(editor_source / corpus_chars, 3) if corpus_chars else None},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', default=str(Path(__file__).with_name('chapter_corpus.jsonl')))
    parser.add_argument('--editor-model', default=DEFAULTS['editor'])
    parser.add_argument('--understanding-model', default=DEFAULTS['understanding'])
    parser.add_argument('--reviewer-model', default=DEFAULTS['reviewer'])
    parser.add_argument('--review-mode', choices=('off', 'same', 'independent'), default='same')
    parser.add_argument('--max-requests', type=int, default=60)
    parser.add_argument('--batch-size', type=int, default=1,
                        help='consecutive paragraphs packed into one editor call')
    parser.add_argument('--triage', choices=('off', 'shadow'), default='off',
                        help='shadow screens every editor call without skipping anything')
    parser.add_argument('--triage-model', default=DEFAULTS['editor'])
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    key = sys.stdin.read().strip()
    if not key:
        raise ValueError('Missing key: pipe the credential into stdin, never as an argument')
    logging.basicConfig(level=logging.WARNING)

    texts, chapters, rows = load_corpus(args.corpus)
    corpus_bytes = Path(args.corpus).read_bytes()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    run_root = output / ('run-' + uuid.uuid4().hex[:8])
    run_root.mkdir()

    recorder = Recorder(args.max_requests)
    overrides = {'understanding': {'model': args.understanding_model, 'timeout': 180, 'max_retries': 1},
                 'editor': {'model': args.editor_model, 'timeout': 90, 'max_retries': 1}}
    if args.review_mode == 'independent':
        overrides['reviewer'] = {'model': args.reviewer_model, 'timeout': 90, 'max_retries': 1}
    clients = build_stage_clients({'api_key': key, 'base_url': ENDPOINT, 'model': args.editor_model},
                                  overrides, args.review_mode, factory=profiling_factory(recorder))

    if args.triage != 'off':
        # Triage is not routed by resolve_configs; build it explicitly so its cost is separable.
        clients['triage'] = profiling_factory(recorder)(
            StageConfig('triage', ENDPOINT, args.triage_model, key, 0.1, 60, 1))

    document = MemoryDocument(texts, chapters)
    config = {'language': 'english', 'intensity': 'standard', 'min_chars': 20,
              'use_cross_review': args.review_mode != 'off', 'memory_mode': 'structured',
              'protected_terms': [], 'skipped_chapters': [], 'output_dir': str(run_root),
              'editor_batch_size': args.batch_size, 'triage_mode': args.triage,
              'excel_output_filename': 'Report.xlsx', 'original_filename': Path(args.corpus).name}
    pipeline = IsolatedPipeline(clients['editor'], document, config, stage_clients=clients)
    pipeline.isolate(run_root)

    started = time.monotonic()
    failure = None
    changes = 0
    try:
        changes, _ = pipeline.process_document(args.corpus)
    except Exception as error:  # a capped or failed run is still a reported measurement
        failure = '%s: %s' % (type(error).__name__, error)

    report = {
        'label': args.label,
        'generated': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'endpoint': ENDPOINT,
        'corpus': {'path': Path(args.corpus).name,
                   'sha256': hashlib.sha256(corpus_bytes).hexdigest(),
                   'paragraphs': len(texts), 'chars': sum(len(t) for t in texts),
                   'seeded_paragraphs': sum(1 for row in rows if row.get('seeded'))},
        'config': {'models': {stage: overrides.get(stage, {}).get('model', args.editor_model) for stage in DEFAULTS},
                   'review_mode': args.review_mode, 'memory_mode': 'structured',
                   'language': 'english', 'intensity': 'standard', 'max_requests': args.max_requests,
                   'editor_batch_size': args.batch_size, 'triage_mode': args.triage,
                   'triage_model': args.triage_model if args.triage != 'off' else None},
        'wall_seconds': round(time.monotonic() - started, 3),
        'changes_written': changes,
        'failure': failure,
        'run_summary': pipeline.run_summary,
        'requests': recorder.rows,
    }
    report.update(summarize(recorder.rows, pipeline.last_records, report['corpus']['chars'], len(texts), rows))
    report['triage_agreement'] = triage_agreement(pipeline.run_summary.get('triage'), pipeline.last_records)
    path = run_root / 'cost-profile.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('label', 'totals', 'stages', 'paragraphs', 'amplification',
                                            'triage_agreement', 'failure')},
                     ensure_ascii=False, indent=2))
    print('report: %s' % path, file=sys.stderr)
    if failure:
        sys.exit(1)


if __name__ == '__main__':
    main()
