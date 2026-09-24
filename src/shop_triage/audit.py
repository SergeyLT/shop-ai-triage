"""Просмотр последних записей аудита без вывода текста обращений."""

import argparse
import os
import sqlite3
from contextlib import closing


def main() -> None:
    """Показать краткую историю обработки из SQLite."""
    parser = argparse.ArgumentParser(description="Показать последние записи обработки обращений")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "output/tickets.sqlite3"))
    parser.add_argument("--limit", type=int, default=10)
    arguments = parser.parse_args()
    if not 1 <= arguments.limit <= 100:
        parser.error("--limit должен быть от 1 до 100")

    try:
        with closing(sqlite3.connect(arguments.database)) as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, channel, category, confidence, escalate, error,
                       model, prompt_tokens, completion_tokens, total_tokens
                FROM tickets ORDER BY id DESC LIMIT ?
                """,
                (arguments.limit,),
            ).fetchall()
    except sqlite3.Error as exc:
        parser.exit(1, f"Не удалось прочитать базу данных: {type(exc).__name__}\n")

    if not rows:
        print("Записей пока нет")
        return
    for row in rows:
        (
            ticket_id,
            created_at,
            channel,
            category,
            confidence,
            escalate,
            error,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        ) = row
        print(
            f"#{ticket_id} {created_at} {channel} {category} "
            f"confidence={confidence} escalate={bool(escalate)} error={error or '-'} "
            f"model={model or '-'} tokens={prompt_tokens if prompt_tokens is not None else '-'}"
            f"/{completion_tokens if completion_tokens is not None else '-'}"
            f"/{total_tokens if total_tokens is not None else '-'}"
        )


if __name__ == "__main__":
    main()
