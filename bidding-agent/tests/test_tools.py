"""工具层：结果契约、并行执行、超时与异常隔离。

`ToolRunner` 的职责不是"把函数跑起来"那么简单——它是模型与后端之间的减震器：
模型会给出不存在的工具名、非法的 JSON 参数、不存在的参数名，而后端会超时、会抛错。
这些**全部**必须转成结构化失败结果，Agent 才能继续推理（§13.2）。
"""

from __future__ import annotations

import time

import pytest

from src.clients.base_client import ToolCall
from src.tools.base import BaseTool, ToolRunner, fail, ok
from src.tools.rag_tools import KnowledgeBaseTool, _fmt_knowledge_results, get_tool_schemas

SOURCES = [
    {"question": "哪些情形可以单一来源采购？", "answer": "符合第三十一条的情形。", "score": 0.9},
    {"question": "公示期多久？", "answer": "不得少于5个工作日。", "score": 0.7},
]


def _call(name: str = "echo", arguments: str = "{}", call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


# ---- 结果构造 ----


def test_ok_result_shape():
    result = ok("文本", SOURCES)
    assert result["success"] is True
    assert result["error"] is None
    assert result["data"]["text"] == "文本"
    assert result["data"]["sources"] == SOURCES


def test_fail_result_shape_keeps_text_for_model():
    """失败也要给模型一段可读文本，否则模型面对空内容只能编。"""
    result = fail("后端不可用")
    assert result["success"] is False
    assert result["error"] == "后端不可用"
    assert "后端不可用" in result["data"]["text"]
    assert result["data"]["sources"] == []


# ---- 执行：正常与异常路径 ----


def _runner(**executors) -> ToolRunner:
    return ToolRunner(executors)


def test_run_wraps_executor_output_into_contract():
    runner = _runner(echo=lambda query, **_: (f"结果:{query}", SOURCES))
    result = runner.run(_call(arguments='{"query": "单一来源"}'))
    assert result["success"] is True
    assert result["data"]["text"] == "结果:单一来源"
    assert result["data"]["sources"] == SOURCES


def test_unknown_tool_is_reported_not_raised():
    result = _runner().run(_call(name="no_such_tool"))
    assert result["success"] is False
    assert "未知工具" in result["error"]


def test_malformed_json_arguments_are_reported():
    result = _runner(echo=lambda **_: ("x", [])).run(_call(arguments="{不是 JSON"))
    assert result["success"] is False
    assert "JSON" in result["error"]


def test_non_object_json_arguments_are_rejected():
    result = _runner(echo=lambda **_: ("x", [])).run(_call(arguments='["a", "b"]'))
    assert result["success"] is False


def test_empty_arguments_become_empty_kwargs():
    result = _runner(echo=lambda **_: ("x", [])).run(_call(arguments=""))
    assert result["success"] is True


def test_unexpected_parameter_is_reported():
    """模型经常编出不存在的参数名，这属于参数不合法而非系统故障。"""
    result = _runner(echo=lambda query: ("x", [])).run(_call(arguments='{"nope": 1}'))
    assert result["success"] is False
    assert "参数不合法" in result["error"]


def test_executor_exception_is_isolated():
    def _boom(**_) -> tuple[str, list]:
        raise RuntimeError("qdrant exploded")

    result = _runner(boom=_boom).run(_call(name="boom"))
    assert result["success"] is False
    # 用户/模型可见的表述不含原始异常与内部地址
    assert "qdrant exploded" not in result["error"]
    assert "不可用" in result["error"]


# ---- 批量并行 ----


def test_run_many_preserves_order():
    runner = _runner(echo=lambda tag, **_: (str(tag), []))
    calls = [_call(arguments=f'{{"tag": {i}}}', call_id=f"c{i}") for i in range(4)]
    results = runner.run_many(calls)
    assert [r["data"]["text"] for r in results] == ["0", "1", "2", "3"]


def test_run_many_executes_in_parallel():
    """串行执行会让多查询检索的延迟线性叠加——模型一轮发 2 个查询就是 2 倍等待。"""
    def _sleep(**_) -> tuple[str, list]:
        time.sleep(0.3)
        return "done", []

    runner = _runner(t=_sleep)
    calls = [_call(name="t", call_id=f"c{i}") for i in range(3)]

    started = time.perf_counter()
    results = runner.run_many(calls)
    elapsed = time.perf_counter() - started

    assert all(r["success"] for r in results)
    assert elapsed < 0.75, f"3 个 0.3s 的任务应并行完成，实际 {elapsed:.2f}s"


def test_run_many_isolates_failures_per_call():
    def _boom(**_) -> tuple[str, list]:
        raise RuntimeError("boom")

    runner = _runner(ok_tool=lambda **_: ("fine", []), boom=_boom)
    results = runner.run_many([_call(name="ok_tool"), _call(name="boom", call_id="c2")])

    assert results[0]["success"] is True
    assert results[1]["success"] is False


def test_run_many_empty_returns_empty():
    assert _runner().run_many([]) == []


def test_run_many_times_out_without_blocking(monkeypatch):
    """卡住的工具不能拖死整个请求：超时后立刻返回失败结果。"""
    monkeypatch.setattr(ToolRunner, "TIMEOUT_SECONDS", 0.2)

    def _hang(**_) -> tuple[str, list]:
        time.sleep(5)
        return "太晚了", []

    runner = _runner(hang=_hang)
    started = time.perf_counter()
    results = runner.run_many([_call(name="hang")])
    elapsed = time.perf_counter() - started

    assert results[0]["success"] is False
    assert "超时" in results[0]["error"]
    assert elapsed < 2, f"超时后应立即返回，实际 {elapsed:.2f}s"


# ---- 工具定义 ----


def test_knowledge_tool_schema_is_openai_compatible():
    schema = KnowledgeBaseTool().schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "search_knowledge_base"
    assert "query" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["required"] == ["query"]


def test_get_tool_schemas_exposes_registered_tools():
    names = [s["function"]["name"] for s in get_tool_schemas()]
    assert names == ["search_knowledge_base"]


def test_base_tool_requires_run():
    class _Incomplete(BaseTool):
        name = "x"

    with pytest.raises(TypeError):
        _Incomplete()  # 抽象方法未实现


# ---- 结果格式化 ----


def test_format_includes_numbered_qa_blocks():
    text = _fmt_knowledge_results("单一来源", SOURCES)
    assert "[1] 问：哪些情形可以单一来源采购？" in text
    assert "答：符合第三十一条的情形。" in text
    assert "[2]" in text


def test_format_without_hits_tells_the_model_so():
    """空结果必须显式说明，否则模型会把"没检索到"当成"可以自由发挥"。"""
    text = _fmt_knowledge_results("不存在的问题", [])
    assert "没有检索到" in text
    assert "不存在的问题" in text
