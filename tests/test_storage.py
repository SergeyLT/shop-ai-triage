"""Проверка атомарности лимита при одновременных запросах."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shop_triage.storage import TicketRepository
from shop_triage.usage import TokenUsage


def test_concurrent_requests_cannot_exceed_global_limit(tmp_path: Path) -> None:
    repository = TicketRepository(tmp_path / "tickets.sqlite3")
    repository.initialize()

    def reserve(index: int) -> bool:
        return repository.reserve_request(f"client-{index}", 1000.0, 5, 3)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))

    assert sum(results) == 3


def test_concurrent_token_reservations_and_unknown_usage(tmp_path: Path) -> None:
    repository = TicketRepository(tmp_path / "tickets.sqlite3")
    repository.initialize()

    def reserve(_index: int) -> int | None:
        return repository.reserve_token_budget("2026-09-24", 30, 100)

    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(reserve, range(8)))

    accepted = [reservation for reservation in reservations if reservation is not None]
    assert len(accepted) == 3
    repository.finalize_token_budget(accepted[0], TokenUsage(5, 10, 15))
    repository.finalize_token_budget(accepted[1], None)
    state = repository.read_daily_budget("2026-09-24")
    assert state.charged_tokens == 75
    assert state.measured_tokens == 15
    assert state.unmeasured_calls == 2
    assert repository.reserve_token_budget("2026-09-24", 30, 100) is None
    assert repository.reserve_token_budget("2026-09-25", 30, 100) is not None


def test_existing_ticket_table_is_migrated_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "tickets.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                client_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                text TEXT NOT NULL,
                category TEXT NOT NULL,
                confidence TEXT NOT NULL,
                escalate INTEGER NOT NULL,
                draft_reply TEXT NOT NULL,
                error TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tickets (
                created_at, client_id, channel, text, category, confidence,
                escalate, draft_reply, error
            ) VALUES ('2026-09-24', 'client', 'form', 'Вопрос', 'other', 'low', 1,
                      'Ответ', NULL)
            """
        )

    repository = TicketRepository(path)
    repository.initialize()
    repository.initialize()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT text, model, prompt_tokens, completion_tokens, total_tokens FROM tickets"
        ).fetchone()
    assert row == ("Вопрос", None, None, None, None)
