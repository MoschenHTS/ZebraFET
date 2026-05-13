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
from typing import Any, Callable

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

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> Worker:
        """
        Schedule fn(*args, **kwargs) for execution in the thread pool.

        Returns the Worker so callers can connect to its signals before the
        pool picks it up (signals are connected synchronously before start).
        """
        worker = Worker(fn, *args, **kwargs)
        self._pool.start(worker)
        return worker

    @property
    def active_thread_count(self) -> int:
        return self._pool.activeThreadCount()
