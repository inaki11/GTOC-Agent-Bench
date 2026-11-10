"""Real CUDA and CPU opt-out with a local scripted provider, never a model API."""

import json
import os
import shlex

import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run

IMAGE = os.environ.get("GTOC_TEST_CUDA_IMAGE")
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_CUDA_IMAGE on an NVIDIA host"),
]


@pytest.mark.parametrize("mode", ["auto", "none"])
def test_agent_uses_assigned_gpu_without_root_privileges(tmp_path, mode):
    script = """
import json, os
from pathlib import Path
from gtoc_bench.hardware import probe
science = probe()
assert os.getuid() == 1000
assert int(next(line.split(':')[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')), 16) == 0
for target in ('/etc/gtoc-write-probe', '/opt/gtoc/src/gtoc_bench/gtoc-write-probe'):
    try:
        Path(target).write_text('must fail')
    except OSError:
        pass
    else:
        raise AssertionError('agent can write trusted files: ' + target)
try:
    Path('/opt/gtoc/problems/gtoc12/validator/GTOC12_Verify').read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('agent can read the private validator')
Path('/workspace/gpu-check.json').write_text(json.dumps(science))
"""
    provider = FakeProvider(tool_command="/opt/conda/bin/python -s -P -c " + shlex.quote(script))
    cfg = load_config(ROOT / "configs/codex.yaml")
    env = tmp_path / "synthetic.env"
    env.write_text(cfg["provider"]["api_key_variable"] + "=synthetic-gpu-test-key\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    cfg["hardware"].update(gpus=mode, cpus=2, memory_gb=8)
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 60, "token_limit": None}
    run = create_run(cfg, tmp_path)
    # The controller and neutral preflight must ignore agent-controlled modules
    # and user-site startup hooks in a restored workspace.
    poison = run / "workspace/gtoc_bench"
    poison.mkdir()
    (poison / "__init__.py").write_text(
        "raise RuntimeError('untrusted workspace package loaded')\n"
    )
    (run / "workspace/torch.py").write_text("raise RuntimeError('untrusted Torch loaded')\n")
    site = run / "workspace/.local/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    (site / "preflight.pth").write_text(
        "import pathlib; pathlib.Path('/workspace/preflight-user-site-loaded').touch()\n"
    )
    try:
        Runner(run, prompt="Execute the requested integration command and finish.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        result = json.loads((run / "workspace/gpu-check.json").read_text())
        allocation = json.loads((run / "gpu-allocation.json").read_text())
        assert result["uid"] == 1000
        if mode == "auto":
            assert result["gpu_count"] == len(allocation["host_devices"]) > 0
            assert len(result["cuda_checks"]) == result["gpu_count"]
            assert all(check["ok"] for check in result["cuda_checks"])
        else:
            assert result["gpu_count"] == 0 and not result["cuda_available"]
        assert "GPU allocation:" in (run / "prompt.txt").read_text()
        assert "/opt/conda/bin/python" in (run / "prompt.txt").read_text()
        assert json.loads((run / "preflight.json").read_text())["science"]["uid"] == 1000
        assert not (run / "workspace/preflight-user-site-loaded").exists()
    finally:
        provider.close()
