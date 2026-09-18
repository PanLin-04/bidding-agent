"""RAG 工具：把知识库混合检索注册成 Agent 可调用的工具。

执行函数按 §8.3 的约定返回 `(格式化文本, sources 列表)`——文本给模型读，
sources 给前端的来源卡片用；契约包装由 `ToolRunner` 统一完成。
"""

from __future__ import annotations

from src.logging_config import get_logger
from src.rag.constants import DEFAULT_TOP_K
from src.tools.base import BaseTool

logger = get_logger(__name__)

# top_k 的上下界：模型偶尔会给出 0 或很大的值，这里做一次收敛，
# 既不浪费算力也不至于把来源列表撑爆
MIN_TOP_K = 1
MAX_TOP_K = 20


def _fmt_knowledge_results(query: str, sources: list[dict]) -> str:
    """把检索结果格式化成模型易读的文本。

    用「[编号] 问/答」而不是 JSON：问答对的答案本身是成段的中文说明文，
    塞进 JSON 会带来大量转义噪声，反而降低模型对内容的理解。
    """
    if not sources:
        return f"知识库中没有检索到与「{query}」相关的内容。"
    blocks = []
    for index, source in enumerate(sources, 1):
        question = (source.get("question") or "").strip()
        answer = (source.get("answer") or "").strip()
        blocks.append(f"[{index}] 问：{question}\n答：{answer}")
    return "\n\n".join(blocks)


def search_knowledge_base(query: str, top_k: int = DEFAULT_TOP_K) -> tuple[str, list[dict]]:
    """检索招投标采购知识库，返回 `(格式化文本, sources)`。

    异常**不在此处捕获**：交给 `ToolRunner` 统一转成失败契约，避免每加一个工具
    就重写一遍 try/except。
    """
    from src.rag.pipeline import rag_pipeline

    try:
        limit = max(MIN_TOP_K, min(int(top_k), MAX_TOP_K))
    except (TypeError, ValueError):
        limit = DEFAULT_TOP_K

    # 走带缓存的检索：多轮追问与前端重试时省掉重复的编码与向量检索
    sources = rag_pipeline.search_cached(query, limit)
    logger.info("工具检索: query=%.40s -> %d 条", query, len(sources))
    return _fmt_knowledge_results(query, sources), sources


class KnowledgeBaseTool(BaseTool):
    """招投标采购知识库检索工具。"""

    name = "search_knowledge_base"
    description = (
        "检索招投标采购知识库，返回相关的问答条目。"
        "适用于：政府采购法规与条款、采购方式（公开招标/竞争性磋商/单一来源等）、"
        "招投标流程与时限、质疑投诉与异议处理、投标保证金等业务问题。"
        "可以针对同一问题用不同措辞多次调用以提高召回。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索用的自然语言问题或关键词",
            },
            "top_k": {
                "type": "integer",
                "description": f"返回条数，默认 {DEFAULT_TOP_K}",
                "minimum": MIN_TOP_K,
                "maximum": MAX_TOP_K,
            },
        },
        "required": ["query"],
    }

    def run(self, **kwargs) -> dict:
        from src.tools.base import ok

        text, sources = search_knowledge_base(
            kwargs.get("query", ""), kwargs.get("top_k", DEFAULT_TOP_K)
        )
        return ok(text, sources, tool=self.name)


# 工具注册表：名称 → 执行函数。Agent 侧通过它构造 ToolRunner。
TOOL_EXECUTORS = {KnowledgeBaseTool.name: search_knowledge_base}

ALL_TOOLS = [KnowledgeBaseTool()]


def get_tool_schemas() -> list[dict]:
    """供 LLM 的 tools 参数使用的 Schema 列表。"""
    return [tool.schema() for tool in ALL_TOOLS]
