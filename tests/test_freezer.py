"""Ambiguous engine outcomes, kernel confirmation, and reversible pause failures."""

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gtoc_bench.budget import Budget
from gtoc_bench.freezer import ContainerFreezer, podman_cgroup
from gtoc_bench.provider_wait import ProviderWait
from gtoc_bench.storage import RunStore, read_events


def runner(tmp_path):
    budget = Budget(60)
    budget.start()
    return SimpleNamespace(
        name="gtoc-synthetic",
        budget=budget,
        store=RunStore(tmp_path),
        gpu_allocation={"assigned": "none"},
        evaluation_slot=threading.Lock(),
        status=budget.status,
    )


@pytest.mark.parametrize("frozen", [True, False])
def test_engine_timeout_reconciles_completed_transition_without_replay(tmp_path, frozen):
    freezer = ContainerFreezer(runner(tmp_path))
    calls = []

    def lost_reply(action):
        calls.append(action)
        raise subprocess.TimeoutExpired(["docker", action], 15)

    freezer.engine_command = lost_reply
    freezer.inspect = lambda: {"State": {"Paused": frozen, "Running": True}}
    freezer.transition(frozen)
    assert len(calls) == 1
    events = (tmp_path / "freezer.jsonl").read_text()
    assert '"command_error": "TimeoutExpired"' in events and '"outcome": "confirmed"' in events


def test_unconfirmed_engine_pause_retries_only_once_then_fails(tmp_path):
    freezer = ContainerFreezer(runner(tmp_path))
    calls = []
    freezer.engine_command = calls.append
    freezer.inspect = lambda: {"State": {"Paused": False, "Running": True}}
    with pytest.raises(RuntimeError, match="Cannot confirm"):
        freezer.transition(True)
    assert calls == ["pause", "pause"]
    assert not freezer.runner.budget.status()["budget_paused"]


def test_second_engine_attempt_can_recover_without_stopping_budget(tmp_path):
    freezer = ContainerFreezer(runner(tmp_path))
    calls = []
    freezer.engine_command = calls.append
    freezer.inspect = lambda: {"State": {"Paused": len(calls) == 2, "Running": True}}
    freezer.transition(True)
    assert len(calls) == 2 and freezer.runner.budget.reason is None


def test_kernel_freeze_request_is_not_confirmation_and_thaw_is_attempted(tmp_path, monkeypatch):
    r = runner(tmp_path)
    wait = ProviderWait(r)
    r.send = lambda kind, **data: wait.acknowledge(data.get("id"))
    directory = tmp_path / "cgroup"
    directory.mkdir()
    (directory / "cgroup.freeze").write_text("0")
    (directory / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    (directory / "cgroup.procs").write_text("")
    wait.freezer.directory = directory
    info = directory.stat()
    wait.freezer.inode = (info.st_dev, info.st_ino)
    wait.freezer.backend = "host_cgroup_v2"
    monkeypatch.setattr("gtoc_bench.freezer.TRANSITION_TIMEOUT", 0)
    with pytest.raises(RuntimeError, match="Kernel did not confirm"):
        wait.pause()
    assert (directory / "cgroup.freeze").read_text() == "0"
    assert not wait.suspended.is_set() and not wait.needs_thaw
    assert r.budget.status()["paused_seconds"] == 0
    assert '"freeze_requested": "1"' in (tmp_path / "freezer.jsonl").read_text()


def test_failed_thaw_remains_pending_for_finalization(tmp_path):
    r, calls = runner(tmp_path), []
    wait = ProviderWait(r)
    r.send = lambda kind, **data: wait.acknowledge(data.get("id"))

    def command(action):
        calls.append(action)
        if len(calls) <= 2:
            raise RuntimeError("unconfirmed transition")

    wait.command = command
    with pytest.raises(RuntimeError):
        wait.pause()
    assert wait.needs_thaw and not r.budget.status()["budget_paused"]
    wait.close()
    assert calls == ["pause", "unpause", "unpause"] and not wait.needs_thaw


def test_clock_resumes_before_delayed_thaw_acknowledgement(tmp_path):
    r = runner(tmp_path)
    wait = ProviderWait(r)
    r.send = lambda kind, **data: wait.acknowledge(data.get("id"))
    r.budget.pause()
    wait.suspended.set()
    wait.needs_thaw = True

    def thaw(action):
        assert action == "unpause" and not r.budget.status()["budget_paused"]

    wait.command = thaw
    wait.resume()
    assert not wait.suspended.is_set()


@pytest.mark.parametrize(
    "path,own",
    [
        ("/", "/user.slice/test"),
        ("/user.slice", "/user.slice/test"),
        ("/user.slice/libpod-" + "b" * 64 + ".scope/container", "/user.slice/controller"),
        (
            "/user.slice/libpod-" + "a" * 64 + ".scope/container",
            "/user.slice/libpod-" + "a" * 64 + ".scope/container",
        ),
    ],
)
def test_never_freeze_parent_wrong_container_or_controller_cgroup(path, own, monkeypatch):
    monkeypatch.setattr(
        "gtoc_bench.freezer.process_cgroup", lambda pid: own if pid == "self" else path
    )
    with pytest.raises(ValueError):
        podman_cgroup({"Id": "a" * 64, "State": {"Pid": 12345}})


def test_native_podman_selection_uses_exact_container_leaf(monkeypatch):
    path = "/user.slice/libpod-" + "a" * 64 + ".scope/container"
    monkeypatch.setattr(
        "gtoc_bench.freezer.process_cgroup", lambda pid: "/controller" if pid == "self" else path
    )
    monkeypatch.setattr("gtoc_bench.freezer.os.access", lambda *_: True)
    assert str(podman_cgroup({"Id": "a" * 64, "State": {"Pid": 12345}})) == "/sys/fs/cgroup" + path


def test_preflight_roundtrip_before_budget_start(tmp_path):
    r = runner(tmp_path)
    r.budget = Budget(60)
    r.status = r.budget.status
    wait = ProviderWait(r)
    r.send = lambda kind, **data: wait.acknowledge(data.get("id"))
    calls = []
    wait.command = calls.append
    wait.preflight("docker")
    assert calls == ["pause", "unpause"]
    assert r.budget.started is None
    assert '"preflight": "passed"' in (tmp_path / "freezer.json").read_text()
    assert any(e["type"] == "provider_wait_preflight" for e in read_events(r.store.events_path))


def test_hung_cli_and_its_child_are_terminated_without_leaving_open_pipes(tmp_path, monkeypatch):
    binary = tmp_path / "docker"
    binary.write_text(
        f"#!{sys.executable}\nimport subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])\n"
        "time.sleep(20)\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr("gtoc_bench.freezer.COMMAND_TIMEOUT", 0.3)
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ContainerFreezer(runner(tmp_path / "run")).engine_command("pause")
    # Killing only the CLI would leave its child holding the pipe open for 20 s.
    assert time.monotonic() - began < 3
