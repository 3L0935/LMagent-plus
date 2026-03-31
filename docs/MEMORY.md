# Memory — LMAgent-Plus

## Overview

Memory has two distinct layers:

- **Global memory** — shared across all agents, updated by the main agent
- **Private memory** — owned by each agent, not accessible to others by default

Everything is stored as markdown in `~/.lmagent-plus/memory/`. No database.
Files are human-readable and directly editable by the user or by an agent.

The vector index (`.npy`) is a **reconstructible cache** — it can be deleted without data loss.

> **v0.1 scope:** Only `para_store.py` is implemented. Memory is injected as plain text,
> truncated to the token limits in `config.yaml`. The semantic index (`semantic_index.py`)
> is planned for v0.2 and requires additional dependencies (sentence-transformers, ONNX/PyTorch).
> The `semantic_search: true` config key is reserved but has no effect in v0.1.

---

## Filesystem structure

```
~/.lmagent-plus/memory/
│
├── global/
│   ├── context.md             # global state read by all agents
│   ├── preferences.md         # user preferences (detected or configured)
│   └── index.npy + meta.json  # semantic index (cache — v0.2)
│
└── agents/
    ├── coder/
    │   ├── recent_tasks.md
    │   ├── learned.md
    │   └── index.npy + meta.json  # v0.2
    ├── writer/
    │   └── ...
    └── <agent-name>/
        └── ...
```

---

## Global memory

### Who reads it

All agents, at every session start. Injected at the beginning of the system prompt,
truncated to `max_global_tokens` (defined in `config.yaml`).

### Who modifies it

Only the main agent, or via explicit user command. Other agents must not modify `global/` directly.

### Format `global/context.md`

```markdown
# Global context

## Active projects
- [Project name] : [brief status], [repo link or path if relevant]

## Important facts
- [Fact all agents must know]
- [Persistent info about the environment, tools, constraints]

## User preferences
- [Preferred response style]
- [Favorite tools, conventions, etc.]

## Recent decisions
- YYYY-MM-DD : [decision made, why]
```

### Format `global/preferences.md`

```markdown
# User preferences

## Communication
- Language: [fr/en/...]
- Tone: [direct/formal/...]
- Detail level: [verbose/concise/...]

## Technical
- Preferred shell: [bash/fish/zsh]
- Editor: [nvim/vscode/...]
- OS: [linux/windows/macos]

## Workflow
- [Observed habits, recurring patterns]
```

---

## Per-agent private memory

### `recent_tasks.md`

Tasks performed by this agent, rolling 30 days. Entry added at the end of each session.

```markdown
# Recent tasks — Coder

## 2026-03-31
- Cloned foo/bar into ~/projects/foo
- Analyzed structure: src/, tests/, README.md

## 2026-03-30
- Refactored auth.py: extracted 3 functions (validate_token, refresh, revoke)
- Set up venv for project X, installed dependencies
```

### `learned.md`

Patterns observed across sessions. Updated when the agent detects a recurring preference.

```markdown
# Learned patterns — Coder

## Observed preferences
- Always create the directory before git clone (avoids prompts)
- Check if venv exists before pip install
- User works in Fish shell — adapt command syntax accordingly

## Mistakes to avoid
- Do not offer to open a browser for git or filesystem tasks
- Always confirm before any `rm -rf`
```

---

## Memory lifecycle

### Session start

```
1. Read global/context.md → truncate to max_global_tokens
2. Read global/preferences.md → truncate and merge
3. Read agents/<name>/recent_tasks.md → truncate to max_agent_tokens
4. Read agents/<name>/learned.md → truncate and merge
5. [v0.2] If semantic_search: true → similarity search on initial query
   → add relevant chunks (within remaining context budget)
6. Inject everything into the system prompt via the plugin pipeline
```

### Session end

```
1. Generate a session summary → save to ~/.lmagent-plus/sessions/YYYY-MM-DD-<agent>-NN.md
2. Extract tasks performed → append to agents/<name>/recent_tasks.md
3. If new patterns detected → update agents/<name>/learned.md
4. If important info for other agents → main agent updates global/context.md
5. [v0.2] Rebuild index.npy if files have changed
```

---

## Semantic index (v0.2)

> Planned for v0.2. Not implemented in v0.1.

Will be implemented in `core/memory/semantic_index.py`.

- Embedding model: `all-MiniLM-L6-v2` (384 dimensions, lightweight, local)
- Storage: `.npy` for vectors + `meta.json` for metadata (path, chunk, date)
- Chunking: by markdown paragraph (`\n\n` separator), max 512 tokens per chunk
- Rebuild: `python -m core.memory.semantic_index rebuild` — destroys and recreates the full index

When to rebuild:
- After adding or modifying memory files
- If `index.npy` is missing or corrupt
- The app detects this automatically on startup and offers to rebuild

---

## Memory parameters in `config.yaml`

```yaml
memory:
  max_global_tokens: 2000      # global context injected (global/context.md + preferences.md)
  max_agent_tokens: 1000       # per-agent context injected (recent_tasks.md + learned.md)
  session_auto_archive: true   # automatically archive at end of session
  semantic_search: false       # v0.2 — no effect in v0.1
  embedding_model: "all-MiniLM-L6-v2"  # v0.2
  chunk_max_tokens: 512        # v0.2
```
