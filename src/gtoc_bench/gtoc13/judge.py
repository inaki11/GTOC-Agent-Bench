"""Fail-closed trajectory validation followed by scoring.

Interior sail attitudes are reconstructed before independent DOP853 and Radau
verification. Sail output intervals use the published local relative tolerance;
an additional whole-arc conic closure requirement is not imposed.
"""

import hashlib
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from numpy.polynomial import Polynomial
from scipy.integrate import solve_ivp

from . import REVISION
from .control import SailControl, reconstruct, relative_errors
from .format import Rejection, parse
from .model import (
    AU,
    POS_TOL,
    REL_TOL,
    VEL_TOL,
    acceleration,
    kepler,
    load_bodies,
    velocity_factor,
)

TIMEOUT = 600


def revision_hash():
    h = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def real_roots(poly):
    """All roots on the closed interpolation interval, not a sampling grid."""
    if not np.all(np.isfinite(poly.coef)):
        raise Rejection("NUMERICAL_RANGE", "Non-finite dense interpolation", status="unverified")
    trimmed = poly.trim(tol=max(np.max(np.abs(poly.coef)), 1e-300) * 1e-15)
    roots = trimmed.roots() if trimmed.degree() else []
    result = []
    for root in roots:
        if 0 < root.real < 1:
            if abs(root.imag) < 1e-8:
                result.append(float(root.real))
            elif abs(root.imag) < 1e-5:
                raise Rejection(
                    "EXTREMUM_UNCERTAIN",
                    "Ill-conditioned interior extremum; use finer output steps",
                    status="unverified",
                )
    return sorted(set(result))


def position_polynomials(dense):
    """Exact polynomial representation of SciPy's pinned DOP853 dense output.

    Use its nested representation rather than fitting sampled states. The checker
    then finds extrema of r^2 (degree <=14) and sail illumination (degree <=9).
    """
    x = Polynomial([0, 1])
    polynomials = []
    for component in range(3):
        p = Polynomial([0.0])
        for i, f in enumerate(reversed(dense.F)):
            p = (p + f[component] / AU) * (x if i % 2 == 0 else 1 - x)
        polynomials.append(p + dense.y_old[component] / AU)
    return polynomials


class RangeMonitor:
    def __init__(self):
        self.minimum = math.inf
        self.first_perihelion = None
        self.deep_entries = 0
        self.deep_minima = 0
        self.inside = False
        self.last_sign = 0
        self.last_time = None
        self.last_radius = None
        self.last_error = 0.0

    def consume(self, times, radii, uncertainty, line):
        lower = 0.01 * AU - 1.0
        ordinary = 0.05 * AU - 1.0
        for epoch, radius in zip(times, radii):
            self.minimum = min(self.minimum, radius)
            if radius - uncertainty < lower:
                raise Rejection(
                    "PERIHELION_FLOOR",
                    f"Range {radius / AU:.12g} AU is not safely above 0.01 AU (1 km tolerance)",
                    line,
                )
            deep = radius - uncertainty < ordinary
            if deep and not self.inside:
                self.deep_entries += 1
                if self.deep_entries > 1:
                    raise Rejection(
                        "REPEATED_DEEP_PASS",
                        "More than one passage below 0.05 AU (1 km tolerance)",
                        line,
                    )
            self.inside = deep
            if self.last_time is not None and epoch > self.last_time:
                delta = radius - self.last_radius
                band = uncertainty + self.last_error + 1e-8
                sign = 1 if delta > band else -1 if delta < -band else 0
                if sign > 0 and self.last_sign < 0:
                    if self.first_perihelion is None:
                        self.first_perihelion = self.last_time
                    if self.last_radius - self.last_error < ordinary:
                        self.deep_minima += 1
                        if self.deep_minima > 1:
                            raise Rejection(
                                "REPEATED_DEEP_PASS",
                                "Multiple perihelia within the deep passage",
                                line,
                            )
                if sign:
                    self.last_sign = sign
            self.last_time, self.last_radius, self.last_error = epoch, radius, uncertainty


class Checker:
    def __init__(self, bodies, timeout):
        self.bodies = bodies
        self.deadline = time.monotonic() + timeout
        self.range = RangeMonitor()
        self.flybys = []
        self.propagated = None
        self.alternate = None
        self.arrival_error = np.zeros(2)
        self.stats = {
            "segments": 0,
            "max_position_residual_km": 0.0,
            "max_velocity_residual_km_s": 0.0,
            "max_solver_position_difference_km": 0.0,
            "max_solver_velocity_difference_km_s": 0.0,
            "linear_control_segments": 0,
            "reconstructed_control_segments": 0,
            "control_fit_evaluations": 0,
            "max_control_bend": 0.0,
            "max_sail_position_defect_km": 0.0,
            "max_sail_velocity_defect_km_s": 0.0,
            "max_sail_rk4_relative_error": 0.0,
            "max_sail_truth_relative_error": 0.0,
        }

    def budget(self):
        if time.monotonic() >= self.deadline:
            raise Rejection(
                "VALIDATION_TIMEOUT",
                "Numerical verification exceeded its time budget; no score awarded",
                status="unverified",
            )

    @staticmethod
    def control(a, b, fraction):
        raw = (1 - fraction) * a + fraction * b
        return raw / np.linalg.norm(raw)

    def integration(self, a, b, initial, alternate, sail, control=None):
        self.budget()
        duration = b.t - a.t
        if sail:
            control = control or SailControl(a.control, b.control)
            normal_polynomials = control.polynomials()
            norm_squared = sum(p * p for p in normal_polynomials)
            if min(norm_squared(x) for x in [0.0, *real_roots(norm_squared.deriv()), 1.0]) <= 1e-16:
                raise Rejection(
                    "CONTROL_SINGULARITY",
                    "Interior normal cannot safely be normalized",
                    a.line,
                    "unverified",
                )

        def rhs(t, state):
            self.budget()
            radius = np.linalg.norm(state[:3])
            if not np.isfinite(radius) or radius < 0.005 * AU:
                raise Rejection(
                    "PROPAGATION_DOMAIN",
                    "Integrator approached the stellar singularity; refine the trajectory",
                    a.line,
                    "unverified",
                )
            normal = control.at(t) if sail else None
            result = duration * np.r_[state[3:], acceleration(state[:3], normal)]
            if not np.all(np.isfinite(result)):
                raise Rejection(
                    "PROPAGATION_DOMAIN", "Non-finite differential equation", a.line, "unverified"
                )
            return result

        atol = np.array([1e-6] * 3 + [1e-12] * 3)
        primary = solve_ivp(
            rhs,
            (0.0, 1.0),
            initial,
            method="DOP853",
            rtol=3e-14,
            atol=atol,
            dense_output=True,
            first_step=1.0 if sail else None,
        )
        secondary = solve_ivp(
            rhs,
            (0.0, 1.0),
            alternate,
            method="Radau",
            rtol=3e-13,
            atol=atol,
            dense_output=True,
            first_step=1.0 if sail else None,
        )
        if not primary.success or not secondary.success:
            raise Rejection(
                "INTEGRATION_FAILURE",
                f"DOP853: {primary.message}; Radau: {secondary.message}",
                a.line,
                "unverified",
            )
        self.stats["segments"] += 1
        # Endpoint and continuous constraints are checked against independent
        # numerical results; discrepancies consume, rather than enlarge, tolerances.
        errors = np.abs(primary.y[:, -1] - secondary.y[:, -1])
        for dense in primary.sol.interpolants:
            p = position_polynomials(dense)
            norm_squared = sum(q * q for q in p)
            radius_x = [0.0, *real_roots(norm_squared.deriv()), 1.0]
            cone_x = []
            if sail:
                n = control.polynomials(dense.t_old, dense.h)
                illumination = -sum(v * q for v, q in zip(n, p))
                cone_x = [0.0, *real_roots(illumination.deriv()), 1.0]
            xs = sorted(set(radius_x + cone_x + [0.5]))
            ts = dense.t_old + dense.h * np.array(xs)
            states, other = dense(ts), secondary.sol(ts)
            difference = states - other
            dr = float(np.max(np.linalg.norm(difference[:3], axis=0)))
            dv = float(np.max(np.linalg.norm(difference[3:], axis=0)))
            self.stats["max_solver_position_difference_km"] = max(
                self.stats["max_solver_position_difference_km"], dr
            )
            self.stats["max_solver_velocity_difference_km_s"] = max(
                self.stats["max_solver_velocity_difference_km_s"], dv
            )
            if 4 * dr > POS_TOL / 4 or 4 * dv > VEL_TOL / 4:
                raise Rejection(
                    "SOLVER_DISAGREEMENT",
                    f"Independent solvers disagree (position={dr:.6g} km, velocity={dv:.6g} km/s); refine the output mesh",
                    a.line,
                    "unverified",
                )
            if sail:
                for x in cone_x:
                    t = dense.t_old + dense.h * x
                    normal_raw = control.raw(t)
                    projection_error = (
                        4 * abs(np.dot(normal_raw, (dense(t) - secondary.sol(t))[:3])) / AU
                    )
                    if illumination(x) - projection_error < -1e-14:
                        raise Rejection(
                            "SAIL_CONE_INTERIOR",
                            "Sail orientation cannot be verified within [0,90] degrees throughout the segment",
                            a.line,
                            "unverified",
                        )
            times = a.t + duration * (dense.t_old + dense.h * np.array(radius_x))
            radii = [np.linalg.norm([q(x) for q in p]) * AU for x in radius_x]
            self.range.consume(times, radii, 4 * dr + 1e-6, a.line)
        return (
            primary.y[:, -1],
            secondary.y[:, -1],
            np.array(
                [4 * np.linalg.norm(errors[:3]) + 1e-6, 4 * np.linalg.norm(errors[3:]) + 1e-12]
            ),
        )

    def endpoint(self, predicted, reported, uncertainty, line, code):
        dr = float(np.linalg.norm(predicted[:3] - reported[:3]))
        dv = float(np.linalg.norm(predicted[3:] - reported[3:]))
        self.stats["max_position_residual_km"] = max(self.stats["max_position_residual_km"], dr)
        self.stats["max_velocity_residual_km_s"] = max(self.stats["max_velocity_residual_km_s"], dv)
        if dr + uncertainty[0] > POS_TOL or dv + uncertainty[1] > VEL_TOL:
            # A failed conservative acceptance test is not proof of a violation.
            # Keep an overlapping uncertainty interval unscored and unverified.
            status = (
                "invalid"
                if dr - uncertainty[0] > POS_TOL or dv - uncertainty[1] > VEL_TOL
                else "unverified"
            )
            raise Rejection(
                code,
                f"Endpoint mismatch: position={dr:.9g} km (limit {POS_TOL}), velocity={dv:.9g} km/s (limit {VEL_TOL}); numerical margins {uncertainty.tolist()}",
                line,
                status,
            )

    def conic(self, arc):
        a, b = arc.rows
        initial = a.state if self.propagated is None else self.propagated
        alternate = a.state if self.alternate is None else self.alternate
        analytical = kepler(initial[:3], initial[3:], b.t - a.t)
        primary, secondary, error = self.integration(a, b, initial, alternate, False)
        independent_error = [
            np.linalg.norm(analytical[:3] - primary[:3]),
            np.linalg.norm(analytical[3:] - primary[3:]),
        ]
        error = error + 4 * np.array(independent_error)
        self.endpoint(analytical, b.state, error, b.line, "CONIC_DYNAMICS")
        self.propagated, self.alternate, self.arrival_error = analytical, secondary, error

    def sail(self, arc):
        for a, b in zip(arc.rows, arc.rows[1:]):
            if a.t == b.t:
                continue
            self.budget()
            control, rk4, scales, before, evaluations = reconstruct(a, b, self.budget)
            fitted = bool(np.any(control.parameters != 0))
            self.stats[
                "reconstructed_control_segments" if fitted else "linear_control_segments"
            ] += 1
            self.stats["control_fit_evaluations"] += evaluations
            self.stats["max_control_bend"] = max(
                self.stats["max_control_bend"], float(np.linalg.norm(control.parameters))
            )
            rk_errors = relative_errors(rk4, b.state, scales)
            self.stats["max_sail_rk4_relative_error"] = max(
                self.stats["max_sail_rk4_relative_error"], float(np.max(rk_errors)) * REL_TOL
            )
            if np.any(rk_errors >= 1):
                raise Rejection(
                    "RK4_ACCURACY",
                    f"Position/velocity errors in units of the published tolerance: {rk_errors.tolist()}; interior-control reconstruction could not meet both limits",
                    b.line,
                    "unverified",
                )
            # The published check is local to each submitted output interval.
            # Reconstructed controls are now fixed: fitting is never acceptance.
            primary, alternate, error = self.integration(a, b, a.state, a.state, True, control)
            defects = np.array(
                [
                    np.linalg.norm(primary[:3] - b.state[:3]),
                    np.linalg.norm(primary[3:] - b.state[3:]),
                ]
            )
            truth_errors = (defects + error) / scales
            self.stats["max_sail_position_defect_km"] = max(
                self.stats["max_sail_position_defect_km"], float(defects[0])
            )
            self.stats["max_sail_velocity_defect_km_s"] = max(
                self.stats["max_sail_velocity_defect_km_s"], float(defects[1])
            )
            self.stats["max_sail_truth_relative_error"] = max(
                self.stats["max_sail_truth_relative_error"], float(np.max(truth_errors)) * REL_TOL
            )
            if np.any(truth_errors >= 1):
                raise Rejection(
                    "SAIL_DYNAMICS",
                    f"Independent propagation position/velocity errors in units of the published tolerance: {truth_errors.tolist()}",
                    b.line,
                    "unverified",
                )
        # X_f is an accepted numerical node under the published local test. The
        # next arc/flyby starts at that node; a conic absolute tolerance is not
        # applied again to the sail interval's already-checked relative defect.
        self.propagated = arc.rows[-1].state.copy()
        self.alternate = self.propagated.copy()
        self.arrival_error = np.zeros(2)

    def flyby(self, arc):
        a, b = arc.rows[0], arc.rows[-1]
        body = self.bodies[a.body]
        r, v = body.state(a.t)
        # Arrival tolerance cannot be spent once on dynamics and a second time
        # on the encounter. Compare the continuously propagated state itself.
        actual = a.state if self.propagated is None else self.propagated
        alternate = a.state if self.alternate is None else self.alternate
        reserve = self.arrival_error
        propagated_miss = np.linalg.norm(actual[:3] - r)
        if propagated_miss + reserve[0] > POS_TOL:
            raise Rejection(
                "FLYBY_POSITION",
                f"Propagated arrival misses body {a.body} by {propagated_miss:.9g} km "
                f"(limit {POS_TOL}, numerical margin {reserve[0]:.6g})",
                a.line,
            )
        for row in arc.rows:
            position_error = np.linalg.norm(row.state[:3] - r)
            if position_error > POS_TOL:
                raise Rejection(
                    "FLYBY_POSITION",
                    f"Misses body {a.body} by {position_error:.9g} km (limit {POS_TOL})",
                    row.line,
                )
            vinf_error = np.linalg.norm(row.control - (row.state[3:] - v))
            if vinf_error > VEL_TOL:
                raise Rejection(
                    "FLYBY_VINF",
                    f"Declared v-infinity disagrees with ephemeris by {vinf_error:.9g} km/s",
                    row.line,
                )
        incoming, outgoing = actual[3:] - v, b.state[3:] - v
        if np.linalg.norm(a.control - incoming) + reserve[1] > VEL_TOL:
            raise Rejection(
                "FLYBY_VINF",
                "Incoming v-infinity disagrees with continuously propagated arrival",
                a.line,
            )
        speed = np.linalg.norm(incoming)
        if len(arc.rows) == 2:
            if body.gm == 0:
                if np.linalg.norm(b.state[3:] - actual[3:]) + reserve[1] > VEL_TOL:
                    raise Rejection(
                        "MASSLESS_FLYBY",
                        "A massless body cannot change the spacecraft velocity",
                        b.line,
                    )
            else:
                out_speed = np.linalg.norm(outgoing)
                if abs(speed - out_speed) + reserve[1] > VEL_TOL:
                    raise Rejection(
                        "FLYBY_SPEED",
                        f"Incoming/outgoing v-infinity magnitudes differ by {abs(speed - out_speed):.9g} km/s",
                        b.line,
                    )
                if min(speed, out_speed) <= 0:
                    raise Rejection(
                        "FLYBY_GEOMETRY",
                        "A planetary gravity assist requires non-zero v-infinity",
                        a.line,
                    )
                # Stable half-angle formula, including near-zero turning angles.
                sine_half = np.linalg.norm(incoming / speed - outgoing / out_speed) / 2
                if sine_half <= 0 or sine_half > 1 + 1e-14:
                    raise Rejection(
                        "FLYBY_GEOMETRY",
                        "Zero or invalid turn cannot satisfy the finite altitude bounds",
                        a.line,
                    )
                periapsis = body.gm / speed**2 * (1 / min(1.0, sine_half) - 1)
                other_incoming = alternate[3:] - v
                other_speed = np.linalg.norm(other_incoming)
                other_sine = np.linalg.norm(other_incoming / other_speed - outgoing / out_speed) / 2
                if other_sine <= 0:
                    raise Rejection(
                        "FLYBY_GEOMETRY", "Uncertain planetary turn", a.line, "unverified"
                    )
                other_periapsis = body.gm / other_speed**2 * (1 / min(1.0, other_sine) - 1)
                altitude_reserve = 4 * abs(periapsis - other_periapsis) + 1e-6
                if not (
                    1.1 * body.radius - POS_TOL <= periapsis - altitude_reserve
                    and periapsis + altitude_reserve <= 101 * body.radius + POS_TOL
                ):
                    raise Rejection(
                        "FLYBY_ALTITUDE",
                        f"Altitude={periapsis - body.radius:.9g} km; permitted [{0.1 * body.radius:.9g}, {100 * body.radius:.9g}] km (+/-100 m)",
                        a.line,
                    )
        if self.flybys and self.flybys[-1]["body"] == a.body:
            elapsed = a.t - self.flybys[-1]["epoch"]
            if elapsed < body.period / 3:
                raise Rejection(
                    "FLYBY_SPACING",
                    f"Consecutive encounters of body {a.body} are {elapsed:.9g} s apart; minimum is {body.period / 3:.9g} s",
                    a.line,
                )
        if a.flag == 1:
            count = sum(x["body"] == a.body and x["science"] for x in self.flybys)
            if count >= 13:
                raise Rejection(
                    "SCIENCE_LIMIT", f"More than 13 scientific flybys of body {a.body}", a.line
                )
            if a.body > 1000 and (
                self.range.first_perihelion is None or a.t < self.range.first_perihelion
            ):
                raise Rejection(
                    "SCIENCE_BEFORE_PERIHELION",
                    "Asteroid/comet science requires a previously verified stellar perihelion",
                    a.line,
                )
        self.flybys.append(
            {
                "body": a.body,
                "science": a.flag == 1,
                "epoch": a.t,
                "rhat": r / np.linalg.norm(r),
                "vinf": float(speed),
                "line": a.line,
            }
        )
        if self.propagated is not None and len(arc.rows) == 2 and body.gm:
            # Use the declared outgoing direction with exactly conserved v-infinity
            # magnitude. Do not turn velocity tolerances into repeated free impulses.
            direction = outgoing / np.linalg.norm(outgoing)
            self.propagated = np.r_[actual[:3], v + speed * direction]
            self.alternate = np.r_[alternate[:3], v + np.linalg.norm(alternate[3:] - v) * direction]
        # A massless encounter changes neither independently propagated state.

    def run(self, arcs):
        first = arcs[0].rows[0]
        if abs(first.state[0] + 200 * AU) > POS_TOL or np.linalg.norm(first.state[4:6]) > VEL_TOL:
            raise Rejection(
                "INITIAL_STATE",
                "Initial x must be -200 AU (+/-100 m); Vy,Vz must be zero (0.1 mm/s vector tolerance)",
                first.line,
            )
        for arc in arcs:
            self.budget()
            getattr(self, arc.kind)(arc)
        breakdown = score_flybys(self.flybys, self.bodies)
        return {
            "status": "valid",
            "score": breakdown["total"],
            "diagnostics": [],
            "score_breakdown": breakdown,
            "metrics": self.stats
            | {
                "flybys": len(self.flybys),
                "science_flybys": sum(x["science"] for x in self.flybys),
                "initial_vx_km_s": float(first.state[3]),
                "duration_seconds": arcs[-1].rows[-1].t - first.t,
                "minimum_radius_au": self.range.minimum / AU,
                "first_perihelion_seconds": self.range.first_perihelion,
            },
        }


def score_flybys(flybys, bodies):
    """Merit calculation for already-validated encounters; never an acceptance test."""
    history, by_body = defaultdict(list), defaultdict(float)
    for flyby in flybys:
        if not flyby["science"]:
            continue
        ident, direction = flyby["body"], flyby["rhat"]
        prior = history[ident]
        if len(prior) >= 13:
            raise Rejection("SCIENCE_LIMIT", f"More than 13 scientific flybys of body {ident}")
        total = math.fsum(
            math.exp(
                -(math.degrees(math.acos(float(np.clip(np.dot(direction, p), -1, 1)))) ** 2) / 50
            )
            for p in prior
        )
        seasonal = 0.1 + 0.9 / (1 + 10 * total)
        by_body[ident] += bodies[ident].weight * seasonal * velocity_factor(flyby["vinf"])
        prior.append(direction)
    visited = set(history)
    grand = (
        1.2
        if set(range(1, 11)) | {1000} <= visited and sum(k > 1000 for k in visited) >= 13
        else 1.0
    )
    science = math.fsum(by_body.values())
    return {
        "science": science,
        "grand_tour_bonus": grand,
        "time_bonus": 1.0,
        "by_body": {str(k): v for k, v in sorted(by_body.items())},
        "total": grand * science,
    }


def evaluate(data, timeout=TIMEOUT):
    started = time.monotonic()
    common = {
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "score_unit": "points",
        "problem_revision": REVISION,
        "validator_revision": revision_hash(),
        "validator_kind": "independent_reconstructed_controls",
        "sail_control_family": "normalized_quadratic_with_fixed_endpoints",
        "sail_verification_policy": "published_relative_tolerance_per_output_interval",
        "score": None,
    }
    checker = None
    try:
        bodies = load_bodies()
        arcs = parse(data, bodies)
        with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
            checker = Checker(bodies, timeout)
            result = checker.run(arcs)
    except Rejection as exc:
        result = {
            "status": exc.status,
            "score": None,
            "diagnostics": [str(exc)],
            "errors": [{"code": exc.code, "line": exc.line, "message": exc.message}],
        }
        if checker is not None:
            result["metrics"] = checker.stats | {"verified_flybys": len(checker.flybys)}
    except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
        result = {
            "status": "unverified",
            "score": None,
            "diagnostics": [f"NUMERICAL_FAILURE: {type(exc).__name__}: {exc}"],
        }
    except Exception as exc:
        result = {
            "status": "validator_error",
            "score": None,
            "diagnostics": [f"VALIDATOR_FAILURE: {type(exc).__name__}: {exc}"],
        }
    if result["status"] == "valid" and (not math.isfinite(result["score"]) or result["score"] < 0):
        result = {"status": "validator_error", "score": None, "diagnostics": ["NONFINITE_SCORE"]}
    return common | result | {"validation_seconds": time.monotonic() - started}
