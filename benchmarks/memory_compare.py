"""Fixed synthetic memory-representation comparison. Key on stdin; max 14 attempts."""
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from engine.stage_models import StageClient, build_stage_clients
from engine.sentences import ParagraphSnapshot
from engine.chapter_memory import build_memory
from engine.sentence_pipeline import SentencePolishingPipeline
from engine.patches import sentence_patch_plan


def main():
    key = sys.stdin.read().strip()
    if not key:
        raise ValueError('Missing key')
    root = Path(__file__).resolve().parents[1] / 'cache' / ('memory-compare-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    registered = []
    class BudgetClient(StageClient):
        def __init__(self, cfg):
            super().__init__(cfg)
            registered.append(self)
        def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
            used = sum(len(c.telemetry) for c in registered)
            if used >= 14:
                raise RuntimeError('Memory comparison request cap')
            print(f'{self.stage_config.stage} attempt {used+1}/14', file=sys.stderr, flush=True)
            return super().call_api(messages, temperature, timeout, max_retries=1)
    clients = build_stage_clients({'api_key': key, 'base_url': 'https://api.siliconflow.cn/v1', 'model': 'deepseek-ai/DeepSeek-V4-Flash'},
        {'understanding': {'model': 'Qwen/Qwen3-30B-A3B-Instruct-2507', 'timeout': 90, 'max_retries': 1},
         'editor': {'timeout': 90, 'max_retries': 1}}, 'off', factory=BudgetClient)
    pipeline = SentencePolishingPipeline(clients['editor'], None, {'intensity': 'light'}, stage_clients=clients)
    pipeline.chapter_notes_dir = root / 'notes'
    pipeline.chapter_notes_dir.mkdir()
    chapters = [
        ({'name': 'Experiment A', 'start': 1, 'end': 2}, [ParagraphSnapshot(1, 'APTES denotes reagent A. The curing temperature was 80 ℃.'),
          ParagraphSnapshot(2, 'The APTES samples was cured.')], 'The APTES samples were cured.'),
        ({'name': 'Experiment B', 'start': 3, 'end': 4}, [ParagraphSnapshot(3, 'APTES denotes reagent B. The curing temperature was 120 ℃.'),
          ParagraphSnapshot(4, 'The APTES sample were cured.')], 'The APTES sample was cured.'),
    ]
    rows = []
    for index, (chapter, sources, reference) in enumerate(chapters):
        # Alternate representation order, with identical target/source and editor policy.
        modes = ['legacy', 'structured'] if index == 0 else ['structured', 'legacy']
        for mode in modes:
            started = time.monotonic()
            row = {'chapter': chapter['name'], 'mode': mode}
            try:
                if mode == 'legacy':
                    notes = pipeline.run_stage_0_chapter_understanding(chapter['name'], '\n'.join(p.text for p in sources), 'english')
                    context = {'legacy_notes': notes}
                else:
                    notes = build_memory(chapter, sources, 'fixed-synthetic-comparison', clients['understanding'],
                                         pipeline._chapter_model_identity(), root / 'memory')
                    context = notes.context_for(sources[-1])
                    (root / f"{mode}-{index}.json").write_text(json.dumps(notes.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
                    forbidden = 'reagent B' if index == 0 else 'reagent A'
                    assert forbidden not in json.dumps(context)
                    before = len(clients['understanding'].telemetry)
                    build_memory(chapter, sources, 'fixed-synthetic-comparison', clients['understanding'], pipeline._chapter_model_identity(), root / 'memory')
                    assert len(clients['understanding'].telemetry) == before
                neighbors = [{'paragraph_index': sources[0].index, 'text': sources[0].text}]
                decisions = pipeline._decide(sources[-1], notes, neighbors)
                revised = sources[-1].text
                rejected = []
                for decision in decisions:
                    if decision.decision == 'edit':
                        try:
                            _, revised = sentence_patch_plan(sources[-1], [decision])
                        except ValueError as error:
                            rejected.append(str(error))
                row.update(status='OK', memory_chars=len(json.dumps(context, ensure_ascii=False)),
                           revised=revised, reference_match=revised == reference, guard_rejections=rejected)
            except Exception as error:
                row.update(status=getattr(error, 'status', type(error).__name__))
            row['elapsed_seconds'] = round(time.monotonic()-started, 3)
            rows.append(row)
    report = {'kind': 'synthetic-memory-representation-smoke', 'prompt_version': pipeline.PROMPT_VERSION,
              'rows': rows, 'attempts': sum(len(c.telemetry) for c in registered),
              'requests': {stage: client.telemetry for stage, client in clients.items()}}
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'artifact': str(root / 'report.json'), **report}, ensure_ascii=False))


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        main()
    except Exception as error:
        print('Memory smoke failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
