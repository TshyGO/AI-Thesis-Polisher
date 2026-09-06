"""Explicit synthetic-only stage-routing + Word smoke. Max eight API attempts."""
import logging
import sys
from engine.stage_models import build_stage_clients, StageClient
from benchmarks.p1_word_live import main


def routes(base_client):
    budget = {'attempts': 0}
    class BoundedStageClient(StageClient):
        def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
            if budget['attempts'] >= 8:
                raise RuntimeError('Stage Word test request cap exceeded')
            budget['attempts'] += 1
            print(f"{self.stage_config.stage} request {budget['attempts']}/8", file=sys.stderr, flush=True)
            return super().call_api(messages, temperature, timeout, max_retries=1)
    overrides = {stage: {'timeout': 90, 'max_retries': 1} for stage in ('understanding', 'editor', 'reviewer')}
    overrides['reviewer']['model'] = 'Qwen/Qwen3.6-27B'
    return build_stage_clients({'api_key': base_client.api_key, 'base_url': base_client.base_url, 'model': base_client.model},
                               overrides, 'independent', factory=BoundedStageClient)


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        main(stage_clients_factory=routes)
    except Exception as error:
        print('Stage Word test failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
