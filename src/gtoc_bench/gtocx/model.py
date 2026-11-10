"""GTOCX constants, analytic stellar ephemerides and galactic central force.

Internal units: kpc and Myr; velocities are kpc/Myr. Submission impulses and
reported velocity errors use km/s. The original conversion constants are exact.
"""

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .. import ROOT

KPC_KM = 30856775814671900
YEAR_S = 31557600
VELOCITY = 1_000_000 * YEAR_S / KPC_KM
END = 90.0
TIME_TOL = 1e-6  # 1 year in Myr
POSITION_TOL = 1e-4  # kpc
VELOCITY_TOL = 0.01  # km/s
RANGE_TOL = 0.01  # kpc
COEFFICIENTS = (
    -1.94316e-12,
    3.7516e-10,
    -2.70559e-8,
    9.70521e-7,
    -1.88428e-5,
    0.000198502,
    -0.0010625,
    0.0023821,
    0.00287729,
)


def circular_speed(radius):
    """Speed in km/s. Horner evaluation follows the published polynomial."""
    value = COEFFICIENTS[0]
    for coefficient in COEFFICIENTS[1:]:
        value = value * radius + coefficient
    return 1 / value


def acceleration(position):
    radius2 = float(np.dot(position, position))
    radius = math.sqrt(radius2)
    speed = circular_speed(radius) * VELOCITY
    return -(speed * speed / radius2) * position


@dataclass(frozen=True)
class Catalog:
    radius: np.ndarray
    phase: np.ndarray
    final_angle: np.ndarray
    speed: np.ndarray
    p: np.ndarray
    q: np.ndarray

    def state(self, ident, time):
        angle = self.phase[ident] + self.speed[ident] / self.radius[ident] * time
        c, s = math.cos(angle), math.sin(angle)
        return np.r_[
            self.radius[ident] * (c * self.p[ident] + s * self.q[ident]),
            self.speed[ident] * (-s * self.p[ident] + c * self.q[ident]),
        ]


@lru_cache(maxsize=1)
def load_catalog():
    raw = np.loadtxt(ROOT / "problems/gtocx/data/stars.txt", delimiter=",", skiprows=1)
    if raw.shape != (100001, 6) or not np.all(np.isfinite(raw)):
        raise ValueError("Incomplete/non-finite GTOCX star catalogue")
    if not np.array_equal(raw[:, 0], np.arange(100001)) or not np.all(
        (raw[:, 1] >= 2) & (raw[:, 1] <= 32)
    ):
        raise ValueError("Invalid GTOCX star identifiers or radii")
    inc, node, phase, final_angle = np.deg2rad(raw[:, 2:]).T
    p = np.column_stack((np.cos(node), np.sin(node), np.zeros(len(node))))
    q = np.column_stack((-np.sin(node) * np.cos(inc), np.cos(node) * np.cos(inc), np.sin(inc)))
    arrays = [
        raw[:, 1].copy(),
        phase.copy(),
        final_angle.copy(),
        circular_speed(raw[:, 1]) * VELOCITY,
        p,
        q,
    ]
    for array in arrays:
        array.flags.writeable = False
    return Catalog(*arrays)
