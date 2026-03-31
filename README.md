# LMAgent-Plus

> Local-first AI agent orchestrator. No cloud required, no external runtime, no bullshit.

---

## What is this?

LMAgent-Plus is an all-in-one AI agent platform that runs entirely on your machine.

It downloads and manages **llama.cpp internally** — no Ollama, no LM Studio, no external dependency to install first. Pick a model, it downloads it, it runs. That's it.

You get a **desktop GUI**, a **CLI**, and an optional **web interface** (Tailscale-friendly), all talking to the same local daemon.

---

## Why another orchestrator?

Because the existing ones either:
- Require you to install 3 other things before they work
- Work great with cloud APIs, break with local models
- Give you an empty framework and zero working presets
- Are a great demo that falls apart on real tasks

LMAgent-Plus ships with **tested agent presets** that actually behave correctly with local models — meaning when you say "clone this repo", the agent runs `git clone`, not open a browser.

---

## Features

- **Self-contained runtime** — downloads llama.cpp binaries automatically (CUDA / ROCm / Vulkan / Metal / CPU)
- **Built-in model manager** — pull models from HuggingFace directly from the app
- **Smart backend selection** — detects your GPU and recommends the right backend
- **Agent presets** — `@coder`, `@writer`, `@research`, `@assistant` — ready to use out of the box
- **Custom personas** — define your own agents in YAML, no code required
- **Tool use that works** — strict tool schemas that prevent local models from hallucinating actions
- **Two-layer memory** — global context shared across agents + private memory per agent
- **MCP support** — connect external MCP servers
- **Cloud fallback** — optionally route to Claude, GPT-4 or others when needed
- **Three surfaces** — desktop GUI (Tauri), terminal TUI (Textual), web UI (optional)
- **Everything in `~/.lmagent-plus/`** — clean, readable, editable by hand or by an agent

---

## Supported backends

| Backend | Hardware | Notes |
|---------|----------|-------|
| CUDA | NVIDIA GPUs | Best performance on NVIDIA |
| ROCm | AMD workstation GPUs | Requires ROCm installed |
| Vulkan | AMD / Intel on Linux | Recommended for AMD RX series |
| Metal | Apple Silicon | Native on macOS |
| CPU | Any | Fallback — slow on models >7B |

---

## Quickstart

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/your-org/lmagent-plus/main/installer/install.sh | bash

# Windows
irm https://raw.githubusercontent.com/your-org/lmagent-plus/main/installer/install.ps1 | iex
```

The installer will:
1. Detect your OS, GPU, and available VRAM
2. Download the right llama.cpp binary
3. Recommend a model based on your hardware
4. Download the model
5. Launch the app

---

## Agent presets

| Preset | Best model | Tools | Use for |
|--------|-----------|-------|---------|
| `@coder` | Qwen3-Coder | bash, file_ops, git | Development tasks |
| `@writer` | Mistral 7B | file_ops | Writing, summarizing |
| `@research` | DeepSeek R1 | web_search | Analysis, reasoning |
| `@assistant` | Mistral 7B | configurable | General purpose |

---

## Project structure

```
lmagent-plus/
├── core/          # Python daemon — agent loop, tool registry, memory, runtime
├── personas/      # Agent presets in YAML — main contribution point
├── gui/           # Desktop app (Tauri + Svelte)
├── cli/           # Terminal TUI (Python + Textual)
├── web/           # Optional web server
└── installer/     # One-command install scripts
```

User data lives in `~/.lmagent-plus/` — see [docs/USER_DIR.md](docs/USER_DIR.md).

---

## Contributing

The easiest way to contribute is **creating or improving agent personas** — no Python knowledge required, just YAML.

```bash
cp personas/_base.yaml personas/my-agent.yaml
# edit, test, open a PR
```

For everything else, see [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

Early development — not ready for production use.

See [PLAN.md](PLAN.md) for the current roadmap and phase status.

---

## License

TBD
