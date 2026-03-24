import openai
import json
import time
import logging
import re

class LLMClient:
    """
    通用的大模型 API 客户端，封装了 OpenAI 格式的调用。
    提供自动重试、JSON解析和错误控制。
    """
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        self.logger = logging.getLogger("LLMClient")

    def call_api(self, messages: list, temperature: float = 0.3, timeout: int = 60, max_retries: int = 3) -> str:
        """底层方法：调用 API 并返回原始内容，带异常重试。"""
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    timeout=timeout
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                self.logger.warning(f"  [API] 第{attempt}次调用失败: {e}")
                if attempt < max_retries:
                    time.sleep(2 * attempt)
        
        raise RuntimeError("大语言模型 API 经历了全部重试后依旧处于失败状态，请检查网络代理连通性，或验证 API Key、Base URL 配置是否正确！")

    def _extract_json(self, text: str) -> list:
        """从不可靠的文本中尝试提取 JSON Array"""
        if not text:
            return []
            
        text = text.strip()
        
        # 移除 markdown 代码块标记
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
            
        if text.endswith("```"):
            text = text[:-3]
            
        text = text.strip()
        
        # 如果依然无法直接解析，尝试正则提取
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    result = json.loads(m.group(0))
                    if isinstance(result, list):
                        return result
                except:
                    pass
        return []

    def call_json_api(self, messages: list, temperature: float = 0.3, timeout: int = 60, max_retries: int = 3) -> list:
        """高级方法：专门用于必须返回 JSON 数组的场景（如提取润色建议）"""
        content = self.call_api(messages, temperature, timeout, max_retries)
        return self._extract_json(content)
