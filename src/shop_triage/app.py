"""HTTP-интерфейс сервиса обработки обращений."""

import logging
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shop_triage.config import Settings
from shop_triage.logging_config import (
    configure_logging,
    log_event,
    reset_request_id,
    set_request_id,
)
from shop_triage.models import TriageRequest, TriageResponse
from shop_triage.provider import ProxyAPIProvider, TriageProvider, token_reservation
from shop_triage.service import TriageService
from shop_triage.storage import TicketRepository


class UTF8JSONResponse(JSONResponse):
    """Указывать кодировку JSON для клиентов Windows PowerShell."""

    media_type = "application/json; charset=utf-8"


def create_app(settings: Settings | None = None, provider: TriageProvider | None = None) -> FastAPI:
    """Создать приложение с подменяемым провайдером для автономных тестов."""
    config = settings or Settings.from_env()
    logger = configure_logging(config)
    repository = TicketRepository(config.database_path)
    provider_instance: TriageProvider = (
        provider if provider is not None else ProxyAPIProvider(config)
    )
    owned_provider = provider_instance if provider is None else None
    triage_service = TriageService(provider_instance)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        repository.initialize()
        try:
            yield
        finally:
            if isinstance(owned_provider, ProxyAPIProvider):
                owned_provider.close()

    app = FastAPI(
        title="Shop Triage API",
        version=config.version,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> UTF8JSONResponse:
        return UTF8JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> UTF8JSONResponse:
        fields = [
            {"field": ".".join(map(str, error["loc"])), "code": error["type"]}
            for error in exc.errors()
        ]
        return UTF8JSONResponse(
            status_code=422,
            content={"detail": "Некорректные данные запроса", "errors": fields},
        )

    @app.middleware("http")
    async def request_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid4().hex
        token = set_request_id(request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            log_event(
                logger,
                logging.INFO if status < 500 else logging.ERROR,
                "request_completed",
                "HTTP-запрос завершён",
                route=getattr(route, "path", "unknown"),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            reset_request_id(token)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Проверить готовность приложения и файла SQLite."""
        try:
            repository.check_ready()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "health_check_failed",
                "Хранилище недоступно",
                component="database",
                operation="health_check",
                stage="database_check",
                error_category="database",
                error_code="database_unavailable",
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=503, detail="Хранилище недоступно") from exc
        return {"status": "ok", "version": config.version}

    @app.post("/triage", response_model=TriageResponse)
    def triage(
        payload: TriageRequest,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> TriageResponse:
        """Классифицировать обращение, сохранить аудит и вернуть черновик."""
        if x_api_key is None or not secrets.compare_digest(x_api_key, config.api_key):
            raise HTTPException(status_code=401, detail="Неверный ключ доступа")

        try:
            allowed = repository.reserve_request(
                payload.client_id,
                time.time(),
                config.rate_limit_per_client,
                config.rate_limit_global,
            )
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "rate_limit_failed",
                "Не удалось проверить лимит",
                component="database",
                operation="reserve_request",
                stage="rate_limit",
                error_category="database",
                error_code="database_unavailable",
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=503, detail="Хранилище недоступно") from exc
        if not allowed:
            raise HTTPException(status_code=429, detail="Лимит запросов превышен")

        day = datetime.now(UTC).date().isoformat()
        try:
            reservation_id = repository.reserve_token_budget(
                day,
                token_reservation(payload, config.proxyapi_max_completion_tokens),
                config.daily_token_budget,
            )
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "token_budget_failed",
                "Не удалось проверить бюджет токенов",
                component="database",
                operation="reserve_token_budget",
                stage="token_budget",
                error_category="database",
                error_code="database_unavailable",
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=503, detail="Хранилище недоступно") from exc

        if reservation_id is None:
            outcome = triage_service.fallback("daily_token_budget_exhausted")
            log_event(
                logger,
                logging.WARNING,
                "token_budget_exhausted",
                "Суточный бюджет токенов исчерпан; обращение требует проверки оператором",
                component="llm_client",
                operation="classify",
                stage="token_budget",
                error_category="business_rule",
                error_code="daily_token_budget_exhausted",
                model=config.proxyapi_model,
                retryable=False,
            )
        else:
            outcome = triage_service.process(payload)
            try:
                repository.finalize_token_budget(reservation_id, outcome.usage)
            except sqlite3.Error as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "token_budget_failed",
                    "Не удалось учесть расход токенов",
                    component="database",
                    operation="finalize_token_budget",
                    stage="token_budget",
                    error_category="database",
                    error_code="database_write_failed",
                    error_type=type(exc).__name__,
                )
                raise HTTPException(status_code=503, detail="Хранилище недоступно") from exc
            if outcome.error_code is not None:
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_request_failed",
                    "Ответ модели недоступен; обращение требует проверки оператором",
                    component="llm_client",
                    operation="classify",
                    stage="provider_request",
                    error_category="external_service",
                    error_code=outcome.error_code,
                    error_type=outcome.error_type,
                    provider="ProxyAPI",
                    model=config.proxyapi_model,
                    retryable=False,
                )
        try:
            repository.save_ticket(
                payload, outcome.response, outcome.error_code, config.proxyapi_model, outcome.usage
            )
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "audit_write_failed",
                "Не удалось сохранить обращение",
                component="database",
                operation="save_ticket",
                stage="audit",
                error_category="database",
                error_code="database_write_failed",
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=503, detail="Хранилище недоступно") from exc

        log_event(
            logger,
            logging.INFO,
            "triage_completed",
            "Обращение обработано",
            category=outcome.response.category,
            confidence=outcome.response.confidence,
            escalate=outcome.response.escalate,
            total_tokens=outcome.usage.total_tokens if outcome.usage is not None else None,
        )
        return outcome.response

    return app
