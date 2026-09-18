"""LLM 客户端工厂：按 provider 返回**进程内单例**。

单例的意义不只是省内存——OpenAI SDK 内部维护连接池，重复构造会让每次
请求都新建 TCP/TLS 连接，在高频问答下明显拖慢首包。
"""

from __future__ import annotations

import threading

from src.clients.base_client import BaseLLMClient
from src.config import settings
from src.logging_config import get_logger

logger = get_logger(__name__)

_clients: dict[str, BaseLLMClient] = {}
_lock = threading.Lock()


def get_llm_client(provider: str | None = None) -> BaseLLMClient:
    """取客户端；`provider=None` 时用配置里的默认 provider。"""
    name = (provider or settings.llm_provider or "deepseek").strip().lower()

    if name == "deepseek":
        from src.clients.deepseek_client import build_deepseek_client

        builder = build_deepseek_client
    else:
        raise ValueError(f"未知的 LLM provider: {name}（当前支持: deepseek）")

    if name not in _clients:
        with _lock:
            if name not in _clients:
                _clients[name] = builder()
                logger.info("已创建 LLM 客户端: %s", name)
    return _clients[name]


def reset_clients() -> None:
    """清空单例缓存（测试用：便于切换配置后重新构造）。"""
    with _lock:
        _clients.clear()
