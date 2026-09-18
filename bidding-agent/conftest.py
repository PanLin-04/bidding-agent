"""pytest 根配置：预置必需环境变量。

有了这里的占位值，测试与 CI **不需要**仓库里存在 `.env` 或任何真实密钥
（见 docs/开发文档.md §2.4/§14.1）。注意必须在导入 `src.config` 之前设置，
否则 `Settings` 的单例已经按空配置构造完毕。
"""

from __future__ import annotations

import os

os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("QDRANT_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("LLM_PROVIDER", "deepseek")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 预热会真的加载嵌入模型（数十秒），测试里必须关掉
os.environ.setdefault("WARMUP_ON_START", "0")
