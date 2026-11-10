"""Actual official GTOC11 executable through the protected benchmark interfaces."""

import json
import os
import runpy
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
    pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE to a GTOC11-enabled image"),
]


def test_complete_organizer_example_passes_original_checker_and_b1_score():
    result = validate_file(ROOT / "references/gtoc11/received/Solution.txt", IMAGE, "gtoc11")
    assert result["status"] == "valid", result
    assert result["score"] == 1556.5911
    assert result["native_status"] == result["returncode"] == 0
    assert result["metrics"]["transferred_asteroids"] == 148
    assert result["score_breakdown"]["time_bonus"] == 1
    assert result["score_breakdown"]["independent_score_b1"] == pytest.approx(1556.5911158875315)
    assert (
        result["validator_revision"]
        == "6beb6456ee3959034ef3510ea646edf1633d37cb1fd61e41687dee3d94b4eabb"
    )


@pytest.mark.parametrize(
    "row,column,value,fragment",
    [
        (2, 1, "100000000", "R and V cannot match"),
        (2, 7, "100", "6 km/s"),
        (3, 7, "10", "R and V cannot match"),
        (44, 7, "0", "acceleartion"),
        (1303, 10, "1000000000000000", "Mass cannot match"),
    ],
)
def test_actual_native_physics_rejections_keep_textual_cause(
    tmp_path, row, column, value, fragment
):
    lines = (ROOT / "tests/fixtures/gtoc11/single-asteroid.txt").read_text().splitlines()
    fields = lines[row - 1].split()
    fields[column] = value
    lines[row - 1] = " ".join(fields)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("\n".join(lines) + "\n")
    result = validate_file(candidate, IMAGE, "gtoc11")
    assert result["status"] == "invalid" and result["score"] is None, result
    assert result["native_status"] == -1
    assert fragment in "\n".join(result["diagnostics"]), result
    assert "Line " in "\n".join(result["diagnostics"])


def test_derived_real_trajectory_valid_zero_and_gtoc12_regression():
    result = validate_file(ROOT / "tests/fixtures/gtoc11/single-asteroid.txt", IMAGE, "gtoc11")
    assert result["status"] == "valid" and result["score"] == 0, result
    assert result["metrics"]["transferred_asteroids"] == 1
    assert validate_file(ROOT / "tests/fixtures/zero_mass.txt", IMAGE)["status"] == "valid"


def test_atd_wait_rejects_an_actual_catalogue_state_after_only_29_days(tmp_path):
    module = runpy.run_path(str(ROOT / "references/gtoc11/check_reference.py"))
    candidate = tmp_path / "too-early.txt"
    candidate.write_bytes(
        module["early_activation"](
            (ROOT / "tests/fixtures/gtoc11/single-asteroid.txt").read_bytes()
        )
    )
    result = validate_file(candidate, IMAGE, "gtoc11")
    assert result["status"] == "invalid" and result["score"] is None, result
    assert result["native_status"] == -1
    assert "30 days constraint" in "\n".join(result["diagnostics"]), result


def test_full_agent_run_uses_official_judge_without_exposing_references(tmp_path):
    departure = (ROOT / "tests/fixtures/gtoc11/single-asteroid.txt").read_text().splitlines()[1]
    data = (
        "\n".join(["1 1 0", departure, *[f"{i} 0 0" for i in range(2, 11)], "1.1 0 0 0", "1"])
        + "\n"
    )
    script = r"""
import json, os, subprocess
from pathlib import Path
assert os.getuid()==1000 and os.environ['GTOC_PROBLEM']=='gtoc11'
assert 'id: gtoc11' in Path('/problem/problem.yaml').read_text()
assert len(Path('/problem/data/Candidate Asteroids.txt').read_text().splitlines())==83453
assert '6e-9' in Path('/problem/statement.md').read_text()
assert not Path('/opt/gtoc/references').exists()
assert not Path('/problem/Solution.txt').exists()
assert not Path('/problem/leaderboards').exists()
for filename in ('/opt/gtoc/problems/gtoc11/validator/GTOC11Check_Linux',
                 '/problem/data/Candidate Asteroids.txt'):
    try:
        with open(filename,'wb') as out: out.write(b'tamper')
        raise AssertionError('protected material is writable')
    except OSError: pass
try:
    Path('/opt/gtoc/problems/gtoc11/validator/GTOC11Check_Linux').read_bytes()
    raise AssertionError('native checker is readable by agent')
except PermissionError: pass
Path('/workspace/candidate.txt').write_text(DATA)
p=subprocess.run(['gtoc','evaluate','/workspace/candidate.txt','--json'],capture_output=True,text=True)
assert p.returncode==0,p.stdout+p.stderr
good=json.loads(p.stdout)
assert good['status']=='valid' and good['score']==0 and good['score_breakdown']['time_bonus']==1
rows=DATA.splitlines();fields=rows[1].split();fields[7]='100';rows[1]=' '.join(fields)
Path('/workspace/bad.txt').write_text(chr(10).join(rows)+chr(10))
p=subprocess.run(['gtoc','evaluate','/workspace/bad.txt','--json'],capture_output=True,text=True)
assert p.returncode==2,p.stdout+p.stderr
bad=json.loads(p.stdout)
assert bad['status']=='invalid' and bad['score'] is None
assert '6 km/s' in str(bad['diagnostics']) and 'Line 2' in str(bad['diagnostics'])
Path('/workspace/verified.json').write_text(json.dumps([good,bad]))
""".replace("DATA", repr(data))
    provider = FakeProvider(tool_command="/opt/conda/bin/python -c " + shlex.quote(script))
    config = load_config(ROOT / "configs/codex-gtoc11.yaml")
    credentials = tmp_path / "provider.env"
    credentials.write_text("OPENAI_API_KEY=synthetic-gtoc11-key\n")
    credentials.chmod(0o600)
    config["provider"].update(base_url=provider.url, env_file=str(credentials))
    config["environment"]["image"] = IMAGE
    config["budget"] = {"wall_seconds": 180, "token_limit": None}
    run = create_run(config, tmp_path / "results")
    try:
        Runner(run, prompt="Execute the requested integration command and finish.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert summary["best_score"] == 0
        assert [r["status"] for r in json.loads((run / "workspace/verified.json").read_text())] == [
            "valid",
            "invalid",
        ]
        assert any("6 km/s" in p.read_text() for p in (run / "submissions").glob("*/report.txt"))
    finally:
        provider.close()
