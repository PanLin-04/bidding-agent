"""LLM 客户端包：统一接口 + 工厂。

公共导入路径 `from src.clients import get_llm_client` 为对外契约，勿破坏。
"""

from src.clients.base_client import BaseLLMClient
from src.clients.llm_factory import get_llm_client

__all__ = ["BaseLLMClient", "get_llm_client"]
