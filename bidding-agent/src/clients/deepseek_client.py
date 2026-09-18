"""DeepSeek 客户端（OpenAI 兼容协议）。

出站统一 `trust_env=False`：不继承操作系统代理，需要时显式配置
HTTP_PROXY/HTTPS_PROXY（见 docs/开发文档.md §11.3）。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import httpx

from src.clients.base_client import BaseLLMClient, LLMResponse, ToolCall
from src.logging_config import get_logger

logger = get_logger(__name__)

# LLM 首包延迟通常远超普通 HTTP，60s 是"慢但不至于误杀"的经验值
DEFAULT_TIMEOUT = 60.0


class DeepSeekClient(BaseLLMClient):
    provider = "deepseek"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com/v1",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self._model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self.api_key)

    def supports_tools(self) -> bool:
        return True

    @property
    def client(self):
        """懒加载：未配置 key 时不应在构造期就建连接池。"""
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI

                    self._client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=self.timeout,
                        http_client=httpx.Client(trust_env=False, timeout=self.timeout),
                    )
        return self._client

    def _request(self, messages: list[dict], stream: bool, **kwargs):
        payload = {"model": self._model, "messages": messages, "temperature": 0.3}
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        return self.client.chat.completions.create(**payload, stream=stream)

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self._request(messages, stream=False, **kwargs)
        return (response.choices[0].message.content or "").strip()

    def chat_raw(
        self, messages: list[dict], tools: list[dict] | None = None, **kwargs
    ) -> LLMResponse:
        """带工具的对话。未传 tools 时退化为普通文本调用。"""
        extra = {}
        if tools:
            extra["tools"] = tools
            # 推理类模型对 tool_choice 敏感（显式传会 400），这里保持 auto 语义
            extra["tool_choice"] = kwargs.pop("tool_choice", "auto")
        response = self._request(messages, stream=False, **extra, **kwargs)
        choice = response.choices[0]
        message = choice.message

        calls: list[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            calls.append(
                ToolCall(
                    id=getattr(call, "id", "") or "",
                    name=(getattr(call.function, "name", "") or "").strip(),
                    arguments=getattr(call.function, "arguments", "") or "",
                )
            )
        return LLMResponse(
            content=(message.content or "").strip(),
            tool_calls=calls,
            finish_reason=getattr(choice, "finish_reason", "") or "",
        )

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        stream = self._request(messages, stream=True, **kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue  # 末尾的 usage-only 帧没有 choices
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content


def build_deepseek_client() -> DeepSeekClient:
    from src.config import settings

    return DeepSeekClient(
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
    )
