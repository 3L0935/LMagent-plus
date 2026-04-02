class LMAgentError(Exception):
    """Base exception for all LMAgent-Plus errors."""


class LLMRuntimeError(LMAgentError):
    """llama.cpp backend or subprocess failure."""


class ToolError(LMAgentError):
    """Tool execution failure."""


class BackendError(LMAgentError):
    """LLM backend (local or cloud) communication failure."""


class ConfigError(LMAgentError):
    """Configuration loading or validation failure."""


class IPCError(LMAgentError):
    """WebSocket IPC communication failure."""
