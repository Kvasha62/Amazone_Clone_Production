# ────────────────────────────────────────────────────────────────────────
# PROD-003: StockMovement.related_movement — связь парных движений.
#
# RELEASE ссылается на своё RESERVE, OUT ссылается на своё RESERVE.
# Парность делает release_stock()/commit_stock() идемпотентными:
# повторный или конкурентный вызов обрабатывает только «непарные»
# RESERVE-движения заказа и не может списать/освободить сток дважды.
# ────────────────────────────────────────────────────────────────────────

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockmovement',
            name='related_movement',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='counter_movements',
                to='inventory.stockmovement',
                verbose_name='Связанное движение',
            ),
        ),
    ]
