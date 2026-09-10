# AI编程日志

## 1.创建RAG Agent系统

创建开发环境

```bash
# 创建 .venv 虚拟环境，指定 Python 版本
uv venv --python 3.12

# 初始化项目，生成 pyproject.toml 等文件
uv init
```

创建.env文件，设置LLM和Qdrant向量数据库的URL和API KEY

```bash
# DeepSeek模型 - 获取API密钥：https://platform.deepseek.com/
DEEPSEEK_API_KEY=

# Qdrant 向量数据库配置 - 获取API密钥：https://cloud.qdrant.io/
# 使用Qdrant云服务 (推荐)
QDRANT_URL=https://e8ea4265-4dc1-4d9d-849d-eb8358c9c303.us-east-2-0.aws.cloud.qdrant.io:6333
QDRANT_API_KEY=

# Qdrant集合配置
QDRANT_COLLECTION=bid_qa
QDRANT_VECTOR_SIZE=512
QDRANT_DISTANCE=cosine
QDRANT_TIMEOUT=30

# Hugging Face - 获取API密钥：https://huggingface.co/
HF_TOKEN=
# 设置 Hugging Face 镜像源，解决国内无法直接下载模型的问题
HF_ENDPOINT=https://hf-mirror.com
```

创建data/processed文件夹，保存数据文件：问题答案样例数据20260724.xlsx

### 提示词1：

在Claude Code中切换为plan mode on模式：

```
使用python、Qdrant、BAAI/bge-small-zh-v1.5（512维）、FastAPI创建一个招投标智能问答机器人项目——bidding-qa-chatbot，要求有前端界面（React + Next.js），数据为@data/processed/问题答案样例数据20260724.xlsx，使用混合检索模式（向量 + BM25），参考来源展示。
```

执行`/init`，创建CLAUDE.md

### 提示词2：

```
重排序（Re-ranking）：对知识库RAG初步检索结果进行二次排序，提升顶部结果的相关性。
```



## 2.把RAG功能转成工具

### 提示词1：

```
把RAG功能注册成工具，同时创建Agent。当用户咨询招投标类的问题时，Agent自动调用RAG工具，并回答用户的问题。
```

## 3.增加知识图谱

用glm4.7-flash批处理数据，进行命名实体识别，标的词抽取，将温度设为0。

批量处理
https://docs.bigmodel.cn/cn/guide/tools/batch

招投标数据提取方案
https://docs.bigmodel.cn/cn/best-practice/case/data-extraction

### 提示词1：

提取标的物

```
请从以下项目名称中，提取一个字段：核心标的物（只输出具体的商品或服务名称，不要包含公司名）。项目名称：‘合肥合燃华润燃气有限公司超声波流量计检测服务项目’
```

### 提示词2：

编写去重程序

```
编写一个去重程序，用于按“项目编号”和“中标金额”去重@data/raw/招标采购标的物信息提取训练数据_s.xlsx，保留null值少的那条，保存为“batch/去重.py”
```

### 提示词3：

编写“提数据”、“编JSONL”程序

```
编写一个“提取数据”、“编写JSONL”程序，用于提取@data/processed/招标采购标的物信息提取训练数据_2.xlsx中所有“项目名称”，参考@data/batch/batch_requests_10.jsonl”，编写jsonl文件，确保"custom_id"是递增的，将“项目名称”嵌入到合适的位置，最后保存为“batch/提数据_编JSONL.py”
```

### 提示词4：

编写按"custom_id"排序的程序

```
编写一个按"custom_id"排序的程序，用于重排@data/batch/batch_results_raw.jsonl所有行，确保"custom_id"是递增的，最后保存为“batch/按custom_id排序.py”
```

在项目根目录执行：

```bash
python batch/按custom_id排序.py
```

### 提示词5：

编写“提数据”、“插标的物”程序

```
编写一个“提取数据”、“插入标的物”程序，用于从@data/batch/batch_results_processed.jsonl提取"标的物"的值，并依次插入到@data/processed/招标采购标的物信息提取训练数据_2_标的物.xlsx中的"标的物"列，并保存为“batch/提数据_插标的物.py”。
```

### 提示词5：

编写“统计数据”、“插交易频次”程序

```
编写一个“统计数据”、“插入交易频次”程序，用于统计在@data/processed/招标采购标的物信息提取训练数据_2_标的物.xlsx的"标的物"列中，每个"标的物"出现的频次（交易频次），并依次插入到@data/processed/标的物_交易频次.xlsx中的"标的物"、"交易频次"列，并保存为“batch/统计数据_插交易频次.py”。
```

**创建知识图谱文件**
“招标采购标的物信息提取训练数据\_2\_标的物.xlsx”保持副本，命名为“招标采购标的物信息提取训练数据\_2\_知识图谱.xlsx”，仅保留“标的物”、“采购商”、“代理机构”、“供应商”、“交易额”、“所在地”（市区）列。

将“招标采购标的物信息提取训练数据\_2\_知识图谱.xlsx”和“标的物_交易频次.xlsx”导出 → 更改文件类型 → CSV → 另存为。

**创建Neo4j实例**

```bash
# Neo4j 图数据库配置 - 获取API密钥：https://neo4j.com/cloud/aura/
# 使用Neo4j Aura云服务 (推荐)
# NOTE：不活动 3 天 会自动暂停（点击右上角的 ... 按钮 -> Resume），30天未使用后自动删除，最多20万节点和40万个关系
NEO4J_URI=neo4j+s://f313a350.databases.neo4j.io
NEO4J_USERNAME=f313a350
NEO4J_PASSWORD=
# AURA_INSTANCEID=f313a350
# AURA_INSTANCENAME=neo4j

# 使用本地Neo4j
# NEO4J_URI=bolt://localhost:7687
# NEO4J_USERNAME=neo4j
# NEO4J_PASSWORD=

# Neo4j连接配置
# NEO4J_DATABASE=neo4j
NEO4J_DATABASE=f313a350
NEO4J_MAX_CONNECTION_LIFETIME=3600
NEO4J_MAX_CONNECTION_POOL_SIZE=50
NEO4J_CONNECTION_TIMEOUT=60
```



### 提示词6：

编写“注入图数据库”程序

```bash
# 新版
编写一个“选择CSV文件”、“注入图数据库”的程序，用于选择@data\processed\标的物_交易频次.csv和@data\processed\招标采购标的物信息提取训练数据_2_知识图谱.csv：
CSV 1: data/processed/标的物_交易频次.csv
        → (:SubjectMatter {name, tradeFrequency})           — 标的物节点（含交易频次）

CSV 2: data/processed/招标采购标的物信息提取训练数据_2_知识图谱.csv
        → (:Transaction {id, amount})                       — 交易记录
        → (:Purchaser {name})                                — 采购人
        → (:Agency {name})                                   — 代理机构
        → (:Location {name})                                 — 项目地址
        → (:Supplier {name})                                 — 中标人
        → (:SubjectMatter)-[:HAS_TRANSACTION]->(:Transaction)
        → (:SubjectMatter)-[:PURCHASED_BY]->(:Purchaser)
        → (:SubjectMatter)-[:AGENT_BY]->(:Agency)
        → (:SubjectMatter)-[:LOCATED_AT]->(:Location)
        → (:SubjectMatter)-[:SUPPLIED_BY]->(:Supplier)

连接URI:neo4j+s://f313a350.databases.neo4j.io，将2个CSV文件数据注入图数据库中，最后保存为“to_neo4j.py。”
```

执行命令，将CSV数据注入图数据库：

```bash
# --clear：注入前清空数据库所有节点和关系
python to_neo4j.py --password password --clear
```

在浏览器中可以查看数据库：
https://console.neo4j.io/projects/e8cbc307-d275-5a23-8d68-a77dd8a632ef/instances

## 4.应用知识图谱

### 提示词1：

```
增加基于知识图谱的查询功能，需满足以下要求：
1.把查询neo4j图数据库的功能注册成工具；
2.当用户查询的问题适合用neo4j图数据库中的数据来回答时，Agent会自动调用知识图谱查询工具，并基于查询结果回答用户的问题。
```

### 提示词2：

```
将基于知识图谱的查询功能集成到前端代码中
```

| query_type      | 说明                         |      | 示例问题                      |
| --------------- | ---------------------------- | ---- | ----------------------------- |
| search_entity   | 模糊搜索标的物/采购人/供应商 |      | "有哪些关于空调的采购?"       |
| items_by_entity | 按实体查询关联标的物         |      | "合肥市采购了什么?"           |
| top_traded      | 采购频次排名                 |      | "采购最多的标的是什么?"       |
| entity_detail   | 标的物完整关系               |      | "空调的供应商和采购人有哪些?" |
| graph_stats     | 知识图谱统计                 |      | "知识图谱中有多少数据?"       |

### 提示词3：

```
把"有哪些关于空调的采购?"、"采购最多的标的是什么?"、"空调的供应商和采购人有哪些?"也加入到前端首页的示例问题中
```

### 提示词4：

```
修改bug：（问）采购企业有哪些？（答）再为您补充搜索政府机关、学校等其他类型的采购人。

<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name="search_knowledge_graph"> <｜｜DSML｜｜parameter name="keyword" string="true">管理局</｜｜DSML｜｜parameter> <｜｜DSML｜｜parameter name="query_type" string="true">search_entity</｜｜DSML｜｜parameter> </｜｜DSML｜｜invoke> <｜｜DSML｜｜invoke name="search_knowledge_graph"> <｜｜DSML｜｜parameter name="keyword" string="true">中学</｜｜DSML｜｜parameter> <｜｜DSML｜｜parameter name="query_type" string="true">search_entity</｜｜DSML｜｜parameter> </｜｜DSML｜｜invoke> </｜｜DSML｜｜tool_calls>
```

### 提示词5：

```
修改bug：目前回答有表格信息时，显示的是原始的Markdown代码，并没有渲染成表格：

| 采购人 | 采购方向 | |:---|:---| | 合肥市公安局（各分局） | 公安执法设备、办公耗材、IT设备等 | | 合肥湖滨物业管理有限公司 | 物业服务配套物资、日常运营耗材 | | 合肥市中房物业管理有限公司 | 物业管理日常物资 | | 合肥市青年路小学 | 教学设备、办公用品、打印耗材、家具等 | | 合肥市滨湖启明星幼儿园 | 幼教设施、办公用品、玩具教具等 | | 合肥市安庆路幼儿园 | 幼教物资、办公用品 | | 安徽省公共卫生临床中心 | 医用耗材、办公设备、IT设备 |
```



## 5.增加PostgreSQL

### 提示词1：

编写“选择XLSX文件”、“注入PostgreSQL”的程序

```
编写一个“选择XLSX文件”、“注入PostgreSQL”的程序，用于选择@data\processed\招标采购标的物信息提取训练数据_2_标的物.xlsx，连接POSTGRES_HOST=localhost，将文件数据注入到数据库中，最后保存为“src/database/to_postgresql.py”
```

创建数据库chatbot，注入PostgreSQL

```
python src/database/to_postgresql.py
```

## 6.应用PostgreSQL

### 提示词1：

```
增加基于PostgreSQL的查询功能，需满足以下要求：
1.把查询PostgreSQL数据库的功能注册成工具；
2.当用户查询的问题适合用PostgreSQL数据库中的数据来回答时，Agent会自动调用PostgreSQL查询工具，并基于查询结果回答用户的问题。
3.将基于PostgreSQL的查询功能集成到前端代码中。
```

## 7.图生文

### 提示词1：

前端增加图片上传图标，点击后解析图片并展示结果

```
增加功能：
1.在前端提问窗口，添加“附件”图标；当用户点击该图标后，可以上传图片文件；
2.系统使用智谱AI“glm-4.6v-flashx”模型解析图片内容，并以合适的格式展示给用户；
3.使用智谱 SDK 调用智谱AI：from zai import ZhipuAiClient
```

## 8.增加Agent功能

### 提示词1：

```
这个项目可以增加哪些Agent功能？将答案保存为“增加Agent功能.md”。
```

## 9.增加联网搜索功能

### 提示词1：

```
增加功能，当本地知识库无答案时，Agent 自动搜索最新招投标公告、法规更新，使用 Tavily 自建搜索，前端增加“联网搜索”选项，每个搜索结果显示相关性评分。
```

### 提示词2：

```
修改bug：（问）在招投标法规流程方面，中国大陆和香港有什么不同？（答）知识库中关于香港招投标法规的内容有限，让我联网搜索补充香港的相关规定，以便给您一个全面的对比。

<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name="search_web"> <｜｜DSML｜｜parameter name="query" string="true">香港政府采购招标制度 物料供应处 招标程序 与中国大陆区别</｜｜DSML｜｜parameter> </｜｜DSML｜｜invoke> </｜｜DSML｜｜tool_calls>
```

### 提示词3：

```
选择了“联网搜索”，但缺乏联网搜索内容：（问）在招投标法规流程方面，中国大陆和香港有什么不同？（答）根据提供的参考材料，其中仅涉及中国大陆招投标法规的相关规定（如中标后书面报告提交时限、必须招标的项目规模标准、总承包招标限额等），未包含任何关于香港特别行政区招投标法规流程的信息。因此，无法基于现有参考材料对两地差异进行比较分析。

如需了解香港的招投标制度，建议查阅香港特别行政区政府相关采购及招标法规（如《物料供应及采购规例》等）或咨询专业法律顾问
```

### 提示词4：

```
选择了“联网搜索”后，必需检索本地知识库和联网搜索，并基于知识库检索结果和联网搜索结果回答用户问题。
```

### 提示词5：

```
1.把联网搜索结果也加入"参考来源"面板；
2.联网搜索结果要包含URL来源
```

### 提示词6：

```
去掉侧栏状态提醒：知识库状态
  75 条问答对● 就绪
  知识图谱状态
  ● 已连接Neo4j
  PostgreSQL 状态
  ● 已连接PostgreSQL
```

### 提示词7：

```
去掉“直接 RAG”、“Agent 模式”切换按钮，默认“Agent 模式”。
```

### 提示词8：

```
将问题输入框改成OpenAI风格，将“联网搜索”开关按钮和“插入图像”图标放在输入框内部下方。
```

## 10.流式输出

### 提示词1：

```
实现流式输出功能：LLM 回答逐字流式返回，用户无需等待完整生成。
```

### 提示词2：

```
提问输入框默认改为不“联网搜索”
```

## 11.对话历史持久化

### 提示词1：

```
实现对话历史持久化：用户对话历史存入数据库，支持跨会话恢复、搜索历史对话
```

### 提示词2：

```
修改bug：无论问几个问题，侧边栏一直显示：“暂无历史对话”。
```

### 提示词3：

```
在一组历史对话右侧加3个点，点击这3个点，弹出“删除”链接，点击“删除”链接，删除该组历史对话
```

### 提示词4：

```
修改bug：每点击一次历史对话项，右侧用户就多出一次提问，例如“什么是单一来源采购？”“什么是单一来源采购？”
```

### 提示词5：

```
修改bug：“对话标题”显示的不是最初的对话标题“什么是单一来源采购？”，而是最后一个用户对话“很好”。
（问）什么是单一来源采购？
（答）根据检索结果，单一来源采购是《政府采购法》规定的一种采购方式，适用于符合法定情形之一货物或服务：
...
（问）很好
（答）感谢您的认可！如果您还有其他关于招投标领域的问题，比如：
...
```



## 12.建立评估体系

### 提示词1：

```
使用RAGAS框架对答案的**忠实度**（是否忠于检索文档）、**相关性**（是否回答用户问题）、**上下文召回率**等进行量化评估，用数据驱动优化。
```

RAGAS安装有冲突，Claude Code用LLM模拟RAGAS框架功能。

### 提示词2：

优化程序，提高RAG质量：

```
关键发现：
  1. 召回率极低 (0.15) — Qdrant 向量检索 + RRF 融合后，前 5 条文档没有覆盖问题答案所需的信息。
  2. 忠实度偏低 (0.25) — RAG 的 LLM 生成环节在检索文档信息不足时会"自由发挥"，而非诚实告知。
  
优化程序：

  - 调高 dense_limit/sparse_limit 扩大初检召回范围
  - 优化 RAG 的 LLM prompt，强制要求基于文档回答
```

## 13.LLM Clients 重构

支持多个LLM客户端

```
将llm_client.py、vision_client.py转移到“src/clients”目录下，并创建base_client.py（LLM 客户端基类定义），llm_factory.py（LLM 客户端工厂函数），deepseek_client.py（DeepSeek Responses/Chat API 的 HTTP 客户端），zai_client.py（智谱AI Responses/Chat API 的 HTTP 客户端，使用GLM-4.7-FlashX模型用于纯文本对话），vision_client.py还是使用glm-4.6v-flashx模型
```

## 14.Tools重构

```
重构Tools：将工具转移到“src/tools”目录下，改为Pydantic 模型驱动，并创建base.py，rag_tools.py（search_bidding_knowledge、search_knowledge_graph、search_postgresql、search_web）
```

## 15.Agent 改进

### 提示词1：

实现多步推理 (ReAct / Chain-of-Thought)

```
**现状**: 单轮 Tool Calling → 回答。无法"检索→分析→再检索"。

**建议**: 实现 ReAct 循环 (最多 3 轮)，每轮 LLM 可调用工具，直到信息充分。

用户问题 → [RAG: 大陆法规] → [分析] → 发现缺香港 → [Web: 香港法规] → 完整回答
```

### 提示词2：

工具调用结果的结构化验证

```
**现状**: 工具返回空结果时，Agent 仍然尝试回答。

**建议**: 工具执行后检查结果质量 (行数、相关性分数)，不达标则自动换工具或告知用户。
```

### 提示词3：

LLM 提供商切换 (Multi-LLM)

```
**现状**: 仅用 DeepSeek。@src/clients/zhipu_client.py 已实现 ZhipuClient 但未被 Agent 使用。

**建议**: Agent 支持 provider 参数，自动切换 DeepSeek / ZhipuAI。
```

## 16.RAG改进

多轮检索 (Multi-hop Retrieval)

```
**现状**: 单次检索 → 单次回答。复杂问题 (如"A和B有什么区别?") 需要多轮检索。

**建议**: Agent 检测到对比/多步问题时，自动分解为子问题，分步检索。
```

## 17.示例问题可点击发送

```
**现状**: `ChatContainer` 中 9 个示例问题为纯展示 `<span>`，不可点击。

**建议**: 改为 `<button>`，点击自动填充到输入框并发送。
```

## 18.设置LLM选择按钮

```
在前端设置一个LLM选择按钮，可以选择回答文本问题所使用的LLM。
```

## 19.`chat()` 与 `chat_stream()` 合并

```
`chat()` 与 `chat_stream()` 合并。

**现状**: 两个方法有 ~60% 重复代码 (prompt 构建、工具列表组装、工具执行)。每改一处需同步两处。

**建议**: 提取公共逻辑到 `_build_messages()` 和 `_execute_tools()` 私有方法。
```

## 20.工具执行并行化移至 BaseTool

```python
**现状**: `ThreadPoolExecutor` 逻辑在 agent.py 中，与新工具耦合。

**建议**: 在 @src/tools/base.py 添加 `ToolRunner` 类。


class ToolRunner:
    @staticmethod
    def execute_parallel(tool_calls, question) -> list[ToolResult]:
        with ThreadPoolExecutor(max_workers=4) as executor:
            ...
```

## 21.模型选择可配置化

```
**现状**: DeepSeek 模型硬编码为 `deepseek-v4-flash`。

**建议**: 在 `.env` 中增加 `LLM_MODEL` 、`LLM_PROVIDER`配置项，agent 从 settings 读取。
```

## 22.检索缓存 (Query Cache)

```python
**现状**: 相同问题反复检索 Qdrant，浪费 API 调用。

**建议**: 对 `search()` 结果做 LRU 缓存 (query embedding → doc ids)。


@lru_cache(maxsize=128)
def search(self, question: str, top_k: int = 5):
    ...
```

## 23.回答操作按钮 (复制/重新生成/反馈)

```
**现状**: 回答气泡无操作按钮。

**建议**: 每条助手消息下方添加：📋复制、🔄重新生成、👍👎反馈。
```

## 24.移动端适配

```
**现状**: 侧边栏在移动端占满屏幕，主区域不可见。

**建议**: 移动端默认收起侧边栏，添加汉堡菜单。
```

## 25.日志结构化

```
**现状**: `logging.basicConfig` 散落在多个模块，输出纯文本。

**建议**: 统一 `logging.conf` 或使用 `structlog`，输出 JSON 格式日志便于分析。
```

## 26.健康检查细化

```
**现状**: `/api/health` 只返回布尔值。

**建议**: 增加各组件延迟 (Qdrant ping、Neo4j ping、DeepSeek API latency)。
```

## 27.请求限流

```
**现状**: 无任何限流保护。

**建议**: 使用 `slowapi` (FastAPI 限流中间件)，每分钟 30 次 / IP。
```

## 28.首页布局问题

```
**现状**: 首页正文部分没有滚动条，如果放大页面，下面的文字输入框就会被挤出屏幕。

**建议**: 首页正文部分像回答问题页面正文部分一样，有滚动条。
```

## 29.配置 Schema 验证

```
**现状**: `.env` 缺失项仅在 `settings.validate()` 中检查，运行时才发现。

**建议**: 使用 `pydantic-settings` 的 `model_validator` 在启动时一次性校验所有必需配置。
```

## 30.用户反馈收集

```
**现状**: 无用户反馈机制。

**建议**: 对每条回答记录 👍/👎，存入 `feedback` 表，用于优化检索和生成策略。
```

## 31.建立Agent评估体系

### 提示词1：

```
建立Agent评估体系
```

执行结果：

```
python eval/agent_eval.py

============================================================
  Agent 综合评估报告
============================================================
  测试用例     : 10
  工具选择准确率: 10/10 (100%)
  Faithfulness  : 0.31
  Relevancy     : 0.41
  Recall        : 0.23
  平均延迟      : 16.5s (min 3.3s, max 37.7s)
  综合评分      : 0.32 / 1.00
  Agent评分     : 0.59 (RAG权重0.6 + 工具权重0.4)
============================================================
```



```
python eval/dashboard.py

==================================================
  Agent 运行看板
==================================================
  总对话数     : 3
  用户反馈     : 3 条
    👍 满意     : 3 (100%)
    👎 不满意   : 0 (0%)
  检索缓存: (暂无数据)
==================================================
```

## 32.搜索对话内容

```
添加搜索对话内容的功能
```

## 33.删除所有对话

```
添加删除所有对话的功能
```

## 34.混合检索权重可调

```
**现状**: RRF k=60 固定值。

**建议**: 根据问题类型动态调整 k 值或 Dense/Sparse 权重。法规类问题偏重 Sparse (关键词)，概念类偏重 Dense (语义)。
```

## 35.增加vLLM

增加本地LLM支持

### 提示词1：

将代码复制到带GPU的服务器上，在服务器上安装Claude Code，从Hugging Face下载LLM，ollama pull deepseek-r1:8b，增加vLLM、Ollama本地推理。

```
vLLM模型本地存储地址：@~/autodl-tmp/DeepSeek-R1-0528-Qwen3-8B

Ollama模型选择deepseek-r1:8b，模型已下载。

增加本地文本推理，在前端右上角下拉框中，在“DeepSeek”、“智谱AI”下方，增加“vLLM”、“Ollama”选项。
```

### 提示词2：

```
修复

1. 封装穿透 (agent.py:79, 128, 163, 197, 268)
# 当前: 绕过 BaseLLMClient 直接用底层 client
self.llm._client.chat.completions.create(...)

# 建议: 在 BaseLLMClient 增加 raw_chat() 方法
self.llm.chat_raw(messages, tools=..., tool_choice="auto")

2. Health check 缓存
# 建议: 加类级别缓存
_health_cache = {"time": 0, "data": None}
if time.time() - _health_cache["time"] < 30:
    return _health_cache["data"]
```

### 提示词3：

```
修改bug：在选择vLLM推理的情况下，回答依然为空。（问）什么是单一来源采购？
（答）
```

```
修改完成。这次从 3 个层面彻底修复：

  1. 后端：错误降级逻辑（src/agent.py）

  之前：只有错误消息匹配到 tool/function/not supported/invalid/400 等关键词才降级到 RAG。vLLM 的很多错误（连接失败、模型不支持等）不在此列 → 直接返回 error 事件，没有 done
  事件。

  之后：
  - 任何错误都触发 _fallback_rag 降级（因为本地模型常不支持 function calling）
  - _fallback_rag 内部加了双层 try/except 保护（知识库检索 + LLM 调用）
  - max_tokens 从 3000 提升到 8192（推理模型的思维链消耗大量 tokens）
  - LLM 返回空字符串时给出降级提示，不再让前端收到空回答

  2. 后端：推理模型 reasoning_content 兼容（上轮修复保留）

  - chat_stream、_generate、OpenAICompatibleClient.chat/chat_stream 四个位置：当 content 为空时自动 fallback 到 reasoning_content

  3. 前端：错误消息保留（frontend/app/page.tsx）

  之前：error 事件只更新了 streamedContent，但循环结束后 answer 依然是 ""，导致最终消息 content 为空，错误信息被覆盖。

  之后：error 事件同步设置 answer = streamedContent，确保错误消息保留到最终消息中。
```

### 启动vLLM

```bash
source .venv/bin/activate

# 修复 OMP_NUM_THREADS 警告
# 或设置为 CPU 核心数，如 8
export OMP_NUM_THREADS=16

# 启动服务
vllm serve /root/autodl-tmp/DeepSeek-R1-0528-Qwen3-8B \
    --trust-remote-code \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len 36000
  
# 查看显存
nvidia-smi
```

## 36.增加Ollama

### 提示词1：

```
修改bug：在选择Ollama推理的情况下，无响应问题。（问）什么是单一来源采购？
（答）正在分析您的问题...
正在思考...
```



## 37.增加Skills

### 提示词1：

```
这个项目适合增加哪些Skills？将答案保存为“Skills.md”
```

### 提示词2：

```
增加LLM客户端作为调用方的Skills：`/bid-analysis` — 招投标数据分析

**描述**: 针对特定招投标分析任务的端到端流程

**内容**:

- 根据用户意图选择数据源 (RAG查法规 / Graph查关系 / PG查统计)
- 组合多个查询获取完整信息
- 生成结构化分析报告 (对比/趋势/排名)
- 建议: "对比A公司和B公司的中标情况"、"分析空调采购的价格趋势"
```

### 提示词3：

```
增加LLM客户端作为调用方的Skills：`/debug-answer` — 回答质量诊断

**描述**: 当用户对回答不满意时，分析问题所在

**内容**:

- 检查是否调用了正确的工具
- 检查检索结果的相关性分数
- 检查工具结果质量标签 (✅/⚠️)
- 检查联网搜索是否返回了垃圾结果
- 给出下次提问的改进建议 (换关键词/开启联网/换LLM)
```

### 提示词4：

```
修改LLM客户端程序，使其会在每一轮推理中查看所有可用技能的description。当它判断某个技能与当前任务高度相关时，会自动将其加载到上下文中使用。技能路径：@src/skills/bid-analysis/SKILL.md、@src/skills/debug-answer/SKILL.md
```

### 提示词5：

```
动态技能加载：不要硬编码AVAILABLE_SKILLS，而是扫描路径@src/skills/，从SKILL.md文件中获取，同时去掉代码：(触发: {info['trigger']})。
```

### 提示词6：

修改bug

```
修改bug：Agent无法看到上一轮对话内容。
（问）对比中国大陆和香港招投标流程法规
（答）根据本地知识库的检索结果，关于中国大陆的招投标流程法规，已包含较详细的内容；但关于香港的招投标流程法规，本地知识库中未包含相关具体信息，因此无法完整进行两地的法规对比。以下基于已检索到的内容，简要梳理中国大陆的情况：
...
（问）为什么回答不完整？
（答）您提到"回答不完整"，但我目前没有看到您所指的上一轮对话内容，因此无法准确定位是哪一次回答出了问题。

常见导致回答不完整的原因包括：

问题本身范围过大——例如同时涉及多个法规体系（如中国大陆与香港的招投标制度差异很大），如果只检索了单一方向，就可能覆盖不全。我刚才就分别检索了内地和香港的相关规则。

本地知识库覆盖有限——某些具体条款、地方性规定或最新政策更新可能不在知识库中，检索不到时我会如实说明"未包含"，但这不等于完整答复。

多跳问题只检索了部分子方面——对比类、多维度问题如果漏检了某个子问题，回答就会显得不完整。

上下文被截断或未携带——在多轮对话中，如果上一轮的检索结果没有被正确继承，回答也可能中断。

如果您能提供具体是哪一次回答、哪个问题内容不完整，我可以使用 debug-answer（回答质量诊断） 技能，对那次回答进行根因分析，找出是检索遗漏、工具选择错误还是生成环节出了问题，并给出改进建议。

请您补充一下具体信息，我来帮您定位原因。
```



## 38.增加MCP工具

### 提示词1：

```
这个项目适合增加哪些MCP工具？将答案保存为“MCP.md”
```

### 提示词2：

```
增加LLM客户端作为调用方的非自定义MCP工具：Exa MCP

**用途**: 补充 Tavily，提供不同搜索引擎的联网能力

**场景**: Agent 联网搜索时提供备选搜索引擎
```

### 提示词3：

```
Exa MCP的调用方不应该是Claude Code，应该是Agent（LLM客户端）
```

### 提示词4：

```
不自定义 Exa API。不要自行编程调用Exa搜索，而是通过Exa MCP工具调用。Exa MCP的调用方不应该是Claude Code，应该是Agent（LLM客户端）。
```

### 提示词5：

```
将mcp_client.py、web_search_exa.py转移到src/mcp目录中。
```

### 提示词6（待办）：

```
增加LLM客户端作为调用方的自定义MCP工具：
```

## 39.增加测试

```
没有任何测试

- **位置**：`tests/` 目录为空，全项目无 `test_*.py`（已 find 确认）。
- **建议**：至少为以下纯逻辑补 pytest：`RateLimiter`、PG 字段白名单/聚合校验（`postgresql_client.py:191-200`）、`_fmt_*` 格式化函数、`_classify_question`、SSE 事件解析、批量脚本的清洗逻辑。配合 CI 跑 `uv sync && pytest`。
```

## 40.增加思考模式选项

把选择权留给用户

### 提示词1：

```
在聊天窗口，增加“深度思考”选项。
```

```python
# Claude Code在“auto mode”模式下自动进入“plan mode”，选择它提供的以下方案
组合：推理模型优先。
配置了推理模型（新增 .env 如 DEEPSEEK_THINKING_MODEL）时切换模型，否则回退到提示词注入。deepseek 下效果最强（真推理模型），但本地 vllm/ollama无推理模型时会自动降级为提示词方式：开启后在系统提示注入深度分析指令（先拆解问题→多角度分析→再给结论），并放宽输出 token 上限。
```

### 提示词2：

```
如果是“深度思考”模式，前端显示思考过程。
```

```python
# Claude Code在“auto mode”模式下自动进入“plan mode”，选择它提供的以下方案
可折叠面板，默认展开
同样是折叠面板，但思考完成后默认展开显示思考内容，用户可手动收起。思考过程可见性强，但界面较长。
```

## 41.再次实现流式输出

### 提示词1：

提问：

```
如果不采用openai sdk，而是采用原生python向LLM发起请求，可以让所有LLM都具备工具调用功能吗？可以既实现流式输出，又避免DeepSeek返回工具消息时，“输出以下工具消息给用户，而不是用户需要的答案”吗？<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name="search_knowledge_graph"> <｜｜DSML｜｜parameter name="keyword" string="true">管理局</｜｜DSML｜｜parameter> <｜｜DSML｜｜parameter name="query_type" string="true">search_entity</｜｜DSML｜｜parameter> </｜｜DSML｜｜invoke> <｜｜DSML｜｜invoke name="search_knowledge_graph"> <｜｜DSML｜｜parameter name="keyword" string="true">中学</｜｜DSML｜｜parameter> <｜｜DSML｜｜parameter name="query_type" string="true">search_entity</｜｜DSML｜｜parameter> </｜｜DSML｜｜invoke> </｜｜DSML｜｜tool_calls>
```

回答：

```
好问题，分三层直接回答：

  1. 原生 Python 请求 ≠ 工具调用能力来源

  工具调用能力来自两处：服务端 API 是否支持 + 模型是否真的会输出 tool_calls。跟客户端用 SDK 还是原生 httpx 无关。 OpenAI SDK 只是 OpenAI 兼容 REST
  接口的薄封装——原生 httpx.post("/v1/chat/completions", json={...tools, tool_choice...}) 与 SDK 发出的请求字节级等价，能力完全一样。

  本项目 4 个 provider（deepseek/zhipu/vllm/ollama）的服务端都已经是 OpenAI 兼容端点、都声明支持 tools。所以换原生 HTTP 不会让"所有 LLM
  都具备工具调用"——你贴的日志恰恰是模型层面的问题：模型没把工具调用放进 tool_calls 字段，而是把它当正文文本输出了。

  2. 你贴的日志是哪个环节漏出来的

  模型输出：
  输出以下工具消息给用户，而不是用户需要的答案
  <tool_calls> <invoke name="search_knowledge_graph">...

  现有防御链（_normalize_tool_content / _generate 三次约束重试 / _generate_deep 一次重试）没能拦住它，根因：

  - _looks_like_tool_call 的检测窗口是开头匹配（startswith("<") 一类）——这段输出是「正文 prose + 工具块」混合，开头是正常中文，检测直接放行；
  - 最终生成回合的 system 提示只在重试时才追加「禁止输出工具调用」，第一轮没有预先约束；
  - 工具块的格式是 Anthropic 风格 <tool_calls><invoke>，即使做子串检测也需要覆盖多种格式。

  3. 既流式又防泄漏：可以，用「先缓冲、后确认、再吐出」

  流式的难点是 token 已经发到前端就收不回来了。标准解法：

  1. 小缓冲确认：累积约 100~200 字符（或 ~200ms）再 flush 给前端；缓冲内检测到工具调用格式 →
  丢弃缓冲、追加硬约束重试——用户永远看不到泄漏文本，代价只是几百毫秒延迟；
  2. 检测增强：_looks_like_tool_call 改为全文本子串匹配多格式：<tool_call、<tool_calls、<invoke、<function_call、"name": "search_ 等；
  3. 预先约束：最终生成回合（_clean_for_final
  重建的系统消息）直接写入「当前已无工具可用，只输出最终答案，禁止输出任何工具调用格式」，而不是等检测失败再追加。

  4. 真正让"所有 LLM 都能用工具"的路径：解析工具调用文本

  你日志里模型其实想调用工具（search_knowledge_graph 管理局/中学），只是格式写在了正文里。可行做法是把这变成能力而非错误：

  - 当 API 没返回 tool_calls 但正文中出现结构化工具调用块时，解析它、执行工具、把结果回填，再让模型基于结果回答——相当于给本地模型手搓一层 Function Calling
  解析器。
  - 这样 ollama/vllm 的弱模型也能用上工具，而不是一律降级纯 RAG。

  我的建议：不要替换 SDK（零收益 + 失去重试/超时/流式迭代的已测试能力）；做第 3 节三项（检测增强 + 流式缓冲 + 预先约束）作为防御修复，第 4
  节的文本工具解析作为可选增强（收益大但改动面较大）。

  要我现在实施吗？可以做「第 3 节三项」或「第 3 + 4 节全做」。
```

### 提示词2：

```
现在实施，第 3 + 4 节全做。
```

## 42.显示回答用时

### 提示词1：

```
每次回答问题时，显示回答问题的各个阶段（如检索/工具执行/生成阶段等）分别用时多少秒，已经总用时多少秒。
```

## 43.增加系统监控功能（待办）

```
增加系统监控功能，例如内存、CPU、GPU、磁盘、日志等
```

## 44.增加.sh一键启动（待办）

## 45.增加历史消息定期压缩（待办）

## 46.迁移RAG代码

```
将 @src 目录下与RAG有关的.py文件迁移到 @rag 目录下
```

## 47.增加Agent功能1

**适合 ReAct 的场景**

### 提示词1：

**多跳检索 / 工具依赖查询**

查询示例（深度思考）：对比马鞍山市人民医院和安徽省第二人民医院在2023年的采购额

答案：结果不稳定，有时候从PostgreSQL中搜索，有时候从Neo4j中搜索

```
@src/agent.py#L163 已写"对比/多方面问题必须分别检索每个子方面"，在此增加Agent ReAct推理模式。

例如「对比 A 公司和 B 公司在 2023 年的采购额」：

- 当前实现：一次性并行调 `search_knowledge_graph` + `search_postgresql`，但**无法根据第一次结果决定要不要补查**。
- ReAct：查到 A 公司实体 → 发现其关联子公司 → 二次检索子公司采购额 → 汇总。当前架构做不到这种"基于 Observation 的链式深入"。
```

 ☐ 激活方式

ReAct 多轮推理如何激活？

总是启用（推荐）
单轮能答完的问题行为不变（首轮无工具调用就直接回答）；只有模型判断还需补查时才继续多轮。无前端改动，成本仅在有真实多轮需求时增加。

 ☐ 迭代上限

多轮检索的迭代上限设为几轮（不含最终生成回合）？

4 轮（推荐）
允许「实体→子公司→金额→补充」级别的链式深入，成本可控。达到上限后强制基于已有结果生成回答。

 ☐ 轨迹展示

多轮推理过程如何在前端展示？

仅状态文字（推荐）
每轮开始/结束更新状态文案，如「第2轮检索: 正在查询子公司采购额...」。前端几乎零改动，轨迹不落库。

 ☐ 观察轮回答

多轮循环中，观察轮（第 2+ 轮）模型不再调用工具、直接给出回答时，如何输出？

直接流式输出（推荐）
与首轮直答路径一致；多轮问题的 LLM 调用次数与现状相同（成本不变）。防编造依赖系统提示词中已有的「只能使用检索结果、严禁编造」严格约束，不经过「最终回合严格规则」二次生成。若线上出现幻觉/工具文本泄漏再切 B（一行改动）。

### 提示词2：

```
@src/agent.py 文件太长了，请拆分一下文件，保存到@src/agent目录下
```



## 47.增加Agent功能2（待办）

**2. 工具选择失败时的自我纠错**

当前在 [L155-161](file:///D:/\OneDrive\AI\Project\BIQA-Robot\Bidding_QA_Chatbot\src\agent.py#L155-161) 用硬编码映射强制约束工具选择，但模型可能选错。ReAct 可以：第一次选 `search_bidding_knowledge` 没命中 → 看到 Observation「未找到」→ 自动改用 `search_postgresql`。当前 [_validate_result](file:///D:/\OneDrive\AI\Project\BIQA-Robot\Bidding_QA_Chatbot\src\agent.py#L986) 只把"⚠️ 未找到"贴进上下文让最终回合"看着办"，没有再次调用工具的能力。

**3.联网搜索**

==回退机制==

==不满足要求，修改查询文本，继续RAG==

==提高召回率==

==原始查询+新查询==
