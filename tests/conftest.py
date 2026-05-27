"""Общие фикстуры для unit-тестов."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from balance_bot.models import AppConfig, ServiceConfig, ServiceStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = PROJECT_ROOT / "plugins"


@pytest.fixture
def plugins_dir() -> Path:
    """Каталог плагинов репозитория."""
    return PLUGINS_DIR


@pytest.fixture
def make_service() -> Callable[..., ServiceConfig]:
    """Фабрика ``ServiceConfig`` с разумными значениями по умолчанию."""

    def _factory(**overrides) -> ServiceConfig:
        defaults = {
            "name": "test-service",
            "plugin": "mock",
            "poll_interval_seconds": 60,
            "balance_threshold": None,
            "subscription_warn_days": None,
            "plugin_config": {},
        }
        defaults.update(overrides)
        return ServiceConfig(**defaults)

    return _factory


@pytest.fixture
def make_app_config(make_service) -> Callable[..., AppConfig]:
    """Фабрика ``AppConfig`` для тестов валидации."""

    def _factory(**overrides) -> AppConfig:
        defaults = {
            "bot_token": "123456789:AAHFakeTokenForTestsOnly",
            "allowed_user_ids": [1],
            "services": [make_service()],
            "plugins_dir": "plugins",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    return _factory


@pytest.fixture
def utc_now() -> datetime:
    """Фиксированное «сейчас» для тестов алертов."""
    return datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
