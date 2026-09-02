# Production Deployment — PROD-008 / F-12

**Каноническая производственная конфигурация развёртывания** репозитория
`Kvasha62/Amazone_Clone_Production` (закрывает F-12 из производственного аудита,
Issue #15).

---

## 1. Каноническая цель развёртывания и обоснование

| Параметр | Значение |
|---|---|
| Цель | **Docker Compose, один хост** — `docker-compose.prod.yml` |
| Runtime приложения | Python 3.13 (образ `python:3.13-slim`), непривилегированный пользователь `app` |
| WSGI-сервер | **Gunicorn** (sync workers, по умолчанию 3) |
| БД | **PostgreSQL 18** (образ `postgres:18`), volume `pgdata` |
| Брокер/result backend | **Redis 7** (`redis:7-alpine`, AOF-персистентность), volume `redisdata` |
| Фоновые задачи | **Celery worker** (очереди `celery,orders,cart,reviews`) |
| Шедулер | **Celery Beat** (файл расписания на volume `beatdata`) |
| Edge | **Nginx 1.27** (`nginx:1.27-alpine`): static/media + прокси на Gunicorn; единственный публичный порт |

**Почему Compose, а не Kubernetes/облако:** приложение эксплуатируется на
одном хосте; Compose уже используется в разработке (единый синтаксис),
даёт воспроизводимый, декларативный, версионируемый в git запуск без
введения новой инфраструктурной платформы (вне скоупа тикета — см. non-goals
Issue #15). Существует **ровно одна** production-стратегия; dev-файл
`docker-compose.yml` остаётся только для разработки.

## 2. Топология сервисов

```
            :8080 (PROD_HTTP_PORT)
  браузер ─────────► nginx ─┬─ /static/*  → volume static_data (RO)
                            ├─ /media/*   → volume media_data  (RO)
                            ├─ /healthz   → 200 (liveness nginx)
                            └─ прочее ──► web (gunicorn :8000)
                                             │  healthcheck → /api/v1/health/
                                             ├──► db    (postgres:18, pgdata)
                                             ├──► redis (redis:7, redisdata, AOF)
            celery worker  ──────────────────┤   (broker redis; БД для задач)
            celery beat    ──────────────────┘   (расписание → volume beatdata)
```

- `web`, `celery`, `celery-beat` — один образ `Dockerfile.backend.prod`
  (`amazone-clone-backend:prod`), разные команды.
- Порты `db`/`redis`/`web` наружу **не публикуются**; публичен только nginx.
- Все сервисы: `restart: unless-stopped`, memory-limit, healthcheck
  (кроме beat — см. §10).

## 3. Предпосылки (Prerequisites)

1. Linux-хост с **Docker Engine ≥ 24** и **docker compose v2** (`docker compose version`).
2. Свободный порт для nginx (по умолчанию `8080`; меняется `PROD_HTTP_PORT`).
3. DNS-имя (или IP), указываемое в `DJANGO_ALLOWED_HOSTS`.
4. Сгенерированные секреты (см. §4).

Чистый checkout репозитория других репозитор local-файлов не требует:
все артефакты сборки — в git, секреты — в `.env.production` (не в git).

## 4. Переменные окружения

Создайте файл окружения и заполните обязательные значения:

```bash
cp .env.production.example .env.production
python -c "import secrets; print(secrets.token_urlsafe(50))"   # DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"       # POSTGRES_PASSWORD (и вебхуки)
```

**Обязательные (fail-closed):**

| Переменная | Проверка | Комментарий |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django (PROD-007) | Пустое/`django-insecure-*` → контейнер web падает с `ImproperlyConfigured` |
| `DJANGO_ALLOWED_HOSTS` | Compose `${VAR:?}` | Отсутствует → `docker compose` отказывается стартовать |
| `POSTGRES_PASSWORD` | Compose `${VAR:?}` | Отсутствует → отказ на этапе конфигурации стека |

**Опциональные:** `POSTGRES_DB`, `POSTGRES_USER`, `CORS_ALLOW_ALL_ORIGINS`
(в prod запрещён `True` — контракт PROD-007), `CORS_ALLOWED_ORIGINS`,
`CSRF_TRUSTED_ORIGINS`, `PROD_HTTP_PORT`, `GUNICORN_WORKERS`,
`GUNICORN_TIMEOUT`, `PAYMENT_WEBHOOK_SECRET`, `THROTTLE_*`, `EMAIL_*`,
`FRONTEND_URL` — полный список с комментариями в `.env.production.example`.

Что задаётся **стеком, а не окружением** (нельзя случайно сломать):
`DJANGO_DEBUG=False`, `DB_HOST=db`, `DB_PORT=5432`, `DB_NAME/DB_USER/DB_PASSWORD`
(из `POSTGRES_*`), `REDIS_URL=redis://redis:6379/0`, внутренние хосты
`web,localhost,127.0.0.1` в `DJANGO_ALLOWED_HOSTS`.

**Секреты не коммитятся:** `.env.production` в `.gitignore`; в git — только
`.env.production.example` с безопасными заполнителями. Значение DJANGO_DEBUG
из `.env.production` игнорируется стеком.

## 5. Сборка

```bash
./scripts/prod.sh build
# эквивалент:
docker compose -f docker-compose.prod.yml --env-file .env.production build
```

Образ собирается из `Dockerfile.backend.prod`: зависимости из
`requirements.txt` (кэш слоя), затем код; `USER app` (non-root);
записываемые каталоги `/app/media`, `/app/staticfiles`, `/app/celerybeat-data`
принадлежат `app` (named volume наследует владельца при первом монтировании).

## 6. Запуск (детерминированная последовательность)

```bash
./scripts/prod.sh up -d        # = docker compose -f docker-compose.prod.yml \
                               #   --env-file .env.production up -d
```

Порядок, который обеспечивает Compose + entrypoint (Issue #15 §8):

1. **Инфраструктура**: `db` (healthcheck `pg_isready`) и `redis`
   (`redis-cli ping`) поднимаются первыми; зависимые сервисы ждут
   `service_healthy`.
2. **Доступность БД**: entrypoint-скрипт каждого Django-сервиса выполняет
   `docker/production/wait_for_db.py` (таймаут `DB_WAIT_TIMEOUT`, по умолчанию
   60 c; недоступность БД → выход с ошибкой, контейнер перезапускается по
   `restart: unless-stopped` — «safe startup при временной недоступности БД»).
3. **Миграции**: только сервис `web` (`RUN_MIGRATIONS=true`) выполняет
   `python manage.py migrate --noinput` — ровно один исполнитель, без гонок.
4. **Static**: только `web` (`RUN_COLLECTSTATIC=true`) выполняет
   `collectstatic --noinput` → volume `static_data`, который nginx отдаёт
   read-only.
5. **Приложение**: `exec gunicorn config.wsgi:application`.
6. **Worker**: `celery -A config worker -Q celery,orders,cart,reviews`
   (все очереди, определённые `task_routes` в `config/celery.py`).
7. **Шедулер**: `celery -A config beat --schedule /app/celerybeat-data/…`
   (персистентный volume).

Никаких недокументированных ручных команд не требуется: `up -d` делает всё.

**Миграции вручную** (например, при откате или особых окнах):

```bash
./scripts/prod.sh migrate        # в запущенном web-контейнере
# либо при остановленном стеке — разовый контейнер с миграциями:
docker compose -f docker-compose.prod.yml --env-file .env.production \
  run --rm --no-deps -e RUN_MIGRATIONS=true -e RUN_COLLECTSTATIC=false web \
  python manage.py migrate --noinput
```

**Static/media вручную:**

```bash
./scripts/prod.sh collectstatic
```

## 7. Static и media

| Сущность | Значение | Где живёт |
|---|---|---|
| `STATIC_URL` / `STATIC_ROOT` | `static/` / `/app/staticfiles` | volume `static_data` (web: rw, nginx: ro) |
| `MEDIA_URL` / `MEDIA_ROOT` | `/media/` / `/app/media` | volume `media_data` (web, celery: rw; nginx: ro) |

- Раздача: static и media отдаёт **nginx** (`docker/production/nginx.conf`);
  Django не раздаёт файлы при `DEBUG=False`.
- Владелец каталогов — `app` (uid из образа); nginx читает их read-only.
- Прежняя рассинхронизация dev-compose (`media:/app/uploads` ≠ `MEDIA_ROOT`)
  исправлена: dev и prod монтируют volume ровно в `/app/media` (Issue #15 §6).
- Облачное хранилище не вводилось (по условию тикета).

## 8. Health / readiness

| Уровень | Как проверяется |
|---|---|
| Процесс/контейнер | `docker compose ps` (restart policy) + healthcheck nginx `wget /healthz/` |
| Приложение + БД | `GET /api/v1/health/` → `200 {"status":"ok","database":"ok"}`; `503`, если БД недоступна (`apps/core/health_urls.py`). healthcheck web дергает его изнутри контейнера; снаружи: `curl http://<host>:8080/api/v1/health/` |
| Зависимость БД | healthcheck `db`: `pg_isready` |
| Зависимость Redis | healthcheck `redis`: `redis-cli ping` |
| Celery worker | healthcheck `celery inspect ping -d celery@$(hostname)` |
| Celery beat | явного healthcheck нет (см. §10); liveness — `restart: unless-stopped` и логи |

Проверка извне:

```bash
curl -fsS http://localhost:8080/healthz/          # nginx жив
curl -fsS http://localhost:8080/api/v1/health/    # приложение + БД
```

Health-check не раскрывает секреты и данные приложения.

## 9. Остановка / перезапуск

```bash
./scripts/prod.sh stop      # SIGTERM: gunicorn graceful (30s), celery warm shutdown
./scripts/prod.sh start     # повторный запуск тех же контейнеров
./scripts/prod.sh restart   # перезапуск
./scripts/prod.sh down      # остановка и удаление контейнеров (volumes СОХРАНЯЮТСЯ)
```

- Graceful shutdown: `stop_grace_period` (web 40s / celery 30s) покрывает
  `--graceful-timeout 30` Gunicorn; Celery завершает задачи warm-shutdown'ом.
- `down -v` **удаляет данные** (pgdata/media/…) — использовать осознанно.

Обновление версии: `git pull && ./scripts/prod.sh build && ./scripts/prod.sh up -d`
(entrypoint сам применит миграции и пересоберёт static).

## 10. Бэкап и восстановление

Персистентные данные (именованные volumes): `amazone-clone-prod-pgdata`,
`amazone-clone-prod-media`, `amazone-clone-prod-static` (восстановим из
`collectstatic`), `amazone-clone-prod-redis`, `amazone-clone-prod-beat`
(восстановим автоматически). Плюс файл `.env.production` (вне git — бэкапить
по вашей процедуре секретов).

```bash
# Бэкап БД
docker exec amazone-clone-prod-db pg_dump -U amazonclone -Fc amazonclone > backup_$(date +%F).dump
# Бэкап медиа
docker run --rm -v amazone-clone-prod-media:/data -v "$PWD":/backup alpine \
  tar czf /backup/media_$(date +%F).tar.gz -C /data .
```

Восстановление: остановить стек (`stop`), развернуть volume из архива,
развернуть БД:

```bash
cat backup.dump | docker exec -i amazone-clone-prod-db \
  pg_restore -U amazonclone -d amazonclone --clean --if-exists
./scripts/prod.sh start
```

## 11. Известные эксплуатационные ограничения

- **TLS не терминируется в стеке** (nginx слушает 80). Терминацию выполняет
  внешний LB/прокси, либо расширяется `nginx.conf` (вне скоупа тикета). Для
  admin за https задайте `CSRF_TRUSTED_ORIGINS`.
- **Один хост**: масштабирование `web` (`up -d --scale web=2`) возможно
  (сервис stateless, volumes общие); `celery-beat` должен оставаться в одном
  экземпляре; `celery` масштабируется при том же списке очередей.
- **Beat без healthcheck**: у `celery beat` нет дешёвого in-process зонда;
  liveness обеспечивается restart-политикой и логами.
- **SMTP**: production использует SMTP-бэкенд; параметры SMTP-сервера
  настраиваются переменными окружения (`EMAIL_BACKEND` и др.), детальная
  конфигурация рассылки — вне скоупа.
- **React-фронтенд не входит в этот репозиторий** (только руководства):
  стек обслуживает backend API + Django admin; origin фронтенда разрешается
  через `CORS_ALLOWED_ORIGINS`.
- Resource limits (`mem_limit`) — стартовые величины, требуют подстройки под
  нагрузку.

## 12. Автоматическая верификация

`config/tests/test_deployment_config.py` (запускается общим набором
`python manage.py test` и в CI) проверяет: структуру prod-compose (все
сервисы, отсутствие runserver, отсутствие bind-mount исходников, fail-closed
`${VAR:?}` без дефолтов секретов), консистентность media/static путей с
`settings.MEDIA_ROOT`/`STATIC_ROOT`, Dockerfile (non-root, gunicorn),
entrypoint (миграции/collectstatic/exec), nginx (proxy/static/media/healthz),
наличие всех `${VAR}` в `.env.production.example`, целостность dev-compose.

## 13. Связанные документы

- Контракт настроек (PROD-007): [`SETTINGS_CONTRACT.md`](./SETTINGS_CONTRACT.md)
- Правила репозитория: [`PRODUCTION_RULES.md`](./PRODUCTION_RULES.md)
- Базлайн: [`PRODUCTION_BASELINE.md`](./PRODUCTION_BASELINE.md)
