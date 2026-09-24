"""Проверка адреса webhook деплоя перед отправкой токена Coolify."""

import os
import sys
from urllib.parse import parse_qs, urlsplit


def is_deploy_webhook(value: str) -> bool:
    """Принять только HTTPS endpoint деплоя одного ресурса Coolify."""
    if not value or value != value.strip():
        return False
    try:
        url = urlsplit(value)
        query = parse_qs(url.query, keep_blank_values=True)
        return (
            url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and url.path == "/api/v1/deploy"
            and not url.fragment
            and len(query.get("uuid", [])) == 1
            and bool(query["uuid"][0])
            and "tag" not in query
        )
    except ValueError:
        return False


def main() -> int:
    """Сообщить о неверном типе webhook, не печатая сам адрес."""
    if is_deploy_webhook(os.getenv("COOLIFY_WEBHOOK", "")):
        return 0
    sys.stderr.reconfigure(encoding="utf-8")
    print(
        "COOLIFY_WEBHOOK должен содержать HTTPS-адрес Deploy Webhook (auth required) "
        "вида /api/v1/deploy?uuid=... из настроек приложения Coolify.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
