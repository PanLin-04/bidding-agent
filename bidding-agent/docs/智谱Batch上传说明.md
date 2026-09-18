# 智谱 Batch 批量任务上传说明

本文档说明数据管道第 2 步与第 3 步之间的**人工环节**：把生成好的请求文件提交到智谱
AI 开放平台跑批量推理，再把结果下载回本地。对应《开发文档》§4.2 的数据管道。

```
第 2 步（本地）                智谱平台（本说明）              第 3、4 步（本地）
batch_requests.jsonl  ──上传──▶  批量任务（模型逐条回答）  ──下载──▶  batch_results_raw.jsonl
   8751 条请求                    异步执行，通常 24h 内完成              排序 → 回填 Excel
```

> **为什么需要这一步**：8751 条项目名称要逐条交给大模型抽取标的物。逐条同步调用
> 又慢又贵；Batch API 专为"不需要即时反馈"的批量任务设计，**无并发限制、价格为
> 标准价的 50%**（官方说明，GLM-4-Flash 免费；其他模型以平台价格页为准）。

---

## 1. 前置准备

### 1.1 API Key

1. 登录智谱开放平台：<https://bigmodel.cn/>
2. 进入「API Keys」页面：<https://bigmodel.cn/usercenter/proj-mgmt/apikeys>
3. 创建或复制一个 API Key
4. 填入项目根目录的 `.env` 文件（模板见 `.env.example`）：

```bash
ZHIPU_API_KEY=你的APIKey
ZHIPU_MODEL=glm-4.7-flashx
```

> API Key 只会出现在本地 `.env` 中，**不要**把它写进代码或提交到 Git。

### 1.2 请求文件

确认请求文件已由第 2 步生成（当前数据实测值）：

| 项目 | 值 |
|---|---|
| 路径 | `data/batch/batch_requests.jsonl` |
| 请求数 | **8751 条** |
| 文件大小 | 约 9.9 MB |
| 模型 | `glm-4.7-flashx`（取 `.env` 的 `ZHIPU_MODEL`） |
| 接口端点 | `/v4/chat/completions` |
| 每行格式 | `{"custom_id": "request-N", "method": "POST", "url": ..., "body": {...}}` |

若文件不存在，先在项目根目录执行：

```powershell
.venv/Scripts/python.exe batch/提数据_编JSONL.py --dry-run   # 先预演核对条数
.venv/Scripts/python.exe batch/提数据_编JSONL.py             # 正式生成
```

平台单文件限制为 **50,000 条 / 100MB**，且一个文件只能含**同一个模型**的请求。
本文件 8751 条、约 9.9MB，远低于上限，可直接上传（第 2 步脚本也会在超限时报错拦下）。

### 1.3 安装官方 SDK

项目虚拟环境当前**未安装**智谱 SDK，先安装（官方文档要求 `zai-sdk`）：

```powershell
.venv/Scripts/python.exe -m pip install zai-sdk
```

---

## 2. 上传文件并创建批量任务

在项目根目录新建一个临时 Python 文件（如 `upload_batch.py`），粘贴以下内容后运行；
任务提交成功后可删除该文件。脚本会依次完成：**上传 → 创建任务 → 轮询状态 → 下载结果**。

```python
"""一次性脚本：上传 batch_requests.jsonl 到智谱并下载结果。用完可删。"""

import time
from pathlib import Path

from dotenv import load_dotenv
from zai import ZhipuAiClient
import os

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("未在 .env 中找到 ZHIPU_API_KEY")

REQUEST_FILE = PROJECT_ROOT / "data" / "batch" / "batch_requests.jsonl"
RESULT_FILE = PROJECT_ROOT / "data" / "batch" / "batch_results_raw.jsonl"
ERROR_FILE = PROJECT_ROOT / "data" / "batch" / "batch_results_error.jsonl"

client = ZhipuAiClient(api_key=API_KEY)

# ① 上传文件（purpose 必须是 "batch"）
with REQUEST_FILE.open("rb") as f:
    file_object = client.files.create(file=f, purpose="batch")
print("文件已上传，file_id =", file_object.id)

# ② 创建批量任务（endpoint 必须与请求体里的 url 一致）
batch = client.batches.create(
    input_file_id=file_object.id,
    endpoint="/v4/chat/completions",
    auto_delete_input_file=False,  # 保留上传的原始文件，方便排查
    metadata={"description": "招投标标的物抽取 8751 条"},
)
print("任务已创建，batch_id =", batch.id)

# ③ 轮询任务状态
while True:
    status = client.batches.retrieve(batch.id)
    print(f"[{time.strftime('%H:%M:%S')}] 状态: {status.status}")
    if status.status == "completed":
        break
    if status.status in ("failed", "expired", "cancelled"):
        raise SystemExit(f"任务未成功完成，状态: {status.status}")
    time.sleep(30)

# ④ 下载成功结果（文件名必须叫 batch_results_raw.jsonl，第 3 步默认认这个名字）
result_content = client.files.content(status.output_file_id)
result_content.write_to_file(str(RESULT_FILE))
print("结果已下载:", RESULT_FILE)

# 部分请求失败时，平台会另外生成一个错误文件，也一并下载备查
if status.error_file_id:
    error_content = client.files.content(status.error_file_id)
    error_content.write_to_file(str(ERROR_FILE))
    print("错误明细已下载:", ERROR_FILE)
```

运行：

```powershell
.venv/Scripts/python.exe upload_batch.py
```

### 任务状态含义

| 状态 | 含义 | 要不要处理 |
|---|---|---|
| `validating` | 文件校验中，任务尚未开始 | 等待 |
| `failed` | 文件未通过校验 | 检查 JSONL 格式后重传 |
| `in_progress` | 校验通过，正在推理 | 等待（8751 条通常需要一段时间） |
| `finalizing` | 推理完成，正在准备结果文件 | 等待 |
| `completed` | 全部完成，结果可下载 | 下载结果 |
| `expired` / `cancelled` | 超时未完成 / 被取消 | 需重新提交 |

官方说明：任务预计 24 小时内完成；超过 7 天未处理完会自动取消。轮询脚本可以中断，
之后凭 `batch_id` 用 `client.batches.retrieve("batch_id")` 重新查询，不影响任务执行。

---

## 3. 下载后的本地处理

### 3.1 确认文件就位

`data/batch/` 目录下应出现：

- `batch_results_raw.jsonl` —— **成功结果**（第 3 步的输入，文件名不可改）
- `batch_results_error.jsonl` —— 失败请求明细（如有，仅供排查，不参与后续管道）

> **不要手工编辑或排序**结果文件。平台按完成先后返回，行序是乱的，排序由第 3 步
> 脚本完成。

### 3.2 第 3 步：按 custom_id 排序

平台不保证结果顺序，必须先按序号**数值**排回行号顺序，再回填 Excel：

```powershell
.venv/Scripts/python.exe batch/按custom_id排序.py --src data/batch/batch_results_raw.jsonl
```

输入文件名是 `batch_results_raw.jsonl` 时，脚本会自动输出为
`batch_results_processed.jsonl`（不是 `_sorted` 后缀）。

预期输出：

```
记录数 : 8751
序号   : 1 .. 8751
缺口   : 0 个
已写出 : data\batch\batch_results_processed.jsonl
```

- 出现**缺口**：第 2 步跳过空「项目名称」所致（本批 8751 条全部有名称，正常应为 0）；
- 出现**重复**：平台对同一请求返回了多条，脚本会保持原先后并提示；
- 若有 `batch_results_error.jsonl`，失败条数会体现为缺口/未填行，可对照错误文件排查。

### 3.3 第 4 步：把标的物回填进 Excel

```powershell
.venv/Scripts/python.exe batch/提数据_插标的物.py --excel "data/processed/招标采购标的物信息提取训练数据_2.xlsx"
```

> `--excel` 必须显式指定为第 1 步去重产物 `data/processed/招标采购标的物信息提取训练数据_2.xlsx`
> （与结果文件里的 `custom_id` 行号严格对应；换别的表会触发"序号越界"拦截）。

预期输出（数字以实际为准）：

```
结果文件 : data\batch\batch_results_processed.jsonl
数据行   : 8751
结果记录 : 8751
已回填   : 8751
未填行   : 0
已写出   : data\processed\招标采购标的物信息提取训练数据_2_标的物.xlsx
```

之后即可继续管道第 5 步（标的物交易频次统计）。

---

## 4. 注意事项与常见问题

1. **结果文件只保留 30 天**：平台上的结果文件到期自动删除且无法恢复，任务完成后
   请立即下载备份。
2. **`custom_id` 是对齐的唯一纽带**：格式为 `request-<数据行号>`（`request-1` =
   Excel 第 2 行第一条数据）。不要手工修改请求文件或结果文件中的 `custom_id`，
   否则第 4 步会错位或越界报错。
3. **请求文件 ≠ 结果文件**：请求文件只有 `custom_id/method/url/body` 四个字段，
   没有 `response`；若第 4 步误传请求文件，脚本会明确报错"没有 response 字段"，
   而不会静默生成一张空表。
4. **上传报文件校验失败（`failed`）**：用文本编辑器抽查 JSONL 是否每行一个合法
   JSON 对象、是否都含 `custom_id`；本项目第 2 步生成时已做行数/体积校验，正常
   不会触发。
5. **模型名要一致**：一个 batch 文件只能含单一模型的请求。若用 `--model` 重新生成
   过请求文件，平台上创建任务时无需额外指定模型（模型已写在每行的 `body.model` 里），
   但结果必须配合同一份去重版 Excel 使用。
6. **费用与额度**：Batch 价格为标准价 50%，提交前请确认账户余额/资源包充足；
   可用第 2 步的 `--limit 10` 先生成 10 条小文件试跑，确认抽取效果满意后再跑全量。
7. **平台文件数量上限**：每次最多保留 1000 个已上传文件，多次提交后可在任务确认
   无误后用 `client.files.delete(file_id=...)` 清理（上面脚本设置了
   `auto_delete_input_file=False`，不会自动删）。

---

## 附：完整流程命令速查（PowerShell）

```powershell
# 0. 安装 SDK（仅首次）
.venv/Scripts/python.exe -m pip install zai-sdk

# 1. 本地第 2 步：生成请求文件（如已生成可跳过）
.venv/Scripts/python.exe batch/提数据_编JSONL.py

# 2. 上传 + 创建任务 + 轮询 + 下载（自建一次性脚本，见本文第 2 节）
.venv/Scripts/python.exe upload_batch.py

# 3. 本地第 3 步：排序平台结果
.venv/Scripts/python.exe batch/按custom_id排序.py --src data/batch/batch_results_raw.jsonl

# 4. 本地第 4 步：回填标的物到去重版 Excel
.venv/Scripts/python.exe batch/提数据_插标的物.py --excel "data/processed/招标采购标的物信息提取训练数据_2.xlsx"
```

参考资料：智谱官方文档《批量处理》<https://docs.bigmodel.cn/cn/guide/tools/batch>
