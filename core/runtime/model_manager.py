"""
Model catalog management for LMAgent-Plus.

Loads the recommended model list from installer/models/recommended.yaml
and filters by available hardware.
Downloads are handled by cli/main.py:_download_model_httpx (httpx streaming).
"""

from __future__ import annotations

from pathlib import Path

import yaml

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
