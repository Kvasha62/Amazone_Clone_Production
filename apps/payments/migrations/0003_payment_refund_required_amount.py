# ────────────────────────────────────────────────────────────────────────
# PROD-003: Payment.refund_required_amount — явное retryable-обязательство
# возврата. Поле заполняется только сервисом при провале возврата
# (record_refund_failure_durable / refund_payment при сбое провайдера);
# проверка refund_required_amount ≤ amount защищает инвариант на уровне БД.
# ────────────────────────────────────────────────────────────────────────

import decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='refund_required_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=decimal.Decimal('0.00'),
                max_digits=12,
                validators=[
                    django.core.validators.MinValueValidator(
                        decimal.Decimal('0'),
                    ),
                ],
                verbose_name='Сумма возврата к исполнению',
            ),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('refund_required_amount__lte', models.F('amount')),
                ),
                name='payment_refund_required_lte_amount',
            ),
        ),
        # Новые типы событий PROD-003 (refund_failed,
        # order_confirm_failed) меняют choices поля event_type.
        migrations.AlterField(
            model_name='paymentevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('created', 'Платёж создан'),
                    ('status_changed', 'Статус изменён'),
                    ('webhook_received', 'Вебхук получен'),
                    ('refund_initiated', 'Возврат инициирован'),
                    ('refund_completed', 'Возврат завершён'),
                    ('refund_failed', 'Возврат не выполнен'),
                    ('cancelled', 'Платёж отменён'),
                    ('confirmed', 'Платёж подтверждён'),
                    ('callback_received', 'Callback получен'),
                    ('order_confirm_failed', 'Подтверждение заказа не удалось'),
                    ('error', 'Ошибка'),
                ],
                db_index=True,
                max_length=30,
                verbose_name='Тип события',
            ),
        ),
    ]
