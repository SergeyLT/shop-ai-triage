"""Учёт токенов, возвращённых моделью."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    """Фактический расход одного вызова по данным провайдера."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class DailyBudgetState:
    """Суточный расход с учётом незавершённых и неизвестных вызовов."""

    charged_tokens: int
    measured_tokens: int
    unmeasured_calls: int
