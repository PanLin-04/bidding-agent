"""工具层：工具定义、注册表与执行器。

`from src.tools import BaseTool, ToolRunner, TOOL_EXECUTORS` 为对外契约
（Agent 与测试依赖），勿破坏（见 docs/开发文档.md §13.3）。
"""

from src.tools.base import BaseTool, ToolRunner, fail, ok
from src.tools.rag_tools import ALL_TOOLS, TOOL_EXECUTORS, KnowledgeBaseTool, get_tool_schemas

__all__ = [
    "ALL_TOOLS",
    "TOOL_EXECUTORS",
    "BaseTool",
    "KnowledgeBaseTool",
    "ToolRunner",
    "fail",
    "get_tool_schemas",
    "ok",
]
