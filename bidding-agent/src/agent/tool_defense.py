"""工具调用文本的检测 / 解析 / 归一化（ToolDefenseMixin）。

**要防的是什么**：模型本该通过原生 Function Calling 发起调用，但偶尔会把调用意图
当成**正文**吐出来，例如把 `<tool_call>{"name": "search_knowledge_base", ...}</tool_call>`
直接写进回答里。用户看到这段 JSON 是灾难性的——既暴露了内部实现，也说明这一轮
其实没有真正检索。它还会出现在流式输出的**中途**，所以必须边流边判。

处理策略：缓冲开头一小段（`STREAM_FLUSH_CHARS`）再决定放行；一旦检出泄漏就发
`reset` 事件把已渲染的正文清掉（协议里 reset 就是干这个的），然后真的去执行那个
调用，再重新生成一次。重试只做一轮，避免模型反复泄漏时陷入循环。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from src.agent.constants import STREAM_FLUSH_CHARS
from src.clients.base_client import ToolCall
from src.logging_config import get_logger

logger = get_logger(__name__)

# 判定"这一段里有工具调用痕迹"的特征串。宁可多列几个：漏判的代价是用户看到
# 一段调用语法，误判的代价只是多缓冲一次——不对等的代价决定了这里取保守策略。
#
# 两种方言都要覆盖（实测 DeepSeek 会用第二种，且标签里**带空格**）：
#   JSON 式： <tool_call>{"name": "search_knowledge_base", ...}</tool_call>
#   XML  式： <tool calls><invoke name="search_knowledge_base">
#               <parameter name="query">…</parameter></invoke></tool calls>
TOOL_TEXT_MARKERS = (
    "<tool_call>",
    "</tool_call>",
    "<tool_calls>",
    "<tool calls>",
    "</tool calls>",
    "<invoke",
    "<parameter name=",
    "<function_calls>",
    "<function_call>",
    '"tool_calls"',
    '"function_call"',
    "search_knowledge_base(",
    "tool_call_id",
)

# XML 方言：<invoke name="工具名"><parameter name="参数">值</parameter>…</invoke>
_INVOKE_RE = re.compile(
    r"<invoke\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<body>.*?)</invoke>", re.DOTALL
)
_PARAM_RE = re.compile(
    r"<parameter\s+name=[\"'](?P<key>[^\"']+)[\"']\s*>(?P<value>.*?)</parameter>", re.DOTALL
)

# `search_knowledge_base("...")` / `search_knowledge_base({"query": "..."})` 这类
# 函数调用语法（不是 JSON，上面的括号扫描扫不到）
_FUNC_CALL_RE = re.compile(r"(\w+)\s*\(\s*(\{.*?\}|[^)]*)\s*\)", re.DOTALL)


def contains_tool_text(text: str) -> bool:
    """文本中是否出现工具调用痕迹。"""
    if not text:
        return False
    return any(marker in text for marker in TOOL_TEXT_MARKERS)


def _iter_json_objects(text: str) -> Iterator[str]:
    """扫描出文本中所有**配平的** JSON 对象字面量。

    用括号配平而不是正则：工具参数的 JSON 里还嵌着对象和数组，正则会截断。
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : index + 1]
                    start = -1


def _coerce_arguments(value) -> str:
    """把 arguments 字段归一化成 JSON 字符串（模型有时直接给对象）。"""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def parse_tool_calls(text: str) -> list[ToolCall]:
    """从正文里解析出工具调用。解析不出来就返回空列表，绝不抛异常。"""
    calls: list[ToolCall] = []

    # XML 方言优先：它与 JSON 方言不会同时出现，但标签里可能嵌着 JSON 味儿的文本，
    # 先按结构化程度更高的 XML 解析，避免被后面的括号扫描误判
    xml_calls: list[ToolCall] = []
    for match in _INVOKE_RE.finditer(text):
        arguments = {
            param.group("key"): param.group("value").strip()
            for param in _PARAM_RE.finditer(match.group("body"))
        }
        xml_calls.append(
            ToolCall(
                id=f"text-{len(xml_calls)}",
                name=match.group("name"),
                arguments=json.dumps(arguments, ensure_ascii=False),
            )
        )
    if xml_calls:
        return xml_calls

    for blob in _iter_json_objects(text):
        try:
            payload = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue

        # 形态一：整包 {"tool_calls": [{...}]}
        if isinstance(payload.get("tool_calls"), list):
            for item in payload["tool_calls"]:
                if isinstance(item, dict):
                    name = item.get("name") or (item.get("function") or {}).get("name")
                    if name:
                        args = item.get("arguments")
                        if args is None:
                            args = (item.get("function") or {}).get("arguments")
                        calls.append(
                            ToolCall(id=str(item.get("id") or f"text-{len(calls)}"), name=name,
                                     arguments=_coerce_arguments(args))
                        )
            continue

        # 形态二：单个 {"name": ..., "arguments"/"parameters": {...}}
        name = payload.get("name") or (payload.get("function") or {}).get("name")
        if not name:
            continue
        args = payload.get("arguments")
        if args is None:
            args = payload.get("parameters")
        if args is None:
            args = (payload.get("function") or {}).get("arguments")
        calls.append(
            ToolCall(id=str(payload.get("id") or f"text-{len(calls)}"), name=str(name),
                     arguments=_coerce_arguments(args))
        )

    if calls:
        return calls

    # 形态三：函数调用语法 `name({...})` 或 `name("关键词")`
    for match in _FUNC_CALL_RE.finditer(text):
        name, raw_args = match.group(1), match.group(2).strip()
        if name not in {"search_knowledge_base"}:
            continue
        if raw_args.startswith("{"):
            arguments = raw_args
        else:
            literal = raw_args.strip().strip("'\"")
            if not literal:
                continue
            arguments = json.dumps({"query": literal}, ensure_ascii=False)
        calls.append(ToolCall(id=f"text-{len(calls)}", name=name, arguments=arguments))

    return calls


def strip_tool_text(text: str) -> str:
    """清掉泄漏片段，保留可能存在的正常正文。"""
    cleaned = text
    # XML 方言：<invoke>…</invoke> 与外层标签整段去掉
    cleaned = re.sub(r"<invoke\b.*?</invoke>", "", cleaned, flags=re.DOTALL)
    for tag in ("<tool calls>", "</tool calls>", "<function_calls>", "</function_calls>"):
        cleaned = cleaned.replace(tag, "")
    # JSON 方言：<tool_call>…</tool_call> 整段去掉（含内容）
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<tool_call>.*$", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


class ToolDefenseMixin:
    """流式生成期间的泄漏防护。宿主类需提供 `llm` 与 `runner`。"""

    def _stream_text(self, text: str, paced: bool = True) -> Iterator[dict]:
        from src.agent.utils import pace_text

        for chunk in pace_text(text, paced):
            yield {"type": "token", "content": chunk}

    def _generate_guarded(self, messages: list[dict], ctx, paced: bool = True) -> Iterator[dict]:
        """流式生成，并在开头缓冲一段做泄漏检测。

        检出泄漏时：`reset` 清屏 → 执行被泄漏的调用 → 用扩充后的上下文重新生成
        （只重试一次，防止反复泄漏时死循环）。
        """
        buffer = ""
        for chunk in self.llm.chat_stream(messages):
            buffer += chunk
            if contains_tool_text(buffer):
                logger.warning("检出工具调用文本泄漏，转由后端执行")
                yield {"type": "reset"}
                yield {"type": "status", "content": "正在检索知识库..."}
                recovered = self._recover_from_leak(buffer, ctx)
                if not recovered:
                    # 解析不出来就只好把泄漏片段去掉、把剩余正文给用户
                    yield from self._stream_text(strip_tool_text(buffer), paced)
                    return
                yield {"type": "status", "content": "正在生成回答..."}
                # 用补齐了工具结果的新上下文重新生成（_clean_for_final 由 GenerationMixin 提供）
                for retry_chunk in self.llm.chat_stream(self._clean_for_final(ctx.messages, ctx)):  # type: ignore[attr-defined]
                    if not contains_tool_text(retry_chunk):
                        yield {"type": "token", "content": retry_chunk}
                return

            if len(buffer) <= STREAM_FLUSH_CHARS:
                continue  # 攒够再放行：泄漏片段总在开头

            yield from self._stream_text(buffer, paced)
            buffer = ""

        if buffer:
            yield from self._stream_text(strip_tool_text(buffer), paced)

    def _recover_from_leak(self, leaked_text: str, ctx) -> bool:
        """执行正文里泄漏出来的工具调用，把结果并入上下文。"""
        calls = parse_tool_calls(leaked_text)
        if not calls:
            return False
        results = self.runner.run_many(calls)  # type: ignore[attr-defined]
        from src.agent.react_loop import merge_sources

        for result in results:
            ctx.tool_ran = True
            merge_sources(ctx, (result.get("data") or {}).get("sources") or [])
        ctx.phase_times.append(["工具兜底执行", 0])
        return True
