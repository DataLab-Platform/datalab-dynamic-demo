"""Integration tests of the live update in DataLab."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

import numpy as np
import psutil
import pytest
import sigima.proc.signal as sips
from datalab.config import Conf
from datalab.gui.actionhandler import ActionCategory
from datalab.gui.main import DLMainWindow
from datalab.gui.processor.base import extract_processing_parameters
from datalab.objectmodel import get_uuid, patch_title_with_ids
from datalab.plugins import PluginRegistry
from datalab.tests import datalab_test_app_context
from qtpy import QtWidgets as QW
from sigima.objects import SignalObj
from sigima.objects.source import get_experimental_source
from sigima.params import GaussianParam

from datalab_dynamic_demo import PLUGIN_ID, PLUGIN_NAME
from datalab_dynamic_demo.adapters.controller import LiveController
from datalab_dynamic_demo.adapters.widgets import LiveStatusWidget
from datalab_dynamic_demo.core.regulation import LiveState

#: Maximum time to wait for an event (s): the dedicated process starts slowly
TIMEOUT = 60.0
#: Source read period in tests (ms)
PERIOD = 20
#: Sigma of the Gaussian filter applied by the normal DataLab path
SIGMA = 4.0


def wait_until(predicate: Callable[[], bool], timeout: float = TIMEOUT) -> None:
    """Process Qt events until a condition is met.

    Raises:
        AssertionError: if the condition is not met in time
    """
    deadline = time.perf_counter() + timeout
    while not predicate():
        if time.perf_counter() > deadline:
            raise AssertionError("Condition not met in time")
        QW.QApplication.processEvents()
        time.sleep(0.002)


def process_events_for(duration: float) -> None:
    """Process Qt events during some time."""
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        QW.QApplication.processEvents()
        time.sleep(0.002)


def published(controller: LiveController) -> int:
    """Return the number of published results."""
    return controller.regulator.stats.published


def frame_of(obj: SignalObj) -> int:
    """Return the frame number recorded in an object's experimental source."""
    return get_experimental_source(obj)["details"]["frame"]


def expected_result(source: SignalObj, sigma: float) -> SignalObj:
    """Return the Gaussian filter result of the current source data."""
    return sips.gaussian_filter(source.copy(), GaussianParam.create(sigma=sigma))


@contextmanager
def live_demo(
    rename: str | None = None,
) -> Generator[tuple[DLMainWindow, LiveController, SignalObj, SignalObj], None, None]:
    """Open DataLab with the plugin, a source and a followed filter result.

    Args:
        rename: title given to the result before following it, if any
    """
    with (
        Conf.plugins_enabled.context(True),
        Conf.plugins_enabled_list.context(None),
        datalab_test_app_context(console=False, exec_loop=False) as win,
    ):
        plugin = PluginRegistry.get_plugin(PLUGIN_ID)
        assert plugin is not None
        plugin.create_source()
        controller = plugin.controller
        controller.set_period(PERIOD)
        source = controller.source_object()
        win.signalpanel.processor.run_feature(
            sips.gaussian_filter, GaussianParam.create(sigma=SIGMA), edit=False
        )
        result = win.signalpanel.objview.get_current_object()
        assert result is not source
        if rename is not None:
            result.title = rename
        controller.follow(result)
        yield win, controller, source, result
        if controller.state in (LiveState.ACTIVE, LiveState.PAUSED):
            controller.stop()


def test_identity_and_scientific_result() -> None:
    """After 100 publications, the same two objects hold coherent data."""
    with live_demo() as (win, controller, source, result):
        panel = win.signalpanel
        uuids = [get_uuid(source), get_uuid(result)]
        groups = [panel.objmodel.get_object_group_id(obj) for obj in (source, result)]
        titles = [source.title, result.title]
        count = len(panel)
        controller.start()
        wait_until(lambda: published(controller) >= 100)
        controller.pause()
        objmodel = panel.objmodel
        assert [objmodel[uuid] for uuid in uuids] == [source, result]
        assert [objmodel.get_object_group_id(obj) for obj in (source, result)] == groups
        assert [source.title, result.title] == titles
        assert len(panel) == count == 2
        # The published source and result come from the same frame
        assert frame_of(source) == frame_of(result) == controller.published_frame
        # The result is the one of the normal Gaussian filter on the source data
        expected = expected_result(source, SIGMA)
        np.testing.assert_allclose(result.xydata, expected.xydata)


def test_sigma_change_during_live_update() -> None:
    """A new sigma is used from the next results and recorded with them."""
    with live_demo() as (_win, controller, source, result):
        controller.start()
        wait_until(lambda: published(controller) >= 5)
        controller.set_sigma(9.0)
        wait_until(
            lambda: (
                extract_processing_parameters(result).param.sigma == 9.0
                and published(controller) >= 10
            )
        )
        controller.pause()
        expected = expected_result(source, 9.0)
        np.testing.assert_allclose(result.xydata, expected.xydata)
        # The title generated by DataLab follows the parameters
        patch_title_with_ids(expected, [source])
        assert result.title == expected.title


def test_user_title_is_kept() -> None:
    """A title chosen by the user, before or after following, is kept."""
    with live_demo(rename="Renamed before following") as (_win, controller, _s, result):
        assert result.title == "Renamed before following"
        controller.start()
        wait_until(lambda: published(controller) >= 5)
        assert result.title == "Renamed before following"
        result.title = "My filtered spectrum"
        controller.set_sigma(6.0)
        wait_until(lambda: extract_processing_parameters(result).param.sigma == 6.0)
        assert result.title == "My filtered spectrum"


@pytest.mark.parametrize("command", ["pause", "stop"])
def test_no_change_after_pause_or_stop(command: str) -> None:
    """No late result changes the objects after a pause or a stop."""
    with live_demo() as (_win, controller, source, result):
        controller.start()
        wait_until(lambda: published(controller) >= 5)
        getattr(controller, command)()
        snapshot = (source.xydata.copy(), result.xydata.copy())
        frame = controller.published_frame
        process_events_for(0.5)
        assert controller.published_frame == frame
        assert np.array_equal(source.xydata, snapshot[0])
        assert np.array_equal(result.xydata, snapshot[1])
        if command == "pause":
            controller.resume()
            wait_until(lambda: controller.published_frame > frame)
        else:
            wait_until(lambda: controller.state is LiveState.STOPPED)
            controller.start()
            wait_until(lambda: controller.published_frame > frame)


def test_unload_releases_resources() -> None:
    """Unloading the plugin frees the process; DataLab's processing still works."""
    with live_demo() as (win, controller, source, _result):
        controller.start()
        wait_until(lambda: published(controller) >= 5)
        pid = controller.executor.submit(os.getpid, ()).get(TIMEOUT).result
        assert psutil.pid_exists(pid)
        plugin = PluginRegistry.get_plugin(PLUGIN_ID)
        plugin.unregister()
        assert plugin.controller is None and plugin.status_widget is None
        try:
            psutil.Process(pid).wait(TIMEOUT)
        except psutil.NoSuchProcess:
            pass
        assert not psutil.pid_exists(pid)
        # The objects stay; a normal processing still runs in DataLab's pool
        count = len(win.signalpanel)
        win.signalpanel.objview.set_current_item_id(get_uuid(source))
        win.signalpanel.processor.run_feature(
            sips.gaussian_filter, GaussianParam.create(sigma=2.0), edit=False
        )
        assert len(win.signalpanel) == count + 1


def test_status_indicator() -> None:
    """The indicator follows the state; the demo adds no dock or window."""
    with (
        Conf.plugins_enabled.context(True),
        Conf.plugins_enabled_list.context(None),
        datalab_test_app_context(console=False, exec_loop=False) as win,
    ):
        docks = len(win.findChildren(QW.QDockWidget))
        windows = [w for w in QW.QApplication.topLevelWidgets() if w.isVisible()]
        plugin = PluginRegistry.get_plugin(PLUGIN_ID)
        assert not win.statusBar().findChildren(LiveStatusWidget)
        plugin.create_source()
        widget = plugin.status_widget
        assert widget in win.statusBar().findChildren(LiveStatusWidget)
        assert not widget.start_button.isEnabled()
        controller = plugin.controller
        source = controller.source_object()
        with pytest.raises(ValueError):
            controller.follow(source)
        win.signalpanel.processor.run_feature(
            sips.gaussian_filter, GaussianParam.create(sigma=SIGMA), edit=False
        )
        controller.follow(win.signalpanel.objview.get_current_object())
        assert widget.start_button.isEnabled()
        controller.set_period(PERIOD)
        controller.start()
        assert widget.pause_button.isEnabled() and not widget.start_button.isEnabled()
        wait_until(lambda: published(controller) >= 3)
        assert widget.frame_label.text() == f"#{controller.published_frame}"
        controller.pause()
        assert widget.start_button.isEnabled()
        assert widget.start_button.toolTip() == "Resume live update"
        widget.show_popup()
        assert widget.popup.isVisible()
        widget.close_popup()
        controller.stop()
        wait_until(lambda: controller.state is LiveState.STOPPED)
        assert len(win.findChildren(QW.QDockWidget)) == docks
        visible = [w for w in QW.QApplication.topLevelWidgets() if w.isVisible()]
        assert len(visible) == len(windows)
        plugin.unregister()
        assert not [
            w for w in win.statusBar().findChildren(LiveStatusWidget) if w.isVisible()
        ]


def plugin_actions(win: DLMainWindow) -> dict[str, QW.QAction]:
    """Return the actions of the plugin submenu, by title."""
    for item in win.signalpanel.get_category_actions(ActionCategory.PLUGINS):
        if isinstance(item, QW.QMenu) and item.title() == PLUGIN_NAME:
            # Qt menus populate their actions lazily
            item.aboutToShow.emit()
            return {action.text(): action for action in item.actions() if action.text()}
    raise AssertionError(f"{PLUGIN_NAME!r} menu not found in the Plugins menu")


def test_menu_actions() -> None:
    """The whole demo can be driven from the Plugins menu."""
    with (
        Conf.plugins_enabled.context(True),
        Conf.plugins_enabled_list.context(None),
        datalab_test_app_context(console=False, exec_loop=False) as win,
    ):
        actions = plugin_actions(win)
        assert list(actions) == [
            "Create simulated source",
            "Follow this result",
            "Start live update",
            "Pause live update",
            "Resume live update",
            "Stop live update",
        ]
        actions["Create simulated source"].trigger()
        controller = PluginRegistry.get_plugin(PLUGIN_ID).controller
        controller.set_period(PERIOD)
        win.signalpanel.processor.run_feature(
            sips.gaussian_filter, GaussianParam.create(sigma=SIGMA), edit=False
        )
        actions["Follow this result"].trigger()
        assert controller.is_linked
        actions["Start live update"].trigger()
        wait_until(lambda: published(controller) >= 3)
        actions["Pause live update"].trigger()
        assert controller.state is LiveState.PAUSED
        actions["Resume live update"].trigger()
        assert controller.state is LiveState.ACTIVE
        actions["Stop live update"].trigger()
        wait_until(lambda: controller.state is LiveState.STOPPED)
