"""MCP exposes one container shell, with no host filesystem or Docker arguments from the model."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

from .agents import cache_environment
from .audit import sanitize
from .storage import utc_now

MAX_OUTPUT = 1024**2
TOOL = {
    "name": "exec",
    "description": "Execute a bash command inside the isolated Linux evaluation container. "
    "Read and write files there; use gtoc status and gtoc evaluate to monitor progress.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def arguments(value):
    if not isinstance(value, dict) or set(value) - {"command", "timeout_seconds"}:
        raise ValueError("Invalid tool arguments")
    if not isinstance(value.get("command"), str) or len(value["command"]) > 65536:
        raise ValueError("Invalid command")
    timeout = value.get("timeout_seconds", 120)
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise ValueError("timeout_seconds must be between 1 and 900")
    return value["command"], timeout


def execute_inside(value):
    command, timeout = arguments(value)
    env = {
        "HOME": "/workspace",
        **cache_environment(),
        "PATH": "/workspace/.local/bin:/opt/conda/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": "/opt/gtoc/src",
        "GTOC_BENCH_ROOT": "/opt/gtoc",
        "GTOC_ENDPOINT": "http://127.0.0.1:8765",
        "LANG": "C.UTF-8",
        "PYTHONUSERBASE": "/workspace/.local",
        "PIP_INDEX_URL": "http://127.0.0.1:8765/packages/simple/",
        "PIP_TRUSTED_HOST": "127.0.0.1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PKG_CONFIG_PATH": "/opt/conda/lib/pkgconfig",
    }
    for key in (
        "LD_LIBRARY_PATH",
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "KMP_AFFINITY",
        "GTOC_PROBLEM",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    process = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-c", command],
        env=env,
        cwd="/workspace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    output = bytearray()
    truncated = [False]

    def drain():
        while chunk := os.read(process.stdout.fileno(), 16384):
            remaining = MAX_OUTPUT - len(output)
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[0] = True

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    reader.join(timeout=1)
    return {
        "returncode": process.returncode,
        "output": output.decode(errors="replace"),
        "truncated": truncated[0],
        "timed_out": timed_out,
    }


def serve(container, audit_path=None, started=None):
    def record(kind, **data):
        if audit_path is None:
            return
        event = sanitize(
            {
                "schema_version": 1,
                "utc": utc_now(),
                "elapsed_seconds": max(0, time.monotonic() - started),
                "type": kind,
                "source": "trusted_mcp_server",
                **data,
            }
        )
        with open(audit_path, "ab", buffering=0) as stream:
            stream.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            os.fsync(stream.fileno())

    for line in sys.stdin.buffer:
        request = {}
        call_id = None
        try:
            if len(line) > 1024**2:
                raise ValueError("Oversized MCP message")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("MCP messages must be JSON objects")
            request = value
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "gtoc-container", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                params = request["params"]
                if not isinstance(params, dict):
                    raise ValueError("Invalid MCP parameters")
                if params.get("name") != "exec":
                    raise ValueError("Unknown tool")
                _, timeout = arguments(params.get("arguments"))
                call_id = uuid.uuid4().hex
                record("tool_started", call_id=call_id, tool="exec", arguments=params["arguments"])
                process = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        "--user",
                        "1000:1000",
                        container,
                        "/opt/conda/bin/python",
                        "-m",
                        "gtoc_bench.container_tools",
                        "--inside",
                    ],
                    input=json.dumps(params["arguments"]).encode(),
                    capture_output=True,
                    timeout=timeout + 10,
                )
                if process.returncode:
                    raise ValueError("Container tool unavailable")
                record("tool_finished", call_id=call_id, result=json.loads(process.stdout))
                result = {"content": [{"type": "text", "text": process.stdout.decode()}]}
            else:
                raise ValueError("Unsupported MCP method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ValueError, KeyError, subprocess.SubprocessError) as exc:
            if call_id:
                record("tool_error", call_id=call_id, error=str(exc))
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32602, "message": str(exc)},
            }
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--inside":
        print(json.dumps(execute_inside(json.load(sys.stdin))))
    else:
        serve(
            sys.argv[1],
            sys.argv[2] if len(sys.argv) > 2 else None,
            float(sys.argv[3]) if len(sys.argv) > 3 else time.monotonic(),
        )
