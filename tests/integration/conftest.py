"""Configuration of the integration tests, as for DataLab's own tests."""

from __future__ import annotations

import gc
import os

import pytest
from datalab.config import Conf, ensure_initialized, reset_to_defaults
from datalab.env import execenv
from datalab.plugins import PluginRegistry
from datalab.tests import close_datalab_background
from qtpy import QtCore as QC

# Tests run without user interaction and without showing windows
execenv.unattended = True
execenv.verbose = "quiet"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def reset_datalab_configuration():
    """Start every test with DataLab defaults, without user configuration."""
    ensure_initialized(load_user_config=False)
    reset_to_defaults()
    Conf.rpc_server_enabled.set(False)
    Conf.faulthandler_enabled.set(False)
    try:
        yield
    finally:
        close_datalab_background()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):  # pylint: disable=unused-argument
    """Unregister the plugins and destroy the widgets of the test."""
    PluginRegistry.unregister_all_plugins()
    if QC.QCoreApplication.instance() is not None:
        QC.QCoreApplication.processEvents()
        QC.QCoreApplication.sendPostedEvents(None, QC.QEvent.DeferredDelete)
    gc.collect()
