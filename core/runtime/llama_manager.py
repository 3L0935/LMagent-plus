"""
llama.cpp binary download and llama-server lifecycle management.

Downloads the appropriate llama-server binary from official GitHub releases,
starts it as a subprocess, and waits for it to become ready.
"""

from __future__ import annotations

import asyncio
import fnmatch
import platform
import socket
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from core.errors import BackendError


LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# Asset name glob patterns — subject to upstream changes.
# Key: (os_name, backend)  — os_name matches platform.system().lower()
BINARY_PATTERNS: dict[tuple[str, str], str] = {
    ("linux",   "cuda"):   "llama-*-bin-ubuntu-x64.zip",
    ("linux",   "rocm"):   "llama-*-bin-ubuntu-x64-rocm.zip",
    ("linux",   "vulkan"): "llama-*-bin-ubuntu-x64-vulkan.zip",
    ("linux",   "cpu"):    "llama-*-bin-ubuntu-x64.zip",
    ("windows", "cuda"):   "llama-*-bin-win-cuda-cu*-x64.zip",
    ("windows", "vulkan"): "llama-*-bin-win-vulkan-x64.zip",
    ("windows", "cpu"):    "llama-*-bin-win-noavx-x64.zip",
    ("darwin",  "metal"):  "llama-*-bin-macos-arm64.zip",
    ("darwin",  "cpu"):    "llama-*-bin-macos-x64.zip",
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
        raise BackendError(f"No llama.cpp binary available for os={os_name} backend={backend}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(
            LLAMA_RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        release = resp.json()

    asset = _match_asset(release.get("assets", []), pattern)
    if asset is None:
        available = [a["name"] for a in release.get("assets", [])]
        raise BackendError(
            f"No asset matching '{pattern}' in release {release.get('tag_name', '?')}. "
            f"Available: {available}"
        )

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BIN_DIR / asset["name"]

    # Download with progress reporting
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

    # Extract llama-server (and llama-cli if present)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            filename = Path(member).name
            if filename in ("llama-server", "llama-server.exe", "llama-cli", "llama-cli.exe"):
                dest = BIN_DIR / filename
                dest.write_bytes(zf.read(member))
                dest.chmod(0o755)

    zip_path.unlink(missing_ok=True)

    binary = SERVER_BINARY
    if not binary.exists():
        # Windows
        binary = BIN_DIR / "llama-server.exe"
    if not binary.exists():
        raise BackendError(f"llama-server binary not found after extraction in {BIN_DIR}")

    return binary


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until the TCP port accepts connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def start_server(
    model_path: Path,
    backend: str,
    port: int = 8080,
    ctx_size: int = 8192,
    gpu_layers: int = -1,
    threads: int = -1,
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

    Returns:
        The running subprocess.Popen handle.

    Raises:
        RuntimeError: If llama-server does not become ready within 30 seconds.
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
        "--model", str(model_path),
        "--port", str(port),
        "--ctx-size", str(ctx_size),
        "--n-gpu-layers", str(gpu_layers),
        "--threads", str(threads),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if not _wait_for_port("127.0.0.1", port, timeout=30.0):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise RuntimeError(
            f"llama-server did not start within 30 seconds on port {port}. "
            f"Exit code: {proc.returncode}"
        )

    return proc


def stop_server(proc: subprocess.Popen) -> None:
    """Gracefully stop a running llama-server subprocess."""
    if proc.poll() is not None:
        return  # already stopped
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
