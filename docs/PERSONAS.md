# Personas — LMAgent-Plus

## Overview

A persona defines the complete behavior of an agent: model used, system prompt,
available tools, tone, and memory context loaded.

Personas are YAML files in `personas/`. This is the primary contribution point
for the community — no Python knowledge required.

---

## Creating a persona

Copy `personas/_base.yaml`, rename it, edit it. That's it.

```bash
cp personas/_base.yaml personas/custom/my-agent.yaml
```

Personas in `personas/custom/` are gitignored (user-private).
To contribute a persona to the community, place it directly in `personas/`.

---

## Full format

```yaml
# personas/_base.yaml — annotated template

# Unique identifier (used in CLI as @persona-name)
name: "persona-name"

# Short description displayed in the UI
description: "What this agent does in one sentence."

# Recommended local model for this persona (must exist in recommended.yaml)
default_model: "qwen3-coder-8b-q4"

# Fallback model if default is not installed or insufficient RAM/VRAM
fallback_model: "mistral-7b-q4"

# Cloud equivalent if the user chooses cloud mode
cloud_equivalent: "claude-sonnet-4-6"

# System prompt injected at the start of each session
# {tools_list} is replaced dynamically with the list of active tools
# {memory_context} is replaced with the relevant memory content
system_prompt: |
  You are [role description].

  You complete tasks using ONLY the available tools.
  You never simulate an action — you execute it via the provided tools.

  Available tools:
  {tools_list}

  Rules:
  - If a required tool is missing → report it, do not improvise
  - Destructive actions → always confirm before executing
  - [Rules specific to this persona]

  Context:
  {memory_context}

# Tone and response style (injected into the system prompt)
tone: "Direct and concise. No unnecessary preamble."

# Tools enabled by default for this persona
tools_enabled:
  - bash
  - file_ops

# Tools available but disabled by default (user can enable in the UI)
tools_optional:
  - git
  # v0.2: web_search, mcp_bridge

# Which part of PARA memory to load into context at startup
# projects | areas | resources | all | none
memory_context: "projects"

# Visual persona (optional)
persona:
  display_name: "My Agent"    # name shown in the UI (can differ from `name`)
  avatar: "🤖"                # emoji shown in the UI
  greeting: null              # welcome message (null = no message)
```

---

## Included personas

### `coder.yaml`

Specialized for development. Tools: bash, file_ops, git.
Recommended model: Qwen3 Coder.
Designed for: clone, read/write code, git operations, structure analysis.

**Key rule in system prompt:** use only the provided tools.
Never offer to open a browser for a git or filesystem task.

### `writer.yaml`

Specialized for writing. Tools: file_ops only by default.
Recommended model: Mistral 7B.
Designed for: writing, summarizing, rephrasing, content generation.

### `research.yaml`

Specialized for analysis and reasoning. Tools: web_search enabled by default (v0.2).
Recommended model: DeepSeek R1.
Designed for: research, synthesis, comparisons, multi-step reasoning.

### `assistant.yaml`

General-purpose agent. All tools optional.
Recommended model: Mistral 7B or equivalent.
Designed for: general use, Q&A, mixed tasks.

---

## Dynamic injection into the system prompt

At runtime, before sending the prompt to the LLM, `core/agent.py` replaces:

**`{tools_list}`** → list of active tools with their descriptions and signatures:
```
- bash(cmd: str) → str : executes a shell command and returns stdout
- file_ops.read(path: str) → str : reads a file and returns its content
- file_ops.write(path: str, content: str) → None : writes to a file
- git.clone(url: str, dest: str) → str : clones a git repository
```

**`{memory_context}`** → relevant memory content (global + agent, truncated):
```
## Active projects
- LMAgent-Plus: v0.1 in progress

## Recent tasks
- 2026-03-31: analyzed repo structure
```

---

## System prompt writing rules

These rules apply to all personas. Following them ensures reliable behavior
with common local models (Qwen, Mistral, DeepSeek, Llama).

1. **List tools explicitly** — do not assume the model knows what is available
2. **Explicitly forbid improvisation** — "if the tool does not exist, report it"
3. **Confirmation rules for destructive actions** — rm, overwrite, etc.
4. **Direct tone** — local models follow short, precise instructions better
5. **Do not overload** — a 500-token system prompt outperforms a 2000-token one
6. **Test with the fallback model** — if it works with the small model, it will work with the large one

---

## Contributing a persona

1. Copy `personas/_base.yaml`
2. Fill in all fields
3. Manually test typical use cases
4. Document in the YAML what was tested and what works well
5. Open a PR with the YAML file only — no need to touch Python code
