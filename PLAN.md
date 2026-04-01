# LMAgent-Plus — PLAN.md

> Source of truth for project progress.
> Update phase statuses and notes as work progresses.
> Do not change the structure — only statuses and notes.

---

## Statuses

- `[ ]` Not started
- `[~]` In progress
- `[x]` Done

---

## v0.1 Phases

### Phase 0 — Foundations `[x]`

**Goal:** Everything other phases import exists. Daemon skeleton runs. Config is loadable.
Must be merged to `main` before any worktree branches off.

- `[x]` `pyproject.toml` — project metadata, dependencies (pyyaml, websockets, httpx, huggingface_hub, textual, pydantic)
- `[x]` `core/__init__.py`
- `[x]` `core/config.py` — load and validate `~/.lmagent-plus/config.yaml`, create defaults on first run
- `[x]` `core/errors.py` — error hierarchy: `LMAgentError > RuntimeError, ToolError, BackendError, ConfigError, IPCError`
- `[x]` `core/daemon.py` — asyncio WebSocket server skeleton on `config.daemon.port` (accepts connections, echo back)
- `[x]` `core/__main__.py` — entry point: `python -m core` starts the daemon
- `[x]` `tests/conftest.py` — shared pytest fixtures (temp config dir, mock config)

**Exit criterion:** `python -m core` starts, accepts a WebSocket connection, and logs startup correctly.

---

### Phase 1 — Runtime `[x]`

**Goal:** llama.cpp runs internally. A model loads. A prompt gets a response.
Can run in parallel with Phase 2 (`feat/runtime` worktree).

- `[x]` `core/runtime/backend_detector.py` — OS / GPU vendor / driver detection (see docs/RUNTIME.md for full spec)
- `[x]` `core/runtime/llama_manager.py` — llama.cpp binary download (scrape GitHub releases API), llama-server lifecycle
- `[x]` `core/runtime/model_manager.py` — model download from HuggingFace, local catalog management
- `[x]` `installer/models/recommended.yaml` — initial tested model list with hardware requirements
- `[x]` `tests/test_runtime.py` — mocked subprocess and HTTP calls

**Exit criterion:** `llama-server` starts automatically with a downloaded model, local API responds to a prompt.

---

### Phase 2 — Agent loop + tool use + IPC `[x]`

**Goal:** An agent receives an instruction, calls tools, chains actions. The daemon exposes this via IPC.
Can run in parallel with Phase 1 (`feat/agent-loop` worktree).
**Critical path**: Phases 3, 4, 5 are blocked until this merges.

- `[x]` `core/tool_registry.py` — tool registry, strict schema validation, tool discovery
- `[x]` `core/tools/bash.py`
- `[x]` `core/tools/file_ops.py`
- `[x]` `core/tools/git.py`
- `[x]` `core/agent.py` — agent loop: call LLM → parse tool calls → execute → loop.
  **Must define a plugin pipeline** for system prompt construction:
  `list[Callable[[], str]] → system_prompt` so Phases 3 and 4 can hook in without modifying core loop logic.
- `[x]` `core/router.py` — backend selector local vs cloud. Cloud-only initially; local backend added after Phase 1 merges.
- `[x]` `core/ipc_protocol.py` — JSON-RPC message types for the WebSocket IPC
- `[x]` Wire `core/daemon.py` WebSocket server to dispatch IPC messages to the agent loop
- `[x]` `tests/test_agent.py` — mocked LLM responses, verify tool call parsing and loop behavior
- `[x]` `tests/test_tool_registry.py`
- `[x]` Add `when_to_use` hints to all tools in the registry schema
  > Tool hints are a high-leverage, low-cost reliability improvement. Prioritize before adding more tools.
- `[ ]` Implement structured JSON task payload schema for agent-to-agent delegation (deferred to Phase 2.5)

**Exit criterion:** Three base use cases work without hallucinated tool calls:
1. "List files in current directory" → uses bash
2. "Read this file and summarize it" → uses file_ops
3. "Clone this repo into ~/test then list its files" → uses git + bash

---

### Phase 2.5 — Multi-agent routing `[x]`

**Goal:** `@assistant` can delegate tasks to specialized agents via a structured tool-call.
Depends on: Phase 2 complete.
Worktree: `feat/routing` (can share `feat/agent-loop` if not yet cleaned up).

- `[x]` `call_agent()` tool in the tool registry (`core/tools/call_agent.py`)
- `[x]` Structured JSON task payload schema (validated by `tool_registry.py`)
- `[x]` Heuristic router (rules-based, no ML) as default routing strategy in `core/router.py`
- `[x]` `@assistant` persona updated to use `call_agent` as primary delegation tool
- `[x]` `when_to_use` hints added to all tools in the registry schema

> **Note:** `when_to_use` hints (from Phase 2 above) are implemented here, not in Phase 2,
> because they are most valuable when the agent must choose among multiple agent-tools.
> Single-agent personas with ≤ 3 tools do not require them to pass the Phase 2 exit criterion.

**Exit criterion:** "fix the bug in auth.py" → `@assistant` calls `call_agent("coder", {task: ..., files: [...]})`
→ `@coder` completes the task. No free-text routing, no wrong-agent dispatch.

---

### Phase 3 — Personas `[x]`

**Goal:** Agents have distinct YAML-defined behaviors. System prompt is injected correctly.
After Phase 2 merges. Parallel with Phases 4 and 5 (`feat/personas` worktree).

- `[x]` `personas/_base.yaml` — annotated template
- `[x]` `personas/coder.yaml`
- `[x]` `personas/writer.yaml`
- `[x]` `personas/research.yaml`
- `[x]` `personas/assistant.yaml`
- `[x]` `core/persona_loader.py` — load persona YAML, validate fields, resolve model references
- `[x]` Hook persona system prompt into `core/agent.py`'s plugin pipeline
- `[x]` Dynamic substitution of `{tools_list}` and `{memory_context}` in system prompts

**Exit criterion:** Switching personas changes available tools and system prompt behavior.

---

### Phase 4 — Memory `[ ]`

**Goal:** Agents have persistent memory across sessions.
After Phase 2 merges. Parallel with Phases 3 and 5 (`feat/memory` worktree).
Scope reduced for v0.1: simple text injection only (no semantic index — see v0.2).

- `[ ]` `core/memory/para_store.py` — filesystem PARA management in `~/.lmagent-plus/memory/`
- `[ ]` Global memory injection into `core/agent.py`'s plugin pipeline (trunated to `max_global_tokens`)
- `[ ]` Per-agent memory injection (truncated to `max_agent_tokens`)
- `[ ]` Session auto-archive to `~/.lmagent-plus/sessions/` at end of conversation
- `[ ]` Update `recent_tasks.md` at end of session

**Exit criterion:** An agent remembers tasks from the previous session.

> **v0.2 note:** `core/memory/semantic_index.py` (vector embeddings with all-MiniLM-L6-v2) is intentionally deferred.
> It adds ~500MB–2GB of dependencies (PyTorch/ONNX). Simple text injection is sufficient for v0.1.
> The `semantic_search: true` config key is reserved but has no effect until v0.2.

---

### Phase 5 — CLI `[ ]`

**Goal:** Functional terminal interface (Textual TUI).
After Phase 2 merges. Parallel with Phases 3 and 4 (`feat/cli` worktree).

- `[ ]` `cli/main.py` — Textual TUI: chat, agent selector, model selector
- `[ ]` Tool call display in real time
- `[ ]` Tool toggles from the CLI
- `[ ]` WebSocket client connecting to the daemon on `config.daemon.port`

**Exit criterion:** Full usage from terminal without GUI.

---

## v0.2 Phases (deferred)

### Phase 6 — Desktop GUI `[ ]`

**Goal:** Native graphical interface (Tauri + Svelte).
Blocked until Phase 5 ships. Requires Rust + Node toolchains.

- `[ ]` Tauri + Svelte setup
- `[ ]` `gui/src/Chat.svelte`
- `[ ]` `gui/src/ModelPicker.svelte`
- `[ ]` `gui/src/ToolToggles.svelte`
- `[ ]` `gui/src/PersonaEditor.svelte`
- `[ ]` `gui/src/ModelManager.svelte`
- `[ ]` `gui/src/BackendSetup.svelte`

**Exit criterion:** A non-technical user can install and use the app without touching the terminal.

---

### Phase 7 — Installer `[ ]`

**Goal:** One-command install, from zero to a running model.
Blocked until Phases 1–5 ship.
**Important:** The installer is orchestration glue, not logic. It bootstraps Python and calls
`core.runtime.backend_detector` and `core.runtime.model_manager` — it does not reimplement
hardware detection in shell.

- `[ ]` `installer/install.sh` — Linux/macOS
- `[ ]` `installer/install.ps1` — Windows
- `[ ]` Hardware detection → model recommendation based on available RAM/VRAM (delegates to Python)
- `[ ]` Correct llama.cpp binary for platform (delegates to `llama_manager.py`)
- `[ ]` Guided first run (model selection → download → chat)

**Exit criterion:** `curl ... | bash` → working app in under 10 minutes.

---

### Phase 8 — Web access `[ ]`

**Goal:** Optional web interface for remote access via Tailscale.
Blocked until Phase 6 (reuses Svelte components).

- `[ ]` `web/server.py` — FastAPI server exposing the daemon API
- `[ ]` Web frontend (reuses Svelte components from Phase 6)
- `[ ]` `web_enabled` + `web_port` config in `config.yaml`
- `[ ]` Tailscale setup documentation

**Exit criterion:** Access from another device via Tailscale without complex network config.

---

## Worktree Strategy

> This section is for the orchestrator agent. It defines which git worktrees to create,
> what each one owns, and the merge order.

### Merge order overview

```
main ← Phase 0 (no worktree, direct commit)
  │
  ├── feat/runtime       (Phase 1) ─────────────────► merge 2nd or 3rd (non-blocking)
  │
  └── feat/agent-loop    (Phase 2) ─────────────────► merge 1st ← CRITICAL PATH
       │
       ├── feat/personas  (Phase 3) ──────────────► merge after 2
       ├── feat/memory    (Phase 4) ──────────────► merge after 2  } all parallel
       └── feat/cli       (Phase 5) ──────────────► merge after 2
```

**Maximum parallelism after Phase 2 merges:** feat/runtime, feat/personas, feat/memory, and feat/cli
can all be active simultaneously — they own non-overlapping directories.

---

### Worktree: feat/foundations
- **Path:** `main` directly (no worktree — this is the starting commit)
- **Covers:** Phase 0
- **Depends on:** nothing (start here)
- **Owns:** `pyproject.toml`, `core/__init__.py`, `core/config.py`, `core/errors.py`, `core/daemon.py`, `core/__main__.py`, `tests/conftest.py`
- **Merge before:** all other worktrees branch off from this

---

### Worktree: feat/runtime
- **Path:** `../lmagent-runtime`
- **Covers:** Phase 1
- **Depends on:** Phase 0 merged to `main`
- **Owns:** `core/runtime/` (all files), `installer/models/`
- **Merge before:** `feat/agent-loop` to enable local backend in `router.py` (but not blocking — agent loop works cloud-only in the meantime)

---

### Worktree: feat/agent-loop
- **Path:** `../lmagent-agent-loop`
- **Covers:** Phase 2
- **Depends on:** Phase 0 merged to `main`
- **Owns:** `core/agent.py`, `core/router.py`, `core/tool_registry.py`, `core/tools/`, `core/ipc_protocol.py`
- **Merge before:** `feat/personas`, `feat/memory`, `feat/cli` can start

---

### Worktree: feat/personas
- **Path:** `../lmagent-personas`
- **Covers:** Phase 3
- **Depends on:** Phase 2 (`feat/agent-loop`) merged
- **Owns:** `personas/`, `core/persona_loader.py`
- **Merge before:** nothing blocking (nice-to-have before CLI final polish)

---

### Worktree: feat/memory
- **Path:** `../lmagent-memory`
- **Covers:** Phase 4
- **Depends on:** Phase 2 (`feat/agent-loop`) merged
- **Owns:** `core/memory/`
- **Merge before:** nothing blocking

---

### Worktree: feat/cli
- **Path:** `../lmagent-cli`
- **Covers:** Phase 5
- **Depends on:** Phase 2 (`feat/agent-loop`) merged
- **Owns:** `cli/`
- **Merge before:** nothing blocking

---

## Subagent Rules

> Copy this section verbatim into each subagent prompt when spawning.

When a subagent is spawned to work on a phase:

1. **Work exclusively in your assigned worktree path.** Do not `cd` outside it.
2. **Never touch files outside your owned directories** (listed in your worktree definition above).
3. **Never run `git merge`, `git rebase`, or `git push`.** Only the orchestrator does these.
4. **If you need something from another worktree that isn't merged yet:** stop immediately and report the blocker. Do not wait silently, do not work around it, do not copy-paste code across worktrees.
5. **Shared files are read-only for all subagents:** `CLAUDE.md`, `PLAN.md`, `TODO.md` — you may read them but never write to them.
6. **Read `PLAN.md` at start** to understand project context, your phase's exit criterion, and your owned files.
7. **Update `TODO.md`** only for tasks within your assigned phase, following the existing format.
8. **Do not install global system packages** without orchestrator approval. Use `pyproject.toml` optional dependencies instead.
9. **Follow the commit convention:** `type(scope): short description` — types: `feat | fix | docs | refactor | test | chore` — scopes: `core | cli | memory | runtime | personas | installer`
10. **Report completion** by summarizing: what was built, what was tested, what is blocked, what the next phase needs to know.

---

## Progress notes

<!-- Agent adds notes here as phases complete -->
