"""
Тесты API endpoints пользователей.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import Address
from apps.users.tests.factories import UserTestCase

User = get_user_model()


class AuthAPITestCase(UserTestCase):

    def setUp(self):
        self.client = APIClient()


# ==========================================================
# POST /api/v1/auth/register/
# ==========================================================

class RegisterAPITests(AuthAPITestCase):

    def test_register_success(self):
        resp = self.client.post('/api/v1/auth/register/', {
            'email': 'newuser@example.com',
            'username': 'newuser',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['email'], 'newuser@example.com')
        self.assertEqual(resp.data['username'], 'newuser')
        self.assertIn('id', resp.data)

    def test_register_passwords_mismatch(self):
        resp = self.client.post('/api/v1/auth/register/', {
            'email': 'mismatch@example.com',
            'username': 'mismatch',
            'password': 'StrongPass123!',
            'password_confirm': 'DifferentPass!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        fields = [item['field'] for item in resp.data['error']['details']]
        self.assertIn('password_confirm', fields)

    def test_register_duplicate_email(self):
        self.client.post('/api/v1/auth/register/', {
            'email': 'test@example.com',
            'username': 'newname',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }, format='json')
        # Второй с тем же email
        resp = self.client.post('/api/v1/auth/register/', {
            'email': 'test@example.com',
            'username': 'another',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_missing_email(self):
        resp = self.client.post('/api/v1/auth/register/', {
            'username': 'noemail',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_short_password(self):
        resp = self.client.post('/api/v1/auth/register/', {
            'email': 'short@example.com',
            'username': 'short',
            'password': '123',
            'password_confirm': '123',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_with_names(self):
        resp = self.client.post('/api/v1/auth/register/', {
            'email': 'named@example.com',
            'username': 'named',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
            'first_name': 'Иван',
            'last_name': 'Иванов',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['first_name'], 'Иван')


# ==========================================================
# POST /api/v1/auth/login/
# ==========================================================

class LoginAPITests(AuthAPITestCase):

    def test_login_returns_tokens(self):
        """SimpleJWT login по email + password → access + refresh."""
        resp = self.client.post('/api/v1/auth/login/', {
            'email': 'test@example.com',
            'password': 'TestPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_login_wrong_password(self):
        resp = self.client.post('/api/v1/auth/login/', {
            'email': 'test@example.com',
            'password': 'WrongPass!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_nonexistent_user(self):
        resp = self.client.post('/api/v1/auth/login/', {
            'email': 'noone@example.com',
            'password': 'SomePass123!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ==========================================================
# POST /api/v1/auth/refresh/
# ==========================================================

class RefreshAPITests(AuthAPITestCase):

    def test_refresh_returns_new_access(self):
        # Получаем refresh token
        login_resp = self.client.post('/api/v1/auth/login/', {
            'email': 'test@example.com',
            'password': 'TestPass123!',
        }, format='json')
        refresh_token = login_resp.data['refresh']

        resp = self.client.post('/api/v1/auth/refresh/', {
            'refresh': refresh_token,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)

    def test_refresh_invalid_token(self):
        resp = self.client.post('/api/v1/auth/refresh/', {
            'refresh': 'invalid-token',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ==========================================================
# POST /api/v1/auth/change-password/
# ==========================================================

class ChangePasswordAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_change_password_success(self):
        resp = self.client.post('/api/v1/auth/change-password/', {
            'old_password': 'TestPass123!',
            'new_password': 'NewPass456!',
            'new_password_confirm': 'NewPass456!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Проверяем что пароль реально изменился
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPass456!'))

    def test_change_password_wrong_old(self):
        resp = self.client.post('/api/v1/auth/change-password/', {
            'old_password': 'WrongOldPass!',
            'new_password': 'NewPass456!',
            'new_password_confirm': 'NewPass456!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_mismatch(self):
        resp = self.client.post('/api/v1/auth/change-password/', {
            'old_password': 'TestPass123!',
            'new_password': 'NewPass456!',
            'new_password_confirm': 'DifferentPass!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_unauthenticated(self):
        self.client.logout()
        resp = self.client.post('/api/v1/auth/change-password/', {
            'old_password': 'TestPass123!',
            'new_password': 'NewPass456!',
            'new_password_confirm': 'NewPass456!',
        }, format='json')
        self.assertIn(resp.status_code, [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ])


# ==========================================================
# GET /api/v1/users/me/
# ==========================================================

class MeAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_get_me(self):
        resp = self.client.get('/api/v1/users/me/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['email'], 'test@example.com')
        self.assertEqual(resp.data['username'], 'testuser')
        self.assertIn('profile', resp.data)

    def test_me_unauthenticated(self):
        self.client.logout()
        resp = self.client.get('/api/v1/users/me/')
        self.assertIn(resp.status_code, [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ])

    def test_me_contains_full_name(self):
        resp = self.client.get('/api/v1/users/me/')
        self.assertEqual(resp.data['full_name'], 'Иван Тестов')

    def test_me_contains_date_joined(self):
        resp = self.client.get('/api/v1/users/me/')
        self.assertIn('date_joined', resp.data)


# ==========================================================
# PATCH /api/v1/users/me/
# ==========================================================

class UpdateMeAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_update_first_name(self):
        resp = self.client.patch('/api/v1/users/me/', {
            'first_name': 'Пётр',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['first_name'], 'Пётр')

    def test_update_phone(self):
        resp = self.client.patch('/api/v1/users/me/', {
            'phone': '+79991234567',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['phone'], '+79991234567')

    def test_update_profile_fields(self):
        resp = self.client.patch('/api/v1/users/me/', {
            'timezone': 'Europe/Moscow',
            'language': 'en',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['profile']['timezone'], 'Europe/Moscow')
        self.assertEqual(resp.data['profile']['language'], 'en')


# ==========================================================
# DELETE /api/v1/users/me/
# ==========================================================

class DeactivateMeAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_deactivate_account(self):
        resp = self.client.delete('/api/v1/users/me/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)


# ==========================================================
# Addresses API
# ==========================================================

class AddressListAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_list_addresses_empty(self):
        resp = self.client.get('/api/v1/users/addresses/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_list_addresses(self):
        self._create_address()
        resp = self.client.get('/api/v1/users/addresses/')
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['city'], 'Москва')

    def test_create_address(self):
        resp = self.client.post('/api/v1/users/addresses/', {
            'recipient_name': 'Иван Тестов',
            'city': 'Казань',
            'street': 'ул. Баумана, 1',
            'postal_code': '420000',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['city'], 'Казань')
        self.assertTrue(resp.data['id'])

    def test_create_address_missing_city(self):
        resp = self.client.post('/api/v1/users/addresses/', {
            'recipient_name': 'Иван',
            'street': 'ул. 1',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_addresses_unauthenticated(self):
        self.client.logout()
        resp = self.client.get('/api/v1/users/addresses/')
        self.assertIn(resp.status_code, [401, 403])


class AddressDetailAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.address = self._create_address()

    def test_get_address(self):
        resp = self.client.get(
            f'/api/v1/users/addresses/{self.address.pk}/',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['city'], 'Москва')

    def test_update_address(self):
        resp = self.client.patch(
            f'/api/v1/users/addresses/{self.address.pk}/',
            {'city': 'Казань'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['city'], 'Казань')

    def test_delete_address(self):
        resp = self.client.delete(
            f'/api/v1/users/addresses/{self.address.pk}/',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Address.objects.filter(pk=self.address.pk).exists())

    def test_get_not_owned(self):
        other = User.objects.create_user(
            username='addr_other', email='addr_other@example.com', password='pass',
        )
        other_address = Address.objects.create(
            user=other,
            recipient_name='Другой',
            city='СПб',
            street='ул. 5',
        )
        resp = self.client.get(
            f'/api/v1/users/addresses/{other_address.pk}/',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class AddressDefaultAPITests(AuthAPITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.addr1 = self._create_address(city='Москва', is_default=True)
        self.addr2 = self._create_address(city='СПб')

    def test_set_default(self):
        resp = self.client.post(
            f'/api/v1/users/addresses/{self.addr2.pk}/default/',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['is_default'])

        self.addr1.refresh_from_db()
        self.assertFalse(self.addr1.is_default)
