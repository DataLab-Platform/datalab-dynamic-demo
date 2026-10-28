"""Process pool dedicated to the live update.

DataLab runs its processing in a single, module-wide process pool: terminating
it would interrupt a normal processing. The live update therefore uses a pool of
its own, with one process, created on demand. Computations go through DataLab's
``run_with_env``, which applies the Sigima options of the GUI process and returns
a ``CompOut`` holding the result, the error or the warning, as for a normal
processing.
"""

from __future__ import annotations

import multiprocessing
import threading
from collections.abc import Callable
from multiprocessing.pool import AsyncResult, Pool
from typing import Any

from datalab.gui.processor.base import run_with_env
from sigima.config import options as sigima_options

__all__ = ["DedicatedExecutor"]


def _terminate(pool: Pool) -> None:
    """Terminate a pool and wait for its process (worker thread)."""
    pool.terminate()
    pool.join()


class DedicatedExecutor:
    """One-process pool of the live update, independent of DataLab's pool.

    The process starts with :meth:`start` (or with the first submission): with the
    ``spawn`` start method, its start-up and the first imports take about a second.
    :meth:`shutdown` terminates it in a thread, so that the GUI is never blocked,
    even by a stuck computation.
    """

    def __init__(self) -> None:
        self._pool: Pool | None = None
        self._warmup: AsyncResult | None = None

    @property
    def is_running(self) -> bool:
        """True if the pool exists."""
        return self._pool is not None

    @property
    def is_ready(self) -> bool:
        """True if the pool process has started and loaded the modules."""
        return self._pool is not None and (self._warmup is None or self._warmup.ready())

    def start(self) -> None:
        """Create the pool, and load the computation modules in its process."""
        if self._pool is None:
            context = multiprocessing.get_context("spawn")
            # pylint: disable=consider-using-with
            self._pool = context.Pool(processes=1)
            env = sigima_options.get_env()
            self._warmup = self._pool.apply_async(run_with_env, (abs, (0,), env))

    def submit(self, func: Callable, args: tuple[Any, ...]) -> AsyncResult:
        """Submit a computation.

        Args:
            func: function, importable by the pool process
            args: picklable arguments

        Returns:
            Asynchronous result of a ``CompOut``
        """
        self.start()
        env = sigima_options.get_env()
        return self._pool.apply_async(run_with_env, (func, args, env))

    def shutdown(self) -> threading.Thread | None:
        """Terminate the pool without blocking the caller.

        Returns:
            Thread terminating the pool, or None if there was no pool
        """
        pool, self._pool = self._pool, None
        self._warmup = None
        if pool is None:
            return None
        thread = threading.Thread(
            target=_terminate, args=(pool,), name="live-update-pool", daemon=True
        )
        thread.start()
        return thread
