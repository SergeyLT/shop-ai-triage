"""Проверенная конфигурация приложения из переменных окружения."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} должен быть положительным целым числом")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} должен быть положительным числом")
    return value


@dataclass(frozen=True)
class Settings:
    """Настройки одного экземпляра сервиса."""

    api_key: str
    proxyapi_api_key: str
    database_path: Path
    proxyapi_base_url: str = "https://api.proxyapi.ru/v1"
    proxyapi_model: str = "openai/gpt-6-luna"
    proxyapi_timeout_seconds: float = 20.0
    proxyapi_max_completion_tokens: int = 1200
    proxyapi_temperature: float | None = None
    proxyapi_json_mode: Literal["schema", "object", "prompt"] = "schema"
    rate_limit_per_client: int = 5
    rate_limit_global: int = 30
    daily_token_budget: int = 20_000
    environment: str = "local"
    version: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        """Прочитать окружение и остановить запуск без обязательных секретов."""
        api_key = os.getenv("TRIAGE_API_KEY", "").strip()
        provider_key = os.getenv("PROXYAPI_API_KEY", "").strip()
        if not api_key or api_key.startswith("replace-with-"):
            raise ValueError("Задайте TRIAGE_API_KEY в окружении")
        if not provider_key or provider_key.startswith("replace-with-"):
            raise ValueError("Задайте PROXYAPI_API_KEY в окружении")

        temperature_raw = os.getenv("PROXYAPI_TEMPERATURE", "none").strip().lower()
        temperature = None if temperature_raw in {"", "none"} else float(temperature_raw)
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("PROXYAPI_TEMPERATURE должен быть от 0 до 2")
        json_mode = os.getenv("PROXYAPI_JSON_MODE", "schema").strip().lower()
        if json_mode not in {"schema", "object", "prompt"}:
            raise ValueError("PROXYAPI_JSON_MODE должен быть schema, object или prompt")

        return cls(
            api_key=api_key,
            proxyapi_api_key=provider_key,
            database_path=Path(os.getenv("DATABASE_PATH", "output/tickets.sqlite3")),
            proxyapi_base_url=os.getenv("PROXYAPI_BASE_URL", "https://api.proxyapi.ru/v1"),
            proxyapi_model=os.getenv("PROXYAPI_MODEL", "openai/gpt-6-luna"),
            proxyapi_timeout_seconds=_positive_float("PROXYAPI_TIMEOUT_SECONDS", 20.0),
            proxyapi_max_completion_tokens=_positive_int("PROXYAPI_MAX_COMPLETION_TOKENS", 1200),
            proxyapi_temperature=temperature,
            proxyapi_json_mode=cast("Literal['schema', 'object', 'prompt']", json_mode),
            rate_limit_per_client=_positive_int("RATE_LIMIT_PER_CLIENT", 5),
            rate_limit_global=_positive_int("RATE_LIMIT_GLOBAL", 30),
            daily_token_budget=_positive_int("DAILY_TOKEN_BUDGET", 20_000),
            environment=os.getenv("APP_ENVIRONMENT", "local"),
            version=os.getenv("APP_VERSION", "dev"),
        )
