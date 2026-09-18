"""SSE 帧构造（见 docs/开发文档.md §6）。

帧格式是前后端之间最细的契约：少一个空格、多一个换行都会让前端解析不到。
"""

from __future__ import annotations

import json

from api.server import _SSE_PADDING, _sse
from src.rag.constants import SSE_PADDING_BYTES


def test_frame_uses_data_prefix_and_blank_line_terminator():
    frame = _sse({"type": "token", "content": "回答"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")


def test_frame_keeps_chinese_readable():
    """ensure_ascii=False：中文不转义成 \\uXXXX，抓包与日志才看得懂。"""
    frame = _sse({"type": "token", "content": "单一来源采购"})
    assert "单一来源采购" in frame
    assert "\\u" not in frame


def test_frame_body_is_single_line_json():
    frame = _sse({"type": "done", "sources": [], "tool_name": ""})
    body = frame[len("data: ") : -2]
    assert json.loads(body) == {"type": "done", "sources": [], "tool_name": ""}
    assert "\n" not in body, "帧体必须单行，否则前端按行解析会截断"


def test_padding_frame_is_a_comment_not_an_event():
    """首帧填充必须是 SSE 注释（`:` 开头）——前端会跳过它，
    若误用 data: 前缀就会被渲染成一条空消息。"""
    assert _SSE_PADDING.startswith(":")
    assert not _SSE_PADDING.lstrip().startswith("data:")
    assert _SSE_PADDING.endswith("\n\n")


def test_padding_frame_is_large_enough_to_force_flush():
    assert len(_SSE_PADDING) >= SSE_PADDING_BYTES
