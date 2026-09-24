"""Согласованная резервная копия SQLite при работающем приложении."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def create_backup(source: Path, destination: Path) -> None:
    """Скопировать базу через SQLite backup API вместе с данными WAL."""
    if not source.is_file():
        raise FileNotFoundError("Файл базы данных не найден")
    if destination.exists():
        raise FileExistsError("Файл резервной копии уже существует")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def main() -> None:
    """Создать резервную копию без чтения текста обращений в консоль."""
    parser = argparse.ArgumentParser(description="Создать резервную копию SQLite")
    parser.add_argument("output", type=Path, help="Новый файл резервной копии")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", "output/tickets.sqlite3")),
    )
    arguments = parser.parse_args()
    try:
        create_backup(arguments.database, arguments.output)
    except (FileNotFoundError, FileExistsError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Не удалось создать резервную копию: {type(exc).__name__}\n")
    print(f"Резервная копия создана: {arguments.output}")


if __name__ == "__main__":
    main()
