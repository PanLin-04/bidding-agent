"""
一个Plan-and-Solve模式的智能体（先制定分步计划，再逐步执行，最后汇总答案）
计划格式：纯文本编号列表

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
from typing import Any, Callable, Dict, List, Tuple

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI, OpenAIError

_ = load_dotenv(find_dotenv())

# —— 循环稳定性常量（与 react_tools.py 保持一致） ——
_MAX_OBSERVATION = 1500  # 工具结果截断长度（搜索类工具需要更大空间）
_REPEAT_STOP = 3        # 连续重复调用达到 3 次 → 硬终止（作用域为单个步骤内）
# —— Plan-and-Solve 专属常量 ——
_MAX_PLAN_STEPS = 8       # 计划步骤数上限
_MAX_PLAN_RETRIES = 2     # 计划格式失败的最大反馈重试次数
_MAX_STEP_ITERATIONS = 5  # 单步执行的最大 LLM 迭代次数
_STEP_RESULT_LIMIT = 500  # 跨步传递的步骤结果摘要截断长度
# 计划编号列表的匹配正则：兼容英文点、中文顿号、右括号等分隔符
_PLAN_STEP_RE = re.compile(r"^\s*(\d+)\s*[.、)）]\s*(.+)$", re.MULTILINE)


class PlanSolveAgent:
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

    def _build_plan_prompt(self, question: str) -> str:
        """
        构建规划阶段的系统提示：只要求模型输出分步计划

        :param question: 用户问题
        :return: 规划阶段的系统提示
        """
        return f"""你是一个智能助手，使用 Plan-and-Solve（先制定计划、再逐步执行）模式解决问题。

现在请先制定计划：把问题拆解为若干可执行的步骤，每步只做一件事，
可以借助工具（计算、搜索），也可以直接推理。

输出要求（严格遵守）：
- 输出一个编号列表，每行一个步骤，以「数字 + 英文点」开头，例如：
  1. 搜索 2026 年世界杯冠军所在的国家
  2. 搜索该国 2025 年的 GDP
- 步骤描述要具体，说明要搜索什么、计算什么
- 3 到 6 步为宜，最多不超过 8 步
- 只输出计划本身，不要输出任何其他解释

当前问题：{question}
"""

    def _build_execute_prompt(self, plan: List[str]) -> str:
        """
        构建执行阶段的系统提示：模型每步执行时都能看到完整计划与工具列表，
        但被要求只做当前这一步

        :param plan: 完整计划步骤列表
        :return: 执行阶段的系统提示
        """
        tool_descriptions = "\n".join([
            f"- {name}: {tool['description']}"
            for name, tool in self.tools.items()
        ])
        plan_text = "\n".join(
            f"{i}. {s}" for i, s in enumerate(plan, 1)
        )

        return f"""你是一个智能助手，正在执行一个既定计划。严格按计划执行【当前这一步】，
不要重新规划，不要执行其他步骤。

完整计划：
{plan_text}

可用工具：
{tool_descriptions}

规则：
1. 需要工具时调用工具，参数严格按工具定义生成
2. 不要重复调用已经调用过且参数相同的工具，结果不会改变
3. 工具结果返回后继续推理；这一步的信息足够时，直接给出这一步的结果
4. 不要编造不存在的工具
"""

    def _build_final_prompt(
        self, question: str, plan: List[str], results: List[str]
    ) -> str:
        """
        构建汇总阶段的系统提示：综合问题、计划与各步骤结果生成最终答案

        :param question: 原始问题
        :param plan: 完整计划
        :param results: 各步骤结果列表
        :return: 汇总阶段的系统提示
        """
        plan_text = "\n".join(
            f"{i}. {s}" for i, s in enumerate(plan, 1)
        )
        results_text = "\n".join(results)

        return f"""你是一个智能助手，请根据以下信息汇总出最终答案。

原始问题：{question}

计划：
{plan_text}

各步骤执行结果：
{results_text}

要求：
1. 汇总所有步骤结果，给出完整、准确的最终答案
2. 某一步失败或信息不足时，基于已有信息尽量回答；确实无法回答时，明确说明
3. 直接输出最终答案，不要输出过程性描述
"""

    def _call_llm(
        self, messages: list, max_retries: int = 2, use_tools: bool = True
    ) -> Any | None:
        """
        调用 LLM，带重试与异常处理

        :param messages: 消息列表
        :param max_retries: 最大重试次数
        :param use_tools: 是否携带 tools 参数（规划/汇总阶段为 False）
        :return: 模型回复的 message 对象（含 content 与 tool_calls），失败时返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    # 规划/汇总阶段不携带工具；未注册工具时省略该参数
                    tools=(self.tool_schemas or None) if use_tools else None,
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

        :param text: 模型回复原文
        :return: 干净的答案文本
        """
        answer = re.sub(r"^```[^\n]*\n?", "", text.strip())
        answer = re.sub(r"```\s*$", "", answer).strip()
        return answer

    def _truncate(self, s: str, limit: int = _MAX_OBSERVATION) -> str:
        """
        截断过长的文本，防止上下文膨胀

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
                    "请改用可用工具，或直接给出这一步的结果。")
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

    def _parse_plan(self, text: str) -> List[str]:
        """
        从模型回复中解析编号步骤列表

        :param text: 模型回复原文
        :return: 步骤描述列表（解析失败时为空列表）
        """
        cleaned = self._clean_answer(text)
        steps = [m[1].strip() for m in _PLAN_STEP_RE.findall(cleaned)]
        return [s for s in steps if s]

    def _make_plan(self, question: str) -> List[str] | None:
        """
        规划阶段：调用 LLM 生成分步计划；格式错误时追加反馈重试，
        仍失败则退化为单步执行（问题整体作为一步，等价于 ReAct）

        :param question: 用户问题
        :return: 步骤列表；API 彻底失败时返回 None
        """
        messages = [
            {"role": "system", "content": self._build_plan_prompt(question)},
        ]
        for attempt in range(_MAX_PLAN_RETRIES + 1):
            message = self._call_llm(messages, use_tools=False)
            if message is None:
                return None
            content = message.content or ""
            if isinstance(content, list):  # 防御：个别 provider 返回列表
                content = "".join(
                    part.get("text", "") for part in content
                )
            steps = self._parse_plan(content)
            if steps:
                if len(steps) > _MAX_PLAN_STEPS:
                    print(f"计划步骤超过 {_MAX_PLAN_STEPS} 步，仅执行前 {_MAX_PLAN_STEPS} 步")
                    steps = steps[:_MAX_PLAN_STEPS]
                return steps
            if attempt < _MAX_PLAN_RETRIES:
                # 格式不符：把模型原文写回历史并追加反馈，随后重试
                print(f"计划格式不正确（第 {attempt + 1} 次），追加反馈后重试")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "计划格式不正确：请按编号列表输出，每行一个步骤"
                               "（如：1. 搜索……）。只输出计划本身。",
                })
        # 重试耗尽仍无法解析：退化为单步执行
        print("计划解析失败，将问题整体作为单步执行")
        return [question]

    def _execute_step(
        self,
        index: int,
        step_desc: str,
        plan: List[str],
        prior_results: List[str],
        max_iterations: int = _MAX_STEP_ITERATIONS,
    ) -> Tuple[str, bool]:
        """
        执行阶段：对单个计划步骤跑一个迷你 ReAct 循环。
        每步独立对话（阶段间只靠结果摘要文本接力），重复检测仅在步内生效。

        :param index: 步骤序号（从 1 开始）
        :param step_desc: 本步骤描述
        :param plan: 完整计划
        :param prior_results: 已完成步骤的结果摘要列表
        :param max_iterations: 步内最大 LLM 迭代次数
        :return: (本步结果文本, 是否成功)
        """
        prior_text = (
            "\n".join(prior_results) if prior_results else "（暂无已完成步骤）"
        )
        conversation_history = [
            {"role": "system", "content": self._build_execute_prompt(plan)},
            {"role": "user", "content": (
                f"请执行第 {index} 步：{step_desc}\n\n"
                f"已完成步骤的结果：\n{prior_text}"
            )},
        ]

        last_call = None      # 上一次执行的 (工具名, 序列化参数)
        repeat_count = 0      # 连续重复调用计数（仅在当前步骤内生效）

        for iteration in range(max_iterations):
            # 1. 调用 LLM（含重试，携带 tools 参数）
            message = self._call_llm(conversation_history)
            if message is None:
                return "API 调用失败，无法完成此步。", False

            tool_calls = list(message.tool_calls or [])
            content = message.content
            if isinstance(content, list):  # 防御：个别 provider 返回列表
                content = "".join(
                    part.get("text", "") for part in content
                )

            # 2. 没有工具调用 → 模型直接给出本步结果
            if not tool_calls:
                result = (content or "").strip()
                if result:
                    conversation_history.append({
                        "role": "assistant",
                        "content": result
                    })
                    print(f"步骤 {index} 完成：{result}")
                    return self._clean_answer(result), True
                # 空回复：提醒模型继续
                observation = "请继续：要么调用工具，要么直接给出这一步的结果。"
                conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                print(observation)
                print("-" * 50)
                continue

            # 3. 将带 tool_calls 的 assistant 消息写入历史
            conversation_history.append({
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
                        return "未能在规定步骤内完成任务：模型连续重复调用相同工具。", False
                    result = (
                        f"警告：你已连续 {repeat_count} 次调用 {tool_name}，"
                        "参数相同，结果不会改变。"
                        "请尝试新的思路，或直接给出这一步的结果。"
                    )
                else:
                    last_call, repeat_count = call, 1
                    result = self._execute_tool(tool_name, tool_args)

                observation = self._truncate(str(result))
                print(f"Observation: {observation}")
                print("-" * 50)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        return "步内迭代次数耗尽，未得出明确结果。", False

    def _make_final_answer(
        self, question: str, plan: List[str], results: List[str]
    ) -> str:
        """
        汇总阶段：不带工具地单独调用一次 LLM，综合各步骤结果生成最终答案

        :param question: 原始问题
        :param plan: 完整计划
        :param results: 各步骤结果列表
        :return: 最终答案；调用失败时降级为拼接各步骤结果摘要
        """
        messages = [
            {"role": "system", "content": self._build_final_prompt(
                question, plan, results
            )},
            {"role": "user", "content": "请给出最终答案。"},
        ]
        message = self._call_llm(messages, use_tools=False)
        if message is None:
            return "（汇总调用失败，以下为各步骤结果摘要）\n" + "\n".join(results)
        content = message.content or ""
        if isinstance(content, list):  # 防御：个别 provider 返回列表
            content = "".join(
                part.get("text", "") for part in content
            )
        answer = self._clean_answer(content)
        if answer:
            return answer
        return "（汇总调用失败，以下为各步骤结果摘要）\n" + "\n".join(results)

    def run(self, question: str) -> str:
        """
        运行智能体：规划 → 逐步执行 → 汇总

        :param question: 用户问题
        :return: 最终答案
        """
        # 阶段一：规划
        print("=== 规划阶段 ===")
        plan = self._make_plan(question)
        if plan is None:
            return "API 调用失败，无法完成任务。"
        print("生成的分步计划：")
        for i, step in enumerate(plan, 1):
            print(f"{i}. {step}")

        # 阶段二：逐步执行
        print()
        print("=== 执行阶段 ===")
        results = []
        for i, step in enumerate(plan, 1):
            print(f"--- 执行步骤 {i}/{len(plan)}: {step} ---")
            result, _ = self._execute_step(i, step, plan, results)
            summary = self._truncate(result, limit=_STEP_RESULT_LIMIT)
            results.append(f"步骤 {i}：{summary}")
            print()

        # 阶段三：汇总
        print("=== 汇总阶段 ===")
        return self._make_final_answer(question, plan, results)


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
    agent = PlanSolveAgent()

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

    print("=== Plan-and-Solve 智能体 ===")
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
