"""Local deterministic HTTP provider for integration tests, with no API expenditure."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def tool_events(body, command, anthropic):
    """Ask the real client to execute a shell tool using its advertised schema."""
    if anthropic:
        tools = body["tools"]
        tool = next((t for t in tools if command is None and t["name"] == "finish"), None)
        if tool is None:
            tool = next(
                t for t in tools if t["name"] in {"Bash", "mcp__gtoc__exec", "terminal", "bash"}
            )
        if command is None and tool["name"] == "bash":
            command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        args = (
            (
                {"message": "Integration complete."}
                if "message" in tool["input_schema"].get("properties", {})
                else {}
            )
            if tool["name"] == "finish"
            else {"command": command, "timeout": 60000}
            if tool["name"] == "Bash"
            else {"command": command}
            if tool["name"] in {"terminal", "bash"}
            else {
                "command": command,
                "timeout_seconds": 60,
            }
        )
        return [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool_test",
                    "name": tool["name"],
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(args),
                },
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
            {"type": "message_stop"},
        ]
    tools = list(body.get("tools", []))
    for entry in body.get("input", []):
        if isinstance(entry, dict) and entry.get("type") == "additional_tools":
            tools.extend(entry.get("tools", []))
    namespace = next((tool for tool in tools if tool.get("name") == "functions"), None)
    if namespace and any(tool.get("name") == "exec" for tool in namespace.get("tools", [])):
        item = {
            "id": "ctc_test",
            "type": "custom_tool_call",
            "name": "exec",
            "namespace": "functions",
            "call_id": "call_test",
            "input": "text(await tools.exec_command("
            + json.dumps({"cmd": command, "yield_time_ms": 30000, "max_output_tokens": 2000})
            + "));",
            "status": "completed",
        }
    else:
        item = shell_call(tools, command)
    response = {
        "id": "resp_tool",
        "object": "response",
        "created_at": int(time.time()),
        "model": body["model"],
        "status": "completed",
        "output": [item],
        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        "service_tier": "default",
    }
    return [
        {
            "type": "response.created",
            "response": response | {"status": "in_progress", "output": []},
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": item | {"status": "in_progress"},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]


def shell_call(tools, command):
    tool = next(
        tool
        for tool in tools
        if tool.get("name")
        in {
            "exec_command",
            "shell_command",
            "shell",
            "terminal",
            "finish",
            "Bash",
            "bash",
            "run_command",
        }
        and ((command is None) == (tool.get("name") == "finish"))
    )
    name = tool["name"]
    arguments = (
        (
            {"message": "Integration complete."}
            if "message" in tool.get("parameters", {}).get("properties", {})
            else {}
        )
        if name == "finish"
        else {
            "CommandLine": command,
            "Cwd": "/workspace",
            "WaitMsBeforeAsync": 10000,
            "toolSummary": "Integration test",
            "toolAction": "Running integration test",
        }
        if name == "run_command"
        else {"command": command, "timeout": 60000}
        if name == "Bash"
        else {"command": command}
        if name in {"terminal", "bash"}
        else {"cmd": command, "yield_time_ms": 30000, "max_output_tokens": 2000}
        if name == "exec_command"
        else {"command": command, "timeout_ms": 60000}
        if name == "shell_command"
        else {"command": ["bash", "-lc", command], "timeout_ms": 60000}
    )
    return {
        "id": "fc_test",
        "type": "function_call",
        "name": name,
        "call_id": "call_test",
        "arguments": json.dumps(arguments),
        "status": "completed",
    }


class FakeProvider:
    def __init__(
        self,
        block=False,
        tool_command=None,
        error_status=None,
        cut_streams=0,
        cut_after_tool=False,
        overload_after=None,
        overload_retry_after=None,
        hold_after=None,
        idle_after=None,
    ):
        self.calls = []
        self.cut_streams = cut_streams
        self.cut_after_tool = cut_after_tool
        self.cuts_sent = 0
        self.tool_sent = False
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.hold_release = threading.Event()
        if not block:
            self.release.set()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.calls.append(
                    {"path": self.path, "body": body, "headers": dict(self.headers)}
                )
                if "count_tokens" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":10}')
                    return
                provider.arrived.set()
                provider.release.wait(90)
                if idle_after is not None and len(provider.calls) == idle_after + 1:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b": synthetic keepalive\n\n")
                    self.wfile.flush()
                    provider.hold_release.wait(90)
                    return
                if hold_after is not None and len(provider.calls) > hold_after:
                    provider.hold_release.wait(120)
                if overload_after is not None and len(provider.calls) == overload_after + 1:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    error = {
                        "type": "service_unavailable_error",
                        "code": "server_is_overloaded",
                        "message": "Synthetic overload",
                    }
                    if overload_retry_after is not None:
                        error["headers"] = {"retry-after": str(overload_retry_after)}
                    self.wfile.write(
                        ("data: " + json.dumps({"type": "error", "error": error}) + "\n\n").encode()
                    )
                    self.wfile.flush()
                    return
                if error_status:
                    self.send_response(error_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "type": "rate_limit_error",
                                    "message": "Synthetic provider unavailable",
                                },
                            }
                        ).encode()
                    )
                    return
                if self.path.endswith("/chat/completions"):
                    tools = [t["function"] for t in body.get("tools", [])]
                    if tool_command and not provider.tool_sent:
                        tool = next(
                            t
                            for t in tools
                            if t["name"] in {"bash", "terminal", "execute_bash", "run_command"}
                        )
                        arguments = {"command": tool_command}
                        if tool["name"] == "run_command":
                            arguments = {
                                "CommandLine": tool_command,
                                "Cwd": "/workspace",
                                "WaitMsBeforeAsync": 10000,
                                "toolSummary": "Integration test",
                                "toolAction": "Running integration test",
                            }
                        provider.tool_sent = True
                    else:
                        tool = next((t for t in tools if t["name"] == "finish"), None)
                        if tool:
                            arguments = (
                                {"message": "Integration complete."}
                                if "message" in tool.get("parameters", {}).get("properties", {})
                                else {}
                            )
                        elif any(t["name"] == "bash" for t in tools):
                            tool = next(t for t in tools if t["name"] == "bash")
                            arguments = {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}
                    message = {
                        "role": "assistant",
                        "content": "Integration test.",
                        "tool_calls": [
                            {
                                "id": "call_" + str(len(provider.calls)),
                                "type": "function",
                                "function": {
                                    "name": tool["name"],
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ]
                        if tool
                        else [],
                    }
                    completion = {
                        "id": "chat_" + str(len(provider.calls)),
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body["model"],
                        "service_tier": "default",
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": "tool_calls"}
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "total_tokens": 110,
                        },
                    }
                    if tool is None:
                        message.pop("tool_calls", None)
                        completion["choices"][0]["finish_reason"] = "stop"
                    if body.get("stream"):
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        delta = dict(message)
                        if delta.get("tool_calls"):
                            delta["tool_calls"] = [
                                dict(t, index=i) for i, t in enumerate(delta["tool_calls"])
                            ]
                        base = {
                            k: v for k, v in completion.items() if k not in {"choices", "usage"}
                        }
                        base["object"] = "chat.completion.chunk"
                        events = [
                            base
                            | {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                            base
                            | {
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": completion["choices"][0]["finish_reason"],
                                    }
                                ]
                            },
                            base | {"choices": [], "usage": completion["usage"]},
                        ]
                        for event in events:
                            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(completion).encode())
                    return
                if "messages" in self.path:
                    events = [
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_test",
                                "type": "message",
                                "role": "assistant",
                                "model": body["model"],
                                "content": [],
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 100, "output_tokens": 0},
                            },
                        },
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Integration test complete."},
                        },
                        {"type": "content_block_stop", "index": 0},
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 10},
                        },
                        {"type": "message_stop"},
                    ]
                else:
                    part = {
                        "type": "output_text",
                        "text": "Integration test complete.",
                        "annotations": [],
                    }
                    item = {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [part],
                    }
                    response = {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": int(time.time()),
                        "model": body["model"],
                        "status": "completed",
                        "output": [item],
                        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                        "service_tier": "default",
                    }
                    events = [
                        {
                            "type": "response.created",
                            "response": response | {"status": "in_progress", "output": []},
                        },
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": item | {"status": "in_progress", "content": []},
                        },
                        {
                            "type": "response.content_part.added",
                            "item_id": "msg_test",
                            "output_index": 0,
                            "content_index": 0,
                            "part": part | {"text": ""},
                        },
                        {
                            "type": "response.output_text.delta",
                            "item_id": "msg_test",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": part["text"],
                        },
                        {
                            "type": "response.output_text.done",
                            "item_id": "msg_test",
                            "output_index": 0,
                            "content_index": 0,
                            "text": part["text"],
                        },
                        {
                            "type": "response.content_part.done",
                            "item_id": "msg_test",
                            "output_index": 0,
                            "content_index": 0,
                            "part": part,
                        },
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": response},
                    ]
                if tool_command and not provider.tool_sent:
                    provider.tool_sent = True
                    events = tool_events(body, tool_command, "messages" in self.path)
                elif "responses" in self.path and any(
                    t.get("name") == "finish" for t in body.get("tools", [])
                ):
                    events = tool_events(body, None, False)
                elif "responses" in self.path and any(
                    t.get("name") == "bash" for t in body.get("tools", [])
                ):
                    events = tool_events(body, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", False)
                elif "messages" in self.path and any(
                    t.get("name") in {"finish", "bash"} for t in body.get("tools", [])
                ):
                    events = tool_events(body, None, True)
                if provider.cuts_sent < provider.cut_streams and "responses" in self.path:
                    provider.cuts_sent += 1
                    if provider.cut_after_tool:
                        events = events[:-1]  # Tool delivered, terminal event missing.
                    else:
                        events = events[:2]  # Incomplete tool must never execute.
                        provider.tool_sent = False
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if body.get("stream") else "application/json",
                )
                self.end_headers()
                if not body.get("stream") and "responses" in self.path:
                    self.wfile.write(json.dumps(events[-1]["response"]).encode())
                    return
                if not body.get("stream") and "messages" in self.path:
                    message = events[0]["message"].copy()
                    block = next(
                        e["content_block"].copy()
                        for e in events
                        if e["type"] == "content_block_start"
                    )
                    delta = next(e["delta"] for e in events if e["type"] == "content_block_delta")
                    if block["type"] == "tool_use":
                        block["input"] = json.loads(delta["partial_json"])
                    else:
                        block["text"] = delta["text"]
                    message["content"] = [block]
                    message["stop_reason"] = events[-2]["delta"]["stop_reason"]
                    message["usage"] = {"input_tokens": 100, "output_tokens": 10}
                    self.wfile.write(json.dumps(message).encode())
                    return
                try:
                    for sequence, event in enumerate(events):
                        event["sequence_number"] = sequence
                        self.wfile.write(
                            (
                                "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                            ).encode()
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.release.set()
        self.hold_release.set()
        self.server.shutdown()
        self.server.server_close()
