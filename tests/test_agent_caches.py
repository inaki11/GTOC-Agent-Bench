"""Cache policy shared by preflight, native clients and container tools."""

import stat
from pathlib import Path

import pytest

from gtoc_bench.agents import cache_environment, prepare_caches


def test_cache_preparation_repairs_owned_read_only_directories(tmp_path):
    config = tmp_path / ".config"
    cache = tmp_path / ".cache"
    for path in [config, cache]:
        path.mkdir(mode=0o500)
    prepare_caches(tmp_path)
    env = cache_environment(tmp_path)
    assert env["MPLCONFIGDIR"] == str(cache / "matplotlib")
    for path in [config, cache, Path(env["MPLCONFIGDIR"]), cache / "fontconfig"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        (path / "writable").write_text("cache check")


def test_cache_preparation_rejects_restored_symlink_without_changing_target(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o500)
    (workspace / ".cache").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="regular directory"):
        prepare_caches(workspace)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o500
    assert not list(outside.iterdir())
