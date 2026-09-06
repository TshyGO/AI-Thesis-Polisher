import openai
import json
import time
import logging


class ModelError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class ModelFormatError(ModelError):
    def __init__(self, message="模型输出不符合修改建议数组格式"):
        super().__init__("MODEL_FORMAT_ERROR", message)

class LLMClient:
    """
    通用的大模型 API 客户端，封装了 OpenAI 格式的调用。
    提供自动重试、JSON解析和错误控制。
    """
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        # One retry owner: the loop below, not a second SDK retry loop.
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, max_retries=0)
        self.logger = logging.getLogger("LLMClient")
        self.telemetry = []

    def call_api(self, messages: list, temperature: float = 0.3, timeout: int = 60, max_retries: int = 3) -> str:
        """底层方法：调用 API 并返回原始内容，带异常重试。"""
        if max_retries < 1:
            raise ValueError("max_retries must be positive")
        for attempt in range(1, max_retries + 1):
            started = time.monotonic()
            response = None
            status = 'OK'
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    timeout=timeout
                )
                if not response.choices or response.choices[0].finish_reason != "stop":
                    raise ModelFormatError("模型输出未正常结束（截断或拒绝）")
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ModelFormatError("模型返回空内容")
                return content.strip()
            except ModelFormatError:
                status = 'MODEL_FORMAT_ERROR'
                raise
            except Exception as e:
                status = 'MODEL_TIMEOUT' if isinstance(e, openai.APITimeoutError) else 'MODEL_HTTP_ERROR'
                last_error = e
                self.logger.warning("[API] 第%s次调用失败 (%s)", attempt, type(e).__name__)
                if attempt < max_retries:
                    time.sleep(2 * attempt)
            finally:
                usage = getattr(response, 'usage', None)
                tokens = {name: getattr(usage, name, None) for name in ('prompt_tokens', 'completion_tokens')}
                if not hasattr(self, 'telemetry'):
                    self.telemetry = []
                self.telemetry.append({'status': status, 'elapsed_seconds': round(time.monotonic()-started, 3),
                                       **{name: value if type(value) is int else None for name, value in tokens.items()}})
        
        status = "MODEL_TIMEOUT" if isinstance(last_error, openai.APITimeoutError) else "MODEL_HTTP_ERROR"
        raise ModelError(status, "模型请求失败，请检查连接与模型配置") from last_error

    def _extract_json(self, text: str) -> list:
        """从不可靠的文本中尝试提取 JSON Array"""
        if not text:
            raise ModelFormatError()
            
        text = text.strip()
        
        # 移除 markdown 代码块标记
        if text.startswith("```"):
            if not text.endswith("```") or text.count("```") != 2:
                raise ModelFormatError()
            if text.startswith("```json"):
                text = text[7:-3]
            else:
                text = text[3:-3]
            
        text = text.strip()
        
        # 仅接受完整 JSON 或完整代码围栏，禁止从错误响应中捞出 []。
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            raise ModelFormatError() from None
        if not isinstance(result, list):
            raise ModelFormatError()
        for item in result:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(key), str) for key in ("old", "new")
            ) or not item["old"]:
                raise ModelFormatError()
            if any(key in item and not isinstance(item[key], str)
                   for key in ("sentence_id", "reason", "priority")):
                raise ModelFormatError()
        return result

    @staticmethod
    def _with_contract(messages, contract):
        # Some compatible providers reject multiple system messages. Preserve all
        # instructions in one leading message instead of silently dropping one.
        systems = [contract] + [message['content'] for message in messages if message['role'] == 'system']
        return [{'role': 'system', 'content': '\n\n'.join(systems)}] + [
            dict(message) for message in messages if message['role'] != 'system']

    def call_json_api(self, messages: list, temperature: float = 0.3, timeout: int = 60, max_retries: int = 3) -> list:
        """高级方法：专门用于必须返回 JSON 数组的场景（如提取润色建议）"""
        contract = (
            "Return ONLY one valid JSON array. No explanations, markdown, or thinking tags. "
            "For no changes return exactly []. Each edit needs string old and new fields; "
            "new may be empty for a deletion. Do not return an unchanged edit to indicate KEEP."
        )
        request = self._with_contract(messages, contract)
        content = self.call_api(request, temperature, timeout, max_retries)
        try:
            return self._extract_json(content)
        except ModelFormatError:
            # One explicit repair request, never silently extract [] from broken prose.
            repaired = self.call_api(request + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Your previous response violated the JSON contract. " + contract},
            ], temperature, timeout, max_retries=1)
            return self._extract_json(repaired)

    def call_sentence_api(self, messages, expected_ids, temperature=0.1, timeout=60, validator=None):
        """Full-sentence protocol; local strict validation with one format retry."""
        from engine.revision_contract import SENTENCE_CONTRACT, parse_decisions
        expected_ids = list(expected_ids)
        contract = SENTENCE_CONTRACT + "\nOutput IDs must be EXACTLY: " + json.dumps(expected_ids)
        request = self._with_contract(messages, contract)
        def parse_result(content):
            decisions = parse_decisions(content, expected_ids)
            if validator is not None:
                validator(decisions)
            return decisions
        content = self.call_api(request, temperature=temperature, timeout=timeout)
        try:
            return parse_result(content)
        except ModelFormatError as error:
            content = self.call_api(request + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Invalid contract: " + str(error) + ". Return exactly the required sentence decisions. " + contract},
            ], temperature=temperature, timeout=timeout, max_retries=1)
            return parse_result(content)

    def call_triage_api(self, messages, expected_ids):
        """Screening verdicts only. One explicit repair, then fail closed."""
        from engine.revision_contract import TRIAGE_CONTRACT, parse_triage
        expected_ids = list(expected_ids)
        contract = TRIAGE_CONTRACT + "\nOutput IDs must be EXACTLY: " + json.dumps(expected_ids)
        request = self._with_contract(messages, contract)
        content = self.call_api(request, temperature=0.1, timeout=60)
        try:
            return parse_triage(content, expected_ids)
        except ModelFormatError as error:
            content = self.call_api(request + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Invalid triage contract: " + str(error) + ". " + contract},
            ], temperature=0.1, timeout=60, max_retries=1)
            return parse_triage(content, expected_ids)

    def call_memory_api(self, messages, sources):
        """Return (selection, rejections). One repair request, never two."""
        from engine.chapter_memory import CONTRACT, parse_selection
        request = self._with_contract(messages, CONTRACT)
        content = self.call_api(request, temperature=0.1, timeout=180)
        first = complaint = None
        try:
            first = parse_selection(content, sources, salvage=True)
        except ModelFormatError as error:
            complaint = str(error)
        else:
            if not first[1]:
                return first
            complaint = first[1][0]['reason']
        repaired = self.call_api(request + [
            {'role': 'assistant', 'content': content},
            {'role': 'user', 'content': 'Invalid source selection: ' + complaint + '. ' + CONTRACT},
        ], temperature=0.1, timeout=180, max_retries=1)
        try:
            second = parse_selection(repaired, sources, salvage=True)
        except ModelFormatError:
            if first is None:
                raise
            return first  # a broken repair never discards an already grounded selection
        if first is None:
            return second
        kept = lambda result: len(result[0]['terms']) + len(result[0]['facts'])
        # An empty selection is contract-valid, so a repair can answer with one and
        # score zero rejections. Never let that erase selections already grounded.
        if kept(second) == 0 and kept(first) > 0:
            return first
        return second if len(second[1]) <= len(first[1]) else first
