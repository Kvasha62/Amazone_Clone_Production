"""
Regression tests for F-17: cart serializer must not swallow relation errors.

``CartSerializer._get_items()`` used to ``except Exception`` and repeat the
same query. That masked DB/programming failures and could return a success
response from an inconsistent state. After the F-17 change the helper simply
propagates any exception to the existing API error boundary.
"""

from django.test import SimpleTestCase

from apps.cart.serializers import CartSerializer


class _BoomItems:
    def all(self):
        raise RuntimeError('items read failed')


class _BrokenCart:
    items = _BoomItems()


class _GoodItems:
    def all(self):
        return ['item-a', 'item-b']


class _GoodCart:
    items = _GoodItems()


class CartSerializerExceptionBoundaryTests(SimpleTestCase):
    """Behavior-level coverage for the changed exception path."""

    def test_get_items_returns_normal_queryset_result(self):
        self.assertEqual(
            CartSerializer._get_items(_GoodCart()),
            ['item-a', 'item-b'],
        )

    def test_get_items_propagates_unexpected_error(self):
        with self.assertRaises(RuntimeError):
            CartSerializer._get_items(_BrokenCart())
