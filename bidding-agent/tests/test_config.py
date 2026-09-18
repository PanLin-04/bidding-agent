"""配置解析与校验。

`Settings` 的字段在**构造时**读取环境变量，因此每个用例构造新实例即可，
不必去动全局单例。
"""

from __future__ import annotations

from src.config import Settings


def _clear(monkeypatch, *keys: str) -> None:
    for key in keys:
        monkeypatch.delenv(key, raising=False)


# ---- 必需项 ----


def test_missing_required_lists_qdrant_keys(monkeypatch):
    _clear(monkeypatch, "QDRANT_URL", "QDRANT_API_KEY")
    assert set(Settings().missing_required) == {"QDRANT_URL", "QDRANT_API_KEY"}


def test_llm_key_is_not_required(monkeypatch):
    """LLM 凭据决定能力等级而非生死：缺了就降级为返回检索原文，
    不能因此拒绝启动（那是「两种模式共存」的前提）。"""
    _clear(monkeypatch, "DEEPSEEK_API_KEY")
    monkeypatch.setenv("QDRANT_URL", "https://example.qdrant.io")
    monkeypatch.setenv("QDRANT_API_KEY", "k")
    settings = Settings()
    assert settings.missing_required == []
    assert settings.llm_available is False


def test_llm_available_when_key_present(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://example.qdrant.io")
    monkeypatch.setenv("QDRANT_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert Settings().llm_available is True


# ---- 本地嵌入式模式 ----


def test_local_qdrant_detected_for_memory_and_paths(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    assert Settings().local_qdrant is True
    monkeypatch.setenv("QDRANT_URL", "./data/qdrant_local")
    assert Settings().local_qdrant is True


def test_remote_qdrant_is_not_local(monkeypatch):
    for url in ("https://x.qdrant.io", "http://localhost:6333"):
        monkeypatch.setenv("QDRANT_URL", url)
        assert Settings().local_qdrant is False


def test_local_mode_does_not_require_api_key(monkeypatch):
    _clear(monkeypatch, "QDRANT_API_KEY")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    assert Settings().missing_required == []


# ---- CORS ----


def test_cors_defaults_to_local_dev_ports(monkeypatch):
    _clear(monkeypatch, "CORS_ORIGINS")
    origins = Settings().cors_origin_list
    assert "http://localhost:3000" in origins


def test_cors_wildcard_and_explicit_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert Settings().cors_origin_list == ["*"]

    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com, https://b.example.com")
    assert Settings().cors_origin_list == ["https://a.example.com", "https://b.example.com"]


# ---- 数值回退 ----


def test_invalid_int_falls_back_to_default(monkeypatch):
    """配置项写错不该让进程崩在 import 期。"""
    monkeypatch.setenv("QDRANT_TIMEOUT", "三十")
    assert Settings().qdrant_timeout == 30


def test_hf_endpoint_is_exported_to_environment(monkeypatch):
    """必须在 huggingface_hub 读取之前写进环境，否则国内下载会走默认站点超时。"""
    import os

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    Settings()
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"
