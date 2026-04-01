from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from core.config import load_config
from core.memory import PARAStore
from core.persona_loader import load_persona, make_system_prompt_hook
from core.tool_registry import ToolRegistry
from core.tools.bash import BASH_TOOL
from core.tools.file_ops import READ_FILE_TOOL, WRITE_FILE_TOOL, LIST_DIRECTORY_TOOL
from core.tools.git import GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL
from core.tools.call_agent import make_call_agent_tool
from core.router import AgentRouter, Router
from core.agent import Agent
from core.daemon import run_daemon

DEFAULT_PERSONA = "assistant"


def _build_agent(config) -> tuple[Agent, PARAStore]:
    """Assemble a fully-wired Agent with memory hooks for the default persona."""
    store = PARAStore(config.memory)
    store.ensure_structure(DEFAULT_PERSONA)

    # Registry with all bundled tools
    registry = ToolRegistry()
    for tool in [
        BASH_TOOL,
        READ_FILE_TOOL, WRITE_FILE_TOOL, LIST_DIRECTORY_TOOL,
        GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL,
    ]:
        registry.register(tool)

    # call_agent — Phase 2.5 multi-agent delegation
    registry.register(make_call_agent_tool(AgentRouter(), registry))

    # Memory hooks
    global_hook = store.make_global_memory_hook()
    agent_hook = store.make_agent_memory_hook(DEFAULT_PERSONA)

    # Persona hook — embeds agent memory into {memory_context}
    persona = load_persona(DEFAULT_PERSONA)
    persona_hook = make_system_prompt_hook(persona, registry, memory_fn=agent_hook)

    agent = Agent(
        router=Router(config),
        tool_registry=registry,
        system_prompt_hooks=[global_hook, persona_hook],
    )
    return agent, store


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _start_local_backend(config) -> subprocess.Popen | None:
    """Start llama-server if routing is local or auto. Returns proc or None."""
    from core.runtime.llama_manager import start_server
    from core.runtime.model_manager import get_model_path

    log = logging.getLogger(__name__)

    routing = config.routing.default
    if routing not in ("local", "auto"):
        return None

    local_cfg = config.backends.local
    model_id = local_cfg.default_model
    if not model_id:
        if routing == "local":
            log.error("backends.local.default_model is not set in config. Cannot start llama-server.")
            sys.exit(1)
        log.warning("backends.local.default_model not set — skipping local backend (auto mode).")
        return None

    model_path = get_model_path(model_id)
    if model_path is None:
        if routing == "local":
            log.error(
                "Model %r not found in ~/.lmagent-plus/models/. "
                "Download it first with: uv run python -c \"import asyncio; from core.runtime.model_manager import download_model; asyncio.run(download_model(...))\"",
                model_id,
            )
            sys.exit(1)
        log.warning("Model %r not downloaded — skipping local backend (auto mode).", model_id)
        return None

    log.info("Starting llama-server with model %r on port %d...", model_id, local_cfg.port)
    try:
        proc = start_server(
            model_path=model_path,
            backend=local_cfg.backend,
            port=local_cfg.port,
            ctx_size=local_cfg.ctx_size,
            gpu_layers=local_cfg.gpu_layers,
            threads=local_cfg.threads,
        )
        log.info("llama-server ready on port %d.", local_cfg.port)
        return proc
    except Exception as exc:
        if routing == "local":
            log.error("Failed to start llama-server: %s", exc)
            sys.exit(1)
        log.warning("Failed to start llama-server (%s) — falling back to cloud.", exc)
        return None


def main() -> None:
    config = load_config()
    _setup_logging(config.daemon.log_level)

    llama_proc = _start_local_backend(config)
    agent, store = _build_agent(config)

    try:
        asyncio.run(
            run_daemon(config, agent=agent, store=store, agent_name=DEFAULT_PERSONA)
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Daemon stopped.")
    finally:
        if llama_proc is not None:
            from core.runtime.llama_manager import stop_server
            logging.getLogger(__name__).info("Stopping llama-server...")
            stop_server(llama_proc)
    sys.exit(0)


if __name__ == "__main__":
    main()
