"""Agent 层常量（见 docs/开发文档.md §8.2）。

两处刻意保留的复用而非重定义：`MAX_HISTORY_ROUNDS` 与 `TOOL_NAME_KB` 的真实定义在
`src/rag/constants.py`（RAG 侧本就在用），这里重新导出是为了让按文档来 `src/agent/
constants.py` 找常量的人能直接找到——两处各写一份数字，迟早会漂移。
"""

from __future__ import annotations

from src.rag.constants import (
    MAX_HISTORY_ROUNDS,
    STREAM_CHUNK_CHARS,
    STREAM_CHUNK_DELAY,
    TOOL_NAME_KB,
)

__all__ = [
    "MAX_HISTORY_ROUNDS",
    "MAX_TOOL_ROUNDS",
    "SOURCE_LIMIT",
    "STREAM_CHUNK_CHARS",
    "STREAM_CHUNK_DELAY",
    "STREAM_FLUSH_CHARS",
    "TOOL_NAME_KB",
]

# ReAct 工具轮次上限（不含最终生成回合）。
# 上限存在的意义不是省钱，而是**防死循环**：模型偶尔会反复"再检索一次"，
# 没有硬上限时这个请求会一直转下去。
MAX_TOOL_ROUNDS = 4

# 流式防泄漏缓冲阈值：先攒够这么多字符再决定是否放行。
# 工具调用文本（<tool_call>…）总是出现在回答开头，攒一小段就能判定；
# 缓冲太长会让首字延迟肉眼可见，80 是"判得出"与"等得起"之间的折中。
STREAM_FLUSH_CHARS = 80

# 多轮检索合并后的来源上限：跨轮去重后仍可能堆到十几条，
# 全塞给模型既稀释注意力、也让来源卡片过长
SOURCE_LIMIT = 8
