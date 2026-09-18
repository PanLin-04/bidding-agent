# 招投标采购 Agent

面向招投标采购场景的智能问答系统：以 **RAG 混合检索（Qdrant）+ 知识图谱（Neo4j）+ 结构化数据库（PostgreSQL）+ 联网搜索（Tavily/Exa）** 四路数据源为底座，由 **ReAct 多轮工具循环**的 Agent 编排回答，前端 **Next.js 14** 流式渲染（SSE）。

> **当前状态**：**Agent + RAG 问答链路已可运行**——Excel → Qdrant（dense 512 维 bge-small-zh-v1.5 + jieba BM25 稀疏向量）→ 原生 RRF 混合检索 → 注册为 `search_knowledge_base` 工具 → **ReAct Agent 自动调用**（多轮、可并发多个查询）→ DeepSeek 生成 → SSE 流式 + 来源卡片；338 例 pytest 全绿。
> 无 LLM 凭据 / provider 不支持工具调用时自动降级为返回知识库原文。知识图谱 / 结构化数据库 / 联网搜索尚未实现，见 `分配说明.md` 分工。

## 快速开始



```
\# 1. 安装后端依赖（Python ≥ 3.12，uv 管理）

uv sync

\# 2. 配置 .env（必需项：QDRANT\_URL / QDRANT\_API\_KEY / DEEPSEEK\_API\_KEY）

\#    首次运行嵌入/精排模型请设置 HF\_ENDPOINT=https://hf-mirror.com

\# 3. 导入知识库（Excel 问/答 → Qdrant）

python main.py ingest

\# 4. 启动后端（http://localhost:8001，接口文档 /docs）

python main.py api

\# 5. 启动前端（另开终端，http://localhost:3000）

cd frontend && npm install && npm run dev
```

## 文档地图



| 文档                  | 定位                                  |
| ------------------- | ----------------------------------- |
| `分配说明.md`           | **团队协作入口**：5 人任务分工、Git 流程、接口契约、联调计划 |
| `docs/开发文档.md`      | 面向开发者：环境、数据管道、API 契约、测试、部署、排障       |
| `docs/技术栈.md`       | 技术栈选型与版本锁定                          |
| `docs/组件工作机制.md`    | 20 个组件的 Mermaid 机制图解                |
| `docs/增加Agent功能.md` | 二期可扩展功能清单与实施路线                      |

## 仓库结构

```
├── main.py                  # CLI 入口: ingest / api / dev          ✅ 已实现
├── pyproject.toml           # 依赖声明（uv）
├── conftest.py              # pytest 根配置（预置必需 env）          ✅
├── .github/workflows/ci.yml # push/PR 自动 uv sync + pytest
├── api/server.py            # FastAPI 端点 + 限流 + SSE + 健康检查    ✅
├── src/
│   ├── rag/                 # 混合检索（pipeline/vector_store/embedder/ingest） ✅
│   ├── agent/               # Agent 编排（core/react_loop/generation/tool_defense） ✅
│   ├── tools/               # 工具定义与执行（base/rag_tools）          ✅
│   ├── clients/             # LLM 客户端（base/deepseek/llm_factory）  ✅
│   ├── config.py            # Settings + .env 校验                    ✅
│   ├── rate_limiter.py      # 滑动窗口限流（30 次/60 秒）              ✅
│   ├── logging_config.py    # 日志配置                                ✅
│   ├── agent/skills.py      # 技能加载与匹配（_match_skills）           ⬜ 待实现
│   ├── database/            # Neo4j / PostgreSQL 客户端与导入           ⬜ 待实现
│   ├── mcp/                 # Exa MCP 客户端                          ⬜ 待实现
│   └── web_search.py        # Tavily 客户端                           ⬜ 待实现
├── frontend/                # Next.js 14 前端（SSE 流式渲染）          ✅
├── tests/                   # pytest 338 例                          ✅
├── eval/                    # retrieval_eval ✅ / ragas·agent·dashboard ⬜
└── batch/                   # 数据处理脚本：去重 / 编JSONL / 排序 / 插标的物 / 交易频次 ✅ 5 个全部落地
```

> 各模块代码由 5 人按 `分配说明.md` 分工实现，通过 GitHub PR 合入 `main`。
> 标 ⬜ 的模块属其余成员的分工范围，接口契约见 `docs/开发文档.md`。