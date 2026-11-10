"""Small, strict configuration contract. No executable config or dotenv interpolation."""

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import ROOT

MAX_CPUS = 32
INFERENCE_TIMEOUTS = {"connect_seconds": 10, "read_seconds": 300, "attempt_seconds": 1800}


def inference_timeouts(provider):
    """Resolve legacy launch files too; effective values are recorded by the gateway."""
    return INFERENCE_TIMEOUTS | provider.get("timeouts", {})


def effective_cpus(requested, available):
    """Always enforce the benchmark ceiling, including old queued launch files."""
    positive(available, "container engine CPU count", integer=True)
    if requested is not None:
        positive(requested, "hardware.cpus")
    return min(MAX_CPUS, available, MAX_CPUS if requested is None else requested)


def pins():
    return json.loads((ROOT / "environments/agents.lock.json").read_text())["agents"]


def positive(value, name, integer=False):
    kind = int if integer else (int, float)
    if (
        isinstance(value, bool)
        or not isinstance(value, kind)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive {'integer' if integer else 'number'}")
    return value


def fields(obj, allowed, name):
    if not isinstance(obj, dict) or set(obj) - set(allowed.split()):
        raise ValueError(f"Invalid or unknown fields in {name}")


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text())
    fields(
        cfg,
        "schema_version purpose problem agent model provider environment hardware network budget recovery storage",
        "experiment",
    )
    if cfg.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    # Phase 1 deliberately does not certify experimental results as official.
    if cfg.get("purpose", "development") != "development":
        raise ValueError(
            "Phase 1 supports development runs; official certification is not implemented"
        )
    cfg["purpose"] = "development"
    from .problems import specification

    specification(cfg.get("problem"))
    fields(cfg.get("agent"), "id version", "agent")
    agent = cfg["agent"]
    if agent.get("id") not in pins():
        raise ValueError("agent.id must be one of: " + ", ".join(pins()))
    version = pins()[agent["id"]]["version"]
    if agent.get("version", version) != version:
        raise ValueError(f"This image pins {agent['id']} to {version}")
    agent["version"] = version
    fields(cfg.get("model"), "id reasoning max_output_tokens catalog_file", "model")
    if "catalog_file" in cfg["model"]:
        if (
            agent["id"] != "codex"
            or not isinstance(cfg["model"]["catalog_file"], str)
            or not cfg["model"]["catalog_file"].strip()
        ):
            raise ValueError("model.catalog_file is a path supported only for Codex")
        catalog = Path(cfg["model"]["catalog_file"]).expanduser()
        cfg["model"]["catalog_file"] = str((path.parent / catalog).resolve())
    if not isinstance(cfg["model"].get("id"), str) or not cfg["model"]["id"].strip():
        raise ValueError("A model ID is required; no automatic model selection")
    effort = cfg["model"].get("reasoning", "high")
    allowed = (
        {"low", "medium", "high", "xhigh", "max", "ultra"}
        if agent["id"] != "claude_code"
        else {"low", "medium", "high", "max"}
    )
    if effort not in allowed:
        raise ValueError("Unsupported reasoning label for this client")
    cfg["model"]["reasoning"] = effort
    fields(
        cfg.get("provider"),
        "id protocol base_url env_file api_key_variable auth_method profile auth_dir timeouts keepalive_seconds",
        "provider",
    )
    provider = cfg["provider"]
    auth_method = provider.setdefault("auth_method", "api_key")
    if provider.get("id") == "antigravity" and auth_method != "antigravity":
        raise ValueError(
            "provider.id: antigravity requires the explicit experimental antigravity auth method"
        )
    if auth_method not in {"api_key", "chatgpt", "claude_subscription", "antigravity"}:
        raise ValueError(
            "provider.auth_method must be api_key, chatgpt, claude_subscription or antigravity"
        )
    if auth_method == "claude_subscription":
        if "timeouts" in provider or "keepalive_seconds" in provider:
            raise ValueError(
                "Native Claude subscription transport is controlled by its official client"
            )
    else:
        fields(provider.get("timeouts", {}), " ".join(INFERENCE_TIMEOUTS), "provider.timeouts")
        provider["timeouts"] = inference_timeouts(provider)
        for key, value in provider["timeouts"].items():
            positive(value, "provider.timeouts." + key)
    if auth_method == "antigravity":
        interval = provider.setdefault("keepalive_seconds", 15)
        if type(interval) is not int or interval < 0:
            raise ValueError("provider.keepalive_seconds must be a nonnegative integer")
        if interval and interval >= provider["timeouts"]["read_seconds"]:
            raise ValueError("Antigravity keepalive_seconds must be less than the read timeout")
    elif "keepalive_seconds" in provider:
        raise ValueError("provider.keepalive_seconds is supported only by Antigravity")
    protocols = {
        "codex": {"responses"},
        "claude_code": {"anthropic"},
        "openhands": {"responses", "chat_completions", "anthropic"},
        "swe_agent": {"chat_completions", "anthropic"},
        "mini_swe_agent": {"chat_completions", "anthropic"},
        "antigravity": {"chat_completions"},
    }[agent["id"]]
    if provider.get("protocol") not in protocols:
        raise ValueError(
            f"{agent['id']} requires {sorted(protocols)}; cross-protocol translation is not implemented"
        )
    if auth_method == "claude_subscription":
        if agent["id"] != "claude_code":
            raise ValueError("Claude subscriptions are supported only by official Claude Code")
        if any(k in provider for k in ("env_file", "api_key_variable", "base_url")):
            raise ValueError(
                "Native Claude subscription mode cannot use keys, gateways or base_url"
            )
        from .auth import auth_path, checked_profile

        provider["profile"] = checked_profile(provider.get("profile", "default"))
        directory = Path(provider.get("auth_dir", "~/.config/gtoc-bench/claude-code")).expanduser()
        provider["auth_dir"] = str(auth_path(path.parent / directory))
        if provider.get("id") != "anthropic":
            raise ValueError("Claude subscription mode requires provider.id: anthropic")
    if auth_method != "claude_subscription" and not provider.get("base_url"):
        raise ValueError("provider.base_url is required")
    url = urlsplit(provider.get("base_url", "https://api.anthropic.com"))
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "provider.base_url must be an HTTP(S) origin/base path without credentials"
        )
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", provider.get("id", "")):
        raise ValueError("provider.id is required")
    if "base_url" in provider:
        provider["base_url"] = provider["base_url"].rstrip("/")
    if auth_method == "chatgpt":
        from .auth import DEFAULT_AUTH_DIR, RESOURCE, auth_path, checked_profile

        if provider["base_url"] != RESOURCE or provider.get("id") != "openai":
            raise ValueError("ChatGPT login requires OpenAI and https://api.openai.com/v1")
        if "env_file" in provider or "api_key_variable" in provider:
            raise ValueError("ChatGPT profiles cannot contain API-key credentials or fallbacks")
        if cfg["model"].get("max_output_tokens") is not None:
            raise ValueError("ChatGPT plan inference does not support model.max_output_tokens")
        cfg["model"]["max_output_tokens"] = None
        provider["profile"] = checked_profile(provider.get("profile", "default"))
        directory = Path(provider.get("auth_dir", DEFAULT_AUTH_DIR)).expanduser()
        provider["auth_dir"] = str(auth_path(path.parent / directory))
    elif auth_method == "antigravity":
        from .antigravity import DEFAULT_DIR
        from .auth import auth_path, checked_profile

        if (
            provider.get("id") != "antigravity"
            or url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.path != "/v1"
            or not url.port
        ):
            raise ValueError(
                "Antigravity requires provider.id: antigravity and a loopback http://127.0.0.1:PORT/v1 endpoint"
            )
        if any(k in provider for k in ("env_file", "api_key_variable")):
            raise ValueError("Antigravity uses a private host profile, without API-key fallbacks")
        provider["profile"] = checked_profile(provider.get("profile", "default"))
        directory = Path(provider.get("auth_dir", DEFAULT_DIR)).expanduser()
        provider["auth_dir"] = str(auth_path(path.parent / directory))
        cfg["model"]["max_output_tokens"] = positive(
            cfg["model"].get("max_output_tokens", 16384), "max_output_tokens", True
        )
    elif auth_method == "claude_subscription":
        cfg["model"]["max_output_tokens"] = positive(
            cfg["model"].get("max_output_tokens", 16384), "max_output_tokens", True
        )
    else:
        if "profile" in provider or "auth_dir" in provider:
            raise ValueError("API-key providers cannot contain ChatGPT profile settings")
        cfg["model"]["max_output_tokens"] = positive(
            cfg["model"].get("max_output_tokens", 16384), "max_output_tokens", True
        )
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", provider.get("api_key_variable", "")):
            raise ValueError("provider.api_key_variable is required")
        env = Path(provider.get("env_file", "")).expanduser()
        if not str(provider.get("env_file", "")):
            raise ValueError("provider.env_file is required")
        provider["env_file"] = str((path.parent / env).resolve())
    # Old profiles used audit_gb as a hard stop. It is now only an admission
    # estimate, including when a queued profile still uses the old spelling.
    storage = cfg.get("storage", {})
    fields(storage, "audit_gb audit_reserve_gb minimum_free_gb", "storage")
    if "audit_gb" in storage:
        if "audit_reserve_gb" in storage:
            raise ValueError("Use only storage.audit_reserve_gb (legacy: audit_gb)")
        storage["audit_reserve_gb"] = storage.pop("audit_gb")
    defaults = {
        "environment": {"image": "auto"},
        "hardware": {
            "cpus": MAX_CPUS,
            "memory_gb": None,
            "gpus": "auto",
            "pids_limit": 2048,
            "workspace_gb": 8,
            "concurrent_with": None,
        },
        "network": {"policy": "docs_and_packages"},
        "budget": {"wall_seconds": 3600, "token_limit": None},
        "storage": {"audit_reserve_gb": 2, "minimum_free_gb": 2},
        "recovery": {
            "clock_policy": "continuous_wall"
            if provider.get("auth_method") == "claude_subscription"
            else "provider_wait",
            "max_transient_retries": 2,
            "max_retry_wait_seconds": 120,
            "provider_wait_seconds": 3600,
            "provider_retry_interval_seconds": 300,
        },
    }
    for section, default in defaults.items():
        fields(cfg.get(section, {}), " ".join(default), section)
        cfg[section] = default | cfg.get(section, {})
    if cfg["network"]["policy"] != "docs_and_packages" or cfg["recovery"]["clock_policy"] not in {
        "continuous_wall",
        "provider_wait",
    }:
        raise ValueError("Use docs_and_packages and a continuous_wall or provider_wait clock")
    if cfg["hardware"]["gpus"] not in {"auto", "none", "all"}:
        raise ValueError("hardware.gpus must be auto, none or all")
    peer = cfg["hardware"]["concurrent_with"]
    if peer is not None:
        peers = [peer] if isinstance(peer, str) else peer
        if (
            not isinstance(peers, list)
            or not peers
            or any(
                not isinstance(value, str) or not re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{12}", value)
                for value in peers
            )
            or len(peers) != len(set(peers))
        ):
            raise ValueError(
                "hardware.concurrent_with must name a run ID or a nonempty list of distinct run IDs"
            )
        if cfg["hardware"]["gpus"] != "none":
            raise ValueError(
                "Concurrent evaluations currently support CPU-only runs; set hardware.gpus: none explicitly"
            )
    for key in ("cpus", "memory_gb", "workspace_gb", "pids_limit"):
        if cfg["hardware"][key] is not None:
            positive(cfg["hardware"][key], key, key == "pids_limit")
    if cfg["hardware"]["cpus"] is not None and cfg["hardware"]["cpus"] > MAX_CPUS:
        raise ValueError(f"hardware.cpus cannot exceed {MAX_CPUS} logical CPUs")
    positive(cfg["budget"]["wall_seconds"], "wall_seconds")
    for key in ("audit_reserve_gb", "minimum_free_gb"):
        positive(cfg["storage"][key], "storage." + key)
    if cfg["budget"]["token_limit"] is not None:
        positive(cfg["budget"]["token_limit"], "token_limit", True)
    retries = cfg["recovery"]["max_transient_retries"]
    if type(retries) is not int or not 0 <= retries <= 5:
        raise ValueError("max_transient_retries must be between 0 and 5")
    positive(cfg["recovery"]["max_retry_wait_seconds"], "max_retry_wait_seconds")
    for field in ("provider_wait_seconds", "provider_retry_interval_seconds"):
        positive(cfg["recovery"][field], field)
    if (
        cfg["recovery"]["provider_retry_interval_seconds"]
        > cfg["recovery"]["provider_wait_seconds"]
    ):
        raise ValueError("Provider retry interval cannot exceed its recovery window")
    if not isinstance(cfg["environment"]["image"], str) or not cfg["environment"]["image"]:
        raise ValueError("An image is required")
    if (
        provider.get("auth_method") == "claude_subscription"
        and cfg["recovery"]["clock_policy"] == "provider_wait"
    ):
        raise ValueError(
            "Native Claude subscription retries are controlled by the official client; use continuous_wall"
        )
    return cfg


def public_config(cfg):
    result = copy.deepcopy(cfg)
    result["provider"].pop("env_file", None)
    result["provider"].pop("auth_dir", None)
    return result


def configuration_id(cfg, resolved=None):
    identity = {"config": public_config(cfg), "resolved": resolved or {}}
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def load_key(cfg):
    path = Path(cfg["provider"]["env_file"])
    if path.is_relative_to(ROOT) or path.is_symlink():
        raise ValueError(
            "Keep provider .env files outside the repository; symlinks are not accepted"
        )
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Provider .env must be private (chmod 600)")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError("Invalid .env entry")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    key = values.get(cfg["provider"]["api_key_variable"])
    if not key or any(c in key for c in "\r\n"):
        raise ValueError("Required provider credential is missing or invalid")
    return key
