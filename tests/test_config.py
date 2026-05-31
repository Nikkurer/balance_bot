"""Тесты загрузки YAML-конфигурации."""

from pathlib import Path

import pytest

from balance_bot.config import ConfigError, load_config
from balance_bot.models import AppConfig


def test_load_config_ci_file() -> None:
    config = load_config(Path("config.ci.yaml"))
    assert isinstance(config, AppConfig)
    assert config.bot_token.startswith("123456789:")
    assert config.allowed_user_ids == [1]
    assert len(config.services) == 1
    assert config.services[0].plugin == "mock"
    assert config.timezone == "UTC"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Не удалось прочитать"):
        load_config(tmp_path / "missing.yaml")


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("telegram:\n  bot_token: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="разбора YAML"):
        load_config(path)


def test_load_config_minimal_valid(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
telegram:
  bot_token: "999888777:ZZZ_TestToken_abc"
  allowed_user_ids: [42]
plugins_dir: plugins
timezone: Europe/Moscow
services:
  - name: demo
    plugin: mock
    poll_interval_seconds: 120
    plugin_config:
      balance: 10
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.allowed_user_ids == [42]
    assert config.services[0].name == "demo"
    assert config.services[0].poll_interval_seconds == 120
    assert config.timezone == "Europe/Moscow"


def test_load_config_history_section(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
telegram:
  bot_token: "999888777:ZZZ_TestToken_abc"
  allowed_user_ids: [42]
history:
  enabled: true
  path: data/test.db
  retention_days: 0
  max_size_mb: 10
services:
  - name: demo
    plugin: mock
    poll_interval_seconds: 120
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.history.enabled is True
    assert config.history.path == "data/test.db"
    assert config.history.retention_days == 0
    assert config.history.max_size_mb == 10


def test_load_config_missing_telegram_section(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("services: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="telegram"):
        load_config(path)
