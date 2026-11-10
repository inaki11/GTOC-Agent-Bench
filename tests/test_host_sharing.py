import copy

import pytest

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.host_sharing import sharing_plan

PEER = "20261030T175140-420e4eb6e6c5"
PEER2 = "20261031T171711-15a557fb8cee"


def test_named_peer_capacity_and_default_exclusion():
    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"]["cpus"] = 32
    info = {"NCPU": 64}
    container = {"Name": "/gtoc-" + PEER.lower(), "HostConfig": {"NanoCpus": 32_000_000_000}}
    with pytest.raises(ValueError, match="Another GTOC"):
        sharing_plan(config, info, [container])
    config["hardware"]["concurrent_with"] = PEER
    plan = sharing_plan(config, info, [container])
    assert plan["peer_cpu_limit"] == plan["cpu_limit"] == 32
    podman = copy.deepcopy(container)
    podman["HostConfig"] = {"CpuQuota": 3200000, "CpuPeriod": 100000}
    assert sharing_plan(config, info, [podman])["peer_cpu_limit"] == 32
    with pytest.raises(ValueError, match="exceed"):
        sharing_plan(config, {"NCPU": 48}, [container])
    with pytest.raises(ValueError, match="declared peer"):
        sharing_plan(config, info, [container, container])
    container["Name"] = "/gtoc-unrelated"
    with pytest.raises(ValueError, match="declared peer"):
        sharing_plan(config, info, [container])


def test_unknown_or_unbounded_peer_is_rejected():
    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"]["concurrent_with"] = PEER
    for limits in ({}, {"CpuQuota": -1, "CpuPeriod": 100000}):
        with pytest.raises(ValueError, match="verify"):
            sharing_plan(
                config, {"NCPU": 64}, [{"Name": "gtoc-" + PEER.lower(), "HostConfig": limits}]
            )


def test_three_declared_evaluations_fit_without_authorizing_other_runs(tmp_path):
    import yaml

    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"].update(cpus=20, gpus="none", concurrent_with=[PEER, PEER2])
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    config = load_config(path)
    containers = [
        {"Name": "/gtoc-" + PEER.lower(), "HostConfig": {"NanoCpus": 20_000_000_000}},
        {"Name": "/gtoc-" + PEER2.lower(), "HostConfig": {"CpuQuota": 2000000, "CpuPeriod": 100000}},
    ]
    plan = sharing_plan(config, {"NCPU": 64}, containers)
    assert plan["cpu_limit"] + plan["peer_cpu_limit"] == 60
    assert plan["expected_peer_run_ids"] == [PEER, PEER2]
    assert len(plan["peer_cpu_limits"]) == 2
    with pytest.raises(ValueError, match="exceed"):
        sharing_plan(config, {"NCPU": 59}, containers)
    config["hardware"]["concurrent_with"] = [PEER]
    with pytest.raises(ValueError, match="declared peers"):
        sharing_plan(config, {"NCPU": 64}, containers)


@pytest.mark.parametrize("peers", [[], [PEER, PEER], [PEER, "other"], 3, [PEER, 2]])
def test_invalid_concurrent_peer_lists_are_rejected(tmp_path, peers):
    import yaml

    config = load_config(ROOT / "configs/codex.yaml")
    config["hardware"].update(gpus="none", concurrent_with=peers)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="distinct run IDs"):
        load_config(path)
