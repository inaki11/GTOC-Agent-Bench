import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fake_provider import FakeProvider
from test_cancellation import StalledProvider

from gtoc_bench import ROOT
from gtoc_bench.auth import ISSUER, PLAN_SCOPE, RESOURCE, TOKEN_URL, Profiles
from gtoc_bench.cli import validate_file
from gtoc_bench.config import load_config, pins
from gtoc_bench.continuation import create_continuation
from gtoc_bench.gateway import Gateway
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE to run Docker integration tests"),
]


def cpu_config(path):
    # These tests cover CPU isolation and protocol behavior, even on an NVIDIA host.
    cfg = load_config(path)
    cfg["hardware"]["gpus"] = "none"
    cfg["recovery"]["clock_policy"] = "continuous_wall"
    return cfg


@pytest.mark.parametrize("after_tool", [False, True])
def test_codex_recovers_partial_stream_without_replaying_tool(tmp_path, after_tool):
    command = "/opt/conda/bin/python -c \"from pathlib import Path; p=Path('/workspace/count.txt'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\""
    provider = FakeProvider(tool_command=command, cut_streams=1, cut_after_tool=after_tool)
    cfg = cpu_config(ROOT / "configs/codex.yaml")
    env = tmp_path / "provider.env"
    env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-recovery-key\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 90, "token_limit": None}
    run = create_run(cfg, tmp_path)
    try:
        Runner(run, prompt="Execute the requested integration command and finish.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert (run / "workspace/count.txt").read_text() == "1"
        assert summary["unknown_usage_requests"] == 1
        events = list(read_events(run / "events.jsonl"))
        assert any(e["type"] == "model_stream_recovered" for e in events)
        assert json.loads((run / "checkpoint.json").read_text())["available"]
    finally:
        provider.close()


@pytest.mark.parametrize("changed_ram", [False, True])
def test_codex_resumes_sealed_conversation_and_preserves_remaining_budget(tmp_path, changed_ram):
    command = "echo persisted-resume-marker > /workspace/marker.txt"
    provider = FakeProvider(tool_command=command, cut_streams=1, cut_after_tool=True)
    cfg = cpu_config(ROOT / "configs/codex.yaml")
    env = tmp_path / "provider.env"
    env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-resume-key\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    cfg["hardware"]["memory_gb"] = 2
    cfg["budget"] = {"wall_seconds": 90, "token_limit": 10000}
    cfg["recovery"]["max_transient_retries"] = 0
    parent = create_run(cfg, tmp_path)
    try:
        Runner(
            parent,
            prompt="Keep this unique context: resume-history-marker-619. Execute the tool and finish.",
        ).run()
        before = json.loads((parent / "summary.json").read_text())
        assert before["state"] == "interrupted", before
        assert json.loads((parent / "checkpoint.json").read_text())["available"]
        seal = (parent / "integrity.json").read_bytes()
        cfg["recovery"]["max_transient_retries"] = 2
        resumed = create_continuation(parent, cfg, tmp_path, memory_gb=3 if changed_ram else None)
        cutoff = len(provider.calls)
        Runner(resumed).run()
        summary = json.loads((resumed / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert summary["wall_seconds"] == before["remaining_seconds"]
        assert summary["token_limit"] == before["remaining_tokens"]
        assert (
            summary["cumulative_known_tokens"] == before["known_tokens"] + summary["known_tokens"]
        )
        assert summary["cumulative_unknown_usage_requests"] >= 1
        assert summary["parent_run_id"] == parent.name
        assert summary["memory_limit_bytes"] == (3 if changed_ram else 2) * 1024**3
        assert "resume-history-marker-619" in json.dumps(provider.calls[cutoff]["body"]["input"])
        assert (parent / "integrity.json").read_bytes() == seal
        assert (
            json.loads((resumed / "checkpoint.json").read_text())["session_id"]
            == json.loads((parent / "checkpoint.json").read_text())["session_id"]
        )
    finally:
        provider.close()


def test_real_native_validator_valid_invalid_and_deterministic():
    first = validate_file(ROOT / "tests/fixtures/zero_mass.txt", IMAGE)
    second = validate_file(ROOT / "tests/fixtures/zero_mass.txt", IMAGE)
    assert first["status"] == second["status"] == "valid"
    assert first["score"] == second["score"] == 0
    assert first["artifact_sha256"] == second["artifact_sha256"]
    invalid = validate_file(ROOT / "tests/fixtures/invalid_mass.txt", IMAGE)
    assert invalid["status"] == "invalid"
    assert invalid["returncode"] == 0


@pytest.mark.parametrize("count", [2, 3])
def test_two_declared_cpu_limited_evaluations_can_coexist(tmp_path, count):
    providers = [FakeProvider(block=True) for _ in range(count)]
    runs, workers = [], []
    try:
        for index, provider in enumerate(providers):
            cfg = cpu_config(ROOT / "configs/codex.yaml")
            env = tmp_path / f"provider-{index}.env"
            env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-concurrency-key\n")
            env.chmod(0o600)
            cfg["provider"].update(base_url=provider.url, env_file=str(env))
            cfg["environment"]["image"] = IMAGE
            cfg["hardware"]["cpus"] = 2
            cfg["hardware"].update(workspace_gb=4, memory_gb=2)
            cfg["budget"] = {"wall_seconds": 90, "token_limit": None}
            if runs:
                cfg["hardware"]["concurrent_with"] = [r.name for r in runs]
            run = create_run(cfg, tmp_path / "results")
            runs.append(run)
            with (run / "controller.log").open("ab") as log:
                workers.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "gtoc_bench", "_worker", str(run)],
                        stdout=log,
                        stderr=log,
                    )
                )
            assert provider.arrived.wait(60), (run / "status.json").read_text()
        assert all(worker.poll() is None for worker in workers)
        sharing = json.loads((runs[-1] / "host-sharing.json").read_text())
        assert sharing["cpu_limit"] == 2 and sharing["peer_cpu_limit"] == 2 * (count - 1)
        assert sharing["expected_peer_run_ids"] == [r.name for r in runs[:-1]]
        storage = json.loads((runs[-1] / "storage-preflight.json").read_text())
        assert storage["admitted"] and storage["peer_reserved_bytes"] == (count - 1) * 6 * 1024**3
        for provider in providers:
            provider.release.set()
        for worker, run in zip(workers, runs):
            worker.wait(timeout=30)
            assert json.loads((run / "summary.json").read_text())["stop_reason"] == "agent_finished"
    finally:
        for provider in providers:
            provider.release.set()
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=30)
        for provider in providers:
            provider.close()


def test_installed_science_tools_solve_and_compile_without_network():
    script = r"""
import json, pathlib, subprocess
import numpy as np
import casadi as ca
import cyipopt
import numba
import pybind11
from gtoc_bench.hardware import tools_inventory

result=cyipopt.minimize_ipopt(lambda x: (x[0]-2)**2, np.array([0.]),
    jac=lambda x: np.array([2*(x[0]-2)]), options={'print_level':0,'sb':'yes'})
assert result.success and abs(result.x[0]-2)<1e-5, result
x=ca.MX.sym('x')
solver=ca.nlpsol('quadratic','ipopt',{'x':x,'f':(x-2)**2},
    {'ipopt.print_level':0,'ipopt.sb':'yes','print_time':False})
assert abs(float(solver(x0=0)['x'])-2)<1e-5

@numba.njit
def total(n):
    s=0
    for i in range(n):s+=i
    return s
assert total(10)==45
assert pathlib.Path(pybind11.get_include()).is_dir()
pathlib.Path('/tmp/test.cpp').write_text('int main() { return 0; }')
subprocess.run(['g++','/tmp/test.cpp','-o','/tmp/test-program'],check=True)
subprocess.run(['/tmp/test-program'],check=True)
catalog=tools_inventory()
assert catalog['packages']['cyipopt']['version']=='1.7.0'
assert catalog['packages']['casadi']['version']=='3.8.2'
assert catalog['commands']['pkg-config']['path']
print(json.dumps({'cyipopt':'solved','casadi':'solved','numba':'compiled','C++':'compiled',
                 'inventory':catalog}))
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "3g",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
            "-e",
            "OMP_NUM_THREADS=1",
            "-e",
            "OPENBLAS_NUM_THREADS=1",
            "-e",
            "NUMBA_NUM_THREADS=1",
            "-e",
            "KMP_AFFINITY=disabled",
            "--entrypoint",
            "/opt/conda/bin/python",
            "-i",
            IMAGE,
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"casadi": "solved"' in result.stdout


@pytest.mark.parametrize("agent", ["codex", "claude_code", "antigravity"])
def test_wall_limit_disconnects_provider_before_finalization(tmp_path, agent):
    provider = StalledProvider("headers")
    cfg = cpu_config(
        ROOT
        / "configs"
        / (
            "antigravity-openai.yaml"
            if agent == "antigravity"
            else agent.replace("_", "-") + ".yaml"
        )
    )
    env = tmp_path / "provider.env"
    env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-cancel-test\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    # ARM hosts run the scientific/agent Python image under emulation. Give
    # startup enough time while keeping the same cutoff accuracy assertion.
    timeout_scale = float(os.environ.get("GTOC_TEST_TIMEOUT_SCALE", "1"))
    wall_seconds = 8 * timeout_scale
    cfg["budget"] = {"wall_seconds": wall_seconds, "token_limit": None}
    run = create_run(cfg, tmp_path / "results")
    worker = subprocess.Popen(
        [sys.executable, "-m", "gtoc_bench", "_worker", str(run)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        startup_deadline = time.monotonic() + 90 * timeout_scale
        while not provider.arrived.wait(0.1):
            if worker.poll() is not None or time.monotonic() >= startup_deadline:
                break
        assert provider.arrived.is_set(), (run / "status.json").read_text()
        assert provider.disconnected.wait(wall_seconds + 12), (
            "Provider stayed connected after the deadline"
        )
        worker.wait(timeout=12 * timeout_scale)
        summary = json.loads((run / "summary.json").read_text())
        assert worker.returncode == 0 and summary["stop_reason"] == "time_limit", summary
        events = list(read_events(run / "events.jsonl"))
        closed = next(e for e in events if e["type"] == "model_transport_closed")
        assert closed["elapsed_seconds"] < wall_seconds + 1.5
        assert closed["sequence"] < events[-1]["sequence"]
        assert events[-1]["type"] == "run_finished"
        assert summary["unknown_usage_requests"] >= 1
        assert not closed["upstream_cancellation_confirmed"]
        assert provider.calls == 1
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=20)
        worker.stderr.close()
        provider.close()
        subprocess.run(
            ["docker", "rm", "-f", "gtoc-" + run.name.lower()], capture_output=True, timeout=10
        )


@pytest.mark.parametrize("agent", ["codex", "claude_code", "antigravity"])
def test_native_agent_gateway_and_soft_token_cutoff(tmp_path, agent):
    provider = FakeProvider(block=True)
    cfg = cpu_config(
        ROOT
        / "configs"
        / (
            "antigravity-openai.yaml"
            if agent == "antigravity"
            else agent.replace("_", "-") + ".yaml"
        )
    )
    env = tmp_path / "provider.env"
    secret = "synthetic-provider-key-not-for-container"
    env.write_text(cfg["provider"]["api_key_variable"] + "=" + secret + "\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 90, "token_limit": 50}
    run = create_run(cfg, tmp_path / "results")
    log = (tmp_path / "worker.log").open("wb")
    worker = subprocess.Popen(
        [sys.executable, "-m", "gtoc_bench", "_worker", str(run)], stdout=log, stderr=log
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not provider.arrived.wait(0.2):
            if worker.poll() is not None:
                pytest.fail(
                    (run / "summary.json").read_text() + (tmp_path / "worker.log").read_text()
                )
        assert provider.arrived.is_set(), (run / "status.json").read_text()
        container = "gtoc-" + run.name.lower()
        candidate = (ROOT / "tests/fixtures/zero_mass.txt").read_text()
        script = """
import json, os, pathlib, socket, urllib.request, urllib.error
assert os.getuid() == 1000
assert "synthetic-provider-key-not-for-container" not in str(os.environ)
capabilities = [x for x in pathlib.Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')][0]
assert int(capabilities.split()[1],16) == 0
assert not pathlib.Path('/var/run/docker.sock').exists()
try:
    pathlib.Path('/opt/gtoc/problems/gtoc12/validator/ScoreData.txt').write_text('tamper')
    raise AssertionError('validator was writable')
except (PermissionError, OSError): pass
try:
    pathlib.Path('/proc/1/fd/0').open('wb')
    raise AssertionError('controller channel exposed')
except (PermissionError, OSError): pass
sock=socket.socket(); sock.settimeout(0.5)
try:
    sock.connect(('1.1.1.1',443)); raise AssertionError('direct network allowed')
except OSError: pass
finally: sock.close()
req=urllib.request.Request('http://127.0.0.1:8765/evaluate', data=CANDIDATE.encode(), headers={'Content-Type':'application/octet-stream'})
with urllib.request.urlopen(req,timeout=45) as r: result=json.load(r)
assert result['status']=='valid' and result['score']==0
with urllib.request.urlopen('http://127.0.0.1:8765/status') as r: status=json.load(r)
assert status['remaining_seconds'] > 0
print(json.dumps(result))
""".replace("CANDIDATE", repr(candidate))
        probe = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "--user",
                "1000:1000",
                container,
                "/opt/conda/bin/python",
                "-",
            ],
            input=script,
            capture_output=True,
            text=True,
            timeout=55,
        )
        assert probe.returncode == 0, probe.stderr
        provider.release.set()
        worker.wait(timeout=80)
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "token_limit", summary
        assert summary["known_tokens"] >= 110 and summary["token_overshoot"] >= 60
        assert summary["best_score"] == 0
        assert len(list((run / "submissions").glob("*/solution.txt.gz"))) == 1
        assert secret not in (run / "manifest.json").read_text()
        assert secret not in (run / "events.jsonl").read_text()
        assert (run / "exports/progress.csv").exists()
        assert list(read_events(run / "events.jsonl"))[-1]["type"] == "run_finished"
        for call in provider.calls:
            if not call["path"].endswith("count_tokens"):
                expected = "standard_only" if agent == "claude_code" else "default"
                assert call["body"]["service_tier"] == expected
        removed = subprocess.run(["docker", "inspect", container], capture_output=True)
        assert removed.returncode != 0
    finally:
        provider.release.set()
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=80)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        provider.close()
        log.close()


@pytest.mark.parametrize("mode", ["wall_limit", "controller_failure"])
def test_detached_lifecycle(tmp_path, mode):
    provider = FakeProvider(block=True)
    cfg = cpu_config(ROOT / "configs/codex.yaml")
    env = tmp_path / "provider.env"
    env.write_text("OPENAI_API_KEY=synthetic-lifecycle-key\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 12 if mode == "wall_limit" else 90, "token_limit": None}
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    launched = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtoc_bench",
            "run",
            str(config_path),
            "--results",
            str(tmp_path / "results"),
            "--detach",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert launched.returncode == 0, launched.stderr
    details = json.loads(launched.stdout)
    run = Path(details["directory"])
    container = "gtoc-" + run.name.lower()
    pid = details["pid"]
    try:
        assert provider.arrived.wait(90), (run / "status.json").read_text()
        # The launcher has exited, but the controller and model request are alive.
        assert launched.returncode == 0
        sleeper = subprocess.run(
            [
                "docker",
                "exec",
                "-d",
                "--user",
                "1000:1000",
                container,
                "/opt/conda/bin/python",
                "-c",
                "import time; time.sleep(120)",
            ],
            capture_output=True,
            timeout=10,
        )
        assert sleeper.returncode == 0, sleeper.stderr
        if mode == "controller_failure":
            os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 20
            running = True
            while time.monotonic() < deadline:
                state = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Running}}", container],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                running = state.returncode == 0 and state.stdout.strip() == "true"
                if not running:
                    break
                time.sleep(0.2)
            assert not running, "Controller loss left the container running"
            assert (run / "events.jsonl").exists()
        else:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = json.loads((run / "status.json").read_text())
                if status.get("stop_reason") == "time_limit":
                    break
                time.sleep(0.2)
            assert status["stop_reason"] == "time_limit", status
            # Cancellation may finish cleanup before this probe; either the agent's
            # processes or the whole container must already have stopped.
            check = "from pathlib import Path; print([p.name for p in Path('/proc').iterdir() if p.name.isdigit() and p.stat().st_uid==1000 and '\\nState:\\tZ' not in (p/'status').read_text()])"
            deadline = time.monotonic() + 3
            stopped = False
            while time.monotonic() < deadline:
                processes = subprocess.run(
                    ["docker", "exec", container, "/opt/conda/bin/python", "-c", check],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if processes.returncode == 0 and processes.stdout.strip() == "[]":
                    stopped = True
                    break
                state = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "-a",
                        "--filter",
                        f"name=^/{container}$",
                        "--format",
                        "{{.State}}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                assert state.returncode == 0, state.stderr
                if state.stdout.strip() in {"", "exited", "dead"}:
                    stopped = True
                    break
                time.sleep(0.1)
            assert stopped, processes.stderr + processes.stdout
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not (run / "summary.json").exists():
                time.sleep(0.2)
            assert json.loads((run / "summary.json").read_text())["stop_reason"] == "time_limit"
    finally:
        provider.release.set()
        if not (run / "summary.json").exists() and mode != "controller_failure":
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        provider.close()
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)


@pytest.mark.parametrize(
    "agent,protocol,model",
    [
        (a, None, None)
        for a in ["codex", "claude_code", "openhands", "swe_agent", "mini_swe_agent", "antigravity"]
    ]
    + [(a, "anthropic", "claude-sonnet-4-6") for a in ["openhands", "swe_agent", "mini_swe_agent"]]
    + [("openhands", "chat_completions", "o3")],
)
def test_native_tool_execution_and_validation_feedback(tmp_path, agent, protocol, model):
    candidate = (ROOT / "tests/fixtures/zero_mass.txt").read_text()
    error_probe = (
        "import json, subprocess; "
        "p=subprocess.run(['gtoc', 'evaluate', '/workspace/missing.txt', '--json'], "
        "capture_output=True, text=True); "
        "assert p.returncode == 1 and not p.stderr, (p.stdout, p.stderr); "
        "assert json.loads(p.stdout)['status'] == 'error'"
    )
    script = (
        "from pathlib import Path; Path('/workspace/solver.py').write_text('print(1)'); "
        "Path('/workspace/solution.txt').write_text(" + repr(candidate) + ")"
    )
    command = (
        "python -c "
        + shlex.quote(error_probe)
        + " && python -c "
        + shlex.quote(script)
        + " && gtoc tools && gtoc packages && gtoc status --json && gtoc evaluate /workspace/solution.txt --json"
        + " && python -c \"from pathlib import Path; Path('/workspace/solver.py').write_text('print(2)')\""
    )
    provider = FakeProvider(tool_command=command)
    cfg = cpu_config(ROOT / "configs" / (agent.replace("_", "-") + ".yaml"))
    if agent == "antigravity":
        cfg["provider"] = {
            "id": "test",
            "protocol": "chat_completions",
            "auth_method": "api_key",
            "api_key_variable": "TEST_API_KEY",
            "base_url": provider.url,
        }
    if protocol:
        cfg["provider"]["protocol"] = protocol
        cfg["model"]["id"] = model
    env = tmp_path / "provider.env"
    env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-tool-test\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 180, "token_limit": 10000}
    run = create_run(cfg, tmp_path / "results")
    try:
        worker = subprocess.run(
            [sys.executable, "-m", "gtoc_bench", "_worker", str(run)],
            capture_output=True,
            text=True,
            timeout=240,
        )
        summary = json.loads((run / "summary.json").read_text())
        assert worker.returncode == 0, summary
        assert summary["stop_reason"] == "agent_finished", summary
        assigned = json.loads((run / "hardware.json").read_text())["assigned"]["cpus"]
        assert 0 < assigned <= 32 and summary["cpu_limit"] == assigned
        container = json.loads((run / "container.json").read_text())
        assert container["command"][container["command"].index("--cpus") + 1] == str(assigned)
        quota, period = json.loads((run / "preflight.json").read_text())["science"][
            "cpu_quota"
        ].split()
        assert quota != "max" and int(quota) / int(period) <= assigned + 0.00001
        (tmp_path / "provider-calls.json").write_text(json.dumps(provider.calls, indent=2))
        assert provider.tool_sent, [
            {
                key: value
                for key, value in call["body"].items()
                if key not in {"input", "instructions", "messages", "system"}
            }
            for call in provider.calls
        ]
        assert summary["best_score"] == 0, (run / "agent/stdout.log").read_text()
        events = list(read_events(run / "events.jsonl"))
        snapshots = [e for e in events if e["type"] == "workspace_snapshot"]
        assert {e["reason"] for e in snapshots} >= {"initial", "final"}
        assert "submission" in {e["reason"] for e in snapshots} or any(
            e["type"] == "observation_skipped" and e.get("requested_reason") == "submission"
            for e in events
        )
        submission = next(e for e in events if e["type"] == "submission_evaluated")
        assert (run / submission["verdict"]["snapshot_ref"]).exists()
        captured = {p.read_text() for p in (run / "snapshots/blobs").iterdir()}
        # A post-verdict observation can see the agent's subsequent edit already.
        assert "print(2)" in captured
        from gtoc_bench.submission_artifacts import read_submission

        assert (
            read_submission(run / "submissions" / submission["submission_id"]).decode() == candidate
        )
        tools = json.loads((run / "preflight.json").read_text())["science"]["tools"]
        assert tools["packages"]["cyipopt"]["version"] == "1.7.0"
        assert (run / "resources.jsonl").stat().st_size > 0
        assert (run / "exports/audit.html").exists()
        assert "solver.py" in json.loads((run / "workspace-manifest.json").read_text())["files"]
        assert (run / "container-state.json").exists()
        if agent in {"codex", "claude_code"}:
            assert (run / "agent/transcript.jsonl").stat().st_size > 0
        for path in (run / "requests").glob("*/metadata.json"):
            from gtoc_bench.request_artifacts import read_request

            metadata = json.loads(path.read_text())
            assert (run / metadata["request_artifacts"]["client"]["path"]).exists()
            assert (run / metadata["request_artifacts"]["provider"]["path"]).exists()
            assert read_request(path.parent)["model"] == cfg["model"]["id"]
            assert read_request(path.parent, client=True)["model"]
            assert "response_trace" in metadata
            assert (run / metadata["response_trace"]["path"]).exists()
        prompt = (run / "prompt.txt").read_text()
        assert f"Container CPU limit: {assigned}" in prompt
        assert "Solve the problem described in `/problem/statement.md`" in prompt
        assert "`/opt/conda/bin/python`" in prompt
        assert "/opt/conda/bin/python -m pip install --user PACKAGE" in prompt
        assert '"wall_seconds": 180' in prompt and '"token_limit": 10000' in prompt
        integrity = json.loads((run / "integrity.json").read_text())["files"]
        assert "resources.jsonl" in integrity and "workspace-manifest.json" in integrity

        def strings(value):
            if isinstance(value, str):
                yield value
                if value.startswith(("{", "[")):
                    try:
                        yield from strings(json.loads(value))
                    except json.JSONDecodeError:
                        pass
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)

        feedback = "\n".join(strings([call["body"] for call in provider.calls[1:]]))
        assert '"status": "valid"' in feedback
        assert '"remaining_seconds"' in feedback
        assert summary["known_tokens"] >= 220
    finally:
        provider.close()
        subprocess.run(
            ["docker", "rm", "-f", "gtoc-" + run.name.lower()], capture_output=True, timeout=10
        )


@pytest.mark.parametrize(
    "agent", ["codex", "claude_code", "openhands", "swe_agent", "mini_swe_agent", "antigravity"]
)
def test_native_agents_with_host_oauth_rotation(tmp_path, monkeypatch, agent):
    profiles = Profiles(tmp_path / "private-auth")
    profiles.write(
        {
            "issuer": ISSUER,
            "subject": "synthetic-account",
            "client_id": "oaiapp_test",
            "session_id": "synthetic-session",
            "access_token": "oauth-access-before-rotation",
            "refresh_token": "oauth-refresh-before-rotation",
            "id_token": "oauth-id-token",
            "expires_at": time.time() + 3600,
            "scopes": [PLAN_SCOPE, "resource.invoke"],
        }
    )
    candidate = (ROOT / "tests/fixtures/zero_mass.txt").read_text()
    script = (
        "import os; from pathlib import Path; "
        "assert os.getuid() == 1000; assert os.environ.get('OPENAI_API_KEY') in (None, 'run-scoped-local-only'); "
        "assert os.environ['GTOC_RUN_TOKEN'] == 'run-scoped-local-only'; "
        "assert not Path('/workspace/.codex/auth.json').exists(); "
        "Path('/workspace/private').mkdir(mode=0o700); "
        "Path('/workspace/private/state').write_text('private artifact'); "
        "Path('/workspace/private/state').chmod(0o600); "
        f"Path('/workspace/solution.txt').write_text({candidate!r})"
    )
    command = (
        "python -c "
        + shlex.quote(script)
        + " && gtoc status --json && gtoc evaluate /workspace/solution.txt --json"
    )
    provider = FakeProvider(tool_command=command)
    authorization, refreshed = [], []

    def endpoint(request):
        if str(request.url) == TOKEN_URL:
            refreshed.append(request)
            assert b"oauth-refresh-before-rotation" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "oauth-access-after-rotation",
                    "refresh_token": "oauth-refresh-after-rotation",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if str(request.url) == RESOURCE + "/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "gpt-6.1-sol",
                            "visibility": "list",
                            "supported_reasoning_levels": [{"effort": "high"}],
                        }
                    ]
                },
            )
        assert str(request.url) == RESOURCE + "/responses"
        authorization.append(request.headers["authorization"])
        body = json.loads(request.content)
        assert body["stream"] is True and body["store"] is False
        assert "max_output_tokens" not in body and body["service_tier"] == "default"
        assert body["reasoning"]["effort"] == "high"
        if len(authorization) == 1:
            with profiles.lock():
                profiles.write(profiles.read() | {"expires_at": 1})
        with httpx.Client(trust_env=False, timeout=30) as local:
            response = local.post(provider.url + "/responses", json=body)
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

    def connection():
        return httpx.Client(transport=httpx.MockTransport(endpoint))

    def gateway_factory(*args):
        gateway = Gateway(*args, transport=httpx.MockTransport(endpoint))
        gateway.client.close()
        gateway.client = connection()
        return gateway

    monkeypatch.setattr("gtoc_bench.auth.http_client", connection)
    monkeypatch.setattr("gtoc_bench.runner.Gateway", gateway_factory)
    cfg = cpu_config(ROOT / "configs/codex-chatgpt.yaml")
    cfg["agent"] = {"id": agent, "version": pins()[agent]["version"]}
    cfg["provider"]["protocol"] = (
        "responses"
        if agent == "codex"
        else "anthropic"
        if agent == "claude_code"
        else "chat_completions"
    )
    cfg["provider"]["auth_dir"] = str(profiles.directory)
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 60, "token_limit": 10000}
    run = create_run(cfg, tmp_path / "results")
    old_mask = os.umask(0o077)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        assert Runner(run).run() == 0, (run / "summary.json").read_text()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["best_score"] == 0 and summary["stop_reason"] == "agent_finished"
        assert summary["known_tokens"] >= 220 and len(refreshed) == 1
        assert (run / "workspace").stat().st_uid == os.getuid()
        assert (run / "workspace/private/state").stat().st_uid == os.getuid()
        assert (run / "workspace/private/state").read_text() == "private artifact"
        assert authorization == [
            "Bearer oauth-access-before-rotation",
            "Bearer oauth-access-after-rotation",
        ]
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["authentication"]["auth_method"] == "chatgpt"
        assert manifest["authentication"]["actual_cost"] is None
        assert manifest["resolved_conditions"]["authentication"] == manifest["authentication"]
        assert len(list((run / "submissions").glob("*/solution.txt.gz"))) == 1
        for file in run.rglob("*"):
            if file.is_file() and file.suffix in {".json", ".jsonl", ".log"}:
                content = file.read_text(errors="replace")
                assert not any(
                    secret in content
                    for secret in (
                        "oauth-access-before-rotation",
                        "oauth-access-after-rotation",
                        "oauth-refresh-before-rotation",
                        "oauth-refresh-after-rotation",
                        "oauth-id-token",
                    )
                ), file
    finally:
        os.umask(old_mask)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        provider.close()
        subprocess.run(
            ["docker", "rm", "-f", "gtoc-" + run.name.lower()], capture_output=True, timeout=10
        )


@pytest.mark.parametrize("token_limit,provider_error", [(10000, None), (50, None), (10000, 429)])
@pytest.mark.parametrize("workspace_recovery", [False, True])
def test_official_host_claude_with_container_mcp(
    tmp_path, monkeypatch, token_limit, provider_error, workspace_recovery
):
    from gtoc_bench import claude_native

    if not claude_native.binary_path().exists():
        pytest.skip("Install the pinned client with gtoc install-agent claude_code")
    candidate = (ROOT / "tests/fixtures/zero_mass.txt").read_text()
    sentinel = tmp_path / "host-only-sentinel"
    sentinel.write_text("This file must never reach the agent's shell")
    (tmp_path / "CLAUDE.md").write_text("HOST_INSTRUCTIONS_MUST_NOT_REACH_THE_BENCH_MODEL")
    script = (
        "import os; from pathlib import Path; assert os.getuid()==1000; "
        "assert not any(k in os.environ for k in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN')); "
        f"assert not Path({str(sentinel)!r}).exists(); "
        f"Path('/workspace/solution.txt').write_text({candidate!r})"
    )
    if workspace_recovery:
        script += "; assert Path('/workspace/checkpoint-marker.txt').read_text() == 'saved state'"
    provider = FakeProvider(
        tool_command="python -c "
        + shlex.quote(script)
        + " && gtoc status --json && gtoc evaluate /workspace/solution.txt --json",
        error_status=provider_error,
    )
    real_environment = claude_native.profile_environment

    def synthetic_environment(*args):
        base, env = real_environment(*args)
        # Exercise the exact official host CLI/MCP path without signing in or spending quota.
        return base, env | {
            "ANTHROPIC_API_KEY": "synthetic-native-key",
            "ANTHROPIC_BASE_URL": provider.url.removesuffix("/v1"),
        }

    monkeypatch.setattr(claude_native, "profile_environment", synthetic_environment)
    monkeypatch.setattr(
        claude_native,
        "subscription_status",
        lambda env: {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "email": "synthetic@example.invalid",
        },
    )
    cfg = cpu_config(ROOT / "configs/claude-subscription.yaml")
    cfg["provider"]["auth_dir"] = str(tmp_path / "private-auth")
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 90, "token_limit": token_limit}
    if provider_error:
        cfg["recovery"].update(max_transient_retries=0, max_retry_wait_seconds=3)
    if workspace_recovery:
        from gtoc_bench.config import public_config
        from gtoc_bench.observation import workspace_inventory
        from gtoc_bench.storage import RunStore
        from gtoc_bench.validator import verify_materials

        parent = create_run(cfg, tmp_path / "parents")
        store = RunStore(parent)
        store.write(
            "manifest.json",
            {
                "config": public_config(cfg),
                "configuration_id": "test",
                "problem": verify_materials(),
            },
        )
        store.write(
            "summary.json",
            {
                "run_id": parent.name,
                "state": "interrupted",
                "best_score": 0,
                "elapsed_seconds": 10,
                "remaining_seconds": 80,
                "known_tokens": 0,
                "unknown_usage_requests": 0,
                "remaining_tokens": token_limit,
            },
        )
        store.append("agent/transcript.jsonl", "agent_activity", native_event={"type": "system"})
        (parent / "workspace/checkpoint-marker.txt").write_text("saved state")
        store.write("workspace-manifest.json", workspace_inventory(parent / "workspace"))
        store.integrity()
        run = create_continuation(parent, cfg, tmp_path / "results", workspace_only=True)
    else:
        run = create_run(cfg, tmp_path / "results")
    old_mask = os.umask(0o077)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        code = Runner(run).run()
        assert code == (1 if provider_error else 0), (run / "summary.json").read_text()
        summary = json.loads((run / "summary.json").read_text())
        if workspace_recovery:
            assert summary["wall_seconds"] == 80
            assert summary["cumulative_elapsed_seconds"] >= 10
            prompt = (run / "prompt.txt").read_text()
            assert "NEW agent conversation" in prompt
            assert "not saved and cannot be resumed exactly" in prompt
        if provider_error:
            assert summary["stop_reason"] == "provider_unavailable", summary
            assert summary["elapsed_seconds"] < 15
            assert summary["unknown_usage_requests"] > 0
            return
        assert summary["stop_reason"] == (
            "agent_finished" if token_limit == 10000 else "token_limit"
        ), summary
        assert summary["known_tokens"] >= 100
        if token_limit == 10000:
            assert summary["best_score"] == 0
            assert summary["known_tokens"] == 220
            from gtoc_bench.audit import read_records

            calls = list(read_records(run / "native-client/tools.jsonl"))
            assert [call["type"] for call in calls] == ["tool_started", "tool_finished"]
            assert calls[0]["call_id"] == calls[1]["call_id"]
            assert calls[1]["result"]["returncode"] == 0
            assert calls[0]["source"] == "trusted_mcp_server"
            assert (run / "agent/transcript.jsonl").exists()
            integrity = json.loads((run / "integrity.json").read_text())["files"]
            assert "native-client/tools.jsonl" in integrity
            receipt = json.loads(next((run / "submissions").glob("*/receipt.json")).read_text())
            assert receipt["usage_pending"] is False
            assert receipt["pending_usage_responses"] == 0
            assert summary["native_usage"]["pending_responses"] == 0
            assert summary["native_usage"]["unattributed_output_tokens"] == 0
            from gtoc_bench.storage import read_events

            observations = [
                e["usage"]
                for e in read_events(run / "events.jsonl")
                if e["type"] == "model_request_finished"
            ]
            assert any(
                u.get("observation") == "message_stop" and u["quality"] == "reported"
                for u in observations
            )
            assert "native-usage.json" in integrity
        else:
            assert summary["unknown_usage_requests"] > 0
        assert provider.tool_sent
        for call in provider.calls:
            assert "HOST_INSTRUCTIONS_MUST_NOT_REACH_THE_BENCH_MODEL" not in json.dumps(
                call["body"]
            )
            for tool in call["body"].get("tools", []):
                assert tool["name"] in {"mcp__gtoc__exec", "EndConversation"}, tool
        assert "synthetic-native-key" not in (run / "events.jsonl").read_text()
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["authentication"]["execution_mode"] == "official_cli_container_tools"
        assert manifest["authentication"]["actual_cost"] is None
    finally:
        os.umask(old_mask)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        provider.close()
        subprocess.run(
            ["docker", "rm", "-f", "gtoc-" + run.name.lower()], capture_output=True, timeout=10
        )


def test_three_official_claude_models_share_profile_with_isolated_gtoc13_tools(
    tmp_path, monkeypatch
):
    from gtoc_bench import claude_native

    if not claude_native.binary_path().exists():
        pytest.skip("Install the pinned official client")
    candidate = (ROOT / "tests/fixtures/gtoc13/planetx-conic.txt").read_text()
    script = (
        "import os; from pathlib import Path; assert os.getuid()==1000; "
        "assert os.environ['GTOC_PROBLEM']=='gtoc13'; "
        "assert len(list(Path('/problem/data').glob('*.csv')))==3; "
        "assert not any(k in os.environ for k in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN')); "
        f"Path('/workspace/solution.txt').write_text({candidate!r})"
    )
    providers = [
        FakeProvider(
            block=True,
            tool_command="/opt/conda/bin/python -c "
            + shlex.quote(script)
            + " && gtoc evaluate /workspace/solution.txt --json",
        )
        for _ in range(3)
    ]
    local = threading.local()
    real_environment = claude_native.profile_environment

    def synthetic_environment(*args):
        base, env = real_environment(*args)
        return base, env | {
            "ANTHROPIC_API_KEY": "synthetic-native-parallel-key",
            "ANTHROPIC_BASE_URL": local.provider.url.removesuffix("/v1"),
        }

    monkeypatch.setattr(claude_native, "profile_environment", synthetic_environment)
    monkeypatch.setattr(
        claude_native,
        "subscription_status",
        lambda env: {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "email": "synthetic@example.invalid",
        },
    )
    models = ("claude-fable-5-1", "claude-sonnet-5-5", "claude-opus-5-5")
    runs, runners, workers = [], [], []
    outcomes = {}

    def run_worker(index, runner):
        local.provider = providers[index]
        outcomes[index] = runner.run()

    try:
        for index, (model, provider) in enumerate(zip(models, providers)):
            cfg = cpu_config(ROOT / "configs/claude-subscription.yaml")
            cfg["provider"]["auth_dir"] = str(tmp_path / "shared-private-profile")
            cfg["model"].update(id=model, reasoning="max", max_output_tokens=128000)
            cfg["problem"] = "gtoc13"
            cfg["environment"]["image"] = IMAGE
            cfg["hardware"].update(cpus=2, memory_gb=2, workspace_gb=0.25)
            cfg["storage"].update(audit_gb=0.1, minimum_free_gb=0.1)
            cfg["budget"] = {"wall_seconds": 150, "token_limit": None}
            if runs:
                cfg["hardware"]["concurrent_with"] = [r.name for r in runs]
            run = create_run(cfg, tmp_path / "results")
            runner = Runner(run, prompt="Execute the requested integration tool and finish.")
            worker = threading.Thread(target=run_worker, args=(index, runner))
            runs.append(run)
            runners.append(runner)
            workers.append(worker)
            worker.start()
            assert provider.arrived.wait(60), (run / "events.jsonl").read_text()
        # All three official clients are awaiting their synthetic provider at once.
        assert all(worker.is_alive() for worker in workers)
        assert len({runner.native.env["CLAUDE_CONFIG_DIR"] for runner in runners}) == 1
        for provider in providers:
            provider.release.set()
        for worker in workers:
            worker.join(60)
            assert not worker.is_alive()
        for index, run in enumerate(runs):
            summary = json.loads((run / "summary.json").read_text())
            assert outcomes[index] == 0 and summary["stop_reason"] == "agent_finished", summary
            assert summary["best_score"] == pytest.approx(21.92704582292371)
            assert (run / "workspace/solution.txt").read_text() == candidate
            assert (
                json.loads((run / "manifest.json").read_text())["config"]["model"]["id"]
                == models[index]
            )
            assert providers[index].tool_sent
            assert "synthetic-native-parallel-key" not in (run / "events.jsonl").read_text()
    finally:
        for provider in providers:
            provider.release.set()
        for runner, worker in zip(runners, workers):
            if worker.is_alive():
                runner.budget.stop("operator_cancelled")
                worker.join(40)
        for provider in providers:
            provider.close()
