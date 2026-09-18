"""工具调用文本的检测 / 解析 / 清理。

要防的场景：模型本该走原生 Function Calling，却把调用意图当正文吐了出来。
用户在回答里看到一段 JSON 是灾难性的——既暴露内部实现，也说明这轮根本没检索。
"""

from __future__ import annotations

import json

from src.agent.tool_defense import contains_tool_text, parse_tool_calls, strip_tool_text


# ---- 检测 ----


def test_detects_tool_call_tags():
    assert contains_tool_text('<tool_call>{"name": "search_knowledge_base"}</tool_call>')


def test_detects_plain_json_tool_calls_block():
    assert contains_tool_text('{"tool_calls": [{"name": "search_knowledge_base"}]}')


def test_detects_function_call_syntax():
    assert contains_tool_text('search_knowledge_base({"query": "单一来源"})')


def test_ignores_normal_answer():
    assert not contains_tool_text("单一来源采购适用于《政府采购法》第三十一条的情形。")
    assert not contains_tool_text("")


def test_detects_xml_tool_call_dialect():
    """实测 DeepSeek 用过这套 XML 方言，且外层标签带空格——只认 JSON 式会漏判。"""
    assert contains_tool_text(
        '<tool calls><invoke name="search_knowledge_base">'
        '<parameter name="query">单一来源</parameter></invoke></tool calls>'
    )
    assert contains_tool_text('<invoke name="search_knowledge_base">')


# ---- 解析 ----


def test_parse_xml_invoke_form():
    text = (
        "<tool calls>\n"
        '<invoke name="search_knowledge_base">\n'
        '<parameter name="query">单一来源采购的适用情形</parameter>\n'
        "</invoke>\n"
        "</tool calls>"
    )
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "search_knowledge_base"
    assert json.loads(calls[0].arguments) == {"query": "单一来源采购的适用情形"}


def test_parse_xml_with_multiple_parameters():
    text = (
        '<invoke name="search_knowledge_base">'
        '<parameter name="query">保证金</parameter>'
        '<parameter name="top_k">3</parameter>'
        "</invoke>"
    )
    calls = parse_tool_calls(text)
    assert json.loads(calls[0].arguments) == {"query": "保证金", "top_k": "3"}


def test_parse_xml_with_multiple_invokes():
    text = (
        '<invoke name="search_knowledge_base"><parameter name="query">甲</parameter></invoke>'
        '<invoke name="search_knowledge_base"><parameter name="query">乙</parameter></invoke>'
    )
    calls = parse_tool_calls(text)
    assert [json.loads(c.arguments)["query"] for c in calls] == ["甲", "乙"]


def test_parse_single_object_form():
    text = '<tool_call>{"name": "search_knowledge_base", "arguments": {"query": "单一来源"}}</tool_call>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "search_knowledge_base"
    assert json.loads(calls[0].arguments) == {"query": "单一来源"}


def test_parse_parameters_alias():
    """有的模型用 `parameters` 而不是 `arguments`。"""
    text = '{"name": "search_knowledge_base", "parameters": {"query": "公示期"}}'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert json.loads(calls[0].arguments) == {"query": "公示期"}


def test_parse_wrapped_tool_calls_array():
    text = (
        '{"tool_calls": ['
        '{"id": "a", "name": "search_knowledge_base", "arguments": {"query": "甲"}},'
        '{"id": "b", "name": "search_knowledge_base", "arguments": {"query": "乙"}}'
        "]}"
    )
    calls = parse_tool_calls(text)
    assert [c.id for c in calls] == ["a", "b"]
    assert [json.loads(c.arguments)["query"] for c in calls] == ["甲", "乙"]


def test_parse_nested_json_arguments_are_not_truncated():
    """参数里嵌套对象/数组时，用括号配平才不会把 JSON 截断。"""
    text = '{"name": "search_knowledge_base", "arguments": {"query": "x", "filters": {"a": [1, 2]}}}'
    calls = parse_tool_calls(text)
    assert json.loads(calls[0].arguments)["filters"] == {"a": [1, 2]}


def test_parse_string_arguments_are_passed_through():
    text = '<tool_call>{"name": "search_knowledge_base", "arguments": "{\\"query\\": \\"甲\\"}"}</tool_call>'
    calls = parse_tool_calls(text)
    assert json.loads(calls[0].arguments) == {"query": "甲"}


def test_parse_function_call_syntax_with_json():
    calls = parse_tool_calls('search_knowledge_base({"query": "投标保证金"})')
    assert calls[0].name == "search_knowledge_base"
    assert json.loads(calls[0].arguments) == {"query": "投标保证金"}


def test_parse_function_call_syntax_with_bare_string():
    calls = parse_tool_calls('search_knowledge_base("投标保证金")')
    assert json.loads(calls[0].arguments) == {"query": "投标保证金"}


def test_parse_ignores_braces_inside_strings():
    """正文里出现带花括号的字符串时不应被误当成 JSON 对象。"""
    calls = parse_tool_calls('注意：占位符形如 {"name": 这不是调用}，请忽略')
    assert calls == []


def test_parse_unparseable_text_returns_empty():
    assert parse_tool_calls("这段文字没有任何工具调用") == []
    assert parse_tool_calls("") == []


def test_parse_unknown_tool_name_is_still_reported():
    """解析层不判合法性——未知工具名留给 ToolRunner 报"未知工具"。"""
    calls = parse_tool_calls('{"name": "made_up_tool", "arguments": {}}')
    assert calls[0].name == "made_up_tool"


# ---- 清理 ----


def test_strip_removes_tool_call_block_keeps_text():
    text = '好的。<tool_call>{"name": "search_knowledge_base"}</tool_call>根据规定……'
    cleaned = strip_tool_text(text)
    assert "<tool_call>" not in cleaned
    assert "search_knowledge_base" not in cleaned
    assert "根据规定" in cleaned


def test_strip_handles_unclosed_block():
    """流式输出被截断时标签可能没有闭合。"""
    cleaned = strip_tool_text('<tool_call>{"name": "search_knowledge_ba')
    assert "search_knowledge_ba" not in cleaned


def test_strip_removes_xml_invoke_block():
    text = (
        "根据规定……\n"
        '<tool calls><invoke name="search_knowledge_base">'
        '<parameter name="query">单一来源</parameter></invoke></tool calls>'
    )
    cleaned = strip_tool_text(text)
    assert "<invoke" not in cleaned
    assert "search_knowledge_base" not in cleaned
    assert "根据规定" in cleaned


def test_strip_leaves_normal_text_untouched():
    assert strip_tool_text("正常回答内容。") == "正常回答内容。"
