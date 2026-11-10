"""Recover admissible interior sail attitudes while preserving submitted endpoints.

The normalized quadratic has two tangent-plane degrees of freedom. Coefficients
are fixed before either independent propagation, so fitting never certifies itself.
"""

import numpy as np
from numpy.polynomial import Polynomial
from scipy.optimize import least_squares

from .format import Rejection
from .model import REL_TOL, acceleration


class SailControl:
    def __init__(self, first, last, parameters=None):
        first, last = np.asarray(first), np.asarray(last)
        if np.dot(first, last) < -1 + 1e-10:
            raise Rejection(
                "CONTROL_INTERPOLATION",
                "Antipodal normals need an intermediate output row",
                status="unverified",
            )
        middle = first + last
        middle = middle / np.linalg.norm(middle)
        axis = np.eye(3)[int(np.argmin(np.abs(middle)))]
        p = np.cross(middle, axis)
        p /= np.linalg.norm(p)
        q = np.cross(middle, p)
        self.parameters = np.zeros(2) if parameters is None else np.asarray(parameters, float)
        bend = self.parameters[0] * p + self.parameters[1] * q
        # n_raw(s) = (1-s)*n0+s*n1 + 4*s*(1-s)*bend.
        self.coefficients = np.array([first, last - first + 4 * bend, -4 * bend])

    def raw(self, fraction):
        c = self.coefficients
        return c[0] + fraction * (c[1] + fraction * c[2])

    def at(self, fraction):
        value = self.raw(fraction)
        return value / np.linalg.norm(value)

    def polynomials(self, offset=0.0, scale=1.0):
        x = Polynomial([offset, scale])
        return [Polynomial(self.coefficients[:, j])(x) for j in range(3)]


def rk4(a, b, control):
    """Exactly one RK4 step with endpoint attitudes and a shared midpoint attitude."""
    dt = b.t - a.t
    r, v = a.state[:3], a.state[3:]
    middle = control.at(0.5)
    k1 = acceleration(r, control.at(0.0))
    r2, v2 = r + dt * v / 2, v + dt * k1 / 2
    k2 = acceleration(r2, middle)
    r3, v3 = r + dt * v2 / 2, v + dt * k2 / 2
    k3 = acceleration(r3, middle)
    r4, v4 = r + dt * v3, v + dt * k3
    k4 = acceleration(r4, control.at(1.0))
    return np.r_[r + dt * (v + 2 * v2 + 2 * v3 + v4) / 6, v + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6]


def segment_scales(a, b):
    changes = np.array(
        [np.linalg.norm(b.state[:3] - a.state[:3]), np.linalg.norm(b.state[3:] - a.state[3:])]
    )
    if np.any(changes == 0) or not np.all(np.isfinite(changes)):
        raise Rejection(
            "RELATIVE_ERROR_SCALE", "Undefined position/velocity relative error denominator", b.line
        )
    return REL_TOL * changes


def relative_errors(predicted, reported, scales):
    return (
        np.array(
            [
                np.linalg.norm(predicted[:3] - reported[:3]),
                np.linalg.norm(predicted[3:] - reported[3:]),
            ]
        )
        / scales
    )


def reconstruct(a, b, budget):
    """Minimize the local defect; only a separately verified control may be accepted."""
    scales = segment_scales(a, b)
    linear = SailControl(a.control, b.control)
    predicted = rk4(a, b, linear)
    before = relative_errors(predicted, b.state, scales)
    if np.max(before) < 0.2:
        return linear, predicted, scales, before, 0

    def residual(parameters):
        budget()
        control = SailControl(a.control, b.control, parameters)
        endpoint = rk4(a, b, control)
        return (endpoint - b.state) / np.repeat(scales, 3)

    result = least_squares(
        residual,
        np.zeros(2),
        bounds=(-1.0, 1.0),
        max_nfev=48,
        ftol=1e-11,
        xtol=1e-11,
        gtol=1e-11,
    )
    fitted = SailControl(a.control, b.control, result.x)
    predicted = rk4(a, b, fitted)
    if np.linalg.norm(residual(result.x)) >= np.linalg.norm(residual(np.zeros(2))):
        return linear, rk4(a, b, linear), scales, before, int(result.nfev)
    return fitted, predicted, scales, before, int(result.nfev)
