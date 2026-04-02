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
/model [name]      Load a model — downloads if needed, hot-reloads daemon (local routing)
/models            List available models (cloud + local catalog)
/hf [query]        Search HuggingFace for GGUF models
/setup             Setup wizard — backend, language, interests
/reload            Restart the daemon (applies config changes)
/clear             Clear chat history (current tab)
/stop              Cancel the current response  (same as ESC)
/status            Daemon connection info

Keyboard
--------
ESC        Cancel current response
Ctrl+C     Quit
Ctrl+P     Theme palette (native Textual)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
import websockets
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static, TabbedContent, TabPane

from core.config import Config

_CLI_STATE = Path.home() / ".lmagent-plus" / "cli_state.json"
_USER_DIR   = Path.home() / ".lmagent-plus"

SLASH_COMMANDS = [
    "/help", "/agent", "/persona", "/tools",
    "/model", "/models", "/hf", "/setup",
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
  [bold]/model[/bold] \\[name]     Load a model — downloads if needed, hot-reloads daemon (local)
  [bold]/models[/bold]            List available models (cloud + local catalog)
  [bold]/hf[/bold] \\[query]       Search HuggingFace for GGUF models
  [bold]/setup[/bold]             Setup wizard — backend, language, interests
  [bold]/reload[/bold]            Restart the daemon (applies config/backend changes)
  [bold]/clear[/bold]             Clear chat history (current tab)
  [bold]/stop[/bold]              Cancel current response  (same as [bold]ESC[/bold])
  [bold]/status[/bold]            Daemon connection info

[dim]Keyboard:[/dim]  ESC = cancel   Ctrl+C = quit   Ctrl+P = theme\
"""

CSS = """
TabbedContent {
    height: 1fr;
    margin: 0 1;
}

TabPane {
    padding: 0;
}

RichLog {
    height: 1fr;
    border: solid $primary-darken-2;
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

#streaming-preview {
    height: auto;
    margin: 0 1;
    padding: 0 1;
    display: none;
    color: $text;
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


async def _download_model_httpx(
    repo_id: str,
    filename: str,
    dest_dir: Path,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> Path:
    """
    Stream a GGUF file directly from HuggingFace via httpx.

    Bypasses hf_hub_download (which spawns git-lfs subprocesses and can
    trigger 'bad value(s) in fds_to_keep' on some Linux setups).

    Args:
        on_progress: called with (bytes_downloaded, total_bytes) each chunk.

    Returns:
        Path to the downloaded model.gguf file.
    """
    url    = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "model.gguf"
    tmp    = dest_dir / "model.gguf.part"

    if target.exists():
        return target

    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total     = int(resp.headers.get("content-length", 0))
            received  = 0
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, total)

    tmp.rename(target)
    return target


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
        # Download flow
        self._download_confirm_pending = False
        self._pending_download_id: str | None = None
        # Setup wizard
        self._wizard_active = False
        self._wizard_step = 0
        self._wizard_data: dict[str, Any] = {}
        self._wizard_backends: list[str] = []
        self._wizard_catalog_picks: list[dict] = []
        # Streaming state
        self._stream_buffer: str = ""
        self._stream_active: bool = False
        super().__init__()

    # ── Composition ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="agent-tabs"):
            with TabPane("@assistant", id="tab-assistant"):
                yield RichLog(markup=True, highlight=False, wrap=True, id="chat-assistant")
        yield Static("", id="completions", markup=True)
        yield Static("", id="streaming-preview", markup=True)
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

        # Wizard intercept before empty-text guard — Enter = accept default
        if self._wizard_active:
            await self._handle_wizard_input(text)
            return

        if not text:
            return

        # Download confirmation intercept
        if self._download_confirm_pending:
            self._download_confirm_pending = False
            if text.lower() in ("y", "yes") and self._pending_download_id:
                asyncio.create_task(self._download_and_reload(self._pending_download_id))
                self._pending_download_id = None
            else:
                self._write_system("[dim]Download cancelled.[/dim]")
                self._pending_download_id = None
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
            self._chat().clear()

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
                    self._ensure_agent_tab(name)
                    self._switch_to_agent_tab(name)
                    self._update_subtitle()
                    self._write_system(f"Switched to [green]@{self._persona}[/green]")

        elif cmd == "persona":
            self._show_persona_info()

        elif cmd == "tools":
            self._show_tools()

        elif cmd == "models":
            self._show_models()

        elif cmd == "hf":
            query = " ".join(args).strip()
            if not query:
                self._write_system(
                    "Usage: [bold]/hf <query>[/bold]  — search HuggingFace for GGUF models\n"
                    "  Example: /hf mistral 7b"
                )
            else:
                asyncio.create_task(self._hf_search(query))

        elif cmd == "setup":
            await self._start_setup_wizard()

        elif cmd == "model":
            await self._handle_model_cmd(args)

        elif cmd == "reload":
            asyncio.create_task(self._restart_daemon())

        elif cmd == "status":
            self._show_connection_status()

        else:
            self._write_system(
                f"[red]Unknown command:[/red] /{escape(cmd)}  — /help for help"
            )

    async def _handle_model_cmd(self, args: list[str]) -> None:
        """/model [id] — checks catalog, prompts download if not present."""
        if not args:
            current = self._model_override or "(default from config)"
            self._write_system(f"Current model override: [cyan]{current}[/cyan]")
            return

        model_id = args[0]

        # Always check the local catalog first, regardless of current routing.
        # If the model is a known local model, handle download / hot-reload.
        try:
            from core.runtime.model_manager import _load_catalog, list_downloaded_models
            catalog   = {m["id"]: m for m in _load_catalog()}
            downloaded = {m["id"] for m in list_downloaded_models()}

            if model_id in catalog and model_id not in downloaded:
                m = catalog[model_id]
                self._pending_download_id = model_id
                self._download_confirm_pending = True
                self._write_system(
                    f"Model [cyan]{escape(model_id)}[/cyan] is not downloaded.\n"
                    f"  Size: ~{m.get('size_gb', '?')} GB  —  {escape(m.get('description', ''))}\n"
                    "[yellow]Download and load now? [y/n][/yellow]"
                )
                return

            if model_id in downloaded:
                self._model_override = model_id
                self._update_subtitle()
                if self._config.routing.default in ("local", "auto"):
                    self._reload_confirm_pending = True
                    self._write_system(
                        f"Model override: [cyan]{escape(model_id)}[/cyan]\n"
                        "[yellow]Hot-reload daemon now? [y/n][/yellow]  "
                        "(or use [bold]/reload[/bold])"
                    )
                else:
                    self._write_system(
                        f"Model override set: [cyan]{escape(model_id)}[/cyan]\n"
                        "[dim]Switch routing to 'local' in config to use it.[/dim]"
                    )
                return
        except Exception:
            pass

        # Fallback — non-catalog id (cloud model name, custom path, etc.)
        self._model_override = model_id
        self._update_subtitle()
        self._reload_confirm_pending = True
        self._write_system(
            f"Model override set: [cyan]{escape(model_id)}[/cyan]\n"
            "[yellow]Reload now? [y/n][/yellow]  (or use [bold]/reload[/bold] later)"
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
                    msg   = n.get("message", "")
                    color = "yellow" if level == "warning" else "dim"
                    self._write_system(f"[{color}]{escape(msg)}[/{color}]")
        except Exception:
            pass  # daemon not running or busy — silent

    # ── HuggingFace search ────────────────────────────────────────────────────

    async def _hf_search(self, query: str) -> None:
        self._write_system(f"Searching HuggingFace: [cyan]{escape(query)}[/cyan]…")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://huggingface.co/api/models",
                    params={
                        "search":    query,
                        "filter":    "gguf",
                        "sort":      "downloads",
                        "direction": "-1",
                        "limit":     "12",
                    },
                )
                resp.raise_for_status()
                results: list[dict] = resp.json()
        except Exception as exc:
            self._write_error(f"HuggingFace search failed: {escape(str(exc))}")
            return

        if not results:
            self._write_system("No GGUF models found for that query.")
            return

        try:
            from core.runtime.model_manager import _load_catalog, list_downloaded_models
            catalog_ids    = {m["id"] for m in _load_catalog()}
            downloaded_ids = {m["id"] for m in list_downloaded_models()}
        except Exception:
            catalog_ids, downloaded_ids = set(), set()

        catalog_hits = [r for r in results if r.get("id") in catalog_ids]
        other_hits   = [r for r in results if r.get("id") not in catalog_ids]

        lines: list[str] = [
            f"[bold]HuggingFace — GGUF results for '{escape(query)}'[/bold]", ""
        ]

        if catalog_hits:
            lines.append("[dim]In local catalog (use /model <id> to load):[/dim]")
            for m in catalog_hits:
                mid  = m.get("id", "?")
                dl   = m.get("downloads", 0)
                tag  = "[green]✓ downloaded[/green]" if mid in downloaded_ids else "[dim]· not downloaded[/dim]"
                lines.append(
                    f"  [cyan]{escape(mid)}[/cyan]  {tag}  [dim]{dl:,} downloads[/dim]"
                )
            lines.append("")

        if other_hits:
            lines.append("[dim]Other results (not in catalog):[/dim]")
            for m in other_hits[:8]:
                mid = m.get("id", "?")
                dl  = m.get("downloads", 0)
                lines.append(
                    f"  [dim]·[/dim] [cyan]{escape(mid)}[/cyan]  [dim]{dl:,} downloads[/dim]"
                )

        lines.append("")
        lines.append(
            "  Catalog: [bold]/model <id>[/bold]  ·  Full list: [bold]/models[/bold]"
        )
        self._write_system("\n".join(lines))

    # ── Setup wizard ──────────────────────────────────────────────────────────
    # Steps:
    #   0  routing   (local / cloud / auto)  — warns about shell env for API keys
    #   1  backend          (vulkan / cuda / …)
    #   2  idle_unload      (seconds, 0=never — skipped for cloud routing)
    #   3  language
    #   4  interests
    #   5  models           (skipped when routing=cloud)
    #   6  confirm

    async def _start_setup_wizard(self) -> None:
        if self._streaming:
            self._write_system(
                "[yellow]Cannot run /setup while a response is in progress.[/yellow]"
            )
            return

        self._write_system(
            "[bold cyan]Setup wizard[/bold cyan]  —  "
            "Enter = accept default  ·  Ctrl+C = quit\n"
        )

        try:
            from core.runtime.backend_detector import detect_best_backend, BACKEND_DESCRIPTIONS
            best, statuses = await asyncio.get_running_loop().run_in_executor(
                None, detect_best_backend
            )
        except Exception as exc:
            self._write_error(f"Hardware detection failed: {escape(str(exc))}")
            return

        lines = ["[bold]Detected backends:[/bold]"]
        available: list[str] = []
        for name, st in statuses.items():
            if st.get("available"):
                desc = BACKEND_DESCRIPTIONS.get(name, {})
                vram = st.get("vram_gb", 0)
                ram  = st.get("ram_gb", 0)
                hw   = f"{vram} GB VRAM" if vram else f"{ram} GB RAM"
                rec  = " [yellow]← recommended[/yellow]" if name == best else ""
                lines.append(
                    f"  [cyan]{name}[/cyan]  "
                    f"[dim]{escape(desc.get('tag', ''))}[/dim]  {hw}{rec}"
                )
                available.append(name)
        self._write_system("\n".join(lines))

        # Default routing: local if hardware available, cloud otherwise
        default_routing = "local" if available else "cloud"

        self._wizard_backends      = available
        self._wizard_data          = {"_best": best, "_default_routing": default_routing}
        self._wizard_catalog_picks = []
        self._wizard_step          = 0
        self._wizard_active        = True

        self._write_system(
            "\n[bold]Step 1/5 — Routing[/bold]\n"
            "  How should the daemon route requests by default?\n"
            "  [dim]local[/dim] = local model only   "
            "[dim]cloud[/dim] = API (Anthropic/OpenAI)   "
            "[dim]auto[/dim] = local + cloud fallback\n"
            f"  [yellow]>[/yellow]  [dim]Enter = {default_routing}[/dim]"
        )

    async def _handle_wizard_input(self, text: str) -> None:  # noqa: C901
        step = self._wizard_step

        if step == 0:  # routing
            default_routing = self._wizard_data.get("_default_routing", "local")
            choice = text.strip().lower() or default_routing
            if choice not in ("local", "cloud", "auto"):
                self._write_system(
                    f"[red]Unknown routing '{escape(choice)}'.[/red]  "
                    "Choose: local, cloud, or auto"
                )
                return
            self._wizard_data["routing"] = choice
            self._wizard_step = 1
            cloud_note = (
                "\n\n  [yellow]API keys required for cloud routing.[/yellow]\n"
                "  Set [bold]ANTHROPIC_API_KEY[/bold] and/or [bold]OPENAI_API_KEY[/bold]\n"
                "  in your shell before starting the daemon  (e.g. in ~/.bashrc or ~/.profile)."
                if choice in ("cloud", "auto") else ""
            )
            best  = self._wizard_data.get("_best", "cpu")
            avail = self._wizard_backends
            self._write_system(
                f"  [dim]Routing →[/dim] [cyan]{choice}[/cyan]{cloud_note}\n\n"
                "[bold]Step 2/5 — Local backend[/bold]\n"
                f"  Available: {', '.join(avail) if avail else '[dim]none detected[/dim]'}\n"
                f"  [yellow]>[/yellow]  [dim]Enter = {best}[/dim]"
            )

        elif step == 1:  # backend
            best   = self._wizard_data.get("_best", "cpu")
            avail  = self._wizard_backends or [best]
            choice = text.strip().lower() or best
            if choice not in avail:
                self._write_system(
                    f"[red]Unknown backend '{escape(choice)}'.[/red]  "
                    f"Choose from: {', '.join(avail)}"
                )
                return
            self._wizard_data["backend"] = choice
            if self._wizard_data.get("routing") == "cloud":
                self._wizard_data["idle_unload"] = 0
                self._wizard_step = 3
                self._write_system(
                    f"  [dim]Backend →[/dim] [cyan]{choice}[/cyan]\n\n"
                    "[bold]Step 3/6 — Language[/bold]\n"
                    "  Preferred language for agent responses\n"
                    "  [yellow]>[/yellow]  [dim]Enter = English[/dim]"
                )
            else:
                self._wizard_step = 2
                self._write_system(
                    f"  [dim]Backend →[/dim] [cyan]{choice}[/cyan]\n\n"
                    "[bold]Step 3/6 — Idle unload[/bold]\n"
                    "  Seconds of inactivity before the model is unloaded from memory.\n"
                    "  Frees VRAM/RAM when the daemon is idle — 0 = never unload.\n"
                    "  [yellow]>[/yellow]  [dim]Enter = 0 (never)[/dim]"
                )

        elif step == 2:  # idle_unload
            raw = text.strip()
            try:
                idle = int(raw) if raw else 0
                if idle < 0:
                    raise ValueError
            except ValueError:
                self._write_system("[red]Enter a positive integer (seconds) or 0:[/red]")
                return
            self._wizard_data["idle_unload"] = idle
            self._wizard_step = 3
            idle_label = f"{idle}s" if idle else "never"
            self._write_system(
                f"  [dim]Idle unload →[/dim] [cyan]{idle_label}[/cyan]\n\n"
                "[bold]Step 4/6 — Language[/bold]\n"
                "  Preferred language for agent responses\n"
                "  [yellow]>[/yellow]  [dim]Enter = English[/dim]"
            )

        elif step == 3:  # language
            self._wizard_data["language"] = text.strip() or "English"
            self._wizard_step = 4
            self._write_system(
                f"  [dim]Language →[/dim] [cyan]{escape(self._wizard_data['language'])}[/cyan]\n\n"
                "[bold]Step 5/6 — Interests[/bold]\n"
                "  Comma-separated topics  (e.g. coding, writing, science)\n"
                "  [yellow]>[/yellow]  [dim]Enter = general[/dim]"
            )

        elif step == 4:  # interests
            self._wizard_data["interests"] = text.strip() or "general"
            self._wizard_step = 5
            if self._wizard_data.get("routing") == "cloud":
                # Skip model step for cloud-only users
                self._wizard_data.setdefault("models_to_download", [])
                self._wizard_step = 6
                await self._wizard_show_confirm()
            else:
                await self._wizard_show_model_step()

        elif step == 5:  # model selection
            raw = text.strip().lower()
            if raw in ("", "skip", "s"):
                self._wizard_data.setdefault("models_to_download", [])
                self._wizard_step = 6
                await self._wizard_show_confirm()
                return
            try:
                indices = [int(x) - 1 for x in raw.split()]
            except ValueError:
                self._write_system(
                    "[red]Invalid input.[/red]  "
                    "Enter numbers (e.g. [bold]1[/bold] or [bold]1 3[/bold]) "
                    "or [bold]skip[/bold]:"
                )
                return
            picks = self._wizard_catalog_picks
            selected: list[str] = []
            for i in indices:
                if 0 <= i < len(picks):
                    selected.append(picks[i]["id"])
                else:
                    self._write_system(f"[red]No model #{i + 1}.[/red]")
                    return
            self._wizard_data["models_to_download"] = selected
            if not self._wizard_data.get("default_model") and selected:
                self._wizard_data["default_model"] = selected[0]
            self._wizard_step = 6
            await self._wizard_show_confirm()

        elif step == 6:  # confirm
            if text.strip().lower() in ("n", "no"):
                self._write_system("[dim]Setup cancelled — no changes made.[/dim]")
            else:
                await self._apply_wizard()
            self._wizard_active = False
            self._wizard_step   = 0

    async def _wizard_show_model_step(self) -> None:
        """Step 6/6 — show downloaded models or suggest catalog picks."""
        try:
            from core.runtime.model_manager import _load_catalog, list_downloaded_models
            downloaded = list_downloaded_models()
            catalog    = _load_catalog()
        except Exception as exc:
            self._write_error(f"Could not read model catalog: {escape(str(exc))}")
            self._wizard_step = 6
            await self._wizard_show_confirm()
            return

        downloaded_ids = {m["id"] for m in downloaded}
        self._wizard_catalog_picks = self._pick_catalog_models(catalog, downloaded_ids)

        lines = ["\n[bold]Step 6/6 — Models[/bold]"]

        if downloaded:
            names   = "  ".join(f"[cyan]{m['id']}[/cyan]" for m in downloaded)
            default = downloaded[0]["id"]
            self._wizard_data["default_model"] = default
            self._wizard_data.setdefault("models_to_download", [])
            lines.append(f"  Already downloaded: {names}")
            lines.append(f"  [dim]Default → {default}[/dim]")
            if self._wizard_catalog_picks:
                lines.append("")
                lines.append("  Add more (optional):")
        else:
            lines.append("  No models found. Suggested picks:")

        for i, m in enumerate(self._wizard_catalog_picks, 1):
            size = f"~{m.get('size_gb', '?')} GB"
            tags = ", ".join(m.get("tags", []))
            lines.append(
                f"  [dim]{i}.[/dim] [cyan]{m['id']}[/cyan]  {size}"
                f"  [dim]{escape(m.get('description', ''))}  [{tags}][/dim]"
            )

        if self._wizard_catalog_picks:
            lines.append("")
            lines.append(
                "  Enter number(s) to download (e.g. [bold]1[/bold] or [bold]1 3[/bold]),"
                "  [bold]skip[/bold] or Enter to skip:"
            )
        else:
            lines.append("  [dim]All catalog models already downloaded.[/dim]")
            lines.append("  [yellow]>[/yellow]  [dim]Enter = continue[/dim]")

        self._write_system("\n".join(lines))

    def _pick_catalog_models(
        self, catalog: list[dict], downloaded_ids: set[str]
    ) -> list[dict]:
        """Return up to 4 representative models not yet downloaded."""
        priorities = [
            lambda m: "tiny" in m.get("tags", []) and "general" in m.get("tags", []),
            lambda m: "general" in m.get("tags", []) and "tiny" not in m.get("tags", []),
            lambda m: "reasoning" in m.get("tags", []),
            lambda m: "code" in m.get("tags", []) and "large" not in m.get("tags", []),
        ]
        picks: list[dict] = []
        seen = set(downloaded_ids)
        for pred in priorities:
            for m in catalog:
                if m["id"] not in seen and pred(m):
                    picks.append(m)
                    seen.add(m["id"])
                    break
        return picks[:4]

    async def _wizard_show_confirm(self) -> None:
        routing   = self._wizard_data.get("routing", "local")
        backend   = self._wizard_data.get("backend", "cpu")
        idle      = self._wizard_data.get("idle_unload", 0)
        language  = self._wizard_data.get("language", "English")
        interests = self._wizard_data.get("interests", "general")
        to_dl     = self._wizard_data.get("models_to_download", [])
        default   = self._wizard_data.get("default_model", "")

        lines = ["\n[bold]Confirm setup:[/bold]"]
        lines.append(f"  Routing:   [cyan]{routing}[/cyan]")
        lines.append(f"  Backend:   [cyan]{backend}[/cyan]")
        if routing in ("local", "auto"):
            idle_label = f"{idle}s" if idle else "never"
            lines.append(f"  Idle unload: [cyan]{idle_label}[/cyan]")
        lines.append(f"  Language:  [cyan]{escape(language)}[/cyan]")
        lines.append(f"  Interests: [cyan]{escape(interests)}[/cyan]")
        if to_dl:
            lines.append(f"  Download:  [cyan]{', '.join(to_dl)}[/cyan]")
        else:
            lines.append("  Models:    [dim]no download[/dim]")
        if default:
            lines.append(f"  Default:   [cyan]{default}[/cyan]")
        lines.append("")
        lines.append("  [yellow]>[/yellow]  [bold]Enter[/bold] = apply   [bold]n[/bold] = cancel")
        self._write_system("\n".join(lines))

    async def _apply_wizard(self) -> None:
        """Write config.yaml + .env + preferences.md, then trigger downloads."""
        import yaml

        routing       = self._wizard_data.get("routing", "local")
        backend       = self._wizard_data.get("backend", "cpu")
        idle_unload   = self._wizard_data.get("idle_unload", 0)
        language      = self._wizard_data.get("language", "English")
        interests     = self._wizard_data.get("interests", "general")
        to_dl         = self._wizard_data.get("models_to_download", [])
        default_model = self._wizard_data.get("default_model", "")

        # Update config.yaml
        from core.config import CONFIG_PATH
        try:
            with CONFIG_PATH.open() as f:
                raw = yaml.safe_load(f) or {}
            local = raw.setdefault("backends", {}).setdefault("local", {})
            local["backend"] = backend
            local["idle_unload_timeout"] = idle_unload
            if default_model:
                local["default_model"] = default_model
            raw.setdefault("routing", {})["default"] = routing
            with CONFIG_PATH.open("w") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
        except Exception as exc:
            self._write_error(f"Failed to update config.yaml: {escape(str(exc))}")
            return

        # Write global/preferences.md
        prefs_path = _USER_DIR / "memory" / "global" / "preferences.md"
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(
            "# User preferences\n\n"
            "## Communication\n\n"
            f"- Preferred language: {language}\n\n"
            "## Workflow\n\n"
            f"- Interests: {interests}\n",
            encoding="utf-8",
        )

        status_lines = [
            "[green]Config written.[/green]",
            f"  routing=[cyan]{routing}[/cyan]  backend=[cyan]{backend}[/cyan]",
        ]
        if default_model:
            status_lines.append(f"  default model → [cyan]{default_model}[/cyan]")
        status_lines.append(
            f"  preferences.md — language=[cyan]{escape(language)}[/cyan], "
            f"interests=[cyan]{escape(interests)}[/cyan]"
        )
        status_lines.append(
            "\nUse [bold]/reload[/bold] to restart the daemon and apply changes."
        )
        self._write_system("\n".join(status_lines))

        # Download llama-server + models only for local/auto routing
        if routing in ("local", "auto"):
            asyncio.create_task(self._wizard_download_server(backend, to_dl))

    async def _wizard_download_server(self, backend: str, models_to_dl: list[str]) -> None:
        """Download llama-server binary then kick off model downloads."""
        from core.runtime.llama_manager import SERVER_BINARY, download_llama_server

        if SERVER_BINARY.exists():
            self._write_system(
                "  [dim]llama-server already present — skipping binary download.[/dim]"
            )
        else:
            self._write_system(f"  Downloading [cyan]llama-server[/cyan] ({backend})…")
            _last_srv_t: list[float] = [0.0]

            def _srv_progress(pct: float) -> None:
                now = time.monotonic()
                if now - _last_srv_t[0] < 5.0:
                    return
                _last_srv_t[0] = now
                self._write_system(f"  [dim]llama-server {int(pct * 100)}%[/dim]")

            try:
                await download_llama_server(backend, on_progress=_srv_progress)
                self._write_system("  [green]llama-server ready.[/green]")
            except Exception as exc:
                self._write_error(f"llama-server download failed: {escape(str(exc))}")
                return

        for model_id in models_to_dl:
            asyncio.create_task(self._download_and_reload(model_id))

        if not models_to_dl:
            self._write_system("[green]llama-server ready — no models to download.[/green]")

    # ── Model download + hot-reload ───────────────────────────────────────────

    async def _download_and_reload(self, model_id: str) -> None:
        """Download a catalog model via httpx then hot-swap the daemon."""
        from core.runtime.model_manager import _load_catalog, MODELS_DIR

        catalog = {m["id"]: m for m in _load_catalog()}
        m = catalog.get(model_id)
        if m is None:
            self._write_error(f"Model {model_id!r} not found in catalog.")
            return

        size_gb = m.get("size_gb", "?")
        self._write_system(
            f"Downloading [cyan]{escape(model_id)}[/cyan] "
            f"({size_gb} GB from HuggingFace)…\n"
            "  [dim]CLI stays responsive — responses are not blocked.[/dim]"
        )

        _last_dl_t: list[float] = [0.0]

        def _on_progress(received: int, total: int) -> None:
            now = time.monotonic()
            if now - _last_dl_t[0] < 5.0:
                return
            _last_dl_t[0] = now
            mb_done  = received / 1024 / 1024
            mb_total = total / 1024 / 1024 if total > 0 else 0
            pct      = int(received * 100 / total) if total > 0 else 0
            self._write_system(
                f"  [dim]{pct}%  {mb_done:.0f} / {mb_total:.0f} MB[/dim]"
            )

        try:
            await _download_model_httpx(
                m["hf_repo"],
                m["hf_file"],
                MODELS_DIR / model_id,
                on_progress=_on_progress,
            )
        except Exception as exc:
            self._write_error(f"Download failed: {escape(str(exc))}")
            return

        self._write_system(
            f"[green]Download complete.[/green] "
            f"Loading [cyan]{escape(model_id)}[/cyan] into daemon…"
        )
        self._model_override = model_id
        self._update_subtitle()

        try:
            await self._send_model_reload(model_id)
            self._write_system(
                f"[green]Model {escape(model_id)} is now active.[/green]"
            )
        except Exception as exc:
            self._write_error(f"Daemon reload failed: {escape(str(exc))}")

    async def _restart_daemon(self) -> None:
        """Send daemon.restart IPC then wait for the daemon to come back up."""
        uri = f"ws://127.0.0.1:{self._config.daemon.port}"
        self._write_system("[yellow]Restarting daemon…[/yellow]")
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "method": "daemon.restart", "id": "restart",
                }))
                await asyncio.wait_for(ws.recv(), timeout=5)
        except Exception:
            pass  # daemon closes the connection as it restarts — that's fine

        # Poll until daemon is back (up to 10 s)
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                async with websockets.connect(uri, open_timeout=1) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "method": "poll", "id": "ping"}))
                    await asyncio.wait_for(ws.recv(), timeout=2)
                self._write_system("[green]Daemon ready.[/green]")
                return
            except Exception:
                continue

        self._write_error("Daemon did not come back up within 10 s.")

    async def _send_model_reload(self, model_id: str) -> None:
        """Send model.reload IPC to the daemon and wait for confirmation."""
        uri = f"ws://127.0.0.1:{self._config.daemon.port}"
        async with websockets.connect(uri, open_timeout=10) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "model.reload",
                "params": {"model_id": model_id},
                "id": str(uuid.uuid4()),
            }))
            # llama-server startup can take up to 60 s
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            data = json.loads(str(raw))
            if data.get("error"):
                raise RuntimeError(data["error"].get("message", "model.reload failed"))

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
        """Persist any theme change to cli_state.json."""
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
        """Apply model override — hot-reload daemon if routing=local."""
        self._reload_confirm_pending = False
        self._update_subtitle()
        if self._config.routing.default in ("local", "auto") and self._model_override:
            asyncio.create_task(self._reload_silent(self._model_override))
        self._write_system(
            f"[green]Model:[/green] [cyan]{escape(self._model_override or '')}[/cyan]"
            " — active on next message."
        )

    async def _reload_silent(self, model_id: str) -> None:
        try:
            await self._send_model_reload(model_id)
        except Exception as exc:
            self._write_error(f"Daemon reload: {escape(str(exc))}")

    # ── Cancel ─────────────────────────────────────────────────────────────────

    async def action_cancel_response(self) -> None:
        """Cancel in-progress AI response (ESC or /stop)."""
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

        Cancellation: CancelledError is raised at the next WebSocket await.
        The `async with websockets.connect()` context manager closes the
        connection cleanly on exit.
        """
        uri = f"ws://127.0.0.1:{self._config.daemon.port}"
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                params: dict = {"message": message, "agent_id": self._persona}
                if self._model_override:
                    params["model_id"] = self._model_override
                payload = json.dumps({
                    "jsonrpc": "2.0",
                    "method":  "chat",
                    "params":  params,
                    "id":      str(uuid.uuid4()),
                })
                await ws.send(payload)

                pending_text: list[str] = []
                _last_sub_agent: str | None = None  # most recent call_agent target

                async for raw in ws:
                    data: dict[str, Any] = json.loads(str(raw))

                    if data.get("method") == "chat.event":
                        evt   = data["params"]
                        etype = evt.get("type")

                        if etype == "status":
                            msg = evt.get("message", "")
                            self._write_system(f"[yellow]{escape(msg)}[/yellow]")
                            self._update_subtitle(msg)

                        elif etype == "model_ready":
                            msg = evt.get("message", "ready")
                            self._write_system(f"[green]{escape(msg)}[/green]")
                            self._update_subtitle("thinking…")

                        elif etype == "text_start":
                            self._stream_buffer = ""
                            self._stream_active = True
                            self._update_subtitle("streaming…")
                            preview = self.query_one("#streaming-preview", Static)
                            preview.display = True

                        elif etype == "text_delta":
                            self._stream_buffer += evt.get("content", "")
                            preview = self.query_one("#streaming-preview", Static)
                            preview.update(
                                f"[bold green]@{self._persona}[/bold green]: "
                                f"{escape(self._stream_buffer)}[blink]▍[/blink]"
                            )

                        elif etype == "text_end":
                            self._stream_active = False
                            preview = self.query_one("#streaming-preview", Static)
                            preview.display = False
                            if self._stream_buffer:
                                self._write_assistant(self._stream_buffer)
                                self._stream_buffer = ""
                            self._update_subtitle("idle")

                        elif etype == "text":
                            pending_text.append(evt.get("content", ""))

                        elif etype == "tool_call":
                            if self._stream_active:
                                self._stream_active = False
                                preview = self.query_one("#streaming-preview", Static)
                                preview.display = False
                                if self._stream_buffer:
                                    self._write_assistant(self._stream_buffer)
                                    self._stream_buffer = ""
                            if pending_text:
                                self._write_assistant("".join(pending_text))
                                pending_text = []
                            tool_name  = evt.get("name", "?")
                            tool_input = evt.get("input", {})
                            self._write_tool_call(tool_name, tool_input)
                            # Multi-agent: open tab for delegated agent
                            if tool_name == "call_agent":
                                sub = tool_input.get("name", "")
                                if sub:
                                    _last_sub_agent = sub
                                    self._ensure_agent_tab(sub)
                                    self._switch_to_agent_tab(sub)

                        elif etype == "tool_result":
                            tool_name = evt.get("name", "?")
                            output    = evt.get("output", {})
                            self._write_tool_result(tool_name, output)
                            # Multi-agent: write sub-agent output to its tab
                            if tool_name == "call_agent" and isinstance(output, dict):
                                sub        = output.get("agent") or _last_sub_agent or ""
                                agent_out  = output.get("output", "")
                                errors     = output.get("errors", [])
                                if sub:
                                    log = self._chat(sub)
                                    if agent_out:
                                        log.write(
                                            f"[bold green]@{sub}[/bold green]: "
                                            f"{escape(agent_out)}"
                                        )
                                    for err in errors:
                                        log.write(f"[bold red]✗[/bold red] {escape(err)}")
                                # Return focus to main agent tab
                                self._switch_to_agent_tab(self._persona)
                                _last_sub_agent = None

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
                        break

        except asyncio.CancelledError:
            raise
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

    # ── Multi-agent tab helpers ────────────────────────────────────────────────

    def _ensure_agent_tab(self, agent_name: str) -> None:
        """Create a tab for agent_name if it does not already exist."""
        tab_id = f"tab-{agent_name}"
        if self.query(f"#{tab_id}"):
            return
        tc = self.query_one("#agent-tabs", TabbedContent)
        tc.add_pane(TabPane(
            f"@{agent_name}",
            RichLog(markup=True, highlight=False, wrap=True, id=f"chat-{agent_name}"),
            id=tab_id,
        ))

    def _switch_to_agent_tab(self, agent_name: str) -> None:
        """Activate the tab for agent_name (creates it first if needed)."""
        self._ensure_agent_tab(agent_name)
        self.query_one("#agent-tabs", TabbedContent).active = f"tab-{agent_name}"

    # ── Rendering helpers ──────────────────────────────────────────────────────

    def _chat(self, agent: str | None = None) -> RichLog:
        """Return the RichLog for the given agent (defaults to current persona)."""
        target = agent or self._persona
        try:
            return self.query_one(f"#chat-{target}", RichLog)
        except Exception:
            try:
                return self.query_one(f"#chat-{self._persona}", RichLog)
            except Exception:
                return self.query_one(RichLog)

    def _write_user(self, text: str) -> None:
        self._chat().write(f"\n[bold blue]You[/bold blue]: {escape(text)}")

    def _write_assistant(self, text: str) -> None:
        self._chat().write(f"[bold green]@{self._persona}[/bold green]: {escape(text)}")

    def _write_tool_call(self, name: str, input_: dict) -> None:
        try:
            body = json.dumps(input_, ensure_ascii=False, indent=2)
        except Exception:
            body = str(input_)
        indented = body.replace("\n", "\n    ")
        self._chat().write(
            f"  [dim]▶ tool [bold]{escape(name)}[/bold][/dim]\n"
            f"  [dim]  {escape(indented)}[/dim]"
        )

    def _write_tool_result(self, name: str, output: dict) -> None:
        body  = format_tool_result(name, output)
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
        cloud       = self._config.backends.cloud
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
            active     = local_default or ""
            for m in _load_catalog():
                mid      = m["id"]
                status   = "[green]✓[/green]" if mid in downloaded else "[dim]·[/dim]"
                is_active = " [yellow]← active[/yellow]" if mid == active else ""
                tags     = ", ".join(m.get("tags", []))
                size     = f"{m.get('size_gb', '?')} GB"
                lines.append(
                    f"  {status} [cyan]{mid}[/cyan]{is_active}\n"
                    f"       {escape(m.get('description', ''))}"
                    f"  [dim]{size}  [{tags}][/dim]"
                )
        except Exception as exc:
            lines.append(f"  [red]Could not load catalog: {escape(str(exc))}[/red]")

        lines.append("")
        lines.append(
            "  [bold]/model <id>[/bold] to load  ·  "
            "[bold]/hf <query>[/bold] to search HuggingFace"
        )
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
            p        = load_persona(self._persona)
            tools_str = get_tools_list_str(p, registry)
            self._write_system(f"[bold]Tools (@{self._persona}):[/bold]\n{tools_str}")
        except Exception as exc:
            self._write_error(escape(str(exc)))

    def _show_connection_status(self) -> None:
        state = "[yellow]busy[/yellow]" if self._streaming else "[green]idle[/green]"
        self._write_system(
            f"Daemon:  ws://127.0.0.1:{self._config.daemon.port}\n"
            f"  Agent:   @{self._persona}\n"
            f"  State:   {state}"
        )
