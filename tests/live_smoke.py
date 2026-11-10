"""Explicit opt-in local checks using the signed-in ChatGPT plan (real quota).

Run: uv run python tests/live_smoke.py [--agent codex] [--case time|tokens]
This is a diagnostic task, never an optimization result or an official ranking.
"""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from gtoc_bench import ROOT
from gtoc_bench.config import load_config, pins
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import write_json

AGENTS = ["codex", "claude_code", "openhands", "swe_agent", "mini_swe_agent"]
PROMPT = """This is a short infrastructure diagnostic, not an optimization task.
Do not read the GTOC statement or attempt to solve it. Do not inspect or change files.
Your entire task is to execute exactly this command with your shell tool:
    /opt/conda/bin/python /workspace/probe.py
Use a blocking call with timeout at least 120 seconds; if your tool exposes
yield_time_ms, set it to 30000. Do not run the command in the background.
The trusted diagnostic script checks the remaining budget, validates a zero-mass
test fixture, and waits for the controller to stop it. Its long wait is intentional.
Do not cancel it, start other work, or finish the run yourself. The controller will
terminate all processes at the configured limit. Keep any explanation under 10 words.
"""
PROBE = """import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
assert os.getuid() == 1000
assert not Path('/var/run/docker.sock').exists()
assert all(v == 'run-scoped-local-only' for k,v in os.environ.items() if k in {'OPENAI_API_KEY','ANTHROPIC_API_KEY','GTOC_RUN_TOKEN'})
def request(path, data=None):
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765'+path, data=data), timeout=25) as response:
        return json.load(response)
before = request('/status')
verdict = request('/evaluate', Path('/workspace/candidate.txt').read_bytes())
assert verdict['status'] == 'valid' and verdict['score'] == 0
child_code = "from pathlib import Path; import time\\nwhile True:\\n Path('/workspace/child-heartbeat').write_text(str(time.time())); time.sleep(0.2)"
child = subprocess.Popen([sys.executable, '-c', child_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path('/workspace/probe.json').write_text(json.dumps({'uid':os.getuid(), 'before':before, 'verdict':verdict, 'child_pid':child.pid}, indent=2))
print('Diagnostic candidate valid; waiting for budget cutoff.', flush=True)
while True:
    Path('/workspace/observed-budget.json').write_text(json.dumps(request('/status')))
    time.sleep(0.5)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", choices=AGENTS)
    parser.add_argument("--case", action="append", choices=["time", "tokens"])
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    results = args.results or ROOT / "results" / (
        "live-smoke-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    results.mkdir(parents=True, exist_ok=False)
    report = {
        "kind": "live_infrastructure_smoke",
        "model": "gpt-6-luna",
        "reasoning": "low",
        "speed": "standard",
        "billing_mode": "subscription",
        "started_at": datetime.now(UTC).isoformat(),
        "checks": [],
    }
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(str(results), flush=True)
    for agent in args.agent or AGENTS:
        for case in args.case or ["time", "tokens"]:
            config = load_config(ROOT / "configs/codex-chatgpt.yaml")
            config["agent"] = {"id": agent, "version": pins()[agent]["version"]}
            config["model"].update(id="gpt-6-luna", reasoning="low")
            config["provider"]["protocol"] = (
                "responses"
                if agent == "codex"
                else "anthropic"
                if agent == "claude_code"
                else "chat_completions"
            )
            config["hardware"].update(cpus=2, memory_gb=4, gpus="none", workspace_gb=2)
            config["budget"] = {
                "wall_seconds": 35 if case == "time" else 90,
                "token_limit": 50000 if case == "time" else 64,
            }
            config["recovery"].update(max_transient_retries=0, max_retry_wait_seconds=3)
            config_path = results / f"{agent}-{case}.yaml"
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
            config = load_config(config_path)
            run = create_run(config, results)
            (run / "workspace/probe.py").write_text(PROBE)
            (run / "workspace/candidate.txt").write_bytes(
                (ROOT / "tests/fixtures/zero_mass.txt").read_bytes()
            )
            write_json(
                run / "smoke-test.json",
                {
                    "case": case,
                    "script_sha256": script_hash,
                    "fixture": "zero_mass",
                    "eligible_for_official_ranking": False,
                },
            )
            print(json.dumps({"starting": agent, "case": case, "run": run.name}), flush=True)
            code = Runner(run, prompt=PROMPT).run()
            summary = json.loads((run / "summary.json").read_text())
            expected = "time_limit" if case == "time" else "token_limit"
            container = subprocess.run(
                ["docker", "inspect", "gtoc-" + run.name.lower()], capture_output=True, timeout=10
            )
            heartbeat = run / "workspace/child-heartbeat"
            before = heartbeat.read_bytes() if heartbeat.exists() else None
            time.sleep(0.6)
            stopped = before == (heartbeat.read_bytes() if heartbeat.exists() else None)
            probe = (
                json.loads((run / "workspace/probe.json").read_text())
                if (run / "workspace/probe.json").exists()
                else None
            )
            observed = run / "workspace/observed-budget.json"
            observed = json.loads(observed.read_text()) if observed.exists() else None
            budget_visible = bool(
                probe
                and observed
                and observed["remaining_seconds"] < probe["before"]["remaining_seconds"]
                and observed["remaining_tokens"] <= probe["before"]["remaining_tokens"]
            )
            requests = [
                json.loads(p.read_text()) for p in (run / "requests").glob("*/metadata.json")
            ]
            completed = [r for r in requests if r.get("response_terminal") == "response.completed"]
            model_confirmed = bool(completed) and all(
                r.get("returned_model") == "gpt-6-luna" for r in completed
            )
            standard_confirmed = bool(completed) and all(
                r.get("returned_service_tier") == "default" for r in completed
            )
            check = {
                "agent": agent,
                "case": case,
                "run_id": run.name,
                "passed": code == 0
                and summary["stop_reason"] == expected
                and container.returncode != 0
                and stopped
                and model_confirmed
                and standard_confirmed
                and (
                    bool(probe) and before is not None and budget_visible
                    if case == "time"
                    else summary["known_tokens"] >= 64
                ),
                "stop_reason": summary["stop_reason"],
                "elapsed_seconds": summary["elapsed_seconds"],
                "known_tokens": summary["known_tokens"],
                "token_overshoot": summary["token_overshoot"],
                "unknown_usage_requests": summary["unknown_usage_requests"],
                "valid_candidate": bool(probe),
                "container_removed": container.returncode != 0,
                "child_stopped": stopped if before else None,
                "remaining_budget_visible": budget_visible,
                "model_confirmed": model_confirmed,
                "standard_tier_confirmed": standard_confirmed,
                "requests": len(requests),
                "returned_models": sorted(
                    {r["returned_model"] for r in requests if r.get("returned_model")}
                ),
                "returned_service_tiers": sorted(
                    {r["returned_service_tier"] for r in requests if r.get("returned_service_tier")}
                ),
                "fatal": summary.get("fatal"),
            }
            report["checks"].append(check)
            write_json(results / "report.json", report)
            print(json.dumps(check), flush=True)
            if summary["stop_reason"] in {
                "provider_unavailable",
                "configuration_error",
                "operator_cancelled",
            }:
                print(
                    "Stopped campaign to avoid additional quota use after provider/operator failure.",
                    flush=True,
                )
                return 1
    return int(not all(c["passed"] for c in report["checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
