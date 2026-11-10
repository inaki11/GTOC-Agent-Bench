"""Resolve Codex model metadata without sending a model turn or using fallback data."""

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path


def model_catalog(config, run):
    """Operator catalog takes precedence; a rich ChatGPT ModelInfo is otherwise used."""
    declared = config["model"].get("catalog_file")
    if declared:
        data = json.loads(Path(declared).read_text())
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("models"), list)
            or not all(isinstance(m, dict) for m in data["models"])
        ):
            raise ValueError(
                "model.catalog_file must contain a Codex JSON catalog with a models array"
            )
        source = "operator_catalog"
    else:
        path = Path(run) / "provider-model.json"
        info = json.loads(path.read_text()) if path.exists() else {}
        if "context_window" not in info or not (
            "model_messages" in info or "base_instructions" in info
        ):
            return None, "bundled_codex_catalog"
        data, source = {"models": [info]}, "provider_catalog"
    if not any(
        isinstance(m, dict) and m.get("slug") == config["model"]["id"] for m in data["models"]
    ):
        raise ValueError("The Codex catalog does not contain the exact requested model")
    selected = selected_metadata(config, data)
    minimum = selected.get("minimal_client_version")
    if minimum:

        def version(value):
            return tuple(int(v) for v in re.findall(r"\d+", value)[:3])

        if version(config["agent"]["version"]) < version(minimum):
            raise ValueError(
                f"Model metadata requires Codex >= {minimum}; the pinned client is {config['agent']['version']}"
            )
    return data, source


def selected_metadata(config, catalog):
    if catalog is None:
        return {}
    return next(m for m in catalog["models"] if m.get("slug") == config["model"]["id"])


def write_catalog(catalog, directory):
    path = Path(directory) / "model-catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    path.chmod(0o444)
    return str(path)


def check_compatibility(config, directory, catalog_path=None, executable="codex", identity=None):
    """Query the pinned CLI over stdio, including an ephemeral thread, never turn/start.

    The temporary CODEX_HOME has no accounts, plugins, workspace instructions or
    recovered conversations. The actual agent uses the same model configuration.
    """
    from .agents import launch_spec

    _, env = launch_spec(config, workspace=str(directory), model_catalog_path=catalog_path)
    kwargs = identity or {}
    if identity:
        for path in [Path(directory), *Path(directory).rglob("*")]:
            os.chown(path, identity["user"], identity["group"], follow_symlinks=False)
    selected = config["model"]["id"]
    proc = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=directory,
        **kwargs,
    )
    replies, errors = queue.Queue(), bytearray()

    def read():
        try:
            for line in proc.stdout:
                replies.put(json.loads(line))
        except (ValueError, OSError) as exc:
            replies.put({"error": {"message": str(exc)}})
        finally:
            replies.put(None)

    def stderr():
        for block in iter(lambda: proc.stderr.read(4096), b""):
            if len(errors) < 65536:
                errors.extend(block[: 65536 - len(errors)])

    readers = [
        threading.Thread(target=read, daemon=True),
        threading.Thread(target=stderr, daemon=True),
    ]
    for thread in readers:
        thread.start()
    deadline = time.monotonic() + 30

    def call(identifier, method, params):
        if time.monotonic() >= deadline:
            raise queue.Empty
        proc.stdin.write(
            (json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode()
        )
        proc.stdin.flush()
        while True:
            message = replies.get(timeout=max(0.001, deadline - time.monotonic()))
            if message is None:
                raise ValueError("Codex exited before the metadata preflight completed")
            if message.get("id") != identifier:
                continue
            if "error" in message:
                raise ValueError("Codex metadata preflight: " + str(message["error"]))
            return message["result"]

    report = {
        "model": selected,
        "reasoning": config["model"]["reasoning"],
        "model_turns": 0,
        "client_version": config["agent"]["version"],
        "status": "incompatible",
        "recognized": False,
    }
    try:
        call(
            1,
            "initialize",
            {
                "clientInfo": {"name": "gtoc-preflight", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        cursor, sequence, found = None, 2, None
        while True:
            result = call(
                sequence, "model/list", {"limit": 100, "includeHidden": True, "cursor": cursor}
            )
            found = next(
                (m for m in result["data"] if m.get("model", m.get("id")) == selected), None
            )
            cursor = result.get("nextCursor")
            if found or not cursor:
                break
            sequence += 1
        if not found:
            report["fallback_blocked"] = True
            raise ValueError(
                f"Codex {config['agent']['version']} has no metadata for {selected}; fallback is prohibited. Provide model.catalog_file or use a compatible pinned client"
            )
        report["recognized"] = True
        efforts = [m["reasoningEffort"] for m in found.get("supportedReasoningEfforts", [])]
        if config["model"]["reasoning"] not in efforts:
            raise ValueError(
                "Codex does not recognize the requested reasoning effort for this model"
            )
        thread = call(
            sequence + 1,
            "thread/start",
            {
                "model": selected,
                "modelProvider": "bench",
                "cwd": str(directory),
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "ephemeral": True,
            },
        )
        if (
            thread["model"] != selected
            or thread.get("reasoningEffort") != config["model"]["reasoning"]
        ):
            raise ValueError("Codex resolved different model/reasoning settings")
        report.update(status="compatible", recognized=True, supported_reasoning_efforts=efforts)
    except (ValueError, OSError, queue.Empty) as exc:
        report["error"] = str(exc) or "Codex metadata preflight timed out"
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=3)
        for thread in readers:
            thread.join(timeout=1)
    warnings = errors.decode(errors="replace")
    fallback = bool(
        re.search(
            r"Unknown model\s+"
            + re.escape(selected)
            + r"\s+is used|fallback model metadata.*"
            + re.escape(selected),
            warnings,
        )
    )
    if fallback:
        report.update(
            status="incompatible", error="Codex used fallback metadata for the requested model"
        )
    report["diagnostics"] = warnings[-16000:]
    report["fallback_selected_model"] = fallback
    if catalog_path:
        data = Path(catalog_path).read_bytes()
        metadata = selected_metadata(config, json.loads(data))
        report.update(
            catalog_sha256=hashlib.sha256(data).hexdigest(),
            context_window=metadata.get("context_window"),
            auto_compact_token_limit=metadata.get("auto_compact_token_limit"),
            compact_limit_source="catalog"
            if metadata.get("auto_compact_token_limit") is not None
            else "pinned_client_default",
            max_context_window=metadata.get("max_context_window"),
        )
    else:
        report.update(context_window=None, compact_limit_source="bundled_pinned_client_defaults")
    return report
