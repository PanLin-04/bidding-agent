"""
一个 Agent Parallelization（并行化）模式的智能体系统：
编排者把问题拆解为相互独立的子任务，用线程池并行分派给搜索员们同时执行
（fan-out），全部完成后由撰稿人子智能体把结果汇总成最终答案（fan-in）。
与 orchestrator_workers.py 的对照：后者按角色分工（研究员/计算员/撰稿人）顺序分派；
本版并行搜索阶段的子智能体完全同构——都是只有 search 一个工具的「搜索员」，
每个子任务动态创建一个新实例——拆解的意义不是按能力分工，而是把互不依赖的
事实查询拆开同时进行。

三个教学点：
1. 计算进线程池，输出归主线程——每个子任务的 Thought/Action/Observation 经注入的
   log_fn 写入各自的 StringIO，主线程按提交顺序回放，全程零锁零交错；
   （若改为实时打印方案，则需 threading.Lock 包住每个 print）
2. 总耗时从「各任务之和」压缩为「≈ 最慢子任务」；
3. _call_llm 与 Tavily 均为阻塞 I/O，线程等待期间释放 GIL，线程池即可获得真实并行，
   无需多进程；OpenAI 同步客户端多线程并发发请求是安全的。

子任务相互独立是并行化的前提：有依赖的子任务只能顺序执行（请参见 orchestrator_workers.py）。

示例问题：
1. 2026 年世界杯冠军和亚军球队及其所在的国家
"""
import inspect
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Tuple

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI, OpenAIError

_ = load_dotenv(find_dotenv())

# —— 循环稳定性常量（与 react_tools.py / orchestrator_workers.py 保持一致） ——
_MAX_OBSERVATION = 1500  # 工具结果截断长度（搜索类工具需要更大空间）
_REPEAT_STOP = 3        # 连续重复调用达到 3 次 → 硬终止（作用域为单个子任务内）
# —— 并行化模式专属常量 ——
_MAX_SUBAGENT_ITERATIONS = 5   # 搜索员单次任务的最大 LLM 迭代次数
_MAX_TASKS = 3                 # 任务数上限（对应 orchestrator_workers 的 _MAX_SUBTASKS，
                               # 本版输出已是纯任务，无「子智能体角色」概念）
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
    log_fn: Callable[[str], None] = print,
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
    :param log_fn: 日志输出函数；并行执行时注入各子任务自己的缓冲区，
                   让「输出归主线程」成为可能（默认直接打印）
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
            log_fn(f"模型回复为空（第 {attempt + 1}/{max_retries + 1} 次），准备重试")
        except OpenAIError as e:
            log_fn(f"API 调用失败（第 {attempt + 1}/{max_retries + 1} 次）: {e}")
        except Exception as e:
            log_fn(f"调用 LLM 时发生未知错误: {e}")
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
        self,
        task: str,
        max_iterations: int = _MAX_SUBAGENT_ITERATIONS,
        log_fn: Callable[[str], None] = print,
    ) -> Tuple[str, bool]:
        """
        执行一个分配的子任务：跑一个带本子智能体工具的迷你 ReAct 循环。
        过程打印 Thought/Action/Observation，最终结果行由编排者统一打印。
        （与 orchestrator_workers.py 共享机制的唯一差异点：print → log_fn 注入，
        并行执行时由编排者注入各任务自己的缓冲区）

        :param task: 子任务描述
        :param max_iterations: 最大 LLM 迭代次数
        :param log_fn: 日志输出函数（并行执行时由编排者注入缓冲区）
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
                log_fn=log_fn,
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
                log_fn(observation)
                log_fn("-" * 50)
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

                log_fn(f"Thought: {content or '（调用工具）'}")
                log_fn(f"Action: {tool_name}"
                       f"({json.dumps(tool_args, ensure_ascii=False)})")
                log_fn("-" * 50)

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
                log_fn(f"Observation: {observation}")
                log_fn("-" * 50)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        return "子任务迭代次数耗尽，未得出明确结果。", False


# —— 预定义子智能体的角色系统提示词 ——
SEARCHER_SYSTEM_PROMPT = """你是一名搜索员，是「并行子智能体」体系中的信息收集专家，擅长通过搜索引擎查找并核实事实。
你的职责：完成分配给你的搜索任务，提取准确、可核实的事实与数据。

规则：
1. 所有事实与数据必须通过 search 工具获取，严禁凭记忆或推测编造
2. 搜索关键词要具体；一次搜索不满足任务要求时，更换关键词继续搜索
3. 信息足够后，用简洁的中文总结发现，列出关键事实与来源要点，直接输出为最终结果
4. 你只有 search 一个工具，不要尝试调用其他工具
5. 当前有多个搜索员在并行执行各自的任务，彼此看不到对方的对话——
   你的最终结果会被单独交给撰稿人汇总，必须完整自足：把结论与依据写全，不要引用看不到的材料"""

WRITER_SYSTEM_PROMPT = """你是一名中文撰稿人，是「子智能体」体系中的文字整合专家，擅长把零散材料整理成通顺、完整的文章。
你的职责：根据分配的子任务与材料，组织成结构化、条理清晰的中文回答。

规则：
1. 你没有工具，只能依据任务描述中提供的材料作答，严禁编造材料中不存在的数字与事实
2. 材料不足时，明确说明缺少哪部分信息，再基于已有材料尽量回答
3. 直接输出可交付的最终文字，不要输出思考过程或过程性描述"""


def _make_searcher(
    name: str, client: OpenAI, model: str = "deepseek-v4-flash"
) -> Subagent:
    """
    工厂：创建一个只有 search 工具的搜索员子智能体。
    每个子任务动态创建一个新实例（1:1 映射）——同构 worker 池无需注册表；
    并发安全的根源 = 无共享写：每个实例独享自己的对话历史与工具表。

    :param name: 子智能体名（如 搜索员-1）
    :param client: 编排者共享的 OpenAI 客户端
    :param model: 模型名
    :return: 已注册 search 工具的搜索员实例
    """
    searcher = Subagent(name, SEARCHER_SYSTEM_PROMPT, client, model)
    searcher.register_tool(
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
    return searcher


def _make_writer(
    client: OpenAI, model: str = "deepseek-v4-flash"
) -> Subagent:
    """
    工厂：创建撰稿人子智能体（不注册任何工具）——fan-in 汇总专用，
    顺序执行于全部搜索任务完成之后。

    :param client: 编排者共享的 OpenAI 客户端
    :param model: 模型名
    :return: 无工具的撰稿人实例
    """
    return Subagent("撰稿人", WRITER_SYSTEM_PROMPT, client, model)


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


class ParallelOrchestrator:
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
        # 编排者没有任何工具属性——工具权限隔离是结构性的，不是提示词层面的。
        # 无子智能体注册表：搜索员由 _execute_one 按任务动态创建（同构 worker 池）

    def _build_plan_prompt(self, question: str) -> str:
        """
        构建编排者的规划提示：拆解问题为相互独立的搜索任务并并行分派

        :param question: 用户问题
        :return: 规划系统提示
        """
        return f"""你是一个多智能体系统的编排者（Orchestrator），负责把用户问题拆解为若干相互独立的子任务，
并分派给搜索员们**并行**执行。你只负责规划与调度，不亲自回答问题，也不调用工具。

子智能体情况（重要）：并行搜索阶段没有角色分工——所有搜索员都是完全相同的「搜索员」，
每个都只有 search 一个工具。拆解的意义不是按能力分工，而是把一次回答拆成多个
**互不依赖的事实查询**同时进行，把总耗时压缩到最慢的那一个。
（汇总由撰稿人负责，编排者自动分派，你不需要为它规划任务。）

规划要求：
1. 每个子任务 = 一个独立的事实查询（如「搜索 XX」「查找 XX」），子任务之间相互独立：
   互不依赖、没有先后顺序、可以同时执行
2. **如果查询之间存在依赖（一个查询需要另一个查询的结果才能进行），不要拆开**——
   把它们合并为一个任务，由同一个搜索员按顺序完成
3. 子任务描述必须完整自足：搜索员互相看不到对方的对话，也无法中途参考其他搜索员的
   结果，任务描述要包含完成查询所需的全部信息
4. 1 到 {_MAX_TASKS} 个任务为宜；最多不超过 {_MAX_TASKS} 个

输出要求（严格遵守）：
- 输出一个合法的 JSON 对象，形如：
  {{"tasks": ["请搜索2026年世界杯冠军球队及其所在的国家", "请搜索2026年世界杯亚军球队及其所在的国家"]}}
- tasks 是数组，每一项是一条任务描述字符串
- 只输出 JSON 本身，不要输出任何其他解释或代码围栏

当前问题：{question}
"""

    def _parse_tasks(self, text: str) -> List[str]:
        """
        从模型回复中解析任务数组

        :param text: 模型回复原文
        :return: 任务描述列表（解析失败时为空列表）
        """
        cleaned = _clean_answer(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        # 兼容三种结构：{"tasks": [...]} / {"subtasks": [...]} / 直接数组 [...]
        if isinstance(data, dict):
            items = data.get("tasks") or data.get("subtasks") or []
        elif isinstance(data, list):
            items = data
        else:
            return []
        if not isinstance(items, list):
            return []

        tasks = [str(item).strip() for item in items
                 if isinstance(item, str) and item.strip()]
        if len(tasks) > _MAX_TASKS:
            print(f"子任务超过 {_MAX_TASKS} 个，仅保留前 {_MAX_TASKS} 个")
            tasks = tasks[:_MAX_TASKS]
        return tasks

    def _make_plan(self, question: str) -> List[str] | None:
        """
        编排阶段：LLM 规划并行搜索任务；格式错误时追加反馈重试，
        仍失败则退化为单任务（问题整体交给一个搜索员，等价于顺序执行）

        :param question: 用户问题
        :return: 任务描述列表；API 彻底失败时返回 None
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
            tasks = self._parse_tasks(content)
            if tasks:
                return tasks
            if attempt < _MAX_PLAN_RETRIES:
                # 格式不符：把模型原文写回历史并追加反馈，随后重试
                print(f"规划格式不正确（第 {attempt + 1} 次），追加反馈后重试")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "规划格式不正确：请输出合法的 JSON 对象，"
                               "形如 {\"tasks\": [\"任务1\", \"任务2\"]}。"
                               "只输出 JSON 本身。",
                })
        # 重试耗尽仍无法解析：退化为单任务
        print("规划解析失败，将问题整体作为单个任务交给搜索员执行")
        return [question]

    def _execute_one(
        self, index: int, task: str, log_fn: Callable[[str], None]
    ) -> Tuple[str, bool, float]:
        """
        供线程池调用的单任务包装：为每个任务动态创建一个搜索员并记录耗时。
        （_call_llm 与 Tavily 均为阻塞 I/O，线程等待期间释放 GIL，
        因此线程池即可获得真实并行——无需多进程；OpenAI 同步客户端
        多线程并发发请求是安全的，各搜索员的对话历史在线程内私有）

        :param index: 任务序号（从 1 开始，用于搜索员命名）
        :param task: 任务描述
        :param log_fn: 本任务自己的日志缓冲区
        :return: (结果文本, 是否正常完成, 耗时秒)
        """
        start = time.perf_counter()  # Windows 高精度单调时钟，专用于间隔测量
        searcher = _make_searcher(f"搜索员-{index}", self.client, self.model)
        result, ok = searcher.execute(task, log_fn=log_fn)
        elapsed = time.perf_counter() - start
        return result, ok, elapsed

    def _dispatch_parallel(
        self, plan: List[str]
    ) -> List[Tuple[str, str, str, bool, float]]:
        """
        并行执行阶段：fan-out（线程池提交全部任务）→ 等待全部完成 →
        主线程按提交顺序回放每个任务的缓冲输出并打印结果。
        全程零锁：线程内只写各自的 StringIO，所有打印由主线程串行执行
        ——「计算进线程池，输出归主线程」，从根上消灭打印竞态。
        （若改为实时打印方案，则需 threading.Lock 包住每个 print）

        :param plan: 任务描述列表
        :return: [(搜索员名, 任务描述, 结果文本, 是否完成, 耗时秒), ...]
        """
        total = len(plan)
        print(f"已向线程池提交 {total} 个子任务（每个子任务一个搜索员线程，并行执行）...")
        buffers: List[io.StringIO] = []  # 每个任务一个日志缓冲区
        with ThreadPoolExecutor(max_workers=total) as pool:
            # max_workers 直接取任务数（上限即 _MAX_TASKS）：
            # 每个任务一个线程，fan-out 语义最直观；
            # with 退出即 shutdown(wait=True)，自动等待全部线程结束
            futures = []
            for i, task in enumerate(plan, 1):
                buf = io.StringIO()
                buffers.append(buf)
                print(f"--- 已提交 {i}/{total}：「{task}」（并行执行）")
                # log_fn 约定：接收一行文本（不含换行），由输出方补换行
                # （print 自动补换行；buf.write 不补，故此处包装）
                # 默认参数 _buf=buf 立即绑定当前缓冲，避免闭包晚绑定
                log_fn = lambda s, _buf=buf: _buf.write(s + "\n")
                futures.append(pool.submit(
                    self._execute_one, i, task, log_fn
                ))
            print("等待全部子任务完成（并行总耗时 ≈ 最慢子任务）...")

            # 按提交顺序取回结果（future.result() 是子线程异常的穿透点；
            # 如需硬超时，可对 result() 传 timeout= 参数——当前每个任务
            # 都有迭代上界 + API 重试上界，总时长有自然上界，无需额外超时）
            collected: List[Tuple[str, str, str, bool, float]] = []
            for i, future in enumerate(futures, 1):
                task = plan[i - 1]
                agent_name = f"搜索员-{i}"
                try:
                    result, ok, elapsed = future.result()
                except Exception as e:
                    result, ok, elapsed = f"子任务执行异常：{e}", False, 0.0
                print(f"--- 子智能体「{agent_name}」执行回放"
                      f"（提交顺序 {i}/{total}，耗时 {elapsed:.1f} 秒）---")
                print(buffers[i - 1].getvalue(), end="")
                print(f"子智能体「{agent_name}」的结果: "
                      f"{_truncate(result, limit=_SUBAGENT_RESULT_LIMIT)}")
                collected.append((agent_name, task, result, ok, elapsed))
                print()
        return collected

    def _build_final_prompt(
        self, question: str, results: List[str]
    ) -> str:
        """
        构建分派给撰稿人的汇总任务文本（材料是撰稿人唯一的事实来源）

        :param question: 原始问题
        :param results: 各搜索员的结果摘要列表
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
        :param results: 各搜索员的结果摘要列表
        :return: 汇总系统提示
        """
        results_text = "\n".join(results)

        return f"""你是一个多智能体系统的编排者（Orchestrator）。撰稿人子智能体执行失败，
请基于以下各搜索员的执行结果，直接汇总出最终答案。

原始问题：{question}

各搜索员执行结果：
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
        汇总阶段（fan-in）：全部搜索任务完成后，把结果作为材料分派给
        「撰稿人」整理成文。汇总天然是串行的——它依赖全部并行结果，
        这正是 fan-in 的含义。

        :param question: 原始问题
        :param results: 各搜索员的结果摘要列表
        :return: 撰稿人成文结果；撰稿人执行失败时返回 None
        """
        print("--- 子智能体「撰稿人」执行中（汇总）---")
        writer = _make_writer(self.client, self.model)
        task = self._build_final_prompt(question, results)
        result, ok = writer.execute(task)  # 默认 log_fn=print，顺序直接打印
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
        :param results: 各搜索员的结果摘要列表
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
            return "（汇总调用失败，以下为各搜索员结果摘要）\n" + "\n".join(results)
        content = message.content or ""
        if isinstance(content, list):  # 防御：个别 provider 返回列表
            content = "".join(
                part.get("text", "") for part in content
            )
        answer = _clean_answer(content)
        if answer:
            return answer
        return "（汇总调用失败，以下为各搜索员结果摘要）\n" + "\n".join(results)

    def run(self, question: str) -> str:
        """
        运行多智能体系统：编排（规划任务）→ 并行执行（fan-out）→ 撰稿人汇总（fan-in）

        :param question: 用户问题
        :return: 最终答案
        """
        # 阶段一：编排
        print("=== 编排阶段 ===")
        plan = self._make_plan(question)
        if plan is None:
            return "API 调用失败，无法完成任务。"
        print("生成的分派计划：")
        for i, task in enumerate(plan, 1):
            print(f"{i}. {task}")

        # 阶段二：并行执行（fan-out）
        print()
        print("=== 并行执行阶段（fan-out）===")
        total_start = time.perf_counter()
        collected = self._dispatch_parallel(plan)
        total_elapsed = time.perf_counter() - total_start
        results = []
        for agent_name, task, result, ok, elapsed in collected:
            summary = _truncate(result, limit=_SUBAGENT_RESULT_LIMIT)
            results.append(f"{agent_name}（{task}）：{summary}")

        # 耗时统计（并行化的实证：T_并行 ≈ max(T_1..T_n)，T_顺序 = Σ T_i）
        print("--- 耗时统计 ---")
        for agent_name, _, _, _, elapsed in collected:
            print(f"「{agent_name}」: {elapsed:.1f} 秒")
        slowest = max((e for _, _, _, _, e in collected), default=0.0)
        sequential = sum(e for _, _, _, _, e in collected)
        print(f"总耗时（并行）: {total_elapsed:.1f} 秒"
              f" ≈ 最慢子任务（{slowest:.1f} 秒）")
        print(f"若改为顺序执行，总耗时约为各子任务之和（{sequential:.1f} 秒）——"
              "并行化的收益 = 把等待时间压缩到最慢子任务的耗时")
        print()

        # 阶段三：汇总（fan-in）
        print("=== 汇总阶段（fan-in）===")
        answer = self._dispatch_synthesis(question, results)
        if answer is None:
            answer = self._make_final_answer(question, results)
        return answer


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 控制台中文乱码
    orchestrator = ParallelOrchestrator()

    print("=== 并行化（Agent Parallelization）模式 ===")
    print("子智能体: 搜索员（并行搜索，全部同构——只有 search 一个工具，"
          "每个子任务动态创建一个新实例）")
    print("         + 撰稿人（fan-in 汇总，无工具，顺序执行）")
    print("提示: 本演示问题的两个子任务相互独立，适合并行执行（fan-out）——")
    print("有依赖的子任务只能顺序执行，请参见 orchestrator_workers.py")
    print()

    question = input("请输入你的问题: ")
    print()

    result = orchestrator.run(question)
    print()
    print("最终结果:")
    print(result)


if __name__ == "__main__":
    main()
