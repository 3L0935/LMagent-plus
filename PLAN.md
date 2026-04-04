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

- [x] `pyproject.toml` — project metadata, dependencies (pyyaml, websockets, httpx, huggingface_hub, textual, pydantic)
- [x] `core/__init__.py`
- [x] `core/config.py` — load and validate `~/.lmagent-plus/config.yaml`, create defaults on first run
- [x] `core/errors.py` — error hierarchy: `LMAgentError > RuntimeError, ToolError, BackendError, ConfigError, IPCError`
- [x] `core/daemon.py` — asyncio WebSocket server skeleton on `config.daemon.port` (accepts connections, echo back)
- [x] `core/__main__.py` — entry point: `python -m core` starts the daemon
- [x] `tests/conftest.py` — shared pytest fixtures (temp config dir, mock config)

**Exit criterion:** `python -m core` starts, accepts a WebSocket connection, and logs startup correctly.

---

### Phase 1 — Runtime `[x]`

**Goal:** llama.cpp runs internally. A model loads. A prompt gets a response.

- [x] `core/runtime/backend_detector.py` — OS / GPU vendor / driver detection (see docs/RUNTIME.md for full spec)
- [x] `core/runtime/llama_manager.py` — llama.cpp binary download (scrape GitHub releases API), llama-server lifecycle
- [x] `core/runtime/model_manager.py` — model download from HuggingFace, local catalog management
- [x] `installer/models/recommended.yaml` — initial tested model list with hardware requirements
- [x] `tests/test_runtime.py` — mocked subprocess and HTTP calls

**Exit criterion:** `llama-server` starts automatically with a downloaded model, local API responds to a prompt.

---

### Phase 2 — Agent loop + tool use + IPC `[x]`

**Goal:** An agent receives an instruction, calls tools, chains actions. The daemon exposes this via IPC.
**Critical path**: Phases 3, 4, 5 are blocked until this merges.

- [x] `core/tool_registry.py` — tool registry, strict schema validation, tool discovery
- [x] `core/tools/bash.py`
- [x] `core/tools/file_ops.py`
- [x] `core/tools/git.py`
- [x] `core/agent.py` — agent loop: call LLM → parse tool calls → execute → loop.
  **Must define a plugin pipeline** for system prompt construction:
  `list[Callable[[], str]] → system_prompt` so Phases 3 and 4 can hook in without modifying core loop logic.
- [x] `core/router.py` — backend selector local vs cloud. Cloud-only initially; local backend added after Phase 1 merges.
- [x] `core/ipc_protocol.py` — JSON-RPC message types for the WebSocket IPC
- [x] Wire `core/daemon.py` WebSocket server to dispatch IPC messages to the agent loop
- [x] `tests/test_agent.py` — mocked LLM responses, verify tool call parsing and loop behavior
- [x] `tests/test_tool_registry.py`
- [x] Add `when_to_use` hints to all tools in the registry schema
  > Tool hints are a high-leverage, low-cost reliability improvement. Prioritize before adding more tools.
- [ ] Implement structured JSON task payload schema for agent-to-agent delegation (deferred to Phase 2.5)

**Exit criterion:** Three base use cases work without hallucinated tool calls:
1. "List files in current directory" → uses bash
2. "Read this file and summarize it" → uses file_ops
3. "Clone this repo into ~/test then list its files" → uses git + bash

---

### Phase 2.5 — Multi-agent routing `[x]`

**Goal:** `@assistant` can delegate tasks to specialized agents via a structured tool-call.
Depends on: Phase 2 complete.

- [x] `call_agent()` tool in the tool registry (`core/tools/call_agent.py`)
- [x] Structured JSON task payload schema (validated by `tool_registry.py`)
- [x] Heuristic router (rules-based, no ML) as default routing strategy in `core/router.py`
- [x] `@assistant` persona updated to use `call_agent` as primary delegation tool
- [x] `when_to_use` hints added to all tools in the registry schema

> **Note:** `when_to_use` hints (from Phase 2 above) are implemented here, not in Phase 2,
> because they are most valuable when the agent must choose among multiple agent-tools.
> Single-agent personas with ≤ 3 tools do not require them to pass the Phase 2 exit criterion.

**Exit criterion:** "fix the bug in auth.py" → `@assistant` calls `call_agent("coder", {task: ..., files: [...]})`
→ `@coder` completes the task. No free-text routing, no wrong-agent dispatch.

---

### Phase 3 — Personas `[x]`

**Goal:** Agents have distinct YAML-defined behaviors. System prompt is injected correctly.
After Phase 2 merges.

- [x] `personas/_base.yaml` — annotated template
- [x] `personas/coder.yaml`
- [x] `personas/writer.yaml`
- [x] `personas/research.yaml`
- [x] `personas/assistant.yaml`
- [x] `core/persona_loader.py` — load persona YAML, validate fields, resolve model references
- [x] Hook persona system prompt into `core/agent.py`'s plugin pipeline
- [x] Dynamic substitution of `{tools_list}` and `{memory_context}` in system prompts

**Exit criterion:** Switching personas changes available tools and system prompt behavior.

---

### Phase 4 — Memory `[x]`

**Goal:** Agents have persistent memory across sessions.
After Phase 2 merges. Scope reduced for v0.1: simple text injection only (no semantic index — see v0.2).

- [x] `core/memory/para_store.py` — filesystem PARA management in `~/.lmagent-plus/memory/`
- [x] Global memory injection into `core/agent.py`'s plugin pipeline (truncated to `max_global_tokens`)
- [x] Per-agent memory injection (truncated to `max_agent_tokens`)
- [x] Session auto-archive to `~/.lmagent-plus/sessions/` at end of conversation
- [x] Update `recent_tasks.md` at end of session

**Exit criterion:** An agent remembers tasks from the previous session.

> **v0.2 note:** `core/memory/semantic_index.py` (vector embeddings with all-MiniLM-L6-v2) is intentionally deferred.
> It adds ~500MB–2GB of dependencies (PyTorch/ONNX). Simple text injection is sufficient for v0.1.
> The `semantic_search: true` config key is reserved but has no effect until v0.2.

---

### Phase 5 — CLI `[x]`

**Goal:** Functional terminal interface (Textual TUI).
After Phase 2 merges.

- [x] `cli/main.py` — Textual TUI: chat, agent selector, model selector
- [x] Tool call display in real time
- [x] Tool toggles from the CLI
- [x] WebSocket client connecting to the daemon on `config.daemon.port`

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

### Phase 5.5 — Pre-release hardening `[x]`

**Goal:** Fix all bugs and architectural inconsistencies identified by code audit before v0.2 scope opens.

**P0 — Critical bugs**

- [ ] BUG-1 · `_openai_completion` returns `None` on success — `return resp.json()` is dead code (placed after the `raise`); non-streaming fallback in `agent.py` crashes with `AttributeError: 'NoneType'`. Fix: move `return resp.json()` outside the `if resp.status_code != 200` block.
- [ ] BUG-2 · `ensure_loaded`: `log` not defined → `NameError` on first launch — `log.info(...)` called before `log` is assigned in scope; if `llama-server` binary is missing, crashes before download. Fix: add `log = logging.getLogger(__name__)` as module-level logger.
- [ ] BUG-3 · Session archive empty in streaming mode — `_archive_session` only collects `type == "text"` events; streaming emits `text_delta`. All sessions archived with empty summary. Fix: capture `text_delta` events in `text_parts` as well.

**P1 — Architectural bugs**

- [ ] BUG-4 · Double tool list in system prompt — `_build_system_prompt()` appends its own tool list (without `when_to_use` hints) on top of the `{tools_list}` fragment already injected by the persona hook (with hints). Local models see tools listed twice. Fix: suppress the automatic list when any hook has already produced `{tools_list}`.
- [ ] BUG-5 · Sub-agents from `call_agent` have no memory hooks — `Agent` instantiated in `call_agent.py` receives neither `global_memory_hook` nor `agent_memory_hook`. Global preferences and persona memory are invisible to sub-agents. Fix: `make_call_agent_tool` must accept a `PARAStore` (or pre-built hooks) and forward them to the sub-agent.
- [ ] SMELL-2 · `call_agent` receives `AgentRouter()` instead of LLM Router — `__main__.py` line 60 passes `AgentRouter()` (keyword heuristic) where an LLM Router is expected; `AgentRouter` is silently ignored inside the tool. Fix: pass the LLM router. Corrected in `__main__.py` call site. (Absorbed by ARCH-3.)
- [ ] ARCH-1 · `call_agent` schema: restricted variant for specialized personas — currently `enum: ["coder", "writer", "research"]` only usable by `@assistant`. Fix: `make_call_agent_tool` accepts `allowed_targets: list[str]`; `@assistant` gets `["coder", "writer", "research"]`, specialists get `["assistant"]`.
- [ ] ARCH-2 · Specialized personas: add restricted `call_agent` to `tools_enabled` — `coder.yaml`, `writer.yaml`, `research.yaml` have no `call_agent`; they cannot escalate. Fix: add `call_agent` to each with `when_to_use: "Si la tâche dépasse les outils disponibles — escalader à @assistant uniquement"`.
- [ ] ARCH-3 · `__main__.py`: build both `call_agent` variants — currently one variant with wrong router. Fix: build two variants: `@assistant` → `make_call_agent_tool(router, registry, allowed_targets=["coder", "writer", "research"])`; specialists → `make_call_agent_tool(router, registry, allowed_targets=["assistant"])`. Absorbs SMELL-2.
- [ ] ARCH-4 · Anti-loop guard: block `@assistant → @assistant` — schema `enum` prevents it structurally; add explicit guard in handler: if `agent_name == current_persona_name` → raise `ToolError` immediately.

**P2 — Severe smells**

- [ ] SMELL-5 · `start_server` raises bare `RuntimeError` instead of `BackendError` — timeout path is not catchable via `except BackendError` in the router. Fix: replace with `BackendError`.
- [ ] SMELL-6 · Synchronous I/O in async `_dispatch` coroutine — `_archive_session` calls `path.write_text` / `path.open` synchronously, blocking the event loop on large contexts. Fix: wrap in `asyncio.get_event_loop().run_in_executor(None, ...)`.
- [ ] SMELL-8 · Zero tests for streaming path — 197 tests cover non-streaming only; no test covers `chat_completion_stream`, SSE tool-call accumulation, or `text_delta` vs `text` distinction. SSE parsing bugs are invisible. Fix: add `tests/test_streaming.py` with SSE mocks for local, Anthropic, and OpenAI backends.
- [ ] BUG-6 · Double `Callable` import + `TYPE_CHECKING` block at end of file in `llama_manager.py` — `Callable` imported at lines 21 and 23; `if TYPE_CHECKING` block placed at end of file instead of top. Fix: merge imports, move `TYPE_CHECKING` block to file header.

**P3 — Silent dead fields**

- [ ] MISSING-2 · `semantic_search: true` has no effect and no warning — key is reserved for v0.2; if set, nothing happens silently. Fix: log a warning at startup if `config.memory.semantic_search is True`.
- [ ] MISSING-3 · `web_enabled: true` has no effect and no warning — same pattern as MISSING-2. Fix: log a warning at startup if `config.daemon.web_enabled is True`.
- [ ] MISSING-4 · `cloud_equivalent` and `fallback_model` in personas never consulted — router always uses `config.backends.cloud.anthropic.default_model`; persona-specific fallback is ignored. Fix: in `Router._cloud_completion`, use `cloud_equivalent` from request context as `model_override` when available.
- [ ] SMELL-1 · `tools_optional` declared in YAMLs, never loaded — `persona_loader.py` and `__main__.py` ignore it completely; dead YAML. Fix (choose one): **Option A (recommended)** — implement loading in `__main__._build_agents` with the same patterns as `tools_enabled`; **Option B** — remove `tools_optional` from all YAMLs and `_base.yaml`, document the removal.

**Exit criterion:** 197 + N tests passing (N = streaming tests + bidirectional routing tests added). BUG-1 to BUG-6 confirmed fixed by code review. No silent behavior for reserved config fields. Scenario validated: `@coder` calls `@assistant` which delegates to `@research` — result propagates back to `@coder` without loop.

---

### Phase 5.6 — Multi-agent UX + daemon fixes `[x]`

**Goal:** Make multi-agent workflows usable end-to-end — per-persona model selection, pre-call setup wizard, and fix persistent daemon bugs (orphan process, double menu, false-positive errors).

**CLI — Multi-agent UX**

- [x] Per-persona model selection — `/model <id>` in a sub-agent tab saves the model for that persona only (not global); persisted in `cli_state.json` as `persona_models: dict[str, str]`; propagated in every IPC chat request
- [x] Persona picker via header icon — clicking the ⭘ header icon opens `PersonaPickerScreen` (modal ListView: name, description, model, active/tab markers); Enter = open tab, N = new chat, Esc = close
- [x] Fix double-menu bug — subclassing `HeaderIcon` fired both parent and child handlers via Textual's MRO dispatch; replaced with `action_command_palette` override on App + `COMMAND_PALETTE_BINDING = ""` + `Binding("ctrl+p", "real_command_palette")` for the real palette
- [x] Header icon tooltip — patched to "Open persona picker" in `on_mount` (overrides Textual's default "Open the command palette")
- [x] Pre-call model setup wizard — blocking prompt emitted by daemon via `persona_setup_required` event before the first sub-agent call; CLI shows numbered model list (Enter = same as @assistant); choice saved permanently to `_persona_models`
- [x] Setup persona: recommended model always shown — `default_model` from persona YAML shown even if not downloaded, marked `⬇ download needed`; download triggered automatically when selected

**Daemon — Bidirectional mid-stream communication**

- [x] `core/context_vars.py` — `ContextVar[dict[str, str]]` for `persona_models` and `ContextVar[Callable]` for `persona_setup_fn`; propagation without changing call signatures
- [x] Daemon reader task + inbox queue — `_handle_connection` restructured: `_reader` task feeds all incoming messages into `asyncio.Queue`; chat handler and `_side_channel` task consume from same queue
- [x] `_side_channel` task — runs concurrently with `agent.run()` stream; receives `persona.model.confirm` messages and resolves `asyncio.Future` objects that `call_agent` is awaiting

**Bug fixes**

- [x] Fix orphan llama-server — on daemon restart, `LocalBackendManager._proc = None` even if a previous llama-server is still running on the port; `_proc.poll()` returned non-None (dead new process), so `is_loaded = False` every message; fix: `_kill_orphan()` in `_start_sync` kills any process on the configured port matching our binary via `fuser` + `/proc/<pid>/cmdline`
- [x] Fix false-positive red in `_write_tool_result` — `"error" in body` was True for all `call_agent` results (JSON contains `"errors"` key); replaced with `output.get("error")` check on the actual dict
- [x] Robustness: `errors`/`agent_out` in tool_result handler made defensive against None/unexpected types

**Exit criterion:** User can call `@coder` from `@assistant`, get the pre-call model setup wizard on first call, select a model (with auto-download if needed), and have the choice persist for future calls. Persona picker opens on header icon click. Ctrl+P opens Textual command palette. No "loading/loaded" spam after daemon restart.

---

## v0.2 Phases (deferred)

### Phase 6 — Desktop GUI `[ ]`

**Goal:** Native graphical interface (Tauri + Svelte).
Blocked until Phase 5 ships. Requires Rust + Node toolchains.

- [ ] Tauri + Svelte setup
- [ ] `gui/src/Chat.svelte`
- [ ] `gui/src/ModelPicker.svelte`
- [ ] `gui/src/ToolToggles.svelte`
- [ ] `gui/src/PersonaEditor.svelte`
- [ ] `gui/src/ModelManager.svelte`
- [ ] `gui/src/BackendSetup.svelte`

**Exit criterion:** A non-technical user can install and use the app without touching the terminal.

---

### Phase 5.2 — CLI model management + JIT `[x]`

**Goal:** Full model lifecycle from the CLI — discover, download, switch, no daemon restart.
Blocked until Phase 5.1 ships.

- [x] JIT model load/unload — daemon starts without loading a model; loads on first request, unloads after idle timeout
- [x] `/model <id>` on a non-downloaded catalog model → prompt to download, then hot-reload llama-server
- [x] `/hf [query]` — HuggingFace model search with download; recommended catalog models shown first (tags, VRAM/RAM requirements)
- [x] `/setup` wizard — guided backend switch (rocm, vulkan, cpu…), user profile questions (language, interests → injected into system prompt as `global/preferences.md`)
- [x] Multi-agent tab view — switch between active agents in the TUI when an agent delegates

**Exit criterion:** User can discover, download and use a new local model entirely from the TUI without touching config files.

---

### Phase 7 — Installer `[ ]`

**Goal:** One-command install, from zero to a running model.
Blocked until Phases 1–5.2 ship.
**Important:** The installer is orchestration glue, not logic. It bootstraps Python and calls
`core.runtime.backend_detector` and `core.runtime.model_manager` — it does not reimplement
hardware detection in shell.

- [ ] `installer/install.sh` — Linux/macOS
- [ ] `installer/install.ps1` — Windows
- [ ] Hardware detection → model recommendation based on available RAM/VRAM (delegates to Python)
- [ ] Correct llama.cpp binary for platform (delegates to `llama_manager.py`)
- [ ] Guided first run (model selection → download → chat)

**Exit criterion:** `curl ... | bash` → working app in under 10 minutes.

---

### Phase 8 — Web access `[ ]`

**Goal:** Optional web interface for remote access via Tailscale.
Blocked until Phase 6 (reuses Svelte components).

- [ ] `web/server.py` — FastAPI server exposing the daemon API
- [ ] Web frontend (reuses Svelte components from Phase 6)
- [ ] `web_enabled` + `web_port` config in `config.yaml`
- [ ] Tailscale setup documentation

**Exit criterion:** Access from another device via Tailscale without complex network config.

---

## Progress notes

- **2026-04-01** — Phase 5.2 started. JIT load/unload complete. Bug fixed: `_idle_watcher` task self-cancelled via `unload()` → `_cancel_idle_watcher()` — `_stop_sync` never ran. Fix: skip `task.cancel()` when the caller is the idle task itself.
- **2026-04-01** — Memory write system added (outside original phase scope): `update_memory` tool + `core/app_prompt.py` global system hook. Agents can now persist preferences/patterns without knowing filesystem paths. Decision logic for `global_preferences` vs `learned` embedded in system prompt with examples.
- **2026-04-01** — Phase 5.2 complete. `/model <id>` download flow, `/hf [query]` HuggingFace search, `/setup` wizard (backend + preferences.md), multi-agent tab view (TabbedContent), `model.reload` IPC added to daemon.
- **2026-04-02** — Fix Vulkan GPU selection: with two GPUs (iGPU Renoir index 0 + RX 6500 XT NAVI24 index 1), llama-server defaulted to the iGPU (shared RAM). Added `vulkan_device: int = -1` to `LocalBackendConfig`, injected `GGML_VULKAN_DEVICE` into subprocess env. Result: 29/29 layers offloaded to RX 6500 XT (~1 GB VRAM).
- **2026-04-02** — Fix model download: replaced `hf_hub_download` with direct httpx streaming to avoid `bad value(s) in fds_to_keep` (inherited git-lfs subprocess). Fixed progress callback: `call_from_thread` → direct call since `_download_model_httpx` runs in the main event loop.
- **2026-04-02** — Fix llama-server binary detection: release b8611 renamed assets (`ubuntu-vulkan-x64`, `.tar.gz`, `win-cpu-x64`). Extended extraction to `.so*`/`.dylib`. `LD_LIBRARY_PATH` auto-injected in `start_server()` to resolve `libmtmd.so.0`.
- **2026-04-02** — Cleanup + wizard routing/API keys. Removed `download_model()` and `huggingface_hub` dependency (replaced by direct httpx). Auto-download llama-server if binary is absent on first `ensure_loaded()` call. Enhanced `/setup` wizard with routing step (local/cloud/auto) and shell env warning for API keys — keys are not collected in the wizard (read from `os.environ` by the router). Updated `recommended.yaml` catalog (unsloth/mradermacher repos). Progress throttled to 5s interval. Backend removed from `preferences.md` (was confusing local models).
- **2026-04-02** — Fix daemon startup: `LocalBackendManager` always instantiated at boot (JIT, zero cost) — removes "local backend not enabled" error after `/setup` wizard. Wizard confirm: Enter = apply (was cancel). Wizard simplified 7→5 steps (API key steps removed, shell env warning added).
- **2026-04-02** — `/reload` redefined: full daemon restart via `daemon.restart` IPC → `os.execv(sys.executable, ["-m", "core"])`. CLI polls for reconnect for up to 10s. Fixed `sys.argv` incompatibility with `uv run`. Wizard always shows `/reload` hint after config is written.
- **2026-04-02** — Fix spurious "Loading model…" message on cloud requests (check routing before JIT load in daemon). Added "Idle unload" step to wizard (seconds before unloading from memory, 0=never, skipped for cloud routing). Wizard 5→6 steps.
- **2026-04-02** — Phase 5.3 started. Security audit identified P0 issues: path traversal in file_ops, command injection in git tools, unrestricted bash execution. Also: missing streaming, broken Anthropic multi-turn, and several quality fixes.
- **2026-04-02** — Phase 5.3 complete. All 12 fixes applied: SecurityConfig + path guard, git subprocess_exec, bash blocklist, streaming (local+cloud+CLI), Anthropic tool-result format conversion, persistent httpx client, memory deduplication, ping healthcheck, LLMRuntimeError rename, auto_fallback_threshold removed, call_agent hint shortened, integration test added. 197 tests passing.
- **2026-04-02** — Multi-persona daemon routing. Daemon now accepts `agents: dict[str, Agent]` and routes each request by `agent_id`. Each persona has a filtered ToolRegistry (tools from `tools_enabled` only), per-persona memory hooks, and an isolated system prompt. `/agent` CLI command removed (duplicate of `/persona`). `/persona` info now shows actual memory file paths and sizes. Vestigial `memory_context` field removed from all persona YAMLs. 197 tests passing.
- **2026-04-02** — Arrow key + Tab autocomplete navigation in CLI. Down/Tab cycles forward through slash command suggestions, Up cycles back. Selected entry highlighted white-on-blue. Fixed async Textual event bug: replaced sync `_completing` flag with `_completion_base` to preserve the full match list during navigation.
- **2026-04-04** — Phase 5.5 complete. 17 items fixed: BUG-1 (`_openai_completion` dead return), BUG-2 (NameError on `log` in `ensure_loaded`), BUG-3 (streaming archive empty), BUG-4 (double tool list in system prompt), BUG-5 (sub-agents missing memory hooks), BUG-6 (double import + TYPE_CHECKING at EOF), ARCH-1/2/3/4 (bidirectional routing: `allowed_targets`, specialist `call_agent`, two variants in `__main__`, anti-loop guard), SMELL-1 (`tools_optional` now loaded), SMELL-2 (AgentRouter removed, LLM router passed), SMELL-5 (`RuntimeError` → `BackendError`), SMELL-6 (sync I/O in async coroutine wrapped in executor), SMELL-8 (test_streaming.py: 12 new tests). MISSING-2/3 (warnings for reserved config keys), MISSING-4 (`cloud_equivalent` used as default model per persona). 197 → 209 tests passing.
