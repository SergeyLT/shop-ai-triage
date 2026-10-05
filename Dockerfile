FROM python:3.12-slim-bookworm

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    DATABASE_PATH=/data/tickets.sqlite3 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE logging.json ./
COPY src ./src

# COPY сохраняет права исходных файлов: сборка должна работать и при umask 077.
RUN chmod -R a+rX /app \
    && pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3).close()"

CMD ["uvicorn", "shop_triage.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-config", "/app/logging.json"]
