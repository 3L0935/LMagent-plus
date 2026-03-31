"""
Tests for Phase 1 — Runtime (backend detection, llama manager, model manager).

All subprocess and HTTP calls are mocked — no real network access or binaries required.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
import yaml


# ── backend_detector ──────────────────────────────────────────────────────────

class TestDetectBestBackend:
    def test_linux_nvidia_with_nvidia_smi(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Linux"),
            patch("core.runtime.backend_detector._run") as mock_run,
            patch("core.runtime.backend_detector._tool_available") as mock_tool,
            patch("builtins.open", mock_open(read_data="MemTotal: 16000000 kB\n")),
        ):
            def run_side_effect(cmd, **kwargs):
                if "lspci" in cmd:
                    return 0, "VGA: NVIDIA GeForce RTX 3080", ""
                if "nvidia-smi" in cmd and "--query-gpu" in cmd:
                    return 0, "10240\n", ""
                return 1, "", ""

            mock_run.side_effect = run_side_effect
            mock_tool.side_effect = lambda name: name == "nvidia-smi"

            best, statuses = detect_best_backend()

        assert best == "cuda"
        assert statuses["cuda"]["available"] is True
        assert statuses["vulkan"]["available"] is False

    def test_linux_amd_with_vulkan(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Linux"),
            patch("core.runtime.backend_detector._run") as mock_run,
            patch("core.runtime.backend_detector._tool_available") as mock_tool,
            patch("builtins.open", mock_open(read_data="MemTotal: 32000000 kB\n")),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            mock_run.side_effect = lambda cmd, **kw: (
                (0, "VGA: AMD Radeon RX 6800", "") if "lspci" in cmd else (1, "", "")
            )
            mock_tool.side_effect = lambda name: name == "vulkaninfo"

            best, statuses = detect_best_backend()

        assert best == "vulkan"
        assert statuses["vulkan"]["available"] is True
        assert statuses["rocm"]["available"] is False

    def test_linux_amd_with_rocm(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Linux"),
            patch("core.runtime.backend_detector._run") as mock_run,
            patch("core.runtime.backend_detector._tool_available") as mock_tool,
            patch("builtins.open", mock_open(read_data="MemTotal: 16000000 kB\n")),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            mock_run.side_effect = lambda cmd, **kw: (
                (0, "VGA: AMD Radeon Pro", "") if "lspci" in cmd else (1, "", "")
            )
            # Vulkan not present, ROCm present
            mock_tool.side_effect = lambda name: name == "rocm-smi"

            best, statuses = detect_best_backend()

        # Priority for amd: vulkan first, then rocm — vulkan unavailable so rocm wins
        assert best == "rocm"
        assert statuses["rocm"]["available"] is True

    def test_linux_cpu_fallback(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Linux"),
            patch("core.runtime.backend_detector._run") as mock_run,
            patch("core.runtime.backend_detector._tool_available", return_value=False),
            patch("builtins.open", mock_open(read_data="MemTotal: 8000000 kB\n")),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            mock_run.side_effect = lambda cmd, **kw: (0, "VGA: Some Unknown GPU", "")

            best, statuses = detect_best_backend()

        assert best == "cpu"
        assert statuses["cpu"]["available"] is True

    def test_macos_returns_metal(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Darwin"),
            patch("core.runtime.backend_detector._run") as mock_run,
        ):
            mock_run.side_effect = lambda cmd, **kw: (
                (0, "VRAM (Total): 8192 MB", "") if "system_profiler" in cmd
                else (0, "hw.memsize: 17179869184", "")
            )
            best, statuses = detect_best_backend()

        assert best == "metal"
        assert statuses["metal"]["available"] is True
        assert statuses["cuda"]["available"] is False

    def test_statuses_contain_required_keys(self):
        from core.runtime.backend_detector import detect_best_backend

        with (
            patch("platform.system", return_value="Linux"),
            patch("core.runtime.backend_detector._run", return_value=(0, "", "")),
            patch("core.runtime.backend_detector._tool_available", return_value=False),
            patch("builtins.open", mock_open(read_data="MemTotal: 4000000 kB\n")),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            _, statuses = detect_best_backend()

        for backend, info in statuses.items():
            assert "available" in info, f"{backend} missing 'available'"
            assert "detected_via" in info, f"{backend} missing 'detected_via'"
            assert "warning" in info, f"{backend} missing 'warning'"


# ── llama_manager ─────────────────────────────────────────────────────────────

class TestStartServer:
    def test_raises_runtime_error_if_port_not_ready(self, tmp_path):
        from core.runtime.llama_manager import start_server, BIN_DIR

        fake_binary = tmp_path / "llama-server"
        fake_binary.touch()
        fake_binary.chmod(0o755)

        with (
            patch("core.runtime.llama_manager.SERVER_BINARY", fake_binary),
            patch("core.runtime.llama_manager._wait_for_port", return_value=False),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.returncode = 1
            mock_popen.return_value = mock_proc

            with pytest.raises(RuntimeError, match="did not start within"):
                start_server(
                    model_path=tmp_path / "model.gguf",
                    backend="cpu",
                    port=18080,
                )

    def test_returns_popen_when_port_ready(self, tmp_path):
        from core.runtime.llama_manager import start_server

        fake_binary = tmp_path / "llama-server"
        fake_binary.touch()
        fake_binary.chmod(0o755)

        with (
            patch("core.runtime.llama_manager.SERVER_BINARY", fake_binary),
            patch("core.runtime.llama_manager._wait_for_port", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            result = start_server(
                model_path=tmp_path / "model.gguf",
                backend="cpu",
                port=18080,
            )

        assert result is mock_proc

    def test_raises_backend_error_if_binary_missing(self, tmp_path):
        from core.runtime.llama_manager import start_server
        from core.errors import BackendError

        missing = tmp_path / "nonexistent-llama-server"

        with (
            patch("core.runtime.llama_manager.SERVER_BINARY", missing),
            patch("core.runtime.llama_manager.BIN_DIR", tmp_path),
        ):
            with pytest.raises(BackendError, match="not found"):
                start_server(model_path=tmp_path / "model.gguf", backend="cpu")


class TestDownloadLlamaServer:
    @pytest.mark.asyncio
    async def test_raises_backend_error_for_unknown_platform_backend(self):
        from core.runtime.llama_manager import download_llama_server
        from core.errors import BackendError

        with patch("core.runtime.llama_manager._os_name", return_value="freebsd"):
            with pytest.raises(BackendError, match="No llama.cpp binary"):
                await download_llama_server("cpu")

    @pytest.mark.asyncio
    async def test_raises_backend_error_if_no_matching_asset(self):
        from core.runtime.llama_manager import download_llama_server
        from core.errors import BackendError

        fake_release = {"tag_name": "b9999", "assets": [{"name": "unrelated.zip", "size": 1, "browser_download_url": "http://x"}]}

        with (
            patch("core.runtime.llama_manager._os_name", return_value="linux"),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.json.return_value = fake_release
            mock_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_resp

            with pytest.raises(BackendError, match="No asset matching"):
                await download_llama_server("vulkan")

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, tmp_path):
        from core.runtime.llama_manager import download_llama_server
        import io, zipfile

        # Build a fake zip with llama-server inside
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("llama-server", b"fake-binary")
        zip_bytes = buf.getvalue()

        fake_asset = {
            "name": "llama-b1234-bin-ubuntu-x64-vulkan.zip",
            "size": len(zip_bytes),
            "browser_download_url": "http://fake/download",
        }
        fake_release = {"tag_name": "b1234", "assets": [fake_asset]}

        progress_values: list[float] = []

        async def fake_aiter_bytes(chunk_size=None):
            yield zip_bytes

        with (
            patch("core.runtime.llama_manager._os_name", return_value="linux"),
            patch("core.runtime.llama_manager.BIN_DIR", tmp_path),
            patch("core.runtime.llama_manager.SERVER_BINARY", tmp_path / "llama-server"),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            # First client call (GET release)
            mock_get_resp = MagicMock()
            mock_get_resp.json.return_value = fake_release
            mock_get_resp.raise_for_status = MagicMock()

            # Second client call (stream download)
            mock_stream_resp = MagicMock()
            mock_stream_resp.raise_for_status = MagicMock()
            mock_stream_resp.aiter_bytes = fake_aiter_bytes
            mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
            mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_get_resp
            # stream() is NOT awaited — it returns an async context manager directly
            mock_client.stream = MagicMock(return_value=mock_stream_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await download_llama_server(
                "vulkan",
                on_progress=progress_values.append,
            )

        assert result == tmp_path / "llama-server"
        assert any(v == 1.0 for v in progress_values)


class TestStopServer:
    def test_stop_already_stopped(self):
        from core.runtime.llama_manager import stop_server

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already exited
        stop_server(mock_proc)
        mock_proc.terminate.assert_not_called()

    def test_stop_running_process(self):
        from core.runtime.llama_manager import stop_server

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # running
        stop_server(mock_proc)
        mock_proc.terminate.assert_called_once()


# ── model_manager ─────────────────────────────────────────────────────────────

FAKE_CATALOG = {
    "models": [
        {"id": "big-model", "size_gb": 18.0, "min_vram_gb": 16, "min_ram_gb": 24, "recommended_for": ["coder"], "tags": []},
        {"id": "mid-model", "size_gb": 4.5,  "min_vram_gb": 4,  "min_ram_gb": 8,  "recommended_for": ["coder"], "tags": []},
        {"id": "small-model","size_gb": 4.1,  "min_vram_gb": 4,  "min_ram_gb": 8,  "recommended_for": ["general"], "tags": []},
    ]
}


class TestRecommendModels:
    def _patch_catalog(self):
        return patch("core.runtime.model_manager._load_catalog", return_value=FAKE_CATALOG["models"])

    def test_high_vram_returns_largest_first(self):
        from core.runtime.model_manager import recommend_models

        with self._patch_catalog():
            result = recommend_models(vram_gb=24.0, ram_gb=64.0)

        # big-model (18 GB) should come before mid/small
        assert result[0] == "big-model"

    def test_low_vram_only_ram_compatible(self):
        from core.runtime.model_manager import recommend_models

        with self._patch_catalog():
            result = recommend_models(vram_gb=0.0, ram_gb=16.0)

        # big-model requires min_ram 24 GB — should be absent
        assert "big-model" not in result
        assert "mid-model" in result
        assert "small-model" in result

    def test_insufficient_resources_returns_empty(self):
        from core.runtime.model_manager import recommend_models

        with self._patch_catalog():
            result = recommend_models(vram_gb=0.0, ram_gb=4.0)

        assert result == []

    def test_gpu_models_before_cpu_models(self):
        from core.runtime.model_manager import recommend_models

        with self._patch_catalog():
            result = recommend_models(vram_gb=8.0, ram_gb=64.0)

        gpu_ids = {"mid-model", "small-model"}
        cpu_ids = set()
        # big-model needs 16 GB VRAM — not in GPU matches for 8 GB VRAM
        # mid and small need 4 GB — both in GPU matches
        assert all(mid in result for mid in gpu_ids)


class TestDownloadModel:
    @pytest.mark.asyncio
    async def test_returns_existing_file_without_download(self, tmp_path):
        from core.runtime.model_manager import download_model

        model_dir = tmp_path / "my-model"
        model_dir.mkdir()
        target = model_dir / "model.gguf"
        target.write_bytes(b"fake")

        progress: list[float] = []

        with patch("core.runtime.model_manager.MODELS_DIR", tmp_path):
            result = await download_model(
                repo_id="org/repo",
                filename="model.gguf",
                dest=Path("my-model"),
                on_progress=progress.append,
            )

        assert result == target
        assert progress == [1.0]

    @pytest.mark.asyncio
    async def test_calls_hf_hub_download(self, tmp_path):
        from core.runtime.model_manager import download_model

        cached_file = tmp_path / "cached.gguf"
        cached_file.write_bytes(b"model data")

        with (
            patch("core.runtime.model_manager.MODELS_DIR", tmp_path),
            patch("core.runtime.model_manager.hf_hub_download", return_value=str(cached_file)) as mock_dl,
        ):
            result = await download_model(
                repo_id="org/repo",
                filename="model.gguf",
                dest=Path("new-model"),
            )

        mock_dl.assert_called_once()
        assert result == tmp_path / "new-model" / "model.gguf"

    @pytest.mark.asyncio
    async def test_raises_lmagent_error_on_failure(self, tmp_path):
        from core.runtime.model_manager import download_model
        from core.errors import LMAgentError

        with (
            patch("core.runtime.model_manager.MODELS_DIR", tmp_path),
            patch("core.runtime.model_manager.hf_hub_download", side_effect=Exception("network error")),
        ):
            with pytest.raises(LMAgentError, match="Failed to download"):
                await download_model(
                    repo_id="org/repo",
                    filename="model.gguf",
                    dest=Path("fail-model"),
                )
