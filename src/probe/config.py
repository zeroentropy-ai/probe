"""Configuration for probe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROVIDER_ENV_VARS = {
    "zeroentropy": "ZEROENTROPY_API_KEY",
    "openai": "OPENAI_API_KEY",
    "cohere": "COHERE_API_KEY",
}

PROVIDER_PRIORITY = ["zeroentropy", "openai", "cohere"]

DEFAULT_MODELS = {
    "zeroentropy": {"embedding": "zembed-1", "rerank": "zerank-2"},
    "openai": {"embedding": "text-embedding-3-large", "rerank": None},
    "cohere": {"embedding": "embed-v4.0", "rerank": "rerank-v3.5"},
}


@dataclass
class ProbeConfig:
    """Probe configuration."""

    embedding_provider: str = "zeroentropy"
    embedding_model: str = "zembed-1"
    embedding_dimensions: int = 1280
    rerank_provider: str = "zeroentropy"
    rerank_model: str = "zerank-2"


def save_config(config: ProbeConfig, path: Path) -> None:
    """Save config to YAML file."""
    data = {
        "providers": {
            "embedding": {
                "name": config.embedding_provider,
                "model": config.embedding_model,
                "dimensions": config.embedding_dimensions,
            },
            "reranker": {
                "name": config.rerank_provider,
                "model": config.rerank_model,
            },
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def load_config(path: Path) -> ProbeConfig:
    """Load config from YAML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return ProbeConfig()

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data or "providers" not in data:
        return ProbeConfig()

    providers = data["providers"]
    embed = providers.get("embedding", {})
    rerank = providers.get("reranker", {})

    return ProbeConfig(
        embedding_provider=embed.get("name", "zeroentropy"),
        embedding_model=embed.get("model", "zembed-1"),
        embedding_dimensions=embed.get("dimensions", 1280),
        rerank_provider=rerank.get("name", "zeroentropy"),
        rerank_model=rerank.get("model", "zerank-2"),
    )


def detect_provider() -> str | None:
    """Auto-detect the best available provider from environment variables."""
    for provider in PROVIDER_PRIORITY:
        env_var = PROVIDER_ENV_VARS[provider]
        if os.environ.get(env_var):
            return provider
    return None
