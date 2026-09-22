# 适合增加的 MCP 工具

> MCP (Model Context Protocol) — Claude Code 的外接工具协议，连接外部数据源和服务。

---

## 一、MCP 概述

MCP 工具与项目内置 Agent 工具 (`search_bidding_knowledge` 等) 的区别：

| | Agent 工具 | MCP 工具 |
|---|---|---|
| **运行位置** | 项目后端 Python 进程 | 独立进程 (本地/远程) |
| **调用方** | DeepSeek LLM (Function Calling) | Claude Code 自身 |
| **用途** | 回答用户招投标问题 | 辅助开发/运维/调试 |
| **生命周期** | 随 API 服务启动 | 随 Claude Code 会话启动 |

---

## 二、推荐 MCP 工具

### 2.1 数据库直连类

#### 1. PostgreSQL MCP Server

**用途**: Claude Code 直接查询数据库，无需通过 Agent 工具中转

**场景**:
- 调试: "查一下 feedback 表有多少条记录"
- 运维: "最近24小时新增了多少对话"
- 分析: "统计各省采购项目的平均中标金额"

**配置** (`.mcp.json`):
```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-postgres", "postgresql://user:pass@localhost:5432/chatbot"]
    }
  }
}
```

---

#### 2. Neo4j MCP Server

**用途**: 直接查询知识图谱

**场景**:
- "列出所有 SubjectMatter 节点数量"
- "检查某个标的物是否存在于图谱中"
- "导出特定子图数据"

---

### 2.2 搜索增强类

#### 3. Brave Search MCP

**用途**: 替代/补充 Tavily，提供不同搜索引擎的联网能力

**优势**: Brave Search 对中文支持较好，结果质量可能优于 Tavily

**场景**: Agent 联网搜索时提供备选搜索引擎

---

#### 4. Exa Search MCP（完成）

**用途**: 语义搜索 (基于嵌入的网页搜索)

**优势**: 比关键词搜索更精准，适合"找类似内容"

**场景**: "搜索关于香港招投标法规的最新文章"

---

### 2.3 文件/文档类

#### 5. Filesystem MCP

**用途**: 安全的文件系统访问 (限定目录)

**场景**:
- "读取 data/raw/ 下的 Excel 文件元数据"
- "列出 eval/ 目录下所有测试用例"
- "检查日志文件大小"

**配置**:
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-filesystem", "/path/to/project/data"]
    }
  }
}
```

---

#### 6. PDF Reader MCP

**用途**: 解析招投标公告 PDF 文件

**场景**: 用户上传招标公告 PDF → MCP 提取文本 → 送入 RAG 知识库

**价值**: 招标文件通常为 PDF 格式，目前系统不支持 PDF 解析

---

### 2.4 开发工具类

#### 7. Git MCP

**用途**: 增强的 Git 操作

**场景**:
- "查看最近 10 次提交的变更统计"
- "对比两个分支的差异"
- "生成 CHANGELOG"

---

#### 8. Shell/Command MCP

**用途**: 安全执行预定义命令

**场景**:
- "重启 API 服务"
- "运行所有评估脚本并汇总结果"
- "清理临时文件"

---

### 2.5 通知/监控类

#### 9. Slack MCP

**用途**: 系统告警通知

**场景**:
- 评估分数低于阈值 → 自动发送 Slack 通知
- 用户反馈差评率高 → 告警
- 数据导入完成 → 通知

---

#### 10. Sentry MCP

**用途**: 错误监控接入

**场景**: 捕获 500 错误、LLM 调用失败等异常，自动上报

---

### 2.6 领域数据类

#### 11. 中国政府采购网 MCP (自定义)无法实现（有反爬）

**用途**: 爬取官方招投标公告

**场景**:

- "搜索最近的公开招标公告"
- "获取某项目的最新中标公示"

**实现**: 自定义 MCP Server，调用政府采购网 API 或 RSS

---

#### 12. 天眼查/企查查 MCP (自定义)

**用途**: 查询企业工商信息

**场景**: "某供应商是否有行政处罚记录", "某采购人的关联企业"

**价值**: 补充 Agent 的供应商/采购人画像维度

---

## 三、优先级建议

| 优先级 | MCP 工具 | 理由 |
|---|---|---|
| 🔴 高 | PostgreSQL MCP | 调试数据库问题的最高频场景 |
| 🔴 高 | Filesystem MCP | 安全访问数据文件, 替代 Bash |
| 🟡 中 | PDF Reader MCP | 招标文件常为PDF, 解锁新数据源 |
| 🟡 中 | Brave/Exa Search | Tavily 中文搜索结果不稳定, 提供备选 |
| 🟢 低 | Neo4j MCP | 已有 pg MCP + 内置 Graph Agent 工具 |
| 🟢 低 | 自定义政府采购网 | 开发成本高, 但领域价值大 |
| 🟢 低 | Slack MCP | 仅在需要告警自动化的场景有用 |

---

## 四、配置示例

项目根目录 `.mcp.json`:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-postgres", "postgresql://localhost:5432/chatbot"]
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-filesystem", "."]
    }
  }
}
```

用户级 `~/.claude/mcp.json` (不提交到仓库):

```json
{
  "mcpServers": {
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-brave-search"],
      "env": { "BRAVE_API_KEY": "xxx" }
    }
  }
}
```

---

## 五、注意事项

1. **MCP 工具与 Agent 工具不冲突**: MCP 是 Claude Code 的开发辅助工具，Agent 工具是用户问答工具，各司其职。
2. **安全**: 涉及密钥的 MCP Server 配置放在 `~/.claude/mcp.json` (用户级, 不提交)，不涉及密钥的放在项目 `.mcp.json`。
3. **性能**: MCP Server 工具 schema 默认延迟加载 (deferred)，仅工具名常驻上下文，不占用 token。
4. **维护**: 优先使用官方 `@anthropic/mcp-server-*` 系列，自定义 MCP 仅在确有领域需求时开发。
