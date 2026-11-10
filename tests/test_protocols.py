import json

import pytest

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.gateway import validate_request
from gtoc_bench.protocols import ChatGPTAdapter


def response():
    return {
        "id": "r1",
        "model": "gpt-6-luna",
        "service_tier": "default",
        "output": [
            {
                "id": "rs1",
                "type": "reasoning",
                "summary": [],
                "encrypted_content": "opaque-test-reasoning",
            },
            {"type": "message", "content": [{"type": "output_text", "text": "Testing."}]},
            {
                "type": "function_call",
                "call_id": "call1",
                "name": "bash",
                "arguments": '{"command":"pwd"}',
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 70},
            "output_tokens_details": {"reasoning_tokens": 4},
        },
    }


@pytest.mark.parametrize("protocol", ["chat_completions", "anthropic"])
def test_function_round_trip_preserves_arguments_usage_and_reasoning(protocol):
    adapter = ChatGPTAdapter()
    converted = adapter.output(response(), protocol)
    if protocol == "chat_completions":
        message = converted["choices"][0]["message"]
        messages = [message, {"role": "tool", "tool_call_id": "call1", "content": "/workspace"}]
        assert converted["usage"]["total_tokens"] == 110
        assert converted["service_tier"] == "default"
    else:
        messages = [
            {"role": "assistant", "content": converted["content"]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call1", "content": "/workspace"}
                ],
            },
        ]
        assert converted["usage"]["input_tokens"] == 30
        assert converted["usage"]["cache_read_input_tokens"] == 70
    request = adapter.request({"model": "gpt-6-luna", "messages": messages}, protocol)
    assert request["store"] is False and request["stream"] is True
    assert any(i.get("encrypted_content") == "opaque-test-reasoning" for i in request["input"])
    calls = [i for i in request["input"] if i.get("type") == "function_call"]
    assert json.loads(calls[0]["arguments"]) == {"command": "pwd"}
    assert request["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call1",
        "output": "/workspace",
    }


@pytest.mark.parametrize(
    "protocol,streaming",
    [
        ("responses", False),
        ("chat_completions", False),
        ("chat_completions", True),
        ("anthropic", False),
        ("anthropic", True),
    ],
)
def test_completed_upstream_stream_is_converted_once(protocol, streaming):
    emitted = []
    callback = ChatGPTAdapter().receiver(protocol, streaming, lambda *args: emitted.append(args))
    data = (
        b"data: "
        + json.dumps({"type": "response.completed", "response": response()}).encode()
        + b"\n\n"
    )
    callback(200, {"Content-Type": "text/event-stream"}, b"", False)
    for i in range(0, len(data), 5):
        callback(200, {}, data[i : i + 5], False)
    assert not emitted
    callback(200, {}, b"", True)
    assert len(emitted) == 1 and emitted[0][0] == 200
    assert b"call1" in emitted[0][2] and b"pwd" in emitted[0][2]


@pytest.mark.parametrize(
    "protocol,streaming",
    [
        ("responses", False),
        ("chat_completions", False),
        ("chat_completions", True),
        ("anthropic", False),
        ("anthropic", True),
    ],
)
@pytest.mark.parametrize("terminal_output", ["empty", "partial", "complete"])
def test_stream_items_survive_missing_terminal_output(protocol, streaming, terminal_output):
    adapter, emitted = ChatGPTAdapter(), []
    callback = adapter.receiver(protocol, streaming, lambda *args: emitted.append(args))
    complete = response()
    events = [
        {"type": "response.output_item.done", "output_index": index, "item": item}
        for index, item in reversed(list(enumerate(complete["output"])))
    ]
    terminal = complete | {
        "output": complete["output"]
        if terminal_output == "complete"
        else complete["output"][:1]
        if terminal_output == "partial"
        else []
    }
    events.append({"type": "response.completed", "response": terminal})
    data = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
    for index in range(0, len(data), 7):
        callback(200, {}, data[index : index + 7], False)
    assert not emitted
    callback(200, {}, b"", True)
    assert len(emitted) == 1 and emitted[0][0] == 200
    body = emitted[0][2]
    if protocol == "responses":
        assert json.loads(body)["output"] == complete["output"]
    else:
        assert adapter.reasoning["call1"][0]["encrypted_content"] == "opaque-test-reasoning"
        assert body.count(b"call1") == 1 and b"pwd" in body and b"Testing." in body


def test_incomplete_stream_never_becomes_success():
    emitted = []
    receiver = ChatGPTAdapter().receiver("anthropic", True, lambda *args: emitted.append(args))
    receiver(
        200,
        {},
        b'data: {"type":"response.failed","response":{"error":{"code":"quota"}}}\n\n',
        False,
    )
    receiver(200, {}, b"", True)
    assert emitted[0][0] == 502


@pytest.mark.parametrize("protocol", ["responses", "chat_completions", "anthropic"])
@pytest.mark.parametrize("envelope", ["error", "response.failed"])
def test_context_error_reaches_client_with_original_diagnostic(protocol, envelope):
    error = {
        "type": "invalid_request_error",
        "code": "context_length_exceeded",
        "message": "Reduce the input context.",
        "param": "input",
    }
    event = {"type": envelope}
    event["error" if envelope == "error" else "response"] = (
        error if envelope == "error" else {"error": error}
    )
    emitted = []
    receiver = ChatGPTAdapter().receiver(protocol, False, lambda *args: emitted.append(args))
    receiver(200, {}, b"data: " + json.dumps(event).encode() + b"\n\n", False)
    receiver(200, {}, b"", True)
    assert len(emitted) == 1 and emitted[0][0] == 400
    assert json.loads(emitted[0][2])["error"] == error


def test_conversion_does_not_fabricate_standard_tier():
    adapter = ChatGPTAdapter()
    assert (
        adapter.output(response() | {"service_tier": "priority"}, "chat_completions")[
            "service_tier"
        ]
        == "priority"
    )
    missing = response()
    del missing["service_tier"]
    assert adapter.output(missing, "chat_completions")["service_tier"] is None


def test_conversion_does_not_enable_provider_tools_or_remote_images():
    cfg = load_config(ROOT / "configs/claude-code.yaml")
    for extra in (
        {"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": "https://example.org/private"},
                        }
                    ],
                }
            ]
        },
    ):
        with pytest.raises(ValueError):
            validate_request({"model": cfg["model"]["id"]} | extra, cfg)
    with pytest.raises(ValueError, match="Unsupported content"):
        ChatGPTAdapter().request(
            {
                "model": "gpt-6-luna",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "data": "AAAA"}}
                        ],
                    }
                ],
            },
            "anthropic",
        )
