from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.discounts.tests.factories import create_test_coupon


class CouponApplyAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.order = create_test_order(
            self.user,
            subtotal=Decimal('1000.00'),
            total=Decimal('1000.00'),
        )
        self.url = reverse('discounts:coupon-apply')

    def test_apply_coupon(self):
        create_test_coupon(code='API10', discount_value=Decimal('10'))
        resp = self.client.post(self.url, {
            'code': 'API10', 'order_id': self.order.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['discount'], '100.00')  # 10% of 1000

    def test_apply_not_found_coupon(self):
        resp = self.client.post(self.url, {
            'code': 'NOPE', 'order_id': self.order.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_apply_coupon_by_order_number(self):
        """F-8 (#73): канонический order_number принимается."""
        create_test_coupon(code='NUM10', discount_value=Decimal('10'))
        resp = self.client.post(self.url, {
            'code': 'NUM10', 'order_number': self.order.order_number,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['order_number'], self.order.order_number)
        self.assertEqual(resp.data['discount'], '100.00')

    def test_apply_rejects_both_order_identifiers(self):
        """order_number + order_id одновременно → 400 (F-8, #73)."""
        create_test_coupon(code='BOTH10', discount_value=Decimal('10'))
        resp = self.client.post(self.url, {
            'code': 'BOTH10',
            'order_number': self.order.order_number,
            'order_id': self.order.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_requires_order_reference(self):
        """Ни order_number, ни order_id → 400 (F-8, #73)."""
        create_test_coupon(code='NOREF', discount_value=Decimal('10'))
        resp = self.client.post(self.url, {'code': 'NOREF'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_coupon_by_order_number(self):
        """POST /discounts/remove/ принимает order_number (F-8, #73)."""
        create_test_coupon(code='RM10', discount_value=Decimal('10'))
        self.client.post(self.url, {
            'code': 'RM10', 'order_number': self.order.order_number,
        }, format='json')

        remove_url = reverse('discounts:coupon-remove')
        resp = self.client.post(remove_url, {
            'order_number': self.order.order_number,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['order_number'], self.order.order_number)
        self.assertEqual(Decimal(resp.data['discount']), Decimal('0'))

    def test_remove_requires_order_reference(self):
        """POST /discounts/remove/ без ссылки на заказ → 400."""
        remove_url = reverse('discounts:coupon-remove')
        resp = self.client.post(remove_url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_other_users_order(self):
        """Чужой заказ при снятии скидки → 404."""
        other = create_test_user()
        order = create_test_order(other, total=Decimal('1000.00'))
        remove_url = reverse('discounts:coupon-remove')
        resp = self.client.post(remove_url, {
            'order_number': order.order_number,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_apply_other_users_order(self):
        other = create_test_user()
        order = create_test_order(other, total=Decimal('1000.00'))
        create_test_coupon(code='OTHER')
        resp = self.client.post(self.url, {
            'code': 'OTHER', 'order_id': order.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class CouponPreviewAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('discounts:coupon-preview')

    def test_preview(self):
        create_test_coupon(code='PREV', discount_value=Decimal('15'))
        resp = self.client.post(self.url, {
            'code': 'PREV', 'order_amount': '1000.00',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['calculated_discount'], '150.00')


class CouponListAPITests(TestCase):

    def test_list_requires_staff(self):
        user = create_test_user()
        client = APIClient()
        client.force_authenticate(user)
        url = reverse('discounts:coupon-list')
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_staff(self):
        staff = create_test_user(is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        create_test_coupon(code='STAFF')
        url = reverse('discounts:coupon-list')
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)
