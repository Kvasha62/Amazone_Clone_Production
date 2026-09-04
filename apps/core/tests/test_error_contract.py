"""API-04 — canonical error envelope for public API v1 resource endpoints."""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.api_errors import (
    CODE_AUTHENTICATION,
    CODE_METHOD,
    CODE_NOT_AUTHENTICATED,
    CODE_NOT_FOUND,
    CODE_PERMISSION,
    CODE_SERVER,
    CODE_VALIDATION,
    flatten_error_details,
)
from apps.orders.tests.factories import create_test_user
from apps.users.models import Address

User = get_user_model()


def assert_envelope(test, resp, *, http_status, code):
    test.assertEqual(resp.status_code, http_status)
    test.assertTrue(resp["Content-Type"].startswith("application/json"))
    body = resp.json()
    test.assertIn("error", body)
    error = body["error"]
    test.assertEqual(set(error.keys()), {"code", "message", "details"})
    test.assertEqual(error["code"], code)
    test.assertIsInstance(error["message"], str)
    test.assertTrue(error["message"])
    test.assertIsInstance(error["details"], list)
    for item in error["details"]:
        test.assertEqual(set(item.keys()), {"field", "code", "message"})
        test.assertIsInstance(item["code"], str)
        test.assertIsInstance(item["message"], str)
    leaked = str(body).lower()
    for token in (
        "traceback",
        "runtimeerror",
        "psycopg",
        "secret",
        "eyj",
        "password=",
    ):
        test.assertNotIn(token, leaked)
    return body


class FlattenDetailsTests(TestCase):
    def test_nested_and_list_paths(self):
        details = flatten_error_details(
            {
                "email": ["already taken"],
                "items": [{"quantity": ["must be >= 1"]}],
                "non_field_errors": ["conflict"],
            }
        )
        fields = {item["field"] for item in details}
        self.assertIn("email", fields)
        self.assertIn("items[0].quantity", fields)
        self.assertIn(None, fields)

    def test_simplejwt_messages_are_not_field_paths(self):
        details = flatten_error_details(
            {
                "detail": "Given token not valid for any token type",
                "messages": [
                    {
                        "token_class": "AccessToken",
                        "token_type": "access",
                        "message": "Token is invalid or expired",
                    }
                ],
            }
        )
        fields = [item["field"] for item in details]
        self.assertEqual(fields, [None])
        self.assertTrue(
            all("messages" not in str(item["field"]) for item in details)
        )
        leaked = str(details).lower()
        self.assertNotIn("token_class", leaked)
        self.assertNotIn("accesstoken", leaked)


class UsersErrorContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="erruser",
            email="erruser@example.com",
            password="TestPass123!",
        )

    def test_register_validation_field_details(self):
        resp = self.client.post(
            "/api/v1/auth/register/",
            {
                "email": "not-an-email",
                "username": "x",
                "password": "123",
                "password_confirm": "456",
            },
            format="json",
        )
        body = assert_envelope(
            self, resp, http_status=400, code=CODE_VALIDATION,
        )
        fields = {item["field"] for item in body["error"]["details"]}
        self.assertTrue({"email", "password", "password_confirm"} & fields)

    def test_register_duplicate_email_is_list_shaped_details(self):
        self.client.post(
            "/api/v1/auth/register/",
            {
                "email": "dup@example.com",
                "username": "dup1",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )
        resp = self.client.post(
            "/api/v1/auth/register/",
            {
                "email": "dup@example.com",
                "username": "dup2",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )
        body = assert_envelope(
            self, resp, http_status=400, code=CODE_VALIDATION,
        )
        self.assertTrue(
            any(item["field"] == "email" for item in body["error"]["details"])
        )

    def test_missing_auth_on_me(self):
        resp = self.client.get("/api/v1/users/me/")
        assert_envelope(
            self, resp, http_status=401, code=CODE_NOT_AUTHENTICATED,
        )

    def test_malformed_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-jwt")
        resp = self.client.get("/api/v1/users/me/")
        assert_envelope(
            self, resp, http_status=401, code=CODE_AUTHENTICATION,
        )

    def test_inactive_user_login_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "erruser@example.com", "password": "TestPass123!"},
            format="json",
        )
        assert_envelope(
            self, login, http_status=401, code=CODE_AUTHENTICATION,
        )

    def test_address_idor_is_404(self):
        owner = self.user
        other = User.objects.create_user(
            username="othererr",
            email="othererr@example.com",
            password="TestPass123!",
        )
        address = Address.objects.create(
            user=owner,
            recipient_name="Owner",
            city="Москва",
            street="ул. 1",
        )
        self.client.force_authenticate(other)
        resp = self.client.get(f"/api/v1/users/addresses/{address.pk}/")
        assert_envelope(self, resp, http_status=404, code=CODE_NOT_FOUND)

    def test_method_not_allowed(self):
        resp = self.client.put("/api/v1/auth/login/", {}, format="json")
        assert_envelope(self, resp, http_status=405, code=CODE_METHOD)


class CatalogCartOrdersErrorContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(password="TestPass123!")
        self.staff = create_test_user(password="TestPass123!", is_staff=True)

    def test_catalog_malformed_query(self):
        resp = self.client.get("/api/v1/catalog/products/?min_price=abc")
        body = assert_envelope(
            self, resp, http_status=400, code=CODE_VALIDATION,
        )
        self.assertTrue(
            any(item["field"] == "min_price" for item in body["error"]["details"])
        )

    def test_catalog_missing_product(self):
        resp = self.client.get(
            "/api/v1/catalog/products/00000000-0000-0000-0000-000000000000/"
        )
        assert_envelope(self, resp, http_status=404, code=CODE_NOT_FOUND)

    def test_catalog_staff_create_forbidden(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/v1/catalog/products/create/",
            {"name": "X", "brand_id": 1, "primary_category_id": 1},
            format="json",
        )
        assert_envelope(self, resp, http_status=403, code=CODE_PERMISSION)

    def test_cart_validation(self):
        resp = self.client.post(
            "/api/v1/cart/items/",
            {"variant_id": "nope", "quantity": 1},
            format="json",
        )
        body = assert_envelope(
            self, resp, http_status=400, code=CODE_VALIDATION,
        )
        self.assertTrue(
            any(item["field"] == "variant_id" for item in body["error"]["details"])
        )

    def test_orders_unauthenticated(self):
        resp = self.client.get("/api/v1/orders/")
        assert_envelope(
            self, resp, http_status=401, code=CODE_NOT_AUTHENTICATED,
        )

    def test_inventory_non_staff_forbidden(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/v1/inventory/")
        assert_envelope(self, resp, http_status=403, code=CODE_PERMISSION)

    def test_reviews_missing_product_identifier_is_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/v1/reviews/",
            {"rating": 5, "text": "Достаточно длинный текст отзыва для валидации."},
            format="json",
        )
        assert_envelope(self, resp, http_status=400, code=CODE_VALIDATION)

    def test_discounts_remove_missing_order_id(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/v1/discounts/remove/", {}, format="json")
        body = assert_envelope(
            self, resp, http_status=400, code=CODE_VALIDATION,
        )
        self.assertTrue(
            any(item["field"] == "order_id" for item in body["error"]["details"])
        )

    def test_payments_idor_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/v1/payments/PAY-999999/")
        assert_envelope(self, resp, http_status=404, code=CODE_NOT_FOUND)

    def test_webhook_invalid_signature_403(self):
        resp = self.client.post(
            "/api/v1/payments/webhook/",
            {
                "external_id": "x",
                "event_type": "payment.succeeded",
                "status": "succeeded",
            },
            format="json",
        )
        assert_envelope(self, resp, http_status=403, code=CODE_PERMISSION)

    @override_settings(PAYMENT_WEBHOOK_SECRET="")
    def test_unexpected_exception_is_safe_500(self):
        with mock.patch(
            "apps.cart.api_views.cart_views.CartView.get",
            side_effect=RuntimeError("secret=internal-db-password TRACEBACK"),
        ):
            resp = self.client.get("/api/v1/cart/")
        body = assert_envelope(
            self, resp, http_status=500, code=CODE_SERVER,
        )
        self.assertEqual(body["error"]["details"], [])
        self.assertNotIn("internal-db-password", str(body))
        self.assertNotIn("TRACEBACK", str(body))

