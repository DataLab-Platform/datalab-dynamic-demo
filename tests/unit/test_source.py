"""Tests of the simulated spectrum source."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from datalab_dynamic_demo.core.source import (
    DRIFT_PERIOD,
    FIXED_PEAKS,
    SimulatedSource,
    SourceSettings,
    make_spectrum,
)


def test_frames_are_numbered_and_read_only() -> None:
    """Frames have monotonic numbers and read-only values."""
    source = SimulatedSource()
    settings = SourceSettings()
    frames = [source.read(settings) for _ in range(3)]
    assert [frame.number for frame in frames] == [1, 2, 3]
    assert source.count == 3
    for frame in frames:
        assert frame.x.shape == frame.y.shape == (settings.size,)
        assert not frame.x.flags.writeable and not frame.y.flags.writeable
        assert frame.source_time <= frame.reception_time
        with pytest.raises(ValueError):
            frame.y[0] = 0.0


def test_frames_are_reproducible() -> None:
    """A frame only depends on the seed, its number and the settings."""
    settings = SourceSettings()
    first = SimulatedSource(seed=3).read(settings)
    again = SimulatedSource(seed=3).read(settings)
    other_seed = SimulatedSource(seed=4).read(settings)
    assert np.array_equal(first.y, again.y)
    assert not np.array_equal(first.y, other_seed.y)
    x, y = make_spectrum(settings, 1, seed=3)
    assert np.array_equal(first.x, x) and np.array_equal(first.y, y)


def test_frame_keeps_its_settings() -> None:
    """Each frame carries the settings it was produced with."""
    source = SimulatedSource()
    settings = SourceSettings(amplitude=2.0)
    frame = source.read(settings)
    assert frame.settings == settings
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.settings.amplitude = 1.0  # type: ignore[misc]


def test_main_peak_follows_settings() -> None:
    """The main peak is where and as high as the settings say."""
    # Frame number 0 has no drift; no noise for an exact check
    settings = SourceSettings(amplitude=3.0, position=12.5, noise=0.0)
    x, y = make_spectrum(settings, 0)
    imax = int(np.argmax(y))
    assert x[imax] == pytest.approx(12.5, abs=x[1] - x[0])
    assert y[imax] == pytest.approx(3.0, rel=0.05)
    for center, _amplitude, _width in FIXED_PEAKS:
        icenter = int(np.argmin(np.abs(x - center)))
        assert y[icenter] > y[icenter - 40] and y[icenter] > y[icenter + 40]


def test_main_peak_drifts() -> None:
    """The center of the main peak oscillates with the frame number."""
    settings = SourceSettings(amplitude=3.0, noise=0.0, drift=1.0)
    x, y_start = make_spectrum(settings, 0)
    _x, y_quarter = make_spectrum(settings, DRIFT_PERIOD // 4)
    shift = x[int(np.argmax(y_quarter))] - x[int(np.argmax(y_start))]
    assert shift == pytest.approx(1.0, abs=2 * (x[1] - x[0]))


@pytest.mark.parametrize(
    "kwargs",
    [{"size": 8}, {"amplitude": -1.0}, {"noise": -0.1}, {"position": 25.0}],
)
def test_invalid_settings(kwargs: dict) -> None:
    """Invalid settings are refused."""
    with pytest.raises(ValueError):
        SourceSettings(**kwargs)
