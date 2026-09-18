"""工具基类与执行器（见 docs/开发文档.md §3 成员 C、§8.3）。

**结果契约（红线，勿改）**：工具执行结果统一为

    {"success": bool, "data": {...} | None, "error": str | None}

失败**不抛异常**——Agent 不能因为某个后端宕机而崩掉，它需要拿到一条可读的失败
说明，好据此决定是换工具还是如实告诉用户。完整异常只进日志，`error` 里放的是
用户/模型可见的表述，不含地址与原始堆栈（§13.2）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, ClassVar

from src.clients.base_client import ToolCall
from src.logging_config import get_logger

logger = get_logger(__name__)


def ok(text: str, sources: list[dict] | None = None, **extra: Any) -> dict:
    """构造成功结果。`text` 是给模型读的格式化文本，`sources` 供来源展示。"""
    return {
        "success": True,
        "data": {"text": text, "sources": sources or [], **extra},
        "error": None,
    }


def fail(error: str, text: str | None = None) -> dict:
    """构造失败结果。`text` 让模型知道"这一步没成"，可以继续作答而非卡死。"""
    return {
        "success": False,
        "data": {"text": text or f"（工具执行失败：{error}）", "sources": []},
        "error": error,
    }


class BaseTool(ABC):
    """工具定义：名称 / 描述 / 参数 Schema / 执行体。"""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def run(self, **kwargs: Any) -> dict:
        """执行并返回契约结果（不要抛异常，失败请用 `fail()`）。"""

    def schema(self) -> dict:
        """转成 OpenAI Function Calling 的 tools 条目。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRunner:
    """并行执行工具调用：**4 线程 / 30s 总超时 / 异常隔离**。

    为什么要并行：模型可以在一轮里发起多个互不依赖的检索（实测 DeepSeek 会对同一个
    问题给出 2 个不同措辞的查询以提升召回），串行执行会让延迟线性叠加。

    为什么要超时兜底：工具内部各有自己的网络超时（Qdrant 30s 等），但只要有一处
    没兜住就会拖死整个请求，这里再加一层总闸。
    """

    MAX_WORKERS = 4
    TIMEOUT_SECONDS = 30

    def __init__(self, executors: dict[str, Any] | None = None) -> None:
        self._executors = executors if executors is not None else {}

    def register(self, name: str, executor) -> None:
        self._executors[name] = executor

    @property
    def executors(self) -> dict[str, Any]:
        return self._executors

    def run(self, call: ToolCall) -> dict:
        """执行单个工具调用，任何异常都转成失败结果。"""
        executor = self._executors.get(call.name)
        if executor is None:
            logger.warning("模型请求了未注册的工具: %s", call.name)
            return fail(f"未知工具 {call.name}")

        try:
            arguments = _parse_arguments(call.arguments)
        except ValueError as exc:
            logger.warning("工具参数解析失败 (%s): %s", call.name, exc)
            return fail(f"工具 {call.name} 的参数不是合法 JSON")

        try:
            text, sources = executor(**arguments)
            return ok(text, sources, tool=call.name)
        except TypeError as exc:
            # 参数名/类型不匹配——多半是模型编了个不存在的参数
            logger.warning("工具 %s 参数不匹配: %s", call.name, exc)
            return fail(f"工具 {call.name} 的参数不合法")
        except Exception as exc:
            logger.exception("工具 %s 执行失败: %s", call.name, exc)
            return fail(f"工具 {call.name} 暂时不可用，请稍后重试")

    def run_many(self, calls: list[ToolCall]) -> list[dict]:
        """并行执行并**保序**返回，顺序与模型发起的顺序一致。

        即使只有一次调用也走线程池：单工具卡死是最常见的情形，为它开一条不走超时的
        快路径，等于把总超时这道闸门关掉了。
        """
        if not calls:
            return []

        results: list[dict | None] = [None] * len(calls)
        pool = ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(calls)))
        try:
            futures = [pool.submit(self.run, call) for call in calls]
            done, _pending = wait(futures, timeout=self.TIMEOUT_SECONDS)
            for index, future in enumerate(futures):
                if future in done:
                    results[index] = future.result()
                else:
                    logger.warning("工具调用超时(>%ss): %s", self.TIMEOUT_SECONDS, calls[index].name)
                    results[index] = fail("工具执行超时")
        finally:
            # wait=False：超时的线程可能还卡在网络上，不能让它拖住整个请求
            pool.shutdown(wait=False)

        return [r if r is not None else fail("工具执行异常") for r in results]


def _parse_arguments(raw: str) -> dict:
    import json

    if not raw or not raw.strip():
        return {}
    parsed = json.loads(raw)  # 非法 JSON 抛 JSONDecodeError（ValueError 子类）
    if not isinstance(parsed, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return parsed
