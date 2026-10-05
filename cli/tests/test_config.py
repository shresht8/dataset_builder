from __future__ import annotations

import pytest
from groundline_cli.config import ConfigError, load_config


def test_env_vars_take_precedence(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('api_url = "http://file.example"\ntoken = "file-token"\n')
    monkeypatch.setattr("groundline_cli.config.CONFIG_PATH", config_path)
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://env.example")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "env-token")

    config = load_config()

    assert config.api_url == "http://env.example"
    assert config.token == "env-token"


def test_falls_back_to_config_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('api_url = "http://file.example"\ntoken = "file-token"\n')
    monkeypatch.setattr("groundline_cli.config.CONFIG_PATH", config_path)
    monkeypatch.delenv("GROUNDLINE_API_URL", raising=False)
    monkeypatch.delenv("GROUNDLINE_TOKEN", raising=False)

    config = load_config()

    assert config.api_url == "http://file.example"
    assert config.token == "file-token"


def test_env_and_file_mix(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('token = "file-token"\n')
    monkeypatch.setattr("groundline_cli.config.CONFIG_PATH", config_path)
    monkeypatch.delenv("GROUNDLINE_TOKEN", raising=False)
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://env.example")

    config = load_config()

    assert config.api_url == "http://env.example"
    assert config.token == "file-token"


def test_missing_everything_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("groundline_cli.config.CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.delenv("GROUNDLINE_API_URL", raising=False)
    monkeypatch.delenv("GROUNDLINE_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="missing configuration"):
        load_config()
