"""ReAct 多轮工具循环（ReActMixin）+ 跨轮来源合并。

循环形状：**问模型 → 有工具调用就执行、把结果喂回去 → 再问**，直到模型不再要求
调用工具、或达到 `MAX_TOOL_ROUNDS` 上限。上限是防死循环的硬闸：模型偶尔会反复
"再检索一次"，没有上限时这个请求会一直转下去。

`TOOL_EXECUTORS` 在本模块与 `src/agent/core.py` 各有一处模块级绑定（后者构造
runner、这里做文本兜底过滤），**测试里打补丁要两处一起替换**（§9.3）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from src.agent.constants import MAX_TOOL_ROUNDS, SOURCE_LIMIT
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.utils import elapsed_ms, truncate_history
from src.logging_config import get_logger
from src.tools.rag_tools import TOOL_EXECUTORS  # noqa: F401 - 文档约定的补丁点之一

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """一次问答的运行时状态。

    ReAct 循环与生成阶段之间只通过它传递状态——把"循环走到哪了、攒了哪些来源"
    集中在一处，比让两个生成器互相传参好读得多。
    """

    question: str
    history: list[dict] = field(default_factory=list)
    top_k: int = 5
    messages: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    tool_ran: bool = False
    direct_content: str = ""
    phase_times: list[list] = field(default_factory=list)
    degraded: bool = False
    errored: bool = False

    @property
    def last_tool_name(self) -> str:
        """done 帧的 tool_name：**单字符串**，取最后一个执行完成的工具（契约，勿改成数组）。"""
        return self.tool_names[-1] if self.tool_names else ""


def merge_sources(ctx: AgentContext, new_sources: list[dict]) -> None:
    """跨轮合并来源：同一问题只留最高分的一条。

    模型常对同一问题换措辞检索多次，结果必然重叠；不去重的话来源卡片会出现
    好几条一模一样的问答，既占地方又让模型以为"多条证据都指向这个答案"。
    """
    if not new_sources:
        return
    best: dict[str, dict] = {s.get("question", ""): s for s in ctx.sources}
    for source in new_sources:
        key = source.get("question", "")
        current = best.get(key)
        if current is None or (source.get("score") or 0) > (current.get("score") or 0):
            best[key] = source
    ordered = sorted(best.values(), key=lambda s: s.get("score") or 0, reverse=True)
    ctx.sources = ordered[:SOURCE_LIMIT]


class ReActMixin:
    """宿主类需提供 `llm`、`runner`、`tool_schemas`。"""

    def _build_context(self, ctx: AgentContext) -> list[dict]:
        """ReAct 轮的初始消息：system + 截断历史 + 当前问题。"""
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(truncate_history(ctx.history))
        messages.append({"role": "user", "content": ctx.question})
        return messages

    def _validate_result(self, result: dict) -> str:
        """给工具结果打质量标签，便于日志与后续按质量调整策略。

        `empty` 与 `failed` 要分开看：前者说明知识库确实没有这条内容（该如实告知
        用户），后者说明后端出了问题（可重试）——两者的用户话术完全不同。
        """
        if not result.get("success"):
            return "failed"
        sources = (result.get("data") or {}).get("sources") or []
        return "empty" if not sources else "ok"

    def _react_events(self, ctx: AgentContext) -> Iterator[dict]:
        """执行工具轮，产出 status 事件并把工具结果并入 ctx.messages / ctx.sources。"""
        messages = self._build_context(ctx)

        for round_no in range(1, MAX_TOOL_ROUNDS + 1):
            if round_no == 1:
                yield {"type": "status", "content": "正在检索与搜索..."}
            else:
                yield {"type": "status", "content": f"正在补充检索（第{round_no}轮）..."}

            started = time.perf_counter()
            response = self.llm.chat_raw(messages, tools=self.tool_schemas)
            ctx.phase_times.append([f"工具轮{round_no}", elapsed_ms(started)])

            if not response.tool_calls:
                # 模型判断无需（继续）检索；它的正文留作"直接回答"的候选
                ctx.direct_content = response.content
                logger.info("第 %d 轮无工具调用，结束工具循环", round_no)
                break

            ctx.tool_ran = True
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in response.tool_calls
                    ],
                }
            )

            results = self.runner.run_many(response.tool_calls)
            for call, result in zip(response.tool_calls, results):
                label = self._validate_result(result)
                if label != "ok":
                    logger.info("工具 %s 结果标签: %s", call.name, label)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": (result.get("data") or {}).get("text") or "",
                    }
                )
                ctx.tool_names.append(call.name)
                merge_sources(ctx, (result.get("data") or {}).get("sources") or [])

            if round_no < MAX_TOOL_ROUNDS:
                yield {"type": "status", "content": "正在分析检索结果..."}

        ctx.messages = messages
