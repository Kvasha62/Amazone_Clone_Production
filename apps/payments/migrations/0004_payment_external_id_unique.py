# ────────────────────────────────────────────────────────────────
# apps/payments/migrations/0004_payment_external_id_unique.py
#
# F-15 / PROD-014 — инвариант уникальности Payment.external_id.
#
# АВТОРИТЕТНОЕ ПРОСТРАНСТВО УНИКАЛЬНОСТИ (из существующего кода):
#   Непустой external_id ГЛОБАЛЬНО уникален, а не в рамках
#   (provider, external_id). Доказательство из продакшн-путей:
#     • вебхук-корреляция ADR-004 — PaymentQuerySet.with_external_id()
#       фильтрует ТОЛЬКО по external_id, provider в выборке не участвует
#       (PaymentService.handle_webhook → .first());
#     • fallback вебхук-view (OrderConfirmationError) — тоже filter
#       по external_id без provider;
#     • HandleWebhookInputSerializer не содержит поля provider —
#       провайдер присылает только идентификатор платежа;
#     • ни одного комбинированного фильтра (provider + external_id)
#       в кодовой базе нет.
#   Один и тот же external_id у двух провайдеров сделал бы
#   .first() неоднозначным → платёж мог бы быть зачислен не тому
#   заказу. Поэтому дубликаты запрещены глобально, в том числе
#   между разными провайдерами.
#
# CHТО ДЕЛАЕТ МИГРАЦИЯ:
#   1. RunPython: fail-loud проверка существующих данных — если в БД
#      уже есть дубликаты непустых external_id, миграция падает с
#      явным списком конфликтующих платёжных номеров. Ничего не
#      удаляется и не переписывается: разрешение конфликта — явное
#      решение оператора (историю платежей трогать запрещено).
#   2. AlterField external_id: убирает обычный db_index — его
#       замещает частичный уникальный индекс из AddConstraint
#       (тот же столбец; продакшн-путь вебхук-корреляции всегда
#       ищет конкретный непустой ID, который удовлетворяет
#       предикату индекса, поэтому план запросов не деградирует).
#   3. AddConstraint payment_external_id_unique: частичный
#      UNIQUE-индекс PostgreSQL:
#        UNIQUE (external_id) WHERE external_id <> ''
#
# BLANK-ЗНАЧЕНИЯ (AC-7):
#   external_id — blank/default='' (платёж до назначения ID
#   провайдером). Условие индекса исключает '' → сколько угодно
#   платежей с пустым external_id (несколько попыток оплаты на
#   один заказ сохранены, PROD-003 не затронут).
#
# ОБРАТИМОСТЬ:
#   reverse возвращает db_index и удаляет constraint; данные не
#   изменяются (RunPython.reverse — no-op).
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/constraints/
# 📖 https://www.postgresql.org/docs/current/indexes-partial.html
# ────────────────────────────────────────────────────────────────

from django.db import migrations, models


MAX_LISTED_CONFLICTS = 10


def assert_no_duplicate_external_ids(apps, schema_editor):
    """
    Fail-loud защита существующих данных (AC-3).

    Находит дубликаты НЕпустых external_id (те, что нарушали бы новый
    UNIQUE-индекс). При обнаружении — RuntimeError с явным списком
    конфликтующих платёжных номеров. Записи НЕ удаляются, НЕ
    переписываются и не «дедуплицируются» молча: оператор обязан
    разрешить конфликт явно, потому что каждая запись — платёжная
    история, а выбор «правильного» платежа требует сверки с
    провайдером, а не автоматического решения.
    """
    Payment = apps.get_model('payments', 'Payment')

    duplicates = (
        Payment.objects
        .exclude(external_id='')
        .values('external_id')
        .annotate(ids_count=models.Count('id'))
        .filter(ids_count__gt=1)
        .order_by('external_id')
    )

    conflicts = []
    for dup in duplicates:
        numbers = list(
            Payment.objects
            .filter(external_id=dup['external_id'])
            .order_by('id')
            .values_list('order_number', flat=True)
        )
        conflicts.append((dup['external_id'], numbers))
        if len(conflicts) >= MAX_LISTED_CONFLICTS:
            break

    if conflicts:
        listed = '\n'.join(
            f'  • {ext_id!r}: платежи {", ".join(numbers)}'
            for ext_id, numbers in conflicts
        )
        raise RuntimeError(
            'F-15: обнаружены дубликаты Payment.external_id — создание '
            'UNIQUE-индекса payment_external_id_unique невозможно без '
            'ручного разрешения конфликта. История платежей не '
            'изменяется автоматически. Конфликтующие external_id '
            f'(показано не более {MAX_LISTED_CONFLICTS}):\n{listed}\n'
            'Разрешите конфликт явно (сверка с платёжным провайдером) '
            'и повторите миграцию.'
        )

    # Пустые external_id намеренно не проверяются на уникальность:
    # условие частичного индекса исключает blank-значения — платежи
    # без назначенного провайдером ID остаются легитимными.


def noop(apps, schema_editor):
    """Данные не изменяются — обращение не требует действий."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_payment_refund_required_amount'),
    ]

    operations = [
        migrations.RunPython(
            assert_no_duplicate_external_ids,
            reverse_code=noop,
        ),
        migrations.AlterField(
            model_name='payment',
            name='external_id',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='Внешний ID'),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.UniqueConstraint(
                condition=models.Q(('external_id', ''), _negated=True),
                fields=('external_id',),
                name='payment_external_id_unique',
            ),
        ),
    ]
