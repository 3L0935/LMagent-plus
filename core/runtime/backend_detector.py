"""
Hardware detection and backend selection for llama.cpp.

Detects GPU vendor, available acceleration backends, VRAM and RAM,
then recommends the best llama.cpp backend for the current system.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path


PRIORITY: dict[str, list[str]] = {
    "nvidia": ["cuda", "vulkan", "cpu"],
    "amd":    ["vulkan", "rocm", "cpu"],
    "apple":  ["metal", "cpu"],
    "cpu":    ["cpu"],
}

BACKEND_DESCRIPTIONS: dict[str, dict] = {
    "cuda": {
        "label": "CUDA",
        "tag": "NVIDIA only",
        "description": "The official NVIDIA engine. Fastest if you have an NVIDIA card. Does not work on AMD.",
        "warning": None,
    },
    "rocm": {
        "label": "ROCm",
        "tag": "AMD workstation",
        "description": "AMD professional equivalent of CUDA. Near-identical performance but requires ROCm installed separately. Better suited for AMD workstation cards (Instinct, Pro).",
        "warning": "ROCm not detected on this system. Consumer GPU: prefer Vulkan.",
    },
    "vulkan": {
        "label": "Vulkan",
        "tag": "Recommended Linux + AMD",
        "description": "Universal option for AMD on Linux. Works without additional installation. Slightly less optimized than ROCm but sufficient for most use cases.",
        "warning": None,
    },
    "metal": {
        "label": "Metal",
        "tag": "Apple Silicon",
        "description": "Apple's native engine. Optimal performance on Mac M1/M2/M3 and macOS integrated GPUs.",
        "warning": None,
    },
    "cpu": {
        "label": "CPU only",
        "tag": "Universal — slow",
        "description": "Works on any hardware without a GPU. A 7B model will take 2–3× longer to respond. Use as last resort or for testing.",
        "warning": "Not recommended for models larger than 7B parameters.",
    },
}


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1, "", ""


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


# ── Linux ────────────────────────────────────────────────────────────────────

def _detect_linux() -> tuple[str, dict[str, dict]]:
    vendor = "cpu"
    _, lspci_out, _ = _run(["lspci"])
    low = lspci_out.lower()
    if "nvidia" in low:
        vendor = "nvidia"
    elif "amd" in low or "advanced micro" in low or "radeon" in low:
        vendor = "amd"

    has_nvidia_smi = _tool_available("nvidia-smi")
    has_rocm_smi   = _tool_available("rocm-smi")
    has_vulkan     = _tool_available("vulkaninfo")

    # VRAM (MB)
    vram_mb = 0.0
    if vendor == "nvidia" and has_nvidia_smi:
        _, out, _ = _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
        try:
            vram_mb = float(out.strip().split("\n")[0])
        except (ValueError, IndexError):
            pass
    elif vendor == "amd":
        for vram_file in Path("/sys/class/drm").glob("*/device/mem_info_vram_total"):
            try:
                vram_mb = int(vram_file.read_text().strip()) / (1024 * 1024)
                break
            except (OSError, ValueError):
                pass

    # RAM (MB)
    ram_mb = 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_mb = int(line.split()[1]) / 1024
                break
    except OSError:
        pass

    statuses: dict[str, dict] = {
        "cuda": {
            "available": vendor == "nvidia" and has_nvidia_smi,
            "detected_via": "nvidia-smi" if has_nvidia_smi else "",
            "warning": None if has_nvidia_smi else "nvidia-smi not found",
            "vram_gb": round(vram_mb / 1024, 1),
            "ram_gb": round(ram_mb / 1024, 1),
        },
        "rocm": {
            "available": vendor == "amd" and has_rocm_smi,
            "detected_via": "rocm-smi" if has_rocm_smi else "",
            "warning": BACKEND_DESCRIPTIONS["rocm"]["warning"] if not has_rocm_smi else None,
            "vram_gb": round(vram_mb / 1024, 1),
            "ram_gb": round(ram_mb / 1024, 1),
        },
        "vulkan": {
            "available": has_vulkan,
            "detected_via": "vulkaninfo" if has_vulkan else "",
            "warning": None,
            "vram_gb": round(vram_mb / 1024, 1),
            "ram_gb": round(ram_mb / 1024, 1),
        },
        "metal": {
            "available": False,
            "detected_via": "",
            "warning": "Metal is macOS only",
            "vram_gb": 0.0,
            "ram_gb": round(ram_mb / 1024, 1),
        },
        "cpu": {
            "available": True,
            "detected_via": "/proc/meminfo",
            "warning": BACKEND_DESCRIPTIONS["cpu"]["warning"],
            "vram_gb": 0.0,
            "ram_gb": round(ram_mb / 1024, 1),
        },
    }

    priority = PRIORITY.get(vendor, PRIORITY["cpu"])
    best = next(b for b in priority if statuses[b]["available"])
    return best, statuses


# ── macOS ────────────────────────────────────────────────────────────────────

def _detect_macos() -> tuple[str, dict[str, dict]]:
    # VRAM
    vram_mb = 0.0
    _, out, _ = _run(["system_profiler", "SPDisplaysDataType"], timeout=10)
    match = re.search(r"VRAM.*?:\s*(\d+)\s*MB", out, re.IGNORECASE)
    if match:
        vram_mb = float(match.group(1))

    # RAM
    ram_mb = 0.0
    _, out, _ = _run(["sysctl", "hw.memsize"])
    match = re.search(r"(\d+)", out)
    if match:
        ram_mb = int(match.group(1)) / (1024 * 1024)

    statuses: dict[str, dict] = {
        "cuda":   {"available": False, "detected_via": "", "warning": "CUDA is Linux/Windows only", "vram_gb": 0.0, "ram_gb": round(ram_mb / 1024, 1)},
        "rocm":   {"available": False, "detected_via": "", "warning": "ROCm is Linux only",         "vram_gb": 0.0, "ram_gb": round(ram_mb / 1024, 1)},
        "vulkan": {"available": False, "detected_via": "", "warning": "Prefer Metal on macOS",      "vram_gb": 0.0, "ram_gb": round(ram_mb / 1024, 1)},
        "metal":  {"available": True,  "detected_via": "always available on macOS", "warning": None, "vram_gb": round(vram_mb / 1024, 1), "ram_gb": round(ram_mb / 1024, 1)},
        "cpu":    {"available": True,  "detected_via": "sysctl hw.memsize", "warning": BACKEND_DESCRIPTIONS["cpu"]["warning"], "vram_gb": 0.0, "ram_gb": round(ram_mb / 1024, 1)},
    }
    return "metal", statuses


# ── Windows ──────────────────────────────────────────────────────────────────

def _detect_windows() -> tuple[str, dict[str, dict]]:
    vendor = "cpu"
    try:
        import wmi  # type: ignore
        c = wmi.WMI()
        for gpu in c.Win32_VideoController():
            name = (gpu.Name or "").lower()
            if "nvidia" in name:
                vendor = "nvidia"
                break
            if "amd" in name or "radeon" in name:
                vendor = "amd"
                break
    except Exception:
        pass

    has_nvidia_smi = _tool_available("nvidia-smi") or _tool_available("nvidia-smi.exe")
    has_vulkan     = _tool_available("vulkaninfo") or _tool_available("vulkaninfo.exe")

    statuses: dict[str, dict] = {
        "cuda":   {"available": vendor == "nvidia" and has_nvidia_smi, "detected_via": "nvidia-smi", "warning": None},
        "rocm":   {"available": False, "detected_via": "", "warning": "ROCm is Linux only"},
        "vulkan": {"available": has_vulkan, "detected_via": "vulkaninfo", "warning": None},
        "metal":  {"available": False, "detected_via": "", "warning": "Metal is macOS only"},
        "cpu":    {"available": True, "detected_via": "wmi", "warning": BACKEND_DESCRIPTIONS["cpu"]["warning"]},
    }

    priority = PRIORITY.get(vendor, PRIORITY["cpu"])
    best = next(b for b in priority if statuses[b]["available"])
    return best, statuses


# ── Public API ────────────────────────────────────────────────────────────────

def detect_best_backend() -> tuple[str, dict]:
    """
    Detect available backends and recommend the best one for this hardware.

    Returns:
        (recommended_backend, all_backends_with_status)

    Each backend status: {"available": bool, "detected_via": str, "warning": str | None,
                          "vram_gb": float, "ram_gb": float}
    """
    system = platform.system()
    if system == "Linux":
        return _detect_linux()
    if system == "Darwin":
        return _detect_macos()
    if system == "Windows":
        return _detect_windows()
    # Unknown platform — CPU fallback
    return "cpu", {
        "cpu": {"available": True, "detected_via": "fallback", "warning": BACKEND_DESCRIPTIONS["cpu"]["warning"], "vram_gb": 0.0, "ram_gb": 0.0}
    }
