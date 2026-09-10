"""
一个ReAct模式的智能体（文本解析式）

示例问题：2026 年世界杯冠军所在国家，在2025年的GDP是多少？
"""
import os
import re
import sys
import time
from typing import Dict, Callable, Any
from openai import OpenAI, OpenAIError
from dotenv import load_dotenv, find_dotenv

_ = load_dotenv(find_dotenv())

# —— 解析正则（编译一次，兼容全角冒号与中文关键词）——
_ACTION_RE = re.compile(
    r"(?:Action|行动)\s*[：:]\s*(\w+)\s*\[\s*(.*?)\s*\]",
    re.IGNORECASE,
)
_FINAL_RE = re.compile(
    r"(?m)^\s*(?:最终答案|Final\s*Answer)\s*[：:]?\s*(.*)",
    re.DOTALL | re.IGNORECASE,
)

# —— 循环稳定性常量 ——
_MAX_OBSERVATION = 1500  # Observation 截断长度（搜索类工具需要更大空间）
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
        self.conversation_history = []

    def register_tool(self, name: str, func: Callable, description: str = ""):
        """
        注册一个工具，用于智能体调用
        
        :param name: 工具名称
        :param func: 工具函数，接收参数并返回结果
        :param description: 工具描述，用于智能体调用时显示
        """
        self.tools[name] = {
            "func": func,
            "description": description
        }

    def _build_prompt(self, question: str) -> str:
        """
        构建智能体的提示模板
        
        :param question: 用户问题
        :return: 智能体的提示模板
        """
        tool_descriptions = "\n".join([
            f"- {name}: {tool['description']}"
            for name, tool in self.tools.items()
        ])
        
        prompt = f"""你是一个智能助手，使用ReAct（Reasoning + Acting）模式来解决问题。

可用工具：
{tool_descriptions}

格式要求（严格遵守）：
每次回复输出一行 Thought 和一行 Action，Action 必须单独一行，使用英文冒号和方括号：
Thought: 你的推理
Action: 工具名[参数]

系统会执行该工具并把结果以 Observation: 开头反馈给你，你继续思考。
一旦得到答案，立刻单独一行输出：
最终答案: 答案内容

规则：
1. 一次只输出一个 Action，Action 行之后不要附加解释文字
2. 输出 Action 后立即停止回复，等待系统返回 Observation；不要自行书写 Observation 行，也不要提前给出最终答案
3. 不要重复调用已经调用过且参数相同的工具，结果不会改变
4. 参数中不要包含方括号 [ ] 或换行；计算表达式可使用圆括号 ( ) 和运算符 + - * / ** %
5. 工具不存在时不要编造工具名，改用其他工具或直接输出最终答案
6. 得到答案后立即输出最终答案，不要继续调用工具

示例（完整对话过程）：
用户: 问题：25 乘以 4 再加 100 等于多少？
助手: Thought: 需要先计算 25*4+100，使用计算器。
Action: calculator[25*4+100]
系统反馈: Observation: 200
助手: Thought: 计算完成，得到 200。
最终答案: 200

用户: 问题：什么是 ReAct 模式？
助手: Thought: 这是概念性问题，先用搜索工具查找。
Action: search[ReAct模式]
系统反馈: Observation: 答案摘要: ReAct 是推理（Reasoning）与行动（Acting）交替进行的智能体模式。搜索结果: 1. ReAct模式详解 (https://example.com/react)
助手: Thought: 已获得足够信息，给出答案。
最终答案: ReAct 是一种让智能体交替进行推理（Reasoning）和行动（Acting）的提示词模式。

当前问题：{question}

开始！
"""
        return prompt

    def _parse_action(self, text: str) -> tuple[str, str] | None:
        """
        解析智能体的行动指令，提取工具名和参数
        
        :param text: 智能体的行动指令，格式为 Action: <工具名>[<参数>]
        :return: 包含工具名和参数的元组，或 None 如果格式错误
        """
        match = _ACTION_RE.search(text)
        if match:
            return match.group(1), match.group(2).strip()
        return None

    def _call_llm(self, messages: list, max_retries: int = 2) -> str | None:
        """
        调用 LLM，带重试与异常处理

        :param messages: 消息列表
        :param max_retries: 最大重试次数
        :return: 模型回复内容，失败时返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,  # 降低格式漂移
                )
                content = response.choices[0].message.content
                if isinstance(content, list):  # 防御：个别 provider 返回列表
                    content = "".join(
                        part.get("text", "") for part in content
                    )
                if isinstance(content, str) and content.strip():
                    return content
                # 推理模型偶发返回空 content，同样重试
                print(f"模型回复为空（第 {attempt + 1}/{max_retries + 1} 次），准备重试")
            except OpenAIError as e:
                print(f"API 调用失败（第 {attempt + 1}/{max_retries + 1} 次）: {e}")
            except Exception as e:
                print(f"调用 LLM 时发生未知错误: {e}")
                break
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 1s, 2s
        return None

    def _extract_final_answer(self, text: str) -> str:
        """
        提取最终答案标记之后的内容，并清理代码围栏

        :param text: 模型回复
        :return: 干净的最终答案文本
        """
        match = _FINAL_RE.search(text)
        if match:
            answer = match.group(1).strip()
        else:  # 兜底：标记不在行首时取标记之后的内容
            parts = re.split(
                r"(?:最终答案|Final\s*Answer)", text, maxsplit=1, flags=re.IGNORECASE
            )
            answer = parts[1].strip() if len(parts) > 1 else text.strip()
        answer = re.sub(r"^```[^\n]*\n?", "", answer).strip()  # 剥离首部代码围栏
        answer = re.sub(r"```\s*$", "", answer).strip()        # 剥离尾部代码围栏
        return answer

    def _truncate(self, s: str, limit: int = _MAX_OBSERVATION) -> str:
        """
        截断过长的 Observation，防止上下文膨胀

        :param s: 原始字符串
        :param limit: 最大长度
        :return: 截断后的字符串
        """
        return s if len(s) <= limit else s[:limit] + "...（已截断）"

    def run(self, question: str, max_steps: int = 10) -> str:
        """
        运行智能体，解决用户问题
        
        :param question: 用户问题
        :param max_steps: 最大推理步骤，默认10步
        :return: 智能体的最终答案
        """
        self.conversation_history = [
            {"role": "system", "content": self._build_prompt(question)}
        ]

        last_call = None      # 上一次执行的 (工具名, 参数)
        repeat_count = 0      # 连续重复调用计数

        for step in range(max_steps):
            # 1. 调用 LLM（含重试）
            assistant_message = self._call_llm(self.conversation_history)
            if assistant_message is None or not assistant_message.strip():
                print(f"Step {step + 1}: API 调用失败或回复为空")
                return "API 调用失败，无法完成任务。"

            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })

            print(f"Step {step + 1}:")
            print(assistant_message)
            print("-" * 50)

            # 2. 最终答案优先检测（兼容中英文标记与全角冒号）
            if _FINAL_RE.search(assistant_message):
                return self._extract_final_answer(assistant_message)

            # 3. 解析 Action（findall 检测多 Action）
            actions = _ACTION_RE.findall(assistant_message)
            if not actions:
                observation = (
                    "Observation: 解析失败：未检测到有效的 Action 行。"
                    "请严格按以下格式单独输出一行（英文冒号、方括号，参数内不要有方括号）："
                    f"Action: 工具名[参数]。可用工具：{', '.join(self.tools)}。"
                    "你也可以直接输出 最终答案: 答案内容。"
                )
                self.conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                print(observation)
                print("-" * 50)
                continue

            if len(actions) > 1:
                observation = "Observation: 检测到多个 Action，请每次只输出一个 Action。"
                self.conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                print(observation)
                print("-" * 50)
                continue

            tool_name, tool_input = actions[0][0], actions[0][1].strip()

            # 4. 重复调用检测（连续相同调用第 2 次起警告且不执行，第 3 次硬终止）
            call = (tool_name, tool_input)
            if call == last_call:
                repeat_count += 1
                if repeat_count >= _REPEAT_STOP:
                    return "未能在规定步骤内完成任务：模型连续重复调用相同工具。"
                observation = (
                    f"Observation: 警告：你已连续 {repeat_count} 次调用 "
                    f"{tool_name}[{tool_input}]，结果不会改变。"
                    "请尝试新的思路，或直接输出 最终答案: 答案内容。"
                )
                self.conversation_history.append({
                    "role": "user",
                    "content": observation
                })
                print(observation)
                print("-" * 50)
                continue
            last_call, repeat_count = call, 1

            # 5. 执行工具（全部异常兜住）
            if tool_name not in self.tools:
                observation = (
                    f"Observation: 未知工具 '{tool_name}'，"
                    f"可用工具：{', '.join(self.tools)}。"
                    "请改用可用工具，或直接输出 最终答案: 答案内容。"
                )
            else:
                try:
                    result = self.tools[tool_name]["func"](tool_input)
                    observation = f"Observation: {self._truncate(str(result))}"
                except Exception as e:
                    observation = f"Observation: 工具执行出错：{e}"

            self.conversation_history.append({
                "role": "user",
                "content": observation
            })
            print(observation)
            print("-" * 50)

        return "未能在规定步骤内完成任务。"


def calculator(input_str: str) -> str:
    """
    数学计算器工具，用于执行数学计算
    
    :param input_str: 数学表达式字符串
    :return: 计算结果字符串
    """
    try:
        result = eval(input_str)
        return str(result)
    except Exception as e:
        return f"计算错误: {str(e)}"


def web_search(input_str: str, max_results: int = 3) -> str:
    """
    Tavily 搜索引擎，用于实时查找网络信息

    :param input_str: 搜索关键词字符串
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
            query=input_str,
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
            lines.append(f"{i}. {r.get('title', '无标题')} ({r.get('url', '')})\n   {content}")
    else:
        lines.append("未找到相关结果")
    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 控制台中文乱码
    agent = ReActAgent()
    
    agent.register_tool(
        "calculator",
        calculator,
        "计算器，用于执行数学计算，输入为数学表达式"
    )
    
    agent.register_tool(
        "search",
        web_search,
        "Tavily 搜索引擎，用于实时查找网络信息，输入为搜索关键词"
    )
    
    print("=== ReAct 智能体 ===")
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
