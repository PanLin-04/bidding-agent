"""API 端点：状态码约定、SSE 响应形态、限流拦截、健康检查。

流水线整体替换为 fake，不加载模型、不连 Qdrant。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.server as server
from src.rag.constants import SSE_PADDING_BYTES
from src.rate_limiter import rate_limiter

DONE_FRAME = {
    "type": "done",
    "sources": [{"question": "问", "answer": "答", "score": 0.8}],
    "web_sources": [],
    "tool_called": True,
    "tool_name": "search_knowledge_base",
    "elapsed_ms": 12,
    "phase_times": [["检索知识库", 5], ["生成回答", 7]],
}


class _FakeStore:
    def health(self) -> dict:
        return {"ok": True, "points_count": 75, "latency_ms": 4}


class _FakeEmbedder:
    model = object()


class _FakePipeline:
    """API 层只通过 agent.pipeline 访问检索侧（store / cache / 预热）。"""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.ready_error = None if ready else "词表文件不存在（请先执行 `python main.py ingest`）"
        self.store = _FakeStore()
        self.embedder = _FakeEmbedder()

    def cache_info(self) -> dict:
        return {"hits": 0, "misses": 0, "size": 0, "maxsize": 256}

    def search_cached(self, question, top_k=5):
        return DONE_FRAME["sources"]


class _FakeAgent:
    """替换 Agent 的对外面：就绪状态 + 三条出口。"""

    def __init__(self, ready: bool = True) -> None:
        self._pipeline = _FakePipeline(ready=ready)
        self.tool_schemas = [
            {"type": "function", "function": {"name": "search_knowledge_base"}}
        ]

    @property
    def pipeline(self):
        return self._pipeline

    @property
    def ready(self) -> bool:
        return self._pipeline.ready

    @property
    def ready_error(self):
        return self._pipeline.ready_error

    @property
    def llm_ready(self) -> bool:
        return True

    def chat_events(self, question, history=None, **kwargs):
        yield {"type": "status", "content": "正在初始化..."}
        yield {"type": "token", "content": "答案片段"}
        yield DONE_FRAME

    def chat(self, question, history=None):
        return {
            "answer": "答案片段",
            "sources": DONE_FRAME["sources"],
            "tool_called": True,
            "tool_name": "search_knowledge_base",
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "bidding_agent", _FakeAgent())
    # 健康检查有 30s 缓存，跨用例必须清掉，否则断言会读到上一个用例的结果
    server._health_cache["payload"] = None
    rate_limiter.reset()
    with TestClient(server.app) as test_client:
        yield test_client


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


# ---- 参数校验 ----


def test_empty_question_is_rejected_with_400(client):
    assert client.post("/api/chat/stream", json={"question": "   "}).status_code == 400
    assert client.post("/api/chat", json={"question": ""}).status_code == 400
    assert client.post("/api/ask", json={"question": ""}).status_code == 400


def test_invalid_top_k_is_rejected_with_422(client):
    """top_k 超出范围由 Pydantic 拦截（开发文档 §5 的 422 约定）。"""
    assert client.post("/api/ask", json={"question": "问题", "top_k": 999}).status_code == 422


# ---- 依赖未就绪 ----


def test_not_ready_returns_503_with_actionable_message(monkeypatch):
    monkeypatch.setattr(server, "bidding_agent", _FakeAgent(ready=False))
    rate_limiter.reset()
    with TestClient(server.app) as test_client:
        response = test_client.post("/api/chat/stream", json={"question": "问题"})
    assert response.status_code == 503
    assert "ingest" in response.json()["detail"]


# ---- SSE ----


def test_stream_returns_event_stream_with_padding_first(client):
    response = client.post("/api/chat/stream", json={"question": "问题"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # 反向代理缓冲会让流式失效，这个响应头必须存在
    assert response.headers["x-accel-buffering"] == "no"
    assert len(response.text) > SSE_PADDING_BYTES
    assert response.text.startswith(":"), "首帧必须是注释填充"


def test_stream_carries_status_token_and_done(client):
    body = client.post("/api/chat/stream", json={"question": "问题"}).text
    events = _parse_sse(body)
    assert [e["type"] for e in events] == ["status", "token", "done"]
    assert events[-1]["tool_name"] == "search_knowledge_base"


def test_stream_emits_error_frame_when_generation_blows_up(monkeypatch):
    class _BoomAgent(_FakeAgent):
        def chat_events(self, question, history=None, **kwargs):
            yield {"type": "status", "content": "正在检索知识库..."}
            raise RuntimeError("unexpected")

    monkeypatch.setattr(server, "bidding_agent", _BoomAgent())
    rate_limiter.reset()
    with TestClient(server.app) as test_client:
        events = _parse_sse(test_client.post("/api/chat/stream", json={"question": "问题"}).text)

    assert events[-1]["type"] == "error"
    assert "异常" in events[-1]["content"]


# ---- 非流式 ----


def test_non_streaming_chat_returns_answer_and_sources(client):
    payload = client.post("/api/chat", json={"question": "问题"}).json()
    assert payload["answer"] == "答案片段"
    assert payload["tool_name"] == "search_knowledge_base"


def test_ask_returns_retrieval_sources_without_llm(client):
    payload = client.post("/api/ask", json={"question": "问题", "top_k": 3}).json()
    assert payload["sources"] == DONE_FRAME["sources"]


# ---- 健康检查 ----


def test_health_reports_component_status(client):
    payload = client.get("/api/health").json()
    assert payload["ready"] is True
    assert payload["points_count"] == 75
    assert payload["latencies"]["qdrant_check_ms"] == 4
    assert "retrieval_cache" in payload


# ---- 限流 ----


def test_chat_endpoints_are_rate_limited_after_quota(client):
    # 空问题会在业务层被 400 拒绝，但中间件先计数——用最小代价打满配额
    codes = [client.post("/api/chat", json={"question": ""}).status_code for _ in range(31)]
    assert codes[:30] == [400] * 30
    assert codes[30] == 429


def test_health_endpoint_is_exempt_from_rate_limit(client):
    for _ in range(40):
        assert client.get("/api/health").status_code == 200


# ---- 启动预热 ----


class _RecordingModel:
    """访问 `.model` 即记录一次加载。

    用普通属性 + 记录对象，而不是给 `_FakePipeline` 加 property——基类 `__init__`
    已经给 `embedder` 赋过值，子类再声明成 property 会直接抛 AttributeError。
    """

    def __init__(self, name: str, log: list) -> None:
        self._name = name
        self._log = log

    @property
    def model(self):
        self._log.append(self._name)
        return object()


def _agent_with(**attrs) -> "_FakeAgent":
    agent = _FakeAgent()
    for key, value in attrs.items():
        setattr(agent._pipeline, key, value)
    return agent


def test_warmup_loads_embedding_model(monkeypatch):
    loaded: list = []
    monkeypatch.setattr(
        server, "bidding_agent", _agent_with(embedder=_RecordingModel("embedder", loaded))
    )
    server._warmup()
    assert loaded == ["embedder"], "就绪时应触发嵌入模型加载"


def test_warmup_skips_when_not_ready(monkeypatch):
    monkeypatch.setattr(server, "bidding_agent", _FakeAgent(ready=False))
    server._warmup()  # 不抛异常即为通过


def test_warmup_loads_reranker_only_when_enabled(monkeypatch):
    """精排模型 1.1GB，关了开关就不该去加载它。"""
    loaded: list = []
    monkeypatch.setattr(
        server,
        "bidding_agent",
        _agent_with(
            embedder=SimpleNamespace(model=object()),
            reranker=_RecordingModel("reranker", loaded),
        ),
    )

    monkeypatch.setattr(server.settings, "rerank_enabled", False)
    server._warmup()
    assert loaded == [], "未开启精排时不应加载模型"

    monkeypatch.setattr(server.settings, "rerank_enabled", True)
    server._warmup()
    assert loaded == ["reranker"]


def test_warmup_swallows_model_errors(monkeypatch):
    """预热失败不能把服务带崩——首次请求会重试并给出正常错误。"""

    class _Boom:
        @property
        def model(self):
            raise RuntimeError("model download failed")

    monkeypatch.setattr(
        server, "bidding_agent", _agent_with(embedder=_Boom())
    )
    server._warmup()
