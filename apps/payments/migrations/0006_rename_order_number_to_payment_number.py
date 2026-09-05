# ────────────────────────────────────────────────────────────────────────
# apps/payments/migrations/0006_rename_order_number_to_payment_number.py
#
# API-01 / F-8 (#73) — исправление семантики идентификатора платежа.
#
# ПРОБЛЕМА:
#   Модельное поле называлось Payment.order_number, но хранило номер
#   ПЛАТЕЖА (PAY-000001), а не номер заказа. Сериализаторы отдавали это
#   значение под ключом "order_number", то есть клиент получал PAY-номер
#   там, где по контракту обязан быть ORD-номер заказа. Ссылки на заказ
#   в payload не было вообще.
#
# РЕШЕНИЕ:
#   Переименовать поле в payment_number (идентичность платежа).
#   Ссылка на заказ отдаётся сериализатором через FK (order.order_number)
#   и отдельного столбца не требует.
#
# ПОЧЕМУ RenameField, А НЕ AddField+RemoveField:
#   RenameField выполняет ALTER TABLE ... RENAME COLUMN — данные,
#   UNIQUE-ограничение и индекс сохраняются. Пара Add/Remove потеряла бы
#   все существующие номера платежей (они не восстанавливаются: номер
#   выдан клиенту, фигурирует в чеках и в поддержке).
#
# ОБРАТИМОСТЬ: полностью обратима (обратный RENAME).
# ────────────────────────────────────────────────────────────────────────

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_paymentwebhooknonce'),
    ]

    operations = [
        migrations.RenameField(
            model_name='payment',
            old_name='order_number',
            new_name='payment_number',
        ),
        migrations.AlterField(
            model_name='payment',
            name='payment_number',
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=20,
                unique=True,
                verbose_name='Номер платежа',
            ),
        ),
    ]
