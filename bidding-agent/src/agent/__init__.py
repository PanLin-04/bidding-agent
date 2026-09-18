"""Agent 包：ReAct 工具循环 + 最终生成 + 工具文本防护。

`from src.agent import BiddingAgent, bidding_agent` 为对外契约（测试与 eval 依赖），
勿破坏（见 docs/开发文档.md §13.3）。

尚未实现（属成员 A 的后续分工）：`skills.py` 技能加载与 `_match_skills` 匹配。
"""

from src.agent.core import BiddingAgent, bidding_agent
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.react_loop import AgentContext, merge_sources
from src.agent.tool_defense import contains_tool_text, parse_tool_calls, strip_tool_text

__all__ = [
    "SYSTEM_PROMPT",
    "AgentContext",
    "BiddingAgent",
    "bidding_agent",
    "contains_tool_text",
    "merge_sources",
    "parse_tool_calls",
    "strip_tool_text",
]
