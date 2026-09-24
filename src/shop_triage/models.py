"""Входной и выходной контракт обработки обращения."""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

Category = Literal["billing", "support", "complaint", "other"]
Confidence = Literal["high", "medium", "low"]
Channel = Literal["email", "form", "chat"]


class TriageRequest(BaseModel):
    """Нормализованное обращение из любого поддерживаемого канала."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    channel: Channel
    client_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]


class TriageResponse(BaseModel):
    """Проверенный черновик, который может прочитать оператор."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Category
    draft_reply: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)
    ]
    confidence: Confidence
    escalate: bool

    @field_validator("draft_reply")
    @classmethod
    def check_sentences(cls, value: str) -> str:
        """Отклонить списки и ответы длиннее шести предложений."""
        if "\n" in value:
            raise ValueError("Черновик должен быть обычным текстом")
        sentences = [item for item in re.split(r"(?<=[.!?])\s+", value) if item.strip()]
        if len(sentences) > 6:
            raise ValueError("Черновик не должен быть длиннее шести предложений")
        return value

    @model_validator(mode="after")
    def escalate_low_confidence(self) -> Self:
        """Низкая уверенность всегда требует участия человека."""
        if self.confidence == "low":
            self.escalate = True
        return self
