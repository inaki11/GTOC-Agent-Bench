"""GTOC13 constants, analytical ephemerides and two-body propagation.

All dynamics use kilometres and seconds. Constants come from the original
statement (in particular, its AU, rather than a library's different AU).
"""

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from .. import ROOT

AU = 149597870.691
MU = 139348062043.343
DAY = 86400.0
YEAR = 365.25 * DAY
END = 200 * YEAR
SAIL = 2 * 5.4026e-6 * 15000 / 500 / 1000  # km/s^2 at one AU
POS_TOL = 0.1
VEL_TOL = 1e-7
REL_TOL = 1e-4
DATA = ROOT / "problems/gtoc13/data"


@dataclass(frozen=True)
class Body:
    id: int
    a: float
    e: float
    inc: float
    node: float
    arg: float
    mean: float
    weight: float
    gm: float = 0.0
    radius: float = 0.0

    @property
    def period(self):
        return 2 * math.pi * math.sqrt(self.a**3 / MU)

    def state(self, epoch):
        m = math.remainder(self.mean + 2 * math.pi * epoch / self.period, 2 * math.pi)
        anomaly = brentq(
            lambda x: x - self.e * math.sin(x) - m, -math.pi, math.pi, xtol=5e-15, rtol=1e-15
        )
        ce, se = math.cos(anomaly), math.sin(anomaly)
        beta = math.sqrt(1 - self.e**2)
        r = np.array([self.a * (ce - self.e), self.a * beta * se, 0.0])
        v = math.sqrt(MU / self.a) / (1 - self.e * ce) * np.array([-se, beta * ce, 0.0])
        co, so = math.cos(self.node), math.sin(self.node)
        cw, sw = math.cos(self.arg), math.sin(self.arg)
        ci, si = math.cos(self.inc), math.sin(self.inc)
        rotation = np.array(
            [
                [co * cw - so * sw * ci, -co * sw - so * cw * ci, so * si],
                [so * cw + co * sw * ci, -so * sw + co * cw * ci, -co * si],
                [sw * si, cw * si, ci],
            ]
        )
        return rotation @ r, rotation @ v


def load_bodies(directory=DATA):
    bodies = {}
    for name in ("planets", "asteroids", "comets"):
        raw = (Path(directory) / f"gtoc13_{name}.csv").read_bytes()
        # The supplied planet catalogue uses Windows-1252 for Beyonce's name.
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("cp1252")
        for row in csv.reader(content.splitlines()):
            if not row or row[0].lstrip().startswith("#"):
                continue
            ident = int(row[0])
            offset = 4 if name == "planets" else 1
            a, e, inc, node, arg, mean, weight = map(float, row[offset:])
            bodies[ident] = Body(
                ident,
                a,
                e,
                *np.deg2rad([inc, node, arg, mean]),
                weight,
                float(row[2]) if name == "planets" else 0.0,
                float(row[3]) if name == "planets" else 0.0,
            )
    expected = set(range(1, 11)) | set(range(1000, 1258)) | set(range(2001, 2043))
    if set(bodies) != expected:
        raise ValueError("Incomplete GTOC13 ephemeris catalogue")
    return bodies


def stumpff(z):
    if abs(z) < 1e-4:
        c = sum((-z) ** k / math.factorial(2 * k + 2) for k in range(8))
        s = sum((-z) ** k / math.factorial(2 * k + 3) for k in range(8))
    elif z > 0:
        q = math.sqrt(z)
        c, s = 2 * math.sin(q / 2) ** 2 / z, (q - math.sin(q)) / q**3
    else:
        q = math.sqrt(-z)
        c, s = (math.cosh(q) - 1) / (-z), (math.sinh(q) - q) / q**3
    return c, s


def kepler(r0, v0, seconds):
    """Universal-variable f/g propagation, independent of the numerical ODE solver."""
    r0, v0 = np.asarray(r0, float), np.asarray(v0, float)
    if seconds == 0:
        return np.r_[r0, v0]
    length = np.linalg.norm(r0)
    time_unit = math.sqrt(length**3 / MU)
    r, v, dt = r0 / length, v0 * time_unit / length, seconds / time_unit
    alpha, radial = 2 - np.dot(v, v), np.dot(r, v)

    def equation(x):
        c, s = stumpff(alpha * x * x)
        return radial * x * x * c + (1 - alpha) * x**3 * s + x - dt

    direction = math.copysign(1, dt)
    hi = direction * min(1.0, abs(dt))
    for _ in range(100):
        try:
            bracketed = direction * equation(hi) >= 0
        except OverflowError:
            bracketed = True
        if bracketed:
            break
        hi *= 2
    else:
        raise ArithmeticError("Universal Kepler equation could not be bracketed")

    def bounded_equation(x):
        try:
            return equation(x)
        except OverflowError:
            return math.copysign(float("inf"), x)

    x = brentq(bounded_equation, min(0, hi), max(0, hi), xtol=5e-15, rtol=2e-15)
    c, s = stumpff(alpha * x * x)
    rf = (1 - x * x * c) * r + (dt - x**3 * s) * v
    norm = np.linalg.norm(rf)
    vf = (alpha * x**3 * s - x) / norm * r + (1 - x * x * c / norm) * v
    return np.r_[rf * length, vf * length / time_unit]


def acceleration(position, normal=None):
    radius = np.linalg.norm(position)
    value = -MU * position / radius**3
    if normal is not None:
        cosine = -np.dot(normal, position) / radius
        value = value - SAIL * (AU / radius) ** 2 * cosine**2 * normal
    return value


def velocity_factor(speed):
    return 0.2 + math.exp(-speed / 13) / (1 + math.exp(-5 * (speed - 1.5)))
