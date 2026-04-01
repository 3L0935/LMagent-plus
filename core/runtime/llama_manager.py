"""
llama.cpp binary download and llama-server lifecycle management.

Downloads the appropriate llama-server binary from official GitHub releases,
starts it as a subprocess, and waits for it to become ready.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import platform
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from typing import Callable, Optional
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import httpx

from core.errors import BackendError

LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# Asset name glob patterns — subject to upstream changes.
# Key: (os_name, backend)  — os_name matches platform.system().lower()
BINARY_PATTERNS: dict[tuple[str, str], str] = {
    # ── Linux — patterns valid as of llama.cpp b8611 ──
    # cpu/cuda: llama-*-bin-ubuntu-x64.tar.gz
    ("linux", "cuda"):   "llama-*-bin-ubuntu-x64.*",
    # rocm:    llama-*-bin-ubuntu-rocm-7.2-x64.tar.gz
    ("linux", "rocm"):   "llama-*-bin-ubuntu-rocm-*-x64.*",
    # vulkan:  llama-*-bin-ubuntu-vulkan-x64.tar.gz
    ("linux", "vulkan"): "llama-*-bin-ubuntu-vulkan-x64.*",
    ("linux", "cpu"):    "llama-*-bin-ubuntu-x64.*",
    # ── Windows ──
    ("windows", "cuda"):   "llama-*-bin-win-cuda-*-x64.*",
    ("windows", "vulkan"): "llama-*-bin-win-vulkan-x64.*",
    # win-cpu-x64 replaced win-noavx-x64
    ("windows", "cpu"):    "llama-*-bin-win-cpu-x64.*",
    # ── macOS ──
    ("darwin", "metal"): "llama-*-bin-macos-arm64.*",
    ("darwin", "cpu"):   "llama-*-bin-macos-x64.*",
}

BIN_DIR = Path.home() / ".lmagent-plus" / "bin"
SERVER_BINARY = BIN_DIR / "llama-server"


def _os_name() -> str:
    return platform.system().lower()


def _match_asset(assets: list[dict], pattern: str) -> dict | None:
    """Return first asset whose name matches the glob pattern."""
    for asset in assets:
        if fnmatch.fnmatch(asset["name"], pattern):
            return asset
    return None


async def download_llama_server(
    backend: str,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """
    Download the llama-server binary appropriate for this platform and backend.

    Fetches the latest release from the GitHub API — never hardcodes release names.
    Extracts to ~/.lmagent-plus/bin/llama-server.

    Args:
        backend: One of cuda | rocm | vulkan | metal | cpu
        on_progress: Optional callback receiving progress as 0.0–1.0

    Returns:
        Path to the llama-server binary.

    Raises:
        BackendError: If no matching release asset is found or download fails.
    """
    os_name = _os_name()
    pattern = BINARY_PATTERNS.get((os_name, backend))
    if pattern is None:
        raise BackendError(
            f"No llama.cpp binary available for os={os_name} backend={backend}"
        )

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(
                LLAMA_RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            release = resp.json()
    except httpx.HTTPStatusError as e:
        raise BackendError(f"Failed to fetch llama.cpp releases: {e}") from e
    except httpx.TimeoutException as e:
        raise BackendError(f"Timeout fetching releases: {e}") from e
    except Exception as e:
        raise BackendError(f"Error fetching releases: {e}") from e

    asset = _match_asset(release.get("assets", []), pattern)
    if asset is None:
        # Fallback: look for any asset containing "llama-server" in its name
        for a in release.get("assets", []):
            if "llama-server" in a["name"].lower():
                asset = a
                break

    if asset is None:
        available = [a["name"] for a in release.get("assets", [])]
        raise BackendError(
            f"No asset matching '{pattern}' in release {release.get('tag_name', '?')}. "
            f"Available: {available}"
        )

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BIN_DIR / asset["name"]

    # Download with progress reporting
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
            total = asset.get("size", 0)
            downloaded = 0
            async with client.stream("GET", asset["browser_download_url"]) as stream:
                stream.raise_for_status()
                with zip_path.open("wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress and total:
                            on_progress(min(downloaded / total, 1.0))
    except httpx.HTTPStatusError as e:
        raise BackendError(f"Failed to download asset '{asset['name']}': {e}") from e
    except httpx.TimeoutException as e:
        raise BackendError(f"Timeout downloading asset '{asset['name']}': {e}") from e
    except Exception as e:
        raise BackendError(f"Error downloading asset '{asset['name']}': {e}") from e

    # Extract binaries and shared libraries required at runtime.
    # Binaries: llama-server, llama-cli (and .exe variants for Windows)
    # Shared libs: all .so* / .dylib files — llama-server links them at load time
    _BIN_TARGETS  = {"llama-server", "llama-server.exe", "llama-cli", "llama-cli.exe"}

    def _should_extract(filename: str) -> bool:
        if filename in _BIN_TARGETS:
            return True
        # Match libXXX.so, libXXX.so.N, libXXX.so.N.N.N, libXXX.dylib
        stem = Path(filename).stem
        return filename.endswith(".dylib") or ".so" in filename

    if zip_path.suffix == ".zip":
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                filename = Path(member).name
                if _should_extract(filename):
                    dest = BIN_DIR / filename
                    dest.write_bytes(zf.read(member))
                    dest.chmod(0o755)
    elif zip_path.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(zip_path) as tf:
            for member in tf.getmembers():
                filename = Path(member.name).name
                if _should_extract(filename):
                    fobj = tf.extractfile(member)
                    if fobj is not None:
                        dest = BIN_DIR / filename
                        dest.write_bytes(fobj.read())
                        dest.chmod(0o755)

    zip_path.unlink(missing_ok=True)

    binary_candidates = [SERVER_BINARY, BIN_DIR / "llama-server.exe"]
    binary = next((c for c in binary_candidates if c.exists()), None)
    if binary is None:
        raise BackendError(
            f"llama-server binary not found after extraction in {BIN_DIR}. "
            f"Available files: {list(BIN_DIR.glob('*'))}"
        )

    return binary


def _wait_for_health(host: str, port: int, timeout: float = 90.0) -> bool:
    """Poll llama-server /health until it returns 200 or timeout expires."""
    import urllib.request
    import urllib.error

    url = f"http://{host}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def start_server(
    model_path: Path,
    backend: str,
    port: int = 8080,
    ctx_size: int = 8192,
    gpu_layers: int = -1,
    threads: int = -1,
    startup_timeout: float = 60.0,
    vulkan_device: int = -1,
) -> subprocess.Popen:
    """
    Launch llama-server as a subprocess and wait for it to be ready.

    Args:
        model_path: Path to the .gguf model file.
        backend: Backend hint (currently informational; binary already compiled for backend).
        port: Port for the OpenAI-compatible HTTP API.
        ctx_size: Context window size.
        gpu_layers: Number of layers to offload to GPU. -1 = all.
        threads: Number of CPU threads. -1 = auto.
        startup_timeout: Seconds to wait for llama-server to become ready.

    Returns:
        The running subprocess.Popen handle.

    Raises:
        RuntimeError: If llama-server does not become ready within startup_timeout seconds.
        BackendError: If the binary is not found.
    """
    binary = SERVER_BINARY
    if not binary.exists():
        binary = BIN_DIR / "llama-server.exe"
    if not binary.exists():
        raise BackendError(
            f"llama-server not found at {SERVER_BINARY}. "
            "Run download_llama_server() first."
        )

    cmd = [
        str(binary),
        "--model",
        str(model_path),
        "--port",
        str(port),
        "--ctx-size",
        str(ctx_size),
        "--n-gpu-layers",
        str(gpu_layers),
        "--threads",
        str(threads),
    ]

    # Ensure llama-server can find the .so files extracted alongside it.
    env = os.environ.copy()
    existing = env.get("LD_LIBRARY_PATH", "")
    bin_dir_str = str(BIN_DIR)
    if bin_dir_str not in existing.split(":"):
        env["LD_LIBRARY_PATH"] = f"{bin_dir_str}:{existing}" if existing else bin_dir_str

    # Select the correct Vulkan device when multiple GPUs are present.
    # Without this, llama.cpp defaults to device 0 which may be the integrated GPU.
    if backend == "vulkan" and vulkan_device >= 0:
        env["GGML_VULKAN_DEVICE"] = str(vulkan_device)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group → clean SIGKILL of all children
        env=env,
    )

    if not _wait_for_health("127.0.0.1", port, timeout=startup_timeout):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise RuntimeError(
            f"llama-server did not become ready within {startup_timeout}s on port {port}. "
            f"Exit code: {proc.returncode}"
        )

    return proc


def stop_server(proc: subprocess.Popen) -> None:
    """Kill the llama-server process group to ensure GPU memory is released."""
    if proc.poll() is not None:
        return  # already stopped
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=10)
    except (ProcessLookupError, PermissionError):
        pass  # process already gone
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class LocalBackendManager:
    """
    JIT llama-server lifecycle manager.

    The daemon starts without loading any model. On the first local-backend
    request, ``ensure_loaded()`` starts llama-server. On model switch, it
    stops the old process and starts a new one (hot-swap).

    Thread-safe via ``asyncio.Lock`` — concurrent callers wait for the
    in-progress start to complete before returning.
    """

    def __init__(
        self,
        config: "Config",
        on_unload: Optional[Callable[[str], None]] = None,
    ) -> None:  # type: ignore[name-defined]
        self._config = config
        self._on_unload = on_unload  # called with model_name when idle-unload fires
        self._proc: subprocess.Popen | None = None
        self._loaded_model: Path | None = None
        self._lock = asyncio.Lock()
        self._last_used: float = 0.0
        self._idle_task: Optional[asyncio.Task] = None

    async def ensure_loaded(self, model_path: Path) -> None:
        """
        Ensure llama-server is running with *model_path*.

        - Auto-downloads the binary if missing.
        - No-op if already running with the same model.
        - Hot-swaps if a different model is loaded.
        - Starts fresh if not running.

        Raises:
            BackendError: Binary not found/download failed or server failed to start.
            RuntimeError: Server did not become ready in time.
        """
        if not SERVER_BINARY.exists():
            log.info("llama-server binary not found — downloading for backend=%s…",
                     self._config.backends.local.backend)
            await download_llama_server(self._config.backends.local.backend)

        async with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                if self._loaded_model == model_path:
                    self._last_used = time.monotonic()
                    return
                # Different model — stop current server before restarting.
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._stop_sync)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._start_sync, model_path)
            self._last_used = time.monotonic()
            self._ensure_idle_watcher()

    async def ensure_loaded_from_config(self) -> None:
        """
        Load the default model from config, raising ``BackendError`` if unavailable.

        Convenience wrapper used by ``Router`` so it doesn't need to know
        about model paths.
        """
        from core.runtime.model_manager import get_model_path
        from core.errors import BackendError

        local_cfg = self._config.backends.local
        model_id = local_cfg.default_model
        if not model_id:
            raise BackendError(
                "backends.local.default_model is not set in config. "
                "Cannot perform JIT model load."
            )
        model_path = get_model_path(model_id)
        if model_path is None:
            raise BackendError(
                f"Model {model_id!r} not found in ~/.lmagent-plus/models/. "
                "Download it first."
            )
        await self.ensure_loaded(model_path)

    async def unload(self) -> None:
        """Stop llama-server if running. Safe to call when already stopped."""
        self._cancel_idle_watcher()
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stop_sync)

    def shutdown(self) -> None:
        """
        Synchronous shutdown — call from ``finally`` blocks after the event
        loop has stopped (e.g. at daemon exit).
        """
        self._stop_sync()

    # ------------------------------------------------------------------
    # Idle unload watcher
    # ------------------------------------------------------------------

    def _ensure_idle_watcher(self) -> None:
        """Start the idle watcher task if timeout is configured and not already running."""
        timeout = self._config.backends.local.idle_unload_timeout
        if timeout <= 0:
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.get_event_loop().create_task(self._idle_watcher(timeout))

    def _cancel_idle_watcher(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            # Don't cancel the task if it's the one currently calling us
            # (e.g. _idle_watcher → unload → _cancel_idle_watcher).
            # Cancelling from within would raise CancelledError at the next
            # await in unload(), preventing _stop_sync from ever running.
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if self._idle_task is not current:
                self._idle_task.cancel()
        self._idle_task = None

    async def _idle_watcher(self, timeout: int) -> None:
        log = logging.getLogger(__name__)
        check_interval = max(10, timeout // 4)
        try:
            while True:
                await asyncio.sleep(check_interval)
                if not self.is_loaded:
                    return
                idle_seconds = time.monotonic() - self._last_used
                if idle_seconds >= timeout:
                    model_name = self._loaded_model.name if self._loaded_model else "model"
                    log.info(
                        "JIT: model idle for %.0fs (limit %ds) — unloading.",
                        idle_seconds, timeout,
                    )
                    await self.unload()
                    if self._on_unload:
                        self._on_unload(model_name)
                    return
        except asyncio.CancelledError:
            pass

    @property
    def is_loaded(self) -> bool:
        """True if llama-server is currently running."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def loaded_model(self) -> Path | None:
        """Path of the currently loaded model, or None."""
        return self._loaded_model

    # ------------------------------------------------------------------
    # Private sync helpers (run inside executor to avoid blocking the loop)
    # ------------------------------------------------------------------

    def _start_sync(self, model_path: Path) -> None:
        local_cfg = self._config.backends.local
        log = logging.getLogger(__name__)
        log.info("JIT: starting llama-server with model %r...", model_path.name)
        self._proc = start_server(
            model_path=model_path,
            backend=local_cfg.backend,
            port=local_cfg.port,
            ctx_size=local_cfg.ctx_size,
            gpu_layers=local_cfg.gpu_layers,
            threads=local_cfg.threads,
            vulkan_device=local_cfg.vulkan_device,
        )
        self._loaded_model = model_path
        log.info("JIT: llama-server ready on port %d.", local_cfg.port)

    def _stop_sync(self) -> None:
        if self._proc is not None:
            log = logging.getLogger(__name__)
            log.info("JIT: stopping llama-server...")
            stop_server(self._proc)
            self._proc = None
            self._loaded_model = None


if TYPE_CHECKING:
    from core.config import Config
