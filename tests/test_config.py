from pathlib import Path
import pytest
from core.config import Config, load_config
from core.errors import ConfigError


def test_default_config_values():
    config = Config()
    assert config.daemon.port == 7771
    assert config.routing.default == "cloud"
    assert config.memory.max_global_tokens == 2000


def test_load_creates_defaults_on_first_run(tmp_user_dir: Path):
    config_path = tmp_user_dir / "config.yaml"
    assert not config_path.exists()

    config = load_config(config_path)

    assert config_path.exists()
    assert isinstance(config, Config)


def test_load_reads_existing_config(tmp_user_dir: Path):
    import yaml
    config_path = tmp_user_dir / "config.yaml"
    config_path.write_text(yaml.dump({"daemon": {"port": 1234}}))

    config = load_config(config_path)

    assert config.daemon.port == 1234


def test_load_raises_config_error_on_invalid_yaml(tmp_user_dir: Path):
    config_path = tmp_user_dir / "config.yaml"
    config_path.write_text("daemon:\n  port: not_a_number\n")

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_config_from_file_fixture(config_from_file: Config):
    assert isinstance(config_from_file, Config)
