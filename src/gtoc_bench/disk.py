"""Host disk/quota observations and conservative admission; no file deletion."""

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

GIB = 1024**3


def parse_quota(text, device, now=None):
    now = time.time() if now is None else now
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] != device or len(parts) < 5:
            continue
        try:
            used, soft, hard, grace = [int(value.strip("*+")) for value in parts[1:5]]
        except ValueError:
            continue
        limits = [hard * 1024] if hard else []
        if soft and used >= soft and (not grace or grace <= now):
            limits.append(soft * 1024)
        limit = min(limits) if limits else None
        return {
            "device": device,
            "used_bytes": used * 1024,
            "soft_limit_bytes": soft * 1024,
            "hard_limit_bytes": hard * 1024,
            "grace_expires_epoch": grace or None,
            "effective_limit_bytes": limit,
            "available_bytes": max(0, limit - used * 1024) if limit else None,
        }
    return None


def quota_status(path):
    if not shutil.which("quota"):
        return {"available": False, "reason": "quota_command_unavailable"}
    try:
        result = subprocess.run(
            ["quota", "-w", "-p"],
            capture_output=True,
            text=True,
            timeout=3,
            env=os.environ | {"LC_ALL": "C"},
        )
        device_number = Path(path).stat().st_dev
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith("/dev/"):
                continue
            device = Path(parts[0])
            if device.exists() and device.stat().st_rdev == device_number:
                parsed = parse_quota(result.stdout, str(device))
                if parsed is not None:
                    return {"available": True, **parsed}
        return {"available": False, "reason": "no_matching_user_quota"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": type(exc).__name__}


def capacity(path):
    disk = shutil.disk_usage(path)
    quota = quota_status(path)
    free = disk.free
    if quota.get("available_bytes") is not None:
        free = min(free, quota["available_bytes"])
    return {"filesystem_free_bytes": disk.free, "available_bytes": free, "quota": quota}


def audit_usage(root):
    """Stat only trusted output paths; skip the agent workspace and all symlinks."""
    total = 0
    for directory, dirs, files, fd in os.fwalk(root, follow_symlinks=False):
        if Path(directory) == Path(root):
            dirs[:] = [name for name in dirs if name != "workspace"]
        for name in files:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            except FileNotFoundError:
                continue
    return total


def audit_reserve_bytes(config):
    """An admission estimate, never an audit size limit; accept queued old configs."""
    policy = config.get("storage", {})
    return policy.get("audit_reserve_gb", policy.get("audit_gb", 2)) * GIB


def admission(config, available, peer_reservations=()):
    storage = config.get("storage", {})
    workspace = config["hardware"]["workspace_gb"] * GIB
    audit = audit_reserve_bytes(config)
    margin = storage.get("minimum_free_gb", 2) * GIB
    required = workspace + audit + margin + sum(peer_reservations)
    report = available | {
        "workspace_limit_bytes": workspace,
        "audit_limit_bytes": None,
        "audit_limit_enforced": False,
        "audit_reserve_bytes": audit,
        "minimum_free_bytes": margin,
        "peer_reserved_bytes": sum(peer_reservations),
        "required_free_bytes": required,
        "admitted": available["available_bytes"] >= required,
        "reservation_scope": "workspace_limit_and_audit_estimate; audit_growth_temporary_memory_and_external_files_not_bounded",
    }
    return report
