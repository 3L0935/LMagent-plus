import pytest
from pathlib import Path
from core.config import Config, load_config


@pytest.fixture
def tmp_user_dir(tmp_path: Path) -> Path:
    """Temporary ~/.lmagent-plus/ equivalent."""
    user_dir = tmp_path / ".lmagent-plus"
    user_dir.mkdir()
    return user_dir


@pytest.fixture
def default_config() -> Config:
    """Default Config instance without touching the filesystem."""
    return Config()


@pytest.fixture
def config_from_file(tmp_user_dir: Path) -> Config:
    """Config loaded from a temp file (triggers default creation)."""
    config_path = tmp_user_dir / "config.yaml"
    return load_config(config_path)
