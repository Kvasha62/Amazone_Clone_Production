# ==============================================================================
# apps/inventory/apps.py — Конфигурация приложения «inventory» (Склад)
# ==============================================================================
# Inventory — модуль управления складскими остатками.
# Отвечает за:
#   • Учёт остатков по каждому ProductVariant
#   • Резервирование стока при подтверждении заказа
#   • Освобождение резерва при отмене
#   • Списание при доставке
#   • Аудит всех движений (StockMovement)
#
# 📖 https://docs.djangoproject.com/en/stable/ref/applications/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Django не сможет загрузить приложение apps.inventory
#   • INSTALLED_APPS упадёт с ImportError
# ==============================================================================

from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """
    Конфигурация приложения apps.inventory.

    Django создаёт экземпляр этого класса при загрузке и использует
    его для определения имени пакета, типа PK и выполнения ready().
    """

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.inventory'
    verbose_name = 'Склад'

    def ready(self):
        """
        Подключаем сигналы после загрузки всех моделей.

        📖 https://docs.djangoproject.com/en/stable/ref/applications/#django.apps.AppConfig.ready
        """
        import apps.inventory.signals  # noqa: F401
