# API v1 Contract (API-01)

**Status:** Normative, pre-freeze draft of the *current* API v1 contract.
**Scope:** Every public `/api/v1/` endpoint served by this repository.
**Issue:** API-01 — Formalize API v1 contract; API-03 — Authentication & JWT
lifecycle contract (§3.4).
**Evidence base:** `config/urls.py`, `apps/*/urls.py`, `apps/*/api_views/**`,
`apps/*/serializers/**`, `apps/*/services/**`, `config/settings.py`
(commit base: `main` @ `45fa9b7`).

> **Reading rules.** Every statement in this document describes behaviour that
> exists in the code today, unless it is explicitly marked as one of:
>
> | Marker | Meaning |
> |---|---|
> | ✅ **CURRENT** | Verified behaviour of the current implementation. |
> | ⚠️ **GAP** | Known inconsistency / not standardized. Must be resolved by a follow-up API Freeze ticket. |
> | ❓ **DECISION REQUIRED** | Contract decision still owed before API v1 freeze. |
> | 🔁 **FOLLOW-UP** | Deliberately deferred to API-02…API-07. |
>
> API-01 is documentation-only: **no endpoint behaviour was changed.**

---

## 1. Purpose and status of API v1

API v1 is the application-level HTTP contract between the Django/DRF backend of
`Amazone_Clone_Production` and its clients (primarily the React frontend and the
payment provider webhook caller).

This document is the authoritative, human-readable contract. It exists because
the OpenAPI schema is *not yet* a guaranteed contract boundary (see §12), and
because several cross-context conventions (pagination, error shape, logout) are
still inconsistent (see §13).

API v1 is **not frozen**. The freeze gate is described in §14.

---

## 2. Base path and versioning

✅ **CURRENT**

* All public endpoints are served under the literal prefix `/api/v1/`.
* Versioning is **path-based and static**. DRF's versioning framework is *not*
  configured (`REST_FRAMEWORK` has no `DEFAULT_VERSIONING_CLASS`); the version is
  purely a URL prefix declared in `config/urls.py`.
* There is no content negotiation on version, no `Accept-Version` header, and no
  deprecation header mechanism.
* Renderers — the JSON-only rule applies to **API resource endpoints**, not to
  the schema/docs endpoints:
  * **API resource endpoints** (every route in §9.1–§9.14): JSON only. The
    global `DEFAULT_RENDERER_CLASSES = (JSONRenderer,)` is in force, the
    browsable API is disabled, and an `Accept` header that cannot be satisfied
    with `application/json` yields `406 Not Acceptable`
    (verified: `GET /api/v1/catalog/products/` with `Accept: text/html` → `406`).
  * **`GET /api/v1/schema/`** (§9.15) — an OpenAPI **schema** endpoint, not an
    API resource. `SpectacularAPIView` **overrides** the global renderers with
    its own set, so it serves OpenAPI schema media types:
    `application/vnd.oai.openapi` (YAML, the default),
    `application/yaml`, `application/vnd.oai.openapi+json` and
    `application/json`. Verified: `Accept: */*` → `200
    application/vnd.oai.openapi`; `Accept: application/json` → `200
    application/json`; `?format=json` → `200 application/vnd.oai.openapi+json`.
  * **`GET /api/v1/docs/`** (§9.15) — a human-facing **documentation** endpoint,
    not an API resource. `SpectacularSwaggerView` overrides the global renderers
    with `TemplateHTMLRenderer` and serves `text/html`. Verified:
    `Accept: */*` → `200 text/html; charset=utf-8`.
* The `406 Unsupported Accept` rule therefore describes API resource endpoints.
  Schema/docs endpoints negotiate against **their own** renderer sets and return
  `406` only when the request accepts none of *those* media types (verified:
  `GET /api/v1/schema/` with `Accept: text/csv` → `406`).
* Parsers: DRF defaults (JSON, form, multipart).
* Non-`/api/v1/` routes that also exist on the deployment: `/admin/` (Django
  admin, not part of this contract) and, in `DEBUG` only, `/media/<path>`.

Prefix map (`config/urls.py`):

| Prefix | Bounded context |
|---|---|
| `/api/v1/auth/…`, `/api/v1/users/…` | users |
| `/api/v1/catalog/…` | catalog |
| `/api/v1/cart/…` | cart |
| `/api/v1/orders/…` | orders |
| `/api/v1/inventory/…` | inventory |
| `/api/v1/pricing/…` | pricing |
| `/api/v1/payments/…` | payments |
| `/api/v1/reviews/…` | reviews |
| `/api/v1/discounts/…` | discounts |
| `/api/v1/shipping/…` | shipping |
| `/api/v1/wishlist/…` | wishlist |
| `/api/v1/notifications/…` | notifications |
| `/api/v1/analytics/…` | analytics |
| `/api/v1/health/` | core |
| `/api/v1/schema/`, `/api/v1/docs/` | drf-spectacular |

⚠️ **GAP — prefix asymmetry.** `apps.users.urls` and `apps.pricing.urls` are
mounted at bare `/api/v1/` and carry their own segment internally
(`auth/…`, `users/…`, `pricing/…`), while every other context is mounted at its
own prefix. This is invisible to clients but makes the routing tree harder to
verify mechanically. Cosmetic only — no client-visible change proposed here.

---

## 3. Global authentication / authorization conventions

### 3.1 Authentication mechanism ✅ **CURRENT**

* Scheme: **JWT bearer** via `rest_framework_simplejwt.authentication.JWTAuthentication`
  (the only entry in `DEFAULT_AUTHENTICATION_CLASSES`).
* Header: `Authorization: Bearer <access token>`.
* Login is by **email**, not username: `POST /api/v1/auth/login/` with
  `{"email", "password"}` (custom `EmailTokenObtainPairView`).
* Token lifetimes (`SIMPLE_JWT`): access **15 minutes**, refresh **7 days**.
* `ROTATE_REFRESH_TOKENS = True` and `BLACKLIST_AFTER_ROTATION = True`: each
  successful refresh returns a *new* refresh token and blacklists the old one.
* Logout: `POST /api/v1/auth/logout/` blacklists the supplied refresh token.
  The access token is **not** blacklisted (see §3.4).
* Sessions: Django sessions still exist and are used **only** for the guest cart
  (`session_key`) and for `/admin/`. API authentication itself is stateless.

### 3.2 Permission tiers ✅ **CURRENT**

| Tier | DRF class | Meaning |
|---|---|---|
| Public | `AllowAny` (or empty `permission_classes`) | No token required. |
| Authenticated | `IsAuthenticated` | Any valid, active user. |
| Staff/admin | `IsAdminUser` | `user.is_staff` is true. |
| Inline staff check | `IsAuthenticated` + `if not request.user.is_staff: 403` | Used by catalog product create/update. |

⚠️ **GAP — two ways of expressing "staff only".** Most staff endpoints use
`IsAdminUser` (→ `403` for a logged-in non-staff user, `401` for anonymous).
`ProductCreateView` / `ProductUpdateView` use `IsAuthenticated` plus a manual
`is_staff` check. Status remains `403`; the body is the canonical envelope
(`permission_denied`).

⚠️ **GAP — object-level permissions are enforced in views/services, not via
DRF `has_object_permission`.** There is no shared `IsOwner` permission class;
each context re-implements ownership. See §10.

### 3.3 Throttling ✅ **CURRENT**

* Global defaults: `AnonRateThrottle` `60/min` (env `THROTTLE_ANON`),
  `UserRateThrottle` `120/min` (env `THROTTLE_USER`).
* Per-view overrides: cart `30/min` anon / `120/min` user; orders `30/min` user;
  users profile & addresses `60/min` user.
* Throttling is disabled in the test configuration (rates set to `None`).
* Exceeding a rate yields `429 Too Many Requests` with the canonical envelope
  (`throttled`) and a `Retry-After` header.

### 3.4 API-03 authentication lifecycle ✅ **CURRENT / NORMATIVE**

API-03 freezes the authentication lifecycle independently of the error-body
contract (API-04) and the general authorization model. The contract explicitly
distinguishes:

1. **Authentication** — a caller proves identity with a JWT
   (`Authorization: Bearer <access_token>`); login is by **email**; Django
   sessions are not used for API authentication.
2. **Authorization/ownership** — authentication only establishes identity.
   Ownership (e.g. an address owned by another user → `404`) is enforced by the
   relevant views/services and remains outside this section.
3. **Refresh-token revocation** — the refresh token is the revocable
   capability. Successful refresh rotates it (`ROTATE_REFRESH_TOKENS=True`,
   `BLACKLIST_AFTER_ROTATION=True`); a rotated token is blacklisted. Logout
   blacklists the presented refresh token. A blacklisted refresh token cannot
   be reused to obtain a new access token.
4. **Access-token expiration** — access tokens are **not** blacklisted on
   logout. They remain valid until their 15-minute expiration, subject to the
   normal authentication checks (including active-account checks).
5. **Password change/reset effects** — password changes do **not** create a new
   authentication mechanism and do **not** individually blacklist existing
   JWTs (`SIMPLE_JWT["CHECK_REVOKE_TOKEN"]` is `False`). Existing access tokens
   remain valid until expiration and existing refresh tokens remain revocable
   only through rotation/logout/expiry. This is the current implementation and
   is frozen by API-03.
6. **Account deactivation effects** — `DELETE /api/v1/users/me/` sets
   `is_active=False`. Inactive users cannot log in, cannot obtain new tokens,
   and cannot refresh. Because SimpleJWT checks `is_active` during
   authentication (`SIMPLE_JWT["CHECK_USER_IS_ACTIVE"]` is `True`), an
   already-issued access token for a deactivated user is rejected on the next
   authenticated request. No separate access-token blacklist is introduced.

**Public authentication endpoints and required credential:**

| Endpoint                                    | Authentication                |
| ------------------------------------------- | ----------------------------- |
| `POST /api/v1/auth/register/`               | Anonymous                     |
| `POST /api/v1/auth/login/`                  | Anonymous                     |
| `POST /api/v1/auth/refresh/`                | Refresh token                 |
| `POST /api/v1/auth/logout/`                 | Authenticated + refresh token |
| `POST /api/v1/auth/change-password/`        | Authenticated                 |
| `POST /api/v1/auth/password-reset/`         | Anonymous                     |
| `POST /api/v1/auth/password-reset/confirm/` | Password-reset token          |

**Deterministic authentication failure statuses (current implementation).**
Error bodies use the canonical envelope (§5).

| Situation | Endpoint | Status |
|---|---|---|
| Missing access token | any `IsAuthenticated` endpoint, logout | `401` |
| Malformed/expired access token | any `IsAuthenticated` endpoint | `401` |
| Invalid login credentials | login | `401` |
| Missing/malformed login body | login | `400` |
| Inactive user | login / refresh / authenticated request | `401` |
| Malformed/expired refresh token | refresh / logout | `401` |
| Blacklisted refresh token | refresh | `401` |
| Already-blacklisted refresh token (repeat logout) | logout | `200` (idempotent) |
| Missing `refresh` field | logout | `400` |
| Refresh token not owned by caller | logout | `401` |

---

## 4. Global request/response conventions

✅ **CURRENT**

* Content type: `application/json` in and out (multipart accepted by DRF's
  default parsers but no documented endpoint requires it; images are set through
  the admin).
* Trailing slash is **mandatory** on every route (Django `APPEND_SLASH` will
  redirect `GET` without a slash, but non-idempotent methods will fail).
* Field naming: `snake_case` throughout.
* Input validation uses explicit `*InputSerializer` / `*QuerySerializer`
  classes with `is_valid(raise_exception=True)`; models are never bound
  directly to request bodies.
* Output uses explicit `*Serializer` / `*ListSerializer` classes;
  `read_only_fields = fields` is the norm on output model serializers.
* Language: user-facing `detail` messages are **Russian free text**. They are
  not stable identifiers.

✅ **CURRENT (API-04)** — machine-readable `error.code` values are defined in §5.
User-facing `error.message` / `details[].message` remain Russian (or DRF English
for built-in validators) free text and are **not** stable identifiers.

---

## 5. Global error conventions (API-04) ✅ **CURRENT / NORMATIVE**

Public `/api/v1/` **resource** endpoints (every route in §9.1–§9.13, plus the
payment webhook error path in §11.3) use one JSON error envelope. The handler
is `apps.core.api_errors.exception_handler`, configured as DRF
`EXCEPTION_HANDLER`. HTTP **status** semantics from API-03 and from the rest of
this document are unchanged; only the **body** is normalized.

### 5.1 Canonical envelope

Content-Type: `application/json`.

```json
{
  "error": {
    "code": "validation_error",
    "message": "Запрос содержит некорректные данные.",
    "details": [
      {"field": "email", "code": "invalid", "message": "Enter a valid email address."}
    ]
  },
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Required | Type | Semantics |
|---|---|---|---|
| `error` | yes | object | Envelope wrapper. |
| `error.code` | yes | string | Machine-readable class of failure (see §5.2). Clients MUST branch on HTTP status + this code, never on `message` text. |
| `error.message` | yes | string | Human-readable summary (Russian free text; **not** a stable identifier). |
| `error.details` | yes | array | Always a list (possibly empty). Each item is `{field, code, message}`. `field` is a dotted/indexed path (`items[0].quantity`) or `null` for non-field errors. |
| `request_id` | optional | string | Present when request-correlation middleware has a current id (`X-Request-ID`). Safe identifier only. |

`details` is **always a list**. Clients must not assume a mapping of field →
string or field → list at the top level.

The envelope never contains exception class names, Python tracebacks, SQL,
database errors, JWTs, passwords, reset tokens, webhook secrets, or other
internals.

### 5.2 HTTP status and `error.code` mapping

| HTTP | `error.code` | When |
|---|---|---|
| `400` | `validation_error` | Serializer / form / request validation; domain `ValidationError` (including business-rule conflicts). Malformed JSON → `parse_error`. |
| `401` | `not_authenticated` | Missing credentials on an `IsAuthenticated` endpoint. |
| `401` | `authentication_failed` | Invalid/expired/malformed JWT, bad login, inactive account, blacklisted/unowned refresh (API-03 statuses preserved). |
| `403` | `permission_denied` | Authenticated caller lacks permission (staff checks, `IsAdminUser`, webhook HMAC failure). |
| `404` | `not_found` | Missing resource **and** intentional ownership/IDOR hiding (§10). Status remains `404`, never `403`. |
| `405` | `method_not_allowed` | Matched route, unsupported method (e.g. `PUT /api/v1/cart/`). |
| `406` | `not_acceptable` | Unsatisfiable `Accept` on a JSON-only resource endpoint. |
| `429` | `throttled` | Rate-limit exceeded (DRF throttles; `Retry-After` retained). Disabled in tests. |
| `500` | `server_error` | Unexpected exception at the API boundary. Stable generic message; traceback only in server logs. |
| `502` | `bad_gateway` | Payment webhook transient order-confirm failure (§11.3) — existing provider-retry signal. |

**Not used (and not invented by API-04):**

* `409 Conflict` — business conflicts remain `400` / `validation_error`.
* `422 Unprocessable Entity` — unused.
* `415 Unsupported Media Type` — not part of the documented client contract.

### 5.3 Validation representation

Serializer and service `ValidationError`s flatten into `error.details`. Nested
list/object errors use indexed paths. Duplicate email/username on register,
wrong `old_password`, missing `order_id` on coupon remove, and catalog query
`min_price=abc` all use this shape with HTTP `400`.

`POST /api/v1/reviews/` without `product_id`/`product_uuid` is a **validation**
failure → `400` (G-29 closed). Unknown product remains `404`.

### 5.4 Authentication / authorization

API-03 status table in §3.4 is unchanged. Bodies are the canonical envelope.
Permission denials (including catalog product create/update non-staff) use
`403` + `permission_denied`. Ownership failures stay `404` + `not_found`.

### 5.5 Unexpected failures

Unhandled exceptions are logged (`api_unhandled_exception`, with traceback on
the server) and returned as `500` + `server_error` with empty `details` and no
internal text. They are not converted into `400`.

### 5.6 Exceptions to the envelope

* **`GET /api/v1/health/`** (§9.14) is a plain Django `View`. `200`/`503`
  bodies stay `{status, version, database}` — not the error envelope.
* **`GET /api/v1/schema/`** and **`GET /api/v1/docs/`** (§9.15) are schema/docs
  endpoints, not API resources.
* **Payment webhook success-path `200`** payloads (`PaymentSerializer`, or
  `{detail: "Платёж не найден, webhook logged."}` / completed-order notice)
  are **not** errors. Webhook **failures** (`403` HMAC, `400` validation,
  `502` retry) use the envelope.

---

## 6. Pagination conventions and current exceptions

### 6.1 Configured default ✅ **CURRENT**

`REST_FRAMEWORK["DEFAULT_PAGINATION_CLASS"] = PageNumberPagination`,
`PAGE_SIZE = 20`. **However**, every endpoint in this API is a plain `APIView`
(no `ListAPIView`, no `ViewSet`), so the default pagination class is **never
applied automatically**. Pagination only happens where a view opts in.

### 6.2 Classification of every collection endpoint

**A. Paginated — DRF `PageNumberPagination` shape**

| Endpoint | Shape |
|---|---|
| `GET /api/v1/catalog/products/` | `{"count": int, "next": url\|null, "previous": url\|null, "results": [...]}` — page size 20, `?page=`; `page_size` is accepted by the query serializer but **ignored** by the paginator (`PAGE_SIZE_QUERY_PARAM` is unset). ⚠️ **GAP** |

**B. Paginated — bespoke shape (reviews only)**

| Endpoint | Shape |
|---|---|
| `GET /api/v1/reviews/` (`ordering` ≠ `helpful`) | `{"count", "page", "page_size", "total_pages", "results"}` |
| `GET /api/v1/reviews/` (`ordering=helpful`) | `{"count", "page", "page_size", "results"}` — **no `total_pages`**, and sorting is done in Python over the fully materialised queryset. ⚠️ **GAP** (shape drift *within one endpoint* + unbounded memory) |

**C. Intentionally non-paginated (bare JSON array)** — small, bounded
collections; documented as a deliberate decision:

`GET /api/v1/catalog/categories/` (tree; pagination would break the hierarchy),
`GET /api/v1/catalog/brands/`,
`GET /api/v1/catalog/products/by-slugs/` (hard cap of 20 slugs),
`GET /api/v1/users/addresses/` (per-user cap),
`GET /api/v1/shipping/methods/`.

**D. Non-paginated and potentially unbounded** ⚠️ **GAP** — these return a bare
JSON array (or an embedded array) whose size grows with the dataset:

`GET /api/v1/orders/`, `GET /api/v1/payments/`,
`GET /api/v1/inventory/`, `GET /api/v1/inventory/{variant_id}/movements/`,
`GET /api/v1/pricing/variants/{variant_id}/history/`,
`GET /api/v1/discounts/coupons/`,
`GET /api/v1/shipping/shipments/`,
`GET /api/v1/notifications/`, `GET /api/v1/notifications/unread/`,
`GET /api/v1/wishlist/` (items embedded in the object),
`GET /api/v1/cart/` (items embedded in the object).

**No pagination behaviour is normalized by API-01.**
🔁 **FOLLOW-UP: API-05 (collection/pagination contract).**

❓ **DECISION REQUIRED (API-05):** one envelope for all collections
(`count`/`next`/`previous`/`results` vs `count`/`page`/`page_size`/`total_pages`),
whether embedded child collections (cart items, wishlist items, payment events,
product variants/images) are exempt, and whether `page_size` becomes a
first-class query parameter.

---

## 7. Identifier conventions

✅ **CURRENT** — identifier semantics differ per resource and are load-bearing:

| Resource | Path identifier | Format / converter | Notes |
|---|---|---|---|
| User (self) | none | — | Always `me`. |
| Address | `address_id` | `<int>` (BigAutoField PK) | Owner-scoped. |
| Product (read) | `identifier` | `<str>` — **UUID or slug** | The view tries `uuid.UUID(identifier)`; on `ValueError` it falls back to slug lookup. |
| Product (update) | `uuid` | `<uuid>` converter | UUID only; a non-UUID never matches the route → `404`. |
| Product (payload / listing output) | `id` | **UUID** | `ProductListSerializer.id` / `ProductDetailSerializer.id` both serialize the product **UUID**, not the integer PK. ⚠️ **GAP — `id` is a UUID here but an integer everywhere else.** |
| Product (reviews filter / create) | `product_id` or `product_uuid` | int PK *or* UUID | Two parallel identifier spaces on one resource. ⚠️ **GAP** |
| Category / Brand / Tag | `slug` | `<slug>` converter (`[-a-zA-Z0-9_]+`) | SEO identifiers. |
| Product variant | `variant_id` | `<int>` PK | Used by cart, wishlist, inventory, pricing. |
| Cart | none | — | Resolved from JWT user or session key. |
| Cart item | `item_id` | `<int>` PK | Ownership checked in service. |
| Order | `order_number` | `<str>`, format `ORD-` + zero-padded sequence (e.g. `ORD-000001`) | Public identifier; internal PK is never in the URL. |
| Order (in payloads) | `order_id` | `<int>` PK | ⚠️ **GAP** — `POST /payments/`, `POST /discounts/apply|remove/`, `POST /shipping/shipments/create/` take the **integer PK**, while every order URL uses `order_number`. |
| Payment | `payment_number` | `<str>`, format `PAY-` + zero-padded sequence (e.g. `PAY-000001`) | ⚠️ Stored in the model field literally named `order_number`; the URL kwarg is `payment_number`. Confusing but client-invisible. |
| Payment (external) | `external_id` | provider string, unique | Webhook correlation key. |
| Shipment | `pk` | `<int>` PK | ⚠️ **GAP** — the only order-adjacent resource still exposing a raw PK. |
| Shipment tracking | `tracking` | `<str>` — external carrier number **or** internal `SHP-00000001` | Public lookup matches either field. |
| Review | `review_id` | `<int>` PK | |
| Notification | `pk` | `<int>` PK | |
| Wishlist item | `item_id` | `<int>` PK | |
| Coupon | `code` | `<str>` in request body | Never in a path. |

❓ **DECISION REQUIRED:** whether `id` in catalog product payloads keeps meaning
"UUID" at freeze, and whether cross-context references to orders standardize on
`order_number`.

---

## 8. Money / Decimal and timestamp conventions

### 8.1 Money ✅ **CURRENT**

* Monetary values are `DecimalField`s on the models and serializers.
* DRF's `COERCE_DECIMAL_TO_STRING` is **not overridden**, so it is `True`:
  **money is serialized as a JSON string**, e.g. `"1500.00"`.
* Scale is 2 decimal places; typical precision is `max_digits=12`.
* Requests may send money as a JSON string or number; DRF coerces both.
* Some endpoints stringify money manually (`str(order.total)` in
  `/discounts/apply/` and `/discounts/remove/`, `revenue`/`total_spent` in
  analytics `Top*` serializers are declared as `CharField`). The wire result is
  the same string form; the schema type differs. ⚠️ **GAP (schema-level only).**
* Currency: `Price.currency` is a `ChoiceField` exposed on `PriceSerializer`.
  Order/payment/shipping amounts carry **no currency field** — a single implicit
  currency is assumed. ❓ **DECISION REQUIRED** before freeze.
* Server-authoritative amounts: order `delivery_cost`, `discount` and `total`
  are computed server-side; a client-supplied `delivery_cost` on order creation
  is rejected (`400`).

### 8.2 Timestamps ✅ **CURRENT**

* `USE_TZ = True`; all datetimes are stored and emitted in **UTC**.
* Serialization uses DRF's default ISO-8601 (`DATETIME_FORMAT` is not
  overridden), e.g. `"2026-09-04T12:34:56.789012Z"`. Microseconds are present
  when non-zero; the `Z` suffix denotes UTC.
* Date-only fields (`date_of_birth`) are ISO `YYYY-MM-DD`.
* Nullable timestamps (`paid_at`, `cancelled_at`, `shipped_at`, `delivered_at`,
  `confirmed_at`, `refunded_at`, `read_at`, `sent_at`, `published_at`) are
  `null` until the corresponding transition occurs.
* `?days=N` analytics windows are computed relative to server "now" in UTC.

### 8.3 Nullable / empty semantics ✅ **CURRENT**

* Optional text fields default to `""` (empty string), not `null`
  (`notes`, `note`, `reason`, `title`, `description`, `tracking_number`).
* Optional FK/date fields are `null`.
* Image fields serialize to a URL string or `null`.
* Empty collections are `[]` (bare-array endpoints) or `{"count": 0, …,
  "results": []}` (paginated endpoints) — never `null`, never `204`.

---

## 9. Endpoint inventory

Legend: **Auth** = Public / Auth / Staff; **Refresh token** and
**Password-reset token** are credential-specific authentication tiers used by
the authentication endpoints (§3.4). All paths are absolute and require the
trailing slash. Unless noted, `401` applies to Auth/Staff endpoints when the
token is absent/invalid, `403` when the caller is authenticated but not
permitted, and `429` when throttled.

### 9.1 Users & authentication (`apps.users`)

| # | Method | Path | Auth |
|---|---|---|---|
| 1 | POST | `/api/v1/auth/register/` | Public |
| 2 | POST | `/api/v1/auth/login/` | Public |
| 3 | POST | `/api/v1/auth/refresh/` | Refresh token |
| 4 | POST | `/api/v1/auth/logout/` | Auth + refresh token |
| 5 | POST | `/api/v1/auth/change-password/` | Auth |
| 6 | POST | `/api/v1/auth/password-reset/` | Public |
| 7 | POST | `/api/v1/auth/password-reset/confirm/` | Password-reset token |
| 8 | GET/PATCH/DELETE | `/api/v1/users/me/` | Auth |
| 9 | GET/POST | `/api/v1/users/addresses/` | Auth |
| 10 | GET/PATCH/DELETE | `/api/v1/users/addresses/{address_id}/` | Auth |
| 11 | POST | `/api/v1/users/addresses/{address_id}/default/` | Auth |

**1. `POST /auth/register/`** — Public.
Body (the **inline** `RegisterInputSerializer` declared in
`apps/users/api_views/auth_views.py`): `email` (email, required), `username`
(≤150, required), `password` (≥8, write-only), `password_confirm` (≥8),
`first_name`, `last_name` (optional, default `""`).
⚠️ **GAP** — a *second*, different `RegisterInputSerializer` exists in
`apps/users/serializers/user_serializers.py` and additionally declares `phone`.
The view uses the inline one, so **`phone` is not accepted at registration**;
the exported serializer is dead code that misrepresents the contract.
`201` → `{"id": int, "email", "username", "first_name", "last_name"}`.
`400`: canonical validation envelope (§5); mismatched passwords → details on
`password_confirm`; duplicate email/username → details on `email` / `username`.
Email uniqueness is case-insensitive (`email__iexact`). Not idempotent.

**2. `POST /auth/login/`** — Public. Body `{"email", "password"}`.
`200` → `{"access": "<jwt>", "refresh": "<jwt>"}`. `401` on bad credentials or
inactive user (canonical envelope, `authentication_failed`).
Authentication classes are empty on this view (`permission_classes = []` in the
resolved route), so an existing token is ignored.

**3. `POST /auth/refresh/`** — Refresh token (SimpleJWT `TokenRefreshView`).
Body `{"refresh"}`. `200` → `{"access", "refresh"}` (rotation is on, so a new
refresh token is issued and the old one is blacklisted). `401` on an expired,
malformed or blacklisted token. **Not idempotent** — replaying the same refresh
token after rotation fails with `401`.

**4. `POST /auth/logout/`** — Auth + refresh token.
Body `{"refresh": "<refresh_token>"}`.
`200` → `{"detail": "Выполнен выход."}`. The supplied refresh token is
blacklisted; subsequent refresh with that token fails. The access token is
**not** blacklisted and remains valid until its 15-minute expiration.
The client is responsible for discarding local tokens.
Repeat logout with an already-blacklisted refresh token returns `200`
(idempotent). `401` on a malformed/expired refresh token, a refresh token not
owned by the caller, or a missing/invalid access token. Missing `refresh` in
the body → `400`.

**5. `POST /auth/change-password/`** — Auth.
Body `old_password`, `new_password` (≥8), `new_password_confirm`.
`200` → `{"detail": "Пароль успешно изменён."}`.
`400` on mismatch or wrong current password (canonical envelope, field
`old_password` / `new_password_confirm`).
✅ **CURRENT (API-03)** — existing access and refresh tokens are **not**
individually invalidated by a password change. They remain valid until
expiration / rotation / logout / explicit refresh-token expiry. A new password
becomes usable for the next login.

**6. `POST /auth/password-reset/`** — Public. Body `{"email"}`.
Always `200` → `{"detail": "Если email существует, письмо отправлено."}`
(deliberate account-enumeration protection). Email delivery is dispatched via
Celery, falling back to synchronous `send_mail` on broker/import failure.
Idempotent from the client's perspective; each call mints a new token.

**7. `POST /auth/password-reset/confirm/`** — Password-reset token.
Body `uid` (urlsafe-base64 of the user PK), `token` (Django default token
generator), `new_password` (8–128), `new_password_confirm`.
`200` → `{"detail": "Пароль успешно изменён."}`.
`400` canonical envelope (invalid uid / expired token). Token validity is
governed by `PASSWORD_RESET_TIMEOUT`. Single-use in effect: the token stops
validating once the password hash changes.
✅ **CURRENT (API-03)** — existing access and refresh tokens are **not**
individually invalidated by a password reset, matching the password-change
lifecycle.

**8. `/users/me/`** — Auth.
`GET` `200` → `UserDetailSerializer`: `id`, `email`, `username`, `first_name`,
`last_name`, `full_name`, `phone`, `is_active`, `date_joined`,
`profile{avatar, date_of_birth, gender, timezone, language, email_subscribed}`.
`PATCH` body (`UpdateProfileInputSerializer`, all optional): `first_name`,
`last_name`, `phone`, `date_of_birth`, `gender` (choice), `timezone`,
`language`, `email_subscribed` → `200` with the same payload. Note: the input
serializer is instantiated **without** `partial=True`, but every field is
declared `required=False`, so partial updates work.
`DELETE` → `200` `{"detail": "Аккаунт деактивирован."}` — **soft delete**
(`is_active=False`), not `204`, and the record is retained. ⚠️ **GAP** —
`DELETE` returning `200` with a body deviates from the `204` convention used by
address/review/wishlist deletes.

**9. `/users/addresses/`** — Auth.
`GET` `200` → **bare array** of `AddressOutputSerializer`
(`id`, `recipient_name`, `country`, `region`, `city`, `street`, `postal_code`,
`notes`, `is_default`, `created_at`, `updated_at`), default address first, then
by date. Not paginated (§6 class C).
`POST` body `AddressInputSerializer` (`recipient_name`, `country`, `region`,
`city`, `street`, `postal_code`, `notes`, `is_default`) → `201` with the created
address. `400` when the per-user address limit is exceeded.

**10. `/users/addresses/{address_id}/`** — Auth, `address_id` int.
`GET` `200`; `PATCH` (partial) `200`; `DELETE` `204` (no body).
**Ownership:** lookups are scoped by `user=request.user`; another user's address
yields `404 {"detail": "Адрес не найден."}` — never `403` (§10).

**11. `POST /users/addresses/{address_id}/default/`** — Auth.
`200` → the address, now `is_default=true`; all other addresses of the user are
demoted. **Idempotent**: repeating the call is a no-op returning `200`.
`404` if not owned.

### 9.2 Catalog (`apps.catalog`)

| # | Method | Path | Auth |
|---|---|---|---|
| 12 | GET | `/api/v1/catalog/products/` | Public |
| 13 | GET | `/api/v1/catalog/products/by-slugs/` | Public |
| 14 | POST | `/api/v1/catalog/products/create/` | Staff (inline check) |
| 15 | GET | `/api/v1/catalog/products/{identifier}/` | Public |
| 16 | PATCH | `/api/v1/catalog/products/{uuid}/update/` | Staff (inline check) |
| 17 | GET | `/api/v1/catalog/categories/` | Public |
| 18 | GET | `/api/v1/catalog/categories/{slug}/` | Public |
| 19 | GET | `/api/v1/catalog/brands/` | Public |
| 20 | GET | `/api/v1/catalog/brands/{slug}/` | Public |

**12. `GET /catalog/products/`** — Public. **Paginated (DRF shape, §6 class A).**
Query parameters (validated by `ProductListQuerySerializer`; unknown parameters
are ignored):

| Param | Type | Semantics |
|---|---|---|
| `category` | slug | Filter by active category (incl. descendants). Unknown slug → `404`. |
| `brand` | slug | Filter by active brand. Unknown slug → `404`. |
| `tag` | slug | Filter by active tag. Unknown slug → `404`. |
| `min_price` / `max_price` | decimal ≥ 0, 2 dp | Denormalized price-range filter. |
| `search` | string ≤ 200 | Full-text search (PostgreSQL `SearchVector`). |
| `ordering` | string | Whitelist: `created_at`, `-created_at`, `min_price`, `-min_price`, `rating`, `-rating`, `views_count`, `-views_count`, `name`, `-name`. **Any other value silently falls back to `-created_at`** ⚠️ **GAP** (invalid input is not rejected). Default `-created_at`. |
| `page` | int ≥ 1 | Page number. Out-of-range → `404`. |
| `page_size` | int 1–100 | **Accepted and validated but ignored** — page size is fixed at 20. ⚠️ **GAP**. |
| `is_featured`, `status` | bool / string | Declared on the query serializer but **not passed to the service** — currently inert. ⚠️ **GAP**. |

`200` → `{"count", "next", "previous", "results": [ProductListSerializer]}`.
`ProductListSerializer`: `id` (**UUID**), `name`, `slug`, `brand_name`,
`brand_slug`, `primary_category_name`, `primary_category_slug`, `main_image`
(URL|null), `min_price`/`max_price` (money strings|null), `price_range` (string),
`rating` (decimal string), `reviews_count`, `is_featured`, `status`,
`published_at`, `created_at`.
`400` on malformed query parameters (e.g. `min_price=abc`).
⚠️ **GAP:** the service computes `applied_filters` but the view discards it —
the response carries no echo of the applied filters.

**13. `GET /catalog/products/by-slugs/?slugs=a,b,c`** — Public.
Comma-separated slugs, **hard-capped at the first 20**; extra values are
silently dropped ⚠️ **GAP** (silent truncation, no `400`). Missing/empty
`slugs` → `200 []`. `200` → **bare array** of `ProductListSerializer`, ordered
by the queryset, **not** by the order of the requested slugs. Unknown slugs are
omitted silently.

**14. `POST /catalog/products/create/`** — Staff.
Body `CreateProductInputSerializer`: `name` (≤255), `brand_id` (int ≥ 1),
`primary_category_id` (int ≥ 1), `description`, `manufacturer_code`, `status`
(choice), `is_featured`, `category_ids` (list[int]), `tag_ids` (list[int]).
`201` → `ProductDetailSerializer`. `403` canonical `permission_denied` for
non-staff. `400` on validation; `404` if a referenced brand or
category does not exist. Slug and UUID are generated server-side. Not idempotent.

**15. `GET /catalog/products/{identifier}/`** — Public. `identifier` is a UUID
**or** a slug (§7). `200` → `ProductDetailSerializer`: `id` (**UUID**), `uuid`,
`name`, `slug`, `description`, `status`, brand/category denormalized fields,
`main_image`, `categories`, `images[]`, `variants[]`, `tags`, `min_price`,
`max_price`, `price_range`, `rating`, `display_rating`, `reviews_count`,
`views_count`, `is_featured`, `manufacturer_code`, `published_at`,
`meta_title`, `meta_description`, `created_at`, `updated_at`.
`404` if not found or not visible.
**Side effect:** every successful `GET` increments `views_count` — this
"read" endpoint is **not** side-effect free. ❓ **DECISION REQUIRED**
(document as intended, or move view tracking to an explicit event endpoint).

**16. `PATCH /catalog/products/{uuid}/update/`** — Staff. Body
`UpdateProductInputSerializer` (same fields as create, all optional).
`200` → `ProductDetailSerializer`. `403` non-staff (custom body), `404` unknown
UUID, `400` validation. A non-UUID path segment does not match the route → `404`.

**17. `GET /catalog/categories/`** — Public. `200` → **bare array**, recursive
tree: `id`, `name`, `slug`, `url_path`, `depth`, `is_active`, `children[]`.
Intentionally non-paginated (§6 class C).

**18. `GET /catalog/categories/{slug}/`** — Public. `200` → `id`, `name`, `slug`,
`description`, `image`, `url_path`, `full_name_cached`, `depth`, `is_active`,
`breadcrumbs[{name, slug, url_path}]`, `products_count`, `meta_title`,
`meta_description`. `404` unknown/inactive slug.

**19. `GET /catalog/brands/`** — Public. `200` → **bare array** of
`{id, name, slug, logo}` for active brands. Intentionally non-paginated.

**20. `GET /catalog/brands/{slug}/`** — Public. `200` →
`{id, name, slug, description, logo, products_count}`. `404` unknown slug.

### 9.3 Cart (`apps.cart`)

| # | Method | Path | Auth |
|---|---|---|---|
| 21 | GET | `/api/v1/cart/` | Public (guest or user) |
| 22 | DELETE | `/api/v1/cart/` | Public |
| 23 | POST | `/api/v1/cart/items/` | Public |
| 24 | PATCH | `/api/v1/cart/items/{item_id}/` | Public |
| 25 | DELETE | `/api/v1/cart/items/{item_id}/` | Public |
| 26 | POST | `/api/v1/cart/merge/` | Auth |

Cart resolution ✅ **CURRENT**: with a JWT the cart is the user's active cart;
without one it is the cart bound to the Django `session_key` (created on
demand). Both are created lazily, so the cart endpoints never `404` on "no cart".

`CartSerializer` payload: `{"id": int, "items": [CartItemSerializer],
"total": "0.00", "total_quantity": int}`; `CartItemSerializer`:
`{id, product_name, sku, price, quantity, total_price}` (money as strings).
Cart items are embedded and **never paginated**.

**21. `GET /cart/`** → `200` cart (empty cart → `items: []`, `total: "0.00"`).
**22. `DELETE /cart/`** → **`200` with the emptied cart body** (not `204`)
⚠️ **GAP** — inconsistent with other `DELETE`s. Idempotent.
**23. `POST /cart/items/`** — body `{"variant_id": int ≥ 1, "quantity": int}`.
`201` → **the whole cart** (not the created item). Adding an existing variant
**increments** the quantity, so repeated identical calls are *cumulative*, not
idempotent. `400` on validation or insufficient stock; `404` unknown variant.
**24. `PATCH /cart/items/{item_id}/`** — body `{"quantity": int}`. `200` → whole
cart. Setting quantity to 0 removes the line (service behaviour). `404` if the
item does not belong to the caller's cart (ownership enforced in
`CartService`, §10). `400` on stock violation.
**25. `DELETE /cart/items/{item_id}/`** → **`200` with the whole cart** (not
`204`) ⚠️ **GAP**. `404` if not owned.
**26. `POST /cart/merge/`** — Auth, empty body. Merges the guest cart identified
by the current `session_key` into the user's cart.
`200` → merged cart; `400` when the request carries no session; `404` when the
guest cart is missing (canonical envelope).
Required because JWT login does not fire `user_logged_in`. Effectively
idempotent (a second call finds no guest cart → `404`).
⚠️ **GAP:** merge depends on a cookie-backed session travelling alongside a
stateless JWT — a cross-mechanism coupling outside API-03 (separate auth/cookie
follow-up, see G-10).

### 9.4 Orders (`apps.orders`)

| # | Method | Path | Auth |
|---|---|---|---|
| 27 | GET | `/api/v1/orders/` | Auth |
| 28 | POST | `/api/v1/orders/` | Auth |
| 29 | GET | `/api/v1/orders/{order_number}/` | Auth (owner or staff) |
| 30 | PATCH | `/api/v1/orders/{order_number}/status/` | Staff |
| 31 | POST | `/api/v1/orders/{order_number}/cancel/` | Auth (owner or staff) |

**27. `GET /orders/`** → `200` **bare array** of `OrderListSerializer`
(`id`, `order_number`, `status`, `status_display`, `total`, `items_count`,
`created_at`), scoped to `request.user`. Not paginated ⚠️ **GAP** (§6 class D).
Staff see only their own orders here — there is no admin order list endpoint.
⚠️ **GAP / ❓ DECISION REQUIRED.**

**28. `POST /orders/`** — body `{"notes": str}` (optional).
`201` → `OrderSerializer`: `id`, `order_number`, `status`, `status_display`,
`is_terminal`, `items[]`, `subtotal`, `delivery_cost`, `discount`, `total`,
`recipient_name`, `full_address`, `notes`, `cancellation_reason`,
`cancelled_at`, `confirmed_at`, `delivered_at`, `created_at`.
Failure modes (all from the service): empty cart → `400`; no default address →
`400`; total below `MIN_ORDER_TOTAL` → `400`; insufficient stock → `400`;
`delivery_cost` supplied in the body → `400` (server-authoritative pricing).
**Not idempotent** — no idempotency key; a retried POST creates a second order.
⚠️ **GAP / ❓ DECISION REQUIRED (API-07).**

**29. `GET /orders/{order_number}/`** → `200` `OrderSerializer`.
**Ownership:** a non-staff caller requesting someone else's order receives
`404 {"detail": "Заказ не найден."}`, deliberately identical to the
"does not exist" response (§10).

**30. `PATCH /orders/{order_number}/status/`** — Staff. Body
`{"status": <OrderStatus choice>}`. `200` → full `OrderSerializer`.
Transitions are validated by the order FSM; an illegal transition → `400`.
`status=cancelled` is routed through `OrderService.cancel()` (the single
cancellation path, coordinating coupon release, inventory and payment).
`404` for an unknown order number (no ownership masking — the caller is staff).
Idempotent only insofar as the FSM allows the same target state.

**31. `POST /orders/{order_number}/cancel/`** — Auth. Body `{"reason": str}`
(optional). `200` → `OrderSerializer`. Allowed for the owner while the order is
not terminal, and always for staff. Terminal order → `400`. Non-owner → `404`.
Second cancel of an already-cancelled order → `400`.

### 9.5 Inventory (`apps.inventory`) — Staff only

| # | Method | Path |
|---|---|---|
| 32 | GET | `/api/v1/inventory/` |
| 33 | GET | `/api/v1/inventory/{variant_id}/` |
| 34 | POST | `/api/v1/inventory/{variant_id}/restock/` |
| 35 | POST | `/api/v1/inventory/{variant_id}/adjust/` |
| 36 | GET | `/api/v1/inventory/{variant_id}/movements/` |

All require `IsAdminUser` (`401` anonymous, `403` authenticated non-staff).

**32.** `200` → **bare array** of `StockSerializer`
(`id`, `variant_id`, `sku`, `product_name`, `quantity`, `reserved_quantity`,
`available_quantity`, `is_low_stock`, `is_out_of_stock`,
`low_stock_threshold`). Not paginated, whole-catalogue scan ⚠️ **GAP**.
**33.** `200` → one `StockSerializer`; the stock row is **created on demand** if
absent, so a `GET` can write ⚠️ **GAP** (side-effecting read).
`404 {"detail": "Вариант товара не найден."}` for an unknown variant.
**34.** Body `{"quantity": int > 0, "note": str}` → `201` `StockMovementSerializer`
(`id`, `kind`, `kind_display`, `delta`, `quantity_before`, `quantity_after`,
`note`, `order_number`, `performed_by_email`, `created_at`). Cumulative, **not**
idempotent.
**35.** Body `{"new_quantity": int ≥ 0, "note": str}` → **`200`** (not `201`,
although it also creates a `StockMovement` row) ⚠️ **GAP**. Sets an absolute
value, therefore effectively idempotent.
**36.** `200` → **bare array** of movements, newest first. When no `Stock` row
exists the endpoint returns `200 []` rather than `404` ⚠️ **GAP** (inconsistent
with #33, which `404`s only for an unknown variant).

### 9.6 Pricing (`apps.pricing`) — Staff only

| # | Method | Path |
|---|---|---|
| 37 | GET | `/api/v1/pricing/variants/{variant_id}/price/` |
| 38 | POST | `/api/v1/pricing/variants/{variant_id}/price/` |
| 39 | GET | `/api/v1/pricing/variants/{variant_id}/history/` |
| 40 | POST | `/api/v1/pricing/prices/bulk/` |

**37.** `200` → `PriceSerializer` (`id`, `variant`, `price`, `sale_price`,
`currency`, `effective_price`, `discount_percent`, `created_at`, `updated_at`).
`404 {"detail": "Вариант не найден."}` or `404 {"detail": "Цена не задана."}`
— two distinct `404` meanings on one route ⚠️ **GAP**.
**38.** Body `{"price": decimal, "sale_price": decimal|null, "reason": str}` →
**`200`** even when the price row is created ⚠️ **GAP** (upsert returning `200`).
Writes a `PriceHistory` audit row with `changed_by = request.user`. Idempotent
for identical payloads in effect, but each call appends history.
**39.** `200` → **bare array** of `PriceHistorySerializer` (`id`, `old_price`,
`new_price`, `old_sale_price`, `new_sale_price`, `changed_by` (PK), `reason`,
`created_at`), newest first. Not paginated ⚠️ **GAP**.
**40.** Body `{"prices": [{"variant_id", "price", "sale_price?"}, …]}` →
`200` **bare array** of resulting `PriceSerializer` objects. Atomic
(`transaction.atomic` in the service): all or nothing. `400` if any entry is
invalid. ❓ **DECISION REQUIRED:** partial-success semantics are explicitly *not*
supported; confirm at freeze.

### 9.7 Payments (`apps.payments`)

| # | Method | Path | Auth |
|---|---|---|---|
| 41 | GET | `/api/v1/payments/` | Auth |
| 42 | POST | `/api/v1/payments/` | Auth |
| 43 | POST | `/api/v1/payments/webhook/` | Public + HMAC |
| 44 | GET | `/api/v1/payments/{payment_number}/` | Auth (owner or staff) |
| 45 | POST | `/api/v1/payments/{payment_number}/refund/` | Staff |
| 46 | POST | `/api/v1/payments/{payment_number}/cancel/` | Auth (owner or staff) |

**41.** `200` → **bare array** of `PaymentListSerializer` (`id`, `order_number`,
`status`, `status_display`, `amount`, `method`, `method_display`, `provider`,
`created_at`, `paid_at`), scoped to the caller. Not paginated ⚠️ **GAP**.
**42.** Body `{"order_id": int, "amount": decimal?, "method": choice
(default `card`), "provider": str (default `mock`)}`. When `amount` is omitted
the order total is used. `201` → `PaymentSerializer` (adds `is_terminal`,
`is_paid`, `is_refundable`, `refund_amount`, `external_id`, `order`, `user`,
`paid_at`, `cancelled_at`, `refunded_at`, `note`, `refund_reason`, `metadata`,
`events[]`, `updated_at`). `404` unknown order.

✅ **CURRENT — ownership is enforced, no IDOR.** The view resolves the order with
`Order.objects.get(pk=data['order_id'])` (no inline owner filter), but it then
passes `user=request.user` into `PaymentService.create_payment()`, whose **first
action** is the ownership check:

```python
if order.user_id != user.pk:
    raise NotFound('Заказ не найден.')
```

Paying for another user's order is therefore impossible, and the failure mode is
`404 {"detail": "Заказ не найден."}` — identical to the "order does not exist"
response, so the endpoint conforms to the project-wide 404-not-403 policy (§10)
and leaks no order existence. Ownership enforcement simply lives in the service
layer rather than the view layer.

This is covered by tests at both layers:
`apps/payments/tests/test_api.py::…::test_create_payment_other_users_order`
asserts `404` for another user's `order_id`, and
`apps/payments/tests/test_services.py::…::test_create_payment_wrong_user`
asserts `NotFound` from the service.

Additional server-side guards applied by the same service call, in order:
order must be in status `PENDING` (else `400`); `amount` within
`MIN_PAYMENT_AMOUNT`…`MAX_PAYMENT_AMOUNT` (else `400`); **`amount` must equal
`order.total`** (else `400` — prevents underpayment); no existing succeeded
payment for the order (else `400 {"detail": "Заказ уже оплачен."}`).

⚠️ **GAP (style, not security):** the ownership check is not visible at the view
boundary, unlike every other owned-resource endpoint (§10). A future refactor
that changed or bypassed the service call would silently remove the protection.
This is a **defence-in-depth/readability** observation only — there is no
exploitable path today, so no follow-up issue is attached.
**43.** See §11.
**44.** `200` → `PaymentSerializer` with the full event history. Non-owner →
`404 {"detail": "Платёж не найден."}`.
**45.** Staff. Body `{"amount": decimal?, "reason": str?}` → `200`
`PaymentSerializer`. Omitting `amount` refunds the full amount. `400` if the
payment is not refundable or the amount exceeds the refundable balance.
**Not idempotent** — repeated calls accumulate refunds up to the cap.
**46.** Body `{"reason": str?}` → `200`. Owner or staff; non-owner → `404`.
`400` if the payment is terminal.

### 9.8 Reviews (`apps.reviews`)

| # | Method | Path | Auth |
|---|---|---|---|
| 47 | GET | `/api/v1/reviews/` | Public |
| 48 | POST | `/api/v1/reviews/` | Auth |
| 49 | GET | `/api/v1/reviews/{review_id}/` | Public |
| 50 | PATCH | `/api/v1/reviews/{review_id}/` | Auth (author) |
| 51 | DELETE | `/api/v1/reviews/{review_id}/` | Auth (author or staff) |
| 52 | POST | `/api/v1/reviews/{review_id}/helpful/` | Auth |

**47. `GET /reviews/`** — Public, **paginated with a bespoke envelope** (§6 B).
Query parameters (parsed by hand, not by a serializer ⚠️ **GAP**):

| Param | Behaviour |
|---|---|
| `product_id` | int; non-numeric → `400 {"product_id": …}`. |
| `product_uuid` | UUID; ignored when `product_id` is present; malformed or unknown → empty result set (**not** `400`/`404`) ⚠️ **GAP**. |
| `user_id` | int. **Silently coerced** to the caller's own id for non-staff users ⚠️ **GAP** (a request for another user's reviews returns *your* reviews). Anonymous callers get `200 []` — a **bare array**, breaking the envelope of the same endpoint ⚠️ **GAP**. |
| `rating`, `rating_gte`, `rating_lte` | int; non-numeric → `400`. No 1–5 range enforcement on `rating_gte`/`rating_lte`. |
| `verified` | truthy tokens `true`/`1`/`yes` only; anything else is ignored. |
| `ordering` | `rating`, `-rating`, `created_at`, `-created_at`, `helpful`; invalid values fall back to `-created_at` silently ⚠️ **GAP**. |
| `page`, `page_size` | `int()` without try/except → a non-numeric value raises `ValueError` → **`500`** ⚠️ **GAP (defect-level)**. `page_size` capped at 100. |

Default scope when neither `product_id`, `product_uuid` nor `user_id` is given:
the caller's own reviews (authenticated) or `200 []` (anonymous).
Only approved reviews are listed. Response items are `ReviewListSerializer`
(`id`, `user_id`, `user_email`, `product_id`, `rating`, `title`,
`verified_purchase`, `helpful_yes`, `helpful_no`, `helpful_score`, `my_vote`,
`created_at`); `my_vote` is populated only for authenticated callers.
`ordering=helpful` sorts in Python over the entire matched set.

**48. `POST /reviews/`** — Auth. Body `product_id` **or** `product_uuid`,
`rating` (1–5), `text`, `title?`. `201` → `ReviewSerializer`. Unknown product →
`404`; neither identifier supplied → **`400`** validation envelope.
Duplicate review by the same user for the same product → `400` from the service.

**49–51. `/reviews/{review_id}/`.** `GET` is public but an unapproved review is
visible only to its author or staff, otherwise `404` (§10). `PATCH` (author
only; body `rating?`, `title?`, `text?`) → `200`. `DELETE` (author or staff) →
`204`. Non-author `PATCH` → `403` from the service.

**52. `POST /reviews/{review_id}/helpful/`** — Auth. Body `{"vote": "yes"|"no"}`.
`200` → `ReviewSerializer` plus `my_vote`. **Toggle semantics:** repeating the
same vote clears it; the opposite vote switches it. Therefore **not idempotent**
— identical repeated calls alternate between voted and cleared.
❓ **DECISION REQUIRED:** freeze toggle semantics or move to explicit
`PUT`/`DELETE` of a vote sub-resource.

### 9.9 Discounts (`apps.discounts`)

| # | Method | Path | Auth |
|---|---|---|---|
| 53 | GET | `/api/v1/discounts/coupons/` | Staff |
| 54 | POST | `/api/v1/discounts/apply/` | Auth |
| 55 | POST | `/api/v1/discounts/remove/` | Auth |
| 56 | POST | `/api/v1/discounts/preview/` | Auth |

**53.** `200` → **bare array** of `CouponListSerializer` (`id`, `code`,
`discount_type`, `discount_value`, `max_discount`, `min_order_amount`,
`is_valid_now`, `is_exhausted`, `started_at`, `ended_at`) for currently valid
coupons. Not paginated ⚠️ **GAP**.
**54.** Body `{"code": str, "order_id": int}`. The order is fetched with
`user=request.user`, so another user's order → `404 {"detail": "Заказ не
найден."}`. `200` → `{"order_id": int, "discount": "…", "total": "…"}` —
a **bespoke mini-payload**, not the order representation ⚠️ **GAP**.
`400` for an invalid/expired/exhausted coupon or an unmet minimum.
**55.** Body `{"order_id": int}` validated by hand →
`400` canonical envelope with `order_id` in `details` when missing.
`200` → same mini-payload. Idempotent.
**56.** Body `{"code": str, "order_amount": decimal}` → `200`
`{code, discount_type, discount_value, max_discount, calculated_discount,
amount_after_discount}`. Pure computation, no side effects, idempotent.
`400`/`404` for an unknown or invalid coupon.

### 9.10 Shipping (`apps.shipping`)

| # | Method | Path | Auth |
|---|---|---|---|
| 57 | GET | `/api/v1/shipping/methods/` | Auth |
| 58 | POST | `/api/v1/shipping/calculate/` | Auth |
| 59 | GET | `/api/v1/shipping/shipments/` | Auth |
| 60 | POST | `/api/v1/shipping/shipments/create/` | Staff |
| 61 | GET | `/api/v1/shipping/shipments/{pk}/` | Auth (owner or staff) |
| 62 | PATCH | `/api/v1/shipping/shipments/{pk}/status/` | Staff |
| 63 | POST | `/api/v1/shipping/shipments/{pk}/tracking/` | Staff |
| 64 | GET | `/api/v1/shipping/track/{tracking}/` | **Public** |

**57.** Query `zone_code?`, `region?`, `shipping_type?` — free-form strings,
unvalidated ⚠️ **GAP**. `200` → **bare array** of `ShippingMethodListSerializer`
(`id`, `name`, `shipping_type`, `zone_name`, `base_price`, `price_per_kg`,
`free_shipping_threshold`, `estimated_days_display`, `is_active`).
⚠️ **GAP / ❓ DECISION REQUIRED:** this is reference data yet requires
authentication, which prevents a guest checkout from showing delivery options.
**58.** Body `order_total` (required), `zone_code?`, `region?`,
`shipping_type?`, `weight_kg?` → `200`
`{"zone": ShippingZone|null, "methods": [{method_id, method_name,
shipping_type, cost, estimated_days_display, is_free}]}`. Pure computation.
Note `cost` is emitted by a hand-built dict, so its JSON type follows the
underlying `Decimal` serialization of `ShippingCostResponseSerializer`'s
declared fields ⚠️ **GAP (schema-level)**.
**59.** `200` → **bare array** of `ShipmentListSerializer`. Staff see **all**
shipments; other users see only their own. Not paginated ⚠️ **GAP**.
**60.** Staff. Note the path is `/shipments/create/`, not `POST /shipments/`
⚠️ **GAP** (non-RESTful, inconsistent with orders/payments). Body `order_id`,
`method_id`, `weight_kg?`, `notes?` → `201` `ShipmentDetailSerializer`.
`404` for an unknown order or method.
**61.** `200` → `ShipmentDetailSerializer`. A non-staff caller asking for
someone else's shipment gets `404 {"detail": "Отправление не найдено."}` (§10).
**62.** Staff. Body `{"status": str, "tracking_number": str?}` → `200`.
`status` is a plain `CharField` here (not a `ChoiceField`), so the valid set is
enforced only by the service FSM ⚠️ **GAP**. Illegal transition → `400`.
**63.** Staff. Body `{"tracking_number": str}` → `200` full shipment.
Idempotent (absolute set). Note this is a `POST` that sets a single field,
whereas #62 uses `PATCH` ⚠️ **GAP (method semantics)**.
**64.** **Public** (`permission_classes = ()` — no authentication at all).
`tracking` matches either the carrier `tracking_number` **or** the internal
`SHP-XXXXXXXX` code. `200` → `ShipmentTrackingSerializer` (`internal_tracking`,
`tracking_number`, `status`, `status_display`, `method_name`,
`estimated_days_display`, `shipped_at`, `created_at`) — deliberately minimal, no
customer or address data. `404` if not found.
⚠️ **GAP:** internal tracking codes are **sequential and guessable**, so this
public endpoint permits enumeration of shipment status. It is also exempt from
per-user throttling (anonymous rate only). ❓ **DECISION REQUIRED.**

### 9.11 Wishlist (`apps.wishlist`) — all Auth

| # | Method | Path |
|---|---|---|
| 65 | GET | `/api/v1/wishlist/` |
| 66 | POST | `/api/v1/wishlist/add/` |
| 67 | DELETE | `/api/v1/wishlist/remove/{item_id}/` |
| 68 | POST | `/api/v1/wishlist/move-to-cart/` |
| 69 | POST | `/api/v1/wishlist/clear/` |

**65.** `200` → `WishlistSerializer` `{id, items_count, items[], created_at,
updated_at}`; created on demand, so it never `404`s. `WishlistItemSerializer`:
`id`, `variant_id`, `product_name`, `variant_name`, `sku`, `effective_price`,
`is_available`, `image_url`, `note`, `sort_order`, `created_at`. Items embedded,
never paginated.
**66.** Body `{"variant_id": int, "note": str?}` → `201` `WishlistItemSerializer`.
Unknown variant → `404`. Adding an existing variant returns the existing item
(idempotent in effect) but still with `201` ⚠️ **GAP**.
**67.** `204`, no body. Ownership enforced in the service; removing a
non-existent/foreign item does not leak existence.
**68.** Body `{"item_ids": [int]?, "variant_id": int?, "quantity": int=1}` →
`200 {"moved": int}`. Bespoke counter payload; the resulting cart is **not**
returned ⚠️ **GAP**. Requires an authenticated cart.
**69.** `200 {"removed": int}`. Idempotent (second call returns `0`).
Note this is a `POST`, whereas the cart's clear operation is a `DELETE`
⚠️ **GAP (method semantics)**.

### 9.12 Notifications (`apps.notifications`) — all Auth

| # | Method | Path |
|---|---|---|
| 70 | GET | `/api/v1/notifications/` |
| 71 | GET | `/api/v1/notifications/unread/` |
| 72 | GET | `/api/v1/notifications/unread-count/` |
| 73 | POST | `/api/v1/notifications/read-all/` |
| 74 | POST | `/api/v1/notifications/{pk}/read/` |

**70.** `200` → **bare array** of `NotificationListSerializer` (`id`,
`notification_type`, `title`, `status`, `is_read`, `created_at`). Not paginated
⚠️ **GAP** — an unbounded, monotonically growing per-user collection.
**71.** `200` → **bare array** of the *full* `NotificationSerializer`
(`id`, `notification_type`, `channel`, `title`, `body`, `status`,
`related_object_type`, `related_object_id`, `action_url`, `is_read`, `sent_at`,
`read_at`, `created_at`). ⚠️ **GAP** — two list endpoints on the same resource
return **different representations**.
**72.** `200 {"unread_count": int}`.
**73.** `200 {"marked": int}`. Idempotent (second call returns `0`).
**74.** `200` → the full notification. Ownership is enforced in
`NotificationService.mark_read(pk, user)`; a foreign id raises `NotFound` →
`404`. Idempotent.

### 9.13 Analytics (`apps.analytics`) — all Staff (`IsAdminUser`)

| # | Method | Path | Extra query params |
|---|---|---|---|
| 75 | GET | `/api/v1/analytics/dashboard/` | `days` |
| 76 | GET | `/api/v1/analytics/sales/` | `days` |
| 77 | GET | `/api/v1/analytics/sales/timeline/` | `days`, `period` |
| 78 | GET | `/api/v1/analytics/top-products/` | `days`, `metric`, `limit` |
| 79 | GET | `/api/v1/analytics/top-categories/` | `days`, `limit` |
| 80 | GET | `/api/v1/analytics/top-customers/` | `days`, `limit` |
| 81 | GET | `/api/v1/analytics/conversion/` | `days` |
| 82 | GET | `/api/v1/analytics/most-viewed/` | `days`, `limit` |

* `days` is validated by `AnalyticsDateRangeSerializer` (integer, bounded,
  default 30); an invalid value → `400`.
* `limit` is parsed with a bare `int(request.query_params.get('limit', 10))`
  — **no validation, no upper bound**; a non-numeric value raises `ValueError`
  → **`500`** ⚠️ **GAP (defect-level)**. Same class of issue as reviews `page`.
* `metric` (`revenue` default) and `period` (`daily` default) are free-form
  strings interpreted by the service; unknown values are not rejected at the
  API boundary ⚠️ **GAP**.
* Response shapes are plain service dictionaries, **not** passed through the
  declared `*Serializer` classes in `apps/analytics/serializers.py`
  ⚠️ **GAP** — the serializers document the intent, the views bypass them.
  `sales/timeline/` wraps its list as `{"timeline": [...]}`; the other list
  endpoints return **bare arrays**. Money in `Top*` payloads is a string.
* All are read-only, idempotent, and non-paginated (bounded by `limit`).

### 9.14 Health (`apps.core`)

**83. `GET /api/v1/health/`** — **Public**, and served by a plain Django `View`
(`JsonResponse`), *not* by DRF: no JWT parsing, no throttling, no DRF renderer,
no OpenAPI schema entry ⚠️ **GAP**.
`200 {"status": "ok", "version": "1.0.0", "database": "ok"}` when
`connection.ensure_connection()` succeeds;
`503 {"status": "degraded", "version": "1.0.0", "database": "error"}` when it
raises `django.db.Error`. `version` is a **hard-coded literal**, not derived
from the release ⚠️ **GAP**. Only `GET` is allowed (`405` otherwise).

### 9.15 Schema & docs (drf-spectacular)

These two routes are **not API resource endpoints**. They are the schema and
documentation surface, and each overrides the global JSON-only renderer
configuration with its own renderer set (§2). They are therefore explicitly
exempt from the JSON-only and `406` rules that govern §9.1–§9.14.

**84. `GET /api/v1/schema/`** — Public. `SpectacularAPIView`. Returns the
OpenAPI 3 document. Renderers (overriding the global default):
`OpenApiYamlRenderer` → `application/vnd.oai.openapi` (**default**, YAML),
`OpenApiYamlRenderer2` → `application/yaml`,
`OpenApiJsonRenderer` → `application/vnd.oai.openapi+json`,
`OpenApiJsonRenderer2` → `application/json`.
Format selection: content negotiation on `Accept`, or the `?format=json` /
`?format=yaml` query parameter. `SERVE_INCLUDE_SCHEMA = False`, so the schema
endpoint does not document itself. An `Accept` matching none of the four schema
media types → `406`.

**85. `GET /api/v1/docs/`** — Public. `SpectacularSwaggerView` — Swagger UI
bound to `url_name="schema"`. Renderer: `TemplateHTMLRenderer` →
**`text/html`** (overriding the global JSON-only default). This endpoint returns
an HTML page by design and never JSON.

Both are unauthenticated in every environment, including production
⚠️ **GAP / ❓ DECISION REQUIRED (API-02).**

**Endpoint total: 85 contract entries** across 15 groups, backed by **77 URL
patterns** in `get_resolver()`. A shared path whose HTTP methods have materially
different semantics is enumerated as separate entries above; a shared path with
uniform semantics is a single entry (e.g. `GET/PATCH/DELETE /users/me/`).

---

## 10. Ownership / IDOR semantics

✅ **CURRENT — the "404 not 403" policy.** For resources owned by a user, the API
deliberately returns `404 Not Found` (never `403`) when the caller is
authenticated but not the owner, so that the response is indistinguishable from
"this resource does not exist". This prevents resource-existence enumeration.

Endpoints applying the policy:

| Resource | Mechanism |
|---|---|
| Address | `Address.objects.get(pk=…, user=request.user)` → `NotFound` |
| Order | fetch by `order_number`, then `if not is_staff and order.user_id != user.pk: raise NotFound` |
| Payment | same pattern on `payment_number` |
| Shipment | non-staff query runs against `request.user.shipments` → `DoesNotExist` → `NotFound` |
| Cart item | ownership checked inside `CartService` against the resolved cart |
| Wishlist item | ownership checked inside `WishlistService` |
| Notification | ownership checked inside `NotificationService.mark_read` |
| Review (unapproved) | invisible to anyone but its author and staff → `404` |
| Discount apply/remove | `Order.objects.get(pk=…, user=request.user)` → `404` |

**Staff bypass:** `is_staff` users bypass the ownership filter on orders,
payments and shipments (they see everything).

**Exceptions to the policy (deliberate `403`):**

* Review `PATCH` by a non-author → `403` (the review is publicly visible
  anyway, so no existence is leaked).
* Catalog product create/update by a non-staff user → `403` with a custom body.
* Any `IsAdminUser` endpoint hit by an authenticated non-staff user → `403`.

✅ **CURRENT — where the check lives varies, but the policy holds everywhere.**
`POST /api/v1/payments/` is the one endpoint that enforces ownership in the
**service** rather than in the view: it fetches the order by raw PK, then
`PaymentService.create_payment()` raises `NotFound` when
`order.user_id != user.pk`. The observable contract is the same `404` as
everywhere else (§9.7 #42). No IDOR exists here; the difference is structural
only, and no follow-up issue is attached.

⚠️ **GAP:** `GET /api/v1/reviews/?user_id=<other>` silently rewrites the filter
to the caller's own id instead of returning `403`/`404`/`400` — a third,
undocumented behaviour for a non-owner request.

---

## 11. Action, idempotency and webhook semantics

### 11.1 Action endpoints ✅ **CURRENT**

Non-CRUD operations are modelled as `POST` to a verb sub-path
(`/cancel/`, `/refund/`, `/helpful/`, `/default/`, `/merge/`, `/restock/`,
`/adjust/`, `/read-all/`, `/{id}/read/`, `/clear/`, `/move-to-cart/`,
`/calculate/`, `/preview/`, `/apply/`, `/remove/`, `/tracking/`), while state
*transitions* driven by an FSM use `PATCH` to a `/status/` sub-path
(orders, shipments).

⚠️ **GAP — status code inconsistency across creating actions:**
`POST /cart/items/` → `201` (returns the *cart*), `POST /inventory/{id}/restock/`
→ `201`, `POST /inventory/{id}/adjust/` → `200` (also creates a movement),
`POST /pricing/variants/{id}/price/` → `200` (may create a price),
`POST /wishlist/add/` → `201` even when the item already existed.
🔁 **FOLLOW-UP: API-04/API-05.**

### 11.2 Idempotency summary ✅ **CURRENT**

| Idempotent | Not idempotent |
|---|---|
| `PATCH /users/me/`, address CRUD, `POST /addresses/{id}/default/` | `POST /auth/register/` |
| `DELETE /cart/`, `PATCH /cart/items/{id}/` (absolute quantity) | `POST /cart/items/` (increments) |
| `POST /notifications/read-all/`, `/{id}/read/` | `POST /orders/` (**no idempotency key**) |
| `POST /wishlist/clear/`, `POST /shipments/{id}/tracking/` | `POST /payments/`, `POST /payments/{n}/refund/` |
| `POST /inventory/{id}/adjust/`, `POST /discounts/preview/`, `POST /shipping/calculate/` | `POST /inventory/{id}/restock/` |
| Order/shipment status transitions (FSM rejects repeats with `400`) | `POST /reviews/{id}/helpful/` (**toggles**) |

⚠️ **GAP:** the API accepts **no** `Idempotency-Key` header anywhere. Order and
payment creation are the two operations where a client retry after a network
timeout can duplicate business state. ❓ **DECISION REQUIRED (API-07).**

### 11.3 Payment webhook — `POST /api/v1/payments/webhook/` ✅ **CURRENT**

* **Authentication:** none in the DRF sense (`authentication_classes = []`,
  `permission_classes = (AllowAny,)`). Security is provided by an
  **HMAC-SHA256** signature over the **raw request body**, presented in the
  `X-Webhook-Signature` header as lowercase hex, compared with
  `hmac.compare_digest` (timing-safe) against `settings.PAYMENT_WEBHOOK_SECRET`.
* **Fail-closed:** if `PAYMENT_WEBHOOK_SECRET` is unset/empty, or the header is
  missing, or the digest does not match → `403` canonical `permission_denied`.
  Neither the secret nor the signature is ever logged.
* **Body** (`HandleWebhookInputSerializer`): `external_id` (str, required),
  `event_type` (str, required), `status` (choice, required), `payload`
  (JSON object, optional). Invalid → `400`.
* **Success:** `200` with the full `PaymentSerializer`.
* **Unknown `external_id`:** `200 {"detail": "Платёж не найден, webhook
  logged."}` — deliberately `200` so the provider does not retry forever.
* **Order already finished** (`delivered`/`cancelled`): the payment is failed
  and a `PaymentEvent` is recorded; response `200 {"detail": "Заказ завершён;
  платёж отклонён."}`.
* **Transient order-confirmation failure:** `502` canonical `bad_gateway` —
  an explicit *retry me* signal to the provider. This is the only `502` in the API.
* **Idempotency/replay:** `Payment.external_id` carries a **unique constraint**
  (migration `payments/0004_payment_external_id_unique`), and repeated webhooks
  for the same `external_id` are absorbed by `PaymentService.handle_webhook`
  with an appended `PaymentEvent` audit trail. There is no timestamp/nonce in
  the signed payload, so a captured request body remains replayable indefinitely.
  ❓ **DECISION REQUIRED:** add a signed timestamp + freshness window before
  freeze (see ADR-004).
* Design rationale is recorded in `docs/adr/ADR-004-secure-idempotent-payment-webhooks.md`.

---

## 12. OpenAPI / drf-spectacular relationship

✅ **CURRENT**

* `drf_spectacular` is installed and configured:
  `DEFAULT_SCHEMA_CLASS = "drf_spectacular.openapi.AutoSchema"`,
  `SPECTACULAR_SETTINGS = {TITLE: "Amazone Clone API", DESCRIPTION:
  "Marketplace API", VERSION: "1.0.0", SERVE_INCLUDE_SCHEMA: False}`.
* Schema is served live at `/api/v1/schema/` and Swagger UI at `/api/v1/docs/`.
* Views are annotated with `@extend_schema` / `@extend_schema_view` for
  summaries, request serializers and (partly) response serializers.

⚠️ **GAP — the schema is not a guaranteed contract boundary:**

1. **Optional imports.** Nearly every `api_views` module wraps the import in
   `try: from drf_spectacular.utils import … except ImportError:` and falls back
   to no-op decorators. If the dependency were absent, the application would
   still boot and serve — but with **no schema annotations at all** and a
   silently degraded `/api/v1/schema/`. Schema generation is therefore
   *best-effort*, not enforced.
2. **No generated artifact is committed** and **no CI check** validates the
   schema or diffs it against a baseline.
3. **Incomplete annotations.** Response schemas are declared as string literals
   in places (`responses={200: 'Discount applied'}`, `'Moved'`, `'Cleared'`,
   `'Removed'`), and the ad-hoc dict responses (discounts, wishlist, analytics,
   notification counters, shipping calculate) are not backed by serializers.
4. **Query parameters** are only modelled where a query serializer exists
   (catalog listing). Reviews, analytics, shipping methods and inventory
   parameters are undocumented in the schema.
5. **`/api/v1/health/` is a plain Django view** and is absent from the schema.
6. **Pagination shapes** (`reviews` bespoke envelope, bare arrays) are not
   reflected accurately.
7. `SERVE_INCLUDE_SCHEMA = False`; both docs endpoints are unauthenticated.

**Precedence rule until API-02 lands:** where this document and the generated
OpenAPI schema disagree, **`docs/api/API_CONTRACT.md` is normative.**
🔁 **FOLLOW-UP: API-02** must make schema generation mandatory (remove the
optional-import fallbacks), commit/validate an artifact in CI, and then invert
this precedence rule so that OpenAPI becomes the machine-readable source of
truth with this document as its prose companion.

---

## 13. Contract gaps and follow-up issues

The table below is the complete list of material gaps found by API-01.
"Owner" names the API Freeze ticket that must resolve it. An **F-n** owner links
to a dedicated GitHub issue created by API-01, because that item falls outside
the API-02…API-07 scope statements.

> **Withdrawn during review — G-17 (`POST /api/v1/payments/` IDOR).** An earlier
> revision of this document classified that endpoint as an IDOR gap on the basis
> of the view code alone. Re-verification of the full chain
> (view → serializer → `PaymentService.create_payment` → tests) showed that the
> service performs the ownership check (`order.user_id != user.pk` → `NotFound`)
> and that both API- and service-level tests assert it. **The gap and its
> follow-up issue reference were removed**; the factual behaviour is documented
> in §9.7 #42 and §10. Issue #68 was filed before this re-verification and is
> now obsolete — it should be closed as "not a defect" by the Owner. Follow-up
> numbering (F-1…F-12) is unchanged so that existing issue links stay valid.

| # | Gap | Where | Owner |
|---|---|---|---|
| G-1 | Three different collection shapes (DRF envelope / bespoke reviews envelope / bare array) and 11 unbounded non-paginated collections | §6 | **API-05** |
| G-2 | `page_size` accepted but ignored on catalog listing; not supported elsewhere | §9.2 | **API-05** |
| G-3 | Reviews list changes shape within one endpoint (`ordering=helpful` drops `total_pages`; anonymous `user_id` returns a bare array) | §9.8 | **API-05** |
| G-4 | `400` bodies mixed string vs list | §5 | **Closed by API-04** (canonical `details` list) |
| G-5 | No machine-readable error codes | §4, §5 | **Closed by API-04** (`error.code`) |
| G-6 | Business conflicts return `400`; `409` unused | §5.2 | **Closed by API-04** (`409` remains unused by design) |
| G-7 | Custom `403` body for catalog staff checks | §3.2 | **Closed by API-04** (canonical envelope) |
| G-8 | `DELETE` semantics inconsistent: `204` vs `200`+body | §9.1, §9.3 | Out of API-04 error-envelope scope (success statuses) |
| G-9 | Creating actions return mixed `200`/`201` | §11.1 | Out of API-04 error-envelope scope |
| G-10 | Guest-cart merge (`/cart/merge/`) couples JWT to a cookie session; password change/reset intentionally keeps existing JWTs/refresh tokens valid (API-03 freeze) | §3.4, §9.3 | Separate auth/cookie follow-up outside API-03 |
| G-11 | Unauthenticated `/api/v1/schema/` and `/api/v1/docs/` in production | §9.15 | **API-02** |
| G-12 | Optional `drf_spectacular` imports make schema generation best-effort; no committed artifact, no CI validation | §12 | **API-02** |
| G-13 | `int()` on `page`/`page_size` (reviews) and `limit` (analytics) without try/except → `500` on non-numeric input | §9.8, §9.13 | **[F-1 (#66)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/66)** |
| G-14 | Reviews list query parameters are hand-parsed; `ordering` and `product_uuid` fail silently instead of `400` | §9.8 | **API-05** (with [#66](https://github.com/Kvasha62/Amazone_Clone_Production/issues/66)) |
| G-15 | `?ordering=` invalid value silently falls back on catalog listing; `is_featured`/`status` query params are inert | §9.2 | **API-05** |
| G-16 | `GET /reviews/?user_id=<other>` silently rewrites to the caller's id | §9.8, §10 | **[F-2 (#67)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/67)** |
| G-18 | Public `/shipping/track/{tracking}/` is enumerable via sequential `SHP-` codes | §9.10 | **[F-4 (#69)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/69)** |
| G-19 | `GET /shipping/methods/` requires authentication although it is reference data (blocks guest checkout) | §9.10 | **[F-5 (#70)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/70)** |
| G-20 | No `Idempotency-Key` support; `POST /orders/` and `POST /payments/` are duplicable on retry | §11.2 | **API-07** |
| G-21 | Webhook signature has no timestamp/nonce → indefinite replay window | §11.3 | **[F-6 (#71)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/71)** |
| G-22 | Side-effecting reads: `GET /catalog/products/{id}/` increments views; `GET /inventory/{variant_id}/` creates a Stock row | §9.2, §9.5 | **[F-7 (#72)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/72)** |
| G-23 | Identifier drift: catalog `id` is a UUID; cross-context order references use the integer PK while order URLs use `order_number`; shipments expose a raw PK | §7 | **[F-8 (#73)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/73)** |
| G-24 | Notification list vs unread list return different representations of the same resource | §9.12 | **API-05** |
| G-25 | Analytics views bypass their own serializers; `metric`/`period` unvalidated | §9.13 | **API-02** (schema) + [#66](https://github.com/Kvasha62/Amazone_Clone_Production/issues/66) |
| G-26 | Bespoke mini-payloads for discounts apply/remove and wishlist move-to-cart | §9.9, §9.11 | Out of API-04 error-envelope scope |
| G-27 | `/api/v1/health/` is outside DRF and outside OpenAPI; `version` is hard-coded | §9.14 | **[F-9 (#74)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/74)** |
| G-28 | No currency field on order/payment/shipping money; single implicit currency | §8.1 | **[F-10 (#75)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/75)** |
| G-29 | `POST /reviews/` returns `404` for a missing-identifier validation error | §9.8 | **Closed by API-04** (`400`) |
| G-30 | `GET /orders/` has no staff-wide variant; `PATCH /orders/{n}/status/` is staff-only but there is no way for staff to list all orders via the API | §9.4 | **[F-11 (#76)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/76)** |
| G-31 | `POST /shipping/shipments/create/` instead of `POST /shipping/shipments/`; `POST …/tracking/` vs `PATCH …/status/`; `POST /wishlist/clear/` vs `DELETE /cart/` | §9.10, §9.11 | **API-04** |
| G-31a | Duplicate `RegisterInputSerializer` (inline view copy vs exported module copy declaring `phone`); the exported one is dead code | §9.1 | **[F-12 (#77)](https://github.com/Kvasha62/Amazone_Clone_Production/issues/77)** |

**Contract decisions made inside API-01** (resolved here, no follow-up needed):

1. The `404`-instead-of-`403` ownership policy is **normative and intentional**
   (§10) — it is documented, not treated as a bug.
2. Money is a **JSON string** with 2 decimal places (§8.1) — normative.
3. Timestamps are **UTC ISO-8601 with a `Z` suffix** (§8.2) — normative.
4. Empty optional text is `""`, empty collections are `[]` (§8.3) — normative.
5. Categories, brands, addresses, shipping methods and `by-slugs` are
   **intentionally non-paginated** (§6 class C) — normative, exempt from API-05.
6. Payment webhook `200`-on-unknown-payment and `502`-on-transient-failure are
   **intentional provider-retry signalling** (§11.3) — normative.
7. Path-based versioning with no DRF versioning class is **intentional** (§2).
8. Until API-02 completes, **this document outranks the generated OpenAPI
   schema** (§12).

---

## 14. Freeze-readiness notes

API v1 **cannot be frozen** until the following are closed:

| Ticket | Must resolve |
|---|---|
| **API-02 — OpenAPI contract** | Remove the optional `drf_spectacular` import fallbacks so schema generation is mandatory; annotate every response (no string-literal responses); document query parameters; include health; commit and CI-validate a schema artifact; decide on protecting `/schema/` and `/docs/`; invert the precedence rule in §12. Closes G-11, G-12, G-25. |
| **API-03 — Authentication contract** | Logout / refresh-token revocation; explicit access-token no-blacklist policy; deterministic password change/reset lifecycle; account deactivation behaviour. Closes the logout portion of G-10; the `/cart/merge/` cookie coupling remains a separate follow-up. |
| **API-04 — Error contract** | One error envelope with stable machine-readable codes; `400` body type consistency; `409` remains unused by design; production-safe `5xx`. Closes G-4, G-5, G-6, G-7, G-29. Success-path G-8/G-9/G-26/G-31 are out of this ticket. |
| **API-05 — Collection/pagination contract** | One pagination envelope; `page_size` as a first-class parameter; paginate the 11 unbounded collections; unify notification representations; validate list query parameters. Closes G-1, G-2, G-3, G-14, G-15, G-24. |
| **API-06 — API integration/contract tests** | Executable tests asserting every claim in §9 (status codes, shapes, ownership `404`s, pagination envelopes, webhook signature handling), so the contract cannot silently drift. |
| **API-07 — End-to-end business scenarios** | Idempotency keys for order and payment creation; the full guest→cart→order→payment→shipment→review journey as a contract-level scenario. Closes G-20. |
| **New follow-ups [#66](https://github.com/Kvasha62/Amazone_Clone_Production/issues/66)–[#77](https://github.com/Kvasha62/Amazone_Clone_Production/issues/77)**, excluding the withdrawn [#68](https://github.com/Kvasha62/Amazone_Clone_Production/issues/68) | The defect- and policy-level items in §13 that are out of scope for API-02…API-07 and each need their own issue. |
| **Final Backend/API Freeze Audit** | Re-run this inventory against the routing tree and confirm zero undocumented public endpoints. |

Once all of the above are closed and this document has been regenerated against
the then-current tree, API v1 may be declared frozen: any subsequent
client-visible change requires `/api/v2/` or an explicit, versioned deprecation.

---

## 15. Verification performed by API-01 and API-03

* Endpoint inventory was produced by walking the live Django URL resolver
  (`get_resolver()`), not by reading docstrings — **77 `/api/v1/` URL patterns**,
  enumerated as **85 contract entries** in §9. API-03 adds
  `POST /api/v1/auth/logout/` (#4 in §9.1). No public `/api/v1/` endpoint was
  found that is absent from this document.
* Permission classes, HTTP methods and serializer field sets were introspected
  from the loaded application (`permission_classes`, `Serializer().fields`).
* `python manage.py check --fail-level WARNING` → *System check identified no
  issues (0 silenced).*
* `python manage.py makemigrations --check --dry-run` → *No changes detected.*
* `git diff --check` → clean.
* API-03's code change is scoped to the authentication lifecycle:
  `POST /api/v1/auth/logout/` blacklists the presented refresh token, and
  `SIMPLE_JWT` freezes `CHECK_USER_IS_ACTIVE=True` /
  `CHECK_REVOKE_TOKEN=False`. No authorization model, error schema, pagination,
  OpenAPI annotation or token-family design was modified.

**Review corrections (PR #78):**

* **Renderer negotiation** was re-verified by issuing real requests through the
  Django test client rather than reading settings: API resource endpoints are
  JSON-only and return `406` on an unsatisfiable `Accept`, while
  `SpectacularAPIView` and `SpectacularSwaggerView` override
  `DEFAULT_RENDERER_CLASSES` with their own renderer sets (OpenAPI schema media
  types and `text/html` respectively). §2 and §9.15 were corrected accordingly;
  the previous blanket "JSON only / `406`" statement was contradictory.
* **`POST /api/v1/payments/` ownership** was re-verified across the whole chain
  (view → `CreatePaymentInputSerializer` → `PaymentService.create_payment` →
  `apps/payments/tests/test_api.py` and `test_services.py`). The service
  enforces `order.user_id != user.pk → NotFound`, so the earlier IDOR
  classification was **incorrect and has been withdrawn** (see the note in §13).
  No runtime code was changed to reach either conclusion.
