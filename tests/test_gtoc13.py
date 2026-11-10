"""Independent references, physical counterexamples and benchmark regression tests."""

import copy
import hashlib
import json
import math
import subprocess
import sys

import numpy as np
import pytest
from numpy.polynomial import Polynomial
from scipy.integrate import solve_ivp

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.gtoc13.format import Arc, Rejection, Row
from gtoc_bench.gtoc13.judge import Checker, evaluate, real_roots, score_flybys
from gtoc_bench.gtoc13.model import AU, END, MU, Body, kepler, load_bodies, velocity_factor
from gtoc_bench.problems import client_timeout, specification
from gtoc_bench.validator import verify_materials

FIXTURES = ROOT / "tests/fixtures/gtoc13"


@pytest.fixture(scope="module")
def bodies():
    return load_bodies()


def encode(rows):
    return (
        "\n".join(" ".join(format(float(x), ".17g") for x in row) for row in rows) + "\n"
    ).encode()


def decode(data):
    return [
        list(map(float, line.replace(",", " ").split()))
        for line in data.decode().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "!"))
    ]


def check_reject(data, code=None):
    result = evaluate(data)
    assert result["status"] in {"invalid", "unverified"}, result
    assert result["score"] is None and result["diagnostics"], result
    if code:
        assert result["errors"][0]["code"] == code, result
    return result


@pytest.mark.parametrize(
    "filename,score,count",
    [
        ("planetx-conic.txt", 21.92704582292371, 1),
        ("kaist-high-score.txt", 111.20732893722135, 6),
    ],
)
def test_reference_trajectories_pass_full_validation(filename, score, count):
    data = (FIXTURES / filename).read_bytes()
    result = evaluate(data)
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(score, abs=1e-9)
    assert result["score_unit"] == "points"
    assert result["score_breakdown"]["time_bonus"] == 1
    assert result["metrics"]["science_flybys"] == count
    assert result["artifact_sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "column,value,code",
    [
        (0, 1.5, "IDENTIFIER"),
        (0, 999, "BODY_ID"),
        (1, 2, "FLAG"),
        (1, 0.5, "IDENTIFIER"),
        (2, -1, "TIME_WINDOW"),
        (2, END + 1, "TIME_WINDOW"),
        (3, float("nan"), "NONFINITE"),
        (3, float("inf"), "NONFINITE"),
        (4, -float("inf"), "NONFINITE"),
        (9, 1, "CONIC_CONTROL"),
    ],
)
def test_strict_numeric_format(column, value, code):
    rows = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    rows[0][column] = value
    check_reject(encode(rows), code)


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "FILE_SIZE"),
        (b"# comment\n!ignored\n", "INITIAL_ARC"),
        (b"1 2 3\n", "COLUMNS"),
        (b"0,,0 0 0 0 0 0 0 0 0 0", "COLUMNS"),
        (b"\xff", "ENCODING"),
        (b"0 0 0 1 0 0 0 0 0 0 0 invalid", "NUMBER"),
    ],
)
def test_empty_malformed_input(data, code):
    check_reject(data, code)


def test_comments_commas_and_final_newline():
    rows = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    data = b"# header\n\n! comment\n" + encode(rows).replace(b" ", b", \t")
    assert evaluate(data)["status"] == "valid"


@pytest.mark.parametrize(
    "row,column,change,code",
    [
        (0, 3, 1, "INITIAL_STATE"),
        (0, 7, 1e-4, "INITIAL_STATE"),
        (1, 3, 1, "CONTINUITY"),
        (1, 6, 1e-4, "CONTINUITY"),
        (2, 2, 1, "CONTINUITY"),
        (2, 9, 1e-4, "FLYBY_VINF"),
    ],
)
def test_invalid_initial_boundary_or_declared_vinf(row, column, change, code):
    rows = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    rows[row][column] += change
    check_reject(encode(rows), code)


def test_forged_conic_arrival_and_velocity_are_rejected():
    original = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    for column, delta in ((3, 10), (6, 1e-4)):
        rows = copy.deepcopy(original)
        for row in rows[1:]:
            row[column] += delta
        check_reject(encode(rows), "CONIC_DYNAMICS")


@pytest.mark.parametrize(
    "position,velocity,margin,expected",
    [
        # Astra: both residuals pass, but the velocity uncertainty exceeds the limit.
        (
            0.006134656408348334,
            5.377791927553734e-8,
            [0.02518979034249815, 2.1800833104437443e-7],
            "unverified",
        ),
        (0.101, 0.0, [0.005, 0.0], "unverified"),
        (0.0, 1.1e-7, [0.0, 0.2e-7], "unverified"),
        # A separate Astra candidate was demonstrably outside the position limit.
        (
            0.14553732753639737,
            1.1797999032026934e-10,
            [0.0013150627044584405, 1.4752970401061182e-12],
            "invalid",
        ),
        (0.0, 2e-7, [0.0, 1e-9], "invalid"),
    ],
)
def test_endpoint_distinguishes_uncertainty_from_a_demonstrated_violation(
    monkeypatch, position, velocity, margin, expected
):
    def endpoint_only(self, arcs):
        self.endpoint(
            np.array([position, 0, 0, velocity, 0, 0]),
            np.zeros(6),
            np.array(margin),
            3,
            "CONIC_DYNAMICS",
        )
        pytest.fail("Uncertain or invalid endpoints cannot be accepted")

    monkeypatch.setattr(Checker, "run", endpoint_only)
    result = evaluate((FIXTURES / "planetx-conic.txt").read_bytes())
    assert result["status"] == expected and result["score"] is None
    assert result["errors"][0]["code"] == "CONIC_DYNAMICS"


def test_endpoint_still_accepts_only_residuals_and_margins_within_both_limits(bodies):
    checker = Checker(bodies, 20)
    checker.endpoint(
        np.array([0.04, 0, 0, 2e-8, 0, 0]),
        np.zeros(6),
        np.array([0.02, 1e-8]),
        3,
        "CONIC_DYNAMICS",
    )


def test_split_conics_cannot_accumulate_small_state_resets():
    state = np.array([-200 * AU, 3 * AU, 0, 20, 0, 0.0])
    rows = []
    for i in range(4):
        rows.append([0, 0, i * 100000, *state, 0, 0, 0])
        state = kepler(state[:3], state[3:], 100000)
        state[0] += 0.06  # Each local error is under 100 m; the total is not.
        rows.append([0, 0, (i + 1) * 100000, *state, 0, 0, 0])
    check_reject(encode(rows), "CONIC_DYNAMICS")


def test_arrival_tolerance_is_not_spent_twice(bodies):
    arc = encounter(bodies)
    checker = Checker(bodies, 20)
    checker.propagated = arc.rows[0].state.copy()
    checker.propagated[0] += 0.18
    checker.alternate = checker.propagated.copy()
    for row in arc.rows:
        row.state[0] += 0.09
    # Submitted position is within 100 m of both endpoints, but physical arrival misses.
    with pytest.raises(Rejection, match="FLYBY_POSITION"):
        checker.flyby(arc)


def test_massless_encounter_does_not_supply_tolerance_impulse(bodies):
    arc = encounter(bodies, ident=1000)
    checker = Checker(bodies, 20)
    checker.propagated = arc.rows[0].state.copy()
    checker.alternate = checker.propagated.copy()
    before = checker.propagated.copy()
    arc.rows[1].state[3] += 5e-8
    arc.rows[1].control[0] += 5e-8
    checker.flyby(arc)
    assert np.array_equal(checker.propagated, before)


@pytest.mark.parametrize("seed", range(64))
def test_seeded_corruptions_never_receive_score(seed):
    """Mutate physics as well as boundaries; do not rely on malformed syntax."""
    rng = np.random.default_rng(seed)
    rows = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    component = int(rng.integers(3, 9))
    magnitude = rng.uniform(1, 100) * (1 if component < 6 else 1e-4)
    for row in rows[1:]:
        row[component] += magnitude * (-1 if seed % 2 else 1)
    check_reject(encode(rows), "CONIC_DYNAMICS")


def test_ephemerides_against_independent_kepler_evolution(bodies):
    assert len(bodies) == 310
    for ident in (1, 9, 10, 1000, 1001, 2001, 2042):
        body = bodies[ident]
        r, v = body.state(0)
        dt = min(body.period * 0.31, END)
        actual = np.r_[body.state(dt)[0], body.state(dt)[1]]
        expected = kepler(r, v, dt)
        assert np.linalg.norm(actual[:3] - expected[:3]) < 0.001
        assert np.linalg.norm(actual[3:] - expected[3:]) < 1e-8


@pytest.mark.parametrize("factor", [0.7, 1.0, math.sqrt(2), 2.5])
def test_conic_solver_conserves_invariants_and_reverses(factor):
    r = np.array([2 * AU, 0, 0.0])
    v = np.array([0, factor * math.sqrt(MU / (2 * AU)), 0])
    propagated = kepler(r, v, 1e6)
    reverse = kepler(propagated[:3], propagated[3:], -1e6)
    assert np.linalg.norm(reverse[:3] - r) < 1e-4
    assert np.linalg.norm(reverse[3:] - v) < 1e-10

    def energy(r, v):
        return np.dot(v, v) / 2 - MU / np.linalg.norm(r)

    assert abs(energy(propagated[:3], propagated[3:]) - energy(r, v)) < 1e-9
    assert np.linalg.norm(np.cross(*np.split(propagated, 2)) - np.cross(r, v)) < 1e-4


def elliptical_arc(peri, apo, fraction):
    """Analytic ellipse starting at apoapsis, independent eccentric anomaly reference."""
    a, e = (apo + peri) * AU / 2, (apo - peri) / (apo + peri)
    body = Body(9999, a, e, 0, 0, 0, math.pi, 0)
    dt = fraction * body.period
    before, after = np.r_[*body.state(0)], np.r_[*body.state(dt)]
    return Arc(
        "conic", [Row(1, 0, 0, 0, before, np.zeros(3)), Row(2, 0, 0, dt, after, np.zeros(3))]
    )


def test_perihelion_of_unflown_conic_is_not_rejected(bodies):
    checker = Checker(bodies, 20)
    checker.conic(elliptical_arc(0.009, 0.2, 0.01))
    assert checker.range.minimum > 0.19 * AU
    assert checker.range.first_perihelion is None


def test_hidden_interior_perihelion_rejected(bodies):
    arc = elliptical_arc(0.009, 0.2, 1)
    assert np.linalg.norm(arc.rows[-1].state[:3]) > 0.19 * AU
    with pytest.raises(Rejection, match="PERIHELION_FLOOR"):
        Checker(bodies, 20).conic(arc)


def test_one_deep_pass_allowed_second_rejected(bodies):
    one = Checker(bodies, 20)
    one.conic(elliptical_arc(0.03, 0.2, 1))
    assert one.range.deep_minima == 1 and one.range.first_perihelion is not None
    with pytest.raises(Rejection, match="REPEATED_DEEP_PASS"):
        Checker(bodies, 20).conic(elliptical_arc(0.03, 0.2, 2))


def test_repeated_perihelia_inside_one_continuously_deep_region_rejected(bodies):
    with pytest.raises(Rejection, match="REPEATED_DEEP_PASS"):
        Checker(bodies, 20).conic(elliptical_arc(0.02, 0.04, 2))


@pytest.fixture(scope="module")
def sail_rows():
    """Manufactured trajectory from independently written RHS and an implicit solver."""
    initial = np.array([-200 * AU, 3 * AU, 0, 20, 0, 0.0])
    normal = np.array([1.0, 0, 0])

    def independent_rhs(_, state):
        r, v = state[:3], state[3:]
        length = np.linalg.norm(r)
        pressure = 2 * 5.4026e-6 * 15000 / 500 / 1000
        force = (
            -139348062043.343 * r / length**3
            - pressure * (149597870.691 / length) ** 2 * (r[0] / length) ** 2 * normal
        )
        return np.r_[v, force]

    solution = solve_ivp(
        independent_rhs,
        (0, 200000),
        initial,
        method="Radau",
        rtol=3e-14,
        atol=[1e-7] * 3 + [1e-14] * 3,
        dense_output=True,
    )
    assert solution.success
    return [[0, 1, t, *solution.sol(t), *normal] for t in (0, 100000, 200000)]


def test_independently_generated_sail_arc_passes(sail_rows):
    result = evaluate(encode(sail_rows))
    assert result["status"] == "valid", result
    assert result["score"] == 0
    assert result["metrics"]["segments"] == 2


@pytest.mark.parametrize(
    "control,code",
    [([2, 0, 0], "SAIL_NORMAL"), ([-1, 0, 0], "SAIL_CONE"), ([0, 0, 0], "SAIL_NORMAL")],
)
def test_invalid_sail_control(sail_rows, control, code):
    rows = copy.deepcopy(sail_rows)
    rows[0][9:] = control
    check_reject(encode(rows), code)


def test_sail_timestep_and_duplicate_state(sail_rows):
    rows = copy.deepcopy(sail_rows)
    rows[1][2] = 59
    check_reject(encode(rows), "SAIL_TIMESTEP")
    rows = copy.deepcopy(sail_rows)
    rows.insert(1, rows[0].copy())
    assert evaluate(encode(rows))["status"] == "valid"
    rows[1][3] += 1
    check_reject(encode(rows), "CONTINUITY")


def test_sail_defects_use_published_local_tolerance_and_are_reported(sail_rows):
    rows = copy.deepcopy(sail_rows)
    for i, row in enumerate(rows):
        row[3] += i
    result = evaluate(encode(rows))
    assert result["status"] == "valid", result
    assert result["metrics"]["max_sail_position_defect_km"] > 0.9
    assert result["metrics"]["max_sail_truth_relative_error"] < 1e-4
    assert result["score"] == 0  # No fabricated encounter can be scored.


def test_sail_position_defect_beyond_local_tolerance_rejected(sail_rows):
    rows = copy.deepcopy(sail_rows)
    rows[1][3] += 1e6
    rows[2][3] += 2e6
    check_reject(encode(rows), "RK4_ACCURACY")


def test_wrong_sail_dynamics_rejected(sail_rows):
    rows = copy.deepcopy(sail_rows)
    rows[1][6] += 1e-3
    check_reject(encode(rows), "RK4_ACCURACY")


def test_sail_cone_is_checked_between_reported_rows(bodies):
    state = np.array([AU, 0, 0, 0, math.sqrt(MU / AU), 0.0])
    normal = np.array([-1.0, 0, 0])
    period = 2 * math.pi * math.sqrt(AU**3 / MU)
    a = Row(1, 0, 1, 0, state, normal)
    b = Row(2, 0, 1, period, state, normal)
    # Both reported endpoints have an inward normal. The propagated orbit
    # passes behind the star, where the same normal points outward.
    with pytest.raises(Rejection, match="SAIL_CONE_INTERIOR"):
        Checker(bodies, 20).integration(a, b, state, state, True)


def test_exact_rk4_output_still_needs_independent_truth_check(bodies):
    from gtoc_bench.gtoc13.model import acceleration

    state = np.array([AU, 0, 0, 0, math.sqrt(MU / AU), 0.0])
    normal, dt = np.array([-1.0, 0, 0]), 2e6

    def rhs(y):
        return np.r_[y[3:], acceleration(y[:3], normal)]

    k1 = rhs(state)
    k2 = rhs(state + dt * k1 / 2)
    k3 = rhs(state + dt * k2 / 2)
    k4 = rhs(state + dt * k3)
    endpoint = state + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6
    arc = Arc("sail", [Row(1, 0, 1, 0, state, normal), Row(2, 0, 1, dt, endpoint, normal)])
    with pytest.raises(Rejection, match="SAIL_DYNAMICS"):
        Checker(bodies, 20).sail(arc)


def test_numerical_timeout_is_not_a_score():
    data = (FIXTURES / "planetx-conic.txt").read_bytes()
    result = evaluate(data, timeout=0)
    assert result["status"] == "unverified" and result["score"] is None
    assert result["errors"][0]["code"] == "VALIDATION_TIMEOUT"


def encounter(bodies, ident=5, epoch=0, science=1, periapsis_radii=2, speed=10):
    body = bodies[ident]
    r, v = body.state(epoch)
    incoming = np.array([speed, 0, 0], dtype=float)
    if body.gm:
        sine = 1 / (1 + periapsis_radii * body.radius * speed**2 / body.gm)
        angle = 2 * math.asin(sine)
        outgoing = speed * np.array([math.cos(angle), math.sin(angle), 0])
    else:
        outgoing = incoming.copy()
    return Arc(
        "flyby",
        [
            Row(1, ident, science, epoch, np.r_[r, v + incoming], incoming),
            Row(2, ident, science, epoch, np.r_[r, v + outgoing], outgoing),
        ],
    )


def test_flyby_altitude_speed_and_massless_constraints(bodies):
    checker = Checker(bodies, 20)
    checker.flyby(encounter(bodies))
    for radius in (1.01, 200):
        with pytest.raises(Rejection, match="FLYBY_ALTITUDE"):
            Checker(bodies, 20).flyby(encounter(bodies, periapsis_radii=radius))
    arc = encounter(bodies)
    arc.rows[1].state[3:] *= 1.001
    arc.rows[1].control = arc.rows[1].state[3:] - bodies[5].state(0)[1]
    with pytest.raises(Rejection, match="FLYBY_SPEED"):
        Checker(bodies, 20).flyby(arc)
    arc = encounter(bodies, ident=1000)
    arc.rows[1].state[3] += 0.01
    arc.rows[1].control[0] += 0.01
    with pytest.raises(Rejection, match="MASSLESS_FLYBY"):
        Checker(bodies, 20).flyby(arc)


def test_flyby_position_and_navigation_spacing(bodies):
    checker = Checker(bodies, 20)
    checker.flyby(encounter(bodies))
    with pytest.raises(Rejection, match="FLYBY_SPACING"):
        checker.flyby(encounter(bodies, epoch=60))
    checker.flyby(encounter(bodies, epoch=bodies[5].period / 3 + 1))
    bad = encounter(bodies, ident=6)
    bad.rows[0].state[0] += 1
    with pytest.raises(Rejection, match="FLYBY_POSITION"):
        checker.flyby(bad)


def test_non_science_does_not_bypass_physical_checks(bodies):
    with pytest.raises(Rejection, match="FLYBY_ALTITUDE"):
        Checker(bodies, 20).flyby(encounter(bodies, science=0, periapsis_radii=200))


def test_science_limit_and_before_perihelion(bodies):
    checker = Checker(bodies, 20)
    for i in range(13):
        checker.flyby(encounter(bodies, epoch=i * bodies[5].period / 2))
    with pytest.raises(Rejection, match="SCIENCE_LIMIT"):
        checker.flyby(encounter(bodies, epoch=13 * bodies[5].period / 2))
    for ident in (1001, 2001):
        with pytest.raises(Rejection, match="SCIENCE_BEFORE_PERIHELION"):
            Checker(bodies, 20).flyby(encounter(bodies, ident=ident))
        allowed = Checker(bodies, 20)
        allowed.range.first_perihelion = 0
        allowed.flyby(encounter(bodies, ident=ident, epoch=100))
    Checker(bodies, 20).flyby(encounter(bodies, ident=1000))


def test_published_score_example_and_seasonal_history(bodies):
    def fb(science=True, rhat=(1, 0, 0)):
        return {"body": 10, "science": science, "rhat": np.array(rhat), "vinf": 10}

    first = 50 * velocity_factor(10)
    assert first * 1.13 == pytest.approx(37.480, abs=0.0005)
    assert score_flybys([fb()], bodies)["total"] == pytest.approx(first)
    assert score_flybys([fb(), fb()], bodies)["total"] == pytest.approx(first * (1 + 2 / 11))
    assert score_flybys([fb(False), fb()], bodies)["total"] == pytest.approx(first)
    assert score_flybys([fb(), fb(rhat=(-1, 0, 0))], bodies)["total"] == pytest.approx(2 * first)


def test_grand_tour_requires_distinct_bodies(bodies):
    ids = list(range(1, 11)) + [1000] + list(range(1001, 1014))
    flybys = [
        {"body": ident, "science": True, "rhat": np.array([1, 0, 0]), "vinf": 10} for ident in ids
    ]
    assert score_flybys(flybys, bodies)["grand_tour_bonus"] == 1.2
    assert score_flybys(flybys[:-1], bodies)["grand_tour_bonus"] == 1
    flybys[-1]["body"] = 1001
    assert score_flybys(flybys, bodies)["grand_tour_bonus"] == 1


def test_roots_find_interior_extrema_not_only_samples():
    p = Polynomial.fromroots([0.103, 0.507, 0.911])
    assert real_roots(p) == pytest.approx([0.103, 0.507, 0.911])


def test_huge_numbers_fail_closed_without_nan_score():
    rows = decode((FIXTURES / "planetx-conic.txt").read_bytes())
    rows[0][4] = 1e300
    check_reject(encode(rows))


def test_materials_and_configuration_are_selected():
    manifest = verify_materials("gtoc13")
    assert "data/gtoc13_comets.csv" in manifest["files"]
    assert len(manifest["code_files"]) >= 4
    assert load_config(ROOT / "configs/codex-gtoc13.yaml")["problem"] == "gtoc13"
    assert specification("gtoc13")["score_unit"] == "points"
    assert client_timeout("gtoc13") == 660
    assert client_timeout("gtoc12") == 180
    with pytest.raises(ValueError):
        specification("../gtoc13")


def test_cli_rejection_text_and_json(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("0 0 NaN 1 0 0 0 0 0 0 0 0\n")
    command = [sys.executable, "-m", "gtoc_bench.gtoc13", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "NONFINITE" in result.stdout and "line 1" in result.stdout
    result = subprocess.run(command + ["--json"], capture_output=True, text=True, check=False)
    assert result.returncode == 2 and json.loads(result.stdout)["score"] is None
