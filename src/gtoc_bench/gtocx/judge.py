"""Validate a complete settlement tree, then compute J with time bonus B=1.

No participant solver or proprietary host code is executed. Independent DOP853
and Radau propagation and dense-polynomial extrema checks cover every flown arc.
"""

import hashlib
import math
import time
from pathlib import Path

import numpy as np
from numpy.polynomial import Polynomial
from scipy.integrate import solve_ivp

from . import REVISION
from .format import Rejection, parse
from .model import (
    POSITION_TOL,
    RANGE_TOL,
    TIME_TOL,
    VELOCITY,
    VELOCITY_TOL,
    acceleration,
    load_catalog,
)

TIMEOUT = 600
LIMITS = {
    "mother": (200.0, 500.0),
    "pod": (300.0, 300.0),
    "fast": (1500.0, 1500.0),
    "settler": (175.0, 400.0),
}


def revision_hash():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def position_polynomials(dense):
    """Use each pinned SciPy interpolator's exact polynomial representation."""
    if hasattr(dense, "F"):  # DOP853, degree seven.
        x = Polynomial([0.0, 1.0])
        result = []
        for component in range(3):
            p = Polynomial([0.0])
            for i, f in enumerate(reversed(dense.F)):
                p = (p + f[component]) * (x if i % 2 == 0 else 1 - x)
            result.append(p + dense.y_old[component])
        return result
    # Radau, degree three; unlike RK45 its Q already includes the step length.
    return [Polynomial(np.r_[dense.y_old[j], dense.Q[j]]) for j in range(3)]


def extrema_times(solution):
    values = []
    for dense in solution.sol.interpolants:
        p = position_polynomials(dense)
        derivative = sum(q * q for q in p).deriv()
        if not np.all(np.isfinite(derivative.coef)):
            raise Rejection(
                "NUMERICAL_EXTREMA", "Non-finite continuous-range polynomial", status="unverified"
            )
        points = [0.0, 0.5, 1.0]
        scale = float(np.max(np.abs(derivative.coef)))
        if scale > 1e-12:
            for root in derivative.trim(tol=scale * 1e-15).roots():
                if 0 < root.real < 1:
                    if abs(root.imag) < 1e-8:
                        points.append(float(root.real))
                    elif abs(root.imag) < 1e-5:
                        raise Rejection(
                            "NUMERICAL_EXTREMA",
                            "Ill-conditioned continuous-range extremum",
                            status="unverified",
                        )
        values.extend(dense.t_old + dense.h * np.array(points))
    return np.unique(values)


def score_settlements(ids, catalog, delta_v_used, delta_v_max):
    """Pure arithmetic for verified settlements; this function cannot accept a file."""
    ids = np.asarray(ids, dtype=int)
    n = len(ids)
    if not n or len(np.unique(ids)) != n or np.any(ids <= 0):
        raise Rejection("SCORE_SETTLEMENTS", "Scoring requires distinct, non-Sol settlements")
    if not math.isfinite(delta_v_used) or delta_v_used <= 0:
        raise Rejection(
            "ZERO_DELTA_V",
            "A finite positive total Delta-V is required for the merit ratio",
            status="unverified",
        )
    radial_grid = np.arange(31, dtype=float) + 2
    angular_grid = -math.pi + 2 * math.pi * np.arange(33) / 32
    s = 2 * math.pi / 32
    fr, ft = np.zeros(31), np.zeros(33)
    for first in range(0, n, 4096):
        selected = ids[first : first + 4096]
        fr += np.maximum(0, 1 - np.abs(radial_grid[:, None] - catalog.radius[selected])).sum(axis=1)
        # No angular wrapping: the original finite-domain PDF uses half-weight
        # endpoint targets at both -pi and +pi, not a periodic kernel.
        ft += (
            np.maximum(
                0, 1 - np.abs(angular_grid[:, None] - catalog.final_angle[selected]) / s
            ).sum(axis=1)
            / s
        )
    fr, ft = fr / n, ft / n
    gr = 2 * radial_grid / (32**2 - 2**2)
    gr[[0, -1]] *= np.array([0.5833, 0.4948])
    gt = np.full(33, 1 / (2 * math.pi))
    gt[[0, -1]] *= 0.5
    er = float(np.sum((fr / gr - 1) ** 2))
    et = float(np.sum((ft / gt - 1) ** 2))
    distribution = n / (1 + 1e-4 * n * (er + et))
    ratio = delta_v_max / delta_v_used
    return {
        "settled_stars": n,
        "radial_error": er,
        "angular_error": et,
        "distribution_term": distribution,
        "delta_v_used_km_s": delta_v_used,
        "delta_v_max_km_s": delta_v_max,
        "delta_v_ratio": ratio,
        "time_bonus": 1.0,
        "total": distribution * ratio,
    }


class Checker:
    def __init__(self, catalog, deadline):
        self.catalog = catalog
        self.deadline = deadline
        self.costs, self.capacities = [], []
        self.stats = {
            "propagated_segments": 0,
            "minimum_radius_kpc": float(catalog.radius[0]),
            "maximum_radius_kpc": float(catalog.radius[0]),
            "max_position_error_kpc": 0.0,
            "max_arrival_magnitude_error_km_s": 0.0,
            "max_arrival_vector_difference_km_s": 0.0,
            "max_solver_position_difference_kpc": 0.0,
            "max_solver_velocity_difference_km_s": 0.0,
        }

    def budget(self):
        if time.monotonic() >= self.deadline:
            raise Rejection(
                "VALIDATION_TIMEOUT",
                "Verification exceeded the numerical time budget; no score awarded",
                status="unverified",
            )

    def propagate(self, initial, alternate, start, end, line):
        self.budget()
        if end < start:
            raise Rejection("EVENT_ORDER", "Propagation cannot run backwards", line)
        if end == start:
            return initial, alternate

        def rhs(_, state):
            self.budget()
            radius = np.linalg.norm(state[:3])
            if not np.isfinite(radius) or not 0.5 < radius < 64:
                raise Rejection(
                    "PROPAGATION_DOMAIN",
                    "Numerical trajectory left the safe galactic domain",
                    line,
                    "unverified",
                )
            result = np.r_[state[3:], acceleration(state[:3])]
            if not np.all(np.isfinite(result)):
                raise Rejection(
                    "PROPAGATION_DOMAIN", "Non-finite galactic acceleration", line, "unverified"
                )
            return result

        settings = {
            "dense_output": True,
            "atol": [2e-12] * 3 + [2e-13] * 3,
            "max_step": min(2.0, end - start),
        }
        a = solve_ivp(rhs, (0.0, end - start), initial, method="DOP853", rtol=2e-11, **settings)
        b = solve_ivp(rhs, (0.0, end - start), alternate, method="Radau", rtol=1e-10, **settings)
        if not a.success or not b.success:
            raise Rejection(
                "PROPAGATION_FAILED", f"DOP853: {a.message}; Radau: {b.message}", line, "unverified"
            )
        self.budget()
        times = np.unique(np.r_[extrema_times(a), extrema_times(b)])
        states, other = a.sol(times), b.sol(times)
        dr = float(np.max(np.linalg.norm(states[:3] - other[:3], axis=0)))
        dv = float(np.max(np.linalg.norm(states[3:] - other[3:], axis=0))) / VELOCITY
        self.stats["max_solver_position_difference_kpc"] = max(
            self.stats["max_solver_position_difference_kpc"], dr
        )
        self.stats["max_solver_velocity_difference_km_s"] = max(
            self.stats["max_solver_velocity_difference_km_s"], dv
        )
        if 4 * dr > POSITION_TOL / 100 or 4 * dv > VELOCITY_TOL / 100:
            raise Rejection(
                "SOLVER_DISAGREEMENT",
                f"Independent propagations disagree: {dr:.9g} kpc, {dv:.9g} km/s",
                line,
                "unverified",
            )
        radii = np.r_[np.linalg.norm(states[:3], axis=0), np.linalg.norm(other[:3], axis=0)]
        low, high = float(np.min(radii)), float(np.max(radii))
        self.stats["minimum_radius_kpc"] = min(self.stats["minimum_radius_kpc"], low)
        self.stats["maximum_radius_kpc"] = max(self.stats["maximum_radius_kpc"], high)
        reserve = 4 * dr + 1e-10
        if low - reserve < 2 - RANGE_TOL or high + reserve > 32 + RANGE_TOL:
            certain = low + reserve < 2 - RANGE_TOL or high - reserve > 32 + RANGE_TOL
            raise Rejection(
                "GALACTIC_RANGE",
                f"Continuous range [{low:.12g}, {high:.12g}] kpc must stay in [1.99,32.01]; numerical reserve={reserve:.4g} kpc",
                line,
                "invalid" if certain else "unverified",
            )
        self.stats["propagated_segments"] += 1
        return a.y[:, -1], b.y[:, -1]

    def capture(self, primary, alternate, target, epoch, impulse, line, kind, prior_cost=0.0):
        reference = self.catalog.state(target, epoch)
        position_error = float(np.linalg.norm(primary[:3] - reference[:3]))
        vector_difference = (
            float(np.linalg.norm(primary[3:] + impulse * VELOCITY - reference[3:])) / VELOCITY
        )
        required = float(np.linalg.norm(reference[3:] - primary[3:])) / VELOCITY
        # The official tolerance explicitly compares arrival-impulse magnitudes.
        # Public host-accepted files include the opposite vector sign (Jena).
        # Reconstruct the physical arrival impulse from the propagated state;
        # all preceding impulses retain their full submitted vector directions.
        velocity_error = abs(required - float(np.linalg.norm(impulse)))
        rp = 4 * float(np.linalg.norm(primary[:3] - alternate[:3])) + 1e-10
        rv = 4 * float(np.linalg.norm(primary[3:] - alternate[3:])) / VELOCITY + 1e-8
        self.stats["max_position_error_kpc"] = max(
            self.stats["max_position_error_kpc"], position_error
        )
        self.stats["max_arrival_magnitude_error_km_s"] = max(
            self.stats["max_arrival_magnitude_error_km_s"], velocity_error
        )
        self.stats["max_arrival_vector_difference_km_s"] = max(
            self.stats["max_arrival_vector_difference_km_s"], vector_difference
        )
        if position_error + rp > POSITION_TOL:
            raise Rejection(
                "ARRIVAL_POSITION",
                f"Misses star {target} by {position_error:.12g} kpc (limit {POSITION_TOL}, reserve {rp:.4g})",
                line,
                "invalid" if position_error - rp > POSITION_TOL else "unverified",
            )
        if velocity_error + rv > VELOCITY_TOL:
            raise Rejection(
                "ARRIVAL_IMPULSE",
                f"Arrival impulse magnitude at star {target} differs by {velocity_error:.12g} km/s (limit {VELOCITY_TOL}, reserve {rv:.4g}); required physical impulse is reconstructed",
                line,
                "invalid" if velocity_error - rv > VELOCITY_TOL else "unverified",
            )
        single, total = LIMITS[kind]
        if (
            required + rv > single + VELOCITY_TOL
            or prior_cost + required + rv > total + VELOCITY_TOL
        ):
            raise Rejection(
                "ACTUAL_ARRIVAL_BUDGET",
                "The independently reconstructed arrival impulse exceeds the vessel's Delta-V limit",
                line,
            )

    def costs_for(self, vessel):
        single, total = LIMITS[vessel.kind]
        magnitudes = np.linalg.norm(vessel.impulses, axis=1)
        cost = float(math.fsum(magnitudes))
        if np.any(magnitudes > single + VELOCITY_TOL):
            raise Rejection(
                "SINGLE_IMPULSE",
                f"{vessel.kind}: impulse exceeds {single} km/s (+0.01 tolerance)",
                vessel.line,
            )
        if cost > total + VELOCITY_TOL:
            raise Rejection(
                "TOTAL_DELTA_V",
                f"{vessel.kind}: total {cost:.12g} km/s exceeds {total} (+0.01 tolerance)",
                vessel.line,
            )
        self.costs.append(cost)
        self.capacities.append(total)
        return magnitudes

    def mother(self, vessel):
        self.costs_for(vessel)
        first = float(vessel.times[0])
        if first > 10 + TIME_TOL:
            raise Rejection(
                "SOL_DEPARTURE",
                "All initial ships must leave Sol by 10 Myr (1 year tolerance)",
                vessel.line,
            )
        if vessel.times[-1] > max(p.time for p in vessel.pods):
            raise Rejection(
                "MOTHER_AFTER_LAST_POD",
                "No mother-ship impulses may follow the last pod release; subsequent motion is ignored",
                vessel.line,
            )
        for pod in vessel.pods:
            if pod.time < first:
                raise Rejection(
                    "POD_BEFORE_DEPARTURE",
                    "Pod release precedes the mother ship's departure",
                    pod.line,
                )
            magnitude = float(np.linalg.norm(pod.impulse))
            if magnitude > LIMITS["pod"][0] + VELOCITY_TOL:
                raise Rejection(
                    "SINGLE_IMPULSE", "Pod impulse exceeds 300 km/s (+0.01 tolerance)", pod.line
                )
            self.costs.append(magnitude)
            self.capacities.append(300.0)
        activities = [(float(t), vessel.line) for t in vessel.times]
        activities += [(p.time, p.line) for p in vessel.pods if np.any(p.impulse != 0)]
        activities.sort()
        for (a, _), (b, line) in zip(activities, activities[1:]):
            if b - a < 1 - TIME_TOL:
                raise Rejection(
                    "MOTHER_POD_SPACING",
                    "All mother impulses and nonzero pod impulses, both preceding and following, must be 1 Myr apart",
                    line,
                )
        events = [(float(t), 0, i) for i, t in enumerate(vessel.times)]
        events += [(p.time, 1, i) for i, p in enumerate(vessel.pods)]
        events.sort()
        state = alternate = self.catalog.state(0, first)
        previous = first
        before_burn = None
        burn_time = None
        for epoch, event, index in events:
            state, alternate = self.propagate(state, alternate, previous, epoch, vessel.line)
            if event == 0:
                before_burn = (state.copy(), alternate.copy())
                burn_time = epoch
                delta = np.r_[np.zeros(3), vessel.impulses[index] * VELOCITY]
                state, alternate = state + delta, alternate + delta
            else:
                pod = vessel.pods[index]
                # The organizer explicitly allows a zero-impulse pod at a mother
                # rendezvous. At coincident events it can detach before or after
                # the mother's burn, whichever actually matches the target star.
                choices = [(state, alternate)]
                if not np.any(pod.impulse) and burn_time == epoch:
                    choices.append(before_burn)
                reference = self.catalog.state(pod.target, epoch)
                chosen = min(
                    choices,
                    key=lambda pair: np.linalg.norm(
                        pair[0][3:] + pod.impulse * VELOCITY - reference[3:]
                    ),
                )
                self.capture(*chosen, pod.target, epoch, pod.impulse, pod.line, "pod")
            previous = epoch

    def transfer(self, vessel):
        magnitudes = self.costs_for(vessel)
        first = float(vessel.times[0])
        if vessel.kind == "fast" and first > 10 + TIME_TOL:
            raise Rejection(
                "SOL_DEPARTURE",
                "Fast Ship departure must be by 10 Myr (1 year tolerance)",
                vessel.line,
            )
        state = alternate = self.catalog.state(vessel.source, first)
        for i, (epoch, delta_v) in enumerate(zip(vessel.times, vessel.impulses)):
            if i:
                state, alternate = self.propagate(
                    state, alternate, vessel.times[i - 1], epoch, vessel.line
                )
            if i == len(vessel.times) - 1:
                self.capture(
                    state,
                    alternate,
                    vessel.target,
                    epoch,
                    delta_v,
                    vessel.line,
                    vessel.kind,
                    float(math.fsum(magnitudes[:-1])),
                )
            else:
                delta = np.r_[np.zeros(3), delta_v * VELOCITY]
                state, alternate = state + delta, alternate + delta

    def run(self, tree):
        for vessel in tree.mothers:
            self.budget()
            self.mother(vessel)
        for vessel in tree.fast + tree.settlers:
            self.budget()
            self.transfer(vessel)
        breakdown = score_settlements(
            list(tree.arrivals), self.catalog, math.fsum(self.costs), math.fsum(self.capacities)
        )
        return {
            "status": "valid",
            "score": breakdown["total"],
            "diagnostics": [],
            "score_breakdown": breakdown,
            "format_notes": tree.format_notes,
            "metrics": self.stats
            | {
                "mother_ships": len(tree.mothers),
                "settlement_pods": sum(len(m.pods) for m in tree.mothers),
                "fast_ships": len(tree.fast),
                "settler_ships": len(tree.settlers),
                "settled_stars": len(tree.arrivals),
                "last_settlement_myr": max(t for t, _ in tree.arrivals.values()),
            },
        }


def evaluate(data, timeout=TIMEOUT):
    started = time.monotonic()
    common = {
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "score": None,
        "score_unit": "points",
        "problem_revision": REVISION,
        "validator_revision": revision_hash(),
        "validator_kind": "independent_conservative",
    }
    try:
        tree = parse(data)
        catalog = load_catalog()
        with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
            result = Checker(catalog, started + timeout).run(tree)
    except Rejection as exc:
        result = {
            "status": exc.status,
            "score": None,
            "diagnostics": [str(exc)],
            "errors": [{"code": exc.code, "line": exc.line, "message": exc.message}],
        }
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
