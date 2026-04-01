# Architecture — LMAgent-Plus

## Repository structure

```
lmagent-plus/
├── PLAN.md                    # phases + statuses + worktree strategy
├── pyproject.toml             # project metadata and dependencies
│
├── docs/                      # technical documentation
│   ├── ARCHITECTURE.md        # this file
│   ├── MEMORY.md              # memory system
│   ├── RUNTIME.md             # llama.cpp, backends, models
│   ├── PERSONAS.md            # persona YAML format
│   └── USER_DIR.md            # ~/.lmagent-plus/ structure
│
├── core/                      # main Python daemon
│   ├── __init__.py
│   ├── __main__.py            # entry point: python -m core
│   ├── config.py              # load/validate ~/.lmagent-plus/config.yaml
│   ├── errors.py              # error hierarchy (LMAgentError subclasses)
│   ├── daemon.py              # asyncio WebSocket server on daemon.port
│   ├── ipc_protocol.py        # JSON-RPC message types (request/response)
│   ├── agent.py               # agent loop + system prompt plugin pipeline
│   ├── router.py              # backend selector: local vs cloud
│   ├── tool_registry.py       # tool registry + schema validation
│   ├── persona_loader.py      # load and validate persona YAML files
│   ├── runtime/
│   │   ├── backend_detector.py
│   │   ├── llama_manager.py
│   │   └── model_manager.py
│   ├── tools/
│   │   ├── bash.py
│   │   ├── file_ops.py
│   │   └── git.py
│   │   # v0.2: web_search.py, mcp_bridge.py
│   └── memory/
│       └── para_store.py
│       # v0.2: semantic_index.py
│
├── personas/                  # agent config YAML files
│   ├── _base.yaml             # annotated template
│   ├── coder.yaml
│   ├── writer.yaml
│   ├── research.yaml
│   ├── assistant.yaml
│   └── custom/                # user personas (gitignored)
│
├── cli/                       # terminal interface (Python + Textual)
│   └── main.py
│
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_runtime.py
│   ├── test_agent.py
│   └── test_tool_registry.py
│
│   # v0.2 directories:
├── gui/                       # native desktop (Tauri + Svelte)
│   ├── src/
│   │   ├── Chat.svelte
│   │   ├── ModelPicker.svelte
│   │   ├── ToolToggles.svelte
│   │   ├── PersonaEditor.svelte
│   │   ├── ModelManager.svelte
│   │   └── BackendSetup.svelte
│   └── src-tauri/
│
├── web/                       # optional web server (Tailscale)
│   └── server.py
│
└── installer/
    ├── install.sh
    ├── install.ps1
    └── models/
        └── recommended.yaml
```

---

## Separation of concerns

### `core/` — the brain

Runs in the background as a daemon. All surfaces (CLI, GUI, web) communicate with it via IPC
(WebSocket on the port defined in `config.yaml` under `daemon.port`).

**Never put UI logic in core. Never put business logic in gui/ or cli/.**

The daemon starts with:
```
python -m core
```

Which:
1. Loads config from `~/.lmagent-plus/config.yaml` (creates defaults on first run)
2. Starts `llama-server` if local backend is configured (Phase 1)
3. Initializes the tool registry
4. Loads active personas
5. Loads memory context
6. Starts the WebSocket IPC server on `daemon.port`

### `core/agent.py` — plugin pipeline

The system prompt is assembled via a pipeline of callables:

```python
# Each hook returns a string fragment to include in the system prompt
system_prompt_hooks: list[Callable[[], str]] = [
    persona_hook,    # injected by persona_loader (Phase 3)
    memory_hook,     # injected by para_store (Phase 4)
    tools_hook,      # always present (Phase 2)
]
system_prompt = "\n\n".join(hook() for hook in system_prompt_hooks)
```

Phases 3 and 4 register their hooks into this pipeline. They do not modify the core loop logic.

### `core/ipc_protocol.py` — IPC contract

All messages over the WebSocket use JSON-RPC 2.0. The protocol definition lives here.
CLI, GUI, and web all use the same message types.

### `personas/` — main contribution point

YAML files only. No Python knowledge required to contribute here. See `docs/PERSONAS.md`.

### `gui/` and `cli/`

Two separate surfaces sharing the same logic via the daemon. The Svelte frontend in `gui/src/`
is also reused by `web/` — single frontend codebase. (v0.2)

---

## Code conventions

### Python (`core/`, `cli/`, `web/`)

- Python 3.11+
- `asyncio` for all I/O (LLM calls, downloads, IPC)
- Type hints required on all public functions
- `pathlib.Path` everywhere — never raw strings for paths
- Secrets via env vars only — never in files
- Logging via stdlib `logging`, level configurable from `config.yaml`
- Prefer stdlib — add dependencies only when necessary

```python
# Correct
from pathlib import Path
config_path = Path.home() / ".lmagent-plus" / "config.yaml"

# Wrong
config_path = f"/home/{os.getenv('USER')}/.lmagent-plus/config.yaml"
```

### YAML (`personas/`, `config.yaml`)

- Explanatory comments on every non-obvious key
- Default values documented in `personas/_base.yaml`

### Svelte (`gui/src/`) — v0.2

- Svelte 5
- CSS custom properties for theming — no external CSS framework
- Self-contained components, shared state via dedicated Svelte stores only

### Commits

```
type(scope): short description

Types  : feat | fix | docs | refactor | test | chore
Scopes : core | gui | cli | memory | runtime | personas | installer | web
```

---

## Multi-agent architecture (Phase 2.5)

> This section documents the planned architecture. No multi-agent code exists yet.
> Implementation target: Phase 2.5.

### Delegation pattern

The key insight is to decompose large decision spaces into small sequential ones:

```
User → @assistant (orchestrator)
           ↓
    selects 1 agent        ← simple decision (1 of N agents)
           ↓
    selects among 4 tools  ← simple decision (1 of 4 tools)
```

Instead of: selecting among 25 tools in one shot.

Each specialized agent (`@coder`, `@research`, `@writer`) has:
- Its own model (can differ per agent — Qwen for coder, DeepSeek for research)
- Its own curated toolset (≤ 5 tools, see `docs/PERSONAS.md`)
- Its own memory context (loaded from the relevant PARA categories)

### Orchestrator tools

`@assistant` (the orchestrator) has access to a special delegation tool:

```python
call_agent(name: str, payload: dict) → dict
```

### Structured task payload

Messages between agents must be structured JSON, not free text.
Free text loses context across hops; structured payloads are unambiguous:

```json
{
  "task": "fix bug in auth middleware",
  "files": ["auth.py"],
  "constraints": ["do not change the public API"],
  "context": "Bug causes 401 on valid tokens after session refresh"
}
```

The `call_agent` tool validates this schema before dispatching.

### Routing strategies

In order of reliability:

1. **Explicit tool-call** — `call_agent("coder", payload)` — most reliable.
   The orchestrator's system prompt lists agents the same way other personas list tools.
   Selection is a tool-call, not a free-text reasoning step.

2. **Heuristic router** — simple rules, no ML:
   - "if task mentions file/code/git → @coder"
   - "if task mentions search/analysis/synthesis → @research"
   - "if task is prose/writing → @writer"
   Implemented as a `router.py` fallback when `call_agent` is unavailable.

3. **Classification model** — dedicated LLM for intent detection. Out of scope for v0.1.

> **Warning — main failure point**
>
> Agent routing is the single most critical point of failure in multi-agent systems.
> If `@assistant` picks the wrong agent, everything downstream breaks.
> Prefer strategy 1 (explicit tool-call) at all times.
> Strategy 2 is a fallback, not a default.

---

## Analyse d'impact

### Multi-agent architecture
**Statut :** FUTURE (Phase 2.5)

**Raison :**
Il n'existe aucun code multi-agent aujourd'hui. `call_agent()` n'est pas implémenté,
`router.py` fait uniquement du routing local/cloud (pas inter-agents).
Phase 4 (Memory) et Phase 5 (CLI) fonctionnent entièrement dans le modèle single-agent.
La documenter maintenant clarifie l'intention architecturale sans bloquer quoi que ce soit.

**Dépendances :**
- Phase 2 (outil registry) : déjà complète — fournit la base pour enregistrer `call_agent`
- Phase 3 (personas) : déjà complète — fournit le format YAML qui définira `@assistant` comme orchestrateur
- Nouveau fichier `core/tools/call_agent.py` à créer en Phase 2.5
- Mise à jour de `personas/assistant.yaml` pour utiliser `call_agent` comme outil primaire

**Blocker pour :**
- Phase 4 (Memory) ? **Non** — la mémoire est single-agent, pas inter-agents
- Phase 5 (CLI) ? **Non** — le CLI n'a pas besoin de routing multi-agent pour fonctionner
- Phase 2.5 lui-même ? **Oui** — c'est l'objet de la phase

---

## What an agent can do without confirmation

- Read any file in the repo or in `~/.lmagent-plus/`
- Create new files (new modules, new personas)
- Modify `TODO.md` and `PLAN.md` (statuses and notes only)
- Modify `~/.lmagent-plus/config.yaml`
- Add entries to memory files

## What an agent must confirm before doing

- Delete files
- Modify existing core files (except adding new functions)
- Download binaries or models
- Expose a network port
- Any action requiring elevated privileges
