"""Official reference provenance, calendar correction, and agent isolation."""

import json
import os
import runpy
import subprocess
import sys
from datetime import timedelta

import pytest

from gtoc_bench import ROOT
from gtoc_bench.network import allowed_document

BASE = ROOT / "references/leaderboards"
MODULE = runpy.run_path(str(BASE / "rebuild.py"))


def dataset(name):
    return json.loads((BASE / name).read_text())


def test_official_sources_and_artifacts_are_reproducible():
    outputs = MODULE["build"]()
    assert len(outputs) == 14
    for name, text in outputs.items():
        assert (BASE / name).read_text() == text


def test_gtoc12_scores_remain_official_and_are_not_mislabelled_as_raw_mass():
    data = dataset("gtoc12/official-final.json")
    assert len(data["entries"]) == 28
    assert data["entries"][0]["team"] == "Jet Propulsion Laboratory"
    assert data["entries"][0]["official_score"] == 22532.672
    assert data["benchmark_comparable"] is False
    assert all(e["benchmark_score_without_activity_coefficients"] is None for e in data["entries"])
    assert [e["official_score"] for e in data["entries"]] == sorted(
        (e["official_score"] for e in data["entries"]), reverse=True
    )


def test_gtoc13_has_both_complete_versions_with_identical_final_entries():
    official = dataset("gtoc13/official-final.json")
    approximate = dataset("gtoc13/without-time-bonus-approx.json")
    assert len(official["entries"]) == len(approximate["entries"]) == 52
    names = {e["team"] for e in official["entries"]}
    assert len(names) == 52 and names == {e["team"] for e in approximate["entries"]}
    assert official["kind"] == "official_final"
    assert approximate["kind"] == "derived_without_time_bonus_approximate"
    assert official["entries"][0]["official_score"] == 345.216
    assert official["entries"][-1]["official_score"] == 0
    source = {e["team"]: e for e in official["entries"]}
    for row in approximate["entries"]:
        assert row["official_rank"] == source[row["team"]]["official_rank"]
        assert row["official_score"] == source[row["team"]]["official_score"]
        assert row["best_submission_utc"] == source[row["team"]]["best_submission_utc"]
        assert row["time_bonus_estimate"] > 1
        lo, hi = row["score_c1_display_uncertainty_interval"]
        assert lo <= row["score_c1_estimate"] <= hi
        assert row["score_c1_estimate"] <= row["official_score"]


def test_corrected_ranking_is_reordered_by_the_new_score():
    rows = dataset("gtoc13/without-time-bonus-approx.json")["entries"]
    assert [r["score_c1_estimate"] for r in rows] == sorted(
        (r["score_c1_estimate"] for r in rows), reverse=True
    )
    assert [r["approximate_rank"] for r in rows] == list(range(1, 53))
    assert any(r["approximate_rank"] != r["official_rank"] for r in rows)
    by_name = {r["team"]: r for r in rows}
    assert by_name["VidTeam"]["approximate_rank"] < by_name["The Yellowhammers"]["approximate_rank"]


def test_bonus_uses_problem_release_not_submission_opening():
    D = MODULE["Decimal"]
    start = MODULE["epoch"]("2025-10-20T17:00:00Z")
    function = MODULE["time_bonus"]
    assert function(start, start) == D("1.13")
    assert function(start + timedelta(days=7), start) == D("1.13")
    assert function(start + timedelta(days=8), start) == D("1.125")
    assert function(start + timedelta(days=7, seconds=1), start) < D("1.13")
    with pytest.raises(ValueError, match="precedes"):
        function(start - timedelta(seconds=1), start)


def test_kaist_uses_best_submission_and_matches_independent_numeric_reference():
    rows = dataset("gtoc13/without-time-bonus-approx.json")["entries"]
    kaist = next(e for e in rows if e["team"] == "KAIST")
    assert kaist["best_submission_utc"] == "2025-10-28T15:32:00Z"
    assert kaist["last_submission_utc"] == "2025-11-14T06:41:00Z"
    assert kaist["score_c1_estimate"] == pytest.approx(111.20732893722135, abs=0.001)
    start = MODULE["epoch"]("2025-10-20T17:00:00Z")
    wrong = kaist["official_score"] / float(
        MODULE["time_bonus"](MODULE["epoch"](kaist["last_submission_utc"]), start)
    )
    assert abs(wrong - kaist["score_c1_estimate"]) > 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nov. 17, 2025, 5:34 p.m.", "2025-11-17T17:34:00Z"),
        ("Nov. 17, 2025, 12:28 p.m.", "2025-11-17T12:28:00Z"),
        ("Oct. 30, 2025, 12:35 a.m.", "2025-10-30T00:35:00Z"),
        ("Nov. 17, 2025, 9 a.m.", "2025-11-17T09:00:00Z"),
    ],
)
def test_english_dates_are_interpreted_in_utc(text, expected):
    assert MODULE["iso"](MODULE["published_epoch"](text)) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_nonfinite_or_negative_reference_scores_are_rejected(value):
    with pytest.raises(ValueError):
        MODULE["number"](value)


def test_reference_checks_are_available_without_an_agent_run():
    result = subprocess.run(
        [sys.executable, str(BASE / "rebuild.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Verified 14" in result.stdout


def test_leaderboards_stay_outside_agent_inputs_and_document_gateway():
    assert "references/" in (ROOT / ".dockerignore").read_text().splitlines()
    assert BASE.is_relative_to(ROOT / "references")
    for problem in ("gtoc12", "gtoc13"):
        assert not (ROOT / "problems" / problem / "leaderboards").exists()
    for url in dataset("sources.json")["sources"].values():
        assert not allowed_document(url["url"])


@pytest.mark.docker
@pytest.mark.skipif(
    not os.environ.get("GTOC_TEST_IMAGE"), reason="Set GTOC_TEST_IMAGE to enable Docker checks"
)
def test_docker_context_cannot_copy_host_leaderboards():
    result = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "--progress=plain", "-f", "-", "."],
        input="FROM scratch\nCOPY references/leaderboards /forbidden-leaderboards\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, "Host leaderboards unexpectedly entered the Docker context"
    assert "references/leaderboards" in output and "not found" in output, output
