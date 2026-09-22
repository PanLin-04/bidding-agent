"""
一个编排-执行（Orchestrator-Workers）模式的智能体系统：
编排者（无工具，纯推理）把问题拆解为子任务并分派给各子智能体，
子智能体各司其职独立执行，编排者收集结果后交撰稿人汇总成文。
每个子智能体各自拥有：系统提示词（角色）、工具权限（研究员=search、
计算员=calculator、撰稿人=无工具）、独立上下文（互不可见对方的对话）。

示例问题：
1. 请完成下面两件事，并把结果整合成一段完整的中文回答：1) 搜索 2026 年世界杯冠军球队及其所在的国家；2) 计算 25 乘以 4 再加 100 的结果。
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
_REPEAT_STOP = 3        # 连续重复调用达到 3 次 → 硬终止（作用域为单个子任务内）
# —— 子智能体模式专属常量 ——
_MAX_SUBAGENT_ITERATIONS = 5   # 子智能体单次任务的最大 LLM 迭代次数
_MAX_SUBTASKS = 3              # 子任务数量上限
_MAX_PLAN_RETRIES = 2          # 规划格式失败的最大反馈重试次数
_SUBAGENT_RESULT_LIMIT = 500   # 嵌入撰稿人任务的子任务结果摘要截断长度


# —— 模块级共享函数（编排者与各子智能体共用，文件内共享不跨文件） ——
def _call_llm(
    client: OpenAI,
    model: str,
    messages: list,
    max_retries: int = 2,
    use_tools: bool = True,
    tool_schemas: list | None = None,
    response_format: dict | None = None,
) -> Any | None:
    """
    调用 LLM，带重试与异常处理（编排者与各子智能体共用）

    :param client: OpenAI 客户端
    :param model: 模型名
    :param messages: 消息列表
    :param max_retries: 最大重试次数
    :param use_tools: 是否携带 tools 参数
    :param tool_schemas: OpenAI 兼容的 tools 参数列表（按调用方各自传入）
    :param response_format: 回复格式约束（规划阶段传 {"type": "json_object"}）
    :return: 模型回复的 message 对象（含 content 与 tool_calls），失败时返回 None
    """
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                # 无工具/未启用时省略该参数
                tools=(tool_schemas or None) if use_tools else None,
                # json_object 模式要求提示词含 "json"
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


def _clean_answer(text: str) -> str:
    """
    清理最终答案中的代码围栏

    :param text: 模型回复原文
    :return: 干净的答案文本
    """
    answer = re.sub(r"^```[^\n]*\n?", "", text.strip())
    answer = re.sub(r"```\s*$", "", answer).strip()
    return answer


def _truncate(s: str, limit: int = _MAX_OBSERVATION) -> str:
    """
    截断过长的文本，防止上下文膨胀

    :param s: 原始字符串
    :param limit: 最大长度
    :return: 截断后的字符串
    """
    return s if len(s) <= limit else s[:limit] + "...（已截断）"


class Subagent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        client: OpenAI,
        model: str = "deepseek-v4-flash",
    ):
        self.name = name
        self.system_prompt = system_prompt  # 角色系统提示词
        self.client = client                # 与编排者共享同一个客户端
        self.model = model
        self.tools: Dict[str, Callable] = {}
        self.tool_schemas: list = []  # 只含本子智能体注册的工具

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: dict | None = None,
    ):
        """
        注册一个工具，供本子智能体调用

        :param name: 工具名称
        :param func: 工具函数，接收关键字参数并返回结果
        :param description: 工具描述，用于子智能体判断何时调用
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

    def _build_system_prompt(self, task: str) -> str:
        """
        构建子智能体的系统提示：角色提示词 + 可用工具 + 规则 + 当前任务。
        会话在每次 execute 时全新构造，不包含其他子智能体的对话——上下文隔离。

        :param task: 分配的子任务描述
        :return: 子智能体的系统提示
        """
        if self.tools:
            tool_descriptions = "\n".join([
                f"- {name}: {tool['description']}"
                for name, tool in self.tools.items()
            ])
        else:
            tool_descriptions = "（本子智能体没有工具）"

        return f"""{self.system_prompt}

可用工具：
{tool_descriptions}

规则：
1. 需要工具时调用工具，参数严格按工具定义生成
2. 不要重复调用已经调用过且参数相同的工具，结果不会改变
3. 工具结果返回后继续推理；信息足够时直接给出结果，不要再调用工具
4. 不要编造不存在的工具
5. 你只负责当前任务，完成任务后直接输出结果，不要处理任务之外的内容

当前任务：{task}
"""

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
                    "请改用可用工具，或直接给出结果。")
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

    def execute(
        self, task: str, max_iterations: int = _MAX_SUBAGENT_ITERATIONS
    ) -> Tuple[str, bool]:
        """
        执行一个分配的子任务：跑一个带本子智能体工具的迷你 ReAct 循环。
        过程打印 Thought/Action/Observation，最终结果行由编排者统一打印。

        :param task: 子任务描述
        :param max_iterations: 最大 LLM 迭代次数
        :return: (结果文本, 是否正常完成)
        """
        conversation_history = [
            {"role": "system", "content": self._build_system_prompt(task)},
            {"role": "user", "content": f"请完成以下任务：{task}"},
        ]
        last_call = None      # 上一次执行的 (工具名, 序列化参数)
        repeat_count = 0      # 连续重复调用计数（仅在当前子任务内生效）

        for iteration in range(max_iterations):
            # 1. 调用 LLM（含重试，携带本子智能体的 tools 参数）
            message = _call_llm(
                self.client, self.model, conversation_history,
                tool_schemas=self.tool_schemas,
            )
            if message is None:
                return "API 调用失败，无法完成此任务。", False

            tool_calls = list(message.tool_calls or [])
            content = message.content
            if isinstance(content, list):  # 防御：个别 provider 返回列表
                content = "".join(
                    part.get("text", "") for part in content
                )

            # 2. 没有工具调用 → 模型直接给出结果
            if not tool_calls:
                result = (content or "").strip()
                if result:
                    conversation_history.append({
                        "role": "assistant",
                        "content": result
                    })
                    return _clean_answer(result), True
                # 空回复：提醒模型继续
                observation = "请继续：要么调用工具，要么直接给出结果。"
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
                        "请尝试新的思路，或直接给出结果。"
                    )
                else:
                    last_call, repeat_count = call, 1
                    result = self._execute_tool(tool_name, tool_args)

                observation = _truncate(str(result))
                print(f"Observation: {observation}")
                print("-" * 50)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        return "子任务迭代次数耗尽，未得出明确结果。", False


class Orchestrator:
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
        # 编排者没有任何工具属性——工具权限隔离是结构性的，不是提示词层面的
        self.agents: Dict[str, Subagent] = {}  # 子智能体注册表

    def add_agent(self, agent: Subagent):
        """
        注册一个子智能体（重名覆盖）

        :param agent: 子智能体实例
        """
        self.agents[agent.name] = agent

    def _build_plan_prompt(self, question: str) -> str:
        """
        构建编排者的规划提示：拆解问题并分派给合适的子智能体

        :param question: 用户问题
        :return: 规划系统提示
        """
        return f"""你是一个多智能体系统的编排者（Orchestrator），负责把用户问题拆解为若干子任务，并分派给合适的子智能体执行。
你只负责规划与调度，不亲自回答问题，也不调用工具。

可用的子智能体（子任务只能分派给以下角色，角色名必须完全一致）：
- 研究员：拥有网络搜索工具 search，负责查找事实与数据
- 计算员：拥有计算器工具 calculator，负责数学计算
- 撰稿人：没有工具，负责把已有材料整理成文（汇总任务由编排者自动分派，你不需要为它规划子任务）

规划要求：
1. 把问题拆解为需要不同能力的独立子任务，每个子任务只属于一个子智能体
2. 只需要规划「信息收集类」子任务（研究员 / 计算员）；不要规划撰稿人的任务
3. 子任务描述必须完整自足：子智能体看不到其他子智能体的对话，任务描述要包含回答问题所需的全部背景信息

输出要求（严格遵守）：
- 输出一个合法的 JSON 对象，形如：
  {{"subtasks": [{{"agent": "研究员", "task": "请搜索 2026 年世界杯冠军球队及其所在的国家"}}, {{"agent": "计算员", "task": "请计算 25 乘以 4 再加 100 的结果"}}]}}
- subtasks 是数组，每项包含 agent（必须是上面的角色名之一）与 task（具体任务描述）
- 1 到 3 个子任务为宜
- 只输出 JSON 本身，不要输出任何其他解释或代码围栏

当前问题：{question}
"""

    def _parse_subtasks(self, text: str) -> List[Tuple[str, str]]:
        """
        从模型回复中解析子任务列表（跳过未知角色与「撰稿人」）

        :param text: 模型回复原文
        :return: (子智能体名, 任务描述) 列表
        """
        cleaned = _clean_answer(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        # 兼容两种结构：{"subtasks": [...]} 或直接数组 [...]
        if isinstance(data, dict):
            items = data.get("subtasks") or data.get("tasks") or []
        elif isinstance(data, list):
            items = data
        else:
            return []
        if not isinstance(items, list):
            return []

        subtasks: List[Tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            agent_name = str(item.get("agent", "")).strip()
            task = str(item.get("task", "")).strip()
            if not task:
                continue
            # 角色匹配：先精确匹配，再包含匹配（如「研究员（搜索）」也能命中）
            matched = None
            if agent_name in self.agents:
                matched = agent_name
            else:
                for name in self.agents:
                    if agent_name and (name in agent_name or agent_name in name):
                        matched = name
                        break
            if matched is None:
                print(f"跳过未知子智能体「{agent_name}」")
                continue
            if matched == "撰稿人":
                print("跳过「撰稿人」子任务（汇总任务由编排者自动分派）")
                continue
            subtasks.append((matched, task))

        if len(subtasks) > _MAX_SUBTASKS:
            print(f"子任务超过 {_MAX_SUBTASKS} 个，仅保留前 {_MAX_SUBTASKS} 个")
            subtasks = subtasks[:_MAX_SUBTASKS]
        return subtasks

    def _make_plan(self, question: str) -> List[Tuple[str, str]] | None:
        """
        编排阶段：LLM 规划子任务分派；格式错误时追加反馈重试，
        仍失败则退化为单子任务（问题整体交给研究员，等价于 ReAct）

        :param question: 用户问题
        :return: (子智能体名, 任务描述) 列表；API 彻底失败时返回 None
        """
        messages = [
            {"role": "system", "content": self._build_plan_prompt(question)},
        ]
        for attempt in range(_MAX_PLAN_RETRIES + 1):
            message = _call_llm(
                self.client, self.model, messages,
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
            subtasks = self._parse_subtasks(content)
            if subtasks:
                return subtasks
            if attempt < _MAX_PLAN_RETRIES:
                # 格式不符：把模型原文写回历史并追加反馈，随后重试
                print(f"规划格式不正确（第 {attempt + 1} 次），追加反馈后重试")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "规划格式不正确：请输出合法的 JSON 对象，"
                               "形如 {\"subtasks\": [...]}，"
                               "agent 必须是研究员/计算员之一，只输出 JSON 本身。",
                })
        # 重试耗尽仍无法解析：退化为单子任务
        print("规划解析失败，将问题整体交给研究员执行")
        fallback_name = (
            "研究员" if "研究员" in self.agents else next(iter(self.agents))
        )
        return [(fallback_name, question)]

    def _dispatch(
        self, index: int, total: int, agent_name: str, task: str
    ) -> Tuple[str, bool]:
        """
        把一个子任务分派给对应子智能体执行并打印结果。
        （若要并行分派：可用 ThreadPoolExecutor 并行执行各子智能体的
        execute，注意打印需加锁——教学演示保持顺序执行，输出更清晰）

        :param index: 子任务序号（从 1 开始）
        :param total: 子任务总数
        :param agent_name: 子智能体名
        :param task: 子任务描述
        :return: (结果文本, 是否正常完成)
        """
        print(f"--- 子智能体「{agent_name}」执行中（{index}/{total}）---")
        agent = self.agents[agent_name]
        result, ok = agent.execute(task)
        print(f"子智能体「{agent_name}」的结果: "
              f"{_truncate(result, limit=_SUBAGENT_RESULT_LIMIT)}")
        return result, ok

    def _build_final_prompt(
        self, question: str, results: List[str]
    ) -> str:
        """
        构建分派给撰稿人的汇总任务文本（材料是撰稿人唯一的事实来源）

        :param question: 原始问题
        :param results: 各子智能体的结果摘要列表
        :return: 撰稿人任务文本
        """
        results_text = "\n".join(results)

        return f"""请根据以下材料，用一段通顺、完整的中文回答原始问题。
材料来自其他子智能体的执行结果，是你唯一的事实来源；你没有工具，不得编造材料之外的信息。

原始问题：{question}

材料：
{results_text}

要求：
1. 综合所有材料，给出完整、准确的回答
2. 某项材料缺失或标注失败时，说明缺失原因，并基于已有材料尽量回答
3. 直接输出最终文字，不要输出过程性描述
"""

    def _build_summary_prompt(
        self, question: str, results: List[str]
    ) -> str:
        """
        构建编排者自行汇总的系统提示（撰稿人失败时的兜底）

        :param question: 原始问题
        :param results: 各子智能体的结果摘要列表
        :return: 汇总系统提示
        """
        results_text = "\n".join(results)

        return f"""你是一个多智能体系统的编排者（Orchestrator）。撰稿人子智能体执行失败，
请基于以下各子智能体的执行结果，直接汇总出最终答案。

原始问题：{question}

各子智能体执行结果：
{results_text}

要求：
1. 汇总所有结果，给出完整、准确的最终答案
2. 某一步失败或信息不足时，基于已有信息尽量回答；确实无法回答时，明确说明
3. 直接输出最终答案，不要输出过程性描述
"""

    def _dispatch_synthesis(
        self, question: str, results: List[str]
    ) -> str | None:
        """
        汇总阶段：把各子任务结果作为材料分派给「撰稿人」整理成文

        :param question: 原始问题
        :param results: 各子智能体的结果摘要列表
        :return: 撰稿人成文结果；撰稿人未注册或执行失败时返回 None
        """
        if "撰稿人" not in self.agents:
            print("未注册「撰稿人」子智能体，改由编排者自行汇总")
            return None
        print("--- 子智能体「撰稿人」执行中（汇总）---")
        task = self._build_final_prompt(question, results)
        result, ok = self.agents["撰稿人"].execute(task)
        if not ok:
            return None
        print(f"子智能体「撰稿人」的结果: "
              f"{_truncate(result, limit=_SUBAGENT_RESULT_LIMIT)}")
        return result

    def _make_final_answer(
        self, question: str, results: List[str]
    ) -> str:
        """
        编排者自行汇总（撰稿人失败时的兜底）；再失败则降级为拼接结果摘要

        :param question: 原始问题
        :param results: 各子智能体的结果摘要列表
        :return: 最终答案
        """
        messages = [
            {"role": "system", "content": self._build_summary_prompt(
                question, results
            )},
            {"role": "user", "content": "请给出最终答案。"},
        ]
        message = _call_llm(
            self.client, self.model, messages, use_tools=False,
        )
        if message is None:
            return "（汇总调用失败，以下为各子智能体结果摘要）\n" + "\n".join(results)
        content = message.content or ""
        if isinstance(content, list):  # 防御：个别 provider 返回列表
            content = "".join(
                part.get("text", "") for part in content
            )
        answer = _clean_answer(content)
        if answer:
            return answer
        return "（汇总调用失败，以下为各子智能体结果摘要）\n" + "\n".join(results)

    def run(self, question: str) -> str:
        """
        运行多智能体系统：编排（规划分派）→ 执行（子智能体逐个执行）→ 汇总

        :param question: 用户问题
        :return: 最终答案
        """
        if not self.agents:
            return "没有可用子智能体，无法分派任务。"

        # 阶段一：编排
        print("=== 编排阶段 ===")
        plan = self._make_plan(question)
        if plan is None:
            return "API 调用失败，无法完成任务。"
        print("生成的分派计划：")
        for i, (agent_name, task) in enumerate(plan, 1):
            print(f"{i}. [{agent_name}] {task}")

        # 阶段二：执行
        print()
        print("=== 执行阶段 ===")
        results = []
        for i, (agent_name, task) in enumerate(plan, 1):
            result, _ = self._dispatch(i, len(plan), agent_name, task)
            summary = _truncate(result, limit=_SUBAGENT_RESULT_LIMIT)
            results.append(f"{agent_name}（{task}）：{summary}")
            print()

        # 阶段三：汇总
        print("=== 汇总阶段 ===")
        answer = self._dispatch_synthesis(question, results)
        if answer is None:
            answer = self._make_final_answer(question, results)
        return answer


# —— 预定义子智能体的角色系统提示词 ——
RESEARCHER_SYSTEM_PROMPT = """你是一名网络研究员，是「子智能体」体系中的信息收集专家，擅长通过搜索引擎查找并核实事实。
你的职责：根据分配的子任务，搜索相关资料，提取准确、可核实的事实与数据。

规则：
1. 所有事实与数据必须通过 search 工具获取，严禁凭记忆或推测编造
2. 搜索关键词要具体；一次搜索不满足任务要求时，更换关键词继续搜索
3. 信息足够后，用简洁的中文总结发现，列出关键事实与来源要点，直接输出为最终结果
4. 你只有 search 一个工具，不要尝试调用其他工具"""

CALCULATOR_SYSTEM_PROMPT = """你是一名数学计算员，是「子智能体」体系中的计算专家，擅长精确的数学计算。
你的职责：根据分配的子任务，把任务拆解为清晰的算式，用 calculator 工具逐步计算。

规则：
1. 所有计算必须通过 calculator 工具完成，严禁心算或凭感觉给结果
2. 表达式按运算符优先级写清（如 25*4+100），必要时分步计算并交叉验证
3. 计算完成后，给出算式与最终结果，直接输出为最终结果
4. 你只有 calculator 一个工具，不要尝试调用其他工具"""

WRITER_SYSTEM_PROMPT = """你是一名中文撰稿人，是「子智能体」体系中的文字整合专家，擅长把零散材料整理成通顺、完整的文章。
你的职责：根据分配的子任务与材料，组织成结构化、条理清晰的中文回答。

规则：
1. 你没有工具，只能依据任务描述中提供的材料作答，严禁编造材料中不存在的数字与事实
2. 材料不足时，明确说明缺少哪部分信息，再基于已有材料尽量回答
3. 直接输出可交付的最终文字，不要输出思考过程或过程性描述"""


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
    orchestrator = Orchestrator()

    # 研究员：只有 search 工具
    researcher = Subagent(
        "研究员", RESEARCHER_SYSTEM_PROMPT, orchestrator.client
    )
    researcher.register_tool(
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
    orchestrator.add_agent(researcher)

    # 计算员：只有 calculator 工具
    calculator_agent = Subagent(
        "计算员", CALCULATOR_SYSTEM_PROMPT, orchestrator.client
    )
    calculator_agent.register_tool(
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
    orchestrator.add_agent(calculator_agent)

    # 撰稿人：不注册任何工具
    writer = Subagent("撰稿人", WRITER_SYSTEM_PROMPT, orchestrator.client)
    orchestrator.add_agent(writer)

    print("=== 编排-执行（Orchestrator-Workers）模式 ===")
    print("可用子智能体: 研究员(工具: search), 计算员(工具: calculator), 撰稿人(无工具)")
    print()

    question = input("请输入你的问题: ")
    print()

    result = orchestrator.run(question)
    print()
    print("最终结果:")
    print(result)


if __name__ == "__main__":
    main()
