import errno
import json
import os
from types import SimpleNamespace

import pytest
from test_continuation import parent_run

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.continuation import create_continuation
from gtoc_bench.host_sharing import sharing_plan
from gtoc_bench.storage import RunStore
from gtoc_bench.supervision import (
    HEARTBEAT_TIMEOUT,
    HeartbeatFile,
    HeartbeatReader,
    MemoryGuard,
    memory_sample,
    size_bytes,
)


def test_independent_heartbeat_times_out_without_new_valid_observations(tmp_path):
    tick = [100.0]
    writer = HeartbeatFile(tmp_path)
    reader = HeartbeatReader(tmp_path, clock=lambda: tick[0])
    writer.beat()
    assert reader.poll()["sequence"] == 1
    tick[0] += HEARTBEAT_TIMEOUT - 1
    assert reader.poll()["age_seconds"] < HEARTBEAT_TIMEOUT
    writer.path.write_text("partial-invalid-frame")
    tick[0] += 2
    assert reader.poll()["age_seconds"] > HEARTBEAT_TIMEOUT
    writer.beat()
    assert reader.poll()["age_seconds"] == 0


def test_mutable_heartbeat_mount_is_rejected(tmp_path):
    HeartbeatFile(tmp_path).beat()
    with pytest.raises(ValueError, match="read-only"):
        HeartbeatReader(tmp_path).verify_mount()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["heartbeat.json"]


@pytest.mark.parametrize("error", [errno.EROFS, errno.EACCES, errno.EPERM, errno.EIO])
def test_heartbeat_mount_requires_kernel_readonly_enforcement(tmp_path, monkeypatch, error):
    HeartbeatFile(tmp_path).beat()
    original = (tmp_path / "heartbeat.json").read_bytes()
    calls = []

    def open_probe(path, flags, mode):
        calls.append((path, flags, mode))
        raise OSError(error, "synthetic mount probe")

    monkeypatch.setattr("gtoc_bench.supervision.os.open", open_probe)
    # Reproduce the flags observed under x86 emulation on Spark's ARM host.
    monkeypatch.setattr("gtoc_bench.supervision.os.statvfs", lambda _: SimpleNamespace(f_flag=32))
    if error == errno.EROFS:
        HeartbeatReader(tmp_path).verify_mount()
    else:
        with pytest.raises(ValueError, match="read-only"):
            HeartbeatReader(tmp_path).verify_mount()
    path, flags, mode = calls[0]
    assert path.parent == tmp_path and path.name.startswith(".readonly-check-")
    assert flags & os.O_EXCL and flags & os.O_NOFOLLOW and mode == 0o600
    assert (tmp_path / "heartbeat.json").read_bytes() == original


def test_working_set_excludes_reclaimable_file_cache(tmp_path):
    (tmp_path / "memory.current").write_text(str(1000))
    (tmp_path / "memory.max").write_text(str(1024))
    (tmp_path / "memory.stat").write_text("anon 200\ninactive_file 750\nfile 780\n")
    (tmp_path / "memory.events").write_text("max 5\noom 0\noom_kill 0\n")
    sample = memory_sample(tmp_path)
    assert sample["working_set_bytes"] == 250
    assert sample["current_bytes"] == 1000
    assert sample["events"]["oom_kill"] == 0 and sample["pressure"] is None


@pytest.mark.parametrize(
    "text,expected", [("1.5GiB", 1610612736), ("512MiB", 536870912), ("1GB", 1000000000), ("0B", 0)]
)
def test_docker_desktop_memory_units(text, expected):
    assert size_bytes(text) == expected


@pytest.mark.parametrize(
    "usage,oom,stops", [(0.2, 0, False), (0.8, 0, False), (0.91, 0, True), (0.2, 1, True)]
)
def test_memory_guard_cancels_inference_and_stops_with_a_specific_reason(
    tmp_path, monkeypatch, usage, oom, stops
):
    budget = Budget(100)
    budget.start()
    calls = []
    runner = SimpleNamespace(
        store=RunStore(tmp_path),
        budget=budget,
        gateway=SimpleNamespace(cancel=lambda: calls.append("cancel_model")),
        memory_limit_bytes=1000,
        name="synthetic-container",
    )
    guard = MemoryGuard(runner)
    monkeypatch.setattr(
        "gtoc_bench.supervision.subprocess.run", lambda *args, **kw: calls.append("stop_agent")
    )
    guard.observe(
        {
            "working_set_bytes": int(usage * 1000),
            "limit_bytes": 1000,
            "events": {"oom_kill": oom},
            "source": "synthetic",
        }
    )
    assert budget.reason == ("memory_limit" if stops else None)
    assert calls == (["cancel_model", "stop_agent"] if stops else [])
    assert runner.memory_status["working_set_fraction"] == usage


def test_resume_ram_change_requires_explicit_override_and_preserves_budget(tmp_path):
    parent, config, session = parent_run(tmp_path)
    config["hardware"]["memory_gb"] = 96
    with pytest.raises(ValueError, match="hardware"):
        create_continuation(parent, config, tmp_path)
    child = create_continuation(parent, config, tmp_path, memory_gb=96)
    launch = json.loads((child / "launch.json").read_text())
    lineage = json.loads((child / "continuation.json").read_text())
    assert launch["hardware"]["memory_gb"] == 96
    assert launch["budget"] == {"wall_seconds": 70, "token_limit": 600}
    assert lineage["session_id"] == session
    assert not lineage["homogeneous_resources"] and not lineage["independent_run"]
    assert lineage["resource_changes"]["memory_gb"] == {
        "before": None,
        "after": 96,
        "explicit_override": True,
    }


def test_parallel_admission_reserves_host_ram_as_well_as_cpu():
    config = load_config(ROOT / "configs/codex.yaml")
    peer = "20261104T120000-" + "a" * 12
    config["hardware"].update(cpus=2, memory_gb=32, concurrent_with=peer, gpus="none")
    info = {"NCPU": 64, "MemTotal": 64 * 1024**3}
    container = {
        "Name": "/gtoc-" + peer.lower(),
        "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 32 * 1024**3},
    }
    with pytest.raises(ValueError, match="memory limits"):
        sharing_plan(config, info, [container])
    config["hardware"]["memory_gb"] = 16
    result = sharing_plan(config, info, [container])
    assert result["memory_limit_bytes"] + result["peer_memory_limit_bytes"] == 48 * 1024**3
