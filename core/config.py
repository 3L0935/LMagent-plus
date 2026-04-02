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
    idle_unload_timeout: int = 0  # seconds before unloading idle model; 0 = never
    vulkan_device: int = -1  # Vulkan device index (-1 = let llama.cpp choose, 0/1/… = explicit)


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


class SecurityConfig(BaseModel):
    allowed_paths: list[str] = []  # empty = no restriction (backward compat)
    blocked_paths: list[str] = [
        "/etc", "/var", "/usr", "/boot", "/dev", "/proc", "/sys",
        "~/.ssh", "~/.gnupg", "~/.config/systemd",
    ]
    bash_blocked_patterns: list[str] = [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
        ":(){ :|:&", "> /dev/sd", "chmod -R 777 /",
        "curl * | sh", "curl * | bash", "wget * | sh", "wget * | bash",
    ]
    bash_max_timeout: int = 120  # hard cap on timeout, model can't override


class RoutingConfig(BaseModel):
    default: Literal["local", "cloud", "auto"] = "cloud"
    auto_fallback: bool = True


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
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def load_dotenv(path: Path = USER_DIR / ".env") -> None:
    """
    Load KEY=VALUE pairs from ~/.lmagent-plus/.env into os.environ.
    Silent no-op if the file does not exist.
    Does not override variables already set in the environment.
    """
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


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
