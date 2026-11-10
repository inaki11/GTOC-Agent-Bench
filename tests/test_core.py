import hashlib
import json
from types import SimpleNamespace

import pytest

from gtoc_bench import ROOT
from gtoc_bench.analysis import export_progress
from gtoc_bench.budget import Budget
from gtoc_bench.config import effective_cpus, load_config, load_key, public_config
from gtoc_bench.gateway import UsageTracker, locked_request, normalize_usage, retry_delay
from gtoc_bench.network import allowed_document
from gtoc_bench.runner import Runner, create_run, normalize_engine_info
from gtoc_bench.storage import RunStore, read_events
from gtoc_bench.validator import check_input, parse_output, verify_materials


def test_imported_assets_are_unchanged():
    materials = verify_materials()
    assert set(materials["files"]) == {
        "statement.md",
        "data/GTOC12_Asteroids_Data.txt",
        "validator/GTOC12_Verify",
        "validator/ScoreData.txt",
    }


def test_container_engine_info_preserves_podman_identity_and_rootless_limits():
    raw = {
        "host": {
            "cpus": 64,
            "memTotal": 1081903529984,
            "arch": "amd64",
            "security": {"rootless": True},
        },
        "version": {"Version": "5.4.2"},
    }
    result = normalize_engine_info(raw)
    assert result["Engine"] == "podman" and result["Rootless"] is True
    assert result["NCPU"] == 64 and result["MemTotal"] == 1081903529984
    assert result["Architecture"] == "amd64" and result["RawInfo"] == raw
    docker = {
        "NCPU": 8,
        "MemTotal": 16 * 1024**3,
        "Architecture": "aarch64",
        "ServerVersion": "29.6.2",
    }
    assert normalize_engine_info(docker)["Engine"] == "docker"
    with pytest.raises(ValueError, match="Unsupported container engine"):
        normalize_engine_info({"host": {}})


@pytest.mark.parametrize("cpus", [33, 32.1, 64])
def test_configuration_cannot_exceed_32_logical_cpus(tmp_path, cpus):
    import yaml

    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"]["cpus"] = cpus
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="cannot exceed 32"):
        load_config(path)


def test_default_cpu_limit_and_invalid_engine_counts():
    assert load_config(ROOT / "configs/codex.yaml")["hardware"]["cpus"] == 32
    for available in (0, -1, True):
        with pytest.raises(ValueError):
            effective_cpus(None, available)


@pytest.mark.parametrize("engine", ["docker", "podman"])
@pytest.mark.parametrize("agent", ["codex", "swe_agent"])
@pytest.mark.parametrize(
    "requested,available,expected",
    [
        (None, 64, 32),
        (32, 64, 32),
        (None, 8, 8),
        (32, 8, 8),
        (8, 64, 8),
        (2.5, 64, 2.5),
        (64, 64, 32),
    ],
)
def test_container_always_enforces_cpu_ceiling(
    tmp_path, monkeypatch, engine, agent, requested, available, expected
):
    config = load_config(ROOT / "configs/codex.yaml")
    # Also cover old launch files and internal callers that bypass load_config.
    config["hardware"]["cpus"] = requested
    config["hardware"]["gpus"] = "none"
    config["agent"]["id"] = agent
    runner = Runner(create_run(config, tmp_path))
    commands = []
    monkeypatch.setattr(
        "gtoc_bench.runner.subprocess.Popen",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "gtoc_bench.runner.threading.Thread", lambda **kwargs: SimpleNamespace(start=lambda: None)
    )
    monkeypatch.setattr(runner, "send", lambda *args, **kwargs: None)
    try:
        runner.start_container(
            "test-image", {"Engine": engine, "NCPU": available, "MemTotal": 64 * 1024**3}
        )
        command = commands[0]
        assert command[command.index("--cpus") + 1] == str(expected)
        mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "--tmpfs"]
        assert {mount.split(":", 1)[0] for mount in mounts} == {
            "/tmp",
            "/problem",
            "/judge-tmp",
            *(["/root"] if agent == "swe_agent" else []),
        }
        assert all(("notmpcopyup" in mount) == (engine == "podman") for mount in mounts)
        metadata = json.loads((runner.path / "container.json").read_text())
        assert metadata["cpu_limit"] == expected and metadata["maximum_cpus"] == 32
        assert runner.status()["cpu_limit"] == expected
        assert runner.config["hardware"]["cpus"] == requested
    finally:
        runner.pool.shutdown()


@pytest.mark.parametrize(
    "agent,executable",
    [("codex", "codex"), ("claude-code", "claude"), ("claude-subscription", None)],
)
def test_preflight_does_not_require_an_unselected_client(monkeypatch, agent, executable):
    import sys

    from gtoc_bench.bridge import Bridge

    config = load_config(ROOT / f"configs/{agent}.yaml")
    version = config["agent"]["version"]

    def available(command, **_):
        if command == [executable, "--version"]:
            return version
        if command[0] == sys.executable:
            return '__GTOC_PROBE__{"cuda_available": false}'
        raise FileNotFoundError("The other client cannot run on this host")

    monkeypatch.setattr("gtoc_bench.bridge.subprocess.check_output", available)
    events = []
    bridge = Bridge()
    # This test isolates executable/version selection; model metadata has its own
    # offline pinned-client integration tests.
    monkeypatch.setattr(bridge, "prepare_codex_model", lambda *args: None)
    monkeypatch.setattr(bridge, "prepare_temporary_dirs", lambda: None)
    monkeypatch.setattr(bridge, "prepare_problem", lambda problem: None)
    monkeypatch.setattr(bridge, "check_judge", lambda problem: None)
    monkeypatch.setattr(bridge, "prepare_agent_caches", lambda: None)
    monkeypatch.setattr(bridge, "send", lambda kind, **values: events.append((kind, values)))
    bridge.prepare(config)
    assert not bridge.stop.is_set()
    assert events[0][0] == "ready"
    assert events[0][1]["versions"] == (
        {executable: version}
        if executable
        else {"claude_execution": "host", "claude_expected_version": version}
    )


def test_native_exit_zero_is_not_success():
    result = parse_output(0, "Error504. Initial mass exceeds limit", "")
    assert result["status"] == "invalid" and result["score"] is None
    assert parse_output(0, "Check successfully!", "")["status"] == "validator_error"
    assert (
        parse_output(0, "Check successfully!\nThe total resource mass is 123.4 kg.\n", "")["score"]
        == 123.4
    )
    assert (
        parse_output(0, "Check successfully!\nThe total resource mass is 1e999 kg.\n", "")["status"]
        == "validator_error"
    )
    assert parse_output(-11, "", "")["status"] == "validator_error"


@pytest.mark.parametrize(
    "data", [b"", b"\xff", b"1 0 64328 nan 0 0 0 0 0 500", b"101 0 64328 0 0 0 0 0 0 500"]
)
def test_rejects_malformed_and_nonfinite_input(data):
    assert check_input(data)


def test_budget_stops_all_work_and_preserves_stop_instant(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("gtoc_bench.budget.time.monotonic", lambda: now[0])
    budget = Budget(20, 100)
    assert not budget.check()
    budget.start()
    now[0] = 105
    budget.add_usage({"input_tokens": 80, "output_tokens": 30, "quality": "reported"})
    assert not budget.check() and budget.reason == "token_limit"
    now[0] = 130
    assert budget.status()["elapsed_seconds"] == 5
    assert budget.status()["token_overshoot"] == 10
    budget.stop("time_limit")
    assert budget.reason == "token_limit"


def test_wall_budget_includes_provider_wait(monkeypatch):
    now = [1.0]
    monkeypatch.setattr("gtoc_bench.budget.time.monotonic", lambda: now[0])
    budget = Budget(2)
    budget.start()
    now[0] = 3.0
    assert not budget.check() and budget.reason == "time_limit"


def test_cache_and_reasoning_are_not_double_counted():
    openai = normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 70},
            "output_tokens_details": {"reasoning_tokens": 15},
        },
        "responses",
        True,
    )
    assert openai["input_tokens"] + openai["output_tokens"] == 120
    anthropic = normalize_usage(
        {
            "input_tokens": 20,
            "cache_read_input_tokens": 60,
            "cache_creation_input_tokens": 40,
            "output_tokens": 10,
        },
        "anthropic",
        True,
    )
    assert anthropic["input_tokens"] == 120 and anthropic["output_tokens"] == 10
    assert normalize_usage({}, "responses", False)["quality"] == "unknown"


def test_fragmented_anthropic_stream_counts_cumulative_usage_once():
    tracker = UsageTracker("anthropic")
    events = [
        {
            "type": "message_start",
            "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 1}},
        },
        {"type": "message_delta", "usage": {"output_tokens": 4}},
        {"type": "message_delta", "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    stream = b"".join(b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events)
    for offset in range(0, len(stream), 7):
        tracker.feed(stream[offset : offset + 7])
    assert tracker.usage()["output_tokens"] == 7
    assert tracker.usage()["quality"] == "reported"


def test_service_and_model_cannot_be_changed_by_agent():
    cfg = load_config(ROOT / "configs/codex.yaml")
    request = {
        "model": cfg["model"]["id"],
        "service_tier": "priority",
        "reasoning": {"effort": "low"},
    }
    result = locked_request(request, cfg)
    assert result["service_tier"] == "default"
    assert result["reasoning"]["effort"] == "high"
    assert request["service_tier"] == "priority"
    with pytest.raises(ValueError, match="declared model"):
        locked_request({"model": "another-model"}, cfg)
    with pytest.raises(ValueError, match="hosted tools"):
        locked_request({"model": cfg["model"]["id"], "tools": [{"type": "web_search"}]}, cfg)
    with pytest.raises(ValueError, match="hosted tools"):
        locked_request(
            {
                "model": cfg["model"]["id"],
                "input": [
                    {
                        "type": "additional_tools",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "remote",
                                "tools": [{"type": "web_search"}],
                            }
                        ],
                    }
                ],
            },
            cfg,
        )


def test_credentials_are_private_and_not_serialized(tmp_path):
    cfg = load_config(ROOT / "configs/codex.yaml")
    env = tmp_path / "provider.env"
    env.write_text('OPENAI_API_KEY="synthetic-test-secret"\n')
    env.chmod(0o600)
    cfg["provider"]["env_file"] = str(env)
    assert load_key(cfg) == "synthetic-test-secret"
    assert "env_file" not in public_config(cfg)["provider"]
    assert "synthetic-test-secret" not in json.dumps(public_config(cfg))
    env.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_key(cfg)


@pytest.mark.parametrize(
    "extra",
    [
        {"background": True},
        {"mcp_servers": [{"url": "https://example.com/remote"}]},
        {
            "input": [
                {
                    "type": "message",
                    "content": [
                        {"type": "input_file", "file_url": "https://example.com/solution.pdf"}
                    ],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "message",
                    "content": [
                        {"type": "input_image", "image_url": "https://example.com/solution.png"}
                    ],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "url", "url": "https://example.com/solution.pdf"},
                        }
                    ],
                }
            ]
        },
    ],
)
def test_provider_cannot_fetch_external_content_or_continue_in_background(extra):
    cfg = load_config(ROOT / "configs/codex.yaml")
    with pytest.raises(ValueError, match="disabled"):
        locked_request({"model": cfg["model"]["id"]} | extra, cfg)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/team/gtoc-solution",
        "http://127.0.0.1/private",
        "https://docs.python.org/3/../secret",
        "https://docs.python.org/3/%2e%2e/secret",
        "https://user:password@docs.python.org/3/",
        "https://esa.github.io/kep3/gtoc12.html",
        "https://docs.python.org/3/?redirect=http://localhost",
    ],
)
def test_documentation_policy_does_not_become_an_open_proxy(url):
    assert not allowed_document(url)


def test_documentation_api_is_allowed():
    assert allowed_document("https://docs.python.org/3/library/math.html")
    assert retry_delay("12", 0) == 12


def test_event_recovery_preserves_committed_records_and_partial_evidence(tmp_path):
    store = RunStore(tmp_path)
    store.event("first")
    with store.events_path.open("ab") as stream:
        stream.write(b'{"sequence":2,"unfinished":')
    restored = RunStore(tmp_path)
    restored.event("second")
    assert [e["type"] for e in read_events(store.events_path)] == ["first", "second"]
    fragments = list((tmp_path / "recovery").glob("*.bin"))
    assert len(fragments) == 1 and fragments[0].read_bytes().endswith(b'"unfinished":')


def test_complete_corruption_is_not_silently_ignored(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"{broken}\n")
    with pytest.raises(json.JSONDecodeError):
        RunStore(tmp_path)


def test_submission_snapshot_idempotency_and_rebuild(tmp_path):
    cfg = load_config(ROOT / "configs/codex.yaml")
    runner = Runner(create_run(cfg, tmp_path))
    runner.budget.start()
    runner.gateway = SimpleNamespace(inflight=0)
    calls = []

    def send(kind, **frame):
        import base64

        calls.append(kind)
        data = base64.b64decode(frame["body"])
        runner.verdicts[frame["id"]].put(
            {
                "status": "valid",
                "score": 0.0,
                "artifact_sha256": hashlib.sha256(data).hexdigest(),
                "stdout": "test",
            }
        )

    runner.send = send
    data = (ROOT / "tests/fixtures/zero_mass.txt").read_bytes()
    submission_id = "a" * 32
    first = runner.accept(data, submission_id)
    assert runner.accept(data, submission_id) == first
    assert calls == ["validate"]
    with pytest.raises(ValueError, match="different bytes"):
        runner.accept(data + b" ", submission_id)
    runner.budget.stop("time_limit")
    with pytest.raises(ValueError, match="no longer active"):
        runner.accept(data)
    from gtoc_bench.submission_artifacts import read_submission

    assert read_submission(runner.path / "submissions" / submission_id) == data
    export_progress(runner.path)
    exported = (runner.path / "exports/progress.csv").read_bytes()
    (runner.path / "exports/progress.csv").unlink()
    export_progress(runner.path)
    assert (runner.path / "exports/progress.csv").read_bytes() == exported
    runner.pool.shutdown()


def test_only_one_pending_validation(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    runner.evaluation_slot.acquire()
    with pytest.raises(ValueError, match="pending"):
        runner.accept(b"candidate")
    runner.evaluation_slot.release()
    runner.pool.shutdown()


def test_a_run_cannot_be_started_twice(tmp_path):
    path = create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path)
    first = Runner(path)
    with pytest.raises(FileExistsError):
        Runner(path)
    first.pool.shutdown()


def test_stale_status_does_not_claim_a_dead_controller_is_running(tmp_path, monkeypatch):
    from gtoc_bench.cli import read_status
    from gtoc_bench.storage import write_json

    write_json(tmp_path / "status.json", {"state": "running", "pid": 12345})

    def gone(*_):
        raise ProcessLookupError

    monkeypatch.setattr("gtoc_bench.cli.os.kill", gone)
    observed = read_status(tmp_path)
    assert observed["state"] == "interrupted" and observed["status_stale"]
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "running"


def test_configuration_identity_includes_resolved_image_and_hardware():
    from gtoc_bench.config import configuration_id

    cfg = load_config(ROOT / "configs/codex.yaml")
    first = configuration_id(cfg, {"image_id": "sha256:one", "cpus": 8})
    assert first == configuration_id(cfg, {"cpus": 8, "image_id": "sha256:one"})
    assert first != configuration_id(cfg, {"image_id": "sha256:two", "cpus": 8})
    assert first != configuration_id(cfg, {"image_id": "sha256:one", "cpus": 16})
