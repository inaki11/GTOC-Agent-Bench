"""Keep the official host client alive only while its controller sends heartbeats."""

import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time


def main():
    spec = json.loads(sys.stdin.buffer.readline(1024**2))
    process = subprocess.Popen(
        spec["command"],
        env=spec["env"],
        cwd=spec["cwd"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        umask=0o077,
    )
    process.stdin.write(spec["prompt"].encode())
    process.stdin.close()
    stopped, lock = threading.Event(), threading.Lock()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    last_heartbeat = [time.monotonic()]

    def control():
        for line in sys.stdin.buffer:
            if line.strip() == b"heartbeat":
                last_heartbeat[0] = time.monotonic()
        stopped.set()

    def emit(frame):
        with lock:
            print(json.dumps(frame), flush=True)

    def drain(channel):
        stream = getattr(process, channel)
        while chunk := stream.read1(16384):
            emit(
                {
                    "type": "agent_output",
                    "channel": channel,
                    "body": base64.b64encode(chunk).decode(),
                }
            )

    readers = [
        threading.Thread(target=drain, args=(channel,), daemon=True)
        for channel in ("stdout", "stderr")
    ]
    threading.Thread(target=control, daemon=True).start()
    for reader in readers:
        reader.start()
    try:
        while process.poll() is None:
            if stopped.wait(0.25) or time.monotonic() - last_heartbeat[0] > 10:
                break
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        # The client may have exited before its MCP subprocesses.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.join(timeout=2)
        emit({"type": "agent_exit", "returncode": process.returncode})


if __name__ == "__main__":
    main()
