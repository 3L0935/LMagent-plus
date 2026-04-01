"""
LMAgent-Plus CLI — Textual TUI.

Connects to the daemon via WebSocket and streams events in real time.
The daemon must be started separately:  python -m core

Slash commands
--------------
/help              This help message
/agent [name]      Switch active agent
/persona           Show active persona info
/tools             List enabled tools for the active agent
/model [name]      Override model — prompts for immediate reload or use /reload later
/models            List available models (cloud + local catalog)
/reload            Apply current model override to next message
/clear             Clear chat history
/stop              Cancel the current response  (same as ESC)
/status            Daemon connection info

Keyboard
--------
ESC        Cancel current response
Ctrl+C     Quit
p          Toggle dark/light theme
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import websockets
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from core.config import Config

_CLI_STATE = Path.home() / ".lmagent-plus" / "cli_state.json"

SLASH_COMMANDS = [
    "/help", "/agent", "/persona", "/tools", "/model", "/models",
    "/reload", "/clear", "/stop", "/status",
]


def _active_model(config: Config) -> str:
    if config.routing.default == "local":
        return config.backends.local.default_model or "local"
    return config.backends.cloud.anthropic.default_model


VALID_AGENTS = {"assistant", "coder", "writer", "research"}

HELP_TEXT = """\
[bold cyan]LMAgent-Plus[/bold cyan] — slash commands

  [bold]/help[/bold]              This help message
  [bold]/agent[/bold] \\[name]     Switch active agent (assistant | coder | writer | research)
  [bold]/persona[/bold]           Show active persona info
  [bold]/tools[/bold]             List enabled tools for the active agent
  [bold]/model[/bold] \\[name]     Override model — prompts to reload now or use /reload later
  [bold]/models[/bold]           List available models (cloud + local catalog)
  [bold]/reload[/bold]            Apply current model override to next message
  [bold]/clear[/bold]             Clear chat history
  [bold]/stop[/bold]              Cancel current response  (same as [bold]ESC[/bold])
  [bold]/status[/bold]            Daemon connection info

[dim]Keyboard:[/dim]  ESC = cancel   Ctrl+C = quit   p = theme\
"""

CSS = """
RichLog {
    height: 1fr;
    border: solid $primary-darken-2;
    margin: 0 1;
    padding: 0 1;
    scrollbar-gutter: stable;
}

#completions {
    height: auto;
    max-height: 6;
    margin: 0 1;
    padding: 0 1;
    display: none;
    background: $surface-darken-1;
    color: $text-muted;
}

Input {
    margin: 0 1 1 1;
}
"""


# ---------------------------------------------------------------------------
# Pure helpers (testable without Textual)
# ---------------------------------------------------------------------------

def parse_slash_command(text: str) -> tuple[str, list[str]]:
    """Parse '/cmd arg1 arg2' → ('cmd', ['arg1', 'arg2']).

    Returns ('', []) if text does not start with '/'.
    """
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return ("", [])
    return (parts[0][1:].lower(), parts[1:])


def format_tool_result(name: str, output: dict) -> str:
    """Format a tool result for display (compact, max ~400 chars)."""
    if "stdout" in output and output["stdout"]:
        preview = output["stdout"][:400]
        if len(output["stdout"]) > 400:
            preview += "…"
        return f"stdout: {preview}"
    if "stderr" in output and output["stderr"] and not output.get("stdout"):
        preview = output["stderr"][:400]
        return f"stderr: {preview}"
    if "error" in output:
        return f"error: {output['error']}"
    try:
        raw = json.dumps(output, ensure_ascii=False)
        return raw[:400] + ("…" if len(raw) > 400 else "")
    except Exception:
        return str(output)[:400]


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------

class LMAgentTUI(App[None]):
    """LMAgent-Plus terminal interface."""

    TITLE = "LMAgent-Plus"
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel_response", "Cancel", show=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(self, config: Config) -> None:
        self._config = config
        self._persona = "assistant"
        self._model_override: str | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._streaming = False
        self._reload_confirm_pending = False
        super().__init__()

    # ── Composition ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(markup=True, highlight=False, wrap=True, id="chat")
        yield Static("", id="completions", markup=True)
        yield Input(placeholder="Type a message or /help…", id="input")
        yield Footer()

    def on_mount(self) -> None:
        state = self._load_ui_state()
        if "theme" in state:
            self.theme = state["theme"]
        elif "dark" in state:  # backwards compat with old cli_state.json
            self.theme = "textual-dark" if state["dark"] else "textual-light"
        self._update_subtitle()
        self._write_system(
            f"Daemon: [cyan]ws://127.0.0.1:{self._config.daemon.port}[/cyan]  "
            f"Agent: [green]@{self._persona}[/green]  "
            "Type [bold]/help[/bold] for commands"
        )
        self.set_interval(5, self._poll_notifications)

    # ── Input handling ─────────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#completions", Static).display = False
        text = event.value.strip()
        event.input.clear()
        if not text:
            return

        # y/n intercept for model reload confirmation
        if self._reload_confirm_pending:
            self._reload_confirm_pending = False
            if text.lower() in ("y", "yes"):
                self._do_reload()
            else:
                self._write_system(
                    "[dim]Reload cancelled — model change applies on next message. "
                    "Use [bold]/reload[/bold] anytime.[/dim]"
                )
            return

        if text.startswith("/"):
            await self._handle_slash(text)
        elif self._streaming:
            self._write_system("[yellow]Response in progress — ESC to cancel.[/yellow]")
        else:
            await self._start_chat(text)

    async def _handle_slash(self, raw: str) -> None:
        cmd, args = parse_slash_command(raw)

        if cmd == "help":
            self._write_system(HELP_TEXT)

        elif cmd == "stop":
            await self.action_cancel_response()

        elif cmd == "clear":
            self.query_one("#chat", RichLog).clear()

        elif cmd == "agent":
            if not args:
                self._write_system(
                    f"Active agent: [green]@{self._persona}[/green]\n"
                    f"  Valid: {', '.join(sorted(VALID_AGENTS))}"
                )
            else:
                name = args[0].lower()
                if name not in VALID_AGENTS:
                    self._write_system(
                        f"[red]Unknown agent '{escape(name)}'.[/red]  "
                        f"Valid: {', '.join(sorted(VALID_AGENTS))}"
                    )
                else:
                    self._persona = name
                    self._update_subtitle()
                    self._write_system(f"Switched to [green]@{self._persona}[/green]")

        elif cmd == "persona":
            self._show_persona_info()

        elif cmd == "tools":
            self._show_tools()

        elif cmd == "models":
            self._show_models()

        elif cmd == "model":
            if not args:
                current = self._model_override or "(default from config)"
                self._write_system(f"Current model override: [cyan]{current}[/cyan]")
            else:
                self._model_override = args[0]
                self._update_subtitle()
                self._reload_confirm_pending = True
                self._write_system(
                    f"Model override set: [cyan]{escape(self._model_override)}[/cyan]\n"
                    "[yellow]Reload now? [y/n][/yellow]  (or use [bold]/reload[/bold] later)"
                )

        elif cmd == "reload":
            if self._model_override:
                self._do_reload()
            else:
                self._write_system("[dim]No model override set — nothing to reload.[/dim]")

        elif cmd == "status":
            self._show_connection_status()

        else:
            self._write_system(
                f"[red]Unknown command:[/red] /{escape(cmd)}  — /help for help"
            )

    # ── Background notification polling ───────────────────────────────────────

    async def _poll_notifications(self) -> None:
        """Check the daemon for pending system notifications (e.g. idle unload)."""
        uri = f"ws://127.0.0.1:{self._config.daemon.port}"
        try:
            async with websockets.connect(uri, open_timeout=2) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "poll", "id": "poll"}))
                raw = await ws.recv()
                data = json.loads(str(raw))
                for n in data.get("result", {}).get("notifications", []):
                    level = n.get("level", "info")
                    msg = n.get("message", "")
                    color = "yellow" if level == "warning" else "dim"
                    self._write_system(f"[{color}]{escape(msg)}[/{color}]")
        except Exception:
            pass  # daemon not running or busy — silent

    # ── Kill switch ────────────────────────────────────────────────────────────

    # ── Theme persistence ──────────────────────────────────────────────────────

    def _load_ui_state(self) -> dict:
        try:
            if _CLI_STATE.exists():
                return json.loads(_CLI_STATE.read_text())
        except Exception:
            pass
        return {}

    def _save_ui_state(self, state: dict) -> None:
        try:
            _CLI_STATE.parent.mkdir(parents=True, exist_ok=True)
            _CLI_STATE.write_text(json.dumps(state))
        except Exception:
            pass

    def watch_theme(self, theme: str) -> None:
        """Persist any theme change (command palette, API…) to cli_state.json."""
        state = self._load_ui_state()
        state["theme"] = theme
        self._save_ui_state(state)

    # ── Slash command autocomplete ─────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        completions = self.query_one("#completions", Static)
        text = event.value
        if text.startswith("/"):
            matches = [c for c in SLASH_COMMANDS if c.startswith(text.lower())]
            if matches:
                parts = "  ".join(f"[bold cyan]{m}[/bold cyan]" for m in matches)
                completions.update(parts)
                completions.display = True
                return
        completions.display = False

    def _do_reload(self) -> None:
        """Confirm model override is active — takes effect on the next message."""
        self._reload_confirm_pending = False
        self._update_subtitle()
        self._write_system(
            f"[green]Model reloaded:[/green] [cyan]{escape(self._model_override or '')}[/cyan]"
            " — active on next message."
        )

    # ── Kill switch ────────────────────────────────────────────────────────────

    async def action_cancel_response(self) -> None:
        """Cancel the in-progress AI response (ESC or /stop).

        Cancels the asyncio task, which raises CancelledError inside _run_chat
        at the next WebSocket await. The websockets context manager then sends
        a close frame — the daemon receives ConnectionClosedOK and stops cleanly.
        """
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            self._write_system("[yellow]Cancelled.[/yellow]")
        self._streaming = False
        self._update_subtitle()

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def _start_chat(self, message: str) -> None:
        self._write_user(message)
        self._streaming = True
        self._update_subtitle("thinking…")
        self._ws_task = asyncio.create_task(self._run_chat(message))

    async def _run_chat(self, message: str) -> None:
        """Stream a chat request from the daemon.

        Cancellation: asyncio.CancelledError is raised at the next WebSocket
        await. The `async with websockets.connect()` context manager closes
        the connection cleanly on exit (including on exception).
        """
        uri = f"ws://127.0.0.1:{self._config.daemon.port}"
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                params: dict = {"message": message, "agent_id": self._persona}
                if self._model_override:
                    params["model_id"] = self._model_override
                payload = json.dumps({
                    "jsonrpc": "2.0",
                    "method": "chat",
                    "params": params,
                    "id": str(uuid.uuid4()),
                })
                await ws.send(payload)

                pending_text: list[str] = []

                async for raw in ws:
                    data: dict[str, Any] = json.loads(str(raw))

                    if data.get("method") == "chat.event":
                        evt = data["params"]
                        etype = evt.get("type")

                        if etype == "status":
                            msg = evt.get("message", "")
                            self._write_system(f"[yellow]{escape(msg)}[/yellow]")
                            self._update_subtitle(msg)

                        elif etype == "model_ready":
                            msg = evt.get("message", "ready")
                            self._write_system(f"[green]{escape(msg)}[/green]")
                            self._update_subtitle("thinking…")

                        elif etype == "text":
                            pending_text.append(evt.get("content", ""))

                        elif etype == "tool_call":
                            if pending_text:
                                self._write_assistant("".join(pending_text))
                                pending_text = []
                            self._write_tool_call(
                                evt.get("name", "?"), evt.get("input", {})
                            )

                        elif etype == "tool_result":
                            self._write_tool_result(
                                evt.get("name", "?"), evt.get("output", {})
                            )

                        elif etype == "error":
                            self._write_error(evt.get("message", "unknown error"))

                        elif etype == "done":
                            if pending_text:
                                self._write_assistant("".join(pending_text))
                            break

                    elif "error" in data and data["error"]:
                        self._write_error(data["error"].get("message", "RPC error"))
                        break

                    elif "result" in data:
                        # Final RPCResponse — nothing to display
                        break

        except asyncio.CancelledError:
            raise  # let asyncio handle it; finally block still runs
        except (ConnectionRefusedError, OSError):
            self._write_error(
                f"Cannot connect to daemon (port {self._config.daemon.port}).\n"
                "  Start with:  [bold]python -m core[/bold]"
            )
        except Exception as exc:
            self._write_error(f"{escape(str(exc))}")
        finally:
            self._streaming = False
            self._update_subtitle()

    # ── Rendering helpers ──────────────────────────────────────────────────────

    def _chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def _write_user(self, text: str) -> None:
        self._chat().write(f"\n[bold blue]You[/bold blue]: {escape(text)}")

    def _write_assistant(self, text: str) -> None:
        self._chat().write(f"[bold green]@{self._persona}[/bold green]: {escape(text)}")

    def _write_tool_call(self, name: str, input_: dict) -> None:
        try:
            body = json.dumps(input_, ensure_ascii=False, indent=2)
        except Exception:
            body = str(input_)
        # indent continuation lines
        indented = body.replace("\n", "\n    ")
        self._chat().write(
            f"  [dim]▶ tool [bold]{escape(name)}[/bold][/dim]\n"
            f"  [dim]  {escape(indented)}[/dim]"
        )

    def _write_tool_result(self, name: str, output: dict) -> None:
        body = format_tool_result(name, output)
        color = "red" if "error" in body and not body.startswith("stdout") else "dim"
        self._chat().write(
            f"  [{color}]◀ result [bold]{escape(name)}[/bold]: {escape(body)}[/{color}]"
        )

    def _write_error(self, msg: str) -> None:
        self._chat().write(f"[bold red]✗[/bold red] {msg}")

    def _write_system(self, msg: str) -> None:
        self._chat().write(f"[dim]{msg}[/dim]")

    def _update_subtitle(self, status: str = "idle") -> None:
        model = self._model_override or _active_model(self._config)
        self.sub_title = f"@{self._persona} | {model} | {status}"

    # ── Info commands ──────────────────────────────────────────────────────────

    def _show_models(self) -> None:
        cloud = self._config.backends.cloud
        local_default = self._config.backends.local.default_model
        lines: list[str] = ["[bold]Available models:[/bold]", ""]

        lines.append("[dim]Cloud:[/dim]")
        lines.append(f"  [cyan]{cloud.anthropic.default_model}[/cyan]  (Anthropic)")
        lines.append(f"  [cyan]{cloud.openai.default_model}[/cyan]  (OpenAI)")

        lines.append("")
        lines.append("[dim]Local catalog:[/dim]")
        try:
            from core.runtime.model_manager import _load_catalog, list_downloaded_models
            downloaded = {m["id"] for m in list_downloaded_models()}
            active = local_default or ""
            for m in _load_catalog():
                mid = m["id"]
                if mid in downloaded:
                    status = "[green]✓[/green]"
                else:
                    status = "[dim]·[/dim]"
                is_active = " [yellow]← active[/yellow]" if mid == active else ""
                tags = ", ".join(m.get("tags", []))
                size = f"{m.get('size_gb', '?')} GB"
                lines.append(
                    f"  {status} [cyan]{mid}[/cyan]{is_active}\n"
                    f"       {escape(m.get('description', ''))}"
                    f"  [dim]{size}  [{tags}][/dim]"
                )
        except Exception as exc:
            lines.append(f"  [red]Could not load catalog: {escape(str(exc))}[/red]")

        lines.append("")
        lines.append("  Use [bold]/model <id>[/bold] to override for this session.")
        self._write_system("\n".join(lines))

    def _show_persona_info(self) -> None:
        try:
            from core.persona_loader import load_persona
            p = load_persona(self._persona)
            self._write_system(
                f"[bold]@{self._persona}[/bold]: {escape(p.get('description', '—'))}\n"
                f"  model: {escape(str(p.get('default_model', '—')))}\n"
                f"  memory: {escape(str(p.get('memory_context', '—')))}"
            )
        except Exception as exc:
            self._write_error(escape(str(exc)))

    def _show_tools(self) -> None:
        try:
            from core.persona_loader import load_persona, get_tools_list_str
            from core.tool_registry import ToolRegistry
            from core.tools.bash import BASH_TOOL
            from core.tools.file_ops import READ_FILE_TOOL, WRITE_FILE_TOOL, LIST_DIRECTORY_TOOL
            from core.tools.git import GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL
            registry = ToolRegistry()
            for t in [BASH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL,
                      LIST_DIRECTORY_TOOL, GIT_CLONE_TOOL, GIT_STATUS_TOOL, GIT_LOG_TOOL]:
                registry.register(t)
            p = load_persona(self._persona)
            tools_str = get_tools_list_str(p, registry)
            self._write_system(
                f"[bold]Tools (@{self._persona}):[/bold]\n{tools_str}"
            )
        except Exception as exc:
            self._write_error(escape(str(exc)))

    def _show_connection_status(self) -> None:
        state = "[yellow]busy[/yellow]" if self._streaming else "[green]idle[/green]"
        self._write_system(
            f"Daemon:  ws://127.0.0.1:{self._config.daemon.port}\n"
            f"  Agent:   @{self._persona}\n"
            f"  State:   {state}"
        )
