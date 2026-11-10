"""Official GTOC11 envelope, scoring guards and native execution contract."""

import hashlib
import json
import math
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.gtoc11 import REVISION
from gtoc_bench.gtoc11.format import Rejection, Summary, parse
from gtoc_bench.gtoc11.judge import evaluate, parse_output
from gtoc_bench.problems import client_timeout, specification
from gtoc_bench.validator import verify_materials

SMALL = ROOT / "tests/fixtures/gtoc11/single-asteroid.txt"
EXAMPLE = ROOT / "references/gtoc11/received/Solution.txt"


def empty(mode=1):
    return (
        "\n".join([f"{i} 0 0" for i in range(1, 11)] + ["1.1 0 0 0", str(mode)]) + "\n"
    ).encode()


def mutate(data, row, column, value):
    lines = data.decode().splitlines()
    fields = lines[row - 1].split()
    fields[column] = str(value)
    lines[row - 1] = " ".join(fields)
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture(scope="module")
def example_summary():
    return parse(EXAMPLE.read_bytes())


def success(summary):
    mass, count, score = min(summary.station_mass_kg), summary.transferred_asteroids, summary.score
    return (
        f"Extra Info:{mass:.8g}\t{count}\nScore:{score:.8g}\nStatus:0\n**********\n",
        f"{mass:.6g}, {count}\n{score:.8g}\n0\n",
    )


def test_original_received_files_are_unchanged_and_all_materials_pinned():
    provenance = verify_materials("gtoc11")
    assert provenance["revision"] == REVISION
    assert provenance["time_bonus"] == 1
    assert len(provenance["code_files"]) == 3
    manifest = json.loads((ROOT / "references/gtoc11/manifest.json").read_text())
    assert manifest["received_files"] == 4
    for relative, entry in manifest["files"].items():
        data = (ROOT / relative).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert provenance["files"]["validator/GTOC11Check_Linux"]["sha256"] == (
        "6beb6456ee3959034ef3510ea646edf1633d37cb1fd61e41687dee3d94b4eabb"
    )
    assert (
        len((ROOT / "problems/gtoc11/data/Candidate Asteroids.txt").read_text().splitlines())
        == 83453
    )


def test_complete_example_merit_matches_official_standalone_score_without_bonus(example_summary):
    summary = example_summary
    assert summary.transferred_asteroids == summary.flybys == 148
    assert summary.rows == 203828
    assert summary.radius_au == pytest.approx(1.16410197870057)
    assert min(summary.station_mass_kg) == pytest.approx(549777130818197)
    assert summary.score == pytest.approx(1556.5911158875315, abs=1e-10)
    stdout, report = success(summary)
    result = parse_output(0, stdout, "", report, summary)
    assert result["status"] == "valid" and result["score"] == 1556.5911
    assert result["score_breakdown"]["time_bonus"] == 1


@pytest.mark.parametrize("mode", [0, 1])
def test_both_control_modes_and_empty_ship_slots(mode):
    summary = parse(empty(mode))
    assert summary.continuous_thrust_type == mode
    assert summary.delta_v_km_s == (0,) * 10
    assert summary.score == 0
    stdout, report = success(summary)
    result = parse_output(0, stdout, "", report, summary)
    assert result["status"] == "valid" and result["score"] == 0
    assert result["score_breakdown"]["delta_v_penalty"] == 10


def test_merit_hand_calculation_includes_ten_slots_and_excludes_departure_excess():
    summary = Summary(2, 1, (50,) + (0,) * 9, (1e14,) * 12, 12, 12, 100)
    assert summary.score == pytest.approx(1e4 / (4 * 13))
    small = SMALL.read_bytes()
    changed = mutate(small, 2, 7, 0)  # No physics acceptance is asserted by parsing alone.
    assert parse(changed).delta_v_km_s == parse(small).delta_v_km_s
    assert parse(small).delta_v_km_s[0] == pytest.approx(
        math.hypot(7.425939769105, 1.855599134594, -0.049196562806)
    )


def test_crlf_final_newline_and_whitespace():
    assert parse(SMALL.read_bytes().replace(b"\n", b"\r\n")).transferred_asteroids == 1
    assert parse(empty().rstrip()).score == 0
    assert parse(empty() + b"\n \t\n").score == 0


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "FILE_SIZE"),
        (b"\xff\n", "ENCODING"),
        (b"1 0\n", "COLUMNS"),
        (b"1 0 0\n", "TRUNCATED"),
        (b"1 0.0 0\n", "INTEGER"),
        (b"2 0 0\n", "SHIP_ORDER"),
        (b"1 -1 0\n", "RANGE"),
        (b"1 100000000000 0\n", "RANGE"),
        (b"1 " + b"0" * 4100 + b" 0\n", "LINE_LENGTH"),
        (empty().replace(b"1.1 0 0 0", b"NaN 0 0 0"), "NUMBER"),
        (empty().replace(b"1.1 0 0 0", b"1e999 0 0 0"), "NONFINITE"),
        (empty().replace(b"1.1 0 0 0", b"1e100 0 0 0"), "NUMERIC_RANGE"),
        (empty().replace(b"1.1 0 0 0", b"0.64 0 0 0"), "DYSON_RADIUS"),
        (empty(2), "RANGE"),
        (empty() + b"1 1 1\n", "UNVISITED_ASTEROID"),
        (empty() + b"garbage\n", "COLUMNS"),
    ],
)
def test_malformed_envelope_rejected_without_starting_native_process(data, code, monkeypatch):
    monkeypatch.setattr(
        "gtoc_bench.gtoc11.judge.subprocess.run",
        lambda *a, **kw: pytest.fail("Unsafe input reached C++"),
    )
    result = evaluate(data)
    assert result["status"] == "invalid" and result["score"] is None
    assert result["errors"][0]["code"] == code
    assert result["diagnostics"] and result["artifact_sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "row,column,value,code",
    [
        (2, 0, "inf", "NUMBER"),
        (2, 1, "1e309", "NONFINITE"),
        (2, 10, 0, "DEPARTURE"),
        (3, 0, 95000, "CHRONOLOGY"),
        (4, 10, 83454, "RANGE"),
        (4, 10, "81885.0", "INTEGER"),
        (4, 7, 1, "FLYBY_IMPULSE"),
        (8, 10, 81885, "DUPLICATE_FLYBY"),
        (43, 0, 1, "UNVISITED_ASTEROID"),
        (43, 1, 13, "RANGE"),
        (43, 2, 0, "RANGE"),
        (43, 2, 1261, "TRUNCATED"),
        (44, 7, "NaN", "NUMBER"),
        (44, 10, "Infinity", "NUMBER"),
        (45, 0, 100000, "CHRONOLOGY"),
    ],
)
def test_structural_mutations_cannot_pass_the_envelope(row, column, value, code):
    with pytest.raises(Rejection) as error:
        parse(mutate(SMALL.read_bytes(), row, column, value))
    assert error.value.code == code
    assert error.value.line is not None


def test_trailing_data_duplicate_transfer_and_wrong_counts_are_not_ignored():
    data = SMALL.read_bytes()
    rows = data.splitlines()
    for candidate, code in [
        (data + b"garbage\n", "COLUMNS"),
        (data + b"81885 3 1\n", "DUPLICATE_TRANSFER"),
        (b"\n".join(rows[:3] + rows[4:]) + b"\n", "COLUMNS"),
        (mutate(data, 1, 1, 1), "COUNTS"),
    ]:
        with pytest.raises(Rejection) as error:
            parse(candidate)
        assert error.value.code == code


@pytest.mark.parametrize(
    "variant",
    [
        "no_output",
        "no_status",
        "double_status",
        "double_score",
        "wrong_score",
        "bonus_score",
        "missing_report",
        "wrong_report_score",
        "wrong_report_status",
        "wrong_mass",
        "wrong_count",
        "stderr",
        "nonzero_exit",
        "signal",
        "nan_score",
        "negative_score",
        "unknown_status",
    ],
)
def test_ambiguous_or_inconsistent_native_results_never_award_points(variant, example_summary):
    stdout, report = success(example_summary)
    stderr, code = "", 0
    if variant == "no_output":
        stdout = ""
    elif variant == "no_status":
        stdout = stdout.replace("Status:0", "")
    elif variant == "double_status":
        stdout += "Status:0\n"
    elif variant == "double_score":
        stdout += "Score:1556.5911\n"
    elif variant == "wrong_score":
        stdout = stdout.replace("1556.5911", "1556.5912")
    elif variant == "bonus_score":
        stdout = stdout.replace("1556.5911", "1712.2502")
        report = report.replace("1556.5911", "1712.2502")
    elif variant == "missing_report":
        report = ""
    elif variant == "wrong_report_score":
        report = report.replace("1556.5911", "1556.59")
    elif variant == "wrong_report_status":
        report = report.removesuffix("0\n") + "-1\n"
    elif variant == "wrong_mass":
        stdout = stdout.replace("5.4977713e+14", "5.5977713e+14")
    elif variant == "wrong_count":
        stdout = stdout.replace("\t148", "\t149")
    elif variant == "stderr":
        stderr = "runtime error"
    elif variant == "nonzero_exit":
        code = 1
    elif variant == "signal":
        code = -11
    elif variant == "nan_score":
        stdout = stdout.replace("1556.5911", "nan")
    elif variant == "negative_score":
        stdout = stdout.replace("1556.5911", "-1")
    elif variant == "unknown_status":
        stdout = stdout.replace("Status:0", "Status:1")
    result = parse_output(code, stdout, stderr, report, example_summary)
    assert result["status"] == "validator_error" and result["score"] is None
    assert result["diagnostics"]


def test_native_rejection_preserves_original_line_message_and_never_accepts_score_zero():
    diagnostic = "Line 2: Initial DV is too large! (Violates 6 km/s constraint)"
    result = parse_output(
        255,
        f"{diagnostic}\nScore:0\nStatus:-1\n{diagnostic}\n",
        "",
        f"{diagnostic}\n0\n-1\n",
        parse(empty()),
    )
    assert result["status"] == "invalid" and result["score"] is None
    assert result["diagnostics"] == [diagnostic]


def native_platform(monkeypatch):
    monkeypatch.setattr("gtoc_bench.gtoc11.judge.platform.system", lambda: "Linux")
    monkeypatch.setattr("gtoc_bench.gtoc11.judge.platform.machine", lambda: "x86_64")


def test_native_contract_uses_fresh_directories_exact_names_and_closed_stdin(tmp_path, monkeypatch):
    native_platform(monkeypatch)
    paths = []
    data = empty()

    def native(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        paths.append(cwd)
        assert command == [str(ROOT / "problems/gtoc11/validator/GTOC11Check_Linux")]
        assert (cwd / "Solution.txt").read_bytes() == data
        assert (cwd / "Candidate Asteroids.txt").is_symlink()
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 600 and kwargs["env"] == {
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C",
        }
        assert not (cwd / "Result.txt").exists()
        stdout, report = success(parse(data))
        kwargs["stdout"].write(stdout.encode())
        (cwd / "Result.txt").write_text(report)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("gtoc_bench.gtoc11.judge.subprocess.run", native)
    for _ in range(2):
        assert evaluate(data, tmp_path)["status"] == "valid"
    assert paths[0] != paths[1] and not list(tmp_path.iterdir())


@pytest.mark.parametrize("error", ["timeout", "oserror"])
def test_native_failures_are_distinct_from_invalid_trajectories(error, tmp_path, monkeypatch):
    native_platform(monkeypatch)

    def native(*a, **kw):
        if error == "timeout":
            raise subprocess.TimeoutExpired("official", kw["timeout"])
        raise OSError(8, "Exec format error")

    monkeypatch.setattr("gtoc_bench.gtoc11.judge.subprocess.run", native)
    result = evaluate(empty(), tmp_path)
    assert result["status"] == "validator_error" and result["score"] is None
    if error == "timeout":
        assert result["timeout_phase"] == "native_validator" and result["timeout_seconds"] == 600
    assert not list(tmp_path.iterdir())


def test_native_platform_requirement_is_actionable(monkeypatch):
    monkeypatch.setattr("gtoc_bench.gtoc11.judge.platform.system", lambda: "Darwin")
    result = evaluate(empty())
    assert result["status"] == "validator_error"
    assert "Linux amd64" in result["diagnostics"][0] and "--image" in result["diagnostics"][0]


def test_exercise_configuration_and_calendar_references():
    assert load_config(ROOT / "configs/codex-gtoc11.yaml")["problem"] == "gtoc11"
    assert specification("gtoc11")["score_unit"] == "points"
    assert client_timeout("gtoc11") == 660
    base = ROOT / "references/leaderboards/gtoc11"
    official = json.loads((base / "official-final.json").read_text())
    corrected = json.loads((base / "without-time-bonus-approx.json").read_text())
    assert len(official["entries"]) == len(corrected["entries"]) == 25
    assert official["entries"][0]["official_score"] == 8443.630
    assert {r["team"] for r in official["entries"]} == {r["team"] for r in corrected["entries"]}
    alice = next(r for r in corrected["entries"] if r["team"] == "ALICE")
    assert alice["time_bonus_estimate"] == pytest.approx(1.058109626438, abs=1e-11)
    assert alice["score_b1_estimate"] < alice["official_score"]
    iowa = next(r for r in corrected["entries"] if r["team"] == "Iowa State University")
    assert iowa["displayed_after_competition_close"] and iowa["time_bonus_estimate"] == 1
    module = runpy.run_path(str(base.parent / "rebuild.py"))
    start, end = module["epoch"]("2021-10-17T12:00:00Z"), module["epoch"]("2021-11-07T12:00:00Z")
    assert module["gtoc11_bonus"](start, start, end) == 2
    assert module["gtoc11_bonus"](end, start, end) == 1
    assert not (ROOT / "problems/gtoc11/Solution.txt").exists()
    assert "references/" in (ROOT / ".dockerignore").read_text().splitlines()


def test_recorded_native_audit_matches_the_current_checker_and_adapter():
    directory = ROOT / "src/gtoc_bench/gtoc11"
    adapter = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(directory.glob("*.py")))
    ).hexdigest()
    report = json.loads((ROOT / "references/gtoc11/reference-checks.json").read_text())
    assert report["adapter_revision"] == adapter
    assert report["valid_cases"] == 4 and report["invalid_cases"] == 16
    assert len(report["cases"]) == 20
    for record in report["cases"]:
        verdict = record["verdict"]
        assert verdict["status"] == record["expected_status"]
        assert verdict["adapter_revision"] == adapter
        assert verdict["validator_revision"] == report["validator_revision"]
        if verdict["status"] == "invalid":
            assert verdict["score"] is None and verdict["diagnostics"]
        else:
            assert verdict["score_breakdown"]["time_bonus"] == 1
    first = report["cases"][0]["verdict"]
    assert first["artifact_sha256"] == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    early = next(r for r in report["cases"] if r["case"] == "activation_before_30_days")
    assert "30 days constraint" in str(early["verdict"]["diagnostics"])


def test_negative_wait_fixture_kepler_state_matches_the_original_asteroid_ephemeris():
    module = runpy.run_path(str(ROOT / "references/gtoc11/check_reference.py"))
    fields = [float(v) for v in SMALL.read_text().splitlines()[43].split()]
    reference = module["asteroid_state"](81885, fields[0])
    assert math.dist(reference[:3], fields[1:4]) < 0.01
    assert math.dist(reference[3:], fields[4:7]) < 1e-8
