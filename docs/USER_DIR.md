# User directory — LMAgent-Plus

## Principle

Everything user-specific lives in `~/.lmagent-plus/`.
Never in the repo. Never anywhere else.

This directory is created automatically on first launch.
It is human-readable and directly editable — by the user, a text editor,
or an AI agent.

---

## Full structure

```
~/.lmagent-plus/
│
├── config.yaml                # global app configuration
├── agents.yaml                # active agents and their overrides
│
├── bin/                       # binaries managed by the app (do not edit manually)
│   ├── llama-server           # llama.cpp binary downloaded at install
│   └── llama-cli
│
├── models/                    # downloaded models
│   ├── registry.yaml          # catalog of installed models (managed by the app)
│   └── <model-id>/
│       └── model.gguf
│
├── personas/
│   └── custom/                # user's personal personas
│       └── my-agent.yaml
│
├── memory/
│   ├── global/
│   │   ├── context.md         # global state read by all agents
│   │   ├── preferences.md     # user preferences
│   │   ├── index.npy          # vector index (cache — can be deleted)
│   │   └── meta.json          # index metadata
│   └── agents/
│       └── <agent-name>/
│           ├── recent_tasks.md
│           ├── learned.md
│           ├── index.npy
│           └── meta.json
│
├── sessions/                  # archived session history
│   └── YYYY-MM-DD-<agent>-NN.md
│
├── mcp/
│   └── servers.yaml           # MCP servers configured by the user
│
├── tools/                     # user's custom tools (Python or bash scripts)
│   └── my-tool.py
│
└── logs/
    └── daemon.log             # automatic rotation (7 days)
```

---

## `config.yaml` — complete reference

```yaml
version: "0.1"

# Available LLM backends
backends:
  local:
    binary: "~/.lmagent-plus/bin/llama-server"
    backend: "vulkan"           # cuda | rocm | vulkan | metal | cpu
    default_model: "qwen3-coder-8b-q4"
    port: 8080
    ctx_size: 8192
    gpu_layers: -1              # -1 = all layers on GPU, 0 = CPU only
    threads: -1                 # -1 = auto-detected
  cloud:
    anthropic:
      # Do not put the key here — use env var ANTHROPIC_API_KEY
      default_model: "claude-sonnet-4-6"
    openai:
      # Env var: OPENAI_API_KEY
      default_model: "gpt-4o"

# Routing logic between local and cloud
routing:
  default: "local"              # local | cloud | auto
  auto_fallback: true           # if local fails → switch to cloud
  auto_fallback_threshold: 0.7  # minimum confidence score before fallback

# Memory
memory:
  max_global_tokens: 2000       # max size of injected global context
  max_agent_tokens: 1000        # max size of injected per-agent context
  session_auto_archive: true    # archive sessions at end of conversation
  semantic_search: false        # v0.2 — no effect in v0.1
  embedding_model: "all-MiniLM-L6-v2"  # v0.2
  chunk_max_tokens: 512         # v0.2

# Daemon
daemon:
  port: 7771                    # IPC port (local WebSocket)
  log_level: "info"             # debug | info | warning | error
  web_enabled: false            # true to enable web access (Tailscale) — v0.2
  web_port: 7772                # v0.2

# Interface
gui:
  theme: "dark"                 # dark | light | system
  language: "auto"              # auto | fr | en | ...
  show_tool_calls: true         # show tool calls in chat
  confirm_destructive: true     # ask for confirmation before rm, overwrite, etc.
```

---

## `agents.yaml` — per-agent overrides

Allows overriding a persona's parameters without modifying the repo YAML.

```yaml
agents:
  coder:
    enabled: true
    model_override: "qwen3-coder-30b-q4"   # overrides persona's default_model
    tools_extra:                             # tools added on top of tools_enabled
      - web_search
    persona:
      display_name: "Dev"                   # rename agent in the UI
      avatar: "⚡"

  my-custom-agent:
    enabled: true
    persona_file: "~/.lmagent-plus/personas/custom/my-agent.yaml"
```

---

## `mcp/servers.yaml` — MCP configuration

> v0.2 — MCP bridge is not implemented in v0.1.

```yaml
servers:
  - name: "github"
    url: "https://mcp.github.com"
    enabled: true

  - name: "obsidian"
    url: "http://localhost:27124"
    enabled: false
    note: "Enable if the Obsidian MCP plugin is running"

  - name: "my-custom-server"
    command: "python ~/.local/bin/my-mcp-server.py"  # local MCP via subprocess
    enabled: false
```

---

## `tools/` — custom tools

A custom tool is a Python file that exposes a `run()` function.

```python
# ~/.lmagent-plus/tools/my-tool.py

TOOL_SCHEMA = {
    "name": "my_tool",
    "description": "What this tool does in one sentence.",
    "parameters": {
        "param1": {"type": "string", "description": "..."},
    },
    "required": ["param1"],
}

def run(param1: str) -> str:
    """
    Tool logic.
    Always returns a string (result or error message).
    """
    return f"Result: {param1}"
```

The app automatically discovers files in `tools/` and registers them.
No need to declare them anywhere else.

---

## What can be deleted without data loss

- `bin/` — will be re-downloaded if missing
- `memory/**/index.npy` and `memory/**/meta.json` — reconstructible vector index
- `logs/` — logs only, no critical data
- `sessions/` — archives only, app works without them

## What must not be deleted

- `config.yaml` — complete app configuration
- `memory/**/context.md`, `recent_tasks.md`, `learned.md` — actual memory data
- `models/` — downloaded models (large files, slow to re-download)
- `personas/custom/` — user's personal personas
