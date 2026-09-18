"""最终回答生成（GenerationMixin）。

两条出口都汇集到这里：正常情况下用 LLM 基于检索结果生成；无 LLM 凭据或工具轮
失败时降级为**直接返回检索原文**——这是"两种模式共存"里的降级侧，保证系统在
没有任何模型可用时依然能答招投标问题。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from src.agent.prompts import FINAL_STAGE_INSTRUCTION, SYSTEM_PROMPT, build_final_prompt
from src.agent.react_loop import AgentContext
from src.agent.utils import elapsed_ms, truncate_history
from src.logging_config import get_logger

logger = get_logger(__name__)


class GenerationMixin:
    """宿主类需提供 `llm`（供泄漏兜底后的重生成使用）。"""

    @staticmethod
    def _format_sources(sources: list[dict]) -> str | None:
        """把来源拼成模型易读的参考文本；无来源返回 None。"""
        if not sources:
            return None
        blocks = [
            f"[{index}] 问：{(s.get('question') or '').strip()}\n答：{(s.get('answer') or '').strip()}"
            for index, s in enumerate(sources, 1)
        ]
        return "\n\n".join(blocks)

    def _clean_for_final(self, messages: list[dict], ctx: AgentContext) -> list[dict]:
        """把 ReAct 轨迹整理成干净的最终生成上下文。

        取 `messages[0]` 作为 system 消息（约定：ReAct 上下文的第 0 条必是 system）。
        但**不**复用轨迹里的 tool 消息：不同 provider 对 `assistant.tool_calls` 与
        `role=tool` 的格式要求并不一致，走文本上下文可以完全绕开这些差异。

        messages 为空时退回内置提示词而不是越界崩溃——降级路径本来就可能没有轨迹。
        """
        base = (
            messages[0]
            if messages and messages[0].get("role") == "system"
            else {"role": "system", "content": SYSTEM_PROMPT}
        )
        # 在既有 system 提示后追加"本阶段无工具可用"的说明。保留 messages[0] 作为
        # 基底（约定），但要消除"必须调用工具"在无 tools 参数的最终生成里造成的
        # 工具语法泄漏（见 prompts.FINAL_STAGE_INSTRUCTION 的说明）。
        system = {
            "role": "system",
            "content": f"{base.get('content', '')}\n\n{FINAL_STAGE_INSTRUCTION}",
        }
        return [
            system,
            *truncate_history(ctx.history),
            {
                "role": "user",
                "content": build_final_prompt(ctx.question, self._format_sources(ctx.sources)),
            },
        ]

    def _generate_events(self, ctx: AgentContext, paced: bool = True) -> Iterator[dict]:
        """流式生成最终回答。"""
        # 模型在工具轮里已经给了完整正文、且根本没有调用工具（寒暄之类），
        # 直接采用它——再走一次生成既费一次调用，也可能把答案改坏
        if ctx.direct_content and not ctx.tool_ran:
            logger.info("模型判断无需工具，直接输出其回答")
            yield from self._stream_text(ctx.direct_content, paced)
            return

        if not ctx.sources:
            yield {"type": "status", "content": "知识库中没有找到相关内容"}
        else:
            yield {"type": "status", "content": "正在生成回答..."}

        started = time.perf_counter()
        messages = self._clean_for_final(ctx.messages, ctx)
        yield from self._generate_guarded(messages, ctx, paced)
        ctx.phase_times.append(["生成回答", elapsed_ms(started)])

    def _fallback_events(self, ctx: AgentContext, paced: bool = True) -> Iterator[dict]:
        """降级回答：直接输出最匹配的问答原文。"""
        from src.rag.pipeline import RAGPipeline

        text = RAGPipeline.fallback_answer(ctx.sources, ctx.question)
        yield from self._stream_text(text, paced)
