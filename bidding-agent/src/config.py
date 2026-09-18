"""配置中心：`.env` 加载、必需项校验、全局单例 `settings`。

设计要点（与 docs/开发文档.md §2.3/§2.4 对齐）：

- `load_dotenv(override=False)`：只填充**未设置**的变量。容器/CI 注入的真实
  环境变量优先于本地 `.env`，避免部署时被仓库里的文件顶掉。
- 校验分两级：Qdrant 缺失视为致命（没有向量库整个系统无意义）；
  LLM key 缺失**不阻断启动**——按「两种模式共存」的设计降级为直接返回
  检索到的问答原文，这是刻意的产品行为，不是配置事故。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：src/config.py → 上两级。所有相对路径（data/、.env）都以它为基准，
# 使脚本与 CLI 的 CWD 无关。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)


def _str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _int(key: str, default: int) -> int:
    raw = _str(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        # 配置写错不该让进程崩在 import 期，回退默认值并留给启动日志提示
        return default


def _bool(key: str, default: bool = False) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """全部运行期配置。字段名与 `.env` 键名一一对应，便于对照排查。"""

    # ---- 必需：向量库 ----
    qdrant_url: str = field(default_factory=lambda: _str("QDRANT_URL"))
    qdrant_api_key: str = field(default_factory=lambda: _str("QDRANT_API_KEY"))

    # ---- LLM ----
    llm_provider: str = field(default_factory=lambda: _str("LLM_PROVIDER", "deepseek"))
    deepseek_api_key: str = field(default_factory=lambda: _str("DEEPSEEK_API_KEY"))
    deepseek_model: str = field(default_factory=lambda: _str("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    deepseek_base_url: str = field(
        default_factory=lambda: _str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    )

    # ---- 向量库细节 ----
    qdrant_collection: str = field(default_factory=lambda: _str("QDRANT_COLLECTION", "bid_qa"))
    qdrant_vector_size: int = field(default_factory=lambda: _int("QDRANT_VECTOR_SIZE", 512))
    qdrant_distance: str = field(default_factory=lambda: _str("QDRANT_DISTANCE", "cosine").lower())
    qdrant_timeout: int = field(default_factory=lambda: _int("QDRANT_TIMEOUT", 30))

    # ---- 检索 ----
    # 精排（CrossEncoder）默认关闭：bge-reranker-base 约 1.1GB，首次使用需下载，
    # 80 条问答的规模下收益有限。需要时置 1 开启。
    rerank_enabled: bool = field(default_factory=lambda: _bool("RERANK_ENABLED", False))

    # 启动时后台预热嵌入模型：首次加载需数十秒（多为下载），落在请求路径里
    # 会让第一个用户以为服务挂了。测试环境置 0 以免拖慢用例。
    warmup_on_start: bool = field(default_factory=lambda: _bool("WARMUP_ON_START", True))

    # ---- 模型下载 ----
    hf_endpoint: str = field(default_factory=lambda: _str("HF_ENDPOINT", "https://hf-mirror.com"))

    # ---- API 服务 ----
    api_host: str = field(default_factory=lambda: _str("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: _int("API_PORT", 8001))
    cors_origins: str = field(default_factory=lambda: _str("CORS_ORIGINS", ""))

    # ---- 出站代理（显式配置才生效，见 §11.3）----
    http_proxy: str = field(default_factory=lambda: _str("HTTP_PROXY"))
    https_proxy: str = field(default_factory=lambda: _str("HTTPS_PROXY"))

    def __post_init__(self) -> None:
        # HF_ENDPOINT 必须在导入 sentence-transformers 之前写进环境，
        # 否则 huggingface_hub 读到的是默认站点（国内会超时）。
        if self.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)

    # ---- 校验 ----

    @property
    def missing_required(self) -> list[str]:
        """缺失的**致命**配置项。LLM key 不在其列——它决定能力等级，不决定生死。"""
        missing: list[str] = []
        if not self.qdrant_url:
            missing.append("QDRANT_URL")
        if not self.qdrant_api_key and not self.local_qdrant:
            # 本地嵌入式模式不需要 API key
            missing.append("QDRANT_API_KEY")
        return missing

    @property
    def llm_available(self) -> bool:
        """是否具备 LLM 生成能力；否则走「直接返回检索原文」的降级路径。"""
        if self.llm_provider == "deepseek":
            return bool(self.deepseek_api_key)
        return False

    @property
    def local_qdrant(self) -> bool:
        """`QDRANT_URL` 指向本地嵌入式实例时的判定。

        允许 `:memory:`（进程内、不落盘）或文件系统路径——用于离线开发与
        测试；生产配置的是 Cloud/自建服务的 http(s) 地址。
        """
        url = self.qdrant_url
        return bool(url) and not url.startswith(("http://", "https://"))

    @property
    def cors_origin_list(self) -> list[str]:
        """逗号分隔白名单；空值回退本地开发端口。"""
        if not self.cors_origins:
            return ["http://localhost:3000", "http://127.0.0.1:3000"]
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
