"""Explicit synthetic-only SiliconFlow protocol test; credential comes from stdin."""
import json
import logging
import sys
from dataclasses import asdict
from engine.llm_client import LLMClient
from engine.revision_contract import validate_review
from engine.validation import Validator


class BoundedClient(LLMClient):
    calls = 0

    def call_api(self, messages, temperature=0.1, timeout=60, max_retries=3):
        if self.calls >= 6:
            raise RuntimeError('Protocol test request limit')
        self.calls += 1
        response = super().call_api(messages, temperature, timeout=60, max_retries=1)
        print(json.dumps({'request': self.calls, 'response': response}, ensure_ascii=False), file=sys.stderr)
        return response


def main():
    client = BoundedClient(sys.stdin.read().strip(), 'https://api.siliconflow.cn/v1', 'deepseek-ai/DeepSeek-V4-Flash')
    samples = {'P1:S1': 'The results shows 0.021 W m−1 K−1.',
               'P2:S1': 'Fig. 2 shows the APTES samples.',
               'P3:S1': '实验结果表明了该方法是有效的。'}
    proposed = client.call_sentence_api([{'role': 'user', 'content':
        'Conservatively fix clear grammar/wordiness only. Preserve all numbers, units, terms and meaning. ' + json.dumps(samples, ensure_ascii=False)}], list(samples))
    edits = [d for d in proposed if d.decision == 'edit']
    reviewed = client.call_sentence_api([{'role': 'user', 'content':
        'Review only these proposed edits. Keep decision rejects; edit retains the EXACT proposed revised_sentence. '
        + json.dumps({'source': samples, 'proposals': [asdict(d) for d in edits]}, ensure_ascii=False)}], [d.sentence_id for d in edits]) if edits else []
    validate_review(edits, reviewed)
    for decision in reviewed:
        if decision.decision == 'edit':
            assert not Validator(['APTES']).validate(samples[decision.sentence_id], decision.revised_sentence)
    print(json.dumps({'calls': client.calls, 'contract': 'PASS', 'review_subset': 'PASS',
                      'decisions': [asdict(d) for d in proposed]}, ensure_ascii=False))


if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    try:
        main()
    except Exception as error:
        from engine.llm_client import ModelFormatError
        print('Protocol test failed: ' + type(error).__name__ + (': ' + str(error) if isinstance(error, ModelFormatError) else ''), file=sys.stderr)
        sys.exit(1)
