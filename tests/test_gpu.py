import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from gtoc_bench import ROOT, gpu
from gtoc_bench.cli import main
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run

DEVICES = [
    {"uuid": "GPU-a", "name": "NVIDIA A100", "memory_mib": 81920, "driver_version": "610.43.02"}
]
INFO = {"Engine": "podman", "Architecture": "amd64", "NCPU": 64, "MemTotal": 64 * 1024**3}


def test_config_defaults_to_auto_and_preserves_explicit_cpu(tmp_path):
    config = yaml.safe_load((ROOT / "configs/codex.yaml").read_text())
    config.pop("environment", None)
    config.pop("hardware", None)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    assert load_config(path)["hardware"]["gpus"] == "auto"
    assert load_config(path)["environment"]["image"] == "auto"
    config["hardware"] = {"gpus": "none"}
    path.write_text(yaml.safe_dump(config))
    assert load_config(path)["hardware"]["gpus"] == "none"
    config["hardware"] = {"gpus": "auto", "concurrent_with": "20261030T175140-420e4eb6e6c5"}
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="none"):
        load_config(path)


@pytest.mark.parametrize("present", [False, True])
def test_auto_detection_selects_matching_image(monkeypatch, present):
    monkeypatch.setattr(gpu, "nvidia_devices", lambda: DEVICES if present else [])
    plan = gpu.allocation("auto", INFO)
    assert plan["assigned"] == ("all" if present else "none")
    assert gpu.image_name("auto", plan) == "gtoc-bench:phase1-" + ("cuda" if present else "cpu")
    assert gpu.image_name("custom-image", plan) == "custom-image"
    if not present:
        with pytest.raises(ValueError, match="no NVIDIA"):
            gpu.allocation("all", INFO)


def test_explicit_cpu_does_not_require_functioning_nvidia_driver(monkeypatch):
    def broken():
        raise ValueError("broken driver")

    monkeypatch.setattr(gpu, "nvidia_devices", broken)
    assert gpu.allocation("none", INFO)["assigned"] == "none"
    with pytest.raises(ValueError, match="broken driver"):
        gpu.allocation("auto", INFO)


def test_nvidia_on_arm_cannot_silently_run_emulated_cuda(monkeypatch):
    monkeypatch.setattr(gpu, "nvidia_devices", lambda: DEVICES)
    with pytest.raises(ValueError, match="emulation"):
        gpu.allocation("auto", INFO | {"Architecture": "aarch64"})


def test_inventory_parses_heterogeneous_gpus_and_unavailable_vram(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        gpu.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout="GPU-a, NVIDIA A100, 81920, 610.43.02\nGPU-b, NVIDIA GB10, [N/A], 580.173.02\n",
        ),
    )
    assert gpu.nvidia_devices() == DEVICES + [
        {"uuid": "GPU-b", "name": "NVIDIA GB10", "memory_mib": None, "driver_version": "580.173.02"}
    ]


@pytest.mark.parametrize(
    "code,text", [(1, "Failed to initialize NVML"), (6, "Driver/library version mismatch")]
)
def test_discovery_errors_are_not_cpu_fallback(monkeypatch, code, text):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        gpu.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=code, stdout="", stderr=text),
    )
    with pytest.raises(ValueError, match="discovery failed"):
        gpu.nvidia_devices()


def test_missing_driver_is_distinct_from_machine_without_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: None)
    monkeypatch.setattr(gpu, "Path", lambda path: tmp_path / path.lstrip("/"))
    assert gpu.nvidia_devices() == []
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev/nvidiactl").touch()
    with pytest.raises(ValueError, match="driver"):
        gpu.nvidia_devices()


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_runner_assigns_gpus_without_relaxing_isolation(tmp_path, monkeypatch, engine):
    monkeypatch.setattr(gpu, "nvidia_devices", lambda: DEVICES)
    cfg = load_config(ROOT / "configs/codex.yaml")
    runner = Runner(create_run(cfg, tmp_path))
    commands, messages = [], []
    monkeypatch.setattr(
        "gtoc_bench.runner.subprocess.Popen",
        lambda c, **kw: commands.append(c) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "gtoc_bench.runner.threading.Thread", lambda **kw: SimpleNamespace(start=lambda: None)
    )
    monkeypatch.setattr(runner, "send", lambda *a, **kw: messages.append(kw))
    try:
        runner.start_container("cuda-image", INFO | {"Engine": engine})
        command = commands[0]
        assert "--read-only" in command and "no-new-privileges=true" in command
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--cpus") + 1] == "32"
        if engine == "podman":
            assert command[command.index("--device") + 1] == "nvidia.com/gpu=all"
            assert "--gpus" not in command
        else:
            assert command[command.index("--gpus") + 1] == "all"
        assert messages[0]["config"]["hardware"]["gpus"] == "all"
        assert runner.config["hardware"]["gpus"] == "auto"
        assert runner.status()["gpu_allocation"]["host_devices"] == DEVICES
        assert (
            json.loads((runner.path / "container.json").read_text())["gpu_allocation"]["assigned"]
            == "all"
        )
    finally:
        runner.pool.shutdown()


def test_cpu_mode_hides_devices_even_with_a_default_nvidia_runtime():
    flags = gpu.container_arguments({"assigned": "none"}, "docker")
    assert "NVIDIA_VISIBLE_DEVICES=void" in flags and "CUDA_VISIBLE_DEVICES=" in flags
    assert "--gpus" not in flags and "--device" not in flags


def test_visibility_alone_is_insufficient_for_cuda_preflight():
    science = {
        "cuda_available": True,
        "gpu_count": 2,
        "cuda_checks": [{"index": 0, "ok": True}, {"index": 1, "ok": True}],
    }
    gpu.verify_access(science, 2)
    with pytest.raises(ValueError, match="cannot use CUDA"):
        gpu.verify_access(science | {"cuda_available": False}, 2)
    with pytest.raises(ValueError, match="computation failed"):
        gpu.verify_access(science | {"cuda_checks": [{"index": 0, "ok": True}]}, 2)
    with pytest.raises(ValueError, match="computation failed"):
        gpu.verify_access(
            science | {"cuda_checks": [{"ok": True}, {"ok": False, "error": "permission denied"}]},
            2,
        )
    with pytest.raises(ValueError, match="Expected 3"):
        gpu.verify_access(science, 3)


def test_doctor_checks_as_agent_user_without_network(monkeypatch):
    observed = {"cuda_available": True, "gpu_count": 1, "cuda_checks": [{"index": 0, "ok": True}]}

    def run(command, **kwargs):
        if command[1] == "rm":
            return SimpleNamespace(returncode=0)
        assert command[command.index("--user") + 1] == "1000:1000"
        assert command[command.index("--network") + 1] == "none"
        assert "--read-only" in command
        return SimpleNamespace(
            returncode=0, stderr="", stdout="__GTOC_PROBE__" + json.dumps(observed)
        )

    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert (
        gpu.probe_container("image", {"assigned": "all", "host_devices": DEVICES}, "podman")
        == observed
    )


def test_doctor_preserves_container_error(monkeypatch):
    monkeypatch.setattr(
        gpu.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=126, stdout="", stderr="NVIDIA CDI hook failed"),
    )
    with pytest.raises(ValueError, match="CDI hook failed"):
        gpu.probe_container("image", {"assigned": "all", "host_devices": DEVICES}, "podman")


def test_continuation_cannot_gain_or_change_gpu_allocation():
    gpu.verify_continuation([], [])
    gpu.verify_continuation([{"name": "A100"}], [{"name": "A100", "uuid": "GPU-a"}])
    for prior, current in [
        ([], DEVICES),
        (DEVICES, []),
        (DEVICES, [DEVICES[0] | {"uuid": "GPU-b"}]),
    ]:
        with pytest.raises(ValueError, match="Continuation must preserve"):
            gpu.verify_continuation(prior, current)


@pytest.mark.parametrize(
    "arguments,expected", [(["--cpu"], "cpu"), (["--cuda"], "cu128"), ([], "cu128")]
)
def test_build_selects_cuda_or_explicit_cpu(monkeypatch, arguments, expected):
    monkeypatch.setattr(gpu, "nvidia_devices", lambda: DEVICES)
    monkeypatch.setattr(
        "gtoc_bench.runner.docker_json",
        lambda *a: {
            "Architecture": "amd64",
            "NCPU": 64,
            "MemTotal": 64 * 1024**3,
            "ServerVersion": "test",
        },
    )
    commands = []
    monkeypatch.setattr(subprocess, "call", lambda c: commands.append(c) or 0)
    assert main(["build", *arguments]) == 0
    assert "TORCH_VARIANT=" + expected in commands[0]


@pytest.mark.parametrize(
    "arguments,profile,tag",
    [
        (["--cpu", "--agent", "codex"], "codex", "gtoc-bench:codex-cpu"),
        (["--cuda", "--agent", "claude-code"], "claude-code", "gtoc-bench:claude-code-cuda"),
        (["--cuda", "--agent", "codex", "--tag", "custom:cuda"], "codex", "custom:cuda"),
    ],
)
def test_official_client_image_profiles_share_build_and_preserve_custom_tags(
    monkeypatch, arguments, profile, tag
):
    monkeypatch.setattr(
        "gtoc_bench.runner.docker_json",
        lambda *a: {
            "host": {"cpus": 64, "memTotal": 64 * 1024**3, "arch": "amd64"},
            "version": {"Version": "5.4.2"},
        },
    )
    commands = []
    monkeypatch.setattr(subprocess, "call", lambda command, **kwargs: commands.append(command) or 0)
    assert main(["build", *arguments]) == 0
    command = commands[0]
    assert command[command.index("-t") + 1] == tag
    assert "AGENT=" + profile in command
    assert "--layers" in command
    assert command[command.index("-f") + 1] == str(ROOT / "environments/Dockerfile")


def test_known_readonly_cdi_runtime_bug_fails_before_launch(monkeypatch):
    monkeypatch.setattr(gpu, "nvidia_devices", lambda: DEVICES)
    info = INFO | {"RawInfo": {"host": {"ociRuntime": {"version": "crun version 1.21\n"}}}}
    with pytest.raises(ValueError, match="read-only root filesystem"):
        gpu.allocation("auto", info)
    assert gpu.allocation("none", info)["assigned"] == "none"
    info["RawInfo"]["host"]["ociRuntime"]["version"] = "crun version 1.30.1\n"
    assert gpu.allocation("auto", info)["assigned"] == "all"


def test_neutral_preflight_timeout_removes_only_its_own_container(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 180)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gpu.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        gpu.probe_container("image", {"assigned": "none"}, "podman")
    assert commands[1] == ["docker", "rm", "-f", commands[0][commands[0].index("--name") + 1]]
