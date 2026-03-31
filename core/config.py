from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USER_DIR = Path.home() / ".lmagent-plus"
CONFIG_PATH = USER_DIR / "config.yaml"


# --- Sub-models ---

class LocalBackendConfig(BaseModel):
    binary: Path = USER_DIR / "bin" / "llama-server"
    backend: Literal["cuda", "rocm", "vulkan", "metal", "cpu"] = "cpu"
    default_model: str = ""
    port: int = 8080
    ctx_size: int = 8192
    gpu_layers: int = -1
    threads: int = -1


class AnthropicConfig(BaseModel):
    default_model: str = "claude-sonnet-4-6"


class OpenAIConfig(BaseModel):
    default_model: str = "gpt-4o"


class CloudBackendConfig(BaseModel):
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)


class BackendsConfig(BaseModel):
    local: LocalBackendConfig = Field(default_factory=LocalBackendConfig)
    cloud: CloudBackendConfig = Field(default_factory=CloudBackendConfig)


class RoutingConfig(BaseModel):
    default: Literal["local", "cloud", "auto"] = "cloud"
    auto_fallback: bool = True
    auto_fallback_threshold: float = 0.7


class MemoryConfig(BaseModel):
    max_global_tokens: int = 2000
    max_agent_tokens: int = 1000
    session_auto_archive: bool = True
    semantic_search: bool = False


class DaemonConfig(BaseModel):
    port: int = 7771
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    web_enabled: bool = False
    web_port: int = 7772


class GUIConfig(BaseModel):
    theme: Literal["dark", "light", "system"] = "dark"
    language: str = "auto"
    show_tool_calls: bool = True
    confirm_destructive: bool = True


# --- Root config ---

class Config(BaseModel):
    version: str = "0.1"
    backends: BackendsConfig = Field(default_factory=BackendsConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    gui: GUIConfig = Field(default_factory=GUIConfig)


def _create_defaults(path: Path) -> Config:
    """Write default config.yaml to path and return the config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    config = Config()
    with path.open("w") as f:
        yaml.dump(config.model_dump(mode="json"), f, default_flow_style=False, allow_unicode=True)
    logger.info("Created default config at %s", path)
    return config


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load and validate config from disk, creating defaults on first run."""
    if not path.exists():
        logger.info("No config found, creating defaults.")
        return _create_defaults(path)

    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    try:
        return Config.model_validate(raw)
    except Exception as e:
        from core.errors import ConfigError
        raise ConfigError(f"Invalid config at {path}: {e}") from e
