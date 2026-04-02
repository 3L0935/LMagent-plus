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
Must be merged to `main` before other phases start.

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

- `[x]` `core/runtime/backend_detector.py` — OS / GPU vendor / driver detection (see docs/RUNTIME.md for full spec)
- `[x]` `core/runtime/llama_manager.py` — llama.cpp binary download (scrape GitHub releases API), llama-server lifecycle
- `[x]` `core/runtime/model_manager.py` — model download from HuggingFace, local catalog management
- `[x]` `installer/models/recommended.yaml` — initial tested model list with hardware requirements
- `[x]` `tests/test_runtime.py` — mocked subprocess and HTTP calls

**Exit criterion:** `llama-server` starts automatically with a downloaded model, local API responds to a prompt.

---

### Phase 2 — Agent loop + tool use + IPC `[x]`

**Goal:** An agent receives an instruction, calls tools, chains actions. The daemon exposes this via IPC.
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
After Phase 2 merges.

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

### Phase 4 — Memory `[x]`

**Goal:** Agents have persistent memory across sessions.
After Phase 2 merges. Scope reduced for v0.1: simple text injection only (no semantic index — see v0.2).

- `[x]` `core/memory/para_store.py` — filesystem PARA management in `~/.lmagent-plus/memory/`
- `[x]` Global memory injection into `core/agent.py`'s plugin pipeline (truncated to `max_global_tokens`)
- `[x]` Per-agent memory injection (truncated to `max_agent_tokens`)
- `[x]` Session auto-archive to `~/.lmagent-plus/sessions/` at end of conversation
- `[x]` Update `recent_tasks.md` at end of session

**Exit criterion:** An agent remembers tasks from the previous session.

> **v0.2 note:** `core/memory/semantic_index.py` (vector embeddings with all-MiniLM-L6-v2) is intentionally deferred.
> It adds ~500MB–2GB of dependencies (PyTorch/ONNX). Simple text injection is sufficient for v0.1.
> The `semantic_search: true` config key is reserved but has no effect until v0.2.

---

### Phase 5 — CLI `[x]`

**Goal:** Functional terminal interface (Textual TUI).
After Phase 2 merges.

- `[x]` `cli/main.py` — Textual TUI: chat, agent selector, model selector
- `[x]` Tool call display in real time
- `[x]` Tool toggles from the CLI
- `[x]` WebSocket client connecting to the daemon on `config.daemon.port`

**Exit criterion:** Full usage from terminal without GUI.

---

### Phase 5.1 — CLI polish + UX fixes `[x]`

**Goal:** Bug fixes and UX improvements identified during first real use of Phase 5.
After Phase 5 ships.

- [x] Fix `@assistant` persona: `call_agent` hint ambiguity caused file writes to delegate to `writer` — clarified hint, made `file_ops` (read/write/create) explicit
- [x] Fix: theme persistence — `watch_theme` reactive saves any theme (tokyo-night, atom-one-dark…) to `~/.lmagent-plus/cli_state.json`; restored on mount. Use `ctrl+p` (Textual native palette) to change theme.
- [x] UX: slash command autocomplete — `Static#completions` above the input, filtered in real-time on `/`; hides on submit
- [x] UX: `/models` command — lists cloud models + local catalog with download status (`✓`/`·`) and tags
- [x] UX: active model shown in subtitle (`@persona | model | status`)
- [x] UX: `/model` prompts y/n reload; `/reload` command; `model_id` propagated per-request through IPC → daemon → agent → router (no daemon restart needed)
- [x] Fix: `recent_tasks.md` grows unboundedly — capped at 50 entries (oldest trimmed)

**Exit criterion:** Theme persists across restarts, model visible, file writes work from `@assistant`, model override takes effect immediately.

---

### Phase 5.3 — Security hardening + quality fixes `[x]`

**Goal:** Fix security vulnerabilities in tool execution and improve robustness of the agent loop.
Scope: no new features — only fixes, hardening, and missing quality-of-life improvements.

- [x] Path sanitization in file_ops — block writes to sensitive system paths (`_path_guard.py`, `SecurityConfig`)
- [x] Command injection fix in git tools — use `subprocess_exec` instead of shell string formatting
- [x] Bash tool blocklist — configurable blocked patterns + timeout hard cap
- [x] Streaming LLM responses — local + cloud, with non-streaming fallback
- [x] Anthropic tool-result format — convert OpenAI-format tool messages for multi-turn cloud calls
- [x] Persistent httpx client — reuse across requests instead of per-call creation
- [x] Memory deduplication — prevent repeated entries on append
- [x] Daemon healthcheck — `ping` IPC method
- [x] Rename `RuntimeError` → `LLMRuntimeError` to avoid shadowing Python builtin
- [x] Remove unused `auto_fallback_threshold` config field
- [x] Shorten `call_agent` when_to_use hint for local model reliability
- [x] Integration test — daemon ping + chat round-trip

**Exit criterion:** `pytest` passes, no tool can write to `/etc` or `~/.ssh`, bash blocklist rejects `rm -rf /`, streaming works with llama-server, Anthropic multi-turn tool calling works.

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

### Phase 5.2 — CLI model management + JIT `[x]`

**Goal:** Full model lifecycle from the CLI — discover, download, switch, no daemon restart.
Blocked until Phase 5.1 ships.

- `[x]` JIT model load/unload — daemon starts without loading a model; loads on first request, unloads after idle timeout
- `[x]` `/model <id>` on a non-downloaded catalog model → prompt to download, then hot-reload llama-server
- `[x]` `/hf [query]` — HuggingFace model search with download; recommended catalog models shown first (tags, VRAM/RAM requirements)
- `[x]` `/setup` wizard — guided backend switch (rocm, vulkan, cpu…), user profile questions (language, interests → injected into system prompt as `global/preferences.md`)
- `[x]` Multi-agent tab view — switch between active agents in the TUI when an agent delegates

**Exit criterion:** User can discover, download and use a new local model entirely from the TUI without touching config files.

---

### Phase 7 — Installer `[ ]`

**Goal:** One-command install, from zero to a running model.
Blocked until Phases 1–5.2 ship.
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

## Progress notes

- **2026-04-01** — Phase 5.2 started. JIT load/unload complete. Bug fixed: `_idle_watcher` self-cancelled via `unload()` → `_cancel_idle_watcher()` — `_stop_sync` never ran. Fix: skip `task.cancel()` when caller IS the idle task.
- **2026-04-01** — Memory write system added (outside original phase scope): `update_memory` tool + `core/app_prompt.py` global system hook. Agents can now persist preferences/patterns without knowing filesystem paths. Decision logic for `global_preferences` vs `learned` embedded in system prompt with examples.
- **2026-04-01** — Phase 5.2 complete. `/model <id>` download flow, `/hf [query]` HuggingFace search, `/setup` wizard (backend + preferences.md), multi-agent tab view (TabbedContent), `model.reload` IPC ajouté au daemon.
- **2026-04-02** — Fix GPU Vulkan device selection : avec deux GPUs (iGPU Renoir index 0 + RX 6500 XT NAVI24 index 1), llama-server prenait l'iGPU par défaut (RAM partagée). Ajout de `vulkan_device: int = -1` dans `LocalBackendConfig` (`core/config.py`), injection de `GGML_VULKAN_DEVICE` dans l'env subprocess (`core/runtime/llama_manager.py`), config utilisateur mise à jour avec `vulkan_device: 1`. Résultat : 29/29 couches offloadées sur RX 6500 XT (~1 GB VRAM).
- **2026-04-02** — Fix download httpx : `hf_hub_download` remplacé par streaming httpx direct pour éviter erreur `bad value(s) in fds_to_keep` (sous-processus git-lfs hérité). Fix progress callback : `call_from_thread` → appel direct car `_download_model_httpx` tourne dans la boucle principale.
- **2026-04-02** — Fix BINARY_PATTERNS : release b8611 renomme les assets (`ubuntu-vulkan-x64`, `.tar.gz`, `win-cpu-x64`). Extraction étendue aux `.so*`/`.dylib`. `LD_LIBRARY_PATH` injecté automatiquement dans `start_server()` pour résoudre `libmtmd.so.0`.
- **2026-04-02** — Cleanup + wizard routing/API keys. Suppression `download_model()` + dépendance `huggingface_hub` (remplacée par httpx direct). Auto-download llama-server si binaire absent au premier appel `ensure_loaded()`. Wizard `/setup` enrichi : step routing (local/cloud/auto) avec warning shell env pour les clés API cloud — clés non collectées dans le wizard (lues directement depuis os.environ par le router). Catalog `recommended.yaml` mis à jour (repos unsloth/mradermacher). Progress throttling : 10% → 5s interval. Backend retiré de `preferences.md` (confusait les modèles locaux).
- **2026-04-02** — Fix daemon : `LocalBackendManager` toujours instancié au boot (JIT, coût nul) — supprime l'erreur "local backend not enabled" après `/setup` wizard. Wizard confirm : Enter = apply (était cancel). Wizard simplifié 7→5 steps (steps clés API supprimés, warning shell env à la place).
- **2026-04-02** — `/reload` redéfini : restart complet du daemon via `daemon.restart` IPC → `os.execv(sys.executable, ["-m", "core"])`. CLI poll reconnect jusqu'à 10s. Fix `sys.argv` incompatible avec `uv run`. Wizard affiche hint `/reload` systématiquement après config écrit.
- **2026-04-02** — Fix message "Loading model…" spurieux sur requêtes cloud (check routing avant JIT load dans daemon). Step "Idle unload" ajouté au wizard (secondes avant déchargement mémoire, 0=jamais, skippé routing=cloud). Wizard 5→6 steps.
- **2026-04-02** — Phase 5.3 started. Security audit identified P0 issues: path traversal in file_ops, command injection in git tools, unrestricted bash execution. Also: missing streaming, broken Anthropic multi-turn, perf/quality fixes.
- **2026-04-02** — Phase 5.3 complete. All 12 fixes applied: SecurityConfig + path guard, git subprocess_exec, bash blocklist, streaming (local+cloud+CLI), Anthropic tool-result conversion, persistent httpx client, memory dedup, ping healthcheck, LLMRuntimeError rename, auto_fallback_threshold removed, call_agent hint shortened, integration test added. 197 tests passing.
- **2026-04-02** — Multi-persona daemon routing. Daemon now routes each request by agent_id to its own Agent instance. Each persona has a filtered ToolRegistry (only tools_enabled), per-persona memory hooks, and isolated system prompt. /agent CLI command removed (duplicate of /persona). /persona info shows actual memory file paths + sizes. memory_context field removed from all persona YAMLs (was vestigial). 197 tests passing.
- **2026-04-02** — Arrow key + Tab autocomplete navigation in CLI. Down/Tab cycles forward through slash command suggestions, Up cycles back. Selected entry highlighted white-on-blue. Fixed async Changed event bug: replaced sync _completing flag with _completion_base to preserve full match list during navigation.
