#!/usr/bin/env python3
"""
ASTREX v3.0 — Worker Pool
Пул процессов для обработки файлов.

В отличие от concurrent.futures.ProcessPoolExecutor:
- лимит времени на одну задачу (зависший файл убивается, скан продолжается);
- лимит RSS на воркер (процесс, превысивший лимит, перезапускается);
- падение воркера (OOM-killer, segfault в C-библиотеке) затрагивает только
  его текущий файл, а не весь пул;
- задачи выдаются по мере освобождения воркеров (нет очереди из 100 000
  задач в памяти и нет искусственных задержек);
- stop/terminate немедленно завершает все дочерние процессы; кроме того,
  воркеры получают SIGKILL при смерти родителя (PR_SET_PDEATHSIG), поэтому
  "сирот" после Ctrl+C / kill не остаётся.
- используется start method "forkserver"/"spawn": fork() из многопоточного
  процесса с открытыми SQLite-соединениями и CUDA-контекстом небезопасен.
"""

import contextlib
import itertools
import logging
import multiprocessing as mp
import signal
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import wait as mp_wait
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .resource_guard import get_process_rss_mb

logger = logging.getLogger("astrex.workerpool")

_CTX_LOCK = threading.Lock()
_CTX = None
_MAIN_LOCK = threading.Lock()


@contextlib.contextmanager
def _main_module_hidden():
    """Не передавать воркерам путь к главному модулю программы.

    multiprocessing (spawn/forkserver) импортирует __main__ в каждом дочернем
    процессе. Воркерам ASTREX он не нужен (их функции — в пакете core), а
    скрипт пользователя без защиты `if __name__ == "__main__":` выполнялся бы
    заново в каждом воркере; код из stdin/консоли ронял воркеры.
    """
    main = sys.modules.get('__main__')
    with _MAIN_LOCK:
        saved = {}
        if main is not None:
            if '__file__' in main.__dict__:
                saved['__file__'] = main.__dict__.pop('__file__')
            if getattr(main, '__spec__', None) is not None:
                saved['__spec__'] = main.__spec__
                main.__spec__ = None
        try:
            yield
        finally:
            if main is not None:
                main.__dict__.update(saved)


def get_mp_context():
    """Контекст multiprocessing: forkserver (Linux) или spawn."""
    global _CTX
    with _CTX_LOCK:
        if _CTX is None:
            methods = mp.get_all_start_methods()
            if sys.platform.startswith("linux") and "forkserver" in methods:
                ctx = mp.get_context("forkserver")
                try:
                    ctx.set_forkserver_preload(["core.engine"])
                except Exception:
                    pass
            else:
                ctx = mp.get_context("spawn")
            _CTX = ctx
        return _CTX


def _set_parent_death_signal() -> None:
    """Linux: получить SIGKILL при завершении родительского процесса."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass


def _worker_main(conn, func, initializer, initargs) -> None:
    """Цикл воркера: получить задачу → выполнить → отправить результат."""
    _set_parent_death_signal()
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ctrl+C обрабатывает родитель
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except (ValueError, OSError):
        pass

    if initializer is not None:
        try:
            initializer(*initargs)
        except Exception as e:  # воркер всё равно может работать
            try:
                sys.stderr.write(f"astrex worker initializer failed: {e}\n")
            except Exception:
                pass

    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError, KeyboardInterrupt):
            break
        if message is None:
            break
        task_id, args = message
        try:
            result = func(*args)
            conn.send((task_id, True, result))
        except BaseException as e:  # MemoryError, RecursionError и т.п.
            try:
                conn.send((task_id, False, f"{type(e).__name__}: {e}"))
            except Exception:
                break
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                break
    try:
        conn.close()
    except Exception:
        pass


@dataclass
class TaskResult:
    """Результат одной задачи."""
    key: Any
    ok: bool
    value: Any = None
    error: Optional[str] = None
    kind: str = "ok"          # ok | error | timeout | crash | memory
    duration: float = 0.0


class _Worker:
    __slots__ = ("process", "conn", "task_id", "key", "args", "started", "retries")

    def __init__(self, process, conn):
        self.process = process
        self.conn = conn
        self.task_id: Optional[int] = None
        self.key: Any = None
        self.args: Any = None
        self.started = 0.0
        self.retries = 0

    @property
    def busy(self) -> bool:
        return self.task_id is not None


class WorkerPool:
    """Пул процессов с таймаутами, лимитом памяти и жёсткой остановкой."""

    def __init__(
        self,
        func: Callable,
        n_workers: int,
        initializer: Optional[Callable] = None,
        initargs: tuple = (),
        task_timeout: float = 0,
        max_rss_mb: float = 0,
        ctx=None,
    ):
        self.func = func
        self.n_workers = max(1, int(n_workers))
        self.initializer = initializer
        self.initargs = initargs
        self.task_timeout = float(task_timeout or 0)
        self.max_rss_mb = float(max_rss_mb or 0)
        self.ctx = ctx or get_mp_context()
        self._workers: List[_Worker] = []
        self._task_ids = itertools.count(1)
        self._closed = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.terminate()

    def _spawn(self) -> _Worker:
        parent_conn, child_conn = self.ctx.Pipe(duplex=True)
        process = self.ctx.Process(
            target=_worker_main,
            args=(child_conn, self.func, self.initializer, self.initargs),
            daemon=True,
            name="astrex-worker",
        )
        with _main_module_hidden():
            process.start()
        child_conn.close()
        worker = _Worker(process, parent_conn)
        self._workers.append(worker)
        return worker

    def _discard(self, worker: _Worker, kill: bool = True) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        try:
            worker.conn.close()
        except Exception:
            pass
        if kill and worker.process.is_alive():
            try:
                worker.process.kill()
            except Exception:
                pass
        try:
            worker.process.join(timeout=2)
        except Exception:
            pass

    def close(self) -> None:
        """Штатно завершить воркеры (после обработки всех задач)."""
        if self._closed:
            return
        self._closed = True
        for w in list(self._workers):
            try:
                w.conn.send(None)
            except Exception:
                pass
        deadline = time.monotonic() + 5
        for w in list(self._workers):
            try:
                w.process.join(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        self.terminate()

    def terminate(self) -> None:
        """Немедленно завершить все воркеры."""
        self._closed = True
        workers = list(self._workers)
        for w in workers:
            if w.process.is_alive():
                try:
                    w.process.terminate()
                except Exception:
                    pass
        deadline = time.monotonic() + 2
        for w in workers:
            try:
                w.process.join(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
            if w.process.is_alive():
                try:
                    w.process.kill()
                    w.process.join(timeout=1)
                except Exception:
                    pass
            try:
                w.conn.close()
            except Exception:
                pass
        self._workers.clear()

    def __del__(self):
        try:
            self.terminate()
        except Exception:
            pass

    # ── execution ────────────────────────────────────────────────────────────

    @property
    def busy_count(self) -> int:
        return sum(1 for w in self._workers if w.busy)

    def _assign(self, worker: _Worker, key: Any, args: tuple) -> bool:
        task_id = next(self._task_ids)
        try:
            worker.conn.send((task_id, args))
        except (BrokenPipeError, OSError, EOFError):
            return False
        worker.task_id = task_id
        worker.key = key
        worker.args = args
        worker.started = time.monotonic()
        return True

    def _finish(self, worker: _Worker) -> Tuple[Any, float]:
        key = worker.key
        duration = time.monotonic() - worker.started
        worker.task_id = None
        worker.key = None
        worker.args = None
        return key, duration

    def run(
        self,
        tasks: Iterable[Tuple[Any, tuple]],
        should_stop: Callable[[], bool] = lambda: False,
        can_submit: Callable[["WorkerPool"], bool] = lambda pool: True,
        poll_interval: float = 0.25,
    ) -> Iterator[TaskResult]:
        """Выполнить задачи и выдавать результаты по мере готовности.

        Args:
            tasks: итерируемое (key, args) — args передаются в func(*args)
            should_stop: проверка остановки (True — прекратить, воркеры убиваются)
            can_submit: можно ли выдать новую задачу (например, RAM в норме)
        """
        task_iter = iter(tasks)
        exhausted = False
        pending_retry: List[Tuple[Any, tuple, int]] = []
        last_rss_check = 0.0

        while len(self._workers) < self.n_workers:
            self._spawn()

        try:
            while True:
                if should_stop():
                    self.terminate()
                    return

                # 1. Выдать задачи свободным воркерам
                for worker in list(self._workers):
                    if worker.busy:
                        continue
                    if not can_submit(self):
                        break
                    if pending_retry:
                        key, args, retries = pending_retry.pop()
                    elif not exhausted:
                        try:
                            key, args = next(task_iter)
                            retries = 0
                        except StopIteration:
                            exhausted = True
                            break
                    else:
                        break
                    if not self._assign(worker, key, args):
                        # Воркер умер до получения задачи — заменить и повторить
                        self._discard(worker)
                        self._spawn()
                        if retries < 2:
                            pending_retry.append((key, args, retries + 1))
                        else:
                            yield TaskResult(key, False, error="worker unavailable", kind="crash")
                    else:
                        worker.retries = retries

                busy = [w for w in self._workers if w.busy]
                if exhausted and not pending_retry and not busy:
                    return
                if not busy:
                    time.sleep(poll_interval)  # ждём can_submit (например, RAM)
                    continue

                # 2. Дождаться результатов
                waitables: Dict[Any, _Worker] = {}
                for w in busy:
                    waitables[w.conn] = w
                    waitables[w.process.sentinel] = w
                ready = mp_wait(list(waitables.keys()), timeout=poll_interval)

                handled = set()
                for obj in ready:
                    worker = waitables[obj]
                    if id(worker) in handled or not worker.busy:
                        continue
                    handled.add(id(worker))
                    try:
                        if worker.conn.poll():
                            task_id, ok, payload = worker.conn.recv()
                            if task_id != worker.task_id:
                                continue
                            key, duration = self._finish(worker)
                            if ok:
                                yield TaskResult(key, True, value=payload, duration=duration)
                            else:
                                yield TaskResult(key, False, error=str(payload), kind="error",
                                                 duration=duration)
                            continue
                    except (EOFError, OSError):
                        pass
                    # Процесс умер во время задачи
                    exitcode = worker.process.exitcode
                    if exitcode is None:
                        worker.process.join(timeout=0.5)
                        exitcode = worker.process.exitcode
                    key, duration = self._finish(worker)
                    self._discard(worker)
                    self._spawn()
                    if exitcode == -signal.SIGKILL:
                        reason = "worker killed (SIGKILL — probably out of memory)"
                        kind = "memory"
                    else:
                        reason = f"worker crashed (exit code {exitcode})"
                        kind = "crash"
                    yield TaskResult(key, False, error=reason, kind=kind, duration=duration)

                # 3. Таймауты и лимит памяти
                now = time.monotonic()
                check_rss = self.max_rss_mb > 0 and now - last_rss_check >= 1.0
                if check_rss:
                    last_rss_check = now
                for worker in list(self._workers):
                    if not worker.busy:
                        continue
                    if self.task_timeout and now - worker.started > self.task_timeout:
                        key, duration = self._finish(worker)
                        self._discard(worker)
                        self._spawn()
                        yield TaskResult(key, False, kind="timeout", duration=duration,
                                         error=f"timeout: processing took longer than {self.task_timeout:.0f}s")
                        continue
                    if check_rss:
                        rss = get_process_rss_mb(worker.process.pid)
                        if rss > self.max_rss_mb:
                            key, duration = self._finish(worker)
                            self._discard(worker)
                            self._spawn()
                            yield TaskResult(key, False, kind="memory", duration=duration,
                                             error=f"memory limit exceeded ({rss:.0f} MB > {self.max_rss_mb:.0f} MB)")
        except BaseException:
            self.terminate()
            raise


__all__ = ['WorkerPool', 'TaskResult', 'get_mp_context']
