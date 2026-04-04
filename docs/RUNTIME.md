# Runtime — LMAgent-Plus

## Overview

LMAgent-Plus has no external runtime dependency (no Ollama, no LM Studio).
It downloads and manages llama.cpp internally, along with models (.gguf files).

The API exposed by `llama-server` is OpenAI-compatible — the rest of the codebase
does not know it is llama.cpp behind the scenes. It is just a local HTTP endpoint.

---

## llama.cpp Backends

### Selection matrix

| Backend | OS | Detected via | Use case |
|---------|----|-----------------------|----------|
| CUDA | Linux, Windows | `nvidia-smi` | NVIDIA GPU — maximum performance |
| ROCm | Linux | `rocm-smi` | AMD pro/workstation (Instinct, RX Pro) |
| Vulkan | Linux, Windows | `vulkaninfo` | AMD consumer GPU (RX series) on Linux |
| Metal | macOS | always available | Apple Silicon + macOS integrated GPU |
| CPU | all | always available | Guaranteed fallback |

### Priority order by vendor

```python
PRIORITY = {
    "nvidia": ["cuda", "vulkan", "cpu"],
    "amd":    ["vulkan", "rocm", "cpu"],   # Vulkan first for consumer cards
    "apple":  ["metal", "cpu"],
    "cpu":    ["cpu"],
}
```

**AMD note:** Vulkan is recommended by default for consumer AMD GPUs (RX series).
ROCm is offered as an option only if `rocm-smi` is detected — it requires a separate
installation and targets primarily workstation cards.

### UI description strings

These strings are displayed in the UI during backend selection.
Editing here changes the text everywhere (single source of truth).

```python
BACKEND_DESCRIPTIONS = {
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
```

---

## `backend_detector.py` — detection logic

```python
def detect_best_backend() -> tuple[str, dict]:
    """
    Returns (recommended_backend, all_backends_with_status)
    Status per backend: {"available": bool, "detected_via": str, "warning": str | None}
    """
```

### Detection methods by platform

**Linux:**
- GPU vendor: `lspci | grep -i vga`
- NVIDIA: `which nvidia-smi`
- ROCm: `which rocm-smi`
- Vulkan: `which vulkaninfo`
- VRAM: `/sys/class/drm/*/device/mem_info_vram_total` (AMD) or `nvidia-smi --query-gpu=memory.total`
- RAM: `/proc/meminfo`

**Windows:**
- GPU vendor: WMI `Win32_VideoController`
- NVIDIA: `nvidia-smi.exe` in PATH
- Vulkan: `vulkaninfo.exe` in PATH
- VRAM/RAM: WMI

**macOS:**
- Metal always available
- `system_profiler SPDisplaysDataType` for VRAM
- `sysctl hw.memsize` for RAM

---

## `llama_manager.py` — llama-server lifecycle

### Binary download

llama.cpp binaries are fetched from official GitHub releases.
**Do not hardcode release names** — scrape the GitHub releases API to get the latest:
```
GET https://api.github.com/repos/ggml-org/llama.cpp/releases/latest
```
Then match the asset name against the platform/backend combination.

Expected asset name patterns (subject to upstream changes):
```python
BINARY_PATTERNS = {
    ("linux",   "cuda"):   "llama-*-bin-ubuntu-x64.zip",
    ("linux",   "rocm"):   "llama-*-bin-ubuntu-x64-rocm.zip",
    ("linux",   "vulkan"): "llama-*-bin-ubuntu-x64-vulkan.zip",
    ("linux",   "cpu"):    "llama-*-bin-ubuntu-x64.zip",
    ("windows", "cuda"):   "llama-*-bin-win-cuda-cu*-x64.zip",
    ("windows", "vulkan"): "llama-*-bin-win-vulkan-x64.zip",
    ("windows", "cpu"):    "llama-*-bin-win-noavx-x64.zip",
    ("macos",   "metal"):  "llama-*-bin-macos-arm64.zip",
    ("macos",   "cpu"):    "llama-*-bin-macos-x64.zip",
}
```

Destination: `~/.lmagent-plus/bin/llama-server` (+ `llama-cli`)

### Starting llama-server

```python
def start_server(
    model_path: Path,
    backend: str,
    port: int = 8080,
    ctx_size: int = 8192,
    gpu_layers: int = -1,      # -1 = all layers on GPU
    threads: int = -1,         # -1 = auto-detected
) -> subprocess.Popen:
    """
    Launches llama-server as a subprocess.
    Waits for the port to be ready before returning.
    Raises RuntimeError if the server does not start within 30s.
    """
```

Generated command:
```bash
~/.lmagent-plus/bin/llama-server \
  --model <model_path> \
  --port <port> \
  --ctx-size <ctx_size> \
  --n-gpu-layers <gpu_layers> \
  --threads <threads>
```

### API exposed by llama-server

OpenAI-compatible — main endpoint used by core:
```
POST http://localhost:8080/v1/chat/completions
```

---

## `model_manager.py` — model management

### Catalog `installer/models/recommended.yaml`

```yaml
models:
  - id: "qwen3-1.7b-q4"
    display_name: "Qwen3 1.7B (Q4)"
    description: "Tiny generalist — fast, low resource, good for testing"
    hf_repo: "unsloth/Qwen3-1.7B-GGUF"
    hf_file: "Qwen3-1.7B-Q4_K_M.gguf"
    size_gb: 1.1
    min_vram_gb: 2
    min_ram_gb: 4
    recommended_for: ["assistant"]
    tags: ["general", "tiny"]

  - id: "qwen3-coder-30b-q4"
    display_name: "Qwen3 Coder 30B A3B (Q4)"
    description: "Best for code — MoE, activates 3B params, requires ~18 GB VRAM or RAM"
    hf_repo: "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF"
    hf_file: "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
    size_gb: 18.2
    min_vram_gb: 16
    min_ram_gb: 24
    recommended_for: ["coder"]
    tags: ["code", "large"]

  - id: "qwen3-coder-8b-q4"
    display_name: "MD-Coder Qwen3 8B (Q4)"
    description: "Good for code — runs on most machines"
    hf_repo: "mradermacher/MD-Coder-Qwen3-8B-GGUF"
    hf_file: "MD-Coder-Qwen3-8B.Q4_K_M.gguf"
    size_gb: 4.5
    min_vram_gb: 4
    min_ram_gb: 8
    recommended_for: ["coder"]
    tags: ["code", "small"]

  - id: "mistral-7b-q4"
    display_name: "Mistral 7B Claude-instruct (Q4)"
    description: "Good generalist — lightweight and fast"
    hf_repo: "mradermacher/Mistral-7B-claude-instruct-GGUF"
    hf_file: "Mistral-7B-claude-instruct.Q4_K_M.gguf"
    size_gb: 4.1
    min_vram_gb: 4
    min_ram_gb: 8
    recommended_for: ["writer", "assistant"]
    tags: ["general", "small"]

  - id: "deepseek-r1-8b-q4"
    display_name: "DeepSeek R1 Distill Llama 8B (Q4)"
    description: "Reasoning — good for analytical tasks"
    hf_repo: "unsloth/DeepSeek-R1-Distill-Llama-8B-GGUF"
    hf_file: "DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf"
    size_gb: 4.9
    min_vram_gb: 4
    min_ram_gb: 8
    recommended_for: ["research"]
    tags: ["reasoning", "small"]
```

### Automatic recommendation based on hardware

```python
def recommend_models(vram_gb: float, ram_gb: float) -> list[str]:
    """
    Returns model IDs compatible with the detected hardware,
    sorted by relevance (best first).
    """
```

### Download

Via `huggingface_hub`:
```python
from huggingface_hub import hf_hub_download

def download_model(
    repo_id: str,
    filename: str,
    dest: Path,
    on_progress: Callable[[float], None],  # 0.0 → 1.0
) -> Path:
    """
    Downloads the .gguf file from HuggingFace.
    Automatically resumes if interrupted.
    Returns the path to the downloaded file.
    """
```

Destination: `~/.lmagent-plus/models/<model-id>/model.gguf`
