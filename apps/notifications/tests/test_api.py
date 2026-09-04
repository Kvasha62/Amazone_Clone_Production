from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.orders.tests.factories import create_test_user
from apps.notifications.tests.factories import create_test_notification


class NotificationListAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list(self):
        create_test_notification(self.user, title='N1')
        create_test_notification(self.user, title='N2')
        url = reverse('notifications:notification-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual(len(resp.data['results']), 2)
        # Both notification collections use the full representation.
        self.assertIn('body', resp.data['results'][0])

    def test_requires_auth(self):
        self.client.logout()
        url = reverse('notifications:notification-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class NotificationUnreadAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_unread_list(self):
        create_test_notification(self.user)
        url = reverse('notifications:notification-unread')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertIn('body', resp.data['results'][0])

    def test_unread_count(self):
        create_test_notification(self.user)
        create_test_notification(self.user)
        url = reverse('notifications:notification-unread-count')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['unread_count'], 2)


class NotificationMarkReadAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.n = create_test_notification(self.user)

    def test_mark_read(self):
        url = reverse('notifications:notification-mark-read', kwargs={'pk': self.n.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'read')

    def test_mark_read_not_found(self):
        url = reverse('notifications:notification-mark-read', kwargs={'pk': 99999})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class NotificationMarkAllReadAPITests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        create_test_notification(self.user)
        create_test_notification(self.user)

    def test_mark_all_read(self):
        url = reverse('notifications:notification-read-all')
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['marked'], 2)
