"""Тесты семантической валидации конфигурации."""

import pytest

from balance_bot.models import ServiceConfig
from balance_bot.validation import ConfigError, validate_config


def test_valid_config_passes(make_app_config) -> None:
    validate_config(make_app_config())


def test_empty_bot_token_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="bot_token"):
        validate_config(make_app_config(bot_token=""))


def test_placeholder_bot_token_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="шаблон"):
        validate_config(make_app_config(bot_token="YOUR_BOT_TOKEN"))


def test_invalid_bot_token_format_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="формат"):
        validate_config(make_app_config(bot_token="not-a-valid-token"))


def test_empty_allowed_user_ids_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="allowed_user_ids"):
        validate_config(make_app_config(allowed_user_ids=[]))


def test_non_positive_user_id_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="недопустимый id"):
        validate_config(make_app_config(allowed_user_ids=[0]))


def test_empty_plugins_dir_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="plugins_dir"):
        validate_config(make_app_config(plugins_dir="   "))


def test_invalid_timezone_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="timezone"):
        validate_config(make_app_config(timezone="Mars/Phobos"))


def test_no_services_raises(make_app_config) -> None:
    with pytest.raises(ConfigError, match="services"):
        validate_config(make_app_config(services=[]))


def test_duplicate_service_names_raises(make_app_config, make_service) -> None:
    svc = make_service(name="dup")
    with pytest.raises(ConfigError, match='дубликат "dup"'):
        validate_config(make_app_config(services=[svc, make_service(name="dup")]))


def test_empty_service_name_raises(make_app_config) -> None:
    bad = ServiceConfig(
        name="",
        plugin="mock",
        poll_interval_seconds=60,
    )
    with pytest.raises(ConfigError, match=".name: обязателен"):
        validate_config(make_app_config(services=[bad]))


def test_empty_plugin_raises(make_app_config) -> None:
    bad = ServiceConfig(
        name="x",
        plugin="",
        poll_interval_seconds=60,
    )
    with pytest.raises(ConfigError, match=".plugin: обязателен"):
        validate_config(make_app_config(services=[bad]))


def test_non_positive_poll_interval_raises(make_app_config) -> None:
    bad = ServiceConfig(
        name="x",
        plugin="mock",
        poll_interval_seconds=0,
    )
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        validate_config(make_app_config(services=[bad]))
