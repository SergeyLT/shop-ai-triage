"""Проверки публичного контракта без платных вызовов модели."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from shop_triage.app import create_app
from shop_triage.config import Settings
from shop_triage.models import TriageRequest, TriageResponse
from shop_triage.provider import ProviderFailure, ProviderResult, token_reservation
from shop_triage.service import FALLBACK_REPLY
from shop_triage.storage import TicketRepository
from shop_triage.usage import TokenUsage

PAYLOAD = {
    "text": "Заказ оплачен, но статус не обновился",
    "channel": "form",
    "client_id": "demo-1",
}
HEADERS = {"X-API-Key": "local-test-key"}


class FakeProvider:
    def __init__(self, result: TriageResponse | ProviderFailure) -> None:
        self.result = result
        self.calls = 0

    def classify(self, request: TriageRequest) -> ProviderResult:
        self.calls += 1
        if isinstance(self.result, ProviderFailure):
            raise self.result
        return ProviderResult(self.result, TokenUsage(10, 20, 30))


def make_settings(
    path: Path, *, per_client: int = 5, global_limit: int = 30, daily_budget: int = 20_000
) -> Settings:
    return Settings(
        api_key="local-test-key",
        proxyapi_api_key="provider-test-key",
        database_path=path,
        rate_limit_per_client=per_client,
        rate_limit_global=global_limit,
        daily_token_budget=daily_budget,
    )


def good_result() -> TriageResponse:
    return TriageResponse(
        category="support",
        draft_reply="Уточните номер заказа, чтобы оператор мог проверить статус.",
        confidence="medium",
        escalate=False,
    )


def count_tickets(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]


def test_triage_saves_result_and_health_reports_version(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "tickets.sqlite3")
    provider = FakeProvider(good_result())

    with TestClient(create_app(settings, provider)) as client:
        health = client.get("/health")
        response = client.post("/triage", json=PAYLOAD, headers=HEADERS)

    assert health.json() == {"status": "ok", "version": "dev"}
    assert response.status_code == 200
    assert response.json() == good_result().model_dump()
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["X-Request-ID"]
    assert provider.calls == 1
    with sqlite3.connect(settings.database_path) as connection:
        row = connection.execute(
            "SELECT client_id, channel, text, category, confidence, escalate, draft_reply, error, "
            "model, prompt_tokens, completion_tokens, total_tokens "
            "FROM tickets"
        ).fetchone()
    assert row == (
        "demo-1",
        "form",
        PAYLOAD["text"],
        "support",
        "medium",
        0,
        good_result().draft_reply,
        None,
        "openai/gpt-6-luna",
        10,
        20,
        30,
    )


def test_key_and_input_validation_prevent_model_call(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "tickets.sqlite3")
    provider = FakeProvider(good_result())

    with TestClient(create_app(settings, provider)) as client:
        missing_key = client.post("/triage", json=PAYLOAD)
        empty_text = client.post("/triage", json={**PAYLOAD, "text": "   "}, headers=HEADERS)
        too_long = client.post("/triage", json={**PAYLOAD, "text": "я" * 2001}, headers=HEADERS)
        bad_channel = client.post(
            "/triage", json={**PAYLOAD, "channel": "telegram"}, headers=HEADERS
        )

    assert missing_key.status_code == 401
    assert empty_text.status_code == 422
    assert empty_text.headers["content-type"] == "application/json; charset=utf-8"
    assert PAYLOAD["text"] not in str(empty_text.json())
    assert too_long.status_code == 422
    assert bad_channel.status_code == 422
    assert provider.calls == 0
    assert count_tickets(settings.database_path) == 0


def test_provider_failure_is_audited_without_sensitive_logs(tmp_path: Path, capsys) -> None:
    settings = make_settings(tmp_path / "tickets.sqlite3")
    provider = FakeProvider(ProviderFailure("provider_timeout", "APITimeoutError"))

    with TestClient(create_app(settings, provider)) as client:
        response = client.post("/triage", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "category": "other",
        "draft_reply": FALLBACK_REPLY,
        "confidence": "low",
        "escalate": True,
    }
    with sqlite3.connect(settings.database_path) as connection:
        error = connection.execute("SELECT error FROM tickets").fetchone()[0]
    assert error == "provider_timeout"

    output = capsys.readouterr().out
    assert PAYLOAD["text"] not in output
    assert settings.api_key not in output
    assert settings.proxyapi_api_key not in output
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    failure = next(record for record in records if record["event"] == "provider_request_failed")
    assert failure["error_code"] == "provider_timeout"
    assert failure["error_type"] == "APITimeoutError"
    assert failure["request_id"]


def test_daily_token_budget_returns_audited_fallback_without_model_call(tmp_path: Path) -> None:
    required = token_reservation(TriageRequest.model_validate(PAYLOAD), 1200)
    settings = make_settings(tmp_path / "tickets.sqlite3", daily_budget=required + 10)
    provider = FakeProvider(good_result())

    with TestClient(create_app(settings, provider)) as client:
        first = client.post("/triage", json=PAYLOAD, headers=HEADERS)
        second = client.post("/triage", json=PAYLOAD, headers=HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {
        "category": "other",
        "draft_reply": FALLBACK_REPLY,
        "confidence": "low",
        "escalate": True,
    }
    assert provider.calls == 1
    with sqlite3.connect(settings.database_path) as connection:
        rows = connection.execute("SELECT error, total_tokens FROM tickets ORDER BY id").fetchall()
    assert rows == [(None, 30), ("daily_token_budget_exhausted", None)]
    state = TicketRepository(settings.database_path).read_daily_budget(
        datetime.now(UTC).date().isoformat()
    )
    assert state.charged_tokens == 30
    assert state.measured_tokens == 30
    assert state.unmeasured_calls == 0


def test_invalid_model_result_still_counts_reported_tokens(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "tickets.sqlite3")
    provider = FakeProvider(
        ProviderFailure("provider_invalid_response", "ValidationError", TokenUsage(8, 12, 20))
    )

    with TestClient(create_app(settings, provider)) as client:
        response = client.post("/triage", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["escalate"] is True
    with sqlite3.connect(settings.database_path) as connection:
        row = connection.execute(
            "SELECT error, prompt_tokens, completion_tokens, total_tokens FROM tickets"
        ).fetchone()
    assert row == ("provider_invalid_response", 8, 12, 20)


def test_per_client_and_global_limits_prevent_paid_calls(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "tickets.sqlite3", per_client=1, global_limit=2)
    provider = FakeProvider(good_result())

    with TestClient(create_app(settings, provider)) as client:
        first = client.post("/triage", json=PAYLOAD, headers=HEADERS)
        same_client = client.post("/triage", json=PAYLOAD, headers=HEADERS)
        other_client = client.post(
            "/triage", json={**PAYLOAD, "client_id": "demo-2"}, headers=HEADERS
        )
        beyond_global = client.post(
            "/triage", json={**PAYLOAD, "client_id": "demo-3"}, headers=HEADERS
        )

    assert [item.status_code for item in (first, same_client, other_client, beyond_global)] == [
        200,
        429,
        200,
        429,
    ]
    assert provider.calls == 2
    assert count_tickets(settings.database_path) == 2


def test_low_confidence_always_escalates() -> None:
    result = TriageResponse(
        category="other",
        draft_reply="Уточните, пожалуйста, детали обращения.",
        confidence="low",
        escalate=False,
    )
    assert result.escalate is True
