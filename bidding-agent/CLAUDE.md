# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 当前仓库状态（先读这条）

**已落地：RAG 问答链路（可运行）** —— 这是 `docs/开发文档.md` 目标架构中"成员 B + D + E"的那一部分：

| 已实现 | 位置 |
|---|---|
| Excel → Qdrant 导入（全量重建 + 词表落盘） | `src/rag/ingest.py` |
| BGE dense + jieba BM25 稀疏编码（懒加载 + 双检锁） | `src/rag/embedder.py` |
| Qdrant 命名向量 + Prefetch + RRF 混合检索 | `src/rag/vector_store.py` |
| 检索 → （可选精排）→ 生成，两种回答模式 | `src/rag/pipeline.py` |
| **RAG 工具注册 + 工具执行器（并行/超时/异常隔离）** | `src/tools/` |
| **BiddingAgent：ReAct 多轮工具循环 + 生成 + 工具文本防护** | `src/agent/` |
| DeepSeek 客户端（含 `chat_raw` 原生 Function Calling）+ 工厂 | `src/clients/` |
| REST + SSE 端点、限流、健康检查 | `api/server.py` |
| CLI（ingest / api / dev） | `main.py` |
| Next.js 14 聊天前端（流式 + 来源卡片 + 暗色/会话持久化） | `frontend/` |
| 检索质量评测（无精排 vs 各精排策略） | `eval/retrieval_eval.py` |
| 数据管道第 1 步：按「项目编号+中标金额」联合去重 | `batch/去重.py` |
| 数据管道第 2 步：提取「项目名称」→ 智谱 Batch 请求 JSONL | `batch/提数据_编JSONL.py` |
| 数据管道第 3 步：Batch 结果按 custom_id 数值重排回行号顺序 | `batch/按custom_id排序.py` |
| 数据管道第 4 步：结果里的标的物按行号回填进 Excel | `batch/提数据_插标的物.py` |
| 数据管道第 5 步：标的物交易频次统计（**管道 5 步全部落地**） | `batch/统计数据_插交易频次.py` |
| 数据管道提交环节：上传请求 → 建批量任务 → 下载结果 | `batch/提交batch任务.py` |
| pytest 338 例 | `tests/` |

**尚未实现**（团队其余成员的分工范围，按 `分配说明.md` 走）：`src/agent/skills.py`（技能加载与 `_match_skills`）、`src/database/`（Neo4j / PostgreSQL）、`src/mcp/`（Exa）、`src/web_search.py`（Tavily 及联网类工具）、`eval/`（`ragas_eval` / `agent_eval` / `dashboard` 待做）。（`batch/` 的 5 个脚本已全部落地。）
因此本文中关于 **ReAct 循环、工具契约、图谱/数据库工具、联网重排、深度思考** 的描述仍是**目标设计与契约**，不是现状——动手前先确认目标文件是否存在；不存在说明你正在实现它，**照契约写，不要改契约**。

有意的取舍（与目标架构的已知偏差，改动前先看这条）：

- **常量的定义与再导出**：`MAX_HISTORY_ROUNDS` / `TOOL_NAME_KB` 的真实定义在 `src/rag/constants.py`（RAG 侧本就在用），`src/agent/constants.py` 按文档路径再导出，避免两处各写一份数字而漂移。
- **RRF 用 Qdrant 原生融合（固定 k）**：目标架构的"RRF k 按问题类型取 30/60/90"未实现——Qdrant 的 `FusionQuery(RRF)` 的 k 由服务端固定，做 per-type k 得自己实现融合层，收益不抵复杂度。当前是单一固定融合。
- **`data/vocab.json` 与 `eval/paraphrase_cache.json`**：前者是 ingest 产物（已 gitignore），后者是评测用的口语化查询缓存（**已入库**，为的是让前后两次评测建立在同一批查询上）。
- **精排（Re-ranking）已实现但默认关闭**（`RERANK_ENABLED=0`）：CrossEncoder 二次排序在 `src/rag/embedder.py` 的 `Reranker`，检索路径见 `RAGPipeline.search`（开启时先把候选池扩到 `RERANK_CANDIDATE_K=20`，失败自动退回混合检索顺序）。**默认关是因为实测无收益**：75 条语料下混合检索 Top-1 已 98.7%、Top-5 100%，精排后完全持平，而 CPU 上要付约 0.9s/查询。改动前先跑 `python eval/retrieval_eval.py --query-style paraphrase`。
  两个已知陷阱（都有回归测试）：① 与查询配对的字段必须是**知识库问题**而非答案——用答案会把 Top-1 打到 78.7%；② `CrossEncoder.predict()` 默认已套 Sigmoid，必须传 `activation_fn=nn.Identity()` 取原始 logit，否则双重 sigmoid 会把不相关候选的分数抬到 0.50。
- **启动预热默认开启**（`WARMUP_ON_START=1`）：后台线程加载嵌入模型（开启精排时连精排模型一起）。首次查询检索耗时因此从约 31s 降到约 3s。
- **会话持久化只在前端 localStorage**：目标架构中的 PostgreSQL 会话/反馈存储属 `src/database/` 范围。

文档权威顺序：`分配说明.md`（协作流程/分工）→ `docs/开发文档.md`（环境、契约、测试、部署、排障，§ 引用均指本文档）→ `docs/技术栈.md`（版本锁定）→ `docs/组件工作机制.md`（20 个组件 Mermaid 图解）。

## 项目定位

招投标采购智能问答：**四路数据源**（Qdrant 混合检索 / Neo4j 知识图谱 / PostgreSQL 业务表 / Tavily+Exa 联网搜索）为底座，**ReAct 多轮工具循环** Agent 编排，FastAPI 以 SSE 流式输出，Next.js 14 前端逐字渲染。

## 常用命令

```bash
# 环境（Python ≥3.12 由 uv 管理；Node ≥18）
uv sync                                    # 后端依赖（含 dev 组 pytest）
cd frontend && npm install                 # 前端依赖
cp .env.example .env                       # 必需项：QDRANT_URL / QDRANT_API_KEY / DEEPSEEK_API_KEY

# 运行
python main.py ingest                      # Excel 问/答 → Qdrant（全量重建）
python main.py api                         # http://localhost:8001，接口文档 /docs（带 reload，开发用）
python main.py dev                         # 后台线程起后端 + 打印前端启动提示
cd frontend && npm run dev                 # http://localhost:3000

# 测试（根 conftest.py 预置必需 env，无 .env 也可跑；全部同步测试，无 asyncio 插件）
uv run pytest -q                                              # 全量（当前 338 例，CI 同口径）
uv run pytest tests/test_react_loop.py -v                     # 单文件
uv run pytest tests/test_rate_limiter.py::test_thread_safety_exact_quota -v   # 单用例
uv run pytest -k "sse" -v                                     # 关键词筛选

# 数据管道（均 CWD 无关、支持 --dry-run、Windows GBK CSV；必须按序执行）
# 依次：去重.py → 提数据_编JSONL.py → 按custom_id排序.py → 提数据_插标的物.py → 统计数据_插交易频次.py
python batch/去重.py
python batch/提数据_编JSONL.py     # → data/batch/batch_requests.jsonl（智谱 Batch，custom_id=行号）
python batch/提交batch任务.py      # 上传建任务+等完成+下载（需平台实名认证；模型须用批量侧名）
python batch/按custom_id排序.py    # 结果下载后：batch_results_raw → _processed（按序号数值排）
python batch/提数据_插标的物.py    # 按 custom_id 行号回填「标的物」列 → data/processed/<原名>_2_标的物.xlsx
python batch/统计数据_插交易频次.py  # → data/processed/标的物_交易频次.xlsx（标的物、交易频次）
python src/database/to_neo4j.py --password <pwd> --clear       # CSV → 图谱（星型 schema）
python src/database/to_postgresql.py                           # XLSX → bidding_procurement 表

# 评测（走非流式 chat()，默认参数）
python eval/ragas_eval.py | eval/agent_eval.py | eval/dashboard.py

# 生产部署（限流器/缓存/MCP 子进程均为进程内单例，必须单进程）
uv run uvicorn api.server:app --host 0.0.0.0 --port 8001
cd frontend && NEXT_PUBLIC_API_BASE=https://后端地址 npm run build && npm run start
```

CI（`.github/workflows/ci.yml`）：push 到 `main` 与任何 PR 自动 `uv sync` + `uv run pytest -q`。

## 架构

### 请求链路与核心抽象

```
HTTP → api/server.py（滑动窗口限流 30 次/60 秒 per IP）
     → BiddingAgent._chat_events：产出「结构化事件元组」的单一流程
         ├── chat_stream：序列化为 SSE 帧
         └── chat        ：收集为 dict（评测用）
```

`_chat_events` 是唯一编排入口：**新增逻辑只改这一处**，两条出口自动同构——这是刻意的架构选择，不要在 `chat` 里另写一套流程。

### 三条主干

- **混合检索（`src/rag/`）**：Qdrant Named Vectors 每点双向量（`dense` 512 维 bge-small-zh-v1.5 + `sparse` jieba BM25）→ `query_points` 的 `Prefetch` 双路各召回 30 → RRF 融合 → （可选）bge-reranker-base CrossEncoder 精排。**vocab.json 必须与向量数据同步**：换数据必须重新 ingest，否则稀疏向量的词表索引错位、检索质量骤降。
- **两种回答模式共存（`RAGPipeline.chat_events`）**：命中 LLM 凭据时用 DeepSeek 基于检索结果生成；**无凭据 / 检索无结果 / LLM 调用失败**时自动降级为「直接返回最匹配的问答原文」。降级是运行时行为：失败时先发 `reset` 清掉已流出的半截内容，再重新输出原文——`reset` 之后必须**重新输出**，否则用户面对空气泡（`tests/test_pipeline_modes.py` 用模拟前端渲染的 `_rendered()` 守住这一点）。
- **Agent 与工具层（`src/agent/`、`src/tools/`）**：`BiddingAgent` 是问答入口，ReAct 工具循环 ≤4 轮（`MAX_TOOL_ROUNDS`），工具经 `TOOL_EXECUTORS` 注册、由 `ToolRunner` 执行（4 线程并行 / 30s 总超时 / 异常隔离，失败返回结构化错误而非抛异常）。模型判断无需工具时直接作答；**无 LLM 凭据、provider 不支持工具调用、或工具轮抛异常**时，降级为「直接检索 + 返回原文」（与 RAG 流水线的降级行为一致）。
  - **`TOOL_EXECUTORS` 有两处模块级绑定**：`src/agent/core.py` 与 `src/agent/react_loop.py`，测试打补丁需同时替换（§9.3）。
  - **工具文本泄漏防护**（`tool_defense.py`）：模型偶尔把调用当正文吐出来，有 **JSON 方言**（`<tool_call>{...}`）和 **XML 方言**（`<tool calls><invoke name=…>`，实测 DeepSeek 用这套且标签带空格）两种，检测/解析/清理都要覆盖。根因治理在 `prompts.FINAL_STAGE_INSTRUCTION`——最终生成调用不带 `tools` 参数，必须明确告诉模型"本阶段无工具可用"，否则它会继续输出工具语法。

### 目录（目标结构，详见 §1）

```
main.py                 # CLI: ingest / api / dev
api/server.py           # 全部端点 + 限流中间件 + lifespan
src/agent/              # core / react_loop / generation / tool_defense / skills / prompts / constants / utils
src/rag/                # pipeline / vector_store / embedder / ingest
src/clients/            # BaseLLMClient + deepseek/zhipu/openai_compatible/vision + llm_factory
src/tools/              # base / rag_tools
src/database/           # neo4j_client / postgresql_client / to_neo4j / to_postgresql
src/mcp/                # 自研 stdio JSON-RPC 客户端 + Exa MCP 封装
frontend/               # Next.js 14 App Router（app/page.tsx + components/ + lib/api.ts）
tests/ eval/ batch/     # pytest / 评测脚本 / 数据处理
```

### SSE 事件协议（§6，前端与 eval 的硬依赖）

六类事件：`status`（覆盖占位文字）/ `token`（正文增量）/ `thinking`（思考增量，不落库）/ `reset`（检出工具调用文本泄漏 → 清空已流正文重来）/ `done` / `error`。
首帧是 2KB padding 注释（`:` + 空格）强制代理立即 flush；正文 40 字符小帧 + 20ms 间隔切分。
`done` 帧的 **`tool_name` 是单字符串**（= 最后一个执行完成的工具名），前端徽标与 eval 工具准确率都依赖此语义，勿改成数组。

### 前端流式关键点

- **SSE 绕过 Next.js `rewrites` 代理直连后端**：Next 代理会缓冲整个响应导致流式失效；生产用 `NEXT_PUBLIC_API_BASE` 显式指定后端，反代需 `proxy_buffering off`（响应头已带 `X-Accel-Buffering: no`）。
- 流式状态三重防护（`page.tsx`）：`AbortController` 取消旧流 + `requestSeqRef` 请求序号校验 + `sessionIdRef` 会话归属校验。
- `handleRegenerate` 必须经 ref 保持引用稳定，否则每个 token 都生成新回调，破坏 `ChatMessage` 的 `React.memo`。

## 契约与红线（§13.3，破坏即跨人返工）

| 契约 | 内容 |
|---|---|
| 工具结果 | 统一 `{"success", "data"/"results", "error"}`，失败不抛异常 |
| SSE done 帧 | `sources/web_sources/tool_name/elapsed_ms/phase_times`，`tool_name` 单字符串 |
| 公共导入路径 | `from src.agent import ...` / `from src.rag import ...` 重导出不可破坏（测试与 eval 依赖） |
| 前端落库形状 | `PersistedMessage`：`role/content/sources/toolCalled/toolName/image/imageName`；`thinking` 与计时**不落库** |
| `.env` 键名与默认值 | 全表见 §2.3，新增配置项必须同步文档 |
| 安全 | 数据库查询参数化 + 字段/聚合白名单；用户可见错误不泄露内部地址/原始异常（细节只进日志） |

改动以上任一项：同步 `docs/开发文档.md` 并通知相关成员。§13.4 有"改什么 → 同步哪份文档"的对照表。

## 开发约定

- **注释与日志用中文，解释"为什么"而非复述"做什么"**；常量集中在 `src/agent/constants.py`；单例小写下划线（`rag_pipeline`）、类大驼峰（`RAGPipeline`）。
- **单例 + 懒加载 + 双检锁**：模型/客户端首次实际使用时才加载，并发首用只加载一份。
- **出站请求一律 `trust_env=False`**，不继承操作系统代理（桌面代理软件关闭后会把连接路由到失效端口，WinError 10061）；需要代理时显式配 `HTTP_PROXY`/`HTTPS_PROXY`。
- **超时预算**：LLM 60s / 本地模型 300s / Qdrant 30s / Neo4j 30s / MCP 60s / 工具总 30s。
- `load_dotenv(override=False)`：容器/CI 注入的真实环境变量优先于 `.env`。
- 新增工具 / 技能 / LLM Provider 的分步清单见 §8.3 / §8.4 / §8.5。

### 测试编写约定（§9.3，拆包后易踩）

- 用**文件内局部 fake 类**（`_FakeLLM` / `ScriptedLLM` 风格），不建全局 fixture。参考 `tests/test_react_loop.py` 的 `ScriptedLLM`（按队列返回或抛异常，可脚本化多轮）。
- monkeypatch 目标（真实绑定位置）：
  - `TOOL_EXECUTORS` 有**两处**模块级绑定：`src.agent.core`（runner 构造）与 `src.agent.react_loop`（文本兜底过滤）——需同时替换。
  - `_match_skills` → `src.agent.core._match_skills`；联网重排 reranker → `src.rag.reranker`；嵌入模型 → `src.rag.embedder.SentenceTransformer/CrossEncoder`。
- fake 的 `_build_context` 必须返回**含 system 消息的列表**（`_clean_for_final` 取 `messages[0]`，空列表越界）。

## 协作流程（`分配说明.md`）

- `main` 只经 PR 合入，不在本地直接 push；每人一条功能分支：`feat/agent-core`(A) / `feat/rag`(B) / `feat/tools-data`(C) / `feat/api-llm`(D) / `feat/frontend`(E)。
- 提交信息：`类型(模块): 描述`，类型取 `feat/fix/docs/test/chore`，描述用中文写"做了什么 + 为什么"。
- 合 PR 前 `uv run pytest -q` 必须全绿；推荐 Squash and merge；冲突本地 `git pull origin main` 解决，绝不 `--force`。
- `.env` 与密钥永不入库。
