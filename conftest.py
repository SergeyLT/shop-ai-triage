"""Общая настройка тестов для чистого checkout проекта."""

from pytest import Config


def pytest_configure(config: Config) -> None:
    """Создать родительскую папку для настроенного pytest --basetemp."""
    (config.rootpath / ".tmp").mkdir(exist_ok=True)
