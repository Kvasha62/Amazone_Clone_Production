# ────────────────────────────────────────────────────────────────────────
# apps/core/tests/test_concurrency_runner.py
#
# PROD-015 — regression coverage для ограниченного по времени раннера
# конкурентных тестов (apps/core/tests/concurrency.py).
#
# Тесты НЕ трогают БД (SimpleTestCase): проверяется только семантика
# таймаута/teardown/cleanup, ради которой раннер и заведён.
# ────────────────────────────────────────────────────────────────────────

import threading
import unittest
from time import monotonic
from unittest import mock

from django.test import SimpleTestCase

from apps.core.tests import concurrency as concurrency_module
from apps.core.tests.concurrency import ConcurrentJobsMixin, run_concurrent_jobs


class RunConcurrentJobsTests(SimpleTestCase):
    """Базовые гарантии раннера: порядок, ошибки, ограниченность ожидания."""

    def setUp(self):
        # Раннер вызывает connections.close_all() в каждом воркере;
        # реальная БД для этих тестов не нужна.
        patcher = mock.patch.object(concurrency_module, 'connections')
        self.connections_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_results_are_returned_in_job_order(self):
        run = run_concurrent_jobs(
            [lambda: 'a', lambda: 'b', lambda: 'c'],
            timeout=10,
        )

        self.assertEqual(run.results, ['a', 'b', 'c'])
        self.assertEqual(run.errors, [])
        self.assertFalse(run.timed_out)

    def test_worker_exception_is_collected_not_raised(self):
        boom = RuntimeError('boom')

        def failing():
            raise boom

        run = run_concurrent_jobs([lambda: 'ok', failing], timeout=10)

        self.assertEqual(run.results, ['ok'])
        self.assertEqual([index for index, _ in run.errors], [1])
        self.assertIs(run.errors[0][1], boom)
        self.assertFalse(run.timed_out)
        self.assertIn('boom', run.errors_report())

    def test_connections_are_closed_on_every_reachable_exit_path(self):
        """AC-4: соединения воркера закрываются и на успехе, и на ошибке."""

        def failing():
            raise ValueError('fail')

        run_concurrent_jobs([lambda: 'ok', failing], timeout=10)

        # 2 воркера × (сброс унаследованных + finally) = 4 вызова.
        self.assertEqual(self.connections_mock.close_all.call_count, 4)

    def test_stuck_worker_fails_boundedly_instead_of_hanging(self):
        """
        AC-2/AC-3: зависший воркер НЕ приводит к бесконечному ожиданию —
        раннер возвращается по дедлайну и репортит поток детерминированно.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck():
            release.wait(timeout=60)
            return 'late'

        started = monotonic()
        run = run_concurrent_jobs([lambda: 'fast', stuck], timeout=1)
        elapsed = monotonic() - started

        self.assertTrue(run.timed_out)
        self.assertEqual(run.stuck, ['concurrent-job-1'])
        self.assertEqual(run.results, ['fast'])
        self.assertLess(elapsed, 10, 'Ожидание не ограничено таймаутом')
        report = run.stuck_report()
        self.assertIn('concurrent-job-1', report)
        self.assertIn('стек потока concurrent-job-1', report)

    def test_total_wait_is_bounded_by_single_deadline(self):
        """
        AC-5: таймаут общий на прогон, а не timeout × число потоков —
        иначе «ограниченное» ожидание масштабируется до неограниченного.
        """
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck():
            release.wait(timeout=60)

        started = monotonic()
        run = run_concurrent_jobs([stuck, stuck, stuck, stuck], timeout=1)
        elapsed = monotonic() - started

        self.assertTrue(run.timed_out)
        self.assertEqual(len(run.stuck), 4)
        self.assertLess(elapsed, 3.5, f'Суммарное ожидание {elapsed:.1f}s > timeout')

    def test_stuck_worker_threads_are_daemon(self):
        """
        Ключевая причина отказа от ThreadPoolExecutor: его воркеры не
        daemon и джойнятся atexit-хуком — «ограниченный» таймаут просто
        переезжает на выход из интерпретатора.
        """
        release = threading.Event()
        seen = []
        self.addCleanup(release.set)

        def stuck():
            seen.append(threading.current_thread().daemon)
            release.wait(timeout=60)

        run_concurrent_jobs([stuck], timeout=1)

        self.assertEqual(seen, [True])

    def test_broken_barrier_is_reported_as_error_without_hanging(self):
        """Барьер тоже ограничен: он не может подвесить прогон навсегда."""
        def waits_on_foreign_barrier(barrier):
            barrier.wait(timeout=0.05)  # партнёр к барьеру не придёт
            return 'never'

        run = run_concurrent_jobs(
            [waits_on_foreign_barrier, lambda barrier: 'skipped'],
            timeout=10,
            pass_barrier=True,
        )
        self.assertFalse(run.timed_out)
        self.assertEqual(run.results, ['skipped'])
        self.assertEqual(len(run.errors), 1)
        self.assertIsInstance(run.errors[0][1], threading.BrokenBarrierError)

    def test_empty_job_list_is_noop(self):
        run = run_concurrent_jobs([], timeout=1)

        self.assertEqual(run.results, [])
        self.assertEqual(run.errors, [])
        self.assertFalse(run.timed_out)


class ConcurrentJobsMixinTests(SimpleTestCase):
    """Миксин превращает зависание/ошибку в обычный провал теста."""

    def setUp(self):
        patcher = mock.patch.object(concurrency_module, 'connections')
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _run_case(test_method):
        class _Case(ConcurrentJobsMixin, unittest.TestCase):
            concurrency_timeout = 1
            runTest = test_method

        result = unittest.TestResult()
        _Case('runTest').run(result)
        return result

    def test_mixin_fails_test_on_stuck_worker(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def test_method(case):
            case.run_concurrent_jobs([lambda: release.wait(timeout=60)])

        started = monotonic()
        result = self._run_case(test_method)
        elapsed = monotonic() - started

        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assertIn('не завершились за отведённое время', result.failures[0][1])
        self.assertLess(elapsed, 10)

    def test_mixin_fails_test_on_worker_exception(self):
        def test_method(case):
            def failing():
                raise RuntimeError('worker exploded')

            case.run_concurrent_jobs([failing])

        result = self._run_case(test_method)

        self.assertEqual(len(result.failures), 1)
        self.assertIn('worker exploded', result.failures[0][1])

    def test_mixin_returns_results_on_success(self):
        captured = {}

        def test_method(case):
            captured['run'] = case.run_concurrent_jobs([lambda: 1, lambda: 2])

        result = self._run_case(test_method)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(captured['run'].results, [1, 2])

    def test_mixin_can_tolerate_expected_worker_errors(self):
        captured = {}

        def test_method(case):
            def failing():
                raise RuntimeError('tolerated')

            captured['run'] = case.run_concurrent_jobs(
                [lambda: 'ok', failing], allow_errors=True,
            )

        result = self._run_case(test_method)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(captured['run'].results, ['ok'])
        self.assertEqual(len(captured['run'].errors), 1)
