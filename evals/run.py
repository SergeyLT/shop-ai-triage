"""Запустить согласованный набор обращений через HTTP API по явной команде."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CASES_PATH = Path(__file__).with_name("cases.json")


def read_local_key(env_file: Path) -> str:
    """Прочитать только ключ приложения из локального файла окружения."""
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "TRIAGE_API_KEY":
            return value.strip().strip("\"'")
    return ""


def request_json(url: str, payload: dict[str, str] | None, api_key: str) -> tuple[int, object]:
    """Отправить один запрос и вернуть код HTTP с безопасно разобранным ответом."""
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json", "X-API-Key": api_key}
    request = Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urlopen(request, timeout=40) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (ValueError, UnicodeDecodeError):
            return exc.code, {"detail": "Ответ сервиса не является JSON"}


def main() -> int:
    """Проверить доступность сервиса и сохранить фактические результаты."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Платная проверка качества через запущенный API")
    parser.add_argument("--live", action="store_true", help="Явно разрешить вызовы модели")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--ids", nargs="*", help="Запустить только указанные ID")
    parser.add_argument("--output", type=Path, default=Path("output/evals/latest.json"))
    args = parser.parse_args()
    if not args.live:
        parser.error("Добавьте --live: каждый пример вызывает платную модель")

    api_key = os.getenv("TRIAGE_API_KEY", "").strip()
    if not api_key:
        if not args.env_file.is_file():
            parser.error(f"Файл окружения не найден: {args.env_file}")
        api_key = read_local_key(args.env_file)
    if not api_key or api_key.startswith("replace-with-"):
        parser.error("Задайте TRIAGE_API_KEY в окружении или в --env-file")

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or len(ids) < 20:
        parser.error("Набор должен содержать не менее 20 обращений с уникальными ID")
    selected = set(args.ids) if args.ids else set(ids)
    if unknown := selected.difference(ids):
        parser.error(f"Неизвестные ID: {', '.join(sorted(unknown))}")

    base_url = args.base_url.rstrip("/")
    try:
        health_status, health = request_json(f"{base_url}/health", None, "")
    except URLError as exc:
        print(f"Сервис недоступен: {exc.reason}", file=sys.stderr)
        return 1
    if health_status != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        print(f"Сервис не готов: HTTP {health_status}", file=sys.stderr)
        return 1

    results = []
    for case in cases:
        if case["id"] not in selected:
            continue
        payload = {
            "text": case["text"],
            "channel": case["channel"],
            "client_id": f"eval-{case['id'].lower()}",
        }
        try:
            status, response = request_json(f"{base_url}/triage", payload, api_key)
        except URLError as exc:
            status, response = 0, {"detail": f"Сетевая ошибка: {type(exc.reason).__name__}"}
        fallback = (
            isinstance(response, dict)
            and response.get("draft_reply")
            == "Не удалось подготовить ответ автоматически. Обращение требует проверки оператором."
        )
        auto = {
            "model_result": status == 200 and not fallback,
            "category": status == 200
            and not fallback
            and isinstance(response, dict)
            and response.get("category") == case["expected_category"],
            "escalate": status == 200
            and not fallback
            and isinstance(response, dict)
            and response.get("escalate") is case["expected_escalate"],
        }
        results.append(
            {"id": case["id"], "http_status": status, "response": response, "auto": auto}
        )
        print(
            f"{case['id']}: HTTP {status}; модель "
            f"{'OK' if auto['model_result'] else 'FALLBACK'}; категория "
            f"{'OK' if auto['category'] else 'FAIL'}; эскалация "
            f"{'OK' if auto['escalate'] else 'FAIL'}",
            flush=True,
        )

    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "health_version": health.get("version"),
        "case_count": len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Результаты сохранены: {args.output}")
    return 0 if all(all(row["auto"].values()) for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
