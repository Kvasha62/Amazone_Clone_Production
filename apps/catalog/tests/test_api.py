"""
Тесты API endpoints каталога.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.api_views.product_brief_views import ProductBySlugsView
from apps.catalog.constants import ProductStatus
from apps.catalog.models import (
    Brand,
    Category,
    Product,
    Tag,
)

User = get_user_model()


class CatalogAPITestCase(TestCase):
    """Базовый класс для API-тестов каталога."""

    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(name='Samsung')
        cls.root_cat = Category.add_root(name='Электроника')
        cls.leaf_cat = cls.root_cat.add_child(name='Смартфоны')
        cls.tag = Tag.objects.create(name='флагман-api-test')

        cls.product = Product.objects.create(
            name='Galaxy S24',
            brand=cls.brand,
            primary_category=cls.leaf_cat,
            status=ProductStatus.ACTIVE,
            min_price=Decimal('800.00'),
            max_price=Decimal('1200.00'),
        )
        cls.product.categories.add(cls.leaf_cat)
        cls.product.tags.add(cls.tag)

        cls.staff_user = User.objects.create_user(
            username='staff',
            email='staff@test.com',
            password='testpass123',
            is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            username='regular',
            email='regular@test.com',
            password='testpass123',
        )

    def setUp(self):
        self.client = APIClient()


# ==========================================================
# PRODUCT LISTING
# ==========================================================

class ProductListAPITests(CatalogAPITestCase):

    def test_list_products(self):
        resp = self.client.get('/api/v1/catalog/products/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_has_results(self):
        resp = self.client.get('/api/v1/catalog/products/')
        self.assertIn('results', resp.data)
        self.assertTrue(len(resp.data['results']) > 0)

    def test_list_filter_by_brand(self):
        resp = self.client.get('/api/v1/catalog/products/', {
            'brand': self.brand.slug,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_filter_by_category(self):
        resp = self.client.get('/api/v1/catalog/products/', {
            'category': self.leaf_cat.slug,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_filter_by_price(self):
        resp = self.client.get('/api/v1/catalog/products/', {
            'min_price': '500',
            'max_price': '1500',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_search(self):
        resp = self.client.get('/api/v1/catalog/products/', {
            'search': 'Galaxy',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_ordering(self):
        resp = self.client.get('/api/v1/catalog/products/', {
            'ordering': '-rating',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_invalid_ordering_fallback(self):
        """Невалидный ordering обрабатывается сервисом — fallback на -created_at."""
        resp = self.client.get('/api/v1/catalog/products/', {
            'ordering': '-created_at',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_excludes_draft(self):
        draft = Product.objects.create(
            name='Draft',
            brand=self.brand,
            primary_category=self.leaf_cat,
            status=ProductStatus.DRAFT,
        )
        resp = self.client.get('/api/v1/catalog/products/')
        ids = [p['id'] for p in resp.data['results']]
        self.assertNotIn(str(draft.uuid), ids)


# ==========================================================
# PRODUCT BY SLUGS (recently viewed)
# ==========================================================

class ProductBySlugsAPITests(CatalogAPITestCase):
    """F-17: product-brief lookup must not swallow unexpected errors."""

    def test_by_slugs_returns_matching_product(self):
        resp = self.client.get(
            '/api/v1/catalog/products/by-slugs/',
            {'slugs': self.product.slug},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['slug'], self.product.slug)

    def test_empty_slugs_returns_empty_list(self):
        resp = self.client.get('/api/v1/catalog/products/by-slugs/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_unexpected_query_error_propagates(self):
        request = RequestFactory().get(
            '/api/v1/catalog/products/by-slugs/',
            {'slugs': 'some-slug'},
        )
        with mock.patch(
            'apps.catalog.querysets.product_queryset.'
            'ProductQuerySet.with_related',
            side_effect=RuntimeError('db query failed'),
        ):
            resp = ProductBySlugsView.as_view()(request)
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(resp.data['error']['code'], 'server_error')
        self.assertNotIn('db query failed', str(resp.data))


# ==========================================================
# PRODUCT DETAIL
# ==========================================================

class ProductDetailAPITests(CatalogAPITestCase):

    def test_detail_by_uuid(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.uuid}/'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Galaxy S24')

    def test_detail_by_slug(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.slug}/'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Galaxy S24')

    def test_detail_not_found(self):
        resp = self.client.get(
            '/api/v1/catalog/products/00000000-0000-0000-0000-000000000000/'
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_has_variants_field(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.uuid}/'
        )
        self.assertIn('variants', resp.data)

    def test_detail_has_images_field(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.uuid}/'
        )
        self.assertIn('images', resp.data)

    def test_detail_has_tags_field(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.uuid}/'
        )
        self.assertIn('tags', resp.data)

    def test_detail_has_price_range(self):
        resp = self.client.get(
            f'/api/v1/catalog/products/{self.product.uuid}/'
        )
        self.assertIn('price_range', resp.data)

    def test_detail_increments_views(self):
        self.client.get(f'/api/v1/catalog/products/{self.product.uuid}/')
        self.product.refresh_from_db()
        self.assertEqual(self.product.views_count, 1)


# ==========================================================
# PRODUCT CREATE
# ==========================================================

class ProductCreateAPITests(CatalogAPITestCase):

    def test_create_as_staff(self):
        self.client.force_authenticate(self.staff_user)
        resp = self.client.post('/api/v1/catalog/products/create/', {
            'name': 'New Phone',
            'brand_id': self.brand.pk,
            'primary_category_id': self.leaf_cat.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'New Phone')

    def test_create_as_regular_user_forbidden(self):
        self.client.force_authenticate(self.regular_user)
        resp = self.client.post('/api/v1/catalog/products/create/', {
            'name': 'New Phone',
            'brand_id': self.brand.pk,
            'primary_category_id': self.leaf_cat.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_anonymous_forbidden(self):
        resp = self.client.post('/api/v1/catalog/products/create/', {
            'name': 'New Phone',
            'brand_id': self.brand.pk,
            'primary_category_id': self.leaf_cat.pk,
        }, format='json')
        self.assertIn(resp.status_code, [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ])

    def test_create_invalid_brand(self):
        self.client.force_authenticate(self.staff_user)
        resp = self.client.post('/api/v1/catalog/products/create/', {
            'name': 'New Phone',
            'brand_id': 99999,
            'primary_category_id': self.leaf_cat.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ==========================================================
# PRODUCT UPDATE
# ==========================================================

class ProductUpdateAPITests(CatalogAPITestCase):

    def test_update_name_as_staff(self):
        self.client.force_authenticate(self.staff_user)
        resp = self.client.patch(
            f'/api/v1/catalog/products/{self.product.uuid}/update/',
            {'name': 'Galaxy S24 Ultra'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Galaxy S24 Ultra')

    def test_update_as_regular_user_forbidden(self):
        self.client.force_authenticate(self.regular_user)
        resp = self.client.patch(
            f'/api/v1/catalog/products/{self.product.uuid}/update/',
            {'name': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ==========================================================
# CATEGORIES
# ==========================================================

class CategoryAPITests(CatalogAPITestCase):

    def test_category_tree(self):
        resp = self.client.get('/api/v1/catalog/categories/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)
        self.assertTrue(len(resp.data) > 0)

    def test_category_tree_has_children(self):
        resp = self.client.get('/api/v1/catalog/categories/')
        root = resp.data[0]
        self.assertIn('children', root)

    def test_category_detail(self):
        resp = self.client.get(
            f'/api/v1/catalog/categories/{self.leaf_cat.slug}/'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Смартфоны')

    def test_category_has_breadcrumbs(self):
        resp = self.client.get(
            f'/api/v1/catalog/categories/{self.leaf_cat.slug}/'
        )
        self.assertIn('breadcrumbs', resp.data)

    def test_category_not_found(self):
        resp = self.client.get('/api/v1/catalog/categories/nonexistent/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ==========================================================
# BRANDS
# ==========================================================

class BrandAPITests(CatalogAPITestCase):

    def test_brand_list(self):
        resp = self.client.get('/api/v1/catalog/brands/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(len(resp.data) > 0)

    def test_brand_list_excludes_inactive(self):
        Brand.objects.create(name='InactiveBrand', is_active=False)
        resp = self.client.get('/api/v1/catalog/brands/')
        names = [b['name'] for b in resp.data]
        self.assertNotIn('InactiveBrand', names)

    def test_brand_detail(self):
        resp = self.client.get(
            f'/api/v1/catalog/brands/{self.brand.slug}/'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Samsung')

    def test_brand_detail_not_found(self):
        resp = self.client.get('/api/v1/catalog/brands/nonexistent/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
