"""Reconstructed interior controls: real accepted-reference and hostile inputs."""

import hashlib
import json

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from gtoc_bench import ROOT
from gtoc_bench.gtoc13.control import SailControl, reconstruct, relative_errors
from gtoc_bench.gtoc13.format import Rejection, parse
from gtoc_bench.gtoc13.judge import Checker, evaluate
from gtoc_bench.gtoc13.model import AU, load_bodies

FIXTURE = ROOT / "tests/fixtures/gtoc13/antipodes-submitted.csv"
REFERENCE_HASH = "6297881d46e0e1ea238c1cd9a9f612b15ab2e4cb27c880383c41d429a6ad89b8"


def prefix():
    with FIXTURE.open("rb") as stream:
        return b"".join(next(stream) for _ in range(20))


def test_first_ten_real_intervals_pass_unchanged_tolerances():
    result = evaluate(prefix())
    assert result["status"] == "valid", result
    assert result["metrics"]["segments"] == 10
    assert result["metrics"]["reconstructed_control_segments"] == 10
    assert result["metrics"]["max_sail_rk4_relative_error"] < 1e-4
    assert result["metrics"]["max_sail_truth_relative_error"] < 1e-4


def test_reconstruction_preserves_rows_and_reduces_first_interval_error():
    a, b = parse(prefix(), load_bodies())[0].rows[:2]
    before_a, before_b = a.state.copy(), b.state.copy()
    control, predicted, scales, before, evaluations = reconstruct(a, b, lambda: None)
    assert before[1] > 1  # v1 rejected this interval.
    assert relative_errors(predicted, b.state, scales)[1] < 0.1
    assert evaluations > 0
    assert np.array_equal(a.state, before_a) and np.array_equal(b.state, before_b)
    assert np.allclose(control.at(0), a.control, atol=1e-14, rtol=0)
    assert np.allclose(control.at(1), b.control, atol=1e-14, rtol=0)


def test_control_polynomials_match_orientation_on_restricted_intervals():
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.99, 0.1, 0.0])
    b /= np.linalg.norm(b)
    control = SailControl(a, b, [0.03, -0.02])
    polynomials = control.polynomials(0.2, 0.6)
    for local in (0.0, 0.17, 0.7, 1.0):
        assert np.allclose(
            [p(local) for p in polynomials], control.raw(0.2 + 0.6 * local), atol=1e-14
        )


def test_curved_normal_is_checked_interior_not_only_at_endpoints():
    from gtoc_bench.gtoc13.format import Row

    state = np.array([-200 * AU, 3 * AU, 0.0, 20.0, 0.0, 0.0])
    normal = np.array([0.0, 0.0, 1.0])
    control = SailControl(normal, normal, [0.0, 0.1])
    a, b = Row(1, 0, 1, 0, state, normal), Row(2, 0, 1, 100000, state, normal)
    assert normal @ state[:3] == 0
    with pytest.raises(Rejection, match="SAIL_CONE_INTERIOR"):
        Checker(load_bodies(), 20).integration(a, b, state, state, True, control)


def test_independent_curved_sail_trajectory_is_accepted():
    state = np.array([-200 * AU, 3 * AU, 0.0, 20.0, 0.0, 0.0])
    duration = 1e6

    def rhs(t, y):
        s = t / duration
        normal = np.array([1.0, -0.02 * 4 * s * (1 - s), 0.03 * 4 * s * (1 - s)])
        normal /= np.linalg.norm(normal)
        r, v = y[:3], y[3:]
        length = np.linalg.norm(r)
        force = -139348062043.343 * r / length**3
        force -= (
            (2 * 5.4026e-6 * 15000 / 500 / 1000)
            * (149597870.691 / length) ** 2
            * (normal @ r / length) ** 2
            * normal
        )
        return np.r_[v, force]

    truth = solve_ivp(
        rhs, (0, duration), state, method="Radau", rtol=3e-14, atol=[1e-7] * 3 + [1e-13] * 3
    )
    assert truth.success
    rows = [[0, 1, 0, *state, 1, 0, 0], [0, 1, duration, *truth.y[:, -1], 1, 0, 0]]
    data = (
        "\n".join(" ".join(format(float(x), ".17g") for x in row) for row in rows) + "\n"
    ).encode()
    result = evaluate(data)
    assert result["status"] == "valid", result
    assert result["metrics"]["reconstructed_control_segments"] == 1


@pytest.mark.parametrize("component", [6, 7, 8])
def test_control_fitting_cannot_supply_impossible_impulses(component):
    lines = prefix().decode().splitlines()
    # Keep the initial plane and the duplicated boundary consistent; the
    # independent dynamics must reject the forged arrival velocity itself.
    for index in (1, 2):
        fields = lines[index].split(",")
        fields[component] = format(float(fields[component]) + 0.001, ".17g")
        lines[index] = ",".join(fields)
    result = evaluate(("\n".join(lines) + "\n").encode())
    assert result["status"] in {"invalid", "unverified"} and result["score"] is None


@pytest.mark.slow
def test_complete_antipodes_submitted_reference_is_accepted(record_property):
    data = FIXTURE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == REFERENCE_HASH
    result = evaluate(data)
    record_property("verdict", json.dumps(result, allow_nan=False))
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(329.3314957879535, abs=1e-6)
    assert result["score_breakdown"]["science"] == pytest.approx(274.4429131566279, abs=1e-6)
    assert result["metrics"]["flybys"] == 252
    assert result["metrics"]["science_flybys"] == 133
    assert (
        result["metrics"]["linear_control_segments"]
        + result["metrics"]["reconstructed_control_segments"]
        == 23931
    )


def test_complete_reference_is_not_whitelisted():
    data = FIXTURE.read_bytes()
    lines = data.split(b"\n", 1)
    fields = lines[0].split(b",")
    fields[6] = format(float(fields[6]) + 0.001, ".17g").encode()
    result = evaluate(b",".join(fields) + b"\n" + lines[1])
    assert result["status"] in {"invalid", "unverified"} and result["score"] is None
