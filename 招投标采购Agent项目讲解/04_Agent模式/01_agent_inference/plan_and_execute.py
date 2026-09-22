"""
一个Plan-and-Execute模式的智能体：先制定完整执行计划 → 严格按计划逐步执行 →
执行中某步失败时重新规划（只修订剩余步骤）→ 继续执行 → 最终汇总。
计划格式：JSON 数组（response_format 强制输出）

与 plan_and_solve_json.py 的核心区别：执行失败会触发重新规划，
重规划次数上限为 _MAX_REPLANS（置 0 则行为等价于 Plan-and-Solve）。
重规划为确定性触发：仅当某步执行失败（API 失败 / 连续重复调用硬终止 /
步内迭代耗尽）时发生。想稳定演示重规划路径：临时把 _MAX_STEP_ITERATIONS
调小为 1 后提问（需要工具调用的步骤第一轮必然迭代耗尽 → 必触发重规划）。

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
# —— Plan-and-Execute 专属常量 ——
_MAX_PLAN_STEPS = 8       # 计划步骤数上限
_MAX_PLAN_RETRIES = 2     # 计划格式失败的最大反馈重试次数（初始规划与重规划共用）
_MAX_STEP_ITERATIONS = 5  # 单步执行的最大 LLM 迭代次数，默认 5 次
_STEP_RESULT_LIMIT = 500  # 跨步传递的步骤结果摘要截断长度
_MAX_REPLANS = 2          # 重规划次数上限（置 0 则退化为 Plan-and-Solve）


class PlanExecuteAgent:
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
        return f"""你是一个智能助手，使用 Plan-and-Execute（先制定完整计划、再严格按计划逐步执行）模式解决问题。

现在请先制定计划：把问题拆解为若干可执行的步骤，每步只做一件事，
可以借助工具（计算、搜索），也可以直接推理。

输出要求（严格遵守）：
- 输出一个合法的 JSON 对象，形如：
  {{"plan": ["搜索 2026 年世界杯冠军所在的国家", "搜索该国 2025 年的 GDP"]}}
- plan 字段是一个字符串数组，每项是一个步骤描述
- 步骤描述要具体，说明要搜索什么、计算什么
- 3 到 6 步为宜，最多不超过 8 步
- 只输出 JSON 本身，不要输出任何其他解释或代码围栏

当前问题：{question}
"""

    def _build_replan_prompt(
        self,
        question: str,
        current_plan: List[str],
        done_results: List[str],
        failed_step: str,
        failed_reason: str,
    ) -> str:
        """
        构建重规划阶段的系统提示：给出原始问题、当前计划、已完成步骤结果、
        失败步骤与失败原因，要求只输出修订后的剩余计划

        :param question: 原始问题（目标不变）
        :param current_plan: 当前计划（含已完成部分，用于定位失败步）
        :param done_results: 已成功执行步骤的结果摘要列表（不含失败步）
        :param failed_step: 失败步骤的描述文本
        :param failed_reason: 失败原因文本（_execute_step 的失败返回）
        :return: 重规划阶段的系统提示
        """
        plan_text = "\n".join(
            f"{i}. {s}" for i, s in enumerate(current_plan, 1)
        )
        done_text = "\n".join(done_results) if done_results else "（暂无）"

        return f"""你是一个智能助手，正在使用 Plan-and-Execute 模式解决问题。
此前制定的计划在执行中遇到了失败，请重新规划【剩余】的执行计划。

原始问题（最终目标不变）：{question}

既定计划：
{plan_text}

已成功完成的步骤及结果：
{done_text}

执行失败的步骤：
- 步骤内容：{failed_step}
- 失败原因：{failed_reason}

请重新规划（严格遵守）：
- 输出一个合法的 JSON 对象，形如：
  {{"plan": ["……", "……"]}}
- plan 字段是一个字符串数组，每项是一个步骤描述，要具体到搜索什么、计算什么
- 【关键】只输出剩余需要执行的步骤：已成功完成的步骤不要再列出；
  失败的步骤必须用新的方法、新的工具或新的切入点重新安排，作为计划的第一步
- 换用与刚才不同但更可能成功的做法（如更换关键词、更换工具、拆成更小步骤）
- 3 到 6 步为宜，最多不超过 8 步
- 只输出 JSON 本身，不要输出任何其他解释或代码围栏
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
        :param use_tools: 是否携带 tools 参数（规划/重规划/汇总阶段为 False）
        :param response_format: 回复格式约束（规划/重规划阶段传 {"type": "json_object"}）
        :return: 模型回复的 message 对象（含 content 与 tool_calls），失败时返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    # 规划/重规划/汇总阶段不携带工具；未注册工具时省略该参数
                    tools=(self.tool_schemas or None) if use_tools else None,
                    # 规划/重规划阶段强制 JSON 输出（json_object 模式要求提示词含 "json"）
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
        从模型回复中解析 JSON 计划

        :param text: 模型回复原文
        :return: 步骤描述列表（解析失败时为空列表）
        """
        cleaned = self._clean_answer(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        # 兼容两种结构：{"plan": [...]} 或直接数组 [...]
        if isinstance(data, dict):
            steps = data.get("plan") or data.get("steps") or []
        elif isinstance(data, list):
            steps = data
        else:
            return []
        if not isinstance(steps, list):
            return []
        return [str(s).strip() for s in steps if str(s).strip()]

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
                    "content": "计划格式不正确：请输出合法的 JSON 对象，"
                               "形如 {\"plan\": [\"步骤1\", \"步骤2\"]}。"
                               "只输出 JSON 本身。",
                })
        # 重试耗尽仍无法解析：退化为单步执行
        print("计划解析失败，将问题整体作为单步执行")
        return [question]

    def _make_replan(
        self,
        question: str,
        current_plan: List[str],
        done_results: List[str],
        failed_step: str,
        failed_reason: str,
    ) -> List[str] | None:
        """
        重规划阶段：调用 LLM 生成修订后的剩余计划。
        复用 _parse_plan 与格式反馈重试（上限 _MAX_PLAN_RETRIES）。
        与 _make_plan 的区别：失败时不退化单步，而是返回 None
        （由 run 决定按原计划继续执行，避免重复触发重规划）。

        :param question: 原始问题
        :param current_plan: 当前计划
        :param done_results: 已成功执行步骤的结果摘要列表（不含失败步）
        :param failed_step: 失败步骤的描述文本
        :param failed_reason: 失败原因文本
        :return: 修订后的剩余步骤列表；API 失败或解析失败时返回 None
        """
        messages = [
            {"role": "system", "content": self._build_replan_prompt(
                question, current_plan, done_results, failed_step,
                failed_reason
            )},
        ]
        for attempt in range(_MAX_PLAN_RETRIES + 1):
            message = self._call_llm(
                messages,
                use_tools=False,
                response_format={"type": "json_object"},
            )
            if message is None:
                return None  # API 失败：按原计划继续执行
            content = message.content or ""
            if isinstance(content, list):  # 防御：个别 provider 返回列表
                content = "".join(
                    part.get("text", "") for part in content
                )
            steps = self._parse_plan(content)
            if steps:
                if len(steps) > _MAX_PLAN_STEPS:
                    print(f"修订计划超过 {_MAX_PLAN_STEPS} 步，仅保留前 {_MAX_PLAN_STEPS} 步")
                    steps = steps[:_MAX_PLAN_STEPS]
                return steps
            if attempt < _MAX_PLAN_RETRIES:
                # 格式不符：把模型原文写回历史并追加反馈，随后重试
                print(f"修订计划格式不正确（第 {attempt + 1} 次），追加反馈后重试")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "修订计划格式不正确：请输出合法的 JSON 对象，"
                               "形如 {\"plan\": [\"步骤1\", \"步骤2\"]}。"
                               "只输出 JSON 本身。",
                })
        # 重试耗尽仍无法解析：返回 None（区别于 _make_plan 的退化单步）
        print("修订计划解析失败，按原计划继续执行")
        return None

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
        运行智能体：规划 → 逐步执行（失败触发重规划）→ 汇总。
        执行视图 plan_view 全局连续编号；重规划只替换失败步及其之后的剩余
        计划（已完成结果存于 done_results 独立保留，绝不重跑）。
        循环终止性：每次迭代必然推进（成功 next_index+1；失败且重规划成功
        → 视图替换、next_index 不变但该位置是新步；失败且达上限或重规划失败
        → next_index+1），replan_count 单调递增到 _MAX_REPLANS。

        :param question: 用户问题
        :return: 最终答案
        """
        # 阶段一：初始规划
        print("=== 规划阶段 ===")
        plan = self._make_plan(question)
        if plan is None:
            return "API 调用失败，无法完成任务。"
        print("生成的分步计划：")
        for i, step in enumerate(plan, 1):
            print(f"{i}. {step}")

        # 阶段二：计划-执行-修订闭环
        print()
        print("=== 执行阶段 ===")
        plan_view = list(plan)  # 当前完整执行视图（全局连续编号 1..n）
        done_results = []       # 已执行步骤结果摘要（含失败记录）
        next_index = 1          # 下一步全局编号
        replan_count = 0        # 已重规划次数

        while next_index <= len(plan_view):
            step = plan_view[next_index - 1]
            print(f"--- 执行步骤 {next_index}/{len(plan_view)}: {step} ---")
            result, ok = self._execute_step(
                next_index, step, plan_view, done_results
            )
            if ok:
                summary = self._truncate(result, limit=_STEP_RESULT_LIMIT)
                done_results.append(f"步骤 {next_index}：{summary}")
                print()
                next_index += 1
                continue

            # —— 本步失败：记录后触发重规划 ——
            # 触发条件为确定性失败（API 失败/重复调用/迭代耗尽）；
            # 若要更早介入（某步结果质量差但未报错），可在此追加
            # LLM 可达性判定，属进阶扩展。
            summary = self._truncate(result, limit=_STEP_RESULT_LIMIT)
            done_results.append(f"步骤 {next_index} 执行失败：{summary}")
            print(f"步骤 {next_index} 失败：{summary}")
            print()

            if replan_count >= _MAX_REPLANS:
                print(f"（重规划次数已达上限 {_MAX_REPLANS}，按原计划继续执行后续步骤）")
                print()
                next_index += 1
                continue

            # —— 重规划：交回 Planner 修订剩余计划 ——
            replan_count += 1
            print("=== 重新规划 ===")
            revised = self._make_replan(
                question, plan_view, done_results[:-1],  # 失败步单独传
                failed_step=step, failed_reason=summary
            )
            if not revised:  # API 失败 / 解析失败：按原计划继续执行
                print("（重新规划失败，按原计划继续执行后续步骤）")
                print()
                next_index += 1
                continue
            print(f"修订后的剩余计划（第 {replan_count} 次重规划）：")
            for j, s in enumerate(revised, 1):
                print(f"{j}. {s}")
            print()
            # 失败步被修订计划覆盖：视图 = 已成功步 + 修订剩余计划；
            # next_index 不变——视图的新第 next_index 步即修订计划的重做第一步
            plan_view = plan_view[:next_index - 1] + revised

        # 阶段三：汇总
        print("=== 汇总阶段 ===")
        return self._make_final_answer(question, plan_view, done_results)


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
    agent = PlanExecuteAgent()

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

    print("=== Plan-and-Execute 智能体 ===")
    print("可用工具: calculator, search")
    print(f"（重规划：某步执行失败时自动修订剩余计划，上限 {_MAX_REPLANS} 次；"
          "想稳定演示重规划，可临时把 _MAX_STEP_ITERATIONS 调小为 1 后提问）")
    print()

    question = input("请输入你的问题: ")
    print()

    result = agent.run(question)
    print()
    print("最终结果:")
    print(result)


if __name__ == "__main__":
    main()
