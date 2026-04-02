from __future__ import annotations

import asyncio
import logging
import sys

from core.config import load_config, load_dotenv
from core.memory import PARAStore
from core.memory.para_store import MEMORY_DIR
from core.persona_loader import load_persona, make_system_prompt_hook
from core.tool_registry import ToolRegistry
from core.tools.bash import make_bash_tool
from core.tools.file_ops import make_file_ops_tools
from core.tools.git import GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL
from core.tools.call_agent import make_call_agent_tool
from core.tools.memory_ops import make_update_memory_tool
from core.app_prompt import make_app_system_hook
from core.router import AgentRouter, Router
from core.agent import Agent
from core.daemon import run_daemon

DEFAULT_PERSONA = "assistant"


def _build_agent(config, local_manager=None) -> tuple[Agent, PARAStore]:
    """Assemble a fully-wired Agent with memory hooks for the default persona."""
    store = PARAStore(config.memory)
    store.ensure_structure(DEFAULT_PERSONA)

    # Registry with all bundled tools
    registry = ToolRegistry()
    read_tool, write_tool, list_tool = make_file_ops_tools(config.security)
    for tool in [
        make_bash_tool(config.security),
        read_tool, write_tool, list_tool,
        GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL,
    ]:
        registry.register(tool)

    # call_agent — Phase 2.5 multi-agent delegation
    registry.register(make_call_agent_tool(AgentRouter(), registry))

    # update_memory — persist preferences and learned patterns across sessions
    registry.register(make_update_memory_tool(DEFAULT_PERSONA, MEMORY_DIR))

    # Memory hooks
    global_hook = store.make_global_memory_hook()
    agent_hook = store.make_agent_memory_hook(DEFAULT_PERSONA)

    # Persona hook — embeds agent memory into {memory_context}
    persona = load_persona(DEFAULT_PERSONA)
    persona_hook = make_system_prompt_hook(persona, registry, memory_fn=agent_hook)

    # App-level system prompt — injected first, before persona and memory
    app_hook = make_app_system_hook()

    agent = Agent(
        router=Router(config, local_manager=local_manager),
        tool_registry=registry,
        system_prompt_hooks=[app_hook, global_hook, persona_hook],
    )
    return agent, store


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    load_dotenv()
    config = load_config()
    _setup_logging(config.daemon.log_level)

    from core.runtime.llama_manager import LocalBackendManager
    local_manager = LocalBackendManager(config)
    logging.getLogger(__name__).info(
        "JIT local backend enabled — llama-server will start on first request."
    )

    agent, store = _build_agent(config, local_manager=local_manager)

    router = agent._router
    try:
        asyncio.run(
            run_daemon(config, agent=agent, store=store, agent_name=DEFAULT_PERSONA, local_manager=local_manager)
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Daemon stopped.")
    finally:
        if local_manager is not None:
            logging.getLogger(__name__).info("Stopping llama-server...")
            local_manager.shutdown()
        asyncio.run(router.close())
    sys.exit(0)


if __name__ == "__main__":
    main()
