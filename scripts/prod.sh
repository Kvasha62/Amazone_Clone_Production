#!/bin/sh
# ────────────────────────────────────────────────────────────────────────
# scripts/prod.sh — PROD-008 / F-12 канонический вход в production-стек.
#
# Гарантирует, что production всегда поднимается из правильных файлов:
#   -f docker-compose.prod.yml --env-file .env.production
# и не позволяет случайно смешать dev- и prod-конфигурацию.
#
# Использование:
#   ./scripts/prod.sh <команда>
#
# Команды (проксируются в docker compose):
#   up -d | down | stop | start | restart | ps | logs [-f] | build | config
# Утилиты:
#   migrate        — применить миграции в запущенном web-контейнере
#   collectstatic  — пересобрать static-файлы в запущенном web-контейнере
#   checks         — python manage.py check --fail-level WARNING
#
# Полная инструкция: docs/production/DEPLOYMENT.md
# ────────────────────────────────────────────────────────────────────────
set -e

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"

cd "$(dirname "$0")/.."

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "Create it from the template:  cp .env.production.example $ENV_FILE" >&2
    exit 1
fi

DC="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

case "${1:-}" in
    migrate)
        shift
        $DC exec web python manage.py migrate --noinput "$@"
        ;;
    collectstatic)
        shift
        $DC exec web python manage.py collectstatic --noinput "$@"
        ;;
    checks)
        shift
        $DC exec web python manage.py check --fail-level WARNING "$@"
        ;;
    ""|-h|--help|help)
        sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        $DC "$@"
        ;;
esac
