"""
一个ReAct模式的智能体（原生 Function Calling 版）

示例问题：
1. 2026 年世界杯冠军所在国家，在2025年的GDP是多少？
2. 25 乘以 4 再加 100 等于多少？
"""
import inspect
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI, OpenAIError

_ = load_dotenv(find_dotenv())

# —— 循环稳定性常量 ——
_MAX_OBSERVATION = 1500  # 工具结果截断长度（搜索类工具需要更大空间）
_REPEAT_STOP = 3        # 连续重复调用达到 3 次 → 硬终止


class ReActAgent:
    def __init__(self, model: str = "deepseek-v4-flash"):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "未找到 DEEPSEEK_API_KEY，请在 .env 文件中配置后重试"
            )
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1"
        )
        self.model = model
        self.tools: Dict[str, Callable] = {}
        self.tool_schemas: list = []  # OpenAI 兼容的 tools 参数
        self.conversation_history = []

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: dict | None = None,
    ):
        """
        注册一个工具，用于智能体调用

        :param name: 工具名称
        :param func: 工具函数，接收关键字参数并返回结果
        :param description: 工具描述，用于智能体判断何时调用
        :param parameters: 参数的 JSON Schema，模型据此结构化生成调用参数
        """
        self.tools[name] = {"func": func, "description": description}
        self.tool_schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters or {
                    "type": "object",
                    "properties": {},
                },
            },
        })

    def _build_system_prompt(self, question: str) -> str:
        """
        构建智能体的系统提示。
        工具调用交给原生 function calling，不再要求模型输出固定文本格式。

        :param question: 用户问题
        :return: 智能体的系统提示
        """
        tool_descriptions = "\n".join([
            f"- {name}: {tool['description']}"
            for name, tool in self.tools.items()
        ])

        return f"""你是一个智能助手，使用ReAct（Reasoning + Acting）模式来解决问题。
先思考再行动：需要计算、搜索等信息时调用对应工具，工具结果返回后继续推理，
信息足够时直接给出最终答案。

可用工具：
{tool_descriptions}

规则：
1. 需要工具时调用工具，参数严格按工具定义生成
2. 不要重复调用已经调用过且参数相同的工具，结果不会改变
3. 工具结果返回后继续推理；信息足够时立即给出最终答案，不要再调用工具
4. 不要编造不存在的工具

当前问题：{question}
"""

    def _call_llm(self, messages: list, max_retries: int = 2) -> Any | None:
        """
        调用 LLM（携带 tools 参数），带重试与异常处理

        :param messages: 消息列表
        :param max_retries: 最大重试次数
        :return: 模型回复的 message 对象（含 content 与 tool_calls），失败时返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self.tool_schemas or None,  # 未注册工具时省略该参数
                    temperature=0.3,  # 降低参数漂移
                )
                message = response.choices[0].message
                if message.content or message.tool_calls:
                    return message
                # 偶发返回全空，同样重试
                print(f"模型回复为空（第 {attempt + 1}/{max_retries + 1} 次），准备重试")
            except OpenAIError as e:
                print(f"API 调用失败（第 {attempt + 1}/{max_retries + 1} 次）: {e}")
            except Exception as e:
                print(f"调用 LLM 时发生未知错误: {e}")
                break
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 1s, 2s
        return None

    def _clean_answer(self, text: str) -> str:
        """
        清理最终答案中的代码围栏

        :param text: 模型最终回复
        :return: 干净的答案文本
        """
        answer = re.sub(r"^```[^\n]*\n?", "", text.strip())
        answer = re.sub(r"```\s*$", "", answer).strip()
        return answer

    def _truncate(self, s: str, limit: int = _MAX_OBSERVATION) -> str:
        """
        截断过长的工具结果，防止上下文膨胀

        :param s: 原始字符串
        :param limit: 最大长度
        :return: 截断后的字符串
        """
        return s if len(s) <= limit else s[:limit] + "...（已截断）"

    def _execute_tool(self, name: str, args: dict) -> str:
        """
        执行工具，全部异常兜住后以文本返回

        :param name: 工具名
        :param args: 模型生成的结构化参数
        :return: 工具返回结果字符串
        """
        if name not in self.tools:
            return (f"未知工具 '{name}'，"
                    f"可用工具：{', '.join(self.tools)}。"
                    "请改用可用工具，或直接给出最终答案。")
        func = self.tools[name]["func"]
        try:
            # 按函数签名过滤参数，模型偶发多传参数时不至于报错
            sig = inspect.signature(func)
            accepts_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
            kwargs = args if accepts_var_kw else {
                k: v for k, v in args.items() if k in sig.parameters
            }
            return str(func(**kwargs))
        except Exception as e:
            return f"工具执行出错：{e}"

    def run(self, question: str, max_steps: int = 10) -> str:
        """
        运行智能体，解决用户问题

        :param question: 用户问题
        :param max_steps: 最大推理步骤，默认10步
        :return: 智能体的最终答案
        """
        self.conversation_history = [
            {"role": "system", "content": self._build_system_prompt(question)}
        ]

        last_call = None      # 上一次执行的 (工具名, 序列化参数)
        repeat_count = 0      # 连续重复调用计数

        for step in range(max_steps):
            # 1. 调用 LLM（含重试，携带 tools 参数）
            message = self._call_llm(self.conversation_history)
            if message is None:
                print(f"Step {step + 1}: API 调用失败或回复为空")
                return "API 调用失败，无法完成任务。"

            tool_calls = list(message.tool_calls or [])
            content = message.content
            if isinstance(content, list):  # 防御：个别 provider 返回列表
                content = "".join(
                    part.get("text", "") for part in content
                )

            # 2. 没有工具调用 → 模型直接给出最终答案
            if not tool_calls:
                answer = (content or "").strip()
                if answer:
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": answer
                    })
                    print(f"Step {step + 1}:")
                    print(answer)
                    return self._clean_answer(answer)
                # 空回复：提醒模型继续
                observation = "请继续：要么调用工具，要么直接给出最终答案。"
                self.conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                print(observation)
                print("-" * 50)
                continue

            # 3. 将带 tool_calls 的 assistant 消息写入历史
            self.conversation_history.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # 4. 依次执行每个工具调用（支持模型一次并行调用多个工具）
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                print(f"Step {step + 1}:")
                print(f"Thought: {content or '（调用工具）'}")
                print(f"Action: {tool_name}"
                      f"({json.dumps(tool_args, ensure_ascii=False)})")
                print("-" * 50)

                # 重复调用检测（连续相同调用第 2 次起警告且不执行，第 3 次硬终止）
                call = (
                    tool_name,
                    json.dumps(tool_args, sort_keys=True, ensure_ascii=False),
                )
                if call == last_call:
                    repeat_count += 1
                    if repeat_count >= _REPEAT_STOP:
                        return "未能在规定步骤内完成任务：模型连续重复调用相同工具。"
                    result = (
                        f"警告：你已连续 {repeat_count} 次调用 {tool_name}，"
                        "参数相同，结果不会改变。"
                        "请尝试新的思路，或直接给出最终答案。"
                    )
                else:
                    last_call, repeat_count = call, 1
                    result = self._execute_tool(tool_name, tool_args)

                observation = self._truncate(str(result))
                print(f"Observation: {observation}")
                print("-" * 50)
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        return "未能在规定步骤内完成任务。"


def calculator(expression: str) -> str:
    """
    数学计算器工具，用于执行数学计算

    :param expression: 数学表达式字符串，如 25*4+100
    :return: 计算结果字符串
    """
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"计算错误: {str(e)}"


def web_search(query: str, max_results: int = 3) -> str:
    """
    Tavily 搜索引擎，用于实时查找网络信息

    :param query: 搜索关键词字符串
    :param max_results: 最大返回结果数
    :return: 搜索结果字符串
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "搜索错误: 未配置 TAVILY_API_KEY，请在 .env 文件中添加后重试"
    try:
        from tavily import TavilyClient
    except ImportError:
        return "搜索错误: 未安装 tavily-python，请运行 uv sync 安装依赖"
    try:
        response = TavilyClient(api_key=api_key).search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=True,
        )
    except Exception as e:
        return f"搜索错误: {e}"

    lines = []
    answer = response.get("answer")
    if answer:
        lines.append(f"答案摘要: {answer}")
    results = response.get("results") or []
    if results:
        lines.append("搜索结果:")
        for i, r in enumerate(results, 1):
            content = r.get("content", "").strip().replace("\n", " ")[:200]
            lines.append(
                f"{i}. {r.get('title', '无标题')} ({r.get('url', '')})\n"
                f"   {content}"
            )
    else:
        lines.append("未找到相关结果")
    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 控制台中文乱码
    agent = ReActAgent()

    agent.register_tool(
        "calculator",
        calculator,
        "计算器，用于执行数学计算",
        {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 25*4+100，"
                                   "可用运算符 + - * / ** %",
                },
            },
            "required": ["expression"],
        },
    )

    agent.register_tool(
        "search",
        web_search,
        "Tavily 搜索引擎，用于实时查找网络信息",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
    )

    print("=== ReAct 智能体（Function Calling）===")
    print("可用工具: calculator, search")
    print()

    question = input("请输入你的问题: ")
    print()

    result = agent.run(question)
    print()
    print("最终结果:")
    print(result)


if __name__ == "__main__":
    main()
