"""Fault injection for closing a run. No Docker daemon or paid model requests."""

import hashlib
import io
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.lifecycle import controller_alive, predecessor_state, read_status
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events, write_json


class Transport:
    def __init__(self, waits=()):
        self.waits = iter(waits)
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        outcome = next(self.waits, 0)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(["docker", "run"], timeout)
        self.returncode = outcome
        return outcome

    def terminate(self):
        self.signals.append("terminate")
        self.returncode = -15

    def kill(self):
        self.signals.append("kill")
        self.returncode = -9


@pytest.fixture
def run(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    runner.budget.tokens = 1234
    runner.budget.stop("time_limit")
    runner.best = 42.0
    runner.store.write("submissions/test/solution.txt", "preserved candidate")
    runner.send = lambda *_args, **_kwargs: None
    yield runner
    runner.pool.shutdown()


def seal_matches(run):
    files = json.loads((run.path / "integrity.json").read_text())["files"]
    assert "finalization.json" in files and "summary.json" in files
    for name, expected in files.items():
        assert hashlib.sha256((run.path / name).read_bytes()).hexdigest() == expected, name


def test_sdk_runtime_interruption_is_not_a_completed_trial(run):
    run.budget.reason = "agent_runtime_interrupted"
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "interrupted"
    assert summary["stop_reason"] == "agent_runtime_interrupted"
    assert summary["best_score"] == 42
    seal_matches(run)


@pytest.mark.parametrize(
    "agent,code,reason",
    [
        ("antigravity", 75, "agent_runtime_interrupted"),
        ("codex", 75, "agent_error"),
        ("antigravity", 0, "agent_finished"),
    ],
)
def test_sdk_exit_code_is_mapped_only_for_the_native_adapter(run, agent, code, reason):
    run.config["agent"]["id"] = agent
    run.budget = Budget(60)
    run.budget.start()
    run.process = SimpleNamespace(
        stdout=io.BytesIO(json.dumps({"type": "agent_exit", "returncode": code}).encode() + b"\n")
    )
    run.read()
    assert run.budget.reason == reason


def engine(monkeypatch, *, kill_timeout=False, remove_timeout=False, running=False):
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        action = command[1]
        assert kwargs["timeout"] <= 15 and kwargs["check"]
        if action == "kill" and kill_timeout or action == "rm" and remove_timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if action == "inspect":
            stdout = json.dumps([{"State": {"Running": running, "OOMKilled": False}}])
        elif action == "ps":
            stdout = calls[0][0][-1] + "\n" if running else ""
        else:
            stdout = ""
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr("gtoc_bench.finalization.subprocess.run", execute)
    return calls


def test_docker_kill_timeout_does_not_lose_the_result_or_seal(run, monkeypatch):
    run.process = Transport(["timeout", "timeout"])
    calls = engine(monkeypatch, kill_timeout=True)
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "completed" and summary["stop_reason"] == "time_limit"
    assert summary["best_score"] == 42 and summary["known_tokens"] == 1234
    assert summary["finalization"]["status"] == "completed_with_errors"
    assert summary["finalization"]["phase"] == "finished"
    report = json.loads((run.path / "finalization.json").read_text())
    error = next(e for e in report["errors"] if e["stage"] == "docker_kill")
    assert error["timeout_seconds"] == 15 and error["command"] == ["docker", "kill", run.name]
    assert report["container_stopped"] and report["safe_for_successor"]
    assert [c[0][1] for c in calls] == ["kill", "inspect", "rm"]
    assert (run.path / "submissions/test/solution.txt").exists()
    seal_matches(run)
    assert predecessor_state(run.path)["ready"]


def test_unknown_or_running_container_blocks_sealing_and_successors(run, monkeypatch):
    run.process = Transport([0])
    engine(monkeypatch, remove_timeout=True, running=True)
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "interrupted" and summary["stop_reason"] == "time_limit"
    assert summary["best_score"] == 42 and summary["known_tokens"] == 1234
    assert summary["finalization"]["status"] == "incomplete"
    assert summary["finalization"]["safe_for_successor"] is False
    assert not (run.path / "integrity.json").exists()
    assert predecessor_state(run.path) == {
        "ready": False,
        "blocked": True,
        "reason": "predecessor_cleanup_incomplete",
    }


def test_docker_unavailable_is_not_inferred_to_mean_container_stopped(run, monkeypatch):
    run.process = Transport(["timeout", "timeout", "timeout", 0])

    def unavailable(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("gtoc_bench.finalization.subprocess.run", unavailable)
    run.finalize()
    report = json.loads((run.path / "finalization.json").read_text())
    assert report["container_stopped"] is None and report["safe_for_successor"] is False
    assert run.process.signals == ["terminate", "kill"]
    assert (run.path / "summary.json").exists() and not (run.path / "integrity.json").exists()


def test_stopped_container_with_remove_timeout_can_still_seal_the_result(run, monkeypatch):
    run.process = Transport([0])
    engine(monkeypatch, remove_timeout=True)
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "completed"
    assert summary["finalization"]["container_stopped"] is True
    assert summary["finalization"]["status"] == "completed_with_errors"
    seal_matches(run)


def test_export_failure_still_preserves_and_seals_raw_results(run, monkeypatch):
    def failed_export(*_):
        raise ValueError("Synthetic export failure")

    monkeypatch.setattr("gtoc_bench.analysis.export_audit", failed_export)
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "completed"
    assert summary["finalization"]["status"] == "completed_with_errors"
    assert any(
        e["type"] == "finalization_error" and e["stage"] == "export_audit"
        for e in read_events(run.path / "events.jsonl")
    )
    seal_matches(run)


def test_initial_status_io_failure_does_not_prevent_cancellation_or_final_result(run, monkeypatch):
    order = []
    run.gateway = SimpleNamespace(
        inflight=0, cancel=lambda: order.append("cancel"), close=lambda: None
    )
    original = run.store.write
    failed = False

    def write(name, value):
        nonlocal failed
        if name == "status.json" and not failed:
            failed = True
            order.append("status_failure")
            raise OSError("Synthetic status I/O failure")
        return original(name, value)

    monkeypatch.setattr(run.store, "write", write)
    run.finalize()
    assert order[:2] == ["cancel", "status_failure"]
    assert json.loads((run.path / "summary.json").read_text())["state"] == "completed"
    seal_matches(run)


def test_native_close_failure_does_not_skip_container_cleanup_or_summary(run, monkeypatch):
    def native_close():
        raise subprocess.TimeoutExpired(["native-guard"], 10)

    run.native = SimpleNamespace(close=native_close, usage=SimpleNamespace(status=lambda: {}))
    run.process = Transport([0])
    calls = engine(monkeypatch)
    run.finalize()
    assert "rm" in [c[0][1] for c in calls]
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["finalization"]["safe_for_successor"] is False
    assert not (run.path / "integrity.json").exists()


def test_lingering_native_reader_prevents_sealing_even_if_close_returned(run):
    run.native = SimpleNamespace(
        close=lambda: None,
        thread=SimpleNamespace(is_alive=lambda: True),
        usage=SimpleNamespace(status=lambda: {}),
    )
    run.finalize()
    report = json.loads((run.path / "finalization.json").read_text())
    assert report["native_stopped"] is False and report["safe_for_successor"] is False
    assert not (run.path / "integrity.json").exists()


def test_stopped_native_client_has_no_local_requests_left_to_block_successor(run):
    run.native = SimpleNamespace(close=lambda: None, usage=SimpleNamespace(status=lambda: {}))
    assert run.status()["requests_in_flight"] is None
    run.finalize()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["requests_in_flight"] == 0
    assert predecessor_state(run.path)["ready"]
    seal_matches(run)


def test_failed_seal_after_rename_is_removed_and_not_published_as_success(run, monkeypatch):
    original = run.store.integrity

    def fsync_failed():
        original()
        raise OSError("Synthetic directory fsync failure")

    monkeypatch.setattr(run.store, "integrity", fsync_failed)
    run.finalize()
    assert not (run.path / "integrity.json").exists()
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["state"] == "interrupted" and summary["finalization"]["status"] == "incomplete"
    assert predecessor_state(run.path)["blocked"]


def test_diagnostics_io_failure_keeps_result_but_does_not_claim_a_safe_seal(
    run, monkeypatch, capsys
):
    original = run.store.write
    run.store.redact = lambda text: text.replace("SYNTHETIC_SECRET", "[REDACTED]")

    def write(name, value):
        if name == "finalization.json":
            raise OSError("Disk unavailable SYNTHETIC_SECRET")
        return original(name, value)

    monkeypatch.setattr(run.store, "write", write)
    run.finalize()
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().err
    summary = json.loads((run.path / "summary.json").read_text())
    assert summary["best_score"] == 42 and summary["state"] == "interrupted"
    assert summary["finalization"]["safe_for_successor"] is False
    assert not (run.path / "integrity.json").exists()


def test_docker_error_output_is_recorded_and_redacted(run, monkeypatch):
    run.process = Transport([0])
    run.store.redact = lambda text: text.replace("SYNTHETIC_SECRET", "[REDACTED]")

    def failed(command, **kwargs):
        if command[1] == "ps":
            return SimpleNamespace(stdout="", returncode=0)
        raise subprocess.CalledProcessError(1, command, stderr=b"engine error SYNTHETIC_SECRET")

    monkeypatch.setattr("gtoc_bench.finalization.subprocess.run", failed)
    run.finalize()
    report = (run.path / "finalization.json").read_text()
    assert "SYNTHETIC_SECRET" not in report and "engine error [REDACTED]" in report
    errors = json.loads(report)["errors"]
    assert errors[0]["returncode"] == 1 and errors[0]["command"] == ["docker", "inspect", run.name]
    assert json.loads((run.path / "summary.json").read_text())["state"] == "completed"
    seal_matches(run)


def test_orphaned_finalization_is_blocked_without_changing_evidence(tmp_path, monkeypatch):
    original = {"state": "finalizing", "pid": 12345, "stop_reason": "time_limit"}
    write_json(tmp_path / "status.json", original)
    monkeypatch.setattr("gtoc_bench.lifecycle.controller_alive", lambda *_: False)
    observed = read_status(tmp_path)
    assert observed["state"] == "interrupted" and observed["stored_stop_reason"] == "time_limit"
    assert predecessor_state(tmp_path) == {
        "ready": False,
        "blocked": True,
        "reason": "predecessor_controller_lost",
    }
    assert json.loads((tmp_path / "status.json").read_text()) == original


def test_queue_waits_for_a_live_controller_and_rejects_changed_sealed_metadata(run, monkeypatch):
    run.store.write("status.json", {"state": "running", "pid": os.getpid()})
    assert predecessor_state(run.path) == {
        "ready": False,
        "blocked": False,
        "reason": "predecessor_running",
    }
    run.finalize()
    assert predecessor_state(run.path)["ready"]
    (run.path / "summary.json").write_text('{"state":"completed"}')
    assert predecessor_state(run.path)["reason"] == "predecessor_seal_invalid"


def test_reused_pid_does_not_count_as_a_live_controller(monkeypatch):
    monkeypatch.setattr("gtoc_bench.lifecycle.process_start_ticks", lambda _: "new")
    assert controller_alive(os.getpid(), "old") is False


@pytest.mark.parametrize("name", ["summary.json", "finalization.json"])
def test_queue_rejects_malformed_sealed_lifecycle_metadata(run, name):
    run.finalize()
    (run.path / name).write_text("[]")
    run.store.integrity()  # Matching hash alone is insufficient for lifecycle readiness.
    assert predecessor_state(run.path) == {
        "ready": False,
        "blocked": True,
        "reason": "predecessor_seal_invalid",
    }


def test_host_status_exposes_a_blocked_successor_for_orphaned_finalization(
    tmp_path, monkeypatch, capsys
):
    from gtoc_bench import cli

    write_json(
        tmp_path / "status.json", {"state": "finalizing", "pid": 12345, "stop_reason": "time_limit"}
    )
    monkeypatch.setattr("gtoc_bench.lifecycle.controller_alive", lambda *_: False)
    assert cli.main(["status", str(tmp_path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["successor"]["blocked"] and not status["successor"]["ready"]
    assert status["stored_stop_reason"] == "time_limit"


def test_dead_controller_with_terminal_status_but_no_seal_is_not_reported_complete(
    tmp_path, monkeypatch
):
    original = {"state": "completed", "pid": 12345, "stop_reason": "time_limit"}
    write_json(tmp_path / "status.json", original)
    monkeypatch.setattr("gtoc_bench.lifecycle.controller_alive", lambda *_: False)
    status = read_status(tmp_path)
    assert status["state"] == "interrupted" and status["stored_state"] == "completed"
    assert status["stored_stop_reason"] == "time_limit" and predecessor_state(tmp_path)["blocked"]
    assert json.loads((tmp_path / "status.json").read_text()) == original
