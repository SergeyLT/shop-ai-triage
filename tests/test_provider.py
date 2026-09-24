"""Проверка запроса к модели и отказоустойчивого разбора ответа."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shop_triage.config import Settings
from shop_triage.models import TriageRequest
from shop_triage.provider import ProviderFailure, ProxyAPIProvider


def make_provider(tmp_path: Path, mode: str = "schema") -> ProxyAPIProvider:
    settings = Settings(
        api_key="test-key",
        proxyapi_api_key="provider-test-key",
        database_path=tmp_path / "tickets.sqlite3",
        proxyapi_json_mode=mode,
    )
    return ProxyAPIProvider(settings)


def request() -> TriageRequest:
    return TriageRequest(text="Заказ не пришёл", channel="chat", client_id="private-client-id")


def test_provider_sends_only_needed_fields_and_validates_json(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "category": "support",
                                "draft_reply": "Уточните номер заказа для проверки.",
                                "confidence": "low",
                                "escalate": False,
                            }
                        )
                    ),
                )
            ],
        )

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = provider.classify(request())

    assert captured["model"] == "openai/gpt-6-luna"
    assert captured["max_completion_tokens"] == 1200
    assert "temperature" not in captured
    assert captured["response_format"]["type"] == "json_schema"
    assert "private-client-id" not in str(captured["messages"])
    assert result.response.escalate is True
    assert result.usage is not None
    assert result.usage.total_tokens == 30


@pytest.mark.parametrize("content", ["не JSON", '{"category":"support"}', "{}"])
def test_invalid_provider_response_enables_fallback(tmp_path: Path, content: str) -> None:
    provider = make_provider(tmp_path)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop", message=SimpleNamespace(content=content)
                        )
                    ],
                )
            )
        )
    )

    with pytest.raises(ProviderFailure) as error:
        provider.classify(request())
    assert error.value.error_code == "provider_invalid_response"
    assert error.value.usage is not None
    assert error.value.usage.total_tokens == 30


def test_truncated_response_preserves_usage_for_fallback(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=100, completion_tokens=1200, total_tokens=1300
                    ),
                    choices=[
                        SimpleNamespace(
                            finish_reason="length", message=SimpleNamespace(content="{}")
                        )
                    ],
                )
            )
        )
    )

    with pytest.raises(ProviderFailure) as error:
        provider.classify(request())
    assert error.value.error_code == "provider_invalid_response"
    assert error.value.usage is not None
    assert error.value.usage.total_tokens == 1300
