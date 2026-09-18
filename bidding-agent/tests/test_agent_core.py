"""Agent 编排：ReAct 多轮循环、来源合并、降级路径、事件协议。

依赖全部注入（文件内 fake 类，见 docs/开发文档.md §9.3），不连 LLM、不连向量库。
"""

from __future__ import annotations

from src.agent.constants import MAX_TOOL_ROUNDS, TOOL_NAME_KB
from src.agent.core import BiddingAgent
from src.agent.react_loop import AgentContext, merge_sources
from src.clients.base_client import LLMResponse, ToolCall
from src.tools.base import ToolRunner

TOOL = "search_knowledge_base"
HITS = [
    {"question": "哪些情形可以单一来源采购？", "answer": "第三十一条的情形。", "score": 0.9},
    {"question": "公示期多久？", "answer": "不得少于5个工作日。", "score": 0.7},
]


def _tool_reply(query: str = "单一来源", call_id: str = "call-1") -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=call_id, name=TOOL, arguments=f'{{"query": "{query}"}}')],
        finish_reason="tool_calls",
    )


class _ScriptedLLM:
    """按队列返回 chat_raw 响应；chat_stream 固定产出预设分片。"""

    def __init__(
        self,
        raw: list[LLMResponse] | None = None,
        chunks: list[str] | None = None,
        available: bool = True,
        supports_tools: bool = True,
    ) -> None:
        self._raw = list(raw or [])
        self._chunks = ["基于", "知识库", "的回答。"] if chunks is None else chunks
        self._available = available
        self._supports = supports_tools
        self.raw_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []

    def available(self) -> bool:
        return self._available

    def supports_tools(self) -> bool:
        return self._supports

    def chat_raw(self, messages, tools=None, **kwargs) -> LLMResponse:
        self.raw_calls.append([dict(m) for m in messages])
        if not self._raw:
            return LLMResponse(content="（没有更多脚本响应）", finish_reason="stop")
        return self._raw.pop(0)

    def chat_stream(self, messages, **kwargs):
        self.stream_calls.append([dict(m) for m in messages])
        yield from self._chunks


class _FakeRunner:
    def __init__(self, sources=None, text: str = "检索到的内容", fail: bool = False) -> None:
        self.sources = HITS if sources is None else sources
        self.text = text
        self.fail = fail
        self.calls: list[ToolCall] = []

    def run_many(self, calls):
        self.calls.extend(calls)
        if self.fail:
            return [
                {"success": False, "data": {"text": "（工具执行失败）", "sources": []}, "error": "boom"}
                for _ in calls
            ]
        results = []
        for _ in calls:
            results.append(
                {"success": True, "data": {"text": self.text, "sources": self.sources}, "error": None}
            )
        return results


class _StubPipeline:
    """降级路径用：直接检索。"""

    def __init__(self, sources=None, error: Exception | None = None) -> None:
        self.sources = HITS if sources is None else sources
        self.error = error
        self.calls = 0

    def search_cached(self, question, top_k=5):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.sources)

    ready = True
    ready_error = None


def _agent(llm, runner=None, pipeline=None) -> BiddingAgent:
    return BiddingAgent(
        llm=llm,
        runner=runner or _FakeRunner(),
        tool_schemas=[{"type": "function", "function": {"name": TOOL}}],
        pipeline=pipeline or _StubPipeline(),
    )


def _events(agent: BiddingAgent, question: str = "单一来源采购的条件？") -> list[dict]:
    return list(agent.chat_events(question, paced=False))


def _kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def _statuses(events: list[dict]) -> list[str]:
    return [e["content"] for e in events if e["type"] == "status"]


def _text(events: list[dict]) -> str:
    return "".join(e["content"] for e in events if e["type"] == "token")


# ---- ReAct 循环 ----


def test_agent_calls_tool_then_generates():
    llm = _ScriptedLLM(raw=[_tool_reply()])
    runner = _FakeRunner()
    events = _events(_agent(llm, runner))

    assert len(runner.calls) == 1, "Agent 应自动调用 RAG 工具"
    assert runner.calls[0].name == TOOL
    assert _text(events) == "基于知识库的回答。"


def test_status_sequence_follows_documented_react_shape():
    events = _events(_agent(_ScriptedLLM(raw=[_tool_reply()])))
    statuses = _statuses(events)
    assert statuses[0] == "正在初始化..."
    assert "正在检索与搜索..." in statuses
    assert "正在分析检索结果..." in statuses
    assert statuses[-1] == "正在生成回答..."


def test_tool_results_are_fed_back_to_the_model():
    llm = _ScriptedLLM(raw=[_tool_reply()])
    _events(_agent(llm))
    # 第二轮（生成）拿到的上下文里应含检索到的内容
    final_user = llm.stream_calls[-1][-1]["content"]
    assert "第三十一条的情形。" in final_user


def test_second_round_when_model_keeps_searching():
    llm = _ScriptedLLM(raw=[_tool_reply("甲"), _tool_reply("乙", "call-2")])
    runner = _FakeRunner()
    events = _events(_agent(llm, runner))

    assert len(runner.calls) == 2
    assert any("正在补充检索（第2轮）" in s for s in _statuses(events))


def test_tool_rounds_are_capped():
    """模型反复"再检索一次"时必须被硬上限截断，否则请求会一直转下去。"""
    llm = _ScriptedLLM(raw=[_tool_reply(f"q{i}", f"c{i}") for i in range(10)])
    runner = _FakeRunner()
    _events(_agent(llm, runner))

    assert len(runner.calls) == MAX_TOOL_ROUNDS
    assert len(llm.raw_calls) == MAX_TOOL_ROUNDS


def test_model_deciding_not_to_use_tools_answers_directly():
    """寒暄之类的问题不该白白多花一次生成调用。"""
    llm = _ScriptedLLM(raw=[LLMResponse(content="你好，请问有什么可以帮你？", finish_reason="stop")])
    runner = _FakeRunner()
    events = _events(_agent(llm, runner))

    assert runner.calls == [], "模型没要求调用工具"
    assert _text(events) == "你好，请问有什么可以帮你？"
    assert not llm.stream_calls, "无需工具时不应再走一次生成"


def test_tool_failure_still_produces_an_answer():
    """工具挂了也要给出回答——模型可以据此说明检索不可用，而不是整个请求失败。"""
    events = _events(_agent(_ScriptedLLM(raw=[_tool_reply()]), _FakeRunner(fail=True)))

    assert "error" not in _kinds(events)
    assert _text(events) == "基于知识库的回答。"
    assert events[-1]["sources"] == []


# ---- 来源合并 ----


def test_sources_are_deduped_across_rounds_keeping_best_score():
    ctx = AgentContext(question="q")
    merge_sources(ctx, [{"question": "同一条", "answer": "a", "score": 0.3}])
    merge_sources(ctx, [{"question": "同一条", "answer": "a", "score": 0.8}])
    assert len(ctx.sources) == 1
    assert ctx.sources[0]["score"] == 0.8


def test_sources_are_ordered_by_score():
    ctx = AgentContext(question="q")
    merge_sources(ctx, [{"question": "低", "score": 0.2}, {"question": "高", "score": 0.9}])
    assert [s["question"] for s in ctx.sources] == ["高", "低"]


def test_sources_merge_ignores_empty_input():
    ctx = AgentContext(question="q")
    merge_sources(ctx, [])
    assert ctx.sources == []


# ---- 降级路径 ----


def test_no_llm_credentials_falls_back_to_direct_retrieval():
    llm = _ScriptedLLM(available=False)
    pipeline = _StubPipeline()
    events = _events(_agent(llm, pipeline=pipeline))

    assert pipeline.calls == 1
    assert not llm.raw_calls, "无凭据时不该发起模型调用"
    assert _text(events) == HITS[0]["answer"], "降级应直接返回检索原文"
    assert "正在检索知识库..." in _statuses(events)


def test_provider_without_tool_support_degrades():
    """本地推理模型（如 Ollama）不走 Function Calling，直接降级——而不是发一次
    注定拿不到 tool_calls 的请求。"""
    llm = _ScriptedLLM(supports_tools=False)
    events = _events(_agent(llm))
    assert not llm.raw_calls
    assert _text(events) == HITS[0]["answer"]


def test_react_loop_exception_degrades_to_retrieval():
    class _BoomLLM(_ScriptedLLM):
        def chat_raw(self, messages, tools=None, **kwargs):
            raise RuntimeError("upstream 500")

    events = _events(_agent(_BoomLLM()))
    assert "error" not in _kinds(events)
    assert _text(events) == HITS[0]["answer"]


def test_retrieval_failure_in_degraded_path_ends_with_error():
    pipeline = _StubPipeline(error=RuntimeError("qdrant down"))
    events = _events(_agent(_ScriptedLLM(available=False), pipeline=pipeline))

    assert events[-1]["type"] == "error"
    assert not any(e["type"] == "done" for e in events)


# ---- done 帧契约 ----


def test_done_frame_keeps_contract_fields():
    done = _events(_agent(_ScriptedLLM(raw=[_tool_reply()])))[-1]
    assert set(done) == {
        "type",
        "sources",
        "web_sources",
        "tool_called",
        "tool_name",
        "elapsed_ms",
        "phase_times",
    }


def test_tool_name_is_a_single_string():
    """前端徽标与 eval 都依赖这个语义（§6.4），不能改成数组。"""
    done = _events(_agent(_ScriptedLLM(raw=[_tool_reply()])))[-1]
    assert isinstance(done["tool_name"], str)
    assert done["tool_name"] == TOOL


def test_tool_called_reflects_execution_not_result_count():
    """工具有没有执行，与检索到几条是两件事。"""
    events = _events(_agent(_ScriptedLLM(raw=[_tool_reply()]), _FakeRunner(sources=[])))
    assert events[-1]["tool_called"] is True
    assert events[-1]["sources"] == []


def test_no_tool_path_reports_empty_tool_name():
    events = _events(_agent(_ScriptedLLM(raw=[LLMResponse(content="你好")])))
    assert events[-1]["tool_called"] is False
    assert events[-1]["tool_name"] == ""


# ---- 非流式出口 ----


def test_chat_collects_the_same_event_stream():
    result = _agent(_ScriptedLLM(raw=[_tool_reply()])).chat("单一来源的条件？")
    assert result["answer"] == "基于知识库的回答。"
    assert result["tool_name"] == TOOL
    assert result["sources"] == HITS


def test_chat_reports_error_without_raising():
    result = _agent(_ScriptedLLM(available=False), pipeline=_StubPipeline(error=RuntimeError("x"))).chat(
        "任意问题"
    )
    assert "检索失败" in result.get("error", "")
    assert result["answer"] == ""


# ---- 工具文本泄漏兜底 ----


def test_leaked_tool_text_is_replaced_by_real_execution():
    """模型把调用当正文吐出来时：清屏 → 真的去执行 → 重新生成。"""
    leaked = '<tool_call>{"name": "search_knowledge_base", "arguments": {"query": "甲"}}</tool_call>'
    llm = _ScriptedLLM(raw=[_tool_reply()], chunks=[leaked, "这段不应出现"])
    # 让重生成这一路产出正常文本
    llm._chunks = [leaked, "重生成后的正常回答。"]
    runner = _FakeRunner()
    events = _events(_agent(llm, runner))

    assert {"type": "reset"} in events, "必须清掉已渲染的泄漏内容"
    assert "<tool_call>" not in _text(events)
    assert runner.calls, "泄漏出来的调用应被真正执行"


# ---- 上下文构造 ----


def test_history_is_passed_but_system_role_is_filtered():
    llm = _ScriptedLLM(raw=[_tool_reply()])
    _events(_agent(llm))
    first_call = llm.raw_calls[0]
    assert first_call[0]["role"] == "system"
    assert first_call[-1] == {"role": "user", "content": "单一来源采购的条件？"}


def test_final_generation_is_told_tools_are_unavailable():
    """根因防线：最终生成调用不带 tools 参数，若系统提示仍在强调用工具，
    模型就会改用正文输出工具语法（实测 DeepSeek 泄漏过 XML 方言）。"""
    llm = _ScriptedLLM(raw=[_tool_reply()])
    _events(_agent(llm))

    final_system = llm.stream_calls[-1][0]
    assert final_system["role"] == "system"
    assert "没有工具可用" in final_system["content"]
    # ReAct 阶段的 system 提示仍然保留在基底里
    assert "search_knowledge_base" in final_system["content"]
