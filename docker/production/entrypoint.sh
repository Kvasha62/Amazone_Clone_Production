#!/bin/sh
# ────────────────────────────────────────────────────────────────────────
# docker/production/entrypoint.sh — PROD-008 / F-12 production entrypoint.
#
# Детерминированная последовательность запуска (Issue #15, §8):
#   1. ожидание доступности БД (wait_for_db.py, fail-fast по таймауту);
#   2. миграции   — только при RUN_MIGRATIONS=true   (по умолчанию: web);
#   3. collectstatic — только при RUN_COLLECTSTATIC=true (по умолчанию: web);
#   4. exec CMD — Gunicorn / Celery worker / Celery Beat (сигналы
#      SIGTERM/SIGQUIT проксируются напрямую: graceful shutdown).
#
# Флаги задаются в docker-compose.prod.yml per-service:
#   web:         RUN_MIGRATIONS=true,  RUN_COLLECTSTATIC=true
#   celery:      RUN_MIGRATIONS=false, RUN_COLLECTSTATIC=false
#   celery-beat: RUN_MIGRATIONS=false, RUN_COLLECTSTATIC=false
# ────────────────────────────────────────────────────────────────────────
set -e

echo "[entrypoint] waiting for database ..."
python /app/docker/production/wait_for_db.py

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[entrypoint] applying database migrations ..."
    python manage.py migrate --noinput
fi

if [ "${RUN_COLLECTSTATIC:-false}" = "true" ]; then
    echo "[entrypoint] collecting static files ..."
    python manage.py collectstatic --noinput
fi

echo "[entrypoint] starting: $*"
exec "$@"
