"""Ожидание подтверждённого деплоя указанной SHA-версии."""

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def safe_value(value: object) -> str:
    """Показать короткое значение из публичного health без управляющих символов."""
    return json.dumps(value, ensure_ascii=True)[:120]


def main() -> None:
    """Опросить публичный health endpoint с ограниченным числом попыток."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Дождаться новой версии сервиса")
    parser.add_argument("base_url", help="Публичный адрес сервиса")
    parser.add_argument("version", help="Ожидаемый полный SHA коммита")
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.attempts < 1 or arguments.interval < 1:
        parser.error("Число попыток и интервал должны быть положительными")

    base_url = arguments.base_url.rstrip("/")
    if base_url.endswith("/health"):
        parser.error("Передайте базовый адрес без /health")
    url = base_url + "/health"
    for attempt in range(1, arguments.attempts + 1):
        detail = "ответ не получен"
        try:
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=5) as response:
                payload = json.load(response)
            if isinstance(payload, dict):
                status = payload.get("status")
                version = payload.get("version")
                if status == "ok" and version == arguments.version:
                    print(f"Деплой версии {arguments.version} подтверждён", flush=True)
                    return
                detail = f"status={safe_value(status)}, version={safe_value(version)}"
            else:
                detail = "JSON не является объектом"
        except HTTPError as exc:
            detail = f"HTTP {exc.code}"
        except (URLError, TimeoutError, OSError):
            detail = "сетевая ошибка или тайм-аут"
        except ValueError:
            detail = "ответ не является JSON"
        print(
            f"Ожидание новой версии: попытка {attempt}/{arguments.attempts}; {detail}",
            flush=True,
        )
        if attempt < arguments.attempts:
            time.sleep(arguments.interval)
    parser.exit(1, "Новая версия не стала доступна за отведённое время\n")


if __name__ == "__main__":
    main()
