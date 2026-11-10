"""Independent trials, continuation accounting, pricing and historical insertion."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml

from gtoc_bench.comparison import build_comparison, digest, export_comparison, historical_table
from gtoc_bench.comparison_interactive import SCRIPT, SCRIPT_HASH
from gtoc_bench.pricing import PriceLedger, load_prices
from gtoc_bench.storage import RunStore, read_events, write_json


def catalog():
    return {
        "schema_version": 1,
        "as_of": "2026-11-02",
        "currency": "USD",
        "models": {
            "test-model": {
                "source": "https://example.com/prices",
                "input": 2,
                "cached_input": 0.2,
                "cache_write": 2.5,
                "cache_write_long": 4,
                "output": 10,
            }
        },
        "aliases": {},
    }


def fixture_run(
    base,
    rid,
    score,
    *,
    elapsed=100,
    wall=100,
    parent=None,
    prior_seconds=0,
    prior_tokens=0,
    tokens=100,
    stop="agent_finished",
    receipt=10,
    reasoning="max",
    agent="codex",
    version="0.164.0",
    model="test-model",
):
    path = base / rid
    store = RunStore(path)
    config = {
        "problem": "gtoc12",
        "agent": {"id": agent, "version": version},
        "model": {"id": model, "reasoning": reasoning},
        "provider": {"id": "test", "auth_method": "api_key"},
        "budget": {"wall_seconds": wall, "token_limit": None},
        "hardware": {"cpus": 2},
    }
    store.write(
        "manifest.json",
        {
            "run_id": rid,
            "config": config,
            "image_id": "sha256:image",
            "configuration_id": "configuration",
            "resolved_conditions": {"hardware": {"logical_cpus": 2}},
        },
    )
    store.event("run_started", elapsed_seconds=0)
    usage = {
        "input_tokens": tokens,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "quality": "reported",
        "raw": {},
    }
    store.event("model_request_finished", elapsed_seconds=5, request_id="request1", usage=usage)
    verdict = {
        "submission_id": rid + "-candidate",
        "status": "valid",
        "score": score,
        "received_elapsed_seconds": receipt,
        "known_tokens": tokens,
        "usage_pending": False,
        "unknown_usage_requests": 0,
    }
    store.event(
        "submission_evaluated",
        elapsed_seconds=receipt + 0.2,
        submission_id=verdict["submission_id"],
        verdict=verdict,
    )
    store.write(
        "summary.json",
        {
            "run_id": rid,
            "state": "interrupted" if stop == "infrastructure_failure" else "completed",
            "elapsed_seconds": elapsed,
            "wall_seconds": wall,
            "token_limit": None,
            "known_tokens": tokens,
            "unknown_usage_requests": 0,
            "best_score": score,
            "stop_reason": stop,
            **({"parent_run_id": parent.name} if parent else {}),
        },
    )
    if parent:
        store.write(
            "continuation.json",
            {
                "parent_run_id": parent.name,
                "prior_elapsed_seconds": prior_seconds,
                "prior_known_tokens": prior_tokens,
                "parent_integrity_sha256": digest(parent / "integrity.json"),
                "original_budget": {"wall_seconds": 100, "token_limit": None},
            },
        )
    store.write(
        "integrity.json",
        {"algorithm": "sha256", "files": {"manifest.json": digest(path / "manifest.json")}},
    )
    (path / "exports").mkdir()
    (path / "exports/audit.html").write_text("<!doctype html><title>Fixture audit</title>")
    return path


def fixture_recovered_run(base, rid="recovered", score=30):
    path = fixture_run(base, rid, score, stop="time_limit")
    summary = json.loads((path / "summary.json").read_text())
    (path / "summary.json").unlink()
    (path / "integrity.json").unlink()
    shutil.rmtree(path / "exports")
    write_json(path / "status.json", summary | {"state": "finalizing"})
    sid = rid + "-candidate"
    solution = path / "submissions" / sid / "solution.txt"
    solution.parent.mkdir(parents=True)
    solution.write_text("Frozen solution bytes")
    verdict = {
        "status": "valid",
        "score": score,
        "artifact_sha256": digest(solution),
        "received_elapsed_seconds": 10,
        "known_tokens": 100,
    }
    store = RunStore(path)
    store.event("validation_late_verdict", elapsed_seconds=80, submission_id=sid, verdict=verdict)
    store.event("model_cancel_requested", elapsed_seconds=100.01, reason="time_limit")
    store.event(
        "model_request_finished",
        elapsed_seconds=100.02,
        request_id="cancelled",
        usage={"input_tokens": 0, "output_tokens": 0, "quality": "unknown"},
    )
    store.event("agent_exit", elapsed_seconds=117, returncode=-9)
    stop = base / f"{rid}-stop.json"
    write_json(stop, {"run_id": rid, "processes_stopped": True})
    revalidation = base / f"{rid}-revalidation.json"
    write_json(
        revalidation,
        {"run_id": rid, "submission_id": sid, "image_id": "sha256:image", "verdict": verdict},
    )
    record = base / "comparison-recoveries" / f"{rid}.json"
    write_json(
        record,
        {
            "schema_version": 1,
            "kind": "comparison_recovery",
            "run_id": rid,
            "run_path": path.name,
            "processes_stopped": True,
            "note": "Budget exhausted; finalization failed; best independently revalidated.",
            "source_sha256": {
                name: digest(path / name)
                for name in ("manifest.json", "status.json", "events.jsonl")
            },
            "evidence": [
                {"role": role, "path": p.name, "sha256": digest(p)}
                for role, p in [("stop_confirmation", stop), ("best_revalidation", revalidation)]
            ],
        },
    )
    return path, record


@pytest.mark.parametrize("compressed", [False, True])
def test_recovered_budget_and_usage_preserve_failed_finalization_and_originals(
    tmp_path, compressed
):
    run, record = fixture_recovered_run(tmp_path)
    if compressed:
        from gtoc_bench.submission_artifacts import save_submission

        solution = next(run.glob("submissions/*/solution.txt"))
        save_submission(solution.parent, solution.read_bytes())
        solution.unlink()
    before = {str(p): digest(p) for p in tmp_path.rglob("*") if p.is_file()}
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    trial = report["evaluations"][0]
    assert trial["best_score"] == 30 and trial["time_horizon"] == 100
    assert trial["candidate_count"] == 1 and trial["protocol_finished"]
    assert trial["known_tokens"] == 100 and trial["unknown_usage_requests"] == 1
    assert trial["recovered_finalization"] and trial["price_partial"]
    segment = trial["segments"][0]
    assert segment["state"] == "recovered"
    assert segment["post_run_recovery"]["record_sha256"] == digest(record)
    group = report["panels"][0]["groups"][0]
    assert group["completed_evaluations"] == group["recovered_evaluations"] == 1
    assert not (run / "summary.json").exists() and not (run / "integrity.json").exists()
    assert json.loads((run / "status.json").read_text())["state"] == "finalizing"
    assert before == {str(p): digest(p) for p in tmp_path.rglob("*") if p.is_file()}


def test_missing_summary_is_reported_and_partial_provider_run_is_not_finished(tmp_path):
    run, record = fixture_recovered_run(tmp_path)
    record.unlink()
    fixture_run(tmp_path, "sol", 20, elapsed=70, stop="provider_unavailable")
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    assert len(report["evaluations"]) == 1
    trial = report["evaluations"][0]
    assert trial["evaluation_id"] == "sol" and trial["time_horizon"] == 70
    assert not trial["protocol_finished"] and not trial["recovered_finalization"]
    assert {"run_id": run.name, "path": str(run), "reason": "missing_final_summary"} in report[
        "excluded"
    ]


@pytest.mark.parametrize("change", ["source", "stopped", "score", "elapsed", "symlink"])
def test_recovery_rejects_changed_sources_unconfirmed_stop_or_invalid_revalidation(
    tmp_path, change
):
    run, record_path = fixture_recovered_run(tmp_path)
    record = json.loads(record_path.read_text())
    if change == "source":
        with (run / "events.jsonl").open("a") as stream:
            stream.write(" ")
    elif change == "stopped":
        record["processes_stopped"] = False
    elif change == "score":
        item = record["evidence"][1]
        evidence = tmp_path / item["path"]
        data = json.loads(evidence.read_text())
        data["verdict"]["score"] = 999
        write_json(evidence, data)
        item["sha256"] = digest(evidence)
    elif change == "elapsed":
        status = json.loads((run / "status.json").read_text()) | {"elapsed_seconds": 99}
        write_json(run / "status.json", status)
        record["source_sha256"]["status.json"] = digest(run / "status.json")
    else:
        link = tmp_path / "linked-run"
        link.symlink_to(run, target_is_directory=True)
        record["run_path"] = link.name
    write_json(record_path, record)
    with pytest.raises(ValueError, match="[Rr]ecovery|recover"):
        build_comparison(tmp_path, catalog(), minimum_seconds=1)


def test_three_segments_and_duplicate_are_one_evaluation_with_exact_offsets(tmp_path):
    base = tmp_path / "results"
    parent = fixture_run(base, "parent", 2, elapsed=20, tokens=100, stop="infrastructure_failure")
    child = fixture_run(
        base,
        "child",
        5,
        elapsed=30,
        wall=80,
        tokens=200,
        parent=parent,
        prior_seconds=20,
        prior_tokens=100,
        stop="infrastructure_failure",
    )
    fixture_run(
        base,
        "grandchild",
        9,
        elapsed=40,
        wall=50,
        tokens=50,
        parent=child,
        prior_seconds=50,
        prior_tokens=300,
    )
    shutil.copytree(parent, base / "copy-parent")
    # Files created by an agent are data only and are not walked/imported.
    (parent / "workspace").mkdir()
    (parent / "workspace/solver.py").write_text("raise RuntimeError('must never execute')")
    report = build_comparison(base, catalog(), minimum_seconds=1)
    assert len(report["evaluations"]) == len(report["duplicate_copies"]) == 1
    trial = report["evaluations"][0]
    assert len(trial["segments"]) == 3 and trial["best_score"] == 9
    assert trial["observed_seconds"] == 90 and trial["time_horizon"] == 100
    assert trial["known_tokens"] == 350 and trial["known_usd"] == pytest.approx(350 * 2e-6)
    assert [p["seconds"] for p in trial["points"]] == [10, 30, 60]
    assert [p["known_tokens"] for p in trial["points"]] == [100, 300, 350]
    assert [p["known_usd"] for p in trial["points"]] == pytest.approx([0.0002, 0.0006, 0.0007])
    assert report["panels"][0]["groups"][0]["maximum_scores"] == {
        "n": 1,
        "mean": 9,
        "stddev": None,
        "min": 9,
        "max": 9,
    }


def test_mean_sample_dispersion_and_common_horizon_avoid_survivor_bias(tmp_path):
    fixture_run(tmp_path, "one", 10, tokens=100, receipt=10)
    fixture_run(
        tmp_path, "two", 20, tokens=200, elapsed=40, stop="infrastructure_failure", receipt=30
    )
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    group = report["panels"][0]["groups"][0]
    assert group["maximum_scores"]["mean"] == 15
    assert group["maximum_scores"]["stddev"] == pytest.approx(50**0.5)
    assert group["completed_evaluations"] == 1
    assert group["curves"]["seconds"]["common_horizon"] == 40
    at10 = next(p for p in group["curves"]["seconds"]["rows"] if p["seconds"] == 10)
    assert at10["mean"] == 5 and at10["n"] == 2
    assert group["curves"]["known_tokens"]["common_horizon"] == 100
    assert group["curves"]["seconds"]["rows"][-1]["mean"] == 15


def test_latest_late_verdict_replaces_provisional_error_and_keeps_receipt_time(tmp_path):
    path = fixture_run(tmp_path, "late", 10)
    # A subsequent canonical correction is authoritative, not a second candidate.
    store = RunStore(path)
    store.event(
        "validation_late_verdict",
        elapsed_seconds=70,
        submission_id="late-candidate",
        verdict={
            "status": "valid",
            "score": 10,
            "received_elapsed_seconds": 8,
            "known_tokens": 100,
        },
    )
    result = build_comparison(tmp_path, catalog(), minimum_seconds=1)["evaluations"][0]
    assert result["candidate_count"] == 1 and result["points"][0]["seconds"] == 8


def test_missing_parent_forks_bad_offsets_conflicting_copies_and_cycles_fail(tmp_path):
    parent = fixture_run(tmp_path, "parent", 2, elapsed=20, stop="infrastructure_failure")
    child = fixture_run(
        tmp_path, "child", 5, wall=80, elapsed=70, parent=parent, prior_seconds=20, prior_tokens=100
    )
    isolated = tmp_path / "isolated"
    shutil.copytree(child, isolated / "child")
    with pytest.raises(ValueError, match="Missing parent"):
        build_comparison(isolated, catalog(), minimum_seconds=1)
    shutil.rmtree(isolated)
    other = fixture_run(
        tmp_path,
        "other-child",
        7,
        wall=80,
        elapsed=70,
        parent=parent,
        prior_seconds=20,
        prior_tokens=100,
    )
    with pytest.raises(ValueError, match="Branched"):
        build_comparison(tmp_path, catalog(), minimum_seconds=1)
    shutil.rmtree(other)
    continuation = json.loads((child / "continuation.json").read_text())
    continuation["prior_known_tokens"] = 99
    write_json(child / "continuation.json", continuation)
    with pytest.raises(ValueError, match="offsets disagree"):
        build_comparison(tmp_path, catalog(), minimum_seconds=1)
    continuation["prior_known_tokens"] = 100
    write_json(child / "continuation.json", continuation)
    copy_path = tmp_path / "copy-parent"
    shutil.copytree(parent, copy_path)
    summary = json.loads((copy_path / "summary.json").read_text())
    summary["best_score"] = 999
    write_json(copy_path / "summary.json", summary)
    with pytest.raises(ValueError, match="Conflicting copies"):
        build_comparison(tmp_path, catalog(), minimum_seconds=1)
    shutil.rmtree(copy_path)
    store = RunStore(parent)
    store.write("continuation.json", {"parent_run_id": "child"})
    with pytest.raises(ValueError, match="cycle"):
        build_comparison(tmp_path, catalog(), minimum_seconds=1)


def test_budgets_and_reasoning_are_not_averaged_together(tmp_path):
    fixture_run(tmp_path, "max", 10)
    fixture_run(tmp_path, "low", 20, reasoning="low")
    fixture_run(tmp_path, "other-budget", 30, wall=200, elapsed=200)
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    assert len(report["panels"]) == 2
    assert [len(p["groups"]) for p in report["panels"]] == [2, 1]
    assert all(g["maximum_scores"]["n"] == 1 for p in report["panels"] for g in p["groups"])


@pytest.mark.parametrize("other_agent", [False, True])
def test_clock_policies_are_compared_together_with_original_conditions(tmp_path, other_agent):
    fixture_run(tmp_path, "continuous", 10)
    paused = fixture_run(tmp_path, "paused", 20)
    manifest = json.loads((paused / "manifest.json").read_text())
    manifest["config"]["recovery"] = {"clock_policy": "provider_wait"}
    if other_agent:
        manifest["config"]["agent"] = {"id": "swe_agent", "version": "1.1.0"}
    write_json(paused / "manifest.json", manifest)
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    assert len(report["panels"]) == 1
    panel = report["panels"][0]
    assert panel["clock_policies"] == ["continuous_wall", "provider_wait"]
    assert len(panel["groups"]) == (2 if other_agent else 1)
    assert all(
        c["plotted_evaluations"] == c["total_evaluations"] == 2 for c in panel["coverage"].values()
    )
    assert {e["clock_policy"] for e in report["evaluations"]} == {
        "continuous_wall",
        "provider_wait",
    }


def test_explicit_ram_change_keeps_the_chain_and_all_series_in_one_comparison(tmp_path):
    parent = fixture_run(tmp_path, "parent", 1, elapsed=40, stop="infrastructure_failure")
    child = fixture_run(
        tmp_path, "child", 2, parent=parent, elapsed=60, prior_seconds=40, prior_tokens=100
    )
    data = json.loads((child / "continuation.json").read_text())
    data["resource_changes"] = {"memory_gb": {"before": 40, "after": 96, "explicit_override": True}}
    write_json(child / "continuation.json", data)
    fixture_run(tmp_path, "independent", 3)
    report = build_comparison(tmp_path, catalog(), minimum_seconds=0)
    evaluation = next(e for e in report["evaluations"] if e["evaluation_id"] == "parent")
    assert len(evaluation["segments"]) == 2 and not evaluation["homogeneous_resources"]
    assert len(report["panels"]) == 1
    panel = report["panels"][0]
    assert not panel["homogeneous_resources"]
    assert panel["groups"][0]["maximum_scores"]["n"] == 2


def test_harness_means_use_root_maxima_with_unequal_repetitions_and_versions(tmp_path):
    parent = fixture_run(tmp_path, "parent", 2, elapsed=40, stop="infrastructure_failure")
    fixture_run(
        tmp_path,
        "child",
        20,
        parent=parent,
        elapsed=60,
        wall=60,
        prior_seconds=40,
        prior_tokens=100,
    )
    shutil.copytree(parent, tmp_path / "duplicate-parent")
    fixture_run(tmp_path, "codex-repeat", 10)
    fixture_run(tmp_path, "codex-new-version", 80, version="0.165.0")
    for index, score in enumerate([30, 50, 70]):
        fixture_run(tmp_path, f"swe-{index}", score, agent="swe_agent", version="1.1.0")
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    comparison = report["panels"][0]["harness_comparisons"][0]
    assert comparison["harness_count"] == 2 and comparison["evaluation_count"] == 6
    assert comparison["model"] == "test-model" and comparison["reasoning"] == "max"
    rows = {(h["agent"], h["agent_version"]): h for h in comparison["harnesses"]}
    codex = rows["codex", "0.164.0"]
    assert codex["maximum_scores"]["n"] == 2
    assert codex["maximum_scores"]["mean"] == 15
    assert codex["maximum_scores"]["stddev"] == pytest.approx(50**0.5)
    assert set(codex["evaluation_ids"]) == {"parent", "codex-repeat"}
    assert rows["codex", "0.165.0"]["maximum_scores"]["mean"] == 80
    swe = rows["swe_agent", "1.1.0"]
    assert swe["maximum_scores"]["n"] == 3 and swe["maximum_scores"]["mean"] == 50
    assert swe["maximum_scores"]["stddev"] == 20


@pytest.mark.parametrize("difference", ["model", "reasoning", "budget", "version_only"])
def test_harness_section_requires_same_model_effort_budget_and_distinct_harnesses(
    tmp_path, difference
):
    fixture_run(tmp_path, "one", 10)
    options = {"agent": "swe_agent", "version": "1.1.0"}
    if difference == "model":
        options["model"] = "other-model"
    elif difference == "reasoning":
        options["reasoning"] = "low"
    elif difference == "budget":
        options["wall"] = 200
    else:
        options = {"agent": "codex", "version": "0.165.0"}
    fixture_run(tmp_path, "two", 20, **options)
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    assert all(not p["harness_comparisons"] for p in report["panels"])


def test_historical_positions_ignore_other_agent_rows_and_handle_ties(tmp_path):
    write_json(
        tmp_path / "gtoc12/official-final.json",
        {
            "source_url": "https://example.com/ranking",
            "entries": [
                {"team": "Human A", "official_rank": 1, "official_score": 20},
                {"team": "Human B", "official_rank": 2, "official_score": 10},
            ],
        },
    )
    groups = [
        {
            "label": "Agent " + str(score),
            "maximum_scores": {"mean": score, "max": score + 1, "n": 2, "stddev": 1},
        }
        for score in (25, 15, 10)
    ]
    table = historical_table("gtoc12", groups, tmp_path)
    assert [g["historical_rank_mean"] for g in groups] == [1, 2, 2]
    assert [g["historical_rank_best"] for g in groups] == [1, 2, 2]
    assert next(r for r in table["rows"] if r["name"] == "Human B")["historical_rank"] == 2


def test_pricing_cache_categories_long_context_and_no_reasoning_double_count():
    rates = catalog()
    rates["models"]["test-model"]["long_context"] = {
        "threshold": 100,
        "input_multiplier": 2,
        "output_multiplier": 1.5,
    }
    ledger = PriceLedger(rates)
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "output_tokens": 10,
        "reasoning_tokens": 7,
        "quality": "reported",
        "raw": {"input_tokens_details": {"cache_write_tokens": 20}},
    }
    assert ledger.charge("test-model", "one", usage) == pytest.approx(
        (40 * 2 + 40 * 0.2 + 20 * 2.5 + 10 * 10) / 1e6
    )
    usage["input_tokens"] = 101
    assert ledger.charge("test-model", "two", usage) == pytest.approx(
        ((41 * 2 + 40 * 0.2 + 20 * 2.5) * 2 + 10 * 10 * 1.5) / 1e6
    )
    assert ledger.charge("missing-model", "unknown", usage) is None
    assert ledger.charge("test-model", "unbilled", {"quality": "not_billed"}) == 0


def test_native_usage_repeated_updates_and_final_reconciliation_are_not_billed_twice():
    rates = catalog()
    rate = rates["models"]["test-model"]
    ledger = PriceLedger(rates)
    raw = {
        "input_tokens": 2,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 8,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 8},
        "output_tokens": 4,
    }
    usage = {
        "input_tokens": 20,
        "cached_input_tokens": 10,
        "output_tokens": 4,
        "raw": raw,
        "source": "official_claude_cli",
        "quality": "reported",
    }
    assert ledger.charge("test-model", "message", usage) == pytest.approx(
        (
            2 * rate["input"]
            + 10 * rate["cached_input"]
            + 8 * rate["cache_write_long"]
            + 4 * rate["output"]
        )
        / 1e6
    )
    assert (
        ledger.charge("test-model", "message", usage | {"input_tokens": 0, "output_tokens": 0}) == 0
    )
    later = usage | {"input_tokens": 0, "output_tokens": 6, "raw": raw | {"output_tokens": 10}}
    assert ledger.charge("test-model", "message", later) == pytest.approx(60e-6)
    final = {
        "input_tokens": 3,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 8,
        "output_tokens": 10,
    }
    assert ledger.charge(
        "test-model",
        "final",
        usage | {"input_tokens": 1, "output_tokens": 0, "raw": final},
        {"cumulative_reconciliation": True},
    ) == pytest.approx(2e-6)
    assert (
        ledger.charge(
            "test-model",
            "final",
            usage | {"input_tokens": 0, "output_tokens": 0, "raw": final},
            {"cumulative_reconciliation": True},
        )
        == 0
    )


@pytest.mark.parametrize("schema,distributed", [(None, False), (2, False), (2, True)])
def test_native_curves_require_response_usage_instead_of_a_final_session_jump(
    tmp_path, schema, distributed
):
    path = fixture_run(tmp_path, "native", 10, tokens=4010, agent="claude_code", version="2.1.308")
    original = list(read_events(path / "events.jsonl"))
    verdict = original[-1]["verdict"] | {"known_tokens": 4010 if distributed else 11}
    (path / "events.jsonl").unlink()
    store = RunStore(path)
    initial = {
        "input_tokens": 10,
        "output_tokens": 1,
        "cached_input_tokens": 0,
        "source": "official_claude_cli",
        "quality": "reported" if schema is None else "partial",
        "raw": {"input_tokens": 10, "output_tokens": 1},
    }
    if schema:
        initial.update(accounting_schema=schema, scope="response", temporal_quality="observed")
    store.event("model_request_finished", elapsed_seconds=5, request_id="m1", usage=initial)
    if distributed:
        final = initial | {
            "input_tokens": 0,
            "output_tokens": 3999,
            "raw": {"input_tokens": 10, "output_tokens": 4000},
        }
        store.event("model_request_finished", elapsed_seconds=8, request_id="m1", usage=final)
        store.event(
            "model_request_finished",
            elapsed_seconds=8.1,
            request_id="m1",
            usage=final | {"output_tokens": 0, "quality": "reported"},
        )
    store.event(
        "submission_evaluated",
        elapsed_seconds=10.2,
        submission_id=verdict["submission_id"],
        verdict=verdict,
    )
    correction = initial | {
        "input_tokens": 0,
        "output_tokens": 0 if distributed else 3999,
        "quality": "reported",
        "raw": {"input_tokens": 10, "output_tokens": 4000},
    }
    if schema:
        correction.update(
            scope="session_reconciliation",
            temporal_quality="observed" if distributed else "unattributed",
        )
    store.write(
        "requests/final/metadata.json",
        {"returned_model": "test-model", "cumulative_reconciliation": True},
    )
    store.event("model_request_finished", elapsed_seconds=99, request_id="final", usage=correction)
    store.integrity()
    trial = build_comparison(tmp_path, catalog(), minimum_seconds=1)["evaluations"][0]
    assert trial["best_score"] == 10 and trial["known_tokens"] == 4010
    assert trial["known_usd"] == pytest.approx((10 * 2 + 4000 * 10) / 1e6)
    assert trial["axis_status"]["seconds"]["available"]
    for axis in ("known_tokens", "known_usd"):
        assert trial["axis_status"][axis]["available"] is distributed
    if distributed:
        assert not trial["price_partial"] and trial["pending_native_usage_responses"] == 0
    else:
        assert trial["usage_timeline_issues"]


def test_native_output_updates_keep_the_original_long_context_price():
    rates = catalog()
    rates["models"]["test-model"]["long_context"] = {
        "threshold": 100,
        "input_multiplier": 2,
        "output_multiplier": 1.5,
    }
    ledger = PriceLedger(rates)
    raw = {"input_tokens": 110, "output_tokens": 1}
    first = {
        "input_tokens": 110,
        "output_tokens": 1,
        "source": "official_claude_cli",
        "quality": "partial",
        "raw": raw,
    }
    ledger.charge("test-model", "message", first)
    update = first | {"input_tokens": 0, "output_tokens": 9, "raw": raw | {"output_tokens": 10}}
    assert ledger.charge("test-model", "message", update) == pytest.approx(9 * 10 * 1.5 / 1e6)


def test_anthropic_api_usage_prices_both_cache_ttls_without_native_deduplication():
    ledger = PriceLedger(catalog())
    usage = {
        "input_tokens": 20,
        "cached_input_tokens": 10,
        "output_tokens": 4,
        "quality": "reported",
        "raw": {
            "input_tokens": 2,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 8,
            "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 5},
            "output_tokens": 4,
        },
    }
    expected = (2 * 2 + 10 * 0.2 + 3 * 2.5 + 5 * 4 + 4 * 10) / 1e6
    assert ledger.charge("test-model", "api-one", usage) == pytest.approx(expected)
    assert ledger.charge("test-model", "api-two", usage) == pytest.approx(expected)


def test_prices_are_offline_editable_finite_and_missing_rates_are_not_free(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump(catalog()))
    first = load_prices(path)
    changed = catalog()
    changed["models"]["test-model"]["output"] *= 2
    path.write_text(yaml.safe_dump(changed))
    assert first["sha256"] != load_prices(path)["sha256"]
    changed["models"]["test-model"]["input"] = float("nan")
    path.write_text(yaml.safe_dump(changed))
    with pytest.raises(ValueError, match="Invalid input"):
        load_prices(path)
    expired = catalog()
    expired["models"]["test-model"]["valid_through"] = "2026-11-01"
    path.write_text(yaml.safe_dump(expired))
    with pytest.raises(ValueError, match="validity"):
        load_prices(path)
    fixture_run(tmp_path / "results", "one", 10)
    empty = catalog() | {"models": {}}
    report = build_comparison(tmp_path / "results", empty, minimum_seconds=1)
    trial = report["evaluations"][0]
    assert trial["known_usd"] is None and trial["unpriced_observations"] == 1
    assert not report["panels"][0]["groups"][0]["curves"]["known_usd"]["available"]


def test_missing_price_has_explicit_coverage_without_hiding_other_axes(tmp_path):
    fixture_run(tmp_path, "known", 10)
    missing = fixture_run(tmp_path, "missing", 20)
    manifest = json.loads((missing / "manifest.json").read_text())
    manifest["config"]["model"]["id"] = "unpriced-model"
    write_json(missing / "manifest.json", manifest)
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    panel = report["panels"][0]
    for axis in ("maximum", "seconds", "known_tokens"):
        assert panel["coverage"][axis]["plotted_series"] == 2
    price = panel["coverage"]["known_usd"]
    assert price["total_series"] == 2 and price["plotted_series"] == 1
    assert len(price["missing_series"]) == 1
    assert "unpriced-model" in price["missing_series"][0]["reason"]
    assert "Falta tarifa" in price["missing_series"][0]["reason"]


def test_priced_repetitions_stay_visible_when_another_repetition_has_no_price(tmp_path):
    fixture_run(tmp_path, "priced", 10)
    missing = fixture_run(tmp_path, "unpriced", 20)
    write_json(missing / "requests/request1/metadata.json", {"returned_model": "unpriced-model"})
    report = build_comparison(tmp_path, catalog(), minimum_seconds=1)
    group = report["panels"][0]["groups"][0]
    price = group["curves"]["known_usd"]
    assert group["maximum_scores"]["mean"] == 15
    assert price["available"] and price["partial"] and price["n"] == 1 and price["n_total"] == 2
    assert price["included_evaluation_ids"] == ["priced"]
    assert price["excluded_evaluations"][0]["evaluation_id"] == "unpriced"
    assert price["rows"][-1]["mean"] == 10
    assert group["curves"]["known_tokens"]["n"] == 2


def test_model_display_order_is_fixed_data_and_not_score_inference(tmp_path):
    for rid, model, score in [("small", "small-model", 99), ("large", "large-model", 1)]:
        path = fixture_run(tmp_path, rid, score)
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["config"]["model"]["id"] = model
        write_json(path / "manifest.json", manifest)
    prices = catalog() | {"model_order": ["large-model", "small-model"]}
    report = build_comparison(tmp_path, prices, minimum_seconds=1)
    assert [g["model"] for g in report["panels"][0]["groups"]] == ["large-model", "small-model"]


def test_repricing_changes_cost_without_changing_scores_tokens_or_source_files(tmp_path):
    run = fixture_run(tmp_path, "one", 10)
    before = {p.name: digest(p) for p in run.iterdir() if p.is_file()}
    original = build_comparison(tmp_path, catalog(), minimum_seconds=1)["evaluations"][0]
    changed = catalog()
    changed["models"]["test-model"]["input"] *= 3
    repriced = build_comparison(tmp_path, changed, minimum_seconds=1)["evaluations"][0]
    assert repriced["known_usd"] == pytest.approx(original["known_usd"] * 3)
    assert repriced["best_score"] == original["best_score"]
    assert repriced["known_tokens"] == original["known_tokens"]
    assert before == {p.name: digest(p) for p in run.iterdir() if p.is_file()}


@pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None, reason="Requires the analysis extra"
)
def test_cli_report_exports_all_four_figures_and_preserves_canonical_inputs(tmp_path):
    base, output = tmp_path / "results", tmp_path / "comparison"
    fixture_run(base, "one", 10)
    fixture_run(base, "two", 20)
    fixture_recovered_run(base)
    fixture_run(base, "swe", 15, agent="swe_agent", version="1.1.0")
    missing = fixture_run(base, "unpriced", 40)
    manifest = json.loads((missing / "manifest.json").read_text())
    manifest["config"]["model"]["id"] = "unpriced-model"
    write_json(missing / "manifest.json", manifest)
    before = {str(p): digest(p) for p in base.rglob("*") if p.is_file()}
    prices = tmp_path / "prices.yaml"
    prices.write_text(yaml.safe_dump(catalog()))
    # The report must run without network or provider credentials, including
    # recovery, charts and explanations. It never executes workspace scripts.
    guard = tmp_path / "network-guard"
    guard.mkdir()
    marker = guard / "loaded.txt"
    (guard / "sitecustomize.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('network disabled')\n"
        "def deny_network(event, args):\n"
        "    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname'}:\n"
        "        raise RuntimeError('Network forbidden during comparison')\n"
        "sys.addaudithook(deny_network)\n"
    )
    script = base / "one/workspace/solver.py"
    script.parent.mkdir()
    script.write_text("raise RuntimeError('Agent code must not run')\n")
    before[str(script)] = digest(script)
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env["PYTHONPATH"] = str(guard) + os.pathsep + env.get("PYTHONPATH", "")
    worker = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtoc_bench",
            "compare",
            "--results",
            str(base),
            "--output",
            str(output),
            "--prices",
            str(prices),
            "--min-wall-seconds",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert worker.returncode == 0, worker.stdout + worker.stderr
    assert marker.read_text() == "network disabled"
    data = json.loads((output / "comparison.json").read_text())
    group = data["panels"][0]["groups"][0]
    assert group["maximum_scores"]["mean"] == 20
    assert group["recovered_evaluations"] == 1
    assert len(list((output / "figures").iterdir())) == 10
    assert len(list((output / "evaluations").iterdir())) == 5
    assert (output / "summary.csv").is_file() and (output / "curves.csv").is_file()
    html = (output / "index.html").read_text()
    assert html.count("<script>") == 1 and f"<script>{SCRIPT}</script>" in html
    assert f"script-src 'sha256-{SCRIPT_HASH}'" in html and "connect-src 'none'" in html
    assert html.count('class="interactive-chart"') == 5
    assert html.count("data-series-button=") == 14  # 3 per main chart, 2 harness rows.
    assert html.count('disabled title="Falta tarifa') == 1
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    references = re.findall(r'(?:xlink:)?href="#([^"]+)"', html)
    references += re.findall(r"url\(#([^)]*)\)", html)
    assert set(references) <= set(ids)
    for name in ("maximum", "time", "tokens", "price", "harnesses"):
        assert f'id="gtoc12-1-{name}-chart"' in html
        assert f"gtoc12-1-{name}-svg-series-0-" in html
    for layer in ("samples", "bar-0", "error-0", "score", "tick"):
        assert f'id="series-0-{layer}"' in (output / "figures/gtoc12-1-maximum.svg").read_text()
    for layer in ("mean", "trial-0", "trial-1", "band", "legend-handle", "legend-text"):
        assert f'id="series-0-{layer}"' in (output / "figures/gtoc12-1-time.svg").read_text()
    assert "Cierre recuperado:" in html and "Auditoría individual no disponible" in html
    assert "#gtoc12-1" in html and 'id="gtoc12-1"' in html
    assert "recovered_evaluations" in (output / "summary.csv").read_text()
    assert (output / "coverage.csv").is_file()
    assert "3/3" in html and "Disponibilidad de las gráficas" in html
    assert "2/3 series" in html and "Falta tarifa en la plantilla para: unpriced-model" in html
    assert "unpriced-model" in (output / "figures/gtoc12-1-price.svg").read_text()
    comparison = data["panels"][0]["harness_comparisons"][0]
    assert [h["maximum_scores"]["mean"] for h in comparison["harnesses"]] == [20, 15]
    assert (output / "harnesses.csv").is_file()
    assert 'id="gtoc12-1-harnesses"' in html
    assert "SWE-agent" in (output / "figures/gtoc12-1-harnesses.svg").read_text()
    assert before == {str(p): digest(p) for p in base.rglob("*") if p.is_file()}
    # Regenerating a former multi-panel directory removes stale generated
    # figures, keeps unrelated files, and reproduces the same derived data.
    previous = json.loads((output / "comparison.json").read_text())
    previous["panels"].append({"figures": {"time": "figures/gtoc12-2-time.png"}})
    previous["evaluations"].append({"evaluation_id": "obsolete"})
    stale = [
        output / "figures/gtoc12-2-time.png",
        output / "figures/gtoc12-2-time.svg",
        output / "evaluations/obsolete.json",
    ]
    for path in stale:
        path.write_text("Obsolete generated artifact")
    notes = output / "operator-notes.txt"
    notes.write_text("Keep this file")
    write_json(output / "comparison.json", previous)
    repeated = subprocess.run(worker.args, env=env, capture_output=True, text=True, timeout=60)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    regenerated = json.loads((output / "comparison.json").read_text())
    assert {k: v for k, v in regenerated.items() if k != "created_at_utc"} == {
        k: v for k, v in data.items() if k != "created_at_utc"
    }
    assert not any(path.exists() for path in stale) and notes.read_text() == "Keep this file"
    assert before == {str(p): digest(p) for p in base.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="outside original"):
        export_comparison(base, base / "one/exports/comparison", prices, minimum_seconds=1)
