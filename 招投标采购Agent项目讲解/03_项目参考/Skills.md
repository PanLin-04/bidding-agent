# 适合增加的 Skills

> Skills 是 Claude Code 的懒加载指令文件，仅在调用时加载，不占用常驻上下文。

---

## 一、数据导入类

### 1. `/ingest-data` — 数据导入向导

**描述**: 指导数据导入全流程 (Excel→Qdrant, XLSX→PostgreSQL, CSV→Neo4j)

**内容**:
- 检查 data/ 目录下有哪些待导入文件
- 依次执行 `python main.py ingest`, `python src/database/to_postgresql.py`, `python src/database/to_neo4j.py`
- 验证导入结果 (检查 Qdrant 点数, PG 行数, Neo4j 节点数)
- 报告导入统计

**触发词**: "导入数据", "更新数据", "重建索引"

---

### 2. `/batch-process` — 批处理脚本指南

**描述**: 帮用户选择合适的批处理脚本并运行

**内容**:
- 去重: `batch/去重.py` — 按项目编号+中标金额去重
- 提取JSONL: `batch/提数据_编JSONL.py` — 项目名称→batch request
- 插标的物: `batch/提数据_插标的物.py` — JSONL结果→Excel
- 统计频次: `batch/统计数据_插交易频次.py` — 标的物频率分析
- 排序: `batch/按custom_id排序.py` — JSONL按ID排序

**触发词**: "处理Excel", "去重", "统计标的物", "生成JSONL"

---

## 二、评估诊断类

### 3. `/eval-rag` — RAG 质量评估

**描述**: 运行 RAGAS 评估并解读报告

**内容**:
- 检查 eval/test_questions.json 是否需要更新
- 运行 `python eval/ragas_eval.py`
- 解读 Faithfulness/Relevancy/Recall 三项指标
- 如果某项偏低，给出优化方向 (调整RRF k值/扩大召回/优化prompt)

**触发词**: "评估RAG", "检查检索质量", "RAG得分", "Faithfulness"

---

### 4. `/eval-agent` — Agent 综合评估

**描述**: 运行 Agent 评估并诊断工具选择问题

**内容**:
- 运行 `python eval/agent_eval.py`
- 分析工具选择准确率 (哪些问题选错了工具)
- 分析延迟分布 (哪些工具拖慢了响应)
- 查看 `python eval/dashboard.py` 用户反馈数据

**触发词**: "评估Agent", "工具选择", "Agent准确率", "运行看板"

---

### 5. `/health-check` — 系统健康诊断

**描述**: 全面检查系统状态并给出修复建议

**内容**:
- 调用 `/api/health` 检查各组件状态和延迟
- 检查 .env 配置是否完整
- 检查各服务是否可连接 (Qdrant, Neo4j, PostgreSQL, Tavily)
- 给出具体修复命令

**触发词**: "检查系统", "连接不上", "健康检查", "服务挂了"

---

## 三、开发运维类

### 6. `/add-tool` — 新增 Agent 工具向导

**描述**: 指导如何新增一个 Function Calling 工具

**内容**:
- 在 `src/tools/rag_tools.py` 添加工具定义 + 执行函数 (参考现有 4 个工具的模板)
- 在 `TOOL_EXECUTORS` 注册新工具
- 如果需要新数据源，在 `src/database/` 添加对应客户端
- 更新 `src/agent.py` 的 SYSTEM_PROMPT 添加工具选择指引
- 前端 `ChatMessage.tsx` 添加新工具徽章

**触发词**: "新增工具", "添加数据源", "注册工具"

---

### 7. `/add-llm` — 接入新 LLM 提供商

**描述**: 指导如何接入新的 LLM (新云端API或新本地模型)

**内容**:
- 如果是 OpenAI 兼容接口 → 直接使用 `OpenAICompatibleClient` (参考 vllm/ollama)
- 如果是非标准接口 → 创建新 Client 类继承 `BaseLLMClient`，实现 `chat()`/`chat_stream()`/`chat_raw()`
- 在 `src/config.py` 添加配置项
- 在 `src/clients/llm_factory.py` 注册
- 前端 LLM 选择器添加新选项

**触发词**: "接入新模型", "添加LLM", "切换模型"

---

### 8. `/refactor` — 代码重构指南

**描述**: 提供项目特定的重构规范

**内容**:
- 单例模式: 模块级实例, 懒加载
- 工具注册: Pydantic BaseTool + TOOL_EXECUTORS 映射
- 客户端: BaseLLMClient ABC + Factory
- 消息流: chat() 委托 chat_stream(), 避免代码重复
- 安全: 字段白名单, 参数化查询, CAST语法避免 ::冲突

**触发词**: "重构", "整理代码", "代码结构"

---

## 四、领域专用类

### 9. `/bid-analysis` — 招投标数据分析（完成）

**描述**: 针对特定招投标分析任务的端到端流程

**内容**:
- 根据用户意图选择数据源 (RAG查法规 / Graph查关系 / PG查统计)
- 组合多个查询获取完整信息
- 生成结构化分析报告 (对比/趋势/排名)
- 建议: "对比A公司和B公司的中标情况"、"分析空调采购的价格趋势"

**触发词**: "分析招投标", "采购对比", "中标趋势", "供应商分析"

---

### 10. `/debug-answer` — 回答质量诊断（完成）

**描述**: 当用户对回答不满意时，分析问题所在

**内容**:
- 检查是否调用了正确的工具
- 检查检索结果的相关性分数
- 检查工具结果质量标签 (✅/⚠️)
- 检查联网搜索是否返回了垃圾结果
- 给出下次提问的改进建议 (换关键词/开启联网/换LLM)

**触发词**: "为什么回答不对", "回答有问题", "分析错误"

---

## 五、建议优先级

| 优先级 | Skill | 理由 |
|---|---|---|
| 🔴 高 | `/ingest-data` | 数据导入是最常见操作, 流程长易出错 |
| 🔴 高 | `/eval-rag` | 评估是持续优化RAG的关键环节 |
| 🟡 中 | `/health-check` | 调试连接问题的高频场景 |
| 🟡 中 | `/bid-analysis` | 体现领域价值, 提升用户体验 |
| 🟢 低 | `/add-tool` | 低频操作, 但文档价值高 |
| 🟢 低 | `/add-llm` | 已有4个LLM, 短期不会频繁新增 |

---

## 六、Skill 文件示例

以 `/ingest-data` 为例，创建 `.claude/skills/ingest-data/SKILL.md`:

```markdown
---
name: ingest-data
description: 数据导入向导 — Excel→Qdrant, XLSX→PG, CSV→Neo4j
---

# 数据导入向导

## 步骤

1. 列出 data/ 目录下的待导入文件:
   ls data/raw/ data/processed/
   
2. 导入 Qdrant (RAG 知识库):
   python main.py ingest

3. 导入 PostgreSQL (采购数据):
   python src/database/to_postgresql.py

4. 导入 Neo4j (知识图谱):
   python src/database/to_neo4j.py --clear

5. 验证:
   curl http://localhost:8000/api/health

   检查 points_count (Qdrant), pg_ready, graph_ready
   
## 常见问题

- PostgreSQL 连接失败: 检查 POSTGRES_PASSWORD, 确认服务已启动
- Neo4j 认证失败: 检查 NEO4J_USERNAME (AuraDB 通常用数据库名而非 neo4j)
- Qdrant 导入慢: 正常现象, BGE模型首加载需下载
```
