"""Widgets of the live update: status bar indicator and settings popup.

The demo adds no dock widget and no floating window. The indicator sits in the
status bar, next to DataLab's own indicators, once the source exists; a click on
its label opens a popup with the settings, closed by a click elsewhere. The
widgets only call the controller and follow its signals: they never change the
objects themselves.
"""

from __future__ import annotations

from collections.abc import Callable

from qtpy import QtCore as QC
from qtpy import QtWidgets as QW

from ..core.regulation import LiveState
from ..core.source import X_RANGE
from .controller import SOURCE_LOCATION, LiveController

__all__ = ["LiveSettingsPopup", "LiveStatusWidget"]

#: Label of the indicator: the demo is a mockup with a simulated source
INDICATOR_LABEL = "Mockup \N{EM DASH} simulated source"
#: Color of the state dot, by state
STATE_COLORS = {
    LiveState.READY: "#8c8c8c",
    LiveState.ACTIVE: "#2e9d48",
    LiveState.PAUSED: "#e69500",
    LiveState.STOPPING: "#8c8c8c",
    LiveState.STOPPED: "#8c8c8c",
    LiveState.ERROR: "#d43c3c",
}
STATE_NAMES = {
    LiveState.READY: "Ready",
    LiveState.ACTIVE: "Live",
    LiveState.PAUSED: "Paused",
    LiveState.STOPPING: "Stopping",
    LiveState.STOPPED: "Stopped",
    LiveState.ERROR: "Error",
}


def run_command(parent: QW.QWidget, command: Callable[[], None]) -> None:
    """Run a controller command, show its refusal reason if any."""
    try:
        command()
    except ValueError as exc:
        QW.QMessageBox.warning(parent, "Dynamic mode demo", str(exc))


class LiveStatusWidget(QW.QWidget):
    """Indicator of the live update, in DataLab's status bar.

    Args:
        controller: live update controller
        parent: parent widget
    """

    def __init__(self, controller: LiveController, parent: QW.QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.popup: LiveSettingsPopup | None = None
        layout = QW.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self.dot = QW.QLabel("\N{BLACK CIRCLE}")
        self.label = QW.QToolButton()
        self.label.setText(INDICATOR_LABEL)
        self.label.setAutoRaise(True)
        self.label.setCursor(QC.Qt.PointingHandCursor)
        self.label.setToolTip("Live update settings")
        self.label.clicked.connect(self.show_popup)
        self.frame_label = QW.QLabel()
        style = self.style()
        self.start_button = self._add_button(
            style.standardIcon(QW.QStyle.SP_MediaPlay), self.start_or_resume
        )
        self.pause_button = self._add_button(
            style.standardIcon(QW.QStyle.SP_MediaPause), self.pause
        )
        self.pause_button.setToolTip("Pause live update")
        self.stop_button = self._add_button(
            style.standardIcon(QW.QStyle.SP_MediaStop), self.stop
        )
        self.stop_button.setToolTip("Stop live update")
        for widget in (self.dot, self.label, self.frame_label):
            layout.addWidget(widget)
        for button in (self.start_button, self.pause_button, self.stop_button):
            layout.addWidget(button)
        controller.SIG_STATE_CHANGED.connect(self.update_state)
        controller.SIG_STATS_CHANGED.connect(self.update_frame)
        self.update_state()

    def _add_button(self, icon, slot: Callable[[], None]) -> QW.QToolButton:
        """Return a tool button of the indicator."""
        button = QW.QToolButton()
        button.setIcon(icon)
        button.setAutoRaise(True)
        button.clicked.connect(slot)
        return button

    def start_or_resume(self) -> None:
        """Start the live update, or resume it after a pause."""
        if self.controller.state is LiveState.PAUSED:
            run_command(self, self.controller.resume)
        else:
            run_command(self, self.controller.start)

    def pause(self) -> None:
        """Pause the live update."""
        run_command(self, self.controller.pause)

    def stop(self) -> None:
        """Stop the live update."""
        run_command(self, self.controller.stop)

    def show_popup(self) -> None:
        """Show the settings popup above the indicator."""
        if self.popup is None:
            self.popup = LiveSettingsPopup(self.controller, self)
        self.popup.show_above(self)

    def close_popup(self) -> None:
        """Close the settings popup, if shown."""
        if self.popup is not None:
            self.popup.close()

    def update_frame(self) -> None:
        """Show the number of the last published frame."""
        controller = self.controller
        if controller.state is LiveState.ACTIVE and not controller.executor.is_ready:
            self.frame_label.setText(
                "starting computation process\N{HORIZONTAL ELLIPSIS}"
            )
            return
        text = f"#{controller.published_frame}"
        if controller.display_suspended:
            text += " (display suspended)"
        self.frame_label.setText(text)

    def update_state(self) -> None:
        """Show the state of the live update."""
        controller = self.controller
        state = controller.state
        self.dot.setStyleSheet(f"color: {STATE_COLORS[state]};")
        startable = controller.is_linked and state in (
            LiveState.READY,
            LiveState.PAUSED,
            LiveState.STOPPED,
            LiveState.ERROR,
        )
        self.start_button.setEnabled(startable)
        self.start_button.setToolTip(
            "Resume live update" if state is LiveState.PAUSED else "Start live update"
        )
        self.pause_button.setEnabled(state is LiveState.ACTIVE)
        self.stop_button.setEnabled(
            state in (LiveState.ACTIVE, LiveState.PAUSED, LiveState.ERROR)
        )
        lines = [
            f"<b>Live update: {STATE_NAMES[state]}</b>",
            f"Source: simulated spectrum ({SOURCE_LOCATION})",
            f"Read period: {controller.period} ms",
        ]
        result = controller.result_object()
        if result is None:
            lines.append(
                "No result followed: apply the Gaussian filter to the source, then "
                "use <i>Plugins &gt; Dynamic mode demo &gt; Follow this result</i>."
            )
        else:
            lines.append(f"Followed result: {result.title}")
        if controller.display_suspended:
            lines.append("Display suspended: <i>Auto-refresh</i> is off.")
        for text in (controller.message, controller.warning):
            if text:
                lines.append(text)
        self.setToolTip("<br>".join(lines))
        self.update_frame()


class _SliderRow(QW.QWidget):
    """Slider with its value, for a float setting.

    Args:
        minimum: minimum value
        maximum: maximum value
        step: value step
        decimals: number of decimals shown
        suffix: unit suffix
    """

    SIG_VALUE_CHANGED = QC.Signal(float)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int,
        suffix: str = "",
    ) -> None:
        super().__init__()
        self.minimum = minimum
        self.step = step
        self.decimals = decimals
        self.suffix = suffix
        layout = QW.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QW.QSlider(QC.Qt.Horizontal)
        self.slider.setRange(0, round((maximum - minimum) / step))
        self.slider.setMinimumWidth(160)
        self.value_label = QW.QLabel()
        self.value_label.setMinimumWidth(64)
        layout.addWidget(self.slider)
        layout.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._slider_changed)

    def value(self) -> float:
        """Return the value."""
        return round(self.minimum + self.slider.value() * self.step, self.decimals)

    def set_value(self, value: float) -> None:
        """Set the value without emitting a change."""
        with QC.QSignalBlocker(self.slider):
            self.slider.setValue(round((value - self.minimum) / self.step))
        self._show_value()

    def _show_value(self) -> None:
        """Show the value."""
        self.value_label.setText(f"{self.value():.{self.decimals}f}{self.suffix}")

    def _slider_changed(self, _position: int) -> None:
        """Emit the new value."""
        self._show_value()
        self.SIG_VALUE_CHANGED.emit(self.value())


class LiveSettingsPopup(QW.QFrame):
    """Popup with the settings and counters of the live update.

    Args:
        controller: live update controller
        parent: parent widget
    """

    def __init__(self, controller: LiveController, parent: QW.QWidget | None = None):
        super().__init__(parent, QC.Qt.Popup)
        self.controller = controller
        self.setFrameShape(QW.QFrame.StyledPanel)
        layout = QW.QVBoxLayout(self)
        title = QW.QLabel(f"<b>{INDICATOR_LABEL}</b>")
        layout.addWidget(title)
        form = QW.QFormLayout()
        layout.addLayout(form)
        self.sigma = _SliderRow(0.5, 30.0, 0.5, 1, " pt")
        self.sigma.SIG_VALUE_CHANGED.connect(controller.set_sigma)
        self.amplitude = _SliderRow(0.0, 1.4, 0.02, 2)
        self.amplitude.SIG_VALUE_CHANGED.connect(
            lambda value: controller.set_source_settings(amplitude=value)
        )
        self.position = _SliderRow(X_RANGE[0] + 1.0, X_RANGE[1] - 1.0, 0.05, 2, " keV")
        self.position.SIG_VALUE_CHANGED.connect(
            lambda value: controller.set_source_settings(position=value)
        )
        self.noise = _SliderRow(0.0, 0.3, 0.01, 2)
        self.noise.SIG_VALUE_CHANGED.connect(
            lambda value: controller.set_source_settings(noise=value)
        )
        self.period = QW.QSpinBox()
        self.period.setRange(50, 2000)
        self.period.setSingleStep(50)
        self.period.setSuffix(" ms")
        self.period.valueChanged.connect(controller.set_period)
        form.addRow("Filter \N{GREEK SMALL LETTER SIGMA}:", self.sigma)
        form.addRow("Main peak amplitude:", self.amplitude)
        form.addRow("Main peak position:", self.position)
        form.addRow("Noise level:", self.noise)
        form.addRow("Read period:", self.period)
        self.stats = QW.QLabel()
        layout.addWidget(self.stats)
        controller.SIG_PARAMS_CHANGED.connect(self.update_params)
        controller.SIG_STATS_CHANGED.connect(self.update_stats)
        self.update_params()
        self.update_stats()

    def show_above(self, anchor: QW.QWidget) -> None:
        """Show the popup above a widget, aligned on its right edge."""
        self.update_params()
        self.update_stats()
        self.adjustSize()
        corner = anchor.mapToGlobal(QC.QPoint(anchor.width(), 0))
        position = QC.QPoint(corner.x() - self.width(), corner.y() - self.height() - 2)
        screen = QW.QApplication.screenAt(corner)
        if screen is not None:
            area = screen.availableGeometry()
            position.setX(
                max(area.left(), min(position.x(), area.right() - self.width()))
            )
            position.setY(max(area.top(), position.y()))
        self.move(position)
        self.show()

    def update_params(self) -> None:
        """Show the current settings."""
        controller = self.controller
        sigma = controller.sigma
        self.sigma.setEnabled(sigma is not None)
        if sigma is not None:
            self.sigma.set_value(sigma)
        self.amplitude.set_value(controller.settings.amplitude)
        self.position.set_value(controller.settings.position)
        self.noise.set_value(controller.settings.noise)
        with QC.QSignalBlocker(self.period):
            self.period.setValue(controller.period)

    def update_stats(self) -> None:
        """Show the counters of the live update."""
        stats = self.controller.regulator.stats
        duration = self.controller.last_compute_time
        computing = "\N{EN DASH}" if duration is None else f"{duration * 1e3:.0f} ms"
        self.stats.setText(
            f"Frames read: {stats.received} \N{EM DASH} skipped: {stats.skipped}"
            f"<br>Results published: {stats.published} \N{EM DASH} "
            f"discarded: {stats.rejected}<br>Last computation: {computing}"
        )
