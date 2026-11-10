import hashlib
import io
import json
import os
import queue
import threading
from pathlib import Path

import pytest

from gtoc_bench import ROOT
from gtoc_bench.analysis import AUDIT_ROWS, export_audit
from gtoc_bench.config import load_config
from gtoc_bench.observation import SnapshotRequests, WorkspaceObserver, workspace_usage
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.snapshots import read_snapshot
from gtoc_bench.storage import RunStore


def test_cancelled_workspace_scan_is_explicit_and_final_scan_is_not_cancelled(
    tmp_path, monkeypatch
):
    import gtoc_bench.observation as observation

    stop = threading.Event()
    (tmp_path / "a").write_bytes(b"1234")
    (tmp_path / "b").write_bytes(b"12345678")
    fwalk = observation.os.fwalk

    def interrupt(*args, **kwargs):
        for directory, dirs, _, fd in fwalk(*args, **kwargs):
            yield directory, dirs, ["a"], fd
            stop.set()
            yield directory, [], ["b"], fd

    monkeypatch.setattr(observation.os, "fwalk", interrupt)
    incomplete = workspace_usage(tmp_path, stop)
    assert incomplete["bytes"] == 4 and not incomplete["complete"]
    monkeypatch.setattr(observation.os, "fwalk", fwalk)
    final = workspace_usage(tmp_path)
    assert final["complete"] and final["bytes"] == 12


def test_partial_frame_never_overwrites_complete_size_and_final_counts_large_files(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    try:
        runner.process = type(
            "Process",
            (),
            {
                "stdout": io.BytesIO(
                    b'{"type":"workspace_usage","bytes":200,"complete":true}\n'
                    b'{"type":"workspace_usage","bytes":12,"complete":false}\n'
                )
            },
        )()
        runner.read()
        assert runner.workspace_bytes == 200
        work = runner.path / "workspace"
        with (work / "large").open("wb") as stream:
            stream.truncate(2**30)
        (work / "small").write_bytes(b"abc")
        (work / "outside").symlink_to(tmp_path)
        runner.final_workspace_usage()
        assert runner.workspace_bytes == 2**30 + 3
        report = json.loads((runner.path / "workspace-usage.json").read_text())
        assert (
            report["final_scan"]["complete"]
            and report["reported"]["phase"] == "after_container_stop"
        )
    finally:
        runner.pool.shutdown()


def test_failed_final_measurement_retains_last_complete_size(tmp_path, monkeypatch):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    try:
        runner.workspace_bytes = 1450
        runner.workspace_measurement = {"available": True, "bytes": 1450, "complete": True}
        monkeypatch.setattr(
            "gtoc_bench.observation.workspace_usage", lambda _: {"bytes": 164, "complete": False}
        )
        runner.final_workspace_usage()
        assert runner.workspace_bytes == 1450
        assert runner.workspace_measurement["source"] == "last_complete"
        assert not runner.workspace_measurement["final_scan_complete"]
    finally:
        runner.pool.shutdown()


def test_snapshot_requests_coalesce_without_losing_submission_correlation():
    requests = SnapshotRequests(limit=3)
    for _ in range(100):
        requests.request("periodic")
    requests.request("submission", "s1")
    requests.request("submission", "s2")
    with pytest.raises(queue.Full):
        requests.request("submission", "s3")
    batch = requests.take(timeout=0)
    assert sum(t["count"] for t in batch) == 102
    assert {t["submission_id"] for t in batch} == {None, "s1", "s2"}
    with pytest.raises(queue.Empty):
        requests.take(timeout=0)


def test_delta_snapshots_deduplicate_and_reconstruct_edits_deletions_and_reversions(tmp_path):
    work, run = tmp_path / "workspace", tmp_path / "run"
    work.mkdir()
    store = RunStore(run)
    observer = WorkspaceObserver(work)
    blobs = {}

    def send(kind, **frame):
        if kind == "snapshot_file":
            content = frame["content"].replace("synthetic-secret", "[REDACTED]")
            digest = hashlib.sha256(content.encode()).hexdigest()
            blob = "snapshots/blobs/" + digest
            (run / blob).parent.mkdir(parents=True, exist_ok=True)
            (run / blob).write_text(content)
            blobs[frame["original_sha256"]] = {"blob": blob, "stored_sha256": digest}
        else:
            for entry in frame["files"]:
                if entry.get("original_sha256"):
                    entry.update(blobs[entry["original_sha256"]])
            if frame["base_snapshot_id"]:
                frame["base_snapshot"] = f"snapshots/{frame['base_snapshot_id']}/manifest.json"
            store.write(f"snapshots/{frame['snapshot_id']}/manifest.json", frame)

    (work / "solver.py").write_text("synthetic-secret A")
    (work / "keep.py").write_text("stable")
    observer.capture(send, "initial")
    first = observer.previous_id
    observer.capture(send, "periodic")
    second = observer.previous_id
    unchanged = json.loads((run / f"snapshots/{second}/manifest.json").read_text())
    assert unchanged["files"] == [] and len(blobs) == 2
    before = (work / "solver.py").stat()
    (work / "solver.py").write_text("synthetic-secret B")
    os.utime(work / "solver.py", ns=(before.st_atime_ns, before.st_mtime_ns))
    (work / "keep.py").unlink()
    observer.capture(send, "submission", "s1")
    third = observer.previous_id
    result = read_snapshot(run, f"snapshots/{third}/manifest.json")
    assert set(result["files"]) == {"solver.py"}
    assert (run / result["files"]["solver.py"]["blob"]).read_text() == "[REDACTED] B"
    (work / "solver.py").write_text("synthetic-secret A")
    observer.capture(send, "final")
    assert len(blobs) == 3
    original = read_snapshot(run, f"snapshots/{first}/manifest.json")
    final = read_snapshot(run, f"snapshots/{observer.previous_id}/manifest.json")
    assert original["files"]["solver.py"]["blob"] == final["files"]["solver.py"]["blob"]


def test_incomplete_snapshot_does_not_invent_deletions(tmp_path):
    (tmp_path / "keep.py").write_text("preserve")
    observer, frames = WorkspaceObserver(tmp_path), []

    def send(kind, **v):
        frames.append({"type": kind, **v})

    observer.capture(send, "initial")
    stop = threading.Event()
    stop.set()
    observer.capture(send, "periodic", stop=stop)
    assert frames[-1]["removed"] == []
    assert frames[-1]["file_count"] == 1
    assert not frames[-1]["capture_complete"] and frames[-1]["truncated"]


def test_capture_read_budget_includes_binaries_and_reuses_cached_observations(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("gtoc_bench.observation.MAX_SNAPSHOT", 8)
    (tmp_path / "a.bin").write_bytes(b"\x00" * 4)
    (tmp_path / "b.bin").write_bytes(b"\x00" * 4)
    (tmp_path / "solver.py").write_text("code")
    observer, frames = WorkspaceObserver(tmp_path), []

    def send(kind, **frame):
        frames.append({"type": kind, **frame})

    observer.capture(send, "initial")
    assert not [f for f in frames if f["type"] == "snapshot_file"]
    assert frames[-1]["capture_complete"] and frames[-1]["truncated"]
    assert frames[-1]["files"][-1]["omitted"] == "snapshot_size_limit"
    observer.capture(send, "periodic")
    assert [f["content"] for f in frames if f["type"] == "snapshot_file"] == ["code"]
    assert not frames[-1]["truncated"]


def test_delta_frames_reach_runner_with_redacted_blobs_and_all_submission_refs(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    try:
        runner.store.redact = lambda text: text.replace("synthetic-secret", "[REDACTED]")
        work = runner.path / "workspace"
        (work / "solver.py").write_text("synthetic-secret A")
        observer, transport = WorkspaceObserver(work), io.BytesIO()

        def send(kind, **frame):
            transport.write((json.dumps({"type": kind, **frame}) + "\n").encode())

        observer.capture(send, "initial")
        (work / "solver.py").write_text("synthetic-secret B")
        observer.capture(
            send,
            "submission",
            "s1",
            triggers=[
                {"reason": "submission", "submission_id": "s1", "count": 1},
                {"reason": "submission", "submission_id": "s2", "count": 1},
            ],
        )
        transport.seek(0)
        runner.process = type("Process", (), {"stdout": transport})()
        runner.read()
        assert runner.fatal is None
        refs = [
            json.loads((runner.path / f"snapshots/submissions/{sid}.json").read_text())
            for sid in ("s1", "s2")
        ]
        assert refs[0] == refs[1] and refs[0]["coalesced_requests"] == 2
        view = read_snapshot(runner.path, refs[0]["snapshot_ref"])
        entry = view["files"]["solver.py"]
        assert entry["redacted"]
        assert (runner.path / entry["blob"]).read_text() == "[REDACTED] B"
        assert len(list((runner.path / "snapshots/blobs").iterdir())) == 2
        assert not list((runner.path / "snapshots").glob("*/files"))
        assert view["chain_length"] == 2
    finally:
        runner.pool.shutdown()


def test_compact_html_does_not_open_snapshot_manifests_or_inline_huge_results(
    tmp_path, monkeypatch
):
    store = RunStore(tmp_path)
    for n in range(AUDIT_ROWS + 100):
        store.event(
            "workspace_snapshot",
            snapshot_id=str(n),
            reason="periodic",
            artifact=f"snapshots/{n}/manifest.json",
        )
    store.append(
        "agent/transcript.jsonl", "agent_activity", text="<script>attack()</script>" + "x" * 1000000
    )
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert "snapshots" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    target = export_audit(tmp_path)
    assert target.stat().st_size < 250000
    text = target.read_text()
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "registros de 300" in text


def test_legacy_full_snapshot_reader_and_reference_guards(tmp_path):
    store = RunStore(tmp_path)
    store.write(
        "snapshots/a/manifest.json", {"files": [{"path": "solver.py", "original_sha256": "raw"}]}
    )
    name = hashlib.sha256(b"solver.py").hexdigest()
    store.write(
        f"snapshots/a/files/{name}.json", {"path": "solver.py", "blob": "snapshots/blobs/abc"}
    )
    assert (
        read_snapshot(tmp_path, "snapshots/a/manifest.json")["files"]["solver.py"]["blob"]
        == "snapshots/blobs/abc"
    )
    with pytest.raises(ValueError):
        read_snapshot(tmp_path, "../outside.json")
    store.write(
        "snapshots/cycle/manifest.json",
        {"layout": "delta", "base_snapshot": "snapshots/cycle/manifest.json"},
    )
    with pytest.raises(ValueError, match="Cyclic"):
        read_snapshot(tmp_path, "snapshots/cycle/manifest.json")
