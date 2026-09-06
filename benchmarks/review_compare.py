"""Controlled reviewer comparison, not an end-to-end or human acceptance benchmark.

Explicit paid run: pipe a SiliconFlow key into python -m benchmarks.review_compare.
Only the fixed synthetic corpus is transmitted. At most 12 HTTP attempts (3 trials).
"""
import hashlib
import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from engine.stage_models import build_stage_clients, StageClient, resolve_configs
from engine.sentence_pipeline import SentencePolishingPipeline
from engine.sentences import ParagraphSnapshot
from engine.revision_contract import SentenceDecision, validate_review
from engine.patches import sentence_patch_plan


EDITOR = 'deepseek-ai/DeepSeek-V4-Flash'
REVIEWER = 'Qwen/Qwen3-30B-A3B-Instruct-2507'


def load_cases():
    path = Path(__file__).with_name('reviewer_cases.jsonl')
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return cases, cases_fingerprint(cases)


def cases_fingerprint(cases):
    return hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def prepare(cases):
    candidates, nodes, labels, guarded = [], [], {}, []
    for index, case in enumerate(cases, 1):
        snapshot = ParagraphSnapshot(index, case['original'])
        if len(snapshot.sentences) != 1:
            raise ValueError('Each controlled case must have one sentence')
        node = snapshot.sentences[0]
        decision = SentenceDecision(node.id, 'edit', 'Proposed edit', case['revised'], 'clarity', 0.8)
        try:
            sentence_patch_plan(snapshot, [decision])
        except ValueError as error:
            guarded.append({'id': case['id'], 'reason': str(error)})
            continue
        nodes.append(node)
        candidates.append(decision)
        labels[node.id] = case
    return SimpleNamespace(sentences=nodes), candidates, labels, guarded


def score(reviewed, labels):
    if len(reviewed) != len(labels) or {d.sentence_id for d in reviewed} != set(labels):
        raise ValueError('Incomplete reviewer coverage')
    accepted = {d.sentence_id for d in reviewed if d.decision == 'edit'}
    good = {sid for sid, case in labels.items() if case['expected_accept']}
    bad = set(labels) - good
    technical = {sid for sid in bad if labels[sid]['technical_risk']}
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    return {'accepted_good': len(accepted & good), 'retained_bad': len(accepted & bad),
            'rejected_good': len(good - accepted), 'retained_technical_risk': len(accepted & technical),
            'edit_precision': ratio(len(accepted & good), len(accepted)),
            'good_retention_rate': ratio(len(accepted & good), len(good)),
            'bad_retention_rate': ratio(len(accepted & bad), len(bad))}


def run(key, factory=None, trials=3, reviewer_model=REVIEWER):
    if trials not in (1, 2, 3):
        raise ValueError('At most three trials')
    cases, corpus_hash = load_cases()
    snapshot, proposals, labels, guarded = prepare(cases)
    budget = {'attempts': 0}
    class BudgetClient(StageClient):
        def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
            if budget['attempts'] >= 12:
                raise RuntimeError('Comparison request budget exceeded')
            budget['attempts'] += 1
            print(f"API attempt {budget['attempts']}/12", file=sys.stderr, flush=True)
            return super().call_api(messages, temperature, timeout, max_retries=1)
    base = {'base_url': 'https://api.siliconflow.cn/v1', 'model': EDITOR, 'api_key': key}
    resolve_configs(base, {'reviewer': {'model': reviewer_model}}, 'independent')
    output = []
    # Alternate order to reduce a systematic first/second reviewer timing bias.
    for trial in range(trials):
        modes = ['off', 'same', 'independent'] if trial % 2 == 0 else ['off', 'independent', 'same']
        for mode in modes:
            clients = build_stage_clients(base, {'reviewer': {'model': reviewer_model, 'timeout': 90, 'max_retries': 1}},
                                          mode, factory=factory or BudgetClient)
            pipeline = SentencePolishingPipeline(clients['editor'], None, {}, stage_clients=clients)
            started = time.monotonic()
            row = {'trial': trial+1, 'mode': mode}
            try:
                reviewed = proposals if mode == 'off' else pipeline._review(snapshot, proposals, 'Independent synthetic cases. Judge each in isolation.')
                validate_review(proposals, reviewed)
                row.update(status='OK', **score(reviewed, labels))
                row['decisions'] = [{'id': labels[d.sentence_id]['id'], 'accepted': d.decision == 'edit', 'reason': d.reason} for d in reviewed]
            except Exception as error:
                row.update(status=getattr(error, 'status', type(error).__name__))
            events = clients['reviewer'].telemetry
            row.update(elapsed_seconds=round(time.monotonic()-started, 3), requests=len(events))
            for token_type in ('prompt_tokens', 'completion_tokens'):
                values = [event[token_type] for event in events]
                row[token_type] = sum(values) if all(type(v) is int for v in values) else None
            output.append(row)
    return {'kind': 'controlled-synthetic-reviewer-comparison', 'corpus_sha256': corpus_hash,
            'prompt_version': SentencePolishingPipeline.PROMPT_VERSION,
            'system_message_layout': 'single combined system message',
            'editor_model_label': EDITOR, 'independent_reviewer': reviewer_model,
            'candidate_source': 'fixed constructed proposals, not generated by the named editor in this experiment',
            'cases': len(cases), 'eligible': len(proposals), 'guard_rejections': guarded,
            'temperature': 0.1, 'trials': trials, 'rows': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewer-model', default=REVIEWER)
    args = parser.parse_args()
    key = sys.stdin.read().strip()
    if not key:
        raise ValueError('Missing key on stdin')
    report = run(key, reviewer_model=args.reviewer_model)
    folder = Path(__file__).resolve().parents[1] / 'cache' / ('review-compare-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'artifact': str(folder / 'report.json'), 'cases': report['cases'], 'eligible': report['eligible'],
                      'rows': [{k: v for k, v in row.items() if k != 'decisions'} for row in report['rows']]}, ensure_ascii=False))


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        main()
    except Exception as error:
        print('Comparison failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
