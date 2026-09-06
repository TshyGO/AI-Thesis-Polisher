"""Opt-in paid synthetic Word end-to-end test, including cached fresh-output replay."""
import hashlib
import json
import logging
import shutil
import sys
import uuid
from pathlib import Path
from engine.document import DocumentProcessor
from engine.llm_client import LLMClient
from engine.sentence_pipeline import SentencePolishingPipeline
from engine.revision_contract import SentenceDecision
from engine.patches import sentence_patch_plan


class BoundedClient(LLMClient):
    calls = 0
    def call_api(self, messages, temperature=0.1, timeout=60, max_retries=3):
        if self.calls >= 8:
            raise RuntimeError('Request cap exceeded')
        self.calls += 1
        print(f'API request {self.calls}/8', file=sys.stderr, flush=True)
        return super().call_api(messages, temperature, timeout=90, max_retries=1)


def main(stage_clients_factory=None):
    run = Path(__file__).resolve().parents[1] / 'cache' / ('p1-word-live-' + uuid.uuid4().hex)
    run.mkdir(parents=True)
    source = run / 'source.docx'
    with DocumentProcessor() as doc:
        doc.doc = doc.word.Documents.Add()
        doc.doc.Content.Text = ('The results shows 0.021 W m−1 K−1. Fig. 2 shows the APTES samples. '
                                'The results shows 0.021 W m−1 K−1.\r')
        doc.doc.Range(0, 3).Font.Bold = True
        original = doc.snapshot_paragraph(1)
        doc.doc.SaveAs2(str(source), FileFormat=16)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    client = BoundedClient(sys.stdin.read().strip(), 'https://api.siliconflow.cn/v1', 'deepseek-ai/DeepSeek-V4-Flash')
    routes = stage_clients_factory(client) if stage_clients_factory else None
    def request_count():
        return sum(len(c.telemetry) for c in routes.values()) if routes else client.calls
    call_counts = []
    changes_per_run = []
    for n in (1, 2):
        output = run / f'result-{n}.docx'
        shutil.copy2(source, output)
        with DocumentProcessor() as doc:
            pipeline = SentencePolishingPipeline(client, doc, {'language': 'english', 'min_chars': 1,
                'intensity': 'light', 'original_filename': 'source.docx', 'protected_terms': ['APTES'],
                'output_dir': str(run), 'excel_output_filename': f'report-{n}.xlsx', 'use_cross_review': True}, stage_clients=routes)
            pipeline.cache_file = run / 'suggestions.json'
            pipeline.chapter_notes_dir = run / 'notes'
            pipeline.chapter_notes_dir.mkdir(exist_ok=True)
            changes, report = pipeline.process_document(str(output))
            assert changes > 0, 'No edit was written'
            assert not any(r['status'].endswith(('ERROR', 'FAILED', 'TIMEOUT')) for r in pipeline.last_records)
            decisions = [SentenceDecision(r['sentence_id'], 'edit', 'check', r['new'], 'grammar', 1)
                         for r in pipeline.last_records if r['status'] == 'EDIT_WRITTEN']
            _, expected = sentence_patch_plan(original, decisions, ['APTES'])
        with DocumentProcessor() as doc:
            doc.open_document(str(output), read_only=True)
            assert doc._visible_paragraph_text(1) == expected
            assert doc.doc.Revisions.Count > 0
            assert doc.doc.Range(0, 3).Font.Bold == -1
        assert Path(report).exists()
        call_counts.append(request_count())
        changes_per_run.append(changes)
    assert call_counts[0] == call_counts[1], 'Replay unexpectedly called the API'
    assert changes_per_run[0] == changes_per_run[1]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    result = {'status': 'PASS', 'model': client.model, 'api_calls': request_count(),
              'stage_models': {s: c.model for s, c in routes.items()} if routes else {'shared': client.model},
              'replay_calls': 0, 'edited_sentences': changes_per_run[0], 'source_unchanged': True,
              'saved_Word_revisions_verified': True, 'artifacts': str(run)}
    (run / 'validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        main()
    except Exception as error:
        print('Synthetic Word live test failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
