import gzip
import hashlib
import json
import subprocess
import sys
import zlib

import pytest

from gtoc_bench import ROOT
from gtoc_bench.audit import ResponseTrace, read_records
from gtoc_bench.config import load_config
from gtoc_bench.disk import GIB, admission, audit_usage, parse_quota
from gtoc_bench.request_artifacts import read_request, save_requests
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import RunStore
from gtoc_bench.submission_artifacts import read_submission, save_submission


def test_request_overlay_and_gzip_preserve_large_context_without_duplicate_bodies(tmp_path):
    store = RunStore(tmp_path)
    client = {"model": "synthetic", "input": "Repeated scientific context\n" * 20000}
    provider = client | {"service_tier": "default", "reasoning": {"effort": "max"}}
    artifacts = save_requests(store, "requests/r", client, provider)
    folder = tmp_path / "requests/r"
    assert artifacts["client"]["format"] == "gtoc-request-overlay-v1"
    assert read_request(folder, client=True) == client
    assert read_request(folder) == provider
    assert audit_usage(tmp_path) < 20000
    assert not (folder / "request.json").exists()


def test_protocol_conversion_shares_messages_when_overlay_would_duplicate_context(tmp_path):
    store = RunStore(tmp_path)
    original = {"messages": [{"role": "user", "content": "long context " * 10000}]}
    effective = {"input": original["messages"], "instructions": "synthetic"}
    artifacts = save_requests(store, "requests/r", original, effective)
    assert artifacts["client"]["compression"] == "gzip_parts"
    assert artifacts["client"]["reused_parts"] == 1
    assert read_request(tmp_path / "requests/r", client=True) == original


def test_compression_keeps_redaction_and_seals_stored_bytes(tmp_path):
    store = RunStore(tmp_path)
    store.redact = lambda text: text.replace("private-value", "[REDACTED]")
    artifacts = save_requests(
        store, "requests/r", {"input": "private-value"}, {"input": "private-value"}
    )
    assert "private-value" not in json.dumps(read_request(tmp_path / "requests/r"))
    store.integrity()
    seal = json.loads((tmp_path / "integrity.json").read_text())
    assert artifacts["provider"]["path"] in seal["files"]


def test_gzip_response_records_are_readable_live_and_after_an_unfinished_stream(tmp_path):
    trace = ResponseTrace(RunStore(tmp_path), "requests/r/response.jsonl")
    trace.feed(b'data: {"type":"response.created"}\n\n')
    trace.compressed.flush(zlib.Z_SYNC_FLUSH)
    path = tmp_path / trace.relative
    # Live streams have complete synced records but do not yet have a gzip trailer.
    assert len(list(read_records(path))) == 1
    trace.feed(b"another event" * 200)
    result = trace.close(True)
    assert result["compression"] == "gzip" and result["stored_bytes"] < result["bytes"]
    assert len(list(read_records(path))) == 2


def test_legacy_request_and_jsonl_remain_readable(tmp_path):
    (tmp_path / "request.json").write_text('{"input": "old"}')
    (tmp_path / "events.jsonl").write_text('{"old": true}\npartial')
    assert read_request(tmp_path) == {"input": "old"}
    assert list(read_records(tmp_path / "events.jsonl")) == [{"old": True}]


def test_quota_counters_use_user_hard_limit_and_expired_soft_grace():
    base = "/dev/mapper/test 20000000 15728640 41943040 {} 3000 0 0 0"
    future = parse_quota(base.format(2000), "/dev/mapper/test", now=1000)
    assert future["available_bytes"] == (41943040 - 20000000) * 1024
    past = parse_quota(base.format(500), "/dev/mapper/test", now=1000)
    assert past["available_bytes"] == 0
    assert past["effective_limit_bytes"] == 15728640 * 1024
    assert parse_quota("not quota data", "/dev/mapper/test") is None


def test_admission_estimates_three_runs_without_promising_an_audit_size_bound():
    config = load_config(ROOT / "configs/codex-gtoc13.yaml")
    config["hardware"]["workspace_gb"] = 4
    config["storage"] = {"audit_gb": 2, "minimum_free_gb": 2}
    report = admission(config, {"available_bytes": 25 * GIB}, [6 * GIB, 6 * GIB])
    assert report["admitted"] and report["required_free_bytes"] == 20 * GIB
    assert report["audit_reserve_bytes"] == 2 * GIB
    assert report["audit_limit_bytes"] is None and not report["audit_limit_enforced"]
    assert not admission(config, {"available_bytes": 19 * GIB}, [6 * GIB, 6 * GIB])["admitted"]


def test_low_user_quota_rejects_before_container_or_gateway_start(tmp_path, monkeypatch):
    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"]["gpus"] = "none"
    runner = Runner(create_run(config, tmp_path))
    monkeypatch.setattr(
        "gtoc_bench.runner.docker_json",
        lambda *args: [{"Architecture": "amd64", "Os": "linux", "Id": "synthetic-image"}]
        if args[0] == "image"
        else {},
    )
    monkeypatch.setattr(
        "gtoc_bench.runner.normalize_engine_info", lambda value: {"NCPU": 32, "RawInfo": {}}
    )
    monkeypatch.setattr("gtoc_bench.runner.allocation", lambda *args: {"assigned": "none"})
    monkeypatch.setattr("gtoc_bench.runner.subprocess.check_output", lambda *args, **kw: "")
    monkeypatch.setattr(
        "gtoc_bench.disk.capacity",
        lambda path: {
            "filesystem_free_bytes": 200 * GIB,
            "available_bytes": GIB,
            "quota": {"available": True},
        },
    )
    monkeypatch.setattr(
        runner, "start_container", lambda *args: pytest.fail("Container must not start")
    )
    try:
        with pytest.raises(ValueError, match="Insufficient user quota"):
            runner._run_locked()
        assert runner.gateway is None and runner.budget.started is None
        report = json.loads((runner.path / "storage-preflight.json").read_text())
        assert not report["admitted"] and report["filesystem_free_bytes"] == 200 * GIB
    finally:
        runner.pool.shutdown()


def test_audit_size_skips_workspace_and_does_not_follow_links(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace/large").write_bytes(b"x" * 1000)
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests/log").write_bytes(b"abc")
    (tmp_path / "requests/outside").symlink_to(tmp_path / "workspace")
    assert audit_usage(tmp_path) == 3


@pytest.mark.parametrize("legacy", [True, False])
def test_audit_growth_does_not_stop_run_even_with_old_queued_two_gib_config(
    tmp_path, monkeypatch, legacy
):
    config = load_config(ROOT / "configs/codex.yaml")
    config["storage"] = {"audit_gb" if legacy else "audit_reserve_gb": 2, "minimum_free_gb": 2}
    runner = Runner(create_run(config, tmp_path))
    runner.budget.start()
    monkeypatch.setattr("gtoc_bench.disk.capacity", lambda path: {"available_bytes": 3200 * GIB})
    monkeypatch.setattr("gtoc_bench.disk.audit_usage", lambda path: 30 * GIB)
    try:
        runner.check_disk()
        assert runner.budget.check() and runner.budget.reason is None
        status = json.loads((runner.path / "storage-status.json").read_text())
        assert status["audit_bytes"] == 30 * GIB
        assert status["audit_limit_bytes"] is None and not status["audit_limit_enforced"]
        assert status["pressure_cause"] is None
        assert not (runner.path / "events.jsonl").exists()
    finally:
        runner.pool.shutdown()


def test_storage_pressure_still_stops_for_actual_disk_or_user_quota_exhaustion(
    tmp_path, monkeypatch
):
    config = load_config(ROOT / "configs/codex.yaml")
    runner = Runner(create_run(config, tmp_path))
    runner.budget.start()
    monkeypatch.setattr(
        "gtoc_bench.disk.capacity",
        lambda path: {"available_bytes": GIB},
    )
    monkeypatch.setattr("gtoc_bench.disk.audit_usage", lambda path: 0)
    try:
        runner.check_disk()
        assert runner.budget.reason == "infrastructure_failure"
        events = (runner.path / "events.jsonl").read_text()
        assert '"type": "storage_pressure"' in events and "user_quota_or_disk_margin" in events
        status = json.loads((runner.path / "storage-status.json").read_text())
        assert status["pressure_cause"] == "user_quota_or_disk_margin"
    finally:
        runner.pool.shutdown()


def test_legacy_audit_config_is_migrated_to_an_estimate(tmp_path):
    source = (ROOT / "configs/codex.yaml").read_text()
    path = tmp_path / "profile.yaml"
    path.write_text(source + "\nstorage:\n  audit_gb: 3\n")
    cfg = load_config(path)
    assert cfg["storage"] == {"audit_reserve_gb": 3, "minimum_free_gb": 2}
    path.write_text(source + "\nstorage:\n  audit_gb: 3\n  audit_reserve_gb: 4\n")
    with pytest.raises(ValueError, match="Use only"):
        load_config(path)


def test_repeated_context_is_stored_once_and_restored_exactly(tmp_path):
    store = RunStore(tmp_path)
    context = {"role": "user", "content": "scientific context with unicode Ω\n" * 5000}
    for index in range(20):
        request = {
            "input": [context, {"role": "user", "content": f"turn {index}"}],
            "temperature": 0.0,
        }
        artifacts = save_requests(store, f"requests/{index}", request, request)
        assert read_request(tmp_path / f"requests/{index}") == request
        assert read_request(tmp_path / f"requests/{index}", client=True) == request
        assert artifacts["provider"]["reused_parts"] >= (2 if index else 0)
    assert len(list((tmp_path / "requests/blobs").iterdir())) == 22
    assert audit_usage(tmp_path) < 40000
    store.integrity()
    seal = json.loads((tmp_path / "integrity.json").read_text())
    assert all(
        str(p.relative_to(tmp_path)) in seal["files"]
        for p in (tmp_path / "requests/blobs").iterdir()
    )


@pytest.mark.parametrize("kind", ["symlink", "corruption", "invalid_digest", "oversized"])
def test_request_reader_rejects_unsafe_or_corrupt_blobs(tmp_path, kind):
    store = RunStore(tmp_path)
    save_requests(store, "requests/r", {"input": []}, {"input": []})
    folder = tmp_path / "requests/r"
    manifest_path = folder / "request.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    blob = tmp_path / "requests/blobs" / (manifest["envelope"] + ".json.gz")
    if kind == "symlink":
        outside = tmp_path / "outside.gz"
        blob.rename(outside)
        blob.symlink_to(outside)
    elif kind == "corruption":
        blob.write_bytes(gzip.compress(b'{"corrupt":true}'))
    elif kind == "invalid_digest":
        manifest["envelope"] = "../../outside"
        manifest_path.write_text(json.dumps(manifest))
    else:
        manifest["logical_bytes"] = 2**40
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        read_request(folder)


def test_legacy_gzip_and_overlay_remain_readable(tmp_path):
    (tmp_path / "request.json.gz").write_bytes(
        gzip.compress(b'{"input":"old","service_tier":"default"}')
    )
    (tmp_path / "client_request.json").write_text(
        json.dumps(
            {
                "format": "gtoc-request-overlay-v1",
                "base": "request.json.gz",
                "set": {},
                "remove": ["service_tier"],
            }
        )
    )
    assert read_request(tmp_path, client=True) == {"input": "old"}


def test_submissions_are_losslessly_compressed_and_exportable_without_execution(tmp_path):
    data = b" 1.234567890123456789e-16\r\n" * 10000 + b"\xff\x00"
    artifact = save_submission(tmp_path, data)
    assert read_submission(tmp_path) == data
    assert artifact["sha256"] == hashlib.sha256(data).hexdigest()
    assert artifact["stored_bytes"] < len(data) // 10
    assert not (tmp_path / "solution.txt").exists()
    output = tmp_path / "restored.txt"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "gtoc_bench",
            "inspect-submission",
            str(tmp_path),
            "--output",
            str(output),
        ],
        check=True,
    )
    assert output.read_bytes() == data


def test_legacy_submission_and_bounded_non_symlink_readers(tmp_path):
    (tmp_path / "solution.txt").write_bytes(b"old")
    assert read_submission(tmp_path) == b"old"
    save_submission(tmp_path, b"x" * 100)
    with pytest.raises(ValueError, match="size limit"):
        read_submission(tmp_path, limit=10)
    (tmp_path / "solution.txt.gz").unlink()
    (tmp_path / "solution.txt.gz").symlink_to(tmp_path / "solution.txt")
    with pytest.raises(ValueError, match="symlinks"):
        read_submission(tmp_path)
