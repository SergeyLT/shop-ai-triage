"""Просмотр суточного бюджета токенов без вывода данных обращений."""

import argparse
import sqlite3
from datetime import UTC, date, datetime

from shop_triage.config import Settings
from shop_triage.storage import TicketRepository


def main() -> None:
    """Показать измеренный и зарезервированный расход за день UTC."""
    parser = argparse.ArgumentParser(description="Показать расход токенов за день UTC")
    parser.add_argument("--day", default=datetime.now(UTC).date().isoformat())
    arguments = parser.parse_args()
    try:
        day = date.fromisoformat(arguments.day).isoformat()
    except ValueError:
        parser.error("--day должен иметь формат ГГГГ-ММ-ДД")

    settings = Settings.from_env()
    try:
        state = TicketRepository(settings.database_path).read_daily_budget(day)
    except sqlite3.Error as exc:
        parser.exit(1, f"Не удалось прочитать бюджет: {type(exc).__name__}\n")

    print(
        f"{day} UTC: учтено {state.charged_tokens} из {settings.daily_token_budget} токенов; "
        f"измерено {state.measured_tokens}; вызовов без измеренного расхода "
        f"{state.unmeasured_calls}"
    )


if __name__ == "__main__":
    main()
