"""SQLite-аудит и атомарное ограничение частоты запросов."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from shop_triage.models import TriageRequest, TriageResponse
from shop_triage.usage import DailyBudgetState, TokenUsage


class TicketRepository:
    """Хранилище одного экземпляра приложения с постоянным файлом SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        """Создать таблицы и индексы при первом запуске."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    escalate INTEGER NOT NULL,
                    draft_reply TEXT NOT NULL,
                    error TEXT,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tickets)").fetchall()
            }
            for name, definition in (
                ("model", "TEXT"),
                ("prompt_tokens", "INTEGER"),
                ("completion_tokens", "INTEGER"),
                ("total_tokens", "INTEGER"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE tickets ADD COLUMN {name} {definition}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    client_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_events_created ON rate_events(created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_events_client_created "
                "ON rate_events(client_id, created_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS token_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day TEXT NOT NULL,
                    charged_tokens INTEGER NOT NULL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    state TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_events_day ON token_events(day)"
            )

    def reserve_request(
        self, client_id: str, now: float, per_client_limit: int, global_limit: int
    ) -> bool:
        """Атомарно зарезервировать место в минутном окне для платного вызова."""
        cutoff = now - 60
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM rate_events WHERE created_at < ?", (cutoff,))
            global_count = connection.execute(
                "SELECT COUNT(*) FROM rate_events WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]
            client_count = connection.execute(
                "SELECT COUNT(*) FROM rate_events WHERE client_id = ? AND created_at >= ?",
                (client_id, cutoff),
            ).fetchone()[0]
            if global_count >= global_limit or client_count >= per_client_limit:
                return False
            connection.execute(
                "INSERT INTO rate_events (created_at, client_id) VALUES (?, ?)", (now, client_id)
            )
            return True

    def reserve_token_budget(self, day: str, tokens: int, limit: int) -> int | None:
        """Атомарно зарезервировать суточный бюджет перед вызовом модели."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            charged = connection.execute(
                "SELECT COALESCE(SUM(charged_tokens), 0) FROM token_events WHERE day = ?", (day,)
            ).fetchone()[0]
            if charged + tokens > limit:
                return None
            cursor = connection.execute(
                "INSERT INTO token_events (day, charged_tokens, state) VALUES (?, ?, 'reserved')",
                (day, tokens),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def finalize_token_budget(self, reservation_id: int, usage: TokenUsage | None) -> None:
        """Заменить резерв измеренным расходом либо оставить неизвестный расход учтённым."""
        with closing(self._connect()) as connection, connection:
            if usage is None:
                connection.execute(
                    "UPDATE token_events SET state = 'unreported' WHERE id = ?", (reservation_id,)
                )
            else:
                connection.execute(
                    """
                    UPDATE token_events
                    SET charged_tokens = ?, prompt_tokens = ?, completion_tokens = ?,
                        total_tokens = ?, state = 'measured'
                    WHERE id = ?
                    """,
                    (
                        usage.total_tokens,
                        usage.prompt_tokens,
                        usage.completion_tokens,
                        usage.total_tokens,
                        reservation_id,
                    ),
                )

    def read_daily_budget(self, day: str) -> DailyBudgetState:
        """Получить измеренный расход и все зарезервированные токены за день UTC."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(charged_tokens), 0),
                       COALESCE(SUM(total_tokens), 0),
                       COALESCE(SUM(CASE WHEN state != 'measured' THEN 1 ELSE 0 END), 0)
                FROM token_events WHERE day = ?
                """,
                (day,),
            ).fetchone()
        return DailyBudgetState(*row)

    def save_ticket(
        self,
        request: TriageRequest,
        response: TriageResponse,
        error_code: str | None,
        model: str,
        usage: TokenUsage | None,
    ) -> int:
        """Сохранить принятое обращение, результат и безопасный код ошибки."""
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO tickets (
                    created_at, client_id, channel, text, category, confidence,
                    escalate, draft_reply, error, model, prompt_tokens,
                    completion_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    request.client_id,
                    request.channel,
                    request.text,
                    response.category,
                    response.confidence,
                    int(response.escalate),
                    response.draft_reply,
                    error_code,
                    model,
                    usage.prompt_tokens if usage is not None else None,
                    usage.completion_tokens if usage is not None else None,
                    usage.total_tokens if usage is not None else None,
                ),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def check_ready(self) -> None:
        """Проверить доступность базы и таблицы без блокировки записи."""
        with closing(self._connect()) as connection:
            connection.execute("SELECT id FROM tickets LIMIT 1")
