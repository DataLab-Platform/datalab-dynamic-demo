"""Tests of the process pool dedicated to the live update."""

from __future__ import annotations

import os
import time

import datalab.gui.processor.base as processor_base
import numpy as np
import psutil
import pytest
import sigima.proc.signal as sips
from datalab.env import execenv
from sigima.objects import create_signal
from sigima.params import GaussianParam

from datalab_dynamic_demo.adapters.executor import DedicatedExecutor
from datalab_dynamic_demo.core.source import SourceSettings, make_spectrum

#: Maximum time to wait for the pool (s): its process starts with ``spawn``
TIMEOUT = 60.0


@pytest.fixture(name="executor")
def fixture_executor():
    """Return a started executor, shut down after the test."""
    executor = DedicatedExecutor()
    executor.start()
    yield executor
    thread = executor.shutdown()
    if thread is not None:
        thread.join(TIMEOUT)


def test_result_equals_direct_call(executor: DedicatedExecutor) -> None:
    """The Gaussian filter gives the same result in the pool as in process."""
    x, y = make_spectrum(SourceSettings(), 1)
    signal = create_signal("Spectrum", x, y)
    param = GaussianParam.create(sigma=4.0)
    out = executor.submit(sips.gaussian_filter, (signal, param)).get(TIMEOUT)
    assert out.error_msg is None
    expected = sips.gaussian_filter(signal, param)
    np.testing.assert_allclose(out.result.xydata, expected.xydata)


def test_errors_are_returned() -> None:
    """A failing computation returns an error message, the pool survives."""
    # In unattended mode, DataLab raises computation errors, except when testing
    # its catcher; the mode is read by the pool process when it starts
    with execenv.context(catcher_test=True):
        executor = DedicatedExecutor()
        try:
            out = executor.submit(sips.gaussian_filter, (None, None)).get(TIMEOUT)
            assert out.result is None and out.error_msg
            assert executor.submit(abs, (-2,)).get(TIMEOUT).result == 2
        finally:
            executor.shutdown().join(TIMEOUT)


def test_shutdown_interrupts_and_frees_the_process() -> None:
    """Shutting down terminates a stuck computation and its process."""
    pool_before = processor_base.POOL
    executor = DedicatedExecutor()
    pid = executor.submit(os.getpid, ()).get(TIMEOUT).result
    assert pid != os.getpid() and psutil.pid_exists(pid)
    stuck = executor.submit(time.sleep, (30.0,))
    started = time.perf_counter()
    thread = executor.shutdown()
    assert time.perf_counter() - started < 1.0
    assert not executor.is_running
    thread.join(TIMEOUT)
    assert not thread.is_alive()
    assert not stuck.ready()
    # Windows ends the process shortly after the pool is joined
    try:
        psutil.Process(pid).wait(TIMEOUT)
    except psutil.NoSuchProcess:
        pass
    assert not psutil.pid_exists(pid)
    # DataLab's own pool is not touched
    assert processor_base.POOL is pool_before
    # A new pool is created on demand
    assert executor.submit(abs, (-3,)).get(TIMEOUT).result == 3
    executor.shutdown().join(TIMEOUT)
