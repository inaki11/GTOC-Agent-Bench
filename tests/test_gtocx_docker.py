"""Protected GTOCX evaluation and host-only reference isolation with a fake model."""

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
    pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE to a GTOCX-enabled image"),
]


def test_protected_gtocx_judge_and_existing_gtoc12(tmp_path):
    result = validate_file(ROOT / "tests/fixtures/gtocx/small-tree.txt", IMAGE, "gtocx")
    assert result["status"] == "valid" and result["score"] == pytest.approx(14.005617704632781)
    assert result["score_breakdown"]["time_bonus"] == 1
    assert validate_file(ROOT / "tests/fixtures/zero_mass.txt", IMAGE)["status"] == "valid"
    bad = tmp_path / "bad.txt"
    bad.write_text("header\n-11 1 0 74 NaN 0 0 0 0 0\n")
    result = validate_file(bad, IMAGE, "gtocx")
    assert result["status"] == "invalid" and result["score"] is None
    assert "NONFINITE" in result["diagnostics"][0]


def test_full_run_records_score_and_rejection_without_exposing_references(tmp_path):
    data = (ROOT / "tests/fixtures/gtocx/small-tree.txt").read_text()
    script = r"""
import json, os, subprocess
from pathlib import Path
assert os.environ['GTOC_PROBLEM']=='gtocx'
assert 'id: gtocx' in Path('/problem/problem.yaml').read_text()
assert len(Path('/problem/data/stars.txt').read_text().splitlines())==100002
assert len(list(Path('/problem/documents').glob('*.pdf')))==2
assert not Path('/opt/gtoc/references').exists()
assert not Path('/problem/leaderboards').exists()
assert not Path('/problem/submissions').exists()
for name in ('/problem/data/stars.txt','/opt/gtoc/src/gtoc_bench/gtocx/judge.py'):
    try:
        Path(name).write_text('tamper')
        raise AssertionError('judge/data writable')
    except OSError:
        pass
Path('/workspace/solution.txt').write_text(DATA)
p=subprocess.run(['gtoc','evaluate','/workspace/solution.txt','--json'],capture_output=True,text=True)
assert p.returncode==0,p.stdout+p.stderr
valid=json.loads(p.stdout)
assert valid['score_unit']=='points' and valid['score_breakdown']['time_bonus']==1
Path('/workspace/bad.txt').write_text('header\n-11 1 0 74 NaN 0 0 0 0 0\n'.replace('\\n','\n'))
p=subprocess.run(['gtoc','evaluate','/workspace/bad.txt','--json'],capture_output=True,text=True)
assert p.returncode==2,p.stdout+p.stderr
invalid=json.loads(p.stdout)
assert invalid['score'] is None and 'NONFINITE' in invalid['diagnostics'][0]
Path('/workspace/verified.json').write_text(json.dumps([valid,invalid]))
""".replace("DATA", repr(data))
    provider = FakeProvider(tool_command="/opt/conda/bin/python -c " + shlex.quote(script))
    config = load_config(ROOT / "configs/codex-gtocx.yaml")
    credentials = tmp_path / "provider.env"
    credentials.write_text("OPENAI_API_KEY=synthetic-gtocx-key\n")
    credentials.chmod(0o600)
    config["provider"].update(base_url=provider.url, env_file=str(credentials))
    config["environment"]["image"] = IMAGE
    config["budget"] = {"wall_seconds": 180, "token_limit": None}
    run = create_run(config, tmp_path / "results")
    try:
        Runner(run, prompt="Execute the requested integration command and finish.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert summary["best_score"] == pytest.approx(14.005617704632781)
        results = json.loads((run / "workspace/verified.json").read_text())
        assert [r["status"] for r in results] == ["valid", "invalid"]
        assert any("NONFINITE" in p.read_text() for p in (run / "submissions").glob("*/report.txt"))
    finally:
        provider.close()
