"""
task_manager.py — Application-wide QThreadPool singleton.

Wraps any callable in a QRunnable Worker with typed signals (result, error,
finished, progress).  Short-lived, fire-and-forget I/O tasks (photo copying,
suggestion calculation, photo removal) should be submitted here.

Long-running operations that require cooperative cancellation (the analysis
pipeline, DOCX generation) should instead use dedicated QThread instances with
their own _cancelled flag.

Usage::

    from src.core.task_manager import TaskManager

    worker = TaskManager.instance().submit(my_function, arg1, arg2)
    worker.signals.result.connect(handle_result)
    worker.signals.error.connect(handle_error)
"""

import logging
from typing import Any, Callable, Dict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    """Signals emitted by Worker.  Must live on a QObject (not QRunnable)."""
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(int)


class Worker(QRunnable):
    """Runs a callable in the thread pool and emits typed signals."""

    def __init__(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        # Qt owns the QRunnable and disposes of it once run() returns. What must
        # outlive the task is self.signals, not this object; TaskManager retains
        # that separately (see TaskManager.submit).
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as exc:
            log.error(f"TaskManager worker raised: {exc}", exc_info=True)
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class TaskManager:
    """
    Singleton wrapper around QThreadPool.globalInstance().

    The pool thread count matches the number of logical CPU cores (Qt default).
    """

    _instance: "TaskManager | None" = None

    @classmethod
    def instance(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._pool = QThreadPool.globalInstance()
        #: _WorkerSignals objects for tasks that have been started but whose
        #: signals have not yet been delivered, keyed by id().  See submit().
        self._in_flight: Dict[int, _WorkerSignals] = {}
        self._connections: Dict[int, Any] = {}

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> Worker:
        """
        Schedule fn(*args, **kwargs) for execution in the thread pool.

        Returns the Worker so callers can connect to its signals before the
        pool picks it up (signals are connected synchronously before start).

        The Worker's signals object is retained here for the duration of the
        task. Emitting a signal from a pool thread to a main-thread slot posts a
        queued event that the main thread delivers later; if the caller's
        reference to the Worker is the only one and it goes out of scope in the
        meantime (as a `worker = submit(...)` local naturally does), Python may
        destroy the Worker — and with it the _WorkerSignals QObject — while that
        delivery is still pending. The main thread then dereferences a freed
        receiver inside QObject::event and the process dies with SIGSEGV,
        intermittently and far from the code that caused it.

        Only the signals object is retained, never the Worker: QThreadPool takes
        ownership of the QRunnable, so holding a Python reference to it past
        run() would keep it alive for the lifetime of the process.
        """
        worker = Worker(fn, *args, **kwargs)
        key = id(worker.signals)
        self._in_flight[key] = worker.signals
        # finished is emitted after result/error, and all three are queued to
        # the main thread in emission order, so retiring on finished cannot
        # release the signals object before the caller's own handlers have run.
        # The callback closes over the integer key rather than the signals
        # object, so it does not form a reference cycle through the connection.
        self._connections[key] = worker.signals.finished.connect(
            lambda k=key: self._retire(k)
        )
        self._pool.start(worker)
        return worker

    def _retire(self, key: int) -> None:
        """Release a finished task's signals.  Runs on the main thread (queued)."""
        signals = self._in_flight.pop(key, None)
        connection = self._connections.pop(key, None)
        # Drop this manager's own connection so the retained signals object is
        # left with no references from here; the caller's connections are
        # untouched.
        if signals is not None and connection is not None:
            try:
                signals.finished.disconnect(connection)
            except (RuntimeError, TypeError):
                pass

    @property
    def active_thread_count(self) -> int:
        return self._pool.activeThreadCount()
