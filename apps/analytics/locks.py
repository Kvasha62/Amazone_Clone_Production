# ────────────────────────────────────────────────────────────────────────
# apps/analytics/locks.py — сериализация дедупликации просмотров товара.
#
# PROD-021 / F-22.
#
# ЗАЧЕМ ЭТОТ ФАЙЛ:
#   AnalyticsService.record_view() реализует инвариант «один
#   пользователь/сессия → один просмотр товара в час» как
#   check-then-insert: SELECT EXISTS(...) → INSERT. transaction.atomic()
#   такую последовательность НЕ сериализует: две конкурентные транзакции
#   (READ COMMITTED — уровень по умолчанию в PostgreSQL) обе видят
#   «просмотра нет» и обе вставляют строку, дважды инкрементируя
#   Product.views_count.
#
# ПОЧЕМУ НЕ UNIQUE-ОГРАНИЧЕНИЕ:
#   Инвариант задан СКОЛЬЗЯЩИМ окном в один час («за последний час»),
#   а не календарным ведром. Скользящий интервал невыразим ни в UNIQUE,
#   ни в UniqueConstraint(condition=...): ключ уникальности должен быть
#   детерминированной функцией строки, а «час назад» зависит от момента
#   запроса. Замена окна на календарный час (date_trunc) изменила бы
#   внешне наблюдаемое поведение (AC-4/AC-7), поэтому контракт сохранён,
#   а сериализуется сама проверка.
#
# МЕХАНИЗМ:
#   pg_advisory_xact_lock(bigint) — транзакционный advisory-лок ровно на
#   ключ дедупликации (товар + личность зрителя). Он:
#     • сериализует ТОЛЬКО конкурирующие за один и тот же ключ запросы
#       (разные товары/сессии/пользователи не блокируют друг друга);
#     • снимается автоматически при COMMIT или ROLLBACK — «протекающих»
#       локов и ручного unlock не существует (AC-9);
#     • не требует изменения схемы и миграции (AC-11);
#     • не зависит от тайминга приложения (AC-6).
#
# NON-POSTGRESQL BACKENDS (dev-режим, например SQLite):
#   advisory-локов нет — вызов no-op с предупреждением в логе. Это НЕ
#   production-путь: production-СУБД проекта — PostgreSQL (CI и
#   docker-compose.prod).
#
# 📖 https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • AnalyticsService → ImportError;
#   • дедупликация просмотров снова становится гонкой (F-22).
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import logging

from django.db import connections, router

logger = logging.getLogger(__name__)

#: Пространство имён advisory-локов модуля аналитики.
#: Входит в хешируемый ключ, чтобы не пересекаться с локами других
#: подсистем, работающих на той же БД.
LOCK_NAMESPACE = 'analytics.product_view.dedup'

#: Границы signed bigint — тип аргумента pg_advisory_xact_lock().
_BIGINT_RANGE = 1 << 64
_BIGINT_OFFSET = 1 << 63


def dedup_identity(product_id: int, *, user_id=None, session_key: str = '') -> str | None:
    """
    Каноническая «личность» для дедупликации просмотра.

    СЕМАНТИКА (совпадает с record_view()):
      • авторизованный пользователь → (товар, user_id) — session_key
        игнорируется, у одного пользователя может быть несколько сессий;
      • аноним → (товар, session_key);
      • ни того, ни другого → дедупликация неприменима (None).
    """
    if user_id is not None:
        return f'{LOCK_NAMESPACE}:product={product_id}:user={user_id}'
    if session_key:
        return f'{LOCK_NAMESPACE}:product={product_id}:session={session_key}'
    return None


def lock_key(identity: str) -> int:
    """
    Детерминированный signed-bigint ключ advisory-лока.

    blake2b (а не встроенный hash()) — потому что PYTHONHASHSEED
    рандомизирует hash() строк: воркеры gunicorn получали бы РАЗНЫЕ
    ключи для одной личности, и лок не сериализовал бы ничего.
    """
    digest = hashlib.blake2b(identity.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big') % _BIGINT_RANGE - _BIGINT_OFFSET


def acquire_dedup_lock(product_id: int, *, user_id=None, session_key: str = '', using=None) -> bool:
    """
    Берёт транзакционный advisory-лок на ключ дедупликации.

    Вызывать ТОЛЬКО внутри transaction.atomic(): лок живёт до конца
    транзакции и снимается сервером при commit/rollback.

    RETURNS:
        True  — лок взят (проверка+вставка сериализованы);
        False — лок неприменим (нет личности или бэкенд без advisory-локов).
    """
    identity = dedup_identity(product_id, user_id=user_id, session_key=session_key)
    if identity is None:
        return False

    connection = connections[using or router.db_for_write(_model())]
    if connection.vendor != 'postgresql':
        logger.warning(
            'product_view_dedup_lock_unsupported_backend',
            extra={'vendor': connection.vendor, 'product_id': product_id},
        )
        return False

    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key(identity)])
    return True


def _model():
    """Ленивый импорт модели — модуль импортируется из services/."""
    from apps.analytics.models import ProductView
    return ProductView
