"""Проверка согласованной резервной копии SQLite с WAL."""

import sqlite3
from pathlib import Path

from shop_triage.backup import create_backup
from shop_triage.models import TriageRequest, TriageResponse
from shop_triage.storage import TicketRepository


def test_backup_contains_committed_ticket(tmp_path: Path) -> None:
    source = tmp_path / "tickets.sqlite3"
    destination = tmp_path / "backups" / "copy.sqlite3"
    repository = TicketRepository(source)
    repository.initialize()
    repository.save_ticket(
        TriageRequest(text="Где заказ?", channel="chat", client_id="demo"),
        TriageResponse(
            category="support",
            draft_reply="Уточните номер заказа.",
            confidence="medium",
            escalate=False,
        ),
        None,
        "openai/gpt-6-luna",
        None,
    )

    create_backup(source, destination)

    with sqlite3.connect(destination) as connection:
        row = connection.execute("SELECT text, category FROM tickets").fetchone()
    assert row == ("Где заказ?", "support")
