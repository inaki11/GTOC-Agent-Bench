"""Independent-file audit and normalized leaderboard provenance, without re-running 12 long checks."""

import hashlib
import json
import runpy

import pytest

from gtoc_bench import ROOT
from gtoc_bench.gtocx.judge import revision_hash

BASE = ROOT / "references"


def read(name):
    return json.loads((BASE / name).read_text())


def test_all_twelve_official_submissions_pass_the_current_physical_judge():
    checks = read("gtocx/reference-checks.json")
    manifest = read("gtocx/manifest.json")
    assert len(checks) == len(manifest["files"]) == 12
    for name, report in checks.items():
        path = BASE / "gtocx/submissions" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == report["artifact_sha256"]
        assert report["validator_revision"] == revision_hash(), (
            "Rerun public-file audit after judge changes"
        )
        assert report["status"] == "valid" and report["score_breakdown"]["time_bonus"] == 1
        assert report["score"] == report["score_breakdown"]["total"]
    winner = checks["NUDT-XSCC-gtocx-leaderboard-soln"]
    assert winner["score"] == pytest.approx(3101.14604, abs=0.00001)
    assert winner["metrics"]["settled_stars"] == 3798


def test_both_leaderboard_versions_have_all_42_teams():
    official = read("leaderboards/gtocx/official-final.json")
    normalized = read("leaderboards/gtocx/without-time-bonus.json")
    assert len(official["entries"]) == len(normalized["entries"]) == 42
    assert {r["team"] for r in official["entries"]} == {r["team"] for r in normalized["entries"]}
    assert normalized["recomputed_entries"] == 12 and normalized["estimated_entries"] == 30
    scores = [r["score_without_time_bonus"] for r in normalized["entries"]]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank_without_time_bonus"] for r in normalized["entries"]] == list(range(1, 43))


def test_early_bonus_is_removed_and_public_files_take_priority_over_dates():
    rows = {r["team"]: r for r in read("leaderboards/gtocx/without-time-bonus.json")["entries"]}
    jena, citadel = rows["Team Jena"], rows["The Citadel"]
    assert jena["official_score"] == 368.847689
    assert jena["score_without_time_bonus"] == pytest.approx(207.91466258955478)
    assert citadel["official_score"] == 173.746592
    assert citadel["score_without_time_bonus"] == pytest.approx(144.4068488991487)
    for row in (jena, citadel):
        assert row["normalization_method"].startswith("recomputed")
        assert (
            abs(
                row["time_bonus_from_date_estimate"]
                - row["time_bonus_inferred_from_official_score"]
            )
            < 0.001
        )
    assert sum(r["normalization_method"].startswith("recomputed") for r in rows.values()) == 12
    assert any(r["official_rank"] != r["rank_without_time_bonus"] for r in rows.values())


def test_bonus_uses_submission_opening_and_fourth_power():
    module = runpy.run_path(str(BASE / "leaderboards/rebuild.py"))
    epoch, function = module["epoch"], module["gtocx_bonus"]
    start, end = epoch("2019-05-21T20:00:00Z"), epoch("2019-06-12T20:00:00Z")
    assert function(start, start, end) == 2
    assert function(end, start, end) == 1
    assert function(start + (end - start) / 2, start, end) == 1.0625
    with pytest.raises(ValueError):
        function(epoch("2019-05-15T20:00:00Z"), start, end)
