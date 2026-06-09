"""Тесты загрузки плагинов из каталога."""

from pathlib import Path

import pytest

from balance_bot.exceptions import ConfigError
from balance_bot.plugins.loader import (
    create_plugin,
    discover_plugins,
    ensure_plugins_for_services,
    init_plugins,
    registered_plugins,
    resolve_plugins_dir,
)


def test_resolve_plugins_dir_relative_to_config(plugins_dir: Path) -> None:
    config_path = plugins_dir.parent / "config.ci.yaml"
    resolved = resolve_plugins_dir(Path("plugins"), config_path)
    assert resolved == plugins_dir.resolve()


def test_resolve_plugins_dir_absolute(plugins_dir: Path) -> None:
    resolved = resolve_plugins_dir(plugins_dir, Path("/tmp/config.yaml"))
    assert resolved == plugins_dir.resolve()


def test_discover_plugins_includes_mock(plugins_dir: Path) -> None:
    registry = discover_plugins(plugins_dir)
    assert "mock" in registry
    assert "vdsina" in registry


def test_init_plugins_and_registered(plugins_dir: Path) -> None:
    init_plugins(plugins_dir)
    names = registered_plugins()
    assert "mock" in names
    assert names == sorted(names)


def test_ensure_plugins_for_services_ok(plugins_dir: Path, make_service) -> None:
    init_plugins(plugins_dir)
    ensure_plugins_for_services([make_service(plugin="mock")])


def test_ensure_plugins_for_services_missing_plugin(plugins_dir: Path, make_service) -> None:
    init_plugins(plugins_dir)
    with pytest.raises(ConfigError, match="нет плагинов"):
        ensure_plugins_for_services([make_service(plugin="nonexistent-plugin-xyz")])


def test_create_plugin_returns_mock_instance(plugins_dir: Path, make_service) -> None:
    init_plugins(plugins_dir)
    service = make_service(
        plugin="mock",
        plugin_config={"balance": 1.0, "currency": "EUR"},
    )
    plugin = create_plugin(service)
    assert plugin.service is service


def test_create_plugin_unknown_raises(plugins_dir: Path, make_service) -> None:
    init_plugins(plugins_dir)
    with pytest.raises(ConfigError, match="не найден"):
        create_plugin(make_service(plugin="missing"))


def test_discover_plugins_missing_directory() -> None:
    with pytest.raises(FileNotFoundError, match="Plugins directory not found"):
        discover_plugins(Path("/nonexistent/plugins/dir"))
