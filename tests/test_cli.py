"""Machine-readable CLI failures, including overlapping real controller requests."""

import base64
import concurrent.futures
import hashlib
import io
import json
import threading
import urllib.error
from urllib.parse import urlsplit

import pytest

from gtoc_bench import ROOT, cli
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events


@pytest.mark.parametrize("same_submission", [False, True])
def test_json_busy_evaluation_can_be_retried_after_pending_finishes(
    tmp_path, monkeypatch, capsys, same_submission
):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path / "runs"))
    runner.budget.start()
    candidate = tmp_path / "candidate.txt"
    candidate.write_bytes(b"candidate")
    first_id = "a" * 32
    submission_id = first_id if same_submission else "b" * 32
    pending = threading.Event()
    replies = {}
    verdict = {
        "status": "valid",
        "score": 0,
        "diagnostics": [],
        "artifact_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
    }

    def send(kind, **frame):
        if kind == "validate":
            if frame["id"] == first_id and not pending.is_set():
                pending.set()  # Keep the protected validator pending until explicitly released.
            else:
                runner.verdicts[frame["id"]].put(verdict.copy())
        else:
            assert kind == "reply"
            replies[frame["id"]] = frame

    runner.send = send

    def transport(request, **_):
        # Exercise the controller and urllib error handling without Docker or a model API.
        runner.request_slots.acquire()
        runner.handle(
            {
                "id": "cli-request",
                "path": urlsplit(request.full_url).path,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": base64.b64encode(request.data).decode(),
            }
        )
        reply = replies.pop("cli-request")
        body = io.BytesIO(base64.b64decode(reply["body"]))
        if reply["status"] >= 400:
            raise urllib.error.HTTPError(
                request.full_url, reply["status"], "Rejected", reply["headers"], body
            )
        return body

    monkeypatch.setenv("GTOC_ENDPOINT", "http://controller.invalid")
    monkeypatch.setattr(cli.urllib.request, "urlopen", transport)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    first = pool.submit(runner.accept, candidate.read_bytes(), first_id)
    try:
        assert pending.wait(3)
        args = ["evaluate", str(candidate), "--json", "--submission-id", submission_id]
        assert cli.main(args) == 1
        output = capsys.readouterr()
        error = json.loads(output.out)
        assert not output.err
        assert error["status"] == "error" and error["score"] is None
        assert error["code"] == "evaluation_busy" and error["retryable"] is True
        assert error["http_status"] == 400
        assert error["diagnostics"] == [error["error"]]
        assert len(list((runner.path / "submissions").iterdir())) == 1
        assert not first.done()  # No automatic retry or concurrent validation.
        recorded = next(
            e for e in read_events(runner.path / "events.jsonl") if e["type"] == "http_error"
        )
        assert recorded["client_request_id"] == "cli-request"
        assert recorded["response"]["code"] == error["code"]

        runner.verdicts[first_id].put(verdict.copy())
        assert first.result(timeout=3)["status"] == "valid"
        assert cli.main(args) == 0
        output = capsys.readouterr()
        assert not output.err
        assert json.loads(output.out)["status"] == "valid"
        assert len(list((runner.path / "submissions").iterdir())) == (1 if same_submission else 2)
    finally:
        if not first.done() and first_id in runner.verdicts:
            runner.verdicts[first_id].put(verdict.copy())
        pool.shutdown()
        runner.pool.shutdown()


@pytest.mark.parametrize(
    "status,body,message",
    [
        (400, b'{"error":"Bad candidate"}', "Bad candidate"),
        (502, b"Bad gateway", "Bad gateway"),
        (503, b"[]", "[]"),
    ],
)
def test_json_http_errors_preserve_status_and_message(
    tmp_path, monkeypatch, capsys, status, body, message
):
    candidate = tmp_path / "candidate.txt"
    candidate.write_bytes(b"candidate")
    monkeypatch.setenv("GTOC_ENDPOINT", "http://controller.invalid")

    def fail(request, **_):
        raise urllib.error.HTTPError(request.full_url, status, "Rejected", {}, io.BytesIO(body))

    monkeypatch.setattr(cli.urllib.request, "urlopen", fail)
    assert cli.main(["evaluate", str(candidate), "--json"]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not output.err
    assert result["error"] == message and result["diagnostics"] == [message]
    assert result["code"] == "http_error" and result["http_status"] == status
    assert result["retryable"] is False


@pytest.mark.parametrize("failure", ["missing_file", "connection", "timeout"])
def test_json_local_failures_are_parseable(tmp_path, monkeypatch, capsys, failure):
    candidate = tmp_path / "candidate.txt"
    if failure != "missing_file":
        candidate.write_bytes(b"candidate")
    monkeypatch.setenv("GTOC_ENDPOINT", "http://controller.invalid")

    def fail(*_, **__):
        if failure == "connection":
            raise urllib.error.URLError("Connection refused")
        if failure == "timeout":
            raise TimeoutError("Timed out")
        pytest.fail("A missing candidate must fail before contacting the controller")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fail)
    assert cli.main(["evaluate", str(candidate), "--json"]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not output.err
    assert result["status"] == "error" and result["score"] is None
    assert result["diagnostics"] == [result["error"]]
    assert result["code"] == "client_error" and result["http_status"] is None
    assert result["retryable"] is False


@pytest.mark.parametrize("status,exit_code", [("valid", 0), ("invalid", 2), ("validator_error", 2)])
def test_json_verdicts_and_exit_codes_are_preserved(
    tmp_path, monkeypatch, capsys, status, exit_code
):
    candidate = tmp_path / "candidate.txt"
    candidate.write_bytes(b"candidate")
    verdict = {"status": status, "score": 0 if status == "valid" else None, "diagnostics": []}
    monkeypatch.setattr(cli, "client", lambda *args: json.dumps(verdict).encode())
    assert cli.main(["evaluate", str(candidate), "--json"]) == exit_code
    output = capsys.readouterr()
    assert json.loads(output.out) == verdict and not output.err


def test_default_errors_still_use_stderr(tmp_path, capsys):
    assert cli.main(["evaluate", str(tmp_path / "missing.txt")]) == 1
    output = capsys.readouterr()
    assert not output.out and output.err.startswith("gtoc: ")


def test_status_json_errors_use_the_same_contract(tmp_path, capsys):
    assert cli.main(["status", str(tmp_path / "missing"), "--json"]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not output.err
    assert result["status"] == "error" and result["code"] == "client_error"
