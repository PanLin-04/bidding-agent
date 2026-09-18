"""Agent 层通用工具函数（见 docs/开发文档.md §1 的 agent/utils.py）。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from src.agent.constants import MAX_HISTORY_ROUNDS, STREAM_CHUNK_CHARS, STREAM_CHUNK_DELAY


def sse_frame(event: dict) -> str:
    """单条事件序列化为 SSE 帧（保留中文，不转义为 \\uXXXX）。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def truncate_history(history: list[dict] | None) -> list[dict]:
    """截断对话历史并过滤非法角色。

    只保留最近 N 轮：更早的对话对当前问题的边际价值低，却会挤占上下文窗口并
    拉高每次调用的成本。角色白名单也顺带堵住了"前端传 role=system 往提示词里
    塞额外指令"这条路——历史是纯数据，不是指令来源。
    """
    if not history:
        return []
    recent = history[-(MAX_HISTORY_ROUNDS * 2) :]
    messages = []
    for item in recent:
        role = (item or {}).get("role")
        content = ((item or {}).get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


def pace_text(text: str, paced: bool = True) -> Iterator[str]:
    """把整段文本切成小帧，让前端渲染节奏平滑。"""
    for start in range(0, len(text), STREAM_CHUNK_CHARS):
        yield text[start : start + STREAM_CHUNK_CHARS]
        if paced and STREAM_CHUNK_DELAY:
            time.sleep(STREAM_CHUNK_DELAY)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
