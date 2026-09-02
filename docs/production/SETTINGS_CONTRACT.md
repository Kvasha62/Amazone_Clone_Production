# Production settings contract — PROD-007 / F-11

This document is the authoritative description of the Django settings
configuration contract introduced by Issue #12 (PROD-007). It replaces the
previous silent, development-friendly fallbacks with an explicit, fail-closed
production configuration.

The contract is implemented once, in `config/settings.py`, by the
`_build_config(environ)` function and its helpers `_parse_bool` / `_parse_host_list`.
Every security-sensitive setting is derived from that single function, so the
behavior is deterministic and unit-tested.

## Two explicit modes

The project boots in exactly one of two modes, selected by `DJANGO_DEBUG`:

| `DJANGO_DEBUG` | Mode          | Behavior                                            |
| -------------- | ------------- | -------------------------------------------------- |
| `true`         | DEVELOPMENT   | Convenient explicit dev defaults (localhost CORS). |
| `false`        | PRODUCTION    | Strict, fail-closed security contract.              |
| unset / invalid| —             | **Boot fails** with `ImproperlyConfigured`.        |

`DJANGO_DEBUG` is **required and explicit**. There is no implicit default and
no silent fallback to a development/unsafe configuration. Boolean parsing never
uses `bool(os.getenv(...))` truthiness — only the explicit token sets
`{true,1,yes,on,y}` / `{false,0,no,off,n}` are accepted; anything else fails.

## SECRET_KEY (AC-1)

- **Production:** `DJANGO_SECRET_KEY` must be provided. A missing or empty value
  fails fast. A Django-generated `django-insecure-*` placeholder is rejected.
- **Development:** an explicit value is preferred; a safe dev-only placeholder
  is used only when none is given. That placeholder is never used on the
  production path.

## DEBUG (AC-2)

- `DEBUG` is derived deterministically from `DJANGO_DEBUG`. Production cannot
  run with `DEBUG=True` — reaching the production path requires an explicit
  `false`, and the production path never yields `DEBUG=True`.

## ALLOWED_HOSTS (AC-3)

- **Production:** `DJANGO_ALLOWED_HOSTS` must be an explicit, non-empty,
  comma-separated list. The wildcard `*` is rejected. There is no permissive
  fallback.
- **Development:** defaults to `["*"]` when unset (explicit dev convenience).

## CORS (AC-4)

- **Production:** `CORS_ALLOW_ALL_ORIGINS` is forced to `False`. An explicit
  `true` is rejected with `ImproperlyConfigured`. `CORS_ALLOWED_ORIGINS`
  defaults to an empty list (no origins) and is never implicitly permissive.
- **Development:** `CORS_ALLOW_ALL_ORIGINS` defaults to `True` with explicit
  localhost origins.

## Deterministic parsing (AC-5)

- `_parse_bool(name, raw, *, default=_UNSET)` — accepts only the explicit token
  sets; raises on missing-required or invalid input.
- `_parse_host_list(name, raw, *, default=_UNSET, allow_wildcard=True)` —
  deterministic comma-split with strip; raises on missing-required, empty, or
  (when `allow_wildcard=False`) wildcard input.

## Development / test usability (AC-6)

The development/test path remains fully usable: set `DJANGO_DEBUG=true` (plus
optional `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CORS_*`). Those
convenient defaults are applied **only** on the development path and never leak
into production.

## Tests (AC-8)

`config/tests/test_settings_config.py` exercises `_build_config` directly and
covers, at minimum:

- missing `SECRET_KEY` in production fails;
- `DEBUG` cannot silently default to `true` in production;
- missing / wildcard `ALLOWED_HOSTS` in production fails;
- permissive CORS is rejected in production;
- a valid production configuration loads successfully;
- development / test configuration remains usable;
- invalid boolean / list configuration is rejected.

## Environment parsing summary

| Variable                | Production requirement                                  |
| ----------------------- | ------------------------------------------------------- |
| `DJANGO_DEBUG`          | Required, explicit `false`.                             |
| `DJANGO_SECRET_KEY`     | Required, not `django-insecure-*`.                      |
| `DJANGO_ALLOWED_HOSTS`  | Required, explicit, no `*`.                             |
| `CORS_ALLOW_ALL_ORIGINS`| Forced `False` (explicit `true` rejected).              |
| `CORS_ALLOWED_ORIGINS`  | Optional; explicit list (empty by default in prod).     |
