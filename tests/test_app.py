"""Тесты вспомогательной логики CLI."""

from balance_bot.app import _is_debug_enabled


def test_debug_enabled_by_cli_verbose() -> None:
    assert _is_debug_enabled(cli_verbose=True, cli_debug=False) is True


def test_debug_enabled_by_cli_debug() -> None:
    assert _is_debug_enabled(cli_verbose=False, cli_debug=True) is True


def test_debug_enabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("BALANCE_BOT_DEBUG", "yes")
    assert _is_debug_enabled(cli_verbose=False, cli_debug=False) is True


def test_debug_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BALANCE_BOT_DEBUG", raising=False)
    assert _is_debug_enabled(cli_verbose=False, cli_debug=False) is False
