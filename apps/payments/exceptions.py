"""Исключения домена платежей (PROD-003).

OrderConfirmationError сигнализирует, что платёж успешно подтверждён
провайдером, но подтверждение заказа не удалось. Выбрасывается из
PaymentService.confirm_payment() ДО фиксации статуса SUCCEEDED, поэтому
транзакция подтверждения откатывается целиком (платёж остаётся в
предыдущем статусе) — денежное состояние не может уйти вперёд без
соответствующего состояния заказа.

Атрибуты:
    order_status — актуальный статус заказа на момент сбоя (может быть
                   None, если прочитать его не удалось);
    db_error     — True, если причиной была ошибка БД (DatabaseError).

Обработчик (PaymentWebhookView) превращает это исключение в
наблюдаемый и восстанавливаемый результат: PaymentEvent
(order_confirm_failed) + HTTP 502 (провайдер повторит запрос) либо
закрытие платежа + 200, если заказ уже завершён.
"""

from __future__ import annotations


class OrderConfirmationError(Exception):
    """Подтверждение заказа после успешного платежа не удалось."""

    def __init__(
        self,
        message: str,
        *,
        order_status: str | None = None,
        db_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.order_status = order_status
        self.db_error = db_error
