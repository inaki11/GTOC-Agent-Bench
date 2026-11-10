import hashlib
import io
import json
import os
import subprocess
import time

from gtoc_bench.analysis import export_audit
from gtoc_bench.audit import AgentTrace, ResponseTrace, read_records
from gtoc_bench.budget import Budget
from gtoc_bench.container_tools import serve
from gtoc_bench.observation import WorkspaceObserver, workspace_inventory
from gtoc_bench.storage import RunStore, read_events


def test_fragmented_trace_redacts_secrets_and_marks_pending_tools(tmp_path):
    store = RunStore(tmp_path)
    secret = "synthetic-private-credential"
    store.redact = lambda text: text.replace(secret, "[REDACTED]")
    budget = Budget(60)
    budget.start()
    store.started = budget.started
    trace = AgentTrace(store, budget, "codex")
    raw = (
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "command": "echo español " + secret,
                },
            },
            ensure_ascii=False,
        ).encode()
        + b"\n"
    )
    for byte in raw:
        trace.feed("stdout", bytes([byte]))
    trace.feed(
        "stdout",
        json.dumps(
            {
                "type": "item.updated",
                "item": {"id": "cmd1", "type": "command_execution", "status": "in_progress"},
            }
        ).encode()
        + b"\n",
    )
    trace.feed("stderr", b"an incomplete line " + secret.encode())
    budget.stop("time_limit")
    trace.close()
    records = list(read_records(tmp_path / "agent/transcript.jsonl"))
    assert records[0]["activity"]["phase"] == "started"
    assert "español" in records[0]["native_event"]["item"]["command"]
    assert records[-1]["partial"] is True
    assert list(read_events(tmp_path / "events.jsonl"))[-1]["pending_tools"] == ["cmd1"]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    store.integrity()
    assert (
        "agent/transcript.jsonl" in json.loads((tmp_path / "integrity.json").read_text())["files"]
    )


def test_claude_trace_pairs_tool_use_and_result(tmp_path):
    store, budget = RunStore(tmp_path), Budget(60)
    trace = AgentTrace(store, budget, "claude_code")
    for event in [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "false"}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "is_error": True,
                        "content": "exit 1",
                    }
                ]
            },
        },
    ]:
        trace.feed("stdout", json.dumps(event).encode() + b"\n")
    trace.close()
    assert not trace.pending_tools
    assert len(list(read_records(tmp_path / "agent/transcript.jsonl"))) == 2


def test_response_trace_keeps_partial_stream_and_explicit_limit(tmp_path):
    store = RunStore(tmp_path)
    trace = ResponseTrace(store, "requests/test/response.jsonl", limit=200)
    trace.feed(b'data: {"authorization":"Bearer private-secret"}\n\n')
    trace.feed(b"X" * 500)
    result = trace.close(False)
    assert result["complete"] is False and result["dropped_bytes"] == 500
    raw = json.dumps(list(read_records(tmp_path / result["path"])))
    assert "private-secret" not in raw
    assert "[REDACTED]" in raw


def test_snapshots_keep_revisions_and_do_not_follow_symlinks_or_fifo(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("not for the agent")
    (workspace / "link.txt").symlink_to(outside)
    (workspace / "linked-directory").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(workspace / "pipe")
    (workspace / "large.txt").write_bytes(b"x" * (1024**2 + 1))
    (workspace / "binary").write_bytes(b"\x00")
    code = workspace / "solver.py"
    code.write_text("print(1)")
    observer, records = WorkspaceObserver(workspace), []
    observer.capture(lambda kind, **data: records.append({"type": kind, **data}), "initial")
    code.write_text("print(2)")
    observer.capture(
        lambda kind, **data: records.append({"type": kind, **data}), "submission", "s1"
    )
    copies = [r for r in records if r["type"] == "snapshot_file"]
    assert [r["content"] for r in copies] == ["print(1)", "print(2)"]
    assert len({r["original_sha256"] for r in copies}) == 2
    assert records[-1]["submission_id"] == "s1"
    manifests = [r for r in records if r["type"] == "snapshot"]
    assert any(r.get("omitted") == "file_size_limit" for r in manifests[0]["files"])
    assert manifests[1]["layout"] == "delta"
    assert [r["path"] for r in manifests[1]["files"]] == ["solver.py"]
    inventory = workspace_inventory(workspace)
    assert inventory["files"]["solver.py"]["sha256"] == hashlib.sha256(b"print(2)").hexdigest()
    assert "link.txt" not in inventory["files"]
    assert any(r["path"] == "pipe" for r in inventory["omitted"])


def test_audit_report_escapes_untrusted_content_and_handles_missing_metrics(tmp_path):
    store = RunStore(tmp_path)
    store.write("summary.json", {"stop_reason": "time_limit"})
    (tmp_path / "prompt.txt").write_text("<script>alert(1)</script>")
    store.append("resources.jsonl", "resource_sample", cgroup={"memory.current": None})
    store.append("agent/transcript.jsonl", "agent_activity", text="<script>bad()</script>")
    path = export_audit(tmp_path)
    report = path.read_text()
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "Content-Security-Policy" in report


def test_native_mcp_logs_command_and_truncated_result_without_credentials(tmp_path, monkeypatch):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "exec", "arguments": {"command": "echo test", "timeout_seconds": 2}},
    }
    monkeypatch.setattr(
        "sys.stdin",
        type("Input", (), {"buffer": io.BytesIO(json.dumps(request).encode() + b"\n")})(),
    )
    result = {"returncode": 0, "output": "test", "truncated": True, "timed_out": False}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(result).encode()),
    )
    path = tmp_path / "tools.jsonl"
    serve("container", str(path), time.monotonic())
    records = list(read_records(path))
    assert [r["type"] for r in records] == ["tool_started", "tool_finished"]
    assert records[0]["call_id"] == records[1]["call_id"]
    assert records[1]["result"]["truncated"] is True
    assert records[0]["source"] == "trusted_mcp_server"
