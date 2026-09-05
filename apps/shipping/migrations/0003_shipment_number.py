# ────────────────────────────────────────────────────────────────────────
# apps/shipping/migrations/0003_shipment_number.py
#
# API-01 / F-8 (#73) — публичный идентификатор ресурса Shipment.
#
# ЗАЧЕМ:
#   Shipment не имел собственного публичного идентификатора: наружу
#   торчал либо целочисленный PK, либо internal_tracking (внутреннее
#   поле). Frozen identifier contract требует отдельный стабильный
#   immutable public identifier — shipment_number (SHP-00000001).
#
# ПОЧЕМУ МИГРАЦИЯ НЕ ОДНОШАГОВАЯ:
#   shipment_number объявлен UNIQUE. Простой AddField выдал бы всем
#   существующим строкам одинаковое значение по умолчанию ('') и упал
#   бы на уникальном индексе, как только отправлений больше одного.
#   Поэтому порядок такой:
#     1. добавить колонки БЕЗ unique-ограничения;
#     2. backfill: пронумеровать существующие строки детерминированно
#        (по возрастанию pk — порядок создания сохраняется);
#     3. навесить UNIQUE уже на заполненные данные;
#     4. создать SEQUENCE и выставить её из MAX(_shipment_number_seq),
#        чтобы новые номера не пересеклись с backfill-нумерацией.
#
# ИСТОЧНИК НОМЕРОВ:
#   PostgreSQL SEQUENCE — тот же механизм, что ADR-005 применяет к
#   order_number. nextval() атомарен, поэтому параллельные вставки не
#   могут получить один номер (прежняя схема MAX()+1 — гонка).
#
# НЕ-POSTGRESQL BACKENDS (dev, SQLite):
#   SEQUENCE не создаётся; выдача идёт через документированный fallback
#   в apps/shipping/models/shipment.py. Backfill работает на любом бэкенде.
#
# ОБРАТИМОСТЬ:
#   reverse удаляет SEQUENCE и обе колонки; данные отправлений (заказ,
#   статус, треки) не затрагиваются.
# ────────────────────────────────────────────────────────────────────────

from django.db import migrations, models

# Должно совпадать с SHIPMENT_NUMBER_SEQUENCE в models/shipment.py.
SEQUENCE_NAME = 'shipping_shipment_number_seq'

# Должно совпадать с SHIPMENT_NUMBER_PREFIX / _DIGITS в constants.py.
PREFIX = 'SHP'
DIGITS = 8


def backfill_shipment_numbers(apps, schema_editor):
    """Присваивает публичные номера существующим отправлениям.

    Нумерация идёт по возрастанию pk, поэтому порядок номеров совпадает с
    порядком создания отправлений, а результат детерминирован и
    воспроизводим (важно, если миграцию прогоняют на копии продакшена).
    """
    Shipment = apps.get_model('shipping', 'Shipment')
    db_alias = schema_editor.connection.alias

    queryset = (
        Shipment.objects.using(db_alias)
        .order_by('pk')
        .only('pk')
    )

    batch = []
    for index, shipment in enumerate(queryset.iterator(chunk_size=500), start=1):
        shipment._shipment_number_seq = index
        shipment.shipment_number = f'{PREFIX}-{index:0{DIGITS}d}'
        batch.append(shipment)

        if len(batch) >= 500:
            Shipment.objects.using(db_alias).bulk_update(
                batch, ['_shipment_number_seq', 'shipment_number'],
            )
            batch = []

    if batch:
        Shipment.objects.using(db_alias).bulk_update(
            batch, ['_shipment_number_seq', 'shipment_number'],
        )


def noop_reverse(apps, schema_editor):
    """Обратный backfill не нужен: колонки удаляются целиком."""


def create_shipment_number_sequence(apps, schema_editor):
    """Создаёт SEQUENCE и выставляет её из MAX(_shipment_number_seq)."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    Shipment = apps.get_model('shipping', 'Shipment')
    ops = connection.ops
    table = ops.quote_name(Shipment._meta.db_table)
    column = ops.quote_name(
        Shipment._meta.get_field('_shipment_number_seq').column,
    )
    sequence = ops.quote_name(SEQUENCE_NAME)

    with connection.cursor() as cursor:
        cursor.execute(
            f'CREATE SEQUENCE IF NOT EXISTS {sequence} AS bigint MINVALUE 1'
        )
        cursor.execute(f'ALTER SEQUENCE {sequence} OWNED BY {table}.{column}')
        # is_called = FALSE → следующий nextval() вернёт value (пустая
        # таблица → первое отправление получит SHP-00000001);
        # is_called = TRUE → вернёт value + 1 (backfill-номера не
        # переиспользуются).
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


def drop_shipment_number_sequence(apps, schema_editor):
    """Удаляет SEQUENCE (данные отправлений не затрагиваются)."""
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
        ('shipping', '0002_initial'),
    ]

    operations = [
        # ── 1. Колонки без UNIQUE ──
        migrations.AddField(
            model_name='shipment',
            name='_shipment_number_seq',
            field=models.PositiveBigIntegerField(
                blank=True, db_index=True, editable=False, null=True,
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shipment_number',
            field=models.CharField(
                blank=True,
                default='',
                editable=False,
                help_text='Публичный номер отправления (SHP-00000001).',
                max_length=20,
                verbose_name='Номер отправления',
            ),
        ),

        # ── 2. Backfill существующих строк ──
        migrations.RunPython(backfill_shipment_numbers, noop_reverse),

        # ── 3. UNIQUE уже на заполненных данных ──
        migrations.AlterField(
            model_name='shipment',
            name='shipment_number',
            field=models.CharField(
                blank=True,
                editable=False,
                help_text='Публичный номер отправления (SHP-00000001).',
                max_length=20,
                unique=True,
                verbose_name='Номер отправления',
            ),
        ),

        # ── 4. SEQUENCE, выставленная из backfill-максимума ──
        migrations.RunPython(
            create_shipment_number_sequence,
            reverse_code=drop_shipment_number_sequence,
        ),
    ]
