"""Experimental text/function-tool adapters to the public ChatGPT Responses endpoint.

The upstream stream remains the authoritative source of usage. Other client
protocols receive a completed response; no inference or agent loop is added.
"""

import copy
import json
import time

ADAPTER = "chatgpt-responses-v3"


def text_content(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or any(
        p.get("type") not in {"text", "input_text", "output_text"} for p in value
    ):
        raise ValueError("The ChatGPT protocol adapter supports only text and local function tools")
    return "\n".join(p["text"] for p in value)


class ChatGPTAdapter:
    def __init__(self):
        self.reasoning = {}

    def request(self, body, protocol):
        if protocol == "responses":
            result = copy.deepcopy(body)
            if isinstance(result.get("input"), str):
                result["input"] = [{"role": "user", "content": result["input"]}]
            for item in result.get("input", []):
                if item.get("role") == "system":
                    item["role"] = "developer"
        else:
            result = {"model": body["model"], "input": [], "tools": []}
            history = result["input"]
            if body.get("system"):
                history.append({"role": "developer", "content": text_content(body["system"])})
            for message in body.get("messages", []):
                role = message["role"]
                if role == "tool":
                    history.append(
                        {
                            "type": "function_call_output",
                            "call_id": message["tool_call_id"],
                            "output": text_content(message.get("content")),
                        }
                    )
                    continue
                if protocol == "anthropic" and isinstance(message.get("content"), list):
                    for block in message["content"]:
                        kind = block["type"]
                        if kind == "text":
                            history.append(
                                {
                                    "role": "developer" if role == "system" else role,
                                    "content": block["text"],
                                }
                            )
                        elif kind == "tool_use":
                            self.add_call(
                                history, block["id"], block["name"], json.dumps(block["input"])
                            )
                        elif kind == "tool_result":
                            output = text_content(block.get("content"))
                            if block.get("is_error"):
                                output = "Tool error:\n" + output
                            history.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": block["tool_use_id"],
                                    "output": output,
                                }
                            )
                        else:
                            raise ValueError(
                                "Unsupported content in ChatGPT protocol adapter: " + kind
                            )
                else:
                    content = text_content(message.get("content"))
                    if content:
                        history.append(
                            {"role": "developer" if role == "system" else role, "content": content}
                        )
                    for call in message.get("tool_calls") or []:
                        self.add_call(
                            history,
                            call["id"],
                            call["function"]["name"],
                            call["function"]["arguments"],
                        )
            for tool in body.get("tools", []):
                definition = tool["function"] if protocol == "chat_completions" else tool
                result["tools"].append(
                    {
                        "type": "function",
                        "name": definition["name"],
                        "description": definition.get("description", ""),
                        "parameters": definition.get(
                            "input_schema",
                            definition.get("parameters", {"type": "object", "properties": {}}),
                        ),
                        "strict": False,
                    }
                )
            choice = body.get("tool_choice")
            if choice:
                if isinstance(choice, str):
                    result["tool_choice"] = choice
                elif protocol == "anthropic":
                    result["tool_choice"] = (
                        {"type": "function", "name": choice["name"]}
                        if choice["type"] == "tool"
                        else {"any": "required", "auto": "auto", "none": "none"}[choice["type"]]
                    )
                    if choice.get("disable_parallel_tool_use"):
                        result["parallel_tool_calls"] = False
                else:
                    result["tool_choice"] = {"type": "function", "name": choice["function"]["name"]}
            if "parallel_tool_calls" in body:
                result["parallel_tool_calls"] = body["parallel_tool_calls"]
        # These controls are not available on ChatGPT plan inference. The run's
        # declared effort, standard speed and soft token budget apply instead.
        for key in (
            "max_output_tokens",
            "temperature",
            "top_p",
            "prompt_cache_retention",
            "truncation",
            "user",
        ):
            result.pop(key, None)
        result.update(store=False, stream=True)
        result["include"] = list(
            dict.fromkeys(result.get("include", []) + ["reasoning.encrypted_content"])
        )
        return result

    def add_call(self, history, call_id, name, arguments):
        history.extend(copy.deepcopy(self.reasoning.get(call_id, [])))
        history.append(
            {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
        )

    def remember(self, response):
        pending = []
        for item in response.get("output", []):
            if item.get("type") == "reasoning" and item.get("encrypted_content"):
                pending.append(item)
            elif item.get("type") == "function_call":
                self.reasoning[item["call_id"]] = pending
                pending = []

    def output(self, response, protocol):
        self.remember(response)
        blocks, calls, texts = [], [], []
        for item in response.get("output", []):
            if item["type"] == "message":
                for part in item["content"]:
                    if part["type"] == "output_text":
                        texts.append(part["text"])
                        blocks.append({"type": "text", "text": part["text"]})
                    elif part["type"] == "refusal":
                        texts.append(part["refusal"])
                        blocks.append({"type": "text", "text": part["refusal"]})
                    else:
                        raise ValueError("Unsupported Responses message content")
            elif item["type"] == "function_call":
                arguments = item["arguments"]
                calls.append(
                    {
                        "id": item["call_id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": arguments},
                    }
                )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": item["call_id"],
                        "name": item["name"],
                        "input": json.loads(arguments),
                    }
                )
            elif item["type"] != "reasoning":
                raise ValueError("Unsupported Responses output item")
        usage = response.get("usage") or {}
        incoming, outgoing = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        if protocol == "chat_completions":
            return {
                "id": response["id"],
                "object": "chat.completion",
                "created": int(time.time()),
                "model": response["model"],
                "service_tier": response.get("service_tier"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if calls else "stop",
                        "message": {
                            "role": "assistant",
                            "content": "\n".join(texts) or None,
                            "tool_calls": calls or None,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": incoming,
                    "completion_tokens": outgoing,
                    "total_tokens": incoming + outgoing,
                    "prompt_tokens_details": {"cached_tokens": cached},
                    "completion_tokens_details": usage.get("output_tokens_details") or {},
                },
            }
        return {
            "id": response["id"],
            "type": "message",
            "role": "assistant",
            "model": response["model"],
            "content": blocks,
            "stop_reason": "tool_use" if calls else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": incoming - cached,
                "output_tokens": outgoing,
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": 0,
            },
        }

    def receiver(self, protocol, streaming, emit):
        if protocol == "responses" and streaming:
            return emit
        pending, terminal = bytearray(), [None]
        completed_items = {}

        def receive(status, headers, data, end):
            if status >= 400:
                emit(status, headers, data, end)
                return
            pending.extend(data)
            if len(pending) > 16 * 1024**2:
                raise ValueError("Oversized protocol conversion buffer")
            while b"\n\n" in pending:
                block, rest = pending.split(b"\n\n", 1)
                pending[:] = rest
                for line in block.splitlines():
                    if line.startswith(b"data: ") and line != b"data: [DONE]":
                        event = json.loads(line[6:])
                        if event.get("type") == "response.output_item.done":
                            completed_items[event["output_index"]] = event["item"]
                        if event.get("type") in {
                            "response.completed",
                            "response.incomplete",
                            "response.failed",
                            "error",
                        }:
                            terminal[0] = event
            if not end:
                return
            event = terminal[0]
            if not event or event.get("type") != "response.completed":
                error = (event or {}).get("error") or (event or {}).get("response", {}).get("error")
                if isinstance(error, dict) and error.get("code") in {
                    "context_length_exceeded",
                    "context_window_exceeded",
                }:
                    result = {"error": error}
                    if protocol == "anthropic":
                        result["type"] = "error"
                    emit(
                        400, {"Content-Type": "application/json"}, json.dumps(result).encode(), True
                    )
                    return
                emit(
                    502,
                    {"Content-Type": "application/json"},
                    b'{"error":{"type":"api_error","message":"ChatGPT response did not complete"}}',
                    True,
                )
                return
            response = copy.deepcopy(event["response"])
            # Some plan streams finish with output=[], despite having emitted
            # complete tool/message items. Preserve those items for conversion.
            output = dict(enumerate(response.get("output") or []))
            output.update(completed_items)
            response["output"] = [output[index] for index in sorted(output)]
            result = response if protocol == "responses" else self.output(response, protocol)
            if not streaming:
                emit(200, {"Content-Type": "application/json"}, json.dumps(result).encode(), True)
                return
            if protocol == "chat_completions":
                choice = result["choices"][0]
                delta = choice["message"]
                for i, call in enumerate(delta.get("tool_calls") or []):
                    call["index"] = i
                chunks = [
                    result
                    | {
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}
                        ],
                        "usage": None,
                    },
                    result
                    | {"object": "chat.completion.chunk", "choices": [], "usage": result["usage"]},
                ]
                encoded = (
                    b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
                    + b"data: [DONE]\n\n"
                )
            else:
                events = [
                    {
                        "type": "message_start",
                        "message": result
                        | {
                            "content": [],
                            "stop_reason": None,
                            "usage": result["usage"] | {"output_tokens": 0},
                        },
                    }
                ]
                for i, block in enumerate(result["content"]):
                    tool = block["type"] == "tool_use"
                    events.extend(
                        [
                            {
                                "type": "content_block_start",
                                "index": i,
                                "content_block": block | ({"input": {}} if tool else {"text": ""}),
                            },
                            {
                                "type": "content_block_delta",
                                "index": i,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": json.dumps(block["input"]),
                                }
                                if tool
                                else {"type": "text_delta", "text": block["text"]},
                            },
                            {"type": "content_block_stop", "index": i},
                        ]
                    )
                events.extend(
                    [
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": result["stop_reason"], "stop_sequence": None},
                            "usage": {"output_tokens": result["usage"]["output_tokens"]},
                        },
                        {"type": "message_stop"},
                    ]
                )
                encoded = b"".join(
                    ("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n").encode()
                    for e in events
                )
            emit(200, {"Content-Type": "text/event-stream"}, encoded, True)

        return receive
