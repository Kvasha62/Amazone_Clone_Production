# ────────────────────────────────────────────────────────────────────────
# apps/core/health_urls.py — health-check для React.
#
# GET /api/v1/health/ → {"status": "ok", "version": "1.0.0"}
#
# React при запуске проверяет «живой ли бэкенд?».
# Без этого фронтенд-разработчик не понимает:
#   • Бэкенд не запущен?
#   • Сеть недоступна?
#   • Ошибка в API?
# ────────────────────────────────────────────────────────────────────────

from django.db import Error
from django.http import JsonResponse
from django.urls import path
from django.views import View


class HealthCheckView(View):
    """
    Health-check endpoint.

    GET /api/v1/health/

    Возвращает:
      {
          "status": "ok",
          "version": "1.0.0",
          "database": "ok"
      }

    Используется React-приложением для проверки доступности бэкенда.
    """

    def get(self, request):
        # Проверяем что БД отвечает
        db_ok = True
        try:
            from django.db import connection
            connection.ensure_connection()
        except Error:
            # Health-контракт: недоступность БД → 503 degraded. Ловим
            # только django.db.Error (database/operational failures), а не
            # произвольные программные ошибки/конфигурационные сбои.
            db_ok = False

        status_code = 200 if db_ok else 503

        return JsonResponse(
            {
                "status": "ok" if db_ok else "degraded",
                "version": "1.0.0",
                "database": "ok" if db_ok else "error",
            },
            status=status_code,
        )


urlpatterns = [
    path('', HealthCheckView.as_view(), name='health'),
]
