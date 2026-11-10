"""Resolve NVIDIA access before starting the evaluation clock."""

import csv
import io
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path


def nvidia_devices():
    executable = shutil.which("nvidia-smi")
    if executable is None:
        # Do not confuse a missing/broken driver with an ordinary CPU-only machine.
        detected = Path("/dev/nvidiactl").exists()
        for device in Path("/sys/bus/pci/devices").glob("*"):
            try:
                detected |= (device / "vendor").read_text().strip() == "0x10de" and (
                    device / "class"
                ).read_text().strip().startswith("0x03")
            except OSError:
                continue
        if detected:
            raise ValueError(
                "NVIDIA hardware detected but nvidia-smi is missing; repair the host driver"
            )
        return []
    result = subprocess.run(
        [
            executable,
            "--query-gpu=uuid,name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        if result.returncode == 6 and "No devices were found" in result.stdout + result.stderr:
            return []
        raise ValueError("NVIDIA discovery failed: " + (result.stderr or result.stdout).strip())
    devices = []
    for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 4 or not row[0].startswith("GPU-"):
            raise ValueError("Unexpected nvidia-smi inventory format")
        uuid, name, memory, driver = (value.strip() for value in row)
        devices.append(
            {
                "uuid": uuid,
                "name": name,
                "memory_mib": int(memory) if memory.isdigit() else None,
                "driver_version": driver,
            }
        )
    return devices


def allocation(requested, info):
    if requested not in {"auto", "all", "none"}:
        raise ValueError("hardware.gpus must be auto, all or none")
    devices = nvidia_devices() if requested != "none" else []
    plan = {
        "requested": requested,
        "assigned": "all" if devices else "none",
        "host_devices": devices,
        "reason": "explicit_cpu_only"
        if requested == "none"
        else "nvidia_detected"
        if devices
        else "no_nvidia_devices",
    }
    if requested == "all" and not devices:
        raise ValueError("GPU requested but no NVIDIA devices were detected")
    if devices and info["Architecture"] not in {"amd64", "x86_64"}:
        raise ValueError(
            "The current GTOC image is amd64: CUDA under ARM/x86 emulation is unsupported. Use a native amd64 GPU host, or explicitly set hardware.gpus: none for CPU emulation"
        )
    runtime = info.get("RawInfo", {}).get("host", {}).get("ociRuntime", {})
    if runtime:
        plan["runtime"] = runtime
    crun = re.search(r"crun version (\d+)\.(\d+)", runtime.get("version", ""))
    if devices and crun and tuple(map(int, crun.groups())) < (1, 26):
        raise ValueError(
            "NVIDIA CDI with a read-only root filesystem requires crun >= 1.26 or runc; "
            "the selected crun runtime applies read-only mode before NVIDIA hooks can run. "
            "Update/select a compatible OCI runtime; do not remove container isolation"
        )
    return plan


def container_arguments(plan, engine):
    if plan["assigned"] == "none":
        return ["--env", "NVIDIA_VISIBLE_DEVICES=void", "--env", "CUDA_VISIBLE_DEVICES="]
    if engine == "podman":
        return ["--device", "nvidia.com/gpu=all"]
    return ["--gpus", "all", "--env", "NVIDIA_DRIVER_CAPABILITIES=compute,utility"]


def image_name(requested, plan):
    if requested != "auto":
        return requested
    return "gtoc-bench:phase1-" + ("cuda" if plan["assigned"] == "all" else "cpu")


def verify_access(science, expected_count=None):
    if not science["cuda_available"] or not science.get("gpu_count"):
        raise ValueError(
            "GPU assigned but scientific Python cannot use CUDA; build/select the CUDA image and check NVIDIA Container Toolkit/CDI and the host driver"
        )
    checks = science.get("cuda_checks", [])
    if len(checks) != science["gpu_count"] or not all(c["ok"] for c in checks):
        raise ValueError("CUDA computation failed as the agent user: " + str(checks))
    if expected_count is not None and science["gpu_count"] != expected_count:
        raise ValueError(
            f"Expected {expected_count} NVIDIA GPUs, but the agent can use {science['gpu_count']}; verify device visibility/MIG configuration"
        )


def verify_continuation(previous_devices, current_devices):
    # Older runs did not record UUIDs; preserve every field that they did record.
    if len(previous_devices) != len(current_devices) or any(
        any(current.get(key) != value for key, value in previous.items())
        for previous, current in zip(previous_devices, current_devices)
    ):
        raise ValueError(
            "Continuation must preserve the actual GPU allocation; start a separate trial to change hardware"
        )


def probe_container(image, plan, engine):
    """A neutral check with the agent's UID and isolation, without any model calls."""
    name = "gtoc-preflight-" + uuid.uuid4().hex[:12]
    command = [
        "docker",
        "run",
        "--name",
        name,
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--user",
        "1000:1000",
        "--cpus",
        "1",
        "--memory",
        "4g",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
        "--env",
        "HOME=/tmp",
        *container_arguments(plan, engine),
        "--entrypoint",
        "/opt/conda/bin/python",
        image,
        "-s",
        "-P",
        "-c",
        "import json; from gtoc_bench.hardware import probe; print('__GTOC_PROBE__'+json.dumps(probe()))",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    finally:
        # Killing the CLI on timeout does not necessarily stop its container.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError("Container preflight failed: " + (result.stderr or result.stdout).strip())
    science = next(
        (
            json.loads(line.removeprefix("__GTOC_PROBE__"))
            for line in result.stdout.splitlines()
            if line.startswith("__GTOC_PROBE__")
        ),
        None,
    )
    if science is None:
        raise ValueError("Container preflight returned no scientific inventory; rebuild the image")
    if plan["assigned"] == "all":
        verify_access(science, len(plan["host_devices"]))
    return science
