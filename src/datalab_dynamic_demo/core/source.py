"""Simulated spectrum source of the dynamic mode demo.

The source produces a noisy spectrum made of a few fixed peaks and of a main
peak whose amplitude and position are set by the user, on a decaying
background. The position of the main peak drifts slowly from frame to frame, so
that the live update is visible even when the settings are not changed.

Each read returns a detached :class:`Frame`: read-only values, source and
reception times, and a monotonic frame number. The noise only depends on the
seed and on the frame number, so that a frame can be generated again for tests.
"""

from __future__ import annotations

import dataclasses
import math
import threading
import time

import numpy as np

__all__ = [
    "DRIFT_PERIOD",
    "FIXED_PEAKS",
    "X_RANGE",
    "Frame",
    "SimulatedSource",
    "SourceSettings",
    "make_spectrum",
]

#: Energy range of the spectrum (keV)
X_RANGE = (0.0, 20.0)
#: Fixed peaks of the spectrum: (center in keV, amplitude, width in keV)
FIXED_PEAKS = ((3.7, 0.45, 0.09), (6.4, 0.8, 0.11), (8.0, 0.6, 0.12), (15.8, 0.3, 0.16))
#: Width of the main peak (keV)
MAIN_PEAK_WIDTH = 0.14
#: Number of frames of one oscillation of the main peak position
DRIFT_PERIOD = 100


@dataclasses.dataclass(frozen=True)
class SourceSettings:
    """Settings of the simulated source, copied with each frame.

    Args:
        amplitude: height of the main peak
        position: mean center of the main peak (keV)
        noise: standard deviation of the additive noise
        drift: amplitude of the slow oscillation of the main peak center (keV)
        size: number of points of the spectrum
    """

    amplitude: float = 1.0
    position: float = 11.5
    noise: float = 0.04
    drift: float = 0.6
    size: int = 2048

    def __post_init__(self) -> None:
        """Check the settings."""
        if self.size < 16:
            raise ValueError("The spectrum must have at least 16 points")
        if self.amplitude < 0.0 or self.noise < 0.0 or self.drift < 0.0:
            raise ValueError("Amplitude, noise and drift must not be negative")
        if not X_RANGE[0] <= self.position <= X_RANGE[1]:
            raise ValueError(f"Position must be in {X_RANGE}")


@dataclasses.dataclass(frozen=True)
class Frame:
    """Spectrum read from the source.

    Args:
        x: read-only energy values (keV)
        y: read-only intensity values
        number: rank of the frame among the frames read from the source
        source_time: time stamped by the source (s since the epoch)
        reception_time: time of reception (s since the epoch)
        settings: settings used to produce the frame
    """

    x: np.ndarray
    y: np.ndarray
    number: int
    source_time: float
    reception_time: float
    settings: SourceSettings


def _gaussian(
    x: np.ndarray, center: float, amplitude: float, width: float
) -> np.ndarray:
    """Return a Gaussian peak."""
    return amplitude * np.exp(-0.5 * ((x - center) / width) ** 2)


def make_spectrum(
    settings: SourceSettings, number: int, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Return the spectrum of a frame.

    Args:
        settings: source settings
        number: frame number, which sets the drift of the main peak and the noise
        seed: seed of the noise

    Returns:
        Energy (keV) and intensity arrays
    """
    x = np.linspace(*X_RANGE, settings.size)
    y = 0.25 * np.exp(-x / 6.0) + 0.05
    for center, amplitude, width in FIXED_PEAKS:
        y += _gaussian(x, center, amplitude, width)
    center = settings.position + settings.drift * math.sin(
        2.0 * math.pi * number / DRIFT_PERIOD
    )
    y += _gaussian(x, center, settings.amplitude, MAIN_PEAK_WIDTH)
    rng = np.random.default_rng((seed, number))
    y += rng.normal(0.0, settings.noise, settings.size)
    return x, y


class SimulatedSource:
    """Source of simulated spectra, read one frame at a time.

    Reads may run in a worker thread: frame numbers are given under a lock.

    Args:
        seed: seed of the noise
    """

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self._count = 0
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        """Number of frames read since the source was created."""
        with self._lock:
            return self._count

    def read(self, settings: SourceSettings) -> Frame:
        """Read the next frame.

        Args:
            settings: settings copied when the read was requested
        """
        with self._lock:
            self._count += 1
            number = self._count
        source_time = time.time()
        x, y = make_spectrum(settings, number, self.seed)
        x.setflags(write=False)
        y.setflags(write=False)
        return Frame(x, y, number, source_time, time.time(), settings)
