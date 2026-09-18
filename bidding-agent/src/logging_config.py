"""日志配置（见 docs/开发文档.md §3.4）。

约定：**技术细节只进日志**（服务地址、原始异常、完整查询），用户可见的错误
提示由调用方另行组织措辞，不泄露内部信息。
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

# 第三方库吵闹且无诊断价值：HTTP 请求逐条、模型加载进度都会淹没业务日志
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "openai", "sentence_transformers", "jieba", "qdrant_client")


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志；重复调用无副作用（FastAPI reload 会二次导入模块）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
