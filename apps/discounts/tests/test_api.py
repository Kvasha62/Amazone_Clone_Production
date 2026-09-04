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
