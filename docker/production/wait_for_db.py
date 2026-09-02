"""wait_for_db.py — PROD-008 / F-12 database availability gate.

Blocks until the configured PostgreSQL database accepts connections, or
exits non-zero after the timeout. Used by ``docker/production/entrypoint.sh``
so that every production service starts only after the database dependency
is reachable (Issue #15, §4 and §8).

Configuration (environment):
    DB_WAIT_TIMEOUT — total seconds to wait before failing (default: 60).
    DB_WAIT_INTERVAL — seconds between connection attempts (default: 2).

The connection parameters come from the standard Django settings contract
(``DB_*`` variables), identical for all services.
"""

import os
import sys
import time


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        value = int(raw) if raw.strip() else default
    except ValueError:
        print(f"[wait_for_db] invalid {name}={raw!r}; using default {default}")
        return default
    return value if value > 0 else default


def main() -> int:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.db import connection

    timeout = _positive_int("DB_WAIT_TIMEOUT", 60)
    interval = _positive_int("DB_WAIT_INTERVAL", 2)
    deadline = time.monotonic() + timeout

    while True:
        try:
            connection.ensure_connection()
        except Exception as exc:  # noqa: BLE001 — any DB error means "not ready"
            if time.monotonic() >= deadline:
                print(
                    f"[wait_for_db] database not available after {timeout}s: {exc}",
                    file=sys.stderr,
                )
                return 1
            print(f"[wait_for_db] database not ready ({exc.__class__.__name__}); retrying ...")
            time.sleep(interval)
        else:
            print("[wait_for_db] database is available")
            return 0


if __name__ == "__main__":
    sys.exit(main())
