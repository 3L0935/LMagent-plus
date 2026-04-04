# LMAgent-Plus

> Local-first AI agent orchestrator. No cloud required, no external runtime, no bullshit.

---

## What is this?

LMAgent-Plus is an AI agent platform that runs entirely on your machine.

It downloads and manages **llama.cpp internally** — no Ollama, no LM Studio, nothing to install first. Pick a model, it downloads it, it runs.

A Python daemon handles the agent loop, tool execution, and memory. A Textual TUI connects to it over WebSocket. Cloud APIs (Anthropic, OpenAI) are supported as an alternative or fallback to local models.

---

## What works right now (v0.1)

- **Self-contained runtime** — downloads the right llama.cpp binary automatically (CUDA / ROCm / Vulkan / Metal / CPU)
- **Built-in model manager** — pull models from HuggingFace directly from the TUI (`/hf`, `/model`)
- **Multi-persona routing** — `@assistant`, `@coder`, `@writer`, `@research` — each with its own tool set, system prompt, memory, and model override (`/model` in any tab)
- **Tool use** — bash, file read/write/list, git, memory persistence (`update_memory`), agent delegation (`call_agent`)
- **Two-layer memory** — global context shared across personas + private memory per persona, injected at the start of each session
- **Streaming responses** — real-time output for local (llama-server SSE) and cloud (Anthropic / OpenAI)
- **Security** — path guard (blocks writes to `/etc`, `~/.ssh`, etc.), bash blocklist, git command injection protection
- **Cloud routing** — route requests to Anthropic or OpenAI instead of (or alongside) a local model
- **TUI** — slash commands, arrow key autocomplete, tab switching between agents, live tool call display, theme persistence, persona picker (header icon)

**Not yet available** — desktop GUI, web interface, installer scripts, MCP bridge, web search. See [PLAN.md](PLAN.md).

---

## Supported backends

| Backend | Hardware | Notes |
|---------|----------|-------|
| CUDA    | NVIDIA GPUs | Best performance on NVIDIA |
| ROCm    | AMD workstation GPUs | Requires ROCm installed |
| Vulkan  | AMD / Intel on Linux | Recommended for AMD RX series |
| Metal   | Apple Silicon | Native on macOS |
| CPU     | Any | Fallback — slow on models >7B |

---

## Try it

### Requirements

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or pip

### Install

```bash
git clone https://github.com/3L0935/LMagent-plus.git
cd LMagent-plus
uv sync
```

### Run

In one terminal, start the daemon:

```bash
./serve
# or: uv run lmagent-daemon
```

In another terminal, start the TUI:

```bash
./chat
# or: uv run lmagent
```

### First steps in the TUI

```
/setup          guided setup — backend, routing, model download
/hf mistral     search HuggingFace for GGUF models
/model <id>     download and load a model
/persona coder  switch to the coder persona
/tools          list tools available for the active persona
/help           full command reference
```

Use **Up / Down / Tab** to navigate slash command autocomplete.

Click the **header icon** (top-left ⭘) to open the persona picker — switch personas, see active tabs, change the model for a specific agent. **Ctrl+P** opens the command palette.

### Cloud-only (no local model)

Set your API key in the shell before starting the daemon:

```bash
export ANTHROPIC_API_KEY=sk-...
# or
export OPENAI_API_KEY=sk-...
```

Then set routing in `~/.lmagent-plus/config.yaml`:

```yaml
routing:
  default: cloud
```

---

## Personas

| Persona | Recommended local model | Min RAM | Tools | Use for |
|---------|------------------------|---------|-------|---------|
| `@assistant` | Mistral 7B | 8 GB | bash, file_ops, call_agent | General purpose, delegation |
| `@coder` | Qwen3-Coder 30B | 24 GB | bash, file_ops, git | Development tasks |
| `@writer` | Mistral 7B | 8 GB | file_ops | Writing, editing, summarizing |
| `@research` | DeepSeek R1 8B | 8 GB | file_ops | Analysis, reasoning, document review |

> VRAM figures apply to GPU inference (Vulkan/CUDA/Metal). CPU inference requires at least 2× the model size in RAM.

Switch with `/persona <name>`. Each persona gets its own memory under `~/.lmagent-plus/memory/agents/<name>/`.

Custom personas: copy `personas/_base.yaml`, edit, drop in `~/.lmagent-plus/personas/` — no code required.

---

## Project structure

```
lmagent-plus/
├── core/          # Daemon — agent loop, tool registry, memory, runtime, router
├── cli/           # Terminal TUI (Textual)
├── personas/      # Agent presets in YAML
├── installer/     # Model catalog (recommended.yaml) — install scripts deferred to v0.2
├── docs/          # Architecture, memory, runtime, persona format docs
└── tests/         # pytest suite (197 tests)
```

User data lives in `~/.lmagent-plus/` — see [docs/USER_DIR.md](docs/USER_DIR.md).

---

## Run tests

```bash
uv run pytest
```

---

## Contributing

The easiest entry point is **creating or improving personas** — no Python knowledge required.

```bash
cp personas/_base.yaml personas/my-agent.yaml
# edit, test, open a PR
```

For everything else: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| Doc | Content |
|-----|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Repo structure, code conventions |
| [docs/RUNTIME.md](docs/RUNTIME.md) | llama.cpp backends, model manager |
| [docs/MEMORY.md](docs/MEMORY.md) | Two-layer memory system |
| [docs/PERSONAS.md](docs/PERSONAS.md) | Persona YAML format, system prompt rules |
| [docs/USER_DIR.md](docs/USER_DIR.md) | `~/.lmagent-plus/` structure, config reference |

---

## Status

[![CI](https://github.com/3L0935/LMagent-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/3L0935/LMagent-plus/actions/workflows/ci.yml)

Active development — v0.1 core is complete and tested. 197 tests — `uv run pytest`. See [PLAN.md](PLAN.md) for the roadmap.

---

## License

See [LICENSE](LICENSE)
