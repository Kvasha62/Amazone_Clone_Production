# ────────────────────────────────────────────────────────────
# Сериализаторы товаров (Product).
#
# ТРИ ТИПА СЕРИАЛИЗАТОРОВ:
#   1. OUTPUT (ModelSerializer) — для ответов API:
#      ProductListSerializer, ProductDetailSerializer
#   2. INPUT (Serializer) — для валидации входящих данных:
#      CreateProductInputSerializer, UpdateProductInputSerializer
#   3. QUERY (Serializer) — для валидации query-параметров:
#      ProductListQuerySerializer
#
# ПОЧЕМУ ОТДЕЛЬНЫЕ СЕРИАЛИЗАТОРЫ ДЛЯ LIST И DETAIL:
#   List — 50 товаров на странице → минимум полей → быстрый JSON.
#   Detail — 1 товар → все поля, варианты, изображения → полный ответ.
#   Без разделения: listing вернёт description (5KB текст) × 50 = 250KB мусора.
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Все product-related API endpoints перестанут работать.
# ────────────────────────────────────────────────────────────

# Decimal — для валидации денежных значений в query-параметрах.
# Почему не float: см. services/catalog_service.py — та же причина.
from decimal import Decimal

# serializers — модуль DRF с базовыми классами полей.
# ModelSerializer — автоматический сериализатор по модели (Meta.model).
# Serializer — ручной сериализатор (поля указаны явно).
from rest_framework import serializers

# ProductStatus — Enum-статусы для ChoiceField в input-сериализаторах.
from apps.catalog.constants import ProductStatus

# Модели для output-сериализаторов.
# ProductImage и ProductVariant — для вложенных сериализаторов.
from apps.catalog.models import Product, ProductImage, ProductVariant


# ==========================================================
# Вложенные сериализаторы (для использования внутри Product)
# ==========================================================

class ProductImageSerializer(serializers.ModelSerializer):
    """
    Изображение товара — только чтение.

    ПОЧЕМУ ТОЛЬКО ЧТЕНИЕ:
        Загрузка изображений — отдельный процесс (multipart form,
        S3 upload, etc). Этот сериализатор только для отображения.
        Без read_only_fields: API позволил бы менять is_main через JSON.
    """

    class Meta:
        # ProductImage — модель для автоматической генерации полей.
        model = ProductImage
        # fields — строгий белый список полей.
        # image — URL файла (Django автоматически конвертирует
        # ImageField в URL при сериализации).
        # alt — alt-текст для <img alt="..."> (SEO + accessibility).
        # is_main — флаг главного изображения.
        # order — порядок сортировки (1, 2, 3...).
        fields = ('id', 'image', 'alt', 'is_main', 'order')
        # read_only_fields = fields — ВСЕ поля только для чтения.
        # Это удобнее чем перечислять каждое поле дважды:
        # fields = (...) и read_only_fields = (...)
        read_only_fields = fields


class ProductVariantListSerializer(serializers.ModelSerializer):
    """
    Вариант товара для listing внутри Product.
    Минимальный набор для карточки.

    ПОЧЕМУ НЕ ВСЕ ПОЛЯ VARIANT:
        Внутри ProductDetailSerializer варианты — вложенный список.
        Если у товара 10 вариантов по 20 полей — JSON будет огромным.
        Минимальный набор: sku, slug, цена, сток, активность.

    АЛИГМЕНТ С ФРЕНДЕНДОМ (React):
        Frontend ProductVariant type ожидает:
        - id, sku, name, price, sale_price, effective_price
        - stock_quantity, is_active, weight, barcode, attributes
        Поэтому сериализатор обогащён этими полями.
    """

    # ── Цены из pricing-модуля (OneToOne Price) ──

    # source='price.price' — навигация по связям:
    #   variant.price (OneToOne FK к Price) → price_obj.price (DecimalField).
    # Двойной .price: первый — related_name, второй — поле модели.
    # allow_null=True — вариант может не иметь цены (новый, без прайса).
    # read_only=True — цена не меняется через этот сериализатор.
    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        source='price.price',
        allow_null=True,
        read_only=True,
    )

    # sale_price — цена со скидкой (null = без скидки)
    # source='price.sale_price' — variant.price.sale_price
    sale_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        source='price.sale_price',
        allow_null=True,
        read_only=True,
    )

    # effective_price — property на модели Price:
    # sale_price если есть, иначе price.
    # source='price.effective_price' — variant.price.effective_price
    effective_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        source='price.effective_price',
        allow_null=True,
        read_only=True,
    )

    # ── Сток из inventory-модуля (OneToOne Stock) ──

    # source='stock.quantity' — variant.stock.quantity
    stock_quantity = serializers.IntegerField(
        source='stock.quantity',
        allow_null=True,
        read_only=True,
    )

    # ── EAV-атрибуты (VariantAttribute → Attribute + AttributeValue) ──
    # attributes — dict вида {"Цвет": "Чёрный", "Память": "256GB"}
    # SerializerMethodField + get_attributes — собирает из EAV-связей.
    attributes = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        # Полный набор для frontend:
        # id — идентификатор варианта
        # sku — артикул для поиска
        # slug — для URL варианта
        # price — базовая цена (из pricing)
        # sale_price — цена со скидкой (из pricing)
        # effective_price — фактическая цена (из pricing)
        # stock_quantity — остаток (из inventory)
        # is_active — показывает доступность варианта
        # weight — вес для доставки
        # barcode — штрихкод для сканера
        # attributes — EAV dict для UI (Цвет, Размер и т.д.)
        fields = (
            'id',
            'sku',
            'slug',
            'price',
            'sale_price',
            'effective_price',
            'stock_quantity',
            'is_active',
            'weight',
            'barcode',
            'attributes',
        )
        read_only_fields = fields

    def get_attributes(self, obj):
        """
        Собирает EAV-атрибуты варианта в dict.
        {"Цвет": "Чёрный", "Память": "256GB"}

        prefetch_related('attributes__attribute', 'attributes__value')
        в CatalogService обеспечит отсутствие N+1.
        """
        result = {}
        # variant.attributes — reverse FK (related_name='attributes')
        # на VariantAttribute
        for va in obj.attributes.all():
            attr_name = va.attribute.name
            value_str = va.value.value
            result[attr_name] = value_str
        return result


# ==========================================================
# LISTING (список товаров)
# ==========================================================

class ProductListSerializer(serializers.ModelSerializer):
    """
    Товар для listing-страниц каталога.
    Минимальный набор — без variants, без description.

    ПОЧЕМУ НЕ ProductDetailSerializer ДЛЯ ВСЕГО:
        Listing = 50 товаров на странице.
        Detail-сериализатор тянет variants + images + tags × 50 =
        огромный JSON, медленная сериализация.
        List-сериализатор = только базовые поля + brand + category + 1 картинка.

    АЛИГМЕНТ С ФРЕНДЕНДОМ (React):
        Frontend ProductListItem type ожидает:
        - id (UUID), name, slug
        - brand_name, primary_category_name
        - min_price, max_price, main_image
        - rating, reviews_count, is_featured, status

        Публичный идентификатор товара — `id` типа UUID; внутреннее
        модельное поле uuid маппится в него через source и отдельным
        публичным полем НЕ отдаётся (F-8, #73).
        Backend поле main_image_url → frontend main_image.
        Backend category_name → frontend primary_category_name.
        Backend добавляет status для frontend.
    """

    # source='brand.name' — навигация через FK:
    # product.brand (FK к Brand) → brand_obj.name (CharField).
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    brand_slug = serializers.CharField(source='brand.slug', read_only=True)

    # primary_category_name — основная категория товара (для breadcrumbs)
    # source='primary_category.name' — product.primary_category.name
    primary_category_name = serializers.CharField(
        source='primary_category.name', read_only=True,
    )
    primary_category_slug = serializers.CharField(
        source='primary_category.slug', read_only=True,
    )

    # ImageField — автоматически возвращает URL изображения.
    # source='main_image.image' → product.main_image.image (ImageField).
    # allow_null=True — у товара может не быть главного изображения.
    # default=None — если main_image = None → null в JSON (не ошибка).
    main_image = serializers.ImageField(
        source='main_image.image',
        read_only=True,
        allow_null=True,
        default=None,
    )

    # price_range — property на модели Product:
    # «1 000 – 5 000 ₽» (строка, уже отформатированная).
    price_range = serializers.CharField(read_only=True)

    # status — текущий статус товара (enum value, не display name)
    # Frontend expects enum string: 'active', 'draft', etc.
    status = serializers.CharField(read_only=True)

    # F-8 (#73): единственный публичный идентификатор товара — `id`,
    # и его тип UUID (внутреннее модельное поле Product.uuid).
    # Целочисленный PK наружу не выходит.
    #
    # Второго публичного поля `uuid` быть не должно: два ключа с одним и
    # тем же значением — это два конкурирующих пространства
    # идентификаторов на одном ресурсе, ровно то, что запрещает frozen
    # contract. Модельное поле Product.uuid остаётся внутренним.
    id = serializers.UUIDField(source='uuid', read_only=True)

    class Meta:
        model = Product
        # Поля для listing-карточки товара:
        fields = (
            'id',
            'name',
            'slug',
            'brand_name',
            'brand_slug',
            'primary_category_name',
            'primary_category_slug',
            'main_image',
            'min_price',
            'max_price',
            'price_range',
            'rating',
            'reviews_count',
            'is_featured',
            'status',
            'published_at',
            'created_at',
        )
        read_only_fields = fields


# ==========================================================
# DETAIL (карточка товара)
# ==========================================================

class ProductDetailSerializer(serializers.ModelSerializer):
    """
    Полная карточка товара.

    АЛИГМЕНТ С ФРЕНДЕНДОМ (React):
        Frontend ProductDetail type ожидает:
        - id (UUID), name, slug, description
        - brand_name, primary_category_name
        - min_price, max_price, main_image
        - rating, reviews_count, is_featured, status
        - manufacturer_code, meta_title, meta_description
        - variants (ProductVariant[]), categories (Category[]), tags (Tag[])
        - created_at

        Теперь serializer включает manufacturer_code и categories
        как вложенные объекты (Category[]).
    """

    # F-8 (#73): `id` (UUID) — единственный публичный идентификатор;
    # отдельного публичного поля `uuid` нет (см. ProductListSerializer).
    id = serializers.UUIDField(source='uuid', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    brand_slug = serializers.CharField(source='brand.slug', read_only=True)
    # brand_logo — ImageField возвращает URL файла логотипа.
    brand_logo = serializers.ImageField(
        source='brand.logo', read_only=True, allow_null=True,
    )
    primary_category_name = serializers.CharField(
        source='primary_category.name', read_only=True,
    )
    primary_category_slug = serializers.CharField(
        source='primary_category.slug', read_only=True,
    )

    # main_image — URL главного изображения товара
    main_image = serializers.ImageField(
        source='main_image.image',
        read_only=True,
        allow_null=True,
        default=None,
    )

    # categories — вложенные объекты категорий (M2M)
    # Frontend expects [{id, name, slug, url_path, depth, image, is_active}]
    categories = serializers.SerializerMethodField()

    # many=True — у товара много изображений.
    images = ProductImageSerializer(many=True, read_only=True)
    # many=True — у товара много вариантов.
    variants = ProductVariantListSerializer(many=True, read_only=True)

    # tags — вложенные объекты тегов (M2M)
    # Frontend expects [{id, name, slug}] instead of slug strings
    tags = serializers.SerializerMethodField()

    price_range = serializers.CharField(read_only=True)
    # display_rating — property на модели: «4.5 / 5.0 ★»
    display_rating = serializers.CharField(read_only=True)
    # status — enum value string ('active', 'draft', etc.)
    status = serializers.CharField(read_only=True)
    # manufacturer_code — артикул производителя (для frontend)
    manufacturer_code = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        # Полный набор полей для карточки товара.
        fields = (
            'id',
            'name',
            'slug',
            'description',
            'status',
            'brand_name',
            'brand_slug',
            'brand_logo',
            'primary_category_name',
            'primary_category_slug',
            'main_image',
            'categories',
            'images',
            'variants',
            'tags',
            'min_price',
            'max_price',
            'price_range',
            'rating',
            'display_rating',
            'reviews_count',
            'views_count',
            'is_featured',
            'manufacturer_code',
            'published_at',
            'meta_title',
            'meta_description',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_categories(self, obj):
        """Возвращает список категорий товара как вложенные объекты."""
        from apps.catalog.serializers.category_serializers import CategoryTreeSerializer
        cats = obj.categories.filter(is_active=True)
        # Для каждой категории сериализуем как tree-узел
        return CategoryTreeSerializer(cats, many=True).data

    def get_tags(self, obj):
        """Возвращает список тегов товара как объекты [{id, name, slug}]."""
        from apps.catalog.serializers.tag_serializers import TagSerializer
        return TagSerializer(obj.tags.all(), many=True).data


# ==========================================================
# INPUT (валидация запросов)
# ==========================================================

class ProductListQuerySerializer(serializers.Serializer):
    """
    Валидация query-параметров listing'а:
        GET /api/v1/catalog/products/?category=phones&brand=nike&min_price=100

    ПОЧЕМУ НЕ ModelSerializer:
        Query-параметры — не модель. Это фильтры, а не поля товара.
        Serializer даёт полный контроль над валидацией.

    ПОЧЕМУ ВАЛИДАЦИЯ ВОБЩЕ НУЖНА:
        Без неё: ?min_price=abc → SQL-ошибка → 500.
        С ней: ?min_price=abc → «Введите число.» → 400.
    """

    # SlugField — валидирует формат slug (буквы, цифры, дефисы).
    # required=False — параметр необязателен (показать все).
    category = serializers.SlugField(required=False)
    brand = serializers.SlugField(required=False)
    tag = serializers.SlugField(required=False)
    # min_value=Decimal('0') — цена не может быть отрицательной.
    # max_digits=12 — до 999 999 999 999.99.
    min_price = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        required=False, min_value=Decimal('0'),
    )
    max_price = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        required=False, min_value=Decimal('0'),
    )
    # max_length=200 — защита от гигантских поисковых запросов.
    search = serializers.CharField(required=False, max_length=200)
    # ordering — строка сортировки. Валидируется в сервисе (whitelist).
    # default='-created_at' — если не передан → по умолчанию новые первыми.
    ordering = serializers.CharField(
        required=False,
        default='-created_at',
    )
    is_featured = serializers.BooleanField(required=False)
    status = serializers.CharField(required=False)
    # ``page``/``page_size`` are NOT declared here: they are documented and
    # validated by the shared API-05 pagination contract (apps/core/pagination),
    # so the OpenAPI schema does not declare them twice.


class CreateProductInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/catalog/products/.

    ПОЧЕМУ НЕ ModelSerializer:
        Input API отличается от модели:
        - category_ids вместо categories (M2M через id)
        - brand_id вместо brand (FK через id)
        - Нет slug (генерируется автоматически)
        - Нет UUID (генерируется автоматически)
    """

    name = serializers.CharField(max_length=255)
    brand_id = serializers.IntegerField(min_value=1)
    primary_category_id = serializers.IntegerField(min_value=1)
    description = serializers.CharField(required=False, default='')
    manufacturer_code = serializers.CharField(
        required=False, max_length=100, default='',
    )
    status = serializers.ChoiceField(
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
    )
    is_featured = serializers.BooleanField(required=False, default=False)
    category_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=[],
    )
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=[],
    )


class UpdateProductInputSerializer(serializers.Serializer):
    """
    Валидация тела PATCH /api/v1/catalog/products/{uuid}/.

    ОТЛИЧИЕ ОТ CreateProductInputSerializer:
        - Все поля required=False (PATCH = частичное обновление).
        - Нет default (None = не менять).
        - category_ids/tag_ids тоже optional (None = не менять, [] = очистить).
    """

    name = serializers.CharField(max_length=255, required=False)
    brand_id = serializers.IntegerField(min_value=1, required=False)
    primary_category_id = serializers.IntegerField(min_value=1, required=False)
    description = serializers.CharField(required=False)
    manufacturer_code = serializers.CharField(max_length=100, required=False)
    status = serializers.ChoiceField(
        choices=ProductStatus.choices, required=False,
    )
    is_featured = serializers.BooleanField(required=False)
    category_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )
