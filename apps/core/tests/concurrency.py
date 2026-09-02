# ────────────────────────────────────────────────────────────────────────
# apps/core/tests/concurrency.py
#
# PROD-015 — ОГРАНИЧЕННЫЙ ПО ВРЕМЕНИ запуск конкурентных тестов.
#
# ПРОБЛЕМА (test-design defect):
#   Прежние cross-connection тесты reviews/discounts использовали
#       with ThreadPoolExecutor(...) as executor:
#           ...
#           future.result(timeout=30)
#   `future.result(timeout=...)` ограничивает ТОЛЬКО локальное ожидание
#   результата. Выход из `with` вызывает executor.shutdown(wait=True),
#   который ждёт живые воркеры БЕЗ таймаута. Если воркер завис
#   (например, на блокировке строки в PostgreSQL), тест-процесс Django
#   зависает уже в teardown executor'а — без диагностики и без предела.
#   Дополнительно: worker-потоки ThreadPoolExecutor НЕ daemon, а
#   concurrent.futures.thread регистрирует atexit-хук, который джойнит
#   их при завершении интерпретатора — то есть даже shutdown(wait=False)
#   всего лишь ПЕРЕНОСИТ неограниченное ожидание на выход из процесса.
#
# РЕШЕНИЕ:
#   Никакого ThreadPoolExecutor. Используется тот же приём, что уже
#   принят в apps/orders/tests/test_order_number_concurrency.py и
#   apps/pricing/tests/test_services.py:
#     • daemon-потоки (не блокируют завершение интерпретатора);
#     • join по ОБЩЕМУ дедлайну (суммарное ожидание ≤ timeout);
#     • зависшие потоки детектируются и репортятся детерминированно
#       (имя потока + стек), тест падает, а не висит;
#     • каждый воркер закрывает свои DB-соединения в finally на ВСЕХ
#       достижимых путях выхода (успех / исключение / BrokenBarrier),
#       иначе сессии держат тестовую БД и teardown падает с
#       «database is being accessed by other users».
#
# Модуль намеренно test-only: production-логика не затрагивается.
# ────────────────────────────────────────────────────────────────────────

import sys
import threading
import traceback
from dataclasses import dataclass, field
from time import monotonic

from django.db import connections

# Значения по умолчанию — консервативные: ожидание всегда ограничено.
DEFAULT_JOB_TIMEOUT = 30
DEFAULT_BARRIER_TIMEOUT = 10

#: Маркер «job не вернул значение» (упал или не завершился).
_MISSING = object()


@dataclass
class ConcurrentRun:
    """
    Итог ограниченного по времени конкурентного прогона.

    results       — значения успешных job'ов В ПОРЯДКЕ их передачи;
    errors        — список (index, exception) для упавших job'ов;
    stuck         — имена потоков, НЕ завершившихся за отведённый срок;
    stuck_stacks  — стек каждого зависшего потока (диагностика);
    elapsed       — фактическое время ожидания завершения потоков.
    """

    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    stuck: list = field(default_factory=list)
    stuck_stacks: dict = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def timed_out(self) -> bool:
        return bool(self.stuck)

    def stuck_report(self) -> str:
        """Детерминированный человекочитаемый отчёт о зависших потоках."""
        if not self.stuck:
            return ''
        lines = [
            'Конкурентные потоки не завершились за отведённое время '
            f'({self.elapsed:.1f}s): {", ".join(self.stuck)}.',
            'Тест остановлен принудительно (bounded failure), '
            'потоки daemon — интерпретатор их не ждёт.',
        ]
        for name in self.stuck:
            lines.append(f'--- стек потока {name} ---')
            lines.append(self.stuck_stacks.get(name, '<стек недоступен>'))
        return '\n'.join(lines)

    def errors_report(self) -> str:
        if not self.errors:
            return ''
        parts = []
        for index, exc in self.errors:
            formatted = ''.join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
            parts.append(f'--- job #{index} — {exc!r} ---\n{formatted}')
        return '\n'.join(parts)


def _thread_stack(thread) -> str:
    """Снимок стека живого потока (для диагностики зависания)."""
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return '<стек недоступен>'
    return ''.join(traceback.format_stack(frame)).rstrip()


def run_concurrent_jobs(
    jobs,
    *,
    timeout=DEFAULT_JOB_TIMEOUT,
    barrier_timeout=DEFAULT_BARRIER_TIMEOUT,
    pass_barrier=False,
):
    """
    Запускает `jobs` одновременно (барьер старта) и ждёт их НЕ ДОЛЬШЕ
    `timeout` секунд СУММАРНО. Никогда не блокируется бесконечно.

    jobs            — последовательность callable без аргументов
                      (или принимающих barrier, если pass_barrier=True);
    timeout         — общий предел ожидания завершения всех потоков;
    barrier_timeout — предел ожидания на барьере старта (внутри воркера);
    pass_barrier    — передавать barrier в job вместо ожидания внутри
                      раннера (для job'ов, которые сами синхронизируются).

    Возвращает ConcurrentRun. Проверку исходов делает вызывающий тест
    (см. ConcurrentJobsMixin) — раннер сам ничего не ассертит.
    """
    jobs = list(jobs)
    if not jobs:
        return ConcurrentRun()

    barrier = threading.Barrier(len(jobs))
    lock = threading.Lock()
    slots = [_MISSING] * len(jobs)
    errors = []

    def runner(index, fn):
        try:
            # Собственное соединение на поток: сбрасываем унаследованное.
            connections.close_all()
            if pass_barrier:
                value = fn(barrier)
            else:
                barrier.wait(timeout=barrier_timeout)
                value = fn()
        except BaseException as exc:  # noqa: BLE001 — собираем для assert
            with lock:
                errors.append((index, exc))
        else:
            with lock:
                slots[index] = value
        finally:
            # Достижимый путь выхода ЛЮБОГО рода — соединения закрыты,
            # иначе PostgreSQL-сессии переживут поток и заблокируют
            # DROP DATABASE в teardown.
            try:
                connections.close_all()
            except Exception:  # noqa: BLE001 — cleanup не должен маскировать
                pass

    threads = [
        threading.Thread(
            target=runner,
            args=(index, fn),
            name=f'concurrent-job-{index}',
            daemon=True,
        )
        for index, fn in enumerate(jobs)
    ]

    started = monotonic()
    for thread in threads:
        thread.start()

    # ОДИН общий дедлайн: суммарное ожидание ограничено `timeout`,
    # а не timeout × len(threads).
    deadline = started + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - monotonic()))
    elapsed = monotonic() - started

    stuck = [thread.name for thread in threads if thread.is_alive()]
    stuck_stacks = {
        thread.name: _thread_stack(thread)
        for thread in threads
        if thread.is_alive()
    }

    with lock:
        results = [value for value in slots if value is not _MISSING]
        collected_errors = list(errors)

    return ConcurrentRun(
        results=results,
        errors=collected_errors,
        stuck=stuck,
        stuck_stacks=stuck_stacks,
        elapsed=elapsed,
    )


class ConcurrentJobsMixin:
    """
    Миксин для TransactionTestCase: ограниченный по времени запуск
    конкурентных job'ов с детерминированным репортом отказа.

    Поведение:
      • зависший воркер → тест ПАДАЕТ (fail) с именем потока и стеком,
        процесс не остаётся ждать teardown'а executor'а;
      • исключение в воркере → тест падает с полным traceback'ом
        (если не передан allow_errors=True);
      • возвращает результаты успешных job'ов в порядке передачи.
    """

    concurrency_timeout = DEFAULT_JOB_TIMEOUT
    concurrency_barrier_timeout = DEFAULT_BARRIER_TIMEOUT

    def run_concurrent_jobs(
        self,
        jobs,
        *,
        timeout=None,
        barrier_timeout=None,
        pass_barrier=False,
        allow_errors=False,
    ):
        run = run_concurrent_jobs(
            jobs,
            timeout=self.concurrency_timeout if timeout is None else timeout,
            barrier_timeout=(
                self.concurrency_barrier_timeout
                if barrier_timeout is None
                else barrier_timeout
            ),
            pass_barrier=pass_barrier,
        )
        if run.timed_out:
            self.fail(run.stuck_report())
        if run.errors and not allow_errors:
            self.fail(
                'Ошибки в конкурентных потоках:\n' + run.errors_report()
            )
        return run
