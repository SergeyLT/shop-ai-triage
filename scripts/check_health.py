"""Ожидание подтверждённого деплоя указанной SHA-версии."""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> None:
    """Опросить публичный health endpoint с ограниченным числом попыток."""
    parser = argparse.ArgumentParser(description="Дождаться новой версии сервиса")
    parser.add_argument("base_url", help="Публичный адрес сервиса")
    parser.add_argument("version", help="Ожидаемый полный SHA коммита")
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.attempts < 1 or arguments.interval < 1:
        parser.error("Число попыток и интервал должны быть положительными")

    url = arguments.base_url.rstrip("/") + "/health"
    for attempt in range(1, arguments.attempts + 1):
        try:
            with urlopen(url, timeout=5) as response:
                payload = json.load(response)
            if payload.get("status") == "ok" and payload.get("version") == arguments.version:
                print(f"Деплой версии {arguments.version} подтверждён")
                return
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            pass
        print(f"Ожидание новой версии: попытка {attempt}/{arguments.attempts}")
        if attempt < arguments.attempts:
            time.sleep(arguments.interval)
    parser.exit(1, "Новая версия не стала доступна за отведённое время\n")


if __name__ == "__main__":
    main()
