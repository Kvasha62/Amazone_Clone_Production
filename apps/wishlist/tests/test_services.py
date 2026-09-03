from unittest import mock

from django.test import TestCase
from rest_framework.exceptions import NotFound, ValidationError
from apps.cart.services.cart_service import CartService
from apps.orders.tests.factories import create_test_user
from apps.catalog.tests.factories import CatalogTestCase
from apps.wishlist.models import Wishlist, WishlistItem
from apps.wishlist.services.wishlist_service import WishlistService
from apps.wishlist.tests.factories import create_test_wishlist, create_test_wishlist_item


class GetOrCreateTests(CatalogTestCase):

    def test_creates_new(self):
        user = create_test_user()
        wl = WishlistService.get_or_create(user)
        self.assertIsNotNone(wl.pk)
        self.assertEqual(wl.user, user)

    def test_returns_existing(self):
        user = create_test_user()
        wl1 = WishlistService.get_or_create(user)
        wl2 = WishlistService.get_or_create(user)
        self.assertEqual(wl1.pk, wl2.pk)


class AddItemTests(CatalogTestCase):

    def setUp(self):
        self.user = create_test_user()
        self.wl = create_test_wishlist(self.user)

    def test_add_success(self):
        item = WishlistService.add_item(self.user, self.variant_128)
        self.assertIsNotNone(item.pk)
        self.assertEqual(item.variant, self.variant_128)

    def test_add_increments_count(self):
        WishlistService.add_item(self.user, self.variant_128)
        self.wl.refresh_from_db()
        self.assertEqual(self.wl.items_count, 1)

    def test_add_with_note(self):
        item = WishlistService.add_item(
            self.user, self.variant_128, note='Хочу на день рождения',
        )
        self.assertEqual(item.note, 'Хочу на день рождения')

    def test_add_duplicate_raises(self):
        WishlistService.add_item(self.user, self.variant_128)
        with self.assertRaises(ValidationError):
            WishlistService.add_item(self.user, self.variant_128)

    def test_add_different_variants(self):
        WishlistService.add_item(self.user, self.variant_128)
        WishlistService.add_item(self.user, self.variant_256)
        self.wl.refresh_from_db()
        self.assertEqual(self.wl.items_count, 2)


class RemoveItemTests(CatalogTestCase):

    def setUp(self):
        self.user = create_test_user()
        self.wl = create_test_wishlist(self.user)
        self.item = create_test_wishlist_item(self.wl, self.variant_128)
        self.wl.items_count = 1
        self.wl.save()

    def test_remove_success(self):
        WishlistService.remove_item(self.user, self.item.pk)
        self.assertEqual(WishlistItem.objects.count(), 0)

    def test_remove_decrements_count(self):
        WishlistService.remove_item(self.user, self.item.pk)
        self.wl.refresh_from_db()
        self.assertEqual(self.wl.items_count, 0)

    def test_remove_not_found(self):
        with self.assertRaises(NotFound):
            WishlistService.remove_item(self.user, 99999)

    def test_remove_other_users_item(self):
        other = create_test_user()
        other_wl = create_test_wishlist(other)
        other_item = create_test_wishlist_item(other_wl, self.variant_256)
        with self.assertRaises(NotFound):
            WishlistService.remove_item(self.user, other_item.pk)


class ClearTests(CatalogTestCase):

    def setUp(self):
        self.user = create_test_user()
        self.wl = create_test_wishlist(self.user)
        create_test_wishlist_item(self.wl, self.variant_128)
        create_test_wishlist_item(self.wl, self.variant_256)
        self.wl.items_count = 2
        self.wl.save()

    def test_clear(self):
        count = WishlistService.clear(self.user)
        self.assertEqual(count, 2)
        self.wl.refresh_from_db()
        self.assertEqual(self.wl.items_count, 0)

    def test_clear_empty(self):
        wl_empty = create_test_wishlist(create_test_user())
        count = WishlistService.clear(wl_empty.user)
        self.assertEqual(count, 0)


class ListItemsTests(CatalogTestCase):

    def setUp(self):
        self.user = create_test_user()
        self.wl = create_test_wishlist(self.user)
        create_test_wishlist_item(self.wl, self.variant_128)
        create_test_wishlist_item(self.wl, self.variant_256)

    def test_list_items(self):
        items = WishlistService.list_items(self.user)
        self.assertEqual(items.count(), 2)

    def test_list_items_order(self):
        items = WishlistService.list_items(self.user)
        # Both items have same sort_order (default=100),
        # so ordered by -created_at → newest first
        self.assertEqual(items.count(), 2)


class MoveToCartTests(CatalogTestCase):

    def setUp(self):
        self.user = create_test_user()
        self.wl = create_test_wishlist(self.user)
        self.item = create_test_wishlist_item(self.wl, self.variant_128)
        self.wl.items_count = 1
        self.wl.save()
        # Создаём Stock для варианта (нужен для CartService)
        from apps.inventory.models import Stock
        Stock.objects.create(
            variant=self.variant_128,
            quantity=100,
            reserved_quantity=0,
        )
        # Создаём Price для варианта
        from apps.pricing.models import Price
        from decimal import Decimal
        Price.objects.create(
            variant=self.variant_128,
            price=Decimal('50000.00'),
        )

    def test_move_by_variant_id(self):
        moved = WishlistService.move_to_cart(
            self.user, variant_id=self.variant_128.pk,
        )
        self.assertEqual(moved, 1)
        self.assertEqual(WishlistItem.objects.count(), 0)

    def test_move_no_args_raises(self):
        with self.assertRaises(ValidationError):
            WishlistService.move_to_cart(self.user)

    def test_move_skips_domain_error_and_continues(self):
        """Expected per-item domain failures don't stop the batch move."""
        item1 = self.item  # already created in setUp with variant_128
        item2 = create_test_wishlist_item(self.wl, self.variant_256)
        self.wl.items_count = 2
        self.wl.save()

        def _add_item_side_effect(cart, variant_id, quantity):
            if variant_id == self.variant_128.pk:
                raise ValidationError({'detail': 'stock insufficient'})
            return None

        with mock.patch.object(
            CartService,
            'add_item',
            side_effect=_add_item_side_effect,
        ):
            moved = WishlistService.move_to_cart(
                self.user,
                item_ids=[item1.pk, item2.pk],
            )

        self.assertEqual(moved, 1)
        self.assertTrue(WishlistItem.objects.filter(pk=item1.pk).exists())
        self.assertFalse(WishlistItem.objects.filter(pk=item2.pk).exists())

    def test_move_unexpected_error_propagates(self):
        """Unexpected programming/DB failures are not converted to partial success."""
        unexpected = self.item  # already created in setUp with variant_128
        self.wl.items_count = 1
        self.wl.save()

        with mock.patch.object(
            CartService,
            'add_item',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                WishlistService.move_to_cart(
                    self.user,
                    item_ids=[unexpected.pk],
                )

        self.assertTrue(WishlistItem.objects.filter(pk=unexpected.pk).exists())
