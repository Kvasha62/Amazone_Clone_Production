# ────────────────────────────────────────────────────────────────────────
# apps/core/tests/test_concurrency_db.py
#
# PROD-015 — regression coverage для ГЛАВНОЙ гарантии bounded-раннера:
# зависший воркер не может удерживать тестовую БД.
#
# Проверяется именно то, что daemon=True + `finally: close_all()` НЕ
# обеспечивают: воркер, зависший ДО своего finally (внутри запроса или
# внутри открытой транзакции), не должен оставить живую серверную
# сессию с незакрытой транзакцией и локами — иначе teardown Django
# (TRUNCATE / DROP DATABASE) заблокируется.
#
# Локи берутся advisory (pg_advisory_xact_lock): они дают ровно ту же
# семантику «транзакция держит лок до конца», но не требуют моделей,
# миграций и таблиц.
# ────────────────────────────────────────────────────────────────────────

import threading
import time
import unittest

from django.db import DEFAULT_DB_ALIAS, connection, connections, transaction
from django.test import TransactionTestCase

from apps.core.tests.concurrency import (
    ConcurrentJobsMixin,
    run_concurrent_jobs,
)

#: Уникальные ключи advisory-локов (в пределах модуля).
LOCK_KEY_TERMINATED = 915001
LOCK_KEY_IDLE_TIMEOUT = 915002


def _pg_sleep(seconds):
    """Job, зависающий ВНУТРИ DB-операции (finally недостижим)."""

    def job():
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute('SELECT pg_sleep(%s)', [seconds])
        return 'finished'

    return job


def _hold_advisory_lock(key, release, hold=60):
    """
    Job, зависающий В PYTHON-КОДЕ, удерживая ОТКРЫТУЮ транзакцию с
    advisory-локом. statement_timeout здесь бессилен: сервер не видит
    активного запроса — сессия «idle in transaction».
    """

    def job():
        with transaction.atomic():
            with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
            release.wait(timeout=hold)
        return 'released'

    return job


def _advisory_lock_is_free(key, wait=5.0):
    """
    Может ли ГЛАВНЫЙ поток взять тот же advisory-лок?

    Берём на отдельном соединении с lock_timeout: если лок всё ещё
    удерживается брошенной транзакцией воркера — получим ошибку, а не
    зависание (проверка сама остаётся bounded).
    """
    admin = connections.create_connection(DEFAULT_DB_ALIAS)
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                'SELECT set_config(%s, %s, false)',
                ['lock_timeout', str(int(wait * 1000))],
            )
            try:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
            except Exception:  # noqa: BLE001 — lock_timeout / прочее
                return False
            cursor.execute('SELECT pg_advisory_unlock_all()')
        return True
    finally:
        admin.close()


def _backend_is_alive(pid):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_stat_activity WHERE pid = %s', [pid])
        return cursor.fetchone() is not None


@unittest.skipUnless(
    connection.vendor == 'postgresql',
    'PostgreSQL — единственная поддерживаемая СУБД проекта.',
)
class StuckWorkerReleasesDatabaseTests(ConcurrentJobsMixin, TransactionTestCase):
    """Зависший воркер не удерживает тестовую БД — доказательно."""

    def test_successful_worker_registers_and_releases_its_backend(self):
        run = run_concurrent_jobs([lambda: _pg_sleep(0)()], timeout=10)

        self.assertEqual(run.results, ['finished'])
        self.assertFalse(run.timed_out)
        self.assertEqual(len(run.worker_backends), 1)
        pid = next(iter(run.worker_backends))
        # Воркер дошёл до finally сам: сессия закрыта, реапер не нужен.
        self.assertFalse(_backend_is_alive(pid))
        self.assertFalse(run.termination.performed)
        self.assertTrue(run.db_released)

    def test_server_side_timeout_aborts_stuck_query_before_deadline(self):
        """
        Слой 1 (превенция): сервер сам прерывает зависший запрос
        воркера раньше дедлайна join'а — поток разворачивается штатно,
        доходит до finally, прогон даже не считается timed out.
        """
        run = run_concurrent_jobs([_pg_sleep(60)], timeout=2)

        self.assertFalse(run.timed_out, run.stuck_report())
        self.assertEqual(len(run.errors), 1)
        self.assertIn('statement timeout', str(run.errors[0][1]).lower())
        self.assertTrue(run.db_released)
        for pid in run.worker_backends:
            self.assertFalse(_backend_is_alive(pid))

    def test_idle_in_transaction_timeout_releases_locks_of_hung_worker(self):
        """
        Слой 1 для самого опасного случая: воркер завис в Python-коде,
        УДЕРЖИВАЯ открытую транзакцию с локом. Его сессию завершает
        idle_in_transaction_session_timeout — лок освобождён ещё до
        того, как раннер объявит timeout.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        run = run_concurrent_jobs(
            [_hold_advisory_lock(LOCK_KEY_IDLE_TIMEOUT, release)],
            timeout=2,
            termination_wait=5,
            grace_join=2,
        )

        # Сессия воркера уже завершена сервером → реаперу нечего убивать.
        self.assertEqual(run.termination.requested, [])
        self.assertTrue(run.db_released)
        self.assertTrue(
            _advisory_lock_is_free(LOCK_KEY_IDLE_TIMEOUT),
            'Транзакция зависшего воркера всё ещё держит лок',
        )

    def test_reaper_terminates_backend_of_stuck_worker(self):
        """
        Слой 2 (гарантия): даже БЕЗ server-side таймаутов сессия
        зависшего воркера принудительно завершается из главного потока
        до возврата в тест.
        """
        run = run_concurrent_jobs(
            [_pg_sleep(60)],
            timeout=1,
            apply_server_timeouts=False,
            termination_wait=10,
            grace_join=5,
        )

        self.assertTrue(run.timed_out)
        self.assertEqual(len(run.worker_backends), 1)
        pid = next(iter(run.worker_backends))
        self.assertEqual(run.termination.requested, [pid])
        self.assertEqual(run.termination.terminated, [pid])
        self.assertEqual(run.termination.still_alive, [])
        self.assertTrue(run.db_released)
        self.assertFalse(_backend_is_alive(pid))
        # Диагностика причины timeout сохранена.
        report = run.stuck_report()
        self.assertIn('concurrent-job-0', report)
        self.assertIn(f'pid={pid}', report)
        self.assertIn('pg_sleep', report)

    def test_reaper_rolls_back_open_transaction_and_frees_locks(self):
        """
        Ключевой сценарий PROD-015: воркер завис в Python-коде внутри
        открытой транзакции (finally НЕ достигнут, connection не
        закрыт). После прогона лок должен быть свободен — иначе
        teardown БД зависнет.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        run = run_concurrent_jobs(
            [_hold_advisory_lock(LOCK_KEY_TERMINATED, release)],
            timeout=1,
            apply_server_timeouts=False,
            termination_wait=10,
            grace_join=5,
        )

        self.assertTrue(run.timed_out)
        self.assertEqual(run.termination.still_alive, [])
        self.assertTrue(run.termination.terminated)
        self.assertTrue(run.db_released)
        self.assertTrue(
            _advisory_lock_is_free(LOCK_KEY_TERMINATED),
            'Транзакция зависшего воркера не откачена: лок удерживается',
        )
        # Состояние серверной сессии попало в диагностику.
        self.assertIn('idle in transaction', run.stuck_report())

    def test_database_is_usable_immediately_after_bounded_failure(self):
        """
        После bounded-провала главный поток продолжает нормально
        работать с БД: DDL/DML не блокируются брошенной транзакцией.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        run_concurrent_jobs(
            [_hold_advisory_lock(LOCK_KEY_TERMINATED, release)],
            timeout=1,
            apply_server_timeouts=False,
            termination_wait=10,
            grace_join=5,
        )

        started = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute('SELECT set_config(%s, %s, false)', ['lock_timeout', '5000'])
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [LOCK_KEY_TERMINATED])
            cursor.execute('SELECT pg_advisory_unlock_all()')
            cursor.execute('SELECT 1')
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertLess(time.monotonic() - started, 5)

    def test_mixin_fails_and_reports_db_release_on_stuck_worker(self):
        """
        Через миксин: зависание = обычный failure с диагностикой,
        включающей факт освобождения БД.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        with self.assertRaises(AssertionError) as ctx:
            self.run_concurrent_jobs(
                [_hold_advisory_lock(LOCK_KEY_TERMINATED, release)],
                timeout=1,
                apply_server_timeouts=False,
            )

        message = str(ctx.exception)
        self.assertIn('не завершились за отведённое время', message)
        self.assertIn('Принудительное освобождение тестовой БД', message)
        self.assertTrue(_advisory_lock_is_free(LOCK_KEY_TERMINATED))
