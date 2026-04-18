"""Tests for config loading and provider detection."""

from pathlib import Path

from probe.config import ProbeConfig, detect_provider, load_config, save_config


class TestConfig:
    def test_default_config(self):
        config = ProbeConfig()
        assert config.embedding_provider == "zeroentropy"
        assert config.embedding_model == "zembed-1"
        assert config.embedding_dimensions == 1280
        assert config.rerank_provider == "zeroentropy"
        assert config.rerank_model == "zerank-2"

    def test_save_and_load(self, tmp_probe_dir: Path):
        config = ProbeConfig(embedding_provider="openai", embedding_model="text-embedding-3-large")
        save_config(config, tmp_probe_dir / "config.yaml")
        loaded = load_config(tmp_probe_dir / "config.yaml")
        assert loaded.embedding_provider == "openai"
        assert loaded.embedding_model == "text-embedding-3-large"

    def test_load_nonexistent_returns_default(self, tmp_probe_dir: Path):
        config = load_config(tmp_probe_dir / "config.yaml")
        assert config.embedding_provider == "zeroentropy"


class TestProviderDetection:
    def test_detect_zeroentropy(self, monkeypatch):
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        assert detect_provider() == "zeroentropy"

    def test_detect_openai_fallback(self, monkeypatch):
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        assert detect_provider() == "openai"

    def test_detect_prefers_zeroentropy(self, monkeypatch):
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert detect_provider() == "zeroentropy"

    def test_detect_no_keys_returns_none(self, monkeypatch):
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        assert detect_provider() is None
