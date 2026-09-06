"""Explicit synthetic-only stage-routing + Word smoke. Max eight API attempts."""
import logging
import argparse
import sys
from engine.stage_models import build_stage_clients, StageClient
from benchmarks.p1_word_live import main


def routes(base_client, reviewer_model='Qwen/Qwen3.6-27B', understanding_model=None):
    registered = []
    class BoundedStageClient(StageClient):
        def __init__(self, config):
            super().__init__(config)
            registered.append(self)
        def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
            used = sum(len(client.telemetry) for client in registered)
            if used >= 8:
                raise RuntimeError('Stage Word test request cap exceeded')
            attempts = min(2 if max_retries is None else max_retries, 8-used)
            print(f"{self.stage_config.stage}: {used}/8 attempts used; at most {attempts} next", file=sys.stderr, flush=True)
            return super().call_api(messages, temperature, timeout, max_retries=attempts)
    overrides = {stage: {'timeout': 120, 'max_retries': 2} for stage in ('understanding', 'editor', 'reviewer')}
    overrides['reviewer']['model'] = reviewer_model
    if understanding_model:
        overrides['understanding']['model'] = understanding_model
    return build_stage_clients({'api_key': base_client.api_key, 'base_url': base_client.base_url, 'model': base_client.model},
                               overrides, 'independent', factory=BoundedStageClient)


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--reviewer-model', default='Qwen/Qwen3.6-27B')
        parser.add_argument('--understanding-model')
        args = parser.parse_args()
        main(stage_clients_factory=lambda base: routes(base, args.reviewer_model, args.understanding_model))
    except Exception as error:
        print('Stage Word test failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
