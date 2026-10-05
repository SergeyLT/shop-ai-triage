#!/usr/bin/env bash
# Проверка Linux-команд README в свежем клоне с отдельными ресурсами Compose.
set -Eeuo pipefail
umask 077

repo_url='https://github.com/SergeyLT/shop-ai-triage.git'
git_ref='main'
port='18001'
while (($#)); do
    case "$1" in
        --repo|--ref|--port)
            (($# >= 2)) || { printf 'Нет значения для %s\n' "$1" >&2; exit 2; }
            case "$1" in
                --repo) repo_url=$2 ;;
                --ref) git_ref=$2 ;;
                --port) port=$2 ;;
            esac
            shift 2 ;;
        --help)
            printf '%s\n' 'bash scripts/verify-linux-readme.sh [--port 18001] [--ref main] [--repo URL]'
            printf '%s\n' 'Нужны git, curl, Docker Engine и Docker Compose v2. Выполняется один платный вызов ProxyAPI.'
            exit 0 ;;
        *) printf 'Неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ $port =~ ^[0-9]{1,5}$ ]] && ((10#$port >= 1024 && 10#$port <= 65535)) || {
    printf 'Порт должен быть числом от 1024 до 65535.\n' >&2; exit 2;
}
for command_name in git curl docker mktemp cmp; do
    command -v "$command_name" >/dev/null || { printf 'Не найдена команда %s\n' "$command_name" >&2; exit 1; }
done
docker info >/dev/null
docker compose version

workdir=$(mktemp -d "${TMPDIR:-/tmp}/shop-triage-linux-check.XXXXXXXX")
checkout="$workdir/repository"
results="$workdir/results"
mkdir "$results"
project="shoptriage-check-$(date -u +%Y%m%d%H%M%S)-$$"
base_url="http://127.0.0.1:$port"
started=0
compose() {
    # HOST_PORT из родительского окружения не должен перекрыть локальный .env.
    env -u HOST_PORT -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME -u COMPOSE_PROFILES \
        docker compose --project-directory "$checkout" --env-file "$checkout/.env" \
        -f "$checkout/compose.yml" -p "$project" "$@"
}
finish() {
    local code=$?
    trap - EXIT
    if ((started)); then
        compose logs --no-color api > "$results/logs-after-restart.txt" 2>&1 || true
        if ! compose down --volumes --rmi local > "$results/cleanup.txt" 2>&1; then
            printf 'Не удалось очистить тестовый проект %s; см. %s/cleanup.txt\n' "$project" "$results" >&2
            code=1
        fi
    fi
    # Файл конфигурации с ключами не входит в сохраняемые результаты.
    rm -f -- "$checkout/.env"
    unset proxy_key triage_key
    printf '\nРезультаты: %s\n' "$results"
    if ((code == 0)); then
        printf 'ПРОВЕРКА УСПЕШНА: сборка, API, модель, SQLite и сохранность данных.\n'
        printf 'PASS\n' > "$results/status.txt"
    else
        printf 'ПРОВЕРКА НЕ ПРОЙДЕНА. См. вывод выше и сохранённые файлы.\n' >&2
        printf 'FAIL\n' > "$results/status.txt"
    fi
    exit "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Свежий клон: %s\nCompose-проект: %s\nЛокальный адрес: %s\n' "$checkout" "$project" "$base_url"
# Публичные исходники должны читаться пользователем app после COPY в образ.
# Закрытая маска 077 продолжает действовать для .env и результатов проверки.
(umask 022; git clone -- "$repo_url" "$checkout")
git -C "$checkout" checkout --detach "$git_ref"
git -C "$checkout" rev-parse HEAD > "$results/commit.txt"
printf 'SHA: %s\n' "$(cat "$results/commit.txt")"
cp "$checkout/.env.example" "$checkout/.env"
triage_key=$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_urlsafe(48))')
printf 'Будет выполнен один реальный запрос к модели через ProxyAPI.\n'
read -rsp 'Введите PROXYAPI_API_KEY (ввод скрыт): ' proxy_key
printf '\n'
[[ $proxy_key =~ ^[A-Za-z0-9_.:-]+$ ]] || { printf 'Ключ пуст или содержит неподдерживаемые символы.\n' >&2; exit 1; }
# Эквивалент редактирования .env в README, без запуска редактора и вывода ключей.
while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    case "$line" in
        TRIAGE_API_KEY=*) printf "TRIAGE_API_KEY='%s'\n" "$triage_key" ;;
        PROXYAPI_API_KEY=*) printf "PROXYAPI_API_KEY='%s'\n" "$proxy_key" ;;
        HOST_PORT=*) printf 'HOST_PORT=127.0.0.1:%s\n' "$port" ;;
        APP_ENVIRONMENT=*) printf 'APP_ENVIRONMENT=linux-readme-check\n' ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$checkout/.env.example" > "$checkout/.env"
unset proxy_key

printf '\nСборка по README…\n'
compose build 2>&1 | tee "$results/build.txt"
printf '\nЗапуск по README…\n'
started=1
compose up -d
wait_health() {
    local attempt
    for attempt in {1..60}; do
        if curl -fsS --max-time 3 "$base_url/health" > "$results/health.json" 2>/dev/null; then
            compose exec -T api python -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"' < "$results/health.json"
            cat "$results/health.json"
            printf '\n'
            return 0
        fi
        sleep 2
    done
    printf 'Сервис не ответил на /health.\n' >&2
    compose ps -a >&2 || true
    compose logs --no-color --tail 30 api >&2 || true
    return 1
}
wait_health

printf '\nОдин вызов /triage…\n'
# Передаём заголовок через stdin: ключ не попадает в аргументы curl и результаты.
status=$(printf 'header = "X-API-Key: %s"\n' "$triage_key" | \
    curl --config - --silent --show-error --max-time 90 \
    --output "$results/response.json" --dump-header "$results/headers.txt" \
    --write-out '%{http_code}' "$base_url/triage" \
    -H 'Content-Type: application/json' \
    --data '{"text":"Заказ оплачен, но статус не обновился","channel":"form","client_id":"linux-readme-check"}')
printf 'HTTP %s\n' "$status"
[[ $status == 200 ]] || { cat "$results/response.json"; exit 1; }
compose exec -T api python -c 'import sys; from shop_triage.models import TriageResponse; print(TriageResponse.model_validate_json(sys.stdin.read()).model_dump_json(indent=2))' < "$results/response.json"
unset triage_key

check_audit() {
    compose exec -T api python - <<'PY'
import json
import os
import sqlite3

with sqlite3.connect(os.environ["DATABASE_PATH"]) as connection:
    connection.row_factory = sqlite3.Row
    assert connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    row = dict(connection.execute(
        "SELECT id, created_at, client_id, channel, category, confidence, escalate, "
        "error, model, total_tokens FROM tickets ORDER BY id DESC LIMIT 1"
    ).fetchone())
assert row["client_id"] == "linux-readme-check"
assert row["error"] is None, f"Модель не ответила: {row['error']}"
assert row["model"] == "openai/gpt-6-luna"
assert row["total_tokens"] is not None and row["total_tokens"] > 0
print(json.dumps(row, ensure_ascii=False, indent=2))
PY
}
check_audit > "$results/audit-before.json"
compose exec -T api python -m shop_triage.audit --limit 1 | tee "$results/audit.txt"
compose exec -T api python -m shop_triage.budget | tee "$results/budget.txt"
compose logs --no-color api > "$results/logs-request.txt" 2>&1

printf '\nПересоздание контейнера с сохранением тома…\n'
compose down
compose up -d
wait_health
check_audit > "$results/audit-after.json"
cmp "$results/audit-before.json" "$results/audit-after.json"
printf 'Запись SQLite сохранена без изменений.\n'
