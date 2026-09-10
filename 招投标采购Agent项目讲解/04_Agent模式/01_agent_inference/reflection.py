"""
一个Reflection（反思式推理）模式的智能体：Actor 尝试作答，Critic 评审并给出反馈，
不通过则反馈累积进反思记忆，下一次尝试据此修正，直到通过或达到最大尝试次数。

示例问题：
1. 2026 年世界杯冠军所在国家，在2025年的GDP是多少？（事实类问题，常触发多轮反思纠错）
2. 25 乘以 4 再加 100 等于多少？（计算类问题，通常一次尝试即可通过）
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
_REPEAT_STOP = 3        # 连续重复调用达到 3 次 → 硬终止（作用域为单次尝试内）
# —— Reflection 专属常量 ——
_MAX_ATTEMPTS = 3             # 最大尝试次数（Actor 一轮 + Critic 评审算一次）
_MAX_ATTEMPT_ITERATIONS = 5   # 单次尝试内 Actor 的最大 LLM 迭代次数
_FEEDBACK_LIMIT = 500         # 存入反思记忆的结果摘要截断长度
# Critic 输出解析失败时的兜底反馈（按「不通过」处理是安全方向，绝不误放行错误答案）
_NEUTRAL_FEEDBACK = "本次反思未生成有效反馈，请仔细核对答案的正确性与完整性后重新作答。"
# Critic 文本形式输出的正则兜底
_CRITIC_VERDICT_RE = re.compile(r"判定\s*[:：]\s*(通过|不通过)")
_CRITIC_FEEDBACK_RE = re.compile(r"反馈\s*[:：]\s*(.+)", re.DOTALL)


class ReflectionAgent:
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
        self.reflection_history: List[str] = []  # 反思记忆（跨尝试累积，每次 run 开头重置）

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

    def _build_actor_prompt(
        self, question: str, reflections: List[str]
    ) -> str:
        """
        构建 Actor 的系统提示：含工具列表、规则与历史反思记忆

        :param question: 用户问题
        :param reflections: 历史反思记忆列表
        :return: Actor 的系统提示
        """
        tool_descriptions = "\n".join([
            f"- {name}: {tool['description']}"
            for name, tool in self.tools.items()
        ])
        reflections_text = (
            "\n\n".join(reflections)
            if reflections else "（暂无，这是第一次尝试）"
        )

        return f"""你是一个智能助手，使用 Reflection（反思式推理）模式来解决问题：
先基于已有信息完成一次回答，评审模块会评价这次回答；如果评审不通过，
你会在下一次尝试时看到具体的反馈，请据此修正思路，不要重复同样的错误。

可用工具：
{tool_descriptions}

规则：
1. 需要工具时调用工具，参数严格按工具定义生成
2. 不要重复调用已经调用过且参数相同的工具，结果不会改变
3. 工具结果返回后继续推理；信息足够时直接给出最终答案，不要再调用工具
4. 不要编造不存在的工具
5. 涉及事实数据请务必先用 search 确认，涉及计算请务必先用 calculator 验证

历史反思（之前尝试未通过评审的反馈，请逐条对照修正）：
{reflections_text}

当前问题：{question}
"""

    def _build_critic_prompt(
        self, question: str, trajectory: str, answer: str
    ) -> str:
        """
        构建 Critic 的系统提示：给出问题、完整轨迹与最终答案，要求输出评审 JSON

        :param question: 原始问题
        :param trajectory: Actor 本次尝试的完整轨迹
        :param answer: Actor 本次尝试的最终答案
        :return: Critic 的系统提示
        """
        trajectory_text = trajectory or "（无工具调用，直接给出答案）"

        return f"""你是一个严谨的答案评审员（Critic），请判断智能体对下列问题的回答是否合格。

原始问题：{question}

智能体的完整推理轨迹（含工具调用与观察结果）：
{trajectory_text}

智能体的最终答案：
{answer}

评估标准（逐条检查）：
1. 正确性：推理过程是否正确，答案是否基于工具返回的真实结果
2. 完整性：是否回答了问题的所有部分（例如问题同时要求"国家"与"GDP"，缺一不可）
3. 事实性：是否编造了工具结果中不存在的信息，数字是否与搜索结果一致
4. 结论明确：是否给出确定的答案，而不是模棱两可的猜测
5. 只要答案错误、不完整、或推理过程有明显错误，就必须判定不通过

输出要求（严格遵守）：
- 输出一个合法的 JSON 对象，包含两个字段，形如：
  {{"pass": false, "feedback": "问题出在哪里：……；改进建议：……"}}
- pass 字段：true 表示通过，false 表示不通过
- feedback 字段：字符串，必须包含「问题出在哪里」与「改进建议」两部分，供智能体下次尝试修正
- 只输出 JSON 本身，不要输出任何其他解释或代码围栏
"""

    def _call_llm(
        self,
        messages: list,
        max_retries: int = 2,
        use_tools: bool = True,
        response_format: dict | None = None,
    ) -> Any | None:
        """
        调用 LLM，带重试与异常处理

        :param messages: 消息列表
        :param max_retries: 最大重试次数
        :param use_tools: 是否携带 tools 参数（Critic 阶段为 False）
        :param response_format: 回复格式约束（Critic 阶段传 {"type": "json_object"}）
        :return: 模型回复的 message 对象（含 content 与 tool_calls），失败时返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    # Critic 阶段不携带工具；未注册工具时省略该参数
                    tools=(self.tool_schemas or None) if use_tools else None,
                    # Critic 阶段强制 JSON 输出（json_object 模式要求提示词含 "json"）
                    response_format=response_format,
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

    def _parse_critic_output(self, text: str) -> Tuple[bool, str]:
        """
        解析 Critic 的评审输出，三层兜底

        :param text: Critic 回复原文
        :return: (是否通过, 反馈文本)
        """
        cleaned = self._clean_answer(text)
        # 第一层：JSON 解析（兼容英文键 pass/feedback 与中文键 判定/反馈）
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            verdict = data.get("pass", data.get("判定"))
            feedback = str(data.get("feedback", data.get("反馈", ""))).strip()
            if isinstance(verdict, bool):
                return verdict, feedback or _NEUTRAL_FEEDBACK
            if isinstance(verdict, str):
                v = verdict.strip().lower()
                if v in ("true", "通过"):
                    return True, feedback or _NEUTRAL_FEEDBACK
                if v in ("false", "不通过"):
                    return False, feedback or _NEUTRAL_FEEDBACK
        # 第二层：正则解析文本形式的「判定/反馈」
        m = _CRITIC_VERDICT_RE.search(cleaned)
        f = _CRITIC_FEEDBACK_RE.search(cleaned)
        if m and f:
            return m.group(1) == "通过", f.group(1).strip()
        # 第三层：解析失败按「不通过」处理（最多多花一次尝试，绝不误放行错误答案）
        return False, _NEUTRAL_FEEDBACK

    def _attempt_once(
        self,
        question: str,
        reflections: List[str],
        max_iterations: int = _MAX_ATTEMPT_ITERATIONS,
    ) -> Tuple[str | None, str, bool]:
        """
        单次尝试：Actor 跑一个带工具的迷你 ReAct 循环，并同步收集完整轨迹。
        轨迹仅供当次 Critic 评审消费，不进反思记忆（记忆里只存结果摘要 + 反馈）。

        :param question: 用户问题
        :param reflections: 历史反思记忆列表（为空表示第一次尝试）
        :param max_iterations: 尝试内最大 LLM 迭代次数
        :return: (答案文本, 完整轨迹文本, 步内是否正常完成)；API 彻底失败时答案为 None
        """
        conversation_history = [
            {"role": "system", "content": self._build_actor_prompt(
                question, reflections
            )},
        ]
        trace_lines = []     # 完整轨迹（与终端打印同源收集）
        last_call = None     # 上一次执行的 (工具名, 序列化参数)
        repeat_count = 0     # 连续重复调用计数（仅在当前尝试内生效）

        for iteration in range(max_iterations):
            # 1. 调用 LLM（含重试，携带 tools 参数）
            message = self._call_llm(conversation_history)
            if message is None:
                return None, "\n".join(trace_lines), False

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
                    conversation_history.append({
                        "role": "assistant",
                        "content": answer
                    })
                    answer_line = f"最终答案: {answer}"
                    trace_lines.append(answer_line)
                    print(answer_line)
                    return (self._clean_answer(answer),
                            "\n".join(trace_lines), True)
                # 空回复：提醒模型继续
                observation = "请继续：要么调用工具，要么直接给出最终答案。"
                conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                trace_lines.append(observation)
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

                thought_line = f"Thought: {content or '（调用工具）'}"
                action_line = (f"Action: {tool_name}"
                               f"({json.dumps(tool_args, ensure_ascii=False)})")
                trace_lines.append(thought_line)
                trace_lines.append(action_line)
                print(thought_line)
                print(action_line)
                print("-" * 50)

                # 重复调用检测（连续相同调用第 2 次起警告且不执行，第 3 次硬终止）
                call = (
                    tool_name,
                    json.dumps(tool_args, sort_keys=True, ensure_ascii=False),
                )
                if call == last_call:
                    repeat_count += 1
                    if repeat_count >= _REPEAT_STOP:
                        fail_msg = "未能在规定步骤内完成任务：模型连续重复调用相同工具。"
                        trace_lines.append(fail_msg)
                        print(fail_msg)
                        return fail_msg, "\n".join(trace_lines), False
                    result = (
                        f"警告：你已连续 {repeat_count} 次调用 {tool_name}，"
                        "参数相同，结果不会改变。"
                        "请尝试新的思路，或直接给出最终答案。"
                    )
                else:
                    last_call, repeat_count = call, 1
                    result = self._execute_tool(tool_name, tool_args)

                observation = self._truncate(str(result))
                obs_line = f"Observation: {observation}"
                trace_lines.append(obs_line)
                print(obs_line)
                print("-" * 50)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        fail_msg = "步内迭代次数耗尽，未得出明确结果。"
        trace_lines.append(fail_msg)
        print(fail_msg)
        return fail_msg, "\n".join(trace_lines), False

    def _criticize(
        self, question: str, answer: str, trajectory: str
    ) -> Tuple[bool, str] | None:
        """
        反思阶段：Critic 独立评审本次尝试（看完整轨迹与答案，不带历史反思，
        避免"上次判不通过所以这次也从严"的锚定效应）

        :param question: 原始问题
        :param answer: Actor 本次尝试的最终答案
        :param trajectory: Actor 本次尝试的完整轨迹
        :return: (是否通过, 反馈文本)；Critic 调用彻底失败时返回 None
        """
        messages = [
            {"role": "system", "content": self._build_critic_prompt(
                question, trajectory, answer
            )},
            {"role": "user", "content": "请评审以上回答，输出评审结果 JSON。"},
        ]
        message = self._call_llm(
            messages,
            use_tools=False,
            response_format={"type": "json_object"},
        )
        if message is None:
            return None
        content = message.content or ""
        if isinstance(content, list):  # 防御：个别 provider 返回列表
            content = "".join(
                part.get("text", "") for part in content
            )
        return self._parse_critic_output(content)

    def run(self, question: str, max_attempts: int = _MAX_ATTEMPTS) -> str:
        """
        运行智能体：多轮「尝试 → 反思」直到评审通过或达到最大尝试次数

        :param question: 用户问题
        :param max_attempts: 最大尝试次数
        :return: 最终答案（评审通过时返回该次答案；达到上限时返回最后一次尝试的结果）
        """
        self.reflection_history = []  # 反思记忆在每次 run 开头重置
        last_result = ""

        for attempt in range(1, max_attempts + 1):
            print(f"=== 第 {attempt} 次尝试 ===")
            if self.reflection_history:
                print(f"（携带反思记忆 {len(self.reflection_history)} 条，详见上文反馈）")

            # 1. Actor：带工具的迷你 ReAct 循环
            result, trajectory, completed = self._attempt_once(
                question, self.reflection_history
            )
            if result is None:  # Actor 侧 API 彻底失败
                return "API 调用失败，无法完成任务。"
            if not completed:
                print(f"（本次尝试未在 {_MAX_ATTEMPT_ITERATIONS} 步内完成，交由评审模块评价）")

            # 2. Critic：评审本次尝试
            print()
            print("--- 反思阶段 ---")
            critique = self._criticize(question, result, trajectory)
            if critique is None:  # Critic 侧 API 彻底失败
                return "API 调用失败，无法完成任务。"
            passed, feedback = critique

            # 3. 判定：通过 → 返回答案；不通过 → 积累反思记忆后重试
            if passed:
                print("判定: 通过")
                print("-" * 50)
                return result
            print("判定: 不通过")
            print(f"反馈: {feedback}")
            print("-" * 50)
            self.reflection_history.append(
                f"第 {attempt} 次尝试的结果："
                f"{self._truncate(result, limit=_FEEDBACK_LIMIT)}\n反馈：{feedback}"
            )
            last_result = result

        # 4. 达到最大尝试次数仍未通过
        print(f"经过 {max_attempts} 次尝试仍未通过评审，以下为最后一次尝试的结果：")
        return last_result


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
    agent = ReflectionAgent()

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

    print("=== Reflection 智能体（反思式推理）===")
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
