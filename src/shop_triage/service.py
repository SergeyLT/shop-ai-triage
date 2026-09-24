"""Правила обработки обращения и безопасный ответ при сбое модели."""

from dataclasses import dataclass

from shop_triage.models import TriageRequest, TriageResponse
from shop_triage.provider import ProviderFailure, TriageProvider
from shop_triage.usage import TokenUsage

FALLBACK_REPLY = (
    "Не удалось подготовить ответ автоматически. Обращение требует проверки оператором."
)


@dataclass(frozen=True)
class TriageOutcome:
    """Ответ и код ошибки для аудита."""

    response: TriageResponse
    error_code: str | None
    error_type: str | None
    usage: TokenUsage | None


class TriageService:
    """Выполняет один запрос к модели и превращает сбой в эскалацию."""

    def __init__(self, provider: TriageProvider) -> None:
        self._provider = provider

    def process(self, request: TriageRequest) -> TriageOutcome:
        """Вернуть проверенный результат либо безопасный шаблон."""
        try:
            result = self._provider.classify(request)
            return TriageOutcome(result.response, None, None, result.usage)
        except ProviderFailure as exc:
            return self.fallback(exc.error_code, exc.error_type, exc.usage)

    @staticmethod
    def fallback(
        error_code: str, error_type: str | None = None, usage: TokenUsage | None = None
    ) -> TriageOutcome:
        """Отметить обращение для человека, не утверждая о фактической передаче."""
        response = TriageResponse(
            category="other",
            draft_reply=FALLBACK_REPLY,
            confidence="low",
            escalate=True,
        )
        return TriageOutcome(response, error_code, error_type, usage)
