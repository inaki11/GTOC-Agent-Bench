"""Explicit, CPU-bounded coexistence with named evaluations."""

import math

from .config import effective_cpus


def sharing_plan(config, info, containers):
    peer = config["hardware"].get("concurrent_with")
    peers = [peer] if isinstance(peer, str) else peer or []
    names = [c["Name"].lstrip("/") for c in containers]
    if names and not peer:
        raise ValueError("Another GTOC evaluation is active; stop/reconcile it first")
    allowed = {"gtoc-" + value.lower() for value in peers}
    if len(names) != len(set(names)) or any(name not in allowed for name in names):
        raise ValueError("Concurrent execution is authorized only with the declared peers")
    cpus = effective_cpus(config["hardware"]["cpus"], info["NCPU"])
    peer_cpus = 0
    limits = {}
    for container in containers:
        hardware = container["HostConfig"]
        nano = hardware.get("NanoCpus", 0) or 0
        quota, period = hardware.get("CpuQuota", 0), hardware.get("CpuPeriod", 0)
        limit = nano / 1e9 if nano else quota / period if quota and period else 0
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("Cannot verify the peer's CPU limit")
        peer_cpus += limit
        limits[container["Name"].lstrip("/")] = limit
    if cpus + peer_cpus > info["NCPU"]:
        raise ValueError("Combined evaluation CPU limits exceed the host capacity")
    memory = config["hardware"].get("memory_gb")
    own_memory = int(memory * 1024**3) if memory is not None else int(info.get("MemTotal", 0) * 0.9)
    peer_memory = sum(c["HostConfig"].get("Memory", 0) for c in containers)
    if info.get("MemTotal"):
        if any(c["HostConfig"].get("Memory", 0) <= 0 for c in containers):
            raise ValueError("Cannot verify the peer's memory limit")
        if own_memory + peer_memory > info["MemTotal"] * 0.9 + 1:
            raise ValueError(
                "Combined memory limits exceed 90% of host RAM; declare explicit per-run RAM limits"
            )
    return {
        "enabled": bool(peer),
        "expected_peer_run_id": peer if isinstance(peer, str) else None,
        "expected_peer_run_ids": peers,
        "observed_peer_containers": names,
        "peer_cpu_limit": peer_cpus,
        "peer_cpu_limits": limits,
        "cpu_limit": cpus,
        "host_cpus": info["NCPU"],
        "memory_limit_bytes": own_memory,
        "peer_memory_limit_bytes": peer_memory,
        "host_memory_bytes": info.get("MemTotal"),
        "purpose": "exploratory_shared_host" if peer else "exclusive_host",
    }
