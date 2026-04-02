from __future__ import annotations

import asyncio
import logging
import sys

from core.config import load_config, load_dotenv
from core.memory import PARAStore
from core.memory.para_store import MEMORY_DIR
from core.persona_loader import load_persona, list_personas, resolve_tool_names, make_system_prompt_hook
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


def _build_agents(config, store: PARAStore, router: Router) -> dict[str, Agent]:
    """Build one Agent per available persona, each with a filtered tool registry
    and per-persona memory hooks.

    Tools are resolved from persona YAML tools_enabled only — each persona gets
    exactly the tools it declares, nothing more.  update_memory is always injected
    as a system tool (not surfaced in tools_enabled YAML) so every persona can
    persist learned patterns.
    """
    read_tool, write_tool, list_tool = make_file_ops_tools(config.security)
    base_tools = {
        "bash":          make_bash_tool(config.security),
        "read_file":     read_tool,
        "write_file":    write_tool,
        "list_directory": list_tool,
        "git_clone":     GIT_CLONE_TOOL,
        "git_status":    GIT_STATUS_TOOL,
        "git_log":       GIT_LOG_TOOL,
    }

    app_hook    = make_app_system_hook()
    global_hook = store.make_global_memory_hook()
    agents: dict[str, Agent] = {}

    for persona_name in list_personas():
        try:
            persona = load_persona(persona_name)
            store.ensure_structure(persona_name)

            enabled_names = set(resolve_tool_names(persona["tools_enabled"]))
            registry = ToolRegistry()

            for name, tool in base_tools.items():
                if name in enabled_names:
                    registry.register(tool)

            if "call_agent" in enabled_names:
                registry.register(make_call_agent_tool(AgentRouter(), registry))

            # update_memory: always available (system tool, not listed in tools_enabled)
            registry.register(make_update_memory_tool(persona_name, MEMORY_DIR))

            agent_hook   = store.make_agent_memory_hook(persona_name)
            persona_hook = make_system_prompt_hook(persona, registry, memory_fn=agent_hook)

            agents[persona_name] = Agent(
                router=router,
                tool_registry=registry,
                system_prompt_hooks=[app_hook, global_hook, persona_hook],
            )
            logging.getLogger(__name__).debug("Loaded persona: %s", persona_name)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Could not load persona '%s': %s", persona_name, exc
            )

    return agents


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

    store  = PARAStore(config.memory)
    router = Router(config, local_manager=local_manager)
    agents = _build_agents(config, store, router)

    if not agents:
        logging.getLogger(__name__).error("No personas loaded — cannot start daemon.")
        sys.exit(1)

    logging.getLogger(__name__).info(
        "Loaded %d persona(s): %s", len(agents), ", ".join(agents)
    )

    try:
        asyncio.run(
            run_daemon(config, agents=agents, store=store, local_manager=local_manager)
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
