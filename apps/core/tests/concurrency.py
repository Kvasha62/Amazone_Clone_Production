# ────────────────────────────────────────────────────────────────────────
# apps/core/tests/concurrency.py
#
# PROD-015 — ОГРАНИЧЕННЫЙ ПО ВРЕМЕНИ запуск cross-connection тестов
# с ГАРАНТИРОВАННЫМ освобождением тестовой БД.
#
# ── Почему не ThreadPoolExecutor ────────────────────────────────────────
#   with ThreadPoolExecutor(...) as executor:
#       future.result(timeout=30)
#   • future.result(timeout=...) ограничивает ТОЛЬКО локальное ожидание;
#   • выход из `with` вызывает shutdown(wait=True) — ожидание живых
#     воркеров БЕЗ таймаута;
#   • воркеры executor'а НЕ daemon, а concurrent.futures.thread
#     регистрирует atexit-хук, который джойнит их при завершении
#     интерпретатора → shutdown(wait=False) лишь ПЕРЕНОСИТ
#     неограниченное ожидание на выход из процесса.
#
# ── Почему daemon=True + finally: close_all() НЕДОСТАТОЧНО ──────────────
#   Python-поток нельзя безопасно убить. Если воркер завис ВНУТРИ
#   DB-операции или внутри открытой транзакции, его `finally` ещё НЕ
#   выполнен, а значит:
#     • серверная сессия PostgreSQL жива;
#     • её транзакция открыта и держит row locks;
#   и последующий teardown Django (TRUNCATE в TransactionTestCase,
#   DROP DATABASE в конце прогона) блокируется на этих локах/сессии —
#   неограниченное ожидание просто переезжает в teardown.
#
# ── Механизм, который это закрывает ─────────────────────────────────────
#   КЛЮЧЕВОЕ НАБЛЮДЕНИЕ: ресурс, удерживающий тестовую БД, живёт НЕ в
#   Python-потоке, а в backend-процессе PostgreSQL. Его можно завершить
#   ИЗВНЕ — из главного потока, не трогая зависший Python-поток.
#
#   Три слоя, все с ограниченным временем:
#
#   1. ПРЕВЕНЦИЯ (server-side timeouts).
#      На КАЖДОМ соединении воркера (сигнал connection_created) ставятся
#      statement_timeout, lock_timeout и idle_in_transaction_session_
#      timeout ≈ 80% от таймаута прогона. Тогда:
#        • зависший запрос/ожидание лока прерывается СЕРВЕРОМ →
#          воркер получает исключение, разворачивается и доходит до
#          `finally` штатно;
#        • воркер, зависший в Python-коде ВНУТРИ открытой транзакции
#          (там statement_timeout бессилен), убивается
#          idle_in_transaction_session_timeout — PostgreSQL завершает
#          сессию и откатывает транзакцию, освобождая локи.
#
#   2. РЕГИСТРАЦИЯ + РЕАПЕР (гарантия, а не надежда).
#      В момент создания соединения воркера запоминается его
#      pg_backend_pid(). После истечения дедлайна join'а главный поток
#      на ОТДЕЛЬНОМ соединении делает pg_terminate_backend() ровно для
#      этих PID'ов (никакого collateral damage по чужим сессиям).
#      PostgreSQL при этом ОТКАТЫВАЕТ транзакцию backend'а и снимает
#      все её локи — независимо от того, дойдёт ли когда-нибудь
#      Python-поток до своего `finally`.
#
#   3. ВЕРИФИКАЦИЯ (bounded).
#      Реапер ОГРАНИЧЕННО ждёт (TERMINATION_WAIT), пока PID'ы исчезнут
#      из pg_stat_activity, и фиксирует результат в отчёте. Только
#      после этого тест падает — то есть к моменту teardown ни одна
#      сессия воркера не жива.
#
#   Ни один из шагов не вводит неограниченного ожидания:
#     • потоки daemon → интерпретатор их не джойнит (нет atexit-join);
#     • join — по ОДНОМУ общему дедлайну;
#     • grace-join после реапинга ограничен GRACE_JOIN;
#     • ожидание исчезновения backend'ов ограничено TERMINATION_WAIT;
#     • сам реапер работает на отдельном соединении с собственными
#       statement_timeout/lock_timeout.
#
# Диагностика сохраняется: имя потока, его Python-стек, а также
# state/wait_event/query зависшей серверной сессии (снимаются ДО
# завершения backend'а).
#
# Модуль намеренно test-only: production-логика не затрагивается.
# ────────────────────────────────────────────────────────────────────────

import sys
import threading
import traceback
from dataclasses import dataclass, field
from time import monotonic, sleep

from django.db import DEFAULT_DB_ALIAS, connections
from django.db.backends.signals import connection_created

# Значения по умолчанию — консервативные: ожидание всегда ограничено.
DEFAULT_JOB_TIMEOUT = 30
DEFAULT_BARRIER_TIMEOUT = 10

#: Доля таймаута прогона, после которой сервер сам прерывает запрос
#: воркера (должна быть < 1, чтобы воркер успел развернуться штатно).
SERVER_TIMEOUT_RATIO = 0.8
#: Нижняя граница server-side таймаутов (сек) — защита от 0 мс.
MIN_SERVER_TIMEOUT = 0.5
#: Ограниченное ожидание исчезновения terminated backend'ов (сек).
TERMINATION_WAIT = 10.0
#: Ограниченный grace-join после реапинга: воркер должен развернуться.
GRACE_JOIN = 5.0
#: Шаг опроса pg_stat_activity.
_POLL_INTERVAL = 0.05

#: Маркер «job не вернул значение» (упал или не завершился).
_MISSING = object()


def _is_postgres(connection) -> bool:
    return getattr(connection, 'vendor', None) == 'postgresql'


@dataclass
class BackendSnapshot:
    """Состояние серверной сессии воркера на момент таймаута."""

    pid: int
    thread: str
    state: str = ''
    wait_event_type: str = ''
    wait_event: str = ''
    query: str = ''

    def describe(self) -> str:
        wait = '/'.join(part for part in (self.wait_event_type, self.wait_event) if part)
        return (
            f'pid={self.pid} ({self.thread}) state={self.state or "?"}'
            f' wait={wait or "-"} query={self.query.strip()[:200] or "-"}'
        )


@dataclass
class TerminationReport:
    """
    Итог принудительного освобождения тестовой БД.

    requested   — PID'ы воркеров, которые были ещё живы после дедлайна;
    snapshots   — их состояние ДО termination (диагностика);
    terminated  — PID'ы, для которых pg_terminate_backend() вернул true;
    still_alive — PID'ы, не исчезнувшие за TERMINATION_WAIT (ЧП);
    error       — текст ошибки, если реапер не смог отработать.
    """

    requested: list = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    terminated: list = field(default_factory=list)
    still_alive: list = field(default_factory=list)
    error: str = ''
    elapsed: float = 0.0

    @property
    def performed(self) -> bool:
        return bool(self.requested) or bool(self.error)

    @property
    def db_released(self) -> bool:
        """Тестовая БД гарантированно свободна от сессий воркеров."""
        return not self.still_alive and not self.error

    def describe(self) -> str:
        if not self.performed:
            return 'Серверные сессии воркеров: не осталось (реапинг не требовался).'
        lines = [
            'Принудительное освобождение тестовой БД '
            f'({self.elapsed:.2f}s): terminated={self.terminated or "-"}, '
            f'still_alive={self.still_alive or "-"}.'
        ]
        for snapshot in self.snapshots:
            lines.append(f'  • {snapshot.describe()}')
        if self.error:
            lines.append(f'  ! реапер не отработал: {self.error}')
        return '\n'.join(lines)


@dataclass
class ConcurrentRun:
    """
    Итог ограниченного по времени конкурентного прогона.

    results         — значения успешных job'ов В ПОРЯДКЕ передачи;
    errors          — список (index, exception) для упавших job'ов;
    stuck           — имена потоков, не завершившихся К ДЕДЛАЙНУ
                      (именно это и есть факт timeout'а — он НЕ
                      «отменяется» тем, что поток развернулся позже);
    still_alive     — из них те, кто жив и после реапинга и grace-join;
    stuck_stacks    — Python-стек каждого зависшего потока (на дедлайне);
    worker_backends — {pid: имя потока} по всем соединениям воркеров;
    termination     — отчёт реапера серверных сессий;
    elapsed         — фактическое время ожидания потоков.
    """

    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    stuck: list = field(default_factory=list)
    still_alive: list = field(default_factory=list)
    stuck_stacks: dict = field(default_factory=dict)
    worker_backends: dict = field(default_factory=dict)
    termination: TerminationReport = field(default_factory=TerminationReport)
    elapsed: float = 0.0

    @property
    def timed_out(self) -> bool:
        return bool(self.stuck)

    @property
    def db_released(self) -> bool:
        return self.termination.db_released

    def stuck_report(self) -> str:
        """Детерминированный человекочитаемый отчёт о зависших потоках."""
        if not self.stuck:
            return ''
        unwound = [name for name in self.stuck if name not in self.still_alive]
        lines = [
            'Конкурентные потоки не завершились за отведённое время '
            f'({self.elapsed:.1f}s): {", ".join(self.stuck)}.',
            'Тест остановлен принудительно (bounded failure): потоки '
            'daemon (интерпретатор их не ждёт), серверные сессии '
            'воркеров завершены — teardown БД от них не зависит.',
            f'После реапинга развернулись: {", ".join(unwound) or "-"}; '
            f'остались живы: {", ".join(self.still_alive) or "-"}.',
            self.termination.describe(),
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


class WorkerSessionRegistry:
    """
    Реестр серверных сессий воркеров + принудительное их завершение.

    Подписывается на connection_created и для КАЖДОГО соединения,
    созданного в потоке-воркере:
      • ставит server-side таймауты (превенция зависания);
      • запоминает pg_backend_pid() (адрес для реапера).
    """

    def __init__(self, threads, server_timeout, apply_server_timeouts=True):
        self._threads = set(threads)
        self._server_timeout = max(server_timeout, MIN_SERVER_TIMEOUT)
        self._apply_server_timeouts = apply_server_timeouts
        self._lock = threading.Lock()
        # pid -> (имя потока, backend_start). backend_start отсекает
        # редкий, но опасный случай ПЕРЕИСПОЛЬЗОВАНИЯ pid другим
        # backend'ом: реапер не должен убить чужую сессию.
        self._sessions = {}
        self._dispatch_uid = f'prod015-worker-sessions-{id(self)}'

    # ── жизненный цикл подписки ──────────────────────────────────────

    def __enter__(self):
        connection_created.connect(
            self._on_connection_created, dispatch_uid=self._dispatch_uid,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        connection_created.disconnect(dispatch_uid=self._dispatch_uid)
        return False

    @property
    def sessions(self):
        with self._lock:
            return dict(self._sessions)

    @property
    def pids(self):
        """{pid: имя потока} — зарегистрированные сессии воркеров."""
        return {pid: name for pid, (name, _) in self.sessions.items()}

    # ── регистрация соединения воркера ───────────────────────────────

    def _on_connection_created(self, sender=None, connection=None, **kwargs):
        thread = threading.current_thread()
        if thread not in self._threads or not _is_postgres(connection):
            return
        try:
            with connection.cursor() as cursor:
                if self._apply_server_timeouts:
                    self._apply_timeouts(cursor)
                cursor.execute(
                    'SELECT pid, backend_start FROM pg_stat_activity '
                    'WHERE pid = pg_backend_pid()'
                )
                pid, backend_start = cursor.fetchone()
        except Exception:  # noqa: BLE001 — инфраструктура теста не должна
            return        # ронять сам тест; отсутствие pid = нет реапинга
        with self._lock:
            self._sessions[pid] = (thread.name, backend_start)

    def _apply_timeouts(self, cursor):
        """
        Сервер сам прерывает воркер раньше дедлайна join'а.

        • statement_timeout — зависший/долгий запрос;
        • lock_timeout — ожидание row lock;
        • idle_in_transaction_session_timeout — воркер завис в
          Python-коде, УДЕРЖИВАЯ открытую транзакцию (именно этот
          случай не покрывается ни statement_timeout, ни `finally`).
        """
        millis = str(int(self._server_timeout * 1000))
        for parameter in (
            'statement_timeout',
            'lock_timeout',
            'idle_in_transaction_session_timeout',
        ):
            cursor.execute('SELECT set_config(%s, %s, false)', [parameter, millis])

    # ── принудительное освобождение БД ───────────────────────────────

    def terminate_live_sessions(self, wait=TERMINATION_WAIT, thread_names=None):
        """
        Завершает ещё живые серверные сессии воркеров и ОГРАНИЧЕННО
        ждёт их исчезновения. Возвращает TerminationReport.

        thread_names — если задано, реапятся только сессии этих
        потоков (обычно — реально зависших).

        Работает на ОТДЕЛЬНОМ соединении главного потока: состояние
        основного соединения теста не трогаем, а сам реапер не может
        зависнуть (у него собственные statement_timeout/lock_timeout).
        """
        report = TerminationReport()
        sessions = {
            pid: (name, backend_start)
            for pid, (name, backend_start) in self.sessions.items()
            if thread_names is None or name in thread_names
        }
        if not sessions:
            return report

        started = monotonic()
        admin = None
        try:
            admin = connections.create_connection(DEFAULT_DB_ALIAS)
            with admin.cursor() as cursor:
                cursor.execute(
                    'SELECT set_config(%s, %s, false)',
                    ['statement_timeout', str(int(wait * 1000))],
                )
                cursor.execute(
                    'SELECT set_config(%s, %s, false)',
                    ['lock_timeout', str(int(wait * 1000))],
                )
                alive = self._snapshot(cursor, sessions)
                report.requested = [snapshot.pid for snapshot in alive]
                report.snapshots = alive
                if not alive:
                    return report

                cursor.execute(
                    'SELECT pid, pg_terminate_backend(pid) '
                    'FROM pg_stat_activity '
                    'WHERE pid = ANY(%s) AND pid <> pg_backend_pid()',
                    [report.requested],
                )
                report.terminated = sorted(
                    pid for pid, terminated in cursor.fetchall() if terminated
                )

                deadline = monotonic() + wait
                remaining = self._alive_pids(cursor, report.requested)
                while remaining and monotonic() < deadline:
                    sleep(_POLL_INTERVAL)
                    remaining = self._alive_pids(cursor, report.requested)
                report.still_alive = sorted(remaining)
        except Exception as exc:  # noqa: BLE001 — сообщаем, а не падаем
            report.error = f'{type(exc).__name__}: {exc}'
        finally:
            if admin is not None:
                try:
                    admin.close()
                except Exception:  # noqa: BLE001
                    pass
            report.elapsed = monotonic() - started
        return report

    def _snapshot(self, cursor, sessions):
        """
        Живые сессии воркеров. Сверяем не только pid, но и
        backend_start: одинаковый pid с ДРУГИМ backend_start — это уже
        чужая сессия (pid переиспользован), её трогать нельзя.
        """
        cursor.execute(
            'SELECT pid, backend_start, state, wait_event_type, wait_event, query '
            'FROM pg_stat_activity '
            'WHERE pid = ANY(%s) AND pid <> pg_backend_pid()',
            [sorted(sessions)],
        )
        snapshots = []
        for pid, backend_start, state, wait_type, wait_event, query in cursor.fetchall():
            name, registered_start = sessions[pid]
            if registered_start is not None and backend_start != registered_start:
                continue  # pid переиспользован другим backend'ом
            snapshots.append(
                BackendSnapshot(
                    pid=pid,
                    thread=name,
                    state=state or '',
                    wait_event_type=wait_type or '',
                    wait_event=wait_event or '',
                    query=query or '',
                )
            )
        return sorted(snapshots, key=lambda snapshot: snapshot.pid)

    @staticmethod
    def _alive_pids(cursor, pids):
        cursor.execute(
            'SELECT pid FROM pg_stat_activity WHERE pid = ANY(%s)', [sorted(pids)],
        )
        return [row[0] for row in cursor.fetchall()]


def run_concurrent_jobs(
    jobs,
    *,
    timeout=DEFAULT_JOB_TIMEOUT,
    barrier_timeout=DEFAULT_BARRIER_TIMEOUT,
    pass_barrier=False,
    apply_server_timeouts=True,
    termination_wait=TERMINATION_WAIT,
    grace_join=GRACE_JOIN,
):
    """
    Запускает `jobs` одновременно (барьер старта) и ждёт их НЕ ДОЛЬШЕ
    `timeout` секунд СУММАРНО, после чего ГАРАНТИРОВАННО освобождает
    тестовую БД от сессий незавершившихся воркеров.

    jobs                  — callable без аргументов (или принимающие
                            barrier при pass_barrier=True);
    timeout               — общий дедлайн ожидания потоков;
    barrier_timeout       — предел ожидания на барьере старта;
    pass_barrier          — job сам синхронизируется на barrier;
    apply_server_timeouts — ставить ли server-side таймауты воркерам
                            (False используется только в regression-
                            тестах самого раннера, чтобы проверить
                            реапер в чистом виде);
    termination_wait      — предел ожидания исчезновения backend'ов;
    grace_join            — предел ожидания разворачивания воркера
                            после termination.

    Возвращает ConcurrentRun. Ассерты делает вызывающий тест
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
            # Достижимый путь выхода ЛЮБОГО рода — соединения закрыты.
            # Это «мягкий» путь; жёсткую гарантию даёт реапер ниже,
            # т.к. зависший поток сюда может не дойти вовсе.
            try:
                connections.close_all()
            except Exception:  # noqa: BLE001 — cleanup не маскирует ошибки
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

    registry = WorkerSessionRegistry(
        threads,
        server_timeout=timeout * SERVER_TIMEOUT_RATIO,
        apply_server_timeouts=apply_server_timeouts,
    )

    with registry:
        started = monotonic()
        for thread in threads:
            thread.start()

        # ОДИН общий дедлайн: суммарное ожидание ограничено `timeout`,
        # а не timeout × len(threads).
        deadline = started + timeout
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - monotonic()))
        elapsed = monotonic() - started

        stuck_threads = [thread for thread in threads if thread.is_alive()]
        stuck_stacks = {thread.name: _thread_stack(thread) for thread in stuck_threads}

        # ЖЁСТКАЯ гарантия: серверные сессии ЗАВИСШИХ воркеров (тех,
        # что не дошли до своего finally) завершаются здесь — ДО
        # возврата в тест и, значит, до teardown Django.
        # Завершившиеся воркеры уже закрыли свои соединения сами, их
        # pid'ы не трогаем (они могли быть переиспользованы).
        termination = TerminationReport()
        if stuck_threads:
            termination = registry.terminate_live_sessions(
                wait=termination_wait,
                thread_names={thread.name for thread in stuck_threads},
            )

        stuck = [thread.name for thread in stuck_threads]

        if stuck_threads and grace_join > 0:
            # Backend убит → блокирующий вызов в воркере отдаёт ошибку,
            # поток разворачивается и закрывает соединения. Ждём это
            # ОГРАНИЧЕННО: даже если не дождёмся, БД уже свободна.
            grace_deadline = monotonic() + grace_join
            for thread in stuck_threads:
                thread.join(timeout=max(0.0, grace_deadline - monotonic()))

        # ВАЖНО: `stuck` фиксируется НА ДЕДЛАЙНЕ и не пересчитывается.
        # Иначе воркер, развернувшийся после termination, «отменял» бы
        # факт таймаута, и тест ошибочно считался бы успешным.
        still_alive = [thread.name for thread in stuck_threads if thread.is_alive()]

        with lock:
            results = [value for value in slots if value is not _MISSING]
            collected_errors = list(errors)

        return ConcurrentRun(
            results=results,
            errors=collected_errors,
            stuck=stuck,
            still_alive=still_alive,
            stuck_stacks=stuck_stacks,
            worker_backends=registry.pids,
            termination=termination,
            elapsed=elapsed,
        )


class ConcurrentJobsMixin:
    """
    Миксин для TransactionTestCase: ограниченный по времени запуск
    конкурентных job'ов с детерминированным репортом отказа и
    гарантированным освобождением тестовой БД.

    Поведение:
      • зависший воркер → его серверная сессия завершается, тест
        ПАДАЕТ (fail) с именем потока, Python-стеком и состоянием
        серверной сессии; teardown БД от воркера не зависит;
      • исключение в воркере → тест падает с полным traceback'ом
        (если не передан allow_errors=True);
      • не удалось освободить БД → тест падает с явным диагнозом
        (лучше честный fail, чем зависший teardown);
      • возвращает ConcurrentRun с результатами успешных job'ов.
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
        **runner_kwargs,
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
            **runner_kwargs,
        )
        if run.timed_out:
            self.fail(run.stuck_report())
        if not run.db_released:
            self.fail(
                'Не удалось освободить тестовую БД от сессий воркеров:\n'
                + run.termination.describe()
            )
        if run.errors and not allow_errors:
            self.fail('Ошибки в конкурентных потоках:\n' + run.errors_report())
        return run
