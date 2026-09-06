"""Stage routing with explicit endpoint/key binding and secret-free cache identity."""
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from engine.llm_client import LLMClient


DEFAULTS = {'understanding': (0.4, 180), 'editor': (0.1, 60), 'reviewer': (0.1, 60)}


def normalized_endpoint(value):
    if not isinstance(value, str):
        raise ValueError('API URL must be a string')
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API URL must be an http(s) endpoint without embedded credentials, query or fragment')
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), '', ''))


@dataclass(frozen=True)
class StageConfig:
    stage: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    temperature: float = 0.1
    timeout: int = 60
    max_retries: int = 3

    def public(self):
        return {name: getattr(self, name) for name in ('stage', 'base_url', 'model', 'temperature', 'timeout', 'max_retries')}


def resolve_configs(base, overrides=None, review_mode='same'):
    if review_mode not in ('off', 'same', 'independent'):
        raise ValueError('Unknown review mode')
    overrides = overrides or {}
    base_url = normalized_endpoint(base['base_url'])
    configs = {}
    for stage in DEFAULTS:
        options = overrides.get(stage, {})
        if stage == 'reviewer' and review_mode == 'off':
            options = {}
        if stage == 'reviewer' and review_mode in ('same', 'off'):
            # Same model means reuse the editor's endpoint and its bound credential.
            inherited = configs['editor']
            endpoint, model, key = inherited.base_url, inherited.model, inherited.api_key
        else:
            endpoint = normalized_endpoint(options.get('base_url') or base_url)
            model = options.get('model') or base['model']
            if not isinstance(model, str):
                raise ValueError(f'{stage}: model must be a string')
            model = model.strip()
            key = options.get('api_key') or (base.get('api_key', '') if endpoint == base_url else '')
        if not model or not isinstance(key, str) or not key.strip():
            raise ValueError(f'{stage}: model and endpoint-bound API key are required; a different endpoint never inherits the main key')
        temperature = options.get('temperature', DEFAULTS[stage][0])
        timeout = options.get('timeout', DEFAULTS[stage][1])
        retries = options.get('max_retries', 3)
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not 0 <= temperature <= 2 or not math.isfinite(temperature)
                or type(timeout) is not int or not 1 <= timeout <= 600
                or type(retries) is not int or not 1 <= retries <= 5):
            raise ValueError(f'{stage}: invalid temperature, timeout or retry limit')
        configs[stage] = StageConfig(stage, endpoint, model, key, temperature, timeout, retries)
    editor, reviewer = configs['editor'], configs['reviewer']
    if review_mode == 'independent' and (editor.base_url, editor.model) == (reviewer.base_url, reviewer.model):
        raise ValueError('Independent review requires a different model or endpoint')
    return configs


class StageClient(LLMClient):
    def __init__(self, config):
        self.stage_config = config
        super().__init__(config.api_key, config.base_url, config.model)

    def call_api(self, messages, temperature=0.1, timeout=60, max_retries=None):
        cfg = self.stage_config
        # Respect an explicit one-attempt format-repair call; do not add retries.
        retries = cfg.max_retries if max_retries is None else min(max_retries, cfg.max_retries)
        return super().call_api(messages, cfg.temperature, cfg.timeout, retries)


def build_stage_clients(base, overrides=None, review_mode='same', factory=StageClient):
    configs = resolve_configs(base, overrides, review_mode)  # validate all before constructing clients
    return {stage: factory(config) for stage, config in configs.items()}


def client_identity(client, stage):
    if isinstance(client, StageClient):
        return client.stage_config.public()
    return {'stage': stage, 'base_url': getattr(client, 'base_url', ''),
            'model': getattr(client, 'model', ''), 'temperature': DEFAULTS[stage][0], 'timeout': DEFAULTS[stage][1]}


def persistable_overrides(overrides):
    """Stage credentials are session-only, including when saving inactive profiles."""
    allowed = ('base_url', 'model', 'temperature', 'timeout', 'max_retries')
    return {stage: {key: value for key, value in values.items() if key in allowed}
            for stage, values in overrides.items() if stage in DEFAULTS}
