# ────────────────────────────────────────────────────────────────────────
# apps/orders/migrations/0003_order_number_sequence.py
#
# F-13 / PROD-010 — PostgreSQL SEQUENCE для выдачи номеров заказов.
#
# ЗАЧЕМ НУЖНА МИГРАЦИЯ:
#   Устранение гонки «SELECT MAX(_order_number_seq) → +1 → INSERT»
#   невозможно без источника номеров, который выдаёт значения атомарно
#   на стороне СУБД. Им становится SEQUENCE orders_order_number_seq:
#   nextval() возвращает уникальное значение каждому вызову без чтения
#   таблицы, поэтому параллельные создания заказа не могут получить
#   одинаковый order_number.
#
# ЧТО ДЕЛАЕТ МИГРАЦИЯ:
#   1. CREATE SEQUENCE IF NOT EXISTS orders_order_number_seq (bigint, MINVALUE 1)
#   2. Привязывает SEQUENCE к orders_order._order_number_seq (OWNED BY) —
#      схема остаётся согласованной при удалении таблицы.
#   3. Выставляет текущее значение из MAX(_order_number_seq): существующие
#      заказы сохраняют свои номера, новые номера начинаются со следующего
#      значения (для пустой таблицы — с 1).
#
# СХЕМА МОДЕЛЕЙ НЕ МЕНЯЕТСЯ:
#   Ни поля, ни индексы, ни ограничения не меняются — order_number остаётся
#   CharField(unique=True), _order_number_seq остаётсяPositiveBigIntegerField.
#   Поэтому makemigrations не детектит изменений состояния, а публичный
#   контракт номера (ORD-000001) сохранён.
#
# НЕ-POSTGRESQL BACKENDS (dev-режим, SQLite):
#   SEQUENCE не создаётся (операция — no-op), выдача номера идёт через
#   задокументированный dev-fallback в apps/orders/models/order.py.
#
# ОБРАТИМОСТЬ:
#   reverse удаляет SEQUENCE; данные заказов не затрагиваются.
#
# 📖 https://www.postgresql.org/docs/current/sql-createsequence.html
# 📖 https://docs.djangoproject.com/en/stable/ref/migration-operations/#runpython
# ────────────────────────────────────────────────────────────────────────

from django.db import migrations

# Имя должно совпадать с ORDER_NUMBER_SEQUENCE в apps/orders/models/order.py.
SEQUENCE_NAME = 'orders_order_number_seq'


def create_order_number_sequence(apps, schema_editor):
    """Создаёт SEQUENCE и выставляет её из MAX(_order_number_seq)."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        # Бэкенд без SEQUENCE (dev-режим) — не production-путь.
        return

    Order = apps.get_model('orders', 'Order')
    ops = connection.ops
    table = ops.quote_name(Order._meta.db_table)
    column = ops.quote_name(Order._meta.get_field('_order_number_seq').column)
    sequence = ops.quote_name(SEQUENCE_NAME)

    with connection.cursor() as cursor:
        cursor.execute(
            f'CREATE SEQUENCE IF NOT EXISTS {sequence} '
            f'AS bigint MINVALUE 1'
        )
        cursor.execute(
            f'ALTER SEQUENCE {sequence} OWNED BY {table}.{column}'
        )
        # setval(seq, value, is_called):
        #   is_called = FALSE → следующий nextval() вернёт value (пустая
        #   таблица → первый заказ получит ORD-000001);
        #   is_called = TRUE  → следующий nextval() вернёт value + 1
        #   (существующие номера не переиспользуются).
        cursor.execute(
            f"""
            SELECT setval(
                '{SEQUENCE_NAME}',
                GREATEST(COALESCE(MAX({column}), 0), 1),
                COALESCE(MAX({column}), 0) > 0
            )
            FROM {table}
            """
        )


def drop_order_number_sequence(apps, schema_editor):
    """Удаляет SEQUENCE (данные заказов не затрагиваются)."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    with connection.cursor() as cursor:
        cursor.execute(
            f'DROP SEQUENCE IF EXISTS '
            f'{connection.ops.quote_name(SEQUENCE_NAME)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(
            create_order_number_sequence,
            reverse_code=drop_order_number_sequence,
        ),
    ]
