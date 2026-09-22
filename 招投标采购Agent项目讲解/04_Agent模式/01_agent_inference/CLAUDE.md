# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

智能体设计模式的教学示例仓库：代码、注释、提示词与终端输出均为中文。使用 DeepSeek 模型（OpenAI SDK，`base_url="https://api.deepseek.com/v1"`），uv 管理依赖，Python 3.12。

仓库中有八个平行实现，各自是**独立自包含文件**（不互相 import），共享同一套循环稳定性机制：

| 文件 | 模式 | 备注 |
| --- | --- | --- |
| `react_parse.py` | ReAct（文本解析式） | 早期版本，仅供对比 |
| `react_tools.py` | ReAct（原生 function calling） | 单智能体维护版 |
| `plan_and_solve_parse.py` | Plan-and-Solve | 纯文本编号计划 |
| `plan_and_solve_json.py` | Plan-and-Solve | `response_format` 强制 JSON 计划 |
| `plan_and_execute.py` | Plan-and-Execute | 计划-执行-重规划闭环 |
| `reflection.py` | Reflection（反思式推理） | Actor → Critic → 反思记忆 |
| `orchestrator_workers.py` | Orchestrator-Workers | 多智能体，顺序分派 |
| `parallelization.py` | Agent Parallelization | 多智能体，线程池并行分派 |

`react_parse.py` 的 `register_tool` 与工具函数签名与新版不同（旧版无 `parameters` JSON Schema、工具函数收位置参数），不要互相复制代码；其余新文件的共享机制以 `react_tools.py` 为源头，改动时需全仓库同步。

## 常用命令

```bash
uv sync                                # 安装依赖
uv run python react_tools.py           # ReAct（function calling）
uv run python plan_and_solve_parse.py  # Plan-and-Solve（纯文本计划）
uv run python plan_and_solve_json.py   # Plan-and-Solve（JSON 计划）
uv run python plan_and_execute.py      # Plan-and-Execute（含重规划）
uv run python reflection.py            # Reflection
uv run python orchestrator_workers.py  # 编排-执行（顺序分派）
uv run python parallelization.py       # 并行化（线程池 fan-out）
```

没有配置测试与 lint。系统 Python 未装项目依赖，运行脚本一律用 `uv run`；语法检查用 `python -m py_compile <file>`。用管道喂入中文问题做自动化测试时需加 `PYTHONUTF8=1` 前缀（Git Bash 管道下 stdin 的 GBK 解码问题），交互式运行不受影响。

## 环境变量

`.env` 中配置：`DEEPSEEK_API_KEY`（必需，缺失时构造 Agent 直接报错）、`TAVILY_API_KEY`（仅 search 工具需要；缺失时工具返回错误文本而非抛异常）。`.env` 含真实密钥，已被 `.gitignore` 排除，不要提交或打印其内容。

## 架构要点

- **工具注册**：单智能体文件用 `register_tool(name, func, description, parameters)` 把 `parameters`（JSON Schema）组装成 OpenAI 兼容的 `tools` 参数传给模型；工具函数接收关键字参数、返回字符串，内部异常要自己兜住——返回值会作为文本反馈给模型。多智能体文件（orchestrator_workers / parallelization）中每个 `Subagent` 有独立的 tools/tool_schemas，编排者类**没有**任何工具属性（工具权限隔离是结构性的，不是提示词层面的）。
- **循环稳定性机制**（全仓库共享，改动需全仓库同步）：`_MAX_OBSERVATION=1500` 截断工具结果；`_REPEAT_STOP=3` 连续重复调用硬终止；`_execute_tool` 用 `inspect.signature` 过滤模型多余参数；工具异常兜住后以文本反馈给模型。
- **JSON 强制输出**：`response_format={"type": "json_object"}` 已在 plan_and_solve_json / plan_and_execute / reflection / orchestrator_workers / parallelization 中验证可用；提示词必须含 "json" 字样，解析前先 `_clean_answer` 剥代码围栏。
- 默认模型为 `deepseek-v4-flash`（各 Agent 类 `__init__` 参数，不读 .env）。

## 约定

- 用户可见字符串、注释、打印输出均为中文；Windows GBK 控制台乱码靠 `sys.stdout.reconfigure(encoding="utf-8")` 处理，勿删。
- `calculator` 用 `eval` 执行模型生成的表达式，属教学简化的已知风险。
- 各文件为独立自包含的平行实现：允许文件内复制共享机制，但不要跨文件 import。
