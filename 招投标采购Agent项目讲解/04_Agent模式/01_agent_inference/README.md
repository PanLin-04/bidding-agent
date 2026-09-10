# 智能体设计模式示例

智能体（Agent）设计模式的中文教学示例仓库：代码、注释、提示词与终端输出均为中文。
使用 DeepSeek 模型（OpenAI SDK），uv 管理依赖，Python 3.12。

## 项目结构

八个平行实现，各自是独立自包含文件（不互相 import）：

| 文件 | 模式 | 说明 |
| --- | --- | --- |
| `react_parse.py` | ReAct（文本解析式） | 早期版本，仅供对比 |
| `react_tools.py` | ReAct（function calling） | 单智能体维护版：思考 → 行动 → 观察循环 |
| `plan_and_solve_parse.py` | Plan-and-Solve | 先制定分步计划再逐步执行；纯文本编号计划 |
| `plan_and_solve_json.py` | Plan-and-Solve | 同上；`response_format` 强制 JSON 计划 |
| `plan_and_execute.py` | Plan-and-Execute | 计划-执行-重规划闭环：某步失败自动修订剩余计划（上限 2 次） |
| `reflection.py` | Reflection | 尝试 → 评审 → 不通过则携带反馈重试（上限 3 次），反思记忆跨尝试累积 |
| `orchestrator_workers.py` | Orchestrator-Workers | 编排者拆任务、顺序分派给各司其职的子智能体（研究员 / 计算员 / 撰稿人，工具权限各不同） |
| `parallelization.py` | Agent Parallelization | 同一架构，但用线程池并行执行相互独立的子任务（fan-out / fan-in），总耗时 ≈ 最慢子任务 |

## 快速开始

1. 创建并配置 `.env`（Tavily 密钥从 https://tavily.com/ 获取）：

   ```
   DEEPSEEK_API_KEY=...
   TAVILY_API_KEY=...
   ```

2. 安装依赖并运行其中一个实现：

   ```bash
   uv sync

   uv run python react_tools.py           # ReAct
   uv run python plan_and_solve_parse.py  # Plan-and-Solve（纯文本计划）
   uv run python plan_and_solve_json.py   # Plan-and-Solve（JSON 计划）
   uv run python plan_and_execute.py      # Plan-and-Execute（含重规划）
   uv run python reflection.py            # Reflection
   uv run python orchestrator_workers.py  # 编排-执行（顺序分派）
   uv run python parallelization.py       # 并行化（线程池 fan-out）
   ```

3. 输入你的问题。示例问题：

   - 2026 年世界杯冠军所在国家，在2025年的GDP是多少？
   - 25 乘以 4 再加 100 等于多少？

## 内置工具

- calculator - 数学计算器（基于 `eval`，教学简化实现）
- search - Tavily 搜索引擎（实时网络搜索，需配置 `TAVILY_API_KEY`）

## 扩展性

- 单智能体文件：`agent.register_tool(name, func, description, parameters)` 注册新工具——函数接收关键字参数并返回字符串，`parameters` 提供 JSON Schema 供模型结构化生成调用参数。
- 多智能体文件：为每个 `Subagent` 注册各自的工具集即可实现工具权限隔离；用 `orchestrator.add_agent(agent)` 加入新子智能体。
