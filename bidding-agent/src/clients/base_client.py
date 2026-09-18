"""LLM 客户端统一接口。

新增 provider 只需实现本接口并在 `llm_factory` 注册（见 docs/开发文档.md §8.5）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """一次工具调用请求。

    刻意做成与 SDK 无关的普通数据结构：Agent 与工具层不该被 OpenAI SDK 的对象
    形状绑架——换 provider（zhipu / vLLM / Ollama）时只改客户端，其余代码不动。
    """

    id: str
    name: str
    arguments: str = ""  # 原始 JSON 字符串，解析失败由工具层兜底


@dataclass
class LLMResponse:
    """非流式回复：正文 + 工具调用 + 结束原因。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class BaseLLMClient(ABC):
    provider: str = "base"

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前生效的模型名（健康检查与日志用）。"""

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话，返回完整文本。"""

    @abstractmethod
    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式对话，逐段产出正文增量（不含思考内容）。"""

    def chat_raw(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> LLMResponse:
        """非流式对话，保留工具调用信息（Agent 读取 tool_calls 用）。

        默认实现退化为纯文本：不支持 Function Calling 的 provider（如本地 Ollama
        跑推理模型）由此自动走「无工具」路径，Agent 侧再降级为直接检索。
        """
        return LLMResponse(content=self.chat(messages, **kwargs), finish_reason="stop")

    def available(self) -> bool:
        """是否具备调用条件（凭据是否存在）。缺失时上层走降级路径。"""
        return True

    def supports_tools(self) -> bool:
        """是否支持原生 Function Calling。"""
        return False
