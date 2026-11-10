"""Plotting and Fontconfig as UID 1000 in the actual read-only scientific image."""

import os
import subprocess

import pytest

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE")]


@pytest.mark.parametrize("restored_root_cache", [False, True])
def test_preflight_prepares_writable_plotting_caches_as_agent(restored_root_cache):
    code = r'''
import json,os,stat,subprocess,sys
from pathlib import Path
from gtoc_bench.agents import cache_environment
from gtoc_bench.bridge import Bridge
if RESTORED:
 for n in ['.cache','.config']:
  p=Path('/workspace')/n;p.mkdir(mode=0o500)
b=Bridge();b.prepare_agent_caches()
env=os.environ|{'HOME':'/workspace'}|cache_environment()
probe=r"""
import ctypes,json,os,stat
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
assert os.getuid()==1000
assert matplotlib.get_cachedir()==os.environ['MPLCONFIGDIR']
for key in ['XDG_CONFIG_HOME','XDG_CACHE_HOME','MPLCONFIGDIR']:
 p=Path(os.environ[key]);assert p.stat().st_uid==1000
 assert stat.S_IMODE(p.stat().st_mode)==0o700
 (p/'write-check').write_text('ok')
fontconfig=ctypes.CDLL('/opt/conda/lib/libfontconfig.so.1')
fontconfig.FcInit.restype=ctypes.c_int
assert fontconfig.FcInit()
assert (Path(os.environ['XDG_CACHE_HOME'])/'fontconfig').stat().st_uid==1000
plt.plot([0,1],[0,1]);plt.savefig('/workspace/cache-check.png')
assert Path('/workspace/cache-check.png').stat().st_size>0
print('CACHE_OK')
"""
p=subprocess.run([sys.executable,'-s','-P','-c',probe],env=env,user=1000,group=1000,extra_groups=(),capture_output=True,text=True,timeout=90)
assert p.returncode==0,(p.stdout,p.stderr)
assert not any(s in p.stderr.lower() for s in ['no writable cache','temporary cache directory','permission denied']),(p.stdout,p.stderr)
assert 'CACHE_OK' in p.stdout
print(json.dumps({'status':'passed','agent_uid':1000,'fresh_or_restored_caches':True}))
'''.replace("RESTORED", repr(restored_root_cache))
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
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--security-opt",
            "no-new-privileges=true",
            "--cpus",
            "2",
            "--memory",
            "2g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=128m,mode=0755",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--entrypoint",
            "/opt/conda/bin/python",
            IMAGE,
            "-s",
            "-P",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert '"status": "passed"' in result.stdout
