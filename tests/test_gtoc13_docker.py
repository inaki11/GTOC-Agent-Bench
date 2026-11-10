"""Actual isolated judge and controller, with synthetic model responses only."""

import json
import os
import shlex

import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.cli import validate_file
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE to the rebuilt image"),
]


def test_offline_both_problem_judges_and_rejection(tmp_path):
    result = validate_file(ROOT / "tests/fixtures/gtoc13/kaist-high-score.txt", IMAGE, "gtoc13")
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(111.20732893722135, abs=1e-8)
    assert validate_file(ROOT / "tests/fixtures/zero_mass.txt", IMAGE)["status"] == "valid"
    bad = tmp_path / "bad.txt"
    bad.write_text("0 0 NaN 1 0 0 0 0 0 0 0 0\n")
    result = validate_file(bad, IMAGE, "gtoc13")
    assert result["status"] == "invalid" and result["score"] is None
    assert "NONFINITE" in result["diagnostics"][0]


def test_complete_gtoc13_run_records_large_candidate_and_text_diagnostics(tmp_path):
    candidate = (ROOT / "tests/fixtures/gtoc13/planetx-conic.txt").read_text()
    script = r"""
import json, os, subprocess
from pathlib import Path
assert os.environ['GTOC_PROBLEM'] == 'gtoc13'
assert 'id: gtoc13' in Path('/problem/problem.yaml').read_text()
assert len(list(Path('/problem/data').glob('*.csv'))) == 3
assert len(list(Path('/problem/documents').glob('*.pdf'))) == 2
assert not Path('/problem/validator').exists()
assert not Path('/opt/gtoc/tests/fixtures/gtoc13/antipodes-submitted.csv').exists()
assert not Path('/opt/gtoc/references/leaderboards').exists()
for path in ('/problem/instructions.md', '/opt/gtoc/src/gtoc_bench/gtoc13/judge.py'):
    try:
        Path(path).write_text('tamper')
        raise AssertionError('protected material was writable')
    except (OSError, PermissionError):
        pass
# Exercise the CLI, HTTP admission, and both framing directions above 32 MiB
# after base64 encoding. The numeric trajectory itself is unchanged.
Path('/workspace/large.txt').write_text('#'+'x'*(26*1024**2)+'\n'+CANDIDATE)
result = subprocess.run(['gtoc','evaluate','/workspace/large.txt','--json'], capture_output=True, text=True)
assert result.returncode == 0, result.stdout+result.stderr
valid = json.loads(result.stdout)
assert valid['score_unit'] == 'points' and abs(valid['score']-21.92704582292371)<1e-8
Path('/workspace/bad.txt').write_text('0 0 NaN 1 0 0 0 0 0 0 0 0\n')
result = subprocess.run(['gtoc','evaluate','/workspace/bad.txt','--json'], capture_output=True, text=True)
assert result.returncode == 2, result.stdout+result.stderr
bad = json.loads(result.stdout)
assert bad['score'] is None and 'NONFINITE' in bad['diagnostics'][0]
Path('/workspace/test-verdicts.json').write_text(json.dumps([valid,bad]))
""".replace("CANDIDATE", repr(candidate))
    command = "/opt/conda/bin/python -c " + shlex.quote(script)
    provider = FakeProvider(tool_command=command)
    config = load_config(ROOT / "configs/codex-gtoc13.yaml")
    credentials = tmp_path / "provider.env"
    credentials.write_text("OPENAI_API_KEY=synthetic-gtoc13-test\n")
    credentials.chmod(0o600)
    config["provider"].update(base_url=provider.url, env_file=str(credentials))
    config["environment"]["image"] = IMAGE
    config["budget"] = {"wall_seconds": 180, "token_limit": None}
    run = create_run(config, tmp_path / "results")
    try:
        Runner(run, prompt="Execute the requested integration command and finish.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert summary["best_score"] == pytest.approx(21.92704582292371)
        verdicts = json.loads((run / "workspace/test-verdicts.json").read_text())
        assert [v["status"] for v in verdicts] == ["valid", "invalid"]
        reports = [p.read_text() for p in (run / "submissions").glob("*/report.txt")]
        assert any("NONFINITE" in report for report in reports)
        assert len(list((run / "submissions").glob("*/solution.txt.gz"))) == 2
    finally:
        provider.close()


@pytest.mark.slow
def test_complete_antipodes_reference_in_protected_docker_judge(record_property):
    result = validate_file(ROOT / "tests/fixtures/gtoc13/antipodes-submitted.csv", IMAGE, "gtoc13")
    record_property("verdict", json.dumps(result, allow_nan=False))
    assert result["status"] == "valid", result
    assert result["score"] == pytest.approx(329.3314957879535, abs=1e-6)
    assert result["metrics"]["science_flybys"] == 133
    assert result["problem_revision"] == "gtoc13-reconstructed-controls-v2"
