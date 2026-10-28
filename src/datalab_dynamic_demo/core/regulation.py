"""Regulation of the live update: which frame to compute, which result to publish.

The regulator is a plain state machine, driven by the controller from the GUI
thread. It bounds the load to one read and one computation at a time, plus one
pending frame, replaced by the latest frame read. A computation is identified by
a :class:`TaskKey`: its result is published only if the live update was not
invalidated (pause, stop, error) and the parameters were not changed meanwhile.

A result is accepted even if a more recent frame is pending: otherwise nothing
would ever be published when the computation is slower than the source.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Generic, Protocol, TypeVar

__all__ = ["LiveState", "Regulator", "RegulatorStats", "TaskKey"]


class LiveState(enum.Enum):
    """State of the live update."""

    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class NumberedFrame(Protocol):
    """Frame with a monotonic number."""

    number: int


FrameT = TypeVar("FrameT", bound=NumberedFrame)


@dataclasses.dataclass(frozen=True)
class TaskKey:
    """Identity of a computation.

    Args:
        generation: live update session, changed when it is invalidated
        revision: parameter set, changed when the parameters are changed
        frame: number of the computed frame
    """

    generation: int
    revision: int
    frame: int


@dataclasses.dataclass(frozen=True)
class RegulatorStats:
    """Counters of the live update.

    Args:
        received: frames read while active
        skipped: frames replaced by a more recent frame before being computed
        published: results accepted for publication
        rejected: results discarded (invalidated or computed with old parameters)
        last_published: number of the last published frame (0 if none)
    """

    received: int
    skipped: int
    published: int
    rejected: int
    last_published: int


#: Allowed transitions, by command
_TRANSITIONS = {
    "start": {LiveState.READY, LiveState.STOPPED, LiveState.ERROR},
    "pause": {LiveState.ACTIVE},
    "resume": {LiveState.PAUSED},
    "stop": {LiveState.READY, LiveState.ACTIVE, LiveState.PAUSED, LiveState.ERROR},
    "stopped": {LiveState.STOPPING},
}


class Regulator(Generic[FrameT]):
    """Decide which frame to compute and which result to publish."""

    def __init__(self) -> None:
        self.state = LiveState.READY
        self.generation = 0
        self.revision = 0
        self.error: str | None = None
        self.reading = False
        self.computing: TaskKey | None = None
        self.pending: FrameT | None = None
        self._received = 0
        self._skipped = 0
        self._published = 0
        self._rejected = 0
        self._last_published = 0

    @property
    def stats(self) -> RegulatorStats:
        """Counters of the live update."""
        return RegulatorStats(
            self._received,
            self._skipped,
            self._published,
            self._rejected,
            self._last_published,
        )

    def _transition(self, command: str, target: LiveState) -> None:
        """Change state, raise ValueError if the command is not allowed."""
        if self.state not in _TRANSITIONS[command]:
            raise ValueError(f"Cannot {command} from state {self.state.name}")
        self.state = target

    def _invalidate(self) -> None:
        """Invalidate the results of the computations in progress."""
        self.generation += 1
        self.pending = None

    def start(self) -> None:
        """Start (or restart) the live update."""
        self._transition("start", LiveState.ACTIVE)
        self._invalidate()
        self.error = None
        self._last_published = 0

    def pause(self) -> None:
        """Pause: no more reads, results in progress are discarded."""
        self._transition("pause", LiveState.PAUSED)
        self._invalidate()

    def resume(self) -> None:
        """Resume after a pause."""
        self._transition("resume", LiveState.ACTIVE)

    def stop(self) -> None:
        """Stop: the controller then releases its resources, see :meth:`stopped`."""
        self._transition("stop", LiveState.STOPPING)
        self._invalidate()

    def stopped(self) -> None:
        """Tell that the resources are released: no read or computation is left."""
        self._transition("stopped", LiveState.STOPPED)
        self.computing = None
        self.reading = False

    def fail(self, message: str) -> None:
        """Suspend the live update after an error.

        Args:
            message: error message
        """
        if self.state in (LiveState.STOPPING, LiveState.STOPPED):
            return
        self.state = LiveState.ERROR
        self.error = message
        self._invalidate()

    def new_revision(self) -> int:
        """Adopt new parameters: results computed with the old ones are discarded.

        Returns:
            New revision
        """
        self.revision += 1
        return self.revision

    def can_read(self) -> bool:
        """Return True if a read may be requested."""
        return self.state is LiveState.ACTIVE and not self.reading

    def read_started(self) -> None:
        """Tell that a read was requested."""
        self.reading = True

    def read_failed(self) -> None:
        """Tell that the read in progress failed."""
        self.reading = False

    def frame_received(self, frame: FrameT) -> None:
        """Take a frame read from the source.

        The frame becomes the pending frame, replacing a frame not computed yet.
        Frames received while not active are dropped.
        """
        self.reading = False
        if self.state is not LiveState.ACTIVE:
            return
        self._received += 1
        if self.pending is not None:
            self._skipped += 1
        self.pending = frame

    def next_task(self) -> tuple[TaskKey, FrameT] | None:
        """Return the next computation to submit, if any.

        Returns:
            Key and frame of the computation, or None if a computation is in
            progress, if no frame is pending or if the live update is not active
        """
        if (
            self.state is not LiveState.ACTIVE
            or self.computing is not None
            or self.pending is None
        ):
            return None
        frame = self.pending
        self.pending = None
        self.computing = TaskKey(self.generation, self.revision, frame.number)
        return self.computing, frame

    def task_done(self, key: TaskKey) -> bool:
        """Tell that a computation succeeded.

        Args:
            key: key of the computation

        Returns:
            True if its result must be published
        """
        if key == self.computing:
            self.computing = None
        accepted = (
            self.state is LiveState.ACTIVE
            and key.generation == self.generation
            and key.revision == self.revision
            and key.frame > self._last_published
        )
        if accepted:
            self._published += 1
            self._last_published = key.frame
        else:
            self._rejected += 1
        return accepted

    def task_failed(self, key: TaskKey) -> None:
        """Tell that a computation failed or was abandoned.

        Args:
            key: key of the computation
        """
        if key == self.computing:
            self.computing = None
