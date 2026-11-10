"""Runtime measurements, collected before the agent sees the problem."""

import hashlib
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def tools_inventory():
    packages = {}
    for name in (
        "numpy",
        "scipy",
        "torch",
        "pykep",
        "nlopt",
        "casadi",
        "cyipopt",
        "numba",
        "llvmlite",
        "pybind11",
        "Cython",
        "ziglang",
        "threadpoolctl",
        "PyYAML",
    ):
        try:
            distribution = importlib.metadata.distribution(name)
            packages[name] = {
                "version": distribution.version,
                "location": str(distribution.locate_file("")),
            }
        except importlib.metadata.PackageNotFoundError:
            packages[name] = {"version": None, "location": None}
    commands = {}
    version_flags = {
        "zig": "version",
        "pdfinfo": "-v",
        "pdftotext": "-v",
        "pdftoppm": "-v",
        "pdftocairo": "-v",
    }
    for name in (
        "file",
        "gcc",
        "g++",
        "cmake",
        "pkg-config",
        "zig",
        "ldconfig",
        "pdfinfo",
        "pdftotext",
        "pdftoppm",
        "pdftocairo",
    ):
        path = shutil.which(name) or (
            "/usr/sbin/ldconfig"
            if name == "ldconfig" and Path("/usr/sbin/ldconfig").exists()
            else None
        )
        commands[name] = {"path": path}
        if path:
            try:
                output = subprocess.run(
                    [path, version_flags.get(name, "--version")],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                commands[name]["version"] = (output.stdout or output.stderr).splitlines()[0]
            except (OSError, subprocess.TimeoutExpired, IndexError):
                commands[name]["version"] = None
    return {
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        },
        "packages": packages,
        "commands": commands,
        "ipopt_libraries": sorted(str(p) for p in Path("/opt/conda/lib").glob("libipopt.so*")),
        "ipopt_headers": "/opt/conda/include/coin-or",
        "pkg_config_path": "/opt/conda/lib/pkgconfig",
    }


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def probe():
    import numpy
    import pykep
    import scipy
    import torch

    gpu, cuda_checks = [], []
    for index in range(torch.cuda.device_count()):
        device = torch.cuda.get_device_properties(index)
        gpu.append(
            {
                "index": index,
                "name": device.name,
                "memory_bytes": device.total_memory,
                "compute_capability": [device.major, device.minor],
                "uuid": str(device.uuid) if getattr(device, "uuid", None) else None,
            }
        )
        try:
            tensor = torch.ones((32, 32), device=f"cuda:{index}")
            product = tensor @ tensor
            torch.cuda.synchronize(index)
            if product[0, 0].item() != 32:
                raise ValueError("Incorrect CUDA matrix product")
            cuda_checks.append({"index": index, "ok": True})
            del tensor, product
        except Exception as exc:
            cuda_checks.append({"index": index, "ok": False, "error": str(exc)})
    driver = None
    if shutil.which("nvidia-smi"):
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        driver = result.stdout.strip() if result.returncode == 0 else None
    return {
        "uid": os.getuid(),
        "torch": torch.__version__,
        "pykep": pykep.__version__,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "tools": tools_inventory(),
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
        "cuda_checks": cuda_checks,
        "gpu_count": len(gpu),
        "gpus": gpu,
        "nvidia_smi": driver,
        "machine": platform.machine(),
        "kernel": platform.release(),
        "logical_cpus": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "cpu_info": read_optional("/proc/cpuinfo"),
        "memory_info": read_optional("/proc/meminfo"),
        "cpu_quota": read_optional("/sys/fs/cgroup/cpu.max"),
        "memory_limit": read_optional("/sys/fs/cgroup/memory.max"),
        "thread_environment": {
            key: os.environ[key]
            for key in (
                "KMP_AFFINITY",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
            )
            if key in os.environ
        },
        "agent_executable_hashes": {
            str(p.relative_to("/opt/agents")): file_hash(p)
            for p in Path("/opt/agents").rglob("*")
            if p.name in {"codex", "claude"} and p.is_file() and not p.is_symlink()
        },
    }
