"""Tests of the regulation of the live update."""

from __future__ import annotations

import dataclasses

import pytest

from datalab_dynamic_demo.core.regulation import LiveState, Regulator, TaskKey


@dataclasses.dataclass(frozen=True)
class FakeFrame:
    """Frame of a fake source."""

    number: int


class FakeSource:
    """Source numbering its frames."""

    def __init__(self) -> None:
        self.count = 0

    def read(self) -> FakeFrame:
        """Return the next frame."""
        self.count += 1
        return FakeFrame(self.count)


def receive(regulator: Regulator, source: FakeSource) -> FakeFrame:
    """Read one frame through the regulator, as the controller does."""
    assert regulator.can_read()
    regulator.read_started()
    assert not regulator.can_read()
    frame = source.read()
    regulator.frame_received(frame)
    return frame


@pytest.fixture(name="active")
def fixture_active() -> tuple[Regulator, FakeSource]:
    """Return an active regulator and its source."""
    regulator: Regulator[FakeFrame] = Regulator()
    regulator.start()
    return regulator, FakeSource()


def test_one_read_and_one_computation_at_a_time(active) -> None:
    """Reads and computations are bounded to one at a time."""
    regulator, source = active
    receive(regulator, source)
    key, frame = regulator.next_task()
    assert key == TaskKey(regulator.generation, regulator.revision, frame.number)
    receive(regulator, source)
    assert regulator.next_task() is None


def test_overload_keeps_the_latest_frame(active) -> None:
    """While computing, only the latest frame waits; it is computed eventually."""
    regulator, source = active
    receive(regulator, source)
    key, _frame = regulator.next_task()
    for _ in range(5):
        receive(regulator, source)
    assert regulator.pending.number == 6
    assert regulator.stats.skipped == 4
    assert regulator.task_done(key)
    # The source stops producing: the pending frame is still computed
    key, frame = regulator.next_task()
    assert frame.number == 6
    assert regulator.task_done(key)
    assert regulator.next_task() is None
    assert regulator.stats.last_published == 6


def test_slow_computation_still_publishes(active) -> None:
    """Results are published even if a more recent frame is pending."""
    regulator, source = active
    receive(regulator, source)
    published = []
    for _ in range(10):
        key, frame = regulator.next_task()
        # Three frames arrive during each computation
        for _ in range(3):
            receive(regulator, source)
        assert key.frame == frame.number
        if regulator.task_done(key):
            published.append(frame.number)
    assert len(published) == 10
    assert published == sorted(published)
    assert regulator.stats.published == 10


def test_old_revision_is_rejected(active) -> None:
    """A result computed with old parameters is not published."""
    regulator, source = active
    receive(regulator, source)
    key, _frame = regulator.next_task()
    receive(regulator, source)
    regulator.new_revision()
    assert not regulator.task_done(key)
    key, _frame = regulator.next_task()
    assert key.revision == regulator.revision
    assert regulator.task_done(key)
    assert regulator.stats.rejected == 1


def test_pause_discards_late_results(active) -> None:
    """After a pause, results in progress and frames read are discarded."""
    regulator, source = active
    receive(regulator, source)
    key, _frame = regulator.next_task()
    regulator.read_started()
    regulator.pause()
    assert not regulator.can_read()
    regulator.frame_received(source.read())
    assert regulator.pending is None
    assert not regulator.task_done(key)
    assert regulator.next_task() is None
    regulator.resume()
    receive(regulator, source)
    key, _frame = regulator.next_task()
    assert regulator.task_done(key)


def test_stop_and_restart(active) -> None:
    """Results of a stopped live update are never published after a restart."""
    regulator, source = active
    receive(regulator, source)
    old_key, _frame = regulator.next_task()
    # A read is in progress when stopping: it is abandoned
    regulator.read_started()
    regulator.stop()
    assert regulator.state is LiveState.STOPPING
    assert not regulator.can_read()
    regulator.stopped()
    assert regulator.computing is None
    regulator.start()
    assert regulator.can_read()
    assert not regulator.task_done(old_key)
    receive(regulator, source)
    key, _frame = regulator.next_task()
    assert key.generation > old_key.generation
    assert regulator.task_done(key)


def test_error_suspends(active) -> None:
    """An error suspends the live update until it is started again."""
    regulator, source = active
    receive(regulator, source)
    key, _frame = regulator.next_task()
    regulator.task_failed(key)
    regulator.fail("Computation failed")
    assert regulator.state is LiveState.ERROR
    assert regulator.error == "Computation failed"
    assert not regulator.can_read()
    regulator.start()
    assert regulator.error is None
    assert regulator.can_read()


@pytest.mark.parametrize(
    ("state", "command"),
    [
        (LiveState.READY, "pause"),
        (LiveState.READY, "resume"),
        (LiveState.ACTIVE, "start"),
        (LiveState.ACTIVE, "stopped"),
    ],
)
def test_invalid_commands(state: LiveState, command: str) -> None:
    """Commands not allowed in the current state are refused."""
    regulator: Regulator[FakeFrame] = Regulator()
    if state is LiveState.ACTIVE:
        regulator.start()
    with pytest.raises(ValueError):
        getattr(regulator, command)()
