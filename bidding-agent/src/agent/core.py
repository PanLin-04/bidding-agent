"""BiddingAgent：招投标问答 Agent 主体。

**`chat_events` 是唯一的编排入口**（见 docs/开发文档.md §8.1）——SSE 流式与非流式
两条出口都消费同一份事件流，新增逻辑只改这一处，两条路径自动同构。

一次问答的流程：

    正在初始化 → [ReAct 工具轮：检索与搜索 → 分析检索结果 →（补充检索）× ≤4]
                → 正在生成回答 → done

模型判断无需工具时跳过工具轮；无 LLM 凭据或工具轮抛异常时，降级为"直接检索 +
返回原文"，与 RAG 流水线的降级行为保持一致。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

from src.agent.constants import TOOL_NAME_KB
from src.agent.generation import GenerationMixin
from src.agent.react_loop import AgentContext, ReActMixin
from src.agent.tool_defense import ToolDefenseMixin
from src.agent.utils import elapsed_ms, sse_frame
from src.clients.llm_factory import get_llm_client
from src.config import settings
from src.logging_config import get_logger
from src.rag.constants import DEFAULT_TOP_K
from src.rag.pipeline import rag_pipeline
from src.tools.base import ToolRunner
from src.tools.rag_tools import TOOL_EXECUTORS, get_tool_schemas  # noqa: F401 - 文档约定的补丁点之一

logger = get_logger(__name__)


class BiddingAgent(ReActMixin, GenerationMixin, ToolDefenseMixin):
    """依赖可注入，便于测试替换（`llm` / `runner` / `tool_schemas`）。"""

    def __init__(self, *, llm=None, runner=None, tool_schemas=None, pipeline=None) -> None:
        self._llm = llm
        self._runner = runner
        self._tool_schemas = tool_schemas
        self._pipeline = pipeline
        self._lock = threading.Lock()

    # ---- 依赖懒加载 ----

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm_client(settings.llm_provider)
        return self._llm

    @property
    def runner(self) -> ToolRunner:
        if self._runner is None:
            with self._lock:
                if self._runner is None:
                    self._runner = ToolRunner(TOOL_EXECUTORS)
        return self._runner

    @property
    def tool_schemas(self) -> list[dict]:
        if self._tool_schemas is None:
            self._tool_schemas = get_tool_schemas()
        return self._tool_schemas

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = rag_pipeline
        return self._pipeline

    @property
    def ready(self) -> bool:
        return self.pipeline.ready

    @property
    def ready_error(self) -> str | None:
        return self.pipeline.ready_error

    @property
    def llm_ready(self) -> bool:
        """是否具备跑 Agent 循环的条件（凭据 + 原生工具调用支持）。"""
        return self._llm_usable()

    def _llm_usable(self) -> bool:
        """能否跑 Agent 循环：既要凭据，也要模型支持原生 Function Calling。

        不支持工具调用的 provider（如 Ollama 跑推理模型）直接走降级路径，
        而不是发一次注定拿不到 tool_calls 的请求。
        """
        try:
            return self.llm.available() and self.llm.supports_tools()
        except Exception as exc:
            logger.warning("LLM 可用性检查失败: %s", exc)
            return False

    # ---- 降级路径 ----

    def _retrieval_events(self, ctx: AgentContext) -> Iterator[dict]:
        """直接检索（不经过模型）：无 LLM 或工具轮失败时的兜底。"""
        yield {"type": "status", "content": "正在检索知识库..."}
        started = time.perf_counter()
        try:
            ctx.sources = self.pipeline.search_cached(ctx.question, ctx.top_k)
        except Exception as exc:
            # 检索失败是致命路径：没有来源就无从作答，直接以 error 收尾。
            # 完整异常只进日志，用户侧只看到可操作的提示（§13.2）
            logger.exception("知识库检索失败: %s", exc)
            yield {
                "type": "error",
                "content": "知识库检索失败，请稍后重试；若持续失败请联系管理员。",
            }
            ctx.errored = True
            return
        ctx.phase_times.append(["检索知识库", elapsed_ms(started)])
        ctx.tool_names.append(TOOL_NAME_KB)
        ctx.tool_ran = True

    # ---- 编排入口 ----

    def chat_events(
        self,
        question: str,
        history: list[dict] | None = None,
        top_k: int = DEFAULT_TOP_K,
        paced: bool = True,
    ) -> Iterator[dict]:
        """产出协议事件（status / token / reset / done / error）。

        `paced=False` 关闭流式小帧的帧间延时——非流式调用与测试都不需要它。
        """
        started = time.perf_counter()
        ctx = AgentContext(question=question, history=list(history or []), top_k=top_k)
        yield {"type": "status", "content": "正在初始化..."}

        if self._llm_usable():
            try:
                yield from self._react_events(ctx)
            except Exception as exc:
                # 工具轮失败不该让整个问答失败：退回直接检索，用户仍拿得到答案
                logger.warning("工具轮异常(%s)，降级为直接检索", exc)
                ctx.degraded = True
                ctx.messages = []
                ctx.tool_names = []
        else:
            ctx.degraded = True

        if ctx.degraded:
            yield from self._retrieval_events(ctx)
            if ctx.errored:
                return
            yield from self._fallback_events(ctx, paced)
        else:
            yield from self._generate_events(ctx, paced)

        yield {
            "type": "done",
            "sources": ctx.sources,
            "web_sources": [],
            "tool_called": ctx.tool_ran,
            "tool_name": ctx.last_tool_name,
            "elapsed_ms": elapsed_ms(started),
            "phase_times": ctx.phase_times,
        }

    def chat(
        self, question: str, history: list[dict] | None = None, top_k: int = DEFAULT_TOP_K
    ) -> dict:
        """非流式出口：消费同一份事件流，收敛为结果字典（评测脚本用）。"""
        answer_parts: list[str] = []
        result: dict = {"answer": "", "sources": [], "tool_called": False, "tool_name": ""}
        for event in self.chat_events(question, history, top_k, paced=False):
            kind = event["type"]
            if kind == "token":
                answer_parts.append(event["content"])
            elif kind == "reset":
                answer_parts.clear()
            elif kind == "done":
                result.update(
                    {
                        "sources": event["sources"],
                        "tool_called": event["tool_called"],
                        "tool_name": event["tool_name"],
                        "elapsed_ms": event["elapsed_ms"],
                        "phase_times": event["phase_times"],
                    }
                )
            elif kind == "error":
                result["error"] = event["content"]
        result["answer"] = "".join(answer_parts).strip()
        return result

    def chat_stream(
        self, question: str, history: list[dict] | None = None, top_k: int = DEFAULT_TOP_K
    ) -> Iterator[str]:
        """SSE 出口：把同一份事件流序列化为帧字符串。"""
        for event in self.chat_events(question, history, top_k):
            yield sse_frame(event)


# ---- 模块级单例（与 settings 同风格，便于测试整体替换）----
bidding_agent = BiddingAgent()
