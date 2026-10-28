"""Controller of the live update of a simulated source and of a processing result.

The controller links a source signal, created by the demo, and a result of the
Gaussian filter applied to it by the normal DataLab path. When the live update is
active, it reads the source periodically in a worker thread, computes the filter
on each frame in a dedicated process, and publishes the source frame and its
result together, in place, so that the objects keep their identity.

Every object, metadata and panel mutation happens in the GUI thread, from the
timers of the controller. The timers are protected against re-entrance: a plot
refresh may process the Qt events (modal progress dialog).
"""

from __future__ import annotations

import copy
import dataclasses
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from multiprocessing.pool import AsyncResult
from typing import TYPE_CHECKING, Any

import numpy as np
import sigima.proc.signal as sips
from datalab.gui.plugins.instrument import SerialWorker
from datalab.gui.processor.base import (
    PROCESSING_PARAMETERS_OPTION,
    apply_processing_result,
)
from datalab.objectmodel import get_uuid, patch_title_with_ids
from datalab.processingcall import ProcessingCall, resolve_processing_call
from qtpy import QtCore as QC
from sigima.objects import SignalObj, create_signal
from sigima.objects.source import (
    EXPERIMENTAL_SOURCE_KEY,
    make_experimental_source,
    set_experimental_source,
)

from ..core.regulation import LiveState, Regulator, TaskKey
from ..core.source import X_RANGE, Frame, SimulatedSource, SourceSettings
from .executor import DedicatedExecutor

if TYPE_CHECKING:
    from datalab.gui.main import DLMainWindow
    from sigima.params import GaussianParam

__all__ = ["LiveController", "SOURCE_LOCATION"]

#: Experimental source description of the simulated source
SOURCE_KIND = "simulator"
SOURCE_LOCATION = "demo://dynamic/signal"
SOURCE_ENTRY = "waveform"
#: Title of the source signal and of its group
SOURCE_TITLE = "Simulated spectrum"
GROUP_TITLE = "Dynamic demo"
#: Fixed Y range of the source, inherited by its results (stable axes)
Y_RANGE = (-0.15, 1.65)


def _iso(timestamp: float) -> str:
    """Return a POSIX time stamp as an ISO 8601 string (UTC)."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _param_json(obj: SignalObj) -> str | None:
    """Return the serialized processing parameters of an object, if any."""
    try:
        return obj.get_metadata_option(PROCESSING_PARAMETERS_OPTION).get("param_json")
    except ValueError:
        return None


@dataclasses.dataclass
class _Task:
    """Computation in progress."""

    key: TaskKey
    frame: Frame
    param: GaussianParam
    result: AsyncResult
    started: float


class LiveController(QC.QObject):
    """Live update of a simulated source and of a Gaussian filter result.

    Args:
        main: DataLab main window
        seed: seed of the simulated source noise
    """

    #: Emitted when the state, the link or the status message changes
    SIG_STATE_CHANGED = QC.Signal()
    #: Emitted when a frame is published or counters change
    SIG_STATS_CHANGED = QC.Signal()
    #: Emitted when the parameters (filter or source settings) change
    SIG_PARAMS_CHANGED = QC.Signal()
    #: Emitted when the source signal no longer exists
    SIG_SOURCE_REMOVED = QC.Signal()

    #: Default period of the source reads (ms)
    DEFAULT_PERIOD = 200
    #: Period of the polling of reads and computations (ms)
    POLL_PERIOD = 10
    #: Minimum interval between two notifications of object modifications (ms)
    NOTIFY_PERIOD = 1000

    def __init__(self, main: DLMainWindow, seed: int = 0) -> None:
        super().__init__(main)
        self.main = main
        self.panel = main.signalpanel
        self.source = SimulatedSource(seed)
        self.settings = SourceSettings()
        self.regulator: Regulator[Frame] = Regulator()
        self.executor = DedicatedExecutor()
        self.source_uuid: str | None = None
        self.result_uuid: str | None = None
        self.message = ""
        self.warning = ""
        self.published_frame = 0
        self.last_compute_time: float | None = None
        self._reader: SerialWorker | None = None
        self._read_future: Future | None = None
        self._task: _Task | None = None
        self._closing = None
        self._call: ProcessingCall | None = None
        self._param: GaussianParam | None = None
        self._published_param_json: str | None = None
        self._auto_title: str | None = None
        self._modified = False
        self._busy = False
        self._computation_ready = False
        self._read_timer = QC.QTimer(self)
        self._read_timer.setInterval(self.DEFAULT_PERIOD)
        self._read_timer.timeout.connect(self._request_read)
        self._poll_timer = QC.QTimer(self)
        self._poll_timer.setInterval(self.POLL_PERIOD)
        self._poll_timer.timeout.connect(self._poll)
        self._notify_timer = QC.QTimer(self)
        self._notify_timer.setInterval(self.NOTIFY_PERIOD)
        self._notify_timer.timeout.connect(self._notify)
        self.panel.SIG_OBJECT_REMOVED.connect(self._check_linked_objects)
        self.panel.SIG_OBJECT_MODIFIED.connect(self._check_native_edit)
        main.autorefresh_action.toggled.connect(self._autorefresh_toggled)

    def _autorefresh_toggled(self, _checked: bool) -> None:
        """Show whether plots are redrawn (DataLab's Auto-refresh setting)."""
        self.SIG_STATE_CHANGED.emit()

    # ------ Properties ---------------------------------------------------------
    @property
    def state(self) -> LiveState:
        """State of the live update."""
        return self.regulator.state

    @property
    def is_linked(self) -> bool:
        """True if a result follows the source."""
        return self.result_uuid is not None

    @property
    def period(self) -> int:
        """Period of the source reads (ms)."""
        return self._read_timer.interval()

    @property
    def sigma(self) -> float | None:
        """Sigma of the Gaussian filter, None if no result is linked."""
        return None if self._param is None else self._param.sigma

    @property
    def display_suspended(self) -> bool:
        """True if DataLab's Auto-refresh is off: plots are not redrawn."""
        return not self.main.autorefresh_action.isChecked()

    def _linked(self, uuid: str | None) -> SignalObj | None:
        """Return a linked object, or None if it no longer exists."""
        if uuid is None or not self.panel.objmodel.has_uuid(uuid):
            return None
        return self.panel.objmodel[uuid]

    def source_object(self) -> SignalObj | None:
        """Return the source signal, or None if it does not exist."""
        return self._linked(self.source_uuid)

    def result_object(self) -> SignalObj | None:
        """Return the linked result, or None if there is none."""
        return self._linked(self.result_uuid)

    # ------ Source and link ----------------------------------------------------
    def _experimental_source(self, frame: Frame) -> dict[str, Any]:
        """Return the experimental source description of a frame."""
        return make_experimental_source(
            SOURCE_KIND,
            SOURCE_LOCATION,
            entry=SOURCE_ENTRY,
            timestamps={
                "source": _iso(frame.source_time),
                "reception": _iso(frame.reception_time),
            },
            details={"frame": frame.number, "period_ms": self.period},
        )

    def create_source(self) -> SignalObj:
        """Create the source signal, once, in a group of its own.

        Returns:
            The source signal (the existing one if it was already created)
        """
        obj = self.source_object()
        if obj is not None:
            self.panel.objview.set_current_item_id(self.source_uuid)
            return obj
        frame = self.source.read(self.settings)
        obj = create_signal(
            SOURCE_TITLE,
            np.array(frame.x),
            np.array(frame.y),
            labels=("Energy", "Intensity"),
            units=("keV", "a.u."),
        )
        obj.autoscale = False
        obj.xscalemin, obj.xscalemax = X_RANGE
        obj.yscalemin, obj.yscalemax = Y_RANGE
        set_experimental_source(obj, self._experimental_source(frame))
        group = self.panel.add_group(GROUP_TITLE)
        self.panel.add_object(obj, group_id=get_uuid(group))
        self.source_uuid = get_uuid(obj)
        self.published_frame = frame.number
        # The process start-up takes a few seconds: start it right away
        self.executor.start()
        self.SIG_STATE_CHANGED.emit()
        return obj

    def _resolve(self, obj: SignalObj) -> ProcessingCall:
        """Return the call that produced a result of the Gaussian filter.

        Raises:
            ValueError: if the object is not such a result of the demo source
        """
        if self.source_object() is None:
            raise ValueError("Create the simulated source first.")
        call = resolve_processing_call(self.main, obj)
        if call is None or call.pattern != "1-to-1":
            raise ValueError("The selected signal is not a 1-to-1 processing result.")
        feature = self.panel.processor.get_feature(sips.gaussian_filter)
        if call.feature is not feature:
            raise ValueError("Only results of the Gaussian filter can be followed.")
        if call.reason is not None:
            raise ValueError(call.reason)
        if call.inputs[0].object_uuid != self.source_uuid:
            raise ValueError("The result must be computed from the simulated source.")
        return call

    def follow(self, obj: SignalObj) -> None:
        """Link a result of the Gaussian filter applied to the source.

        Args:
            obj: Gaussian filter result

        Raises:
            ValueError: if the object cannot be followed or if the live update is
             running
        """
        if self.state in (LiveState.ACTIVE, LiveState.PAUSED, LiveState.STOPPING):
            raise ValueError("Stop the live update before following another result.")
        call = self._resolve(obj)
        self._call = call
        self._param = copy.deepcopy(call.param)
        self.result_uuid = get_uuid(obj)
        self._published_param_json = _param_json(obj)
        # A title generated by DataLab follows the parameters; a title chosen by
        # the user is kept
        generated = self._generated_title(self._param)
        self._auto_title = obj.title if obj.title == generated else None
        self.message = ""
        # The process may not be started yet (e.g. after a stop)
        self.executor.start()
        self.SIG_PARAMS_CHANGED.emit()
        self.SIG_STATE_CHANGED.emit()

    def _generated_title(self, param: GaussianParam) -> str:
        """Return the title DataLab gives to a result of the filter."""
        source = self.source_object()
        probe = sips.gaussian_filter(source.copy(), param)
        patch_title_with_ids(probe, [source])
        return probe.title

    def _reload_call(self) -> None:
        """Read again the call of the result, before a start or a resume.

        Parameters changed in DataLab (Processing tab) are adopted; otherwise the
        parameters set in the demo are kept.

        Raises:
            ValueError: if the result can no longer be followed
        """
        obj = self.result_object()
        if obj is None:
            raise ValueError("The followed result no longer exists.")
        self._call = self._resolve(obj)
        stored = _param_json(obj)
        if stored != self._published_param_json:
            self._param = copy.deepcopy(self._call.param)
            self._published_param_json = stored
            self._auto_title = obj.title
            self.regulator.new_revision()
            self.SIG_PARAMS_CHANGED.emit()

    # ------ Settings -------------------------------------------------------------
    def set_sigma(self, sigma: float) -> None:
        """Change the sigma of the filter, results computed before are discarded.

        Args:
            sigma: standard deviation of the Gaussian filter (points)
        """
        if self._param is None or sigma == self._param.sigma:
            return
        param = copy.deepcopy(self._param)
        param.sigma = sigma
        self._param = param
        self.regulator.new_revision()
        self.SIG_PARAMS_CHANGED.emit()

    def set_source_settings(self, **changes: Any) -> None:
        """Change the source settings, used from the next read.

        Args:
            changes: fields of :class:`SourceSettings` to change
        """
        self.settings = dataclasses.replace(self.settings, **changes)
        self.SIG_PARAMS_CHANGED.emit()

    def set_period(self, period: int) -> None:
        """Change the period of the source reads.

        Args:
            period: period (ms)
        """
        self._read_timer.setInterval(max(10, int(period)))
        self.SIG_PARAMS_CHANGED.emit()

    # ------ Commands -------------------------------------------------------------
    def _activate(self) -> None:
        """Start the reader and the timers."""
        if self._reader is None:
            self._reader = SerialWorker()
        self.executor.start()
        self._read_timer.start()
        self._poll_timer.start()
        self._notify_timer.start()

    def start(self) -> None:
        """Start (or restart) the live update.

        Raises:
            ValueError: if no result is linked or if it can no longer be followed
        """
        if not self.is_linked:
            raise ValueError("Follow a Gaussian filter result of the source first.")
        self._reload_call()
        self.regulator.start()
        self.message = self.warning = ""
        self._activate()
        self.SIG_STATE_CHANGED.emit()

    def pause(self, message: str = "") -> None:
        """Pause: objects keep the last published frame and result.

        Args:
            message: reason of the pause, shown in the status
        """
        self.regulator.pause()
        self.message = message
        self._read_timer.stop()
        self._flush()
        self.SIG_STATE_CHANGED.emit()

    def resume(self) -> None:
        """Resume after a pause.

        Raises:
            ValueError: if the result can no longer be followed
        """
        self._reload_call()
        self.regulator.resume()
        self.message = ""
        self._activate()
        self.SIG_STATE_CHANGED.emit()

    def stop(self, message: str = "") -> None:
        """Stop the live update and release its reader and process.

        Args:
            message: reason of the stop, shown in the status
        """
        if self.state not in (
            LiveState.READY,
            LiveState.ACTIVE,
            LiveState.PAUSED,
            LiveState.ERROR,
        ):
            return
        self.regulator.stop()
        self.message = message
        self._read_timer.stop()
        self._notify_timer.stop()
        self._read_future = None
        self._task = None
        if self._reader is not None:
            self._reader.shutdown()
            self._reader = None
        self._closing = self.executor.shutdown()
        self._poll_timer.start()
        self._flush()
        self.SIG_STATE_CHANGED.emit()

    def shutdown(self) -> None:
        """Release every resource (plugin unloaded or DataLab closed)."""
        for timer in (self._read_timer, self._poll_timer, self._notify_timer):
            timer.stop()
        for signal, slot in (
            (self.panel.SIG_OBJECT_REMOVED, self._check_linked_objects),
            (self.panel.SIG_OBJECT_MODIFIED, self._check_native_edit),
            (self.main.autorefresh_action.toggled, self._autorefresh_toggled),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.state not in (LiveState.STOPPING, LiveState.STOPPED):
            self.regulator.stop()
        self._read_future = None
        self._task = None
        if self._reader is not None:
            self._reader.shutdown()
            self._reader = None
        self._closing = self.executor.shutdown()

    def _fail(self, message: str) -> None:
        """Suspend the live update after an error, keeping the last pair."""
        self.regulator.fail(message)
        self.message = message
        self._read_timer.stop()
        self._flush()
        self.SIG_STATE_CHANGED.emit()

    # ------ Timers ---------------------------------------------------------------
    def _request_read(self) -> None:
        """Request a read of the source, if none is in progress."""
        if self._reader is None or not self.regulator.can_read():
            return
        self.regulator.read_started()
        self._read_future = self._reader.submit(self.source.read, self.settings)

    def _poll(self) -> None:
        """Collect reads and computations, submit the next computation."""
        if self._busy:
            return
        self._busy = True
        try:
            self._collect_read()
            self._collect_computation()
            self._submit_computation()
            self._check_stopped()
            ready = self.executor.is_ready
            if ready != self._computation_ready:
                self._computation_ready = ready
                self.SIG_STATS_CHANGED.emit()
        finally:
            self._busy = False

    def _collect_read(self) -> None:
        """Take the frame read from the source, if available."""
        future = self._read_future
        if future is None or not future.done():
            return
        self._read_future = None
        try:
            frame = future.result()
        except Exception as exc:  # pylint: disable=broad-except
            self.regulator.read_failed()
            self._fail(f"Source read failed: {exc}")
            return
        self.regulator.frame_received(frame)

    def _collect_computation(self) -> None:
        """Take the result of the computation in progress, if available."""
        task = self._task
        if task is None or not task.result.ready():
            return
        self._task = None
        try:
            out = task.result.get(0)
        except Exception as exc:  # pylint: disable=broad-except
            self.regulator.task_failed(task.key)
            self._fail(f"Computation failed: {exc}")
            return
        self.last_compute_time = time.perf_counter() - task.started
        if out.error_msg is not None or out.result is None:
            self.regulator.task_failed(task.key)
            self._fail(out.error_msg or "Computation returned no result.")
            return
        if self.regulator.task_done(task.key):
            if out.warning_msg:
                self.warning = out.warning_msg
            self._publish(task, out.result)
        self.SIG_STATS_CHANGED.emit()

    def _submit_computation(self) -> None:
        """Submit the pending frame, if no computation is in progress."""
        source = self.source_object()
        if source is None or self._param is None:
            return
        task = self.regulator.next_task()
        if task is None:
            return
        key, frame = task
        signal = source.copy()
        signal.set_xydata(np.array(frame.x), np.array(frame.y))
        param = copy.deepcopy(self._param)
        result = self.executor.submit(sips.gaussian_filter, (signal, param))
        self._task = _Task(key, frame, param, result, time.perf_counter())

    def _check_stopped(self) -> None:
        """Finish a stop once the process is released; stop idle polling."""
        if self.state is LiveState.STOPPING:
            if self._closing is None or not self._closing.is_alive():
                self._closing = None
                self.regulator.stopped()
                self.SIG_STATE_CHANGED.emit()
        if (
            self.state is not LiveState.ACTIVE
            and self.state is not LiveState.STOPPING
            and self._task is None
            and self._read_future is None
        ):
            self._poll_timer.stop()

    def _notify(self) -> None:
        """Notify the modifications of the objects, at most once per period."""
        if self._busy or not self._modified:
            return
        self._modified = False
        self.panel.SIG_OBJECT_MODIFIED.emit()

    def _flush(self) -> None:
        """Notify pending modifications and update the properties panel."""
        if not self._modified:
            return
        self._modified = False
        self.panel.SIG_OBJECT_MODIFIED.emit()
        current = self.panel.objview.get_current_object()
        if current is not None and get_uuid(current) in (
            self.source_uuid,
            self.result_uuid,
        ):
            self.panel.objprop.update_properties_from(current)

    # ------ Publication ----------------------------------------------------------
    def _publish(self, task: _Task, new_obj: SignalObj) -> None:
        """Publish a frame and its result in place, in the GUI thread."""
        source = self.source_object()
        result = self.result_object()
        if source is None or result is None or self._call is None:
            self._check_linked_objects()
            return
        frame = task.frame
        experimental = self._experimental_source(frame)
        source.set_xydata(np.array(frame.x), np.array(frame.y))
        set_experimental_source(source, experimental)
        patch_title_with_ids(new_obj, [source])
        title = result.title
        apply_processing_result(
            result, new_obj, self._call.processing_parameters(task.param)
        )
        if self._auto_title is not None and title == self._auto_title:
            # Title generated by DataLab: it follows the parameters
            self._auto_title = result.title
        else:
            # Title chosen by the user: it is kept
            self._auto_title = None
            result.title = title
        if EXPERIMENTAL_SOURCE_KEY in result.metadata:
            set_experimental_source(result, experimental)
        self._published_param_json = _param_json(result)
        self.published_frame = frame.number
        for uuid in (self.source_uuid, self.result_uuid):
            self.panel.objview.update_item(uuid)
        selected = self.panel.objview.get_sel_object_uuids(include_groups=True)
        if self.source_uuid in selected or self.result_uuid in selected:
            # Not forced: DataLab's Auto-refresh setting is respected
            self.panel.refresh_plot("selected", update_items=True)
        self._modified = True

    # ------ Workspace changes ----------------------------------------------------
    def _check_linked_objects(self) -> None:
        """Stop the live update if a linked object was deleted."""
        source_missing = self.source_uuid is not None and self.source_object() is None
        result_missing = self.result_uuid is not None and self.result_object() is None
        if not source_missing and not result_missing:
            return
        if source_missing:
            self.source_uuid = None
        if result_missing or source_missing:
            self.result_uuid = None
            self._call = None
            self._param = None
        self.stop()
        self.message = "A linked object was deleted: live update stopped."
        self.SIG_PARAMS_CHANGED.emit()
        self.SIG_STATE_CHANGED.emit()
        if source_missing:
            self.SIG_SOURCE_REMOVED.emit()

    def _check_native_edit(self) -> None:
        """Pause if the result parameters were changed in DataLab meanwhile."""
        if self.state is not LiveState.ACTIVE:
            return
        result = self.result_object()
        if result is None or _param_json(result) == self._published_param_json:
            return
        self.pause(
            "Processing parameters were changed in DataLab: live update paused. "
            "Resume to follow the new parameters."
        )
