"""GTOCX physics, graph constraints, published scoring and hostile-input tests."""

import copy
import json
import math
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.gtocx.format import Pod, Rejection, Vessel, parse
from gtoc_bench.gtocx.judge import Checker, evaluate, score_settlements
from gtoc_bench.gtocx.model import VELOCITY, acceleration, circular_speed, load_catalog
from gtoc_bench.problems import client_timeout
from gtoc_bench.validator import verify_materials

REFERENCES = ROOT / "references/gtocx/submissions"
SMALL = ROOT / "tests/fixtures/gtocx/small-tree.txt"


def fixture(name="TheCitadel"):
    return (REFERENCES / (name + "-gtocx-leaderboard-soln")).read_bytes()


def rows(data):
    return [
        [float(x) for x in re.split(r"[,\s]+", line.strip().removesuffix(",")) if x]
        for line in data.decode().splitlines()[1:]
    ]


def encode(values):
    return (
        "test header\n"
        + "\n".join(" ".join(format(v, ".17g") for v in row) for row in values)
        + "\n"
    ).encode()


def rejected(data, code=None):
    result = evaluate(data)
    assert result["status"] in {"invalid", "unverified"}, result
    assert result["score"] is None and result["diagnostics"], result
    if code:
        assert result["errors"][0]["code"] == code, result
    return result


@pytest.mark.parametrize(
    "name,score,count",
    [
        ("TheCitadel", 144.4068488991487, 2),
        ("TeamJena", 207.91466258955478, 5),
    ],
)
def test_public_small_trees_validate_and_exclude_bonus(name, score, count):
    result = evaluate(fixture(name))
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(score, abs=1e-9)
    assert result["score_breakdown"]["time_bonus"] == 1
    assert result["metrics"]["settled_stars"] == count
    assert result["score_unit"] == "points"


def test_branched_subset_of_official_winning_tree():
    result = evaluate(SMALL.read_bytes())
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(14.005617704632781, abs=1e-9)
    assert result["metrics"]["settler_ships"] == 1
    assert result["metrics"]["settled_stars"] == 15


@pytest.mark.parametrize(
    "column,value,code",
    [
        (0, -11.5, "INTEGER"),
        (0, -13, "SHIP_ID"),
        (1, 0, "STAR_ID"),
        (1, 100001, "STAR_ID"),
        (1, 2.5, "INTEGER"),
        (2, -1, "TIME_WINDOW"),
        (2, 11, "SOL_DEPARTURE"),
        (3, 91, "TIME_WINDOW"),
        (4, float("nan"), "NONFINITE"),
        (5, float("inf"), "NONFINITE"),
        (4, 2000, "SINGLE_IMPULSE"),
    ],
)
def test_invalid_numeric_and_initial_ship_data(column, value, code):
    values = rows(fixture())
    values[0][column] = value
    rejected(encode(values), code)


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "FILE_SIZE"),
        (b"header\n", "EMPTY_TREE"),
        (b"header\n\n", "BLANK_LINE"),
        (b"\xff\n", "ENCODING"),
        (b"header\n-11,,1 0 74 0 0 0 0 0 0\n", "COLUMNS"),
        (b"header\n-11 1 0 74 0 0 0 0 0 text\n", "NUMBER"),
        (b"header\n-11 1 0\n", "FAST_STRUCTURE"),
    ],
)
def test_format_rejections(data, code):
    rejected(data, code)


def test_final_newline_and_trailing_comma_compatibility():
    data = encode(rows(fixture()))
    without = data.rstrip(b"\n")
    assert evaluate(without)["status"] == "valid"
    data = data.replace(b"\n", b",\n", 1)  # Header is discarded, even with a comma.
    split = data.splitlines()
    data = b"\n".join([split[0], *[line + b"," for line in split[1:]]]) + b"\n"
    assert evaluate(data)["status"] == "valid"
    rejected(data + b"\n", "BLANK_LINE")


def test_repeat_fast_labels_count_as_distinct_vessels_but_no_extra_fleet():
    result = evaluate(fixture())
    assert result["metrics"]["fast_ships"] == 2
    assert result["format_notes"]
    values = rows(fixture())
    extra = values[0].copy()
    extra[1] = 1000
    rejected(encode(values + [extra]), "FAST_STRUCTURE")


def test_duplicate_settlement_and_total_delta_v():
    values = rows(fixture())
    values[1][1] = values[0][1]
    rejected(encode(values), "DUPLICATE_SETTLEMENT")
    values = rows(fixture())
    values[0][4:10] = [1000.0, 0.0, 0.0, 1000.0, 0.0, 0.0]
    rejected(encode(values), "TOTAL_DELTA_V")


def test_arrival_magnitude_not_direction_matches_published_host_rule():
    values = rows(fixture())
    values[0][7:] = [-v for v in values[0][7:]]
    result = evaluate(encode(values))
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(144.4068488991487)
    values[0][7:] = [3 * v for v in values[0][7:]]
    rejected(encode(values), "ARRIVAL_IMPULSE")


@pytest.mark.parametrize("seed", range(48))
def test_corrupted_flight_vectors_never_receive_a_score(seed):
    values = rows(fixture())
    rng = np.random.default_rng(seed)
    component = int(rng.integers(4, 7))
    values[0][component] += float(rng.uniform(0.05, 2)) * (-1 if seed % 2 else 1)
    rejected(encode(values))


def test_maneuver_and_mother_pod_spacing():
    values = rows(fixture())
    values[0][3] = values[0][2] + 0.5
    rejected(encode(values), "MANEUVER_SPACING")
    values = rows(fixture("TeamJena"))
    values[1][3] = values[0][6] + 0.5  # Last MS impulse plus half a Myr.
    rejected(encode(values), "MOTHER_POD_SPACING")


def test_no_mother_impulses_after_last_pod_and_no_unused_capacity():
    values = rows(fixture("TeamJena"))
    values[1][3] = values[0][6] - 0.1
    rejected(encode(values), "MOTHER_AFTER_LAST_POD")
    values = rows(fixture("TeamJena"))
    values[0][2] = 0
    rejected(encode(values), "POD_COUNT")


def test_orphan_branch_and_colony_wait():
    values = rows(SMALL.read_bytes())
    values[-1][0] = 99999
    rejected(encode(values), "UNSETTLED_SOURCE")
    values = rows(SMALL.read_bytes())
    tree = parse(SMALL.read_bytes())
    source = int(values[-1][0])
    values[-1][3] = tree.arrivals[source][0] + 1
    rejected(encode(values), "COLONY_WAIT")


def test_global_branch_limit_cannot_be_bypassed_by_row_permutation():
    values = rows(SMALL.read_bytes())
    for ident in (70001, 70002, 70003):
        extra = values[-1].copy()
        extra[1] = ident
        values.append(extra)
    rejected(encode(values), "BRANCH_LIMIT")


def test_permuted_canonical_groups_do_not_change_tree_physics():
    values = rows(SMALL.read_bytes())[:-1]
    tree = parse(SMALL.read_bytes())
    roots = {p.target for mother in tree.mothers for p in mother.pods} | {
        v.target for v in tree.fast
    }
    groups = {}
    for row in rows(fixture("NUDT-XSCC")):
        if row[0] in roots:
            groups.setdefault(row[0], []).append(row)
    a, b = [group[:2] for group in groups.values() if len(group) >= 2][:2]
    canonical = evaluate(encode(values + a + b))
    interleaved = evaluate(encode(values + [a[0], b[0], a[1], b[1]]))
    assert canonical["status"] == interleaved["status"] == "valid", (canonical, interleaved)
    assert canonical["score"] == pytest.approx(interleaved["score"], abs=1e-10)
    assert interleaved["metrics"]["settled_stars"] == 18
    assert any("nonconsecutive" in note for note in interleaved["format_notes"])


def test_ephemerides_agree_with_independent_numerical_motion():
    catalog = load_catalog()
    initial = catalog.state(0, 0)
    expected = catalog.state(0, 80)
    checker = Checker(catalog, time.monotonic() + 20)
    actual, other = checker.propagate(initial, initial, 0, 80, 1)
    assert np.linalg.norm(actual[:3] - expected[:3]) < 1e-8
    assert np.linalg.norm(actual[3:] - expected[3:]) / VELOCITY < 1e-6
    assert np.linalg.norm(other[:3] - expected[:3]) < 1e-8
    assert (
        np.linalg.norm(np.cross(initial[:3], initial[3:]) - np.cross(actual[:3], actual[3:])) < 1e-9
    )


def test_continuous_inner_range_violation_between_safe_endpoints():
    catalog = load_catalog()
    initial = np.array([2.5, 0, 0, 0, 0.8 * circular_speed(2.5) * VELOCITY, 0])

    def rhs(_, state):
        return np.r_[state[3:], acceleration(state[:3])]

    def apo(_, state):
        return np.dot(state[:3], state[3:])

    apo.direction = -1
    reference = solve_ivp(
        rhs,
        (0, 80),
        initial,
        method="RK45",
        rtol=1e-10,
        atol=1e-12,
        max_step=0.25,
        events=apo,
        dense_output=True,
    )
    period = next(t for t in reference.t_events[0] if t > 1)
    assert np.linalg.norm(reference.sol(period)[:3]) > 2.4
    with pytest.raises(Rejection, match="GALACTIC_RANGE"):
        Checker(catalog, time.monotonic() + 20).propagate(initial, initial, 0, period, 1)


def test_outer_galactic_range_violation():
    initial = np.array([32.0, 0, 0, 1.0, 0.0, 0.0])
    with pytest.raises(Rejection, match="GALACTIC_RANGE"):
        Checker(load_catalog(), time.monotonic() + 20).propagate(initial, initial, 0, 1, 1)


def test_zero_impulse_pod_at_mother_rendezvous_uses_preburn_state():
    actual = load_catalog()
    catalog = SimpleNamespace(radius=actual.radius, state=lambda _, t: actual.state(0, t))
    vessel = Vessel(
        "mother",
        -1,
        0,
        None,
        np.array([0.0, 1.0]),
        np.array([[0.0, 0, 0], [0.1, 0, 0]]),
        1,
        [Pod(1, 1.0, np.zeros(3), 2)],
    )
    checker = Checker(catalog, time.monotonic() + 20)
    checker.mother(vessel)
    assert checker.capacities == [500, 300]
    nonzero = copy.deepcopy(vessel)
    nonzero.pods[0].impulse[0] = 0.001
    with pytest.raises(Rejection, match="MOTHER_POD_SPACING"):
        Checker(catalog, time.monotonic() + 20).mother(nonzero)


def test_scoring_formula_against_hand_computed_single_settlement():
    catalog = SimpleNamespace(radius=np.array([8.34, 17.0]), final_angle=np.array([0.0, 0.0]))
    result = score_settlements([1], catalog, 100, 400)
    # At r=17: gr=1/30; at theta=0: ft/gt=32. All other kernel samples are zero.
    assert result["radial_error"] == pytest.approx(30 + 29**2)
    assert result["angular_error"] == pytest.approx(32 + 31**2)
    assert result["total"] == pytest.approx(4 / (1 + 0.0001 * (871 + 993)))
    assert result["time_bonus"] == 1


def test_scoring_uses_catalogue_final_angles_not_rounded_element_propagation():
    catalog = load_catalog()
    state = catalog.state(12765, 90)
    calculated = math.atan2(state[1], state[0])
    assert abs(calculated - catalog.final_angle[12765]) > 1e-6


def test_timeout_and_huge_values_fail_closed():
    result = evaluate(fixture(), timeout=0)
    assert result["status"] == "unverified" and result["score"] is None
    assert result["errors"][0]["code"] == "VALIDATION_TIMEOUT"
    values = rows(fixture())
    values[0][4] = 1e300
    rejected(encode(values))


def test_config_and_provenance_for_new_exercise():
    data = verify_materials("gtocx")
    assert "data/stars.txt" in data["files"] and len(data["code_files"]) == 5
    assert load_config(ROOT / "configs/codex-gtocx.yaml")["problem"] == "gtocx"
    assert client_timeout("gtocx") == 660


def test_cli_reports_cause_line_and_json(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("header\n-11 1 0 74 NaN 0 0 0 0 0\n")
    command = [sys.executable, "-m", "gtoc_bench.gtocx", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "NONFINITE" in result.stdout and "line 2" in result.stdout
    result = subprocess.run(command + ["--json"], capture_output=True, text=True, check=False)
    assert result.returncode == 2 and json.loads(result.stdout)["score"] is None
