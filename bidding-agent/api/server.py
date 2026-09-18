"""FastAPI 服务：问答端点 + SSE 流式 + 限流 + 健康检查。

**端点全部用同步 `def`**：FastAPI 会把同步端点丢进线程池执行，LLM 生成动辄
数十秒也不会阻塞事件循环——限流中间件与健康检查在任何时候都还能响应。

错误码约定（见 docs/开发文档.md §5）：400 参数问题 / 422 校验失败 /
429 限流 / 500 处理异常 / 503 依赖未就绪。
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.agent import bidding_agent
from src.agent.utils import sse_frame as _sse
from src.config import settings
from src.logging_config import get_logger, setup_logging
from src.rag.constants import DEFAULT_TOP_K, SSE_PADDING_BYTES
from src.rag.pipeline import RAGPipeline
from src.rate_limiter import rate_limiter

logger = get_logger(__name__)

# 首帧填充：部分反向代理与浏览器缓冲会攒够一定字节才下发，先塞一段 SSE
# 注释（`:` 开头即为注释）强制立刻 flush，否则"流式"会变成"整段一次蹦出来"
_SSE_PADDING = ": " + " " * max(0, SSE_PADDING_BYTES - 3) + "\n\n"


class ChatRequest(BaseModel):
    question: str = Field(default="", description="用户问题")
    history: list[dict] | None = Field(default=None, description="[{role, content}]")


class AskRequest(BaseModel):
    question: str = Field(default="")
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=20)


def _require_ready() -> JSONResponse | None:
    if bidding_agent.ready:
        return None
    return JSONResponse(
        status_code=503,
        content={
            "detail": "知识库未就绪：缺少词表文件，请先执行 `python main.py ingest` 导入知识库。"
        },
    )


# ---- 健康检查缓存（30s）----
_health_cache: dict = {"at": 0.0, "payload": None}
_health_lock = threading.Lock()
HEALTH_TTL = 30


def _collect_health() -> dict:
    agent = bidding_agent
    qdrant = agent.pipeline.store.health()
    return {
        "ready": agent.ready,
        "agent_ready": agent.llm_ready,
        "llm_available": settings.llm_available,
        "llm_model": settings.deepseek_model if settings.llm_available else None,
        "collection": settings.qdrant_collection,
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "points_count": qdrant["points_count"],
        "tools": [schema["function"]["name"] for schema in agent.tool_schemas],
        "latencies": {"qdrant_check_ms": qdrant["latency_ms"]},
        "retrieval_cache": agent.pipeline.cache_info(),
    }


def _warmup() -> None:
    """后台预热检索链路用到的模型。

    BGE 首次加载要数十秒（首次运行还含下载），精排模型也要十几秒；若发生在请求
    路径里，第一个提问的用户会以为服务挂了。预热失败不影响服务——首次请求会再试
    一次并正常报错（精排失败还会退回混合检索顺序）。
    """
    try:
        pipeline = bidding_agent.pipeline
        if not pipeline.ready:
            logger.warning("跳过预热: %s", pipeline.ready_error)
            return
        pipeline.embedder.model  # 触发懒加载
        logger.info("嵌入模型预热完成")
        if settings.rerank_enabled:
            pipeline.reranker.model  # noqa: B018
            logger.info("精排模型预热完成")
    except Exception as exc:
        logger.warning("模型预热失败（首次请求会重试）: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时的配置体检、日志初始化与后台预热。

    缺少必需配置**不阻止**服务启动：起得来，`/api/health` 才有机会把"缺什么"
    告诉调用方，前端也能给出可读提示而不是"连接失败"。
    """
    setup_logging()
    if settings.missing_required:
        logger.error("缺少必需配置: %s（检索功能不可用）", ", ".join(settings.missing_required))
    logger.info(
        "服务启动: collection=%s, provider=%s, llm_available=%s",
        settings.qdrant_collection,
        settings.llm_provider,
        settings.llm_available,
    )
    if settings.warmup_on_start:
        # 后台线程而非阻塞启动：预热期间服务即可接受健康检查与请求
        threading.Thread(target=_warmup, name="model-warmup", daemon=True).start()
    yield


app = FastAPI(title="招投标采购智能问答 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """仅对 /api/chat* 限流：健康检查与管理端点不应被配额拖累。"""
    if request.url.path.startswith("/api/chat"):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(client_ip):
            wait = rate_limiter.retry_after(client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁，请 {wait} 秒后重试。"},
                headers={"Retry-After": str(wait)},
            )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict:
    """各组件就绪状态与探测延迟（30s 缓存，见 §3.3）。"""
    now = time.time()
    with _health_lock:
        if _health_cache["payload"] is None or now - _health_cache["at"] > HEALTH_TTL:
            _health_cache["payload"] = _collect_health()
            _health_cache["at"] = now
        return _health_cache["payload"]


@app.post("/api/chat/stream")
def chat_stream(payload: ChatRequest):
    """SSE 流式问答（推荐路径）。事件协议见 docs/开发文档.md §6。"""
    question = (payload.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"detail": "问题不能为空。"})

    not_ready = _require_ready()
    if not_ready is not None:
        return not_ready

    def event_stream():
        yield _SSE_PADDING
        try:
            for event in bidding_agent.chat_events(question, payload.history):
                yield _sse(event)
        except Exception as exc:
            # 生成器内部异常无法再改状态码，只能以 error 帧收尾，让前端有明确提示
            logger.exception("SSE 流处理异常: %s", exc)
            yield _sse({"type": "error", "content": "服务处理异常，请稍后重试。"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 显式告知 Nginx 不要缓冲本响应，否则流式失效（§11.2）
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat")
def chat(payload: ChatRequest):
    """非流式问答：与流式共用同一份事件流（§8.1）。"""
    question = (payload.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"detail": "问题不能为空。"})

    not_ready = _require_ready()
    if not_ready is not None:
        return not_ready

    result = bidding_agent.chat(question, payload.history)
    if result.get("error"):
        return JSONResponse(status_code=500, content={"detail": result["error"]})
    return {
        "answer": result["answer"],
        "sources": result["sources"],
        "tool_called": result["tool_called"],
        "tool_name": result["tool_name"],
        "elapsed_ms": result.get("elapsed_ms", 0),
    }


@app.post("/api/ask")
def ask(payload: AskRequest):
    """直接走检索，不调用大模型：返回最匹配的问答原文与来源。"""
    question = (payload.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"detail": "问题不能为空。"})

    not_ready = _require_ready()
    if not_ready is not None:
        return not_ready

    try:
        sources = bidding_agent.pipeline.search_cached(question, payload.top_k)
    except Exception as exc:
        logger.exception("检索失败: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "检索失败，请稍后重试。"})

    # 该端点不经过 Agent、不调用大模型：回答即最匹配的问答原文
    return {"answer": RAGPipeline.fallback_answer(sources, question), "sources": sources}
