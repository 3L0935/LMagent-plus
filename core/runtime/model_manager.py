"""
Model catalog management and HuggingFace download for LMAgent-Plus.

Loads the recommended model list from installer/models/recommended.yaml,
filters by available hardware, and downloads models via huggingface_hub.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Callable

import yaml
from huggingface_hub import hf_hub_download

from core.errors import LMAgentError


MODELS_DIR = Path.home() / ".lmagent-plus" / "models"

# Resolved at runtime — path relative to repo root
_CATALOG_PATH = Path(__file__).parent.parent.parent / "installer" / "models" / "recommended.yaml"


def _load_catalog() -> list[dict]:
    """Load the recommended model list from YAML."""
    if not _CATALOG_PATH.exists():
        raise LMAgentError(f"Model catalog not found: {_CATALOG_PATH}")
    with _CATALOG_PATH.open() as f:
        data = yaml.safe_load(f)
    return data.get("models", [])


def recommend_models(vram_gb: float, ram_gb: float) -> list[str]:
    """
    Return model IDs compatible with the detected hardware, best first.

    A model is compatible if:
    - vram_gb >= model.min_vram_gb  (GPU path)
    - OR ram_gb >= model.min_ram_gb (CPU/unified memory path)

    Models are sorted: GPU-compatible first (larger is better), then CPU-only
    candidates sorted by size ascending (smaller = faster on CPU).

    Args:
        vram_gb: Available VRAM in GB (0 if no GPU).
        ram_gb: Available system RAM in GB.

    Returns:
        List of model IDs, best match first.
    """
    catalog = _load_catalog()

    gpu_matches: list[tuple[float, str]] = []
    cpu_matches: list[tuple[float, str]] = []

    for model in catalog:
        min_vram = model.get("min_vram_gb", 0)
        min_ram  = model.get("min_ram_gb", 0)
        size_gb  = model.get("size_gb", 0)
        model_id = model["id"]

        if vram_gb >= min_vram and min_vram > 0:
            gpu_matches.append((size_gb, model_id))
        elif ram_gb >= min_ram:
            cpu_matches.append((size_gb, model_id))

    # GPU: larger model first (better quality)
    gpu_matches.sort(key=lambda x: -x[0])
    # CPU: smaller model first (faster)
    cpu_matches.sort(key=lambda x: x[0])

    return [mid for _, mid in gpu_matches] + [mid for _, mid in cpu_matches]


async def download_model(
    repo_id: str,
    filename: str,
    dest: Path,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """
    Download a .gguf model file from HuggingFace Hub.

    Automatically resumes interrupted downloads (handled by huggingface_hub).
    Stores to: ~/.lmagent-plus/models/<dest>/model.gguf
    The `dest` argument is used as the model directory name inside MODELS_DIR.

    Args:
        repo_id: HuggingFace repository ID (e.g. "Qwen/Qwen3-Coder-8B-Instruct-GGUF").
        filename: Filename within the repo (e.g. "qwen3-coder-8b-instruct-q4_k_m.gguf").
        dest: Directory name under ~/.lmagent-plus/models/ (usually the model ID).
        on_progress: Optional callback receiving progress 0.0–1.0. Note: huggingface_hub
                     uses tqdm internally; this callback is called once with 1.0 on completion.

    Returns:
        Path to the downloaded .gguf file.

    Raises:
        LMAgentError: On download failure.
    """
    model_dir = MODELS_DIR / dest
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / "model.gguf"

    if target.exists():
        if on_progress:
            on_progress(1.0)
        return target

    try:
        # hf_hub_download resumes automatically and caches in HF_HOME.
        # We move the cached file to our target location.
        cached_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(model_dir),
            local_dir_use_symlinks=False,
        )
        cached = Path(cached_path)
        if cached != target:
            cached.rename(target)
    except Exception as exc:
        raise LMAgentError(f"Failed to download {repo_id}/{filename}: {exc}") from exc

    if on_progress:
        on_progress(1.0)

    return target


def get_model_path(model_id: str) -> Path | None:
    """Return the local path of a downloaded model, or None if not present."""
    path = MODELS_DIR / model_id / "model.gguf"
    return path if path.exists() else None


def list_downloaded_models() -> list[dict]:
    """Return info dicts for all locally available models."""
    catalog = {m["id"]: m for m in _load_catalog()}
    result = []
    if not MODELS_DIR.exists():
        return result
    for model_dir in MODELS_DIR.iterdir():
        model_file = model_dir / "model.gguf"
        if model_file.exists():
            info = catalog.get(model_dir.name, {"id": model_dir.name})
            info = dict(info)
            info["local_path"] = str(model_file)
            info["size_bytes"] = model_file.stat().st_size
            result.append(info)
    return result
