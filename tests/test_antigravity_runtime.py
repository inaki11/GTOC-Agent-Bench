"""SDK stream fault injection without installing the SDK or spending model quota."""

import asyncio
from types import SimpleNamespace

import pytest

from gtoc_bench.antigravity_runtime import (
    RECOVERY_LIMIT,
    RECOVERY_PROMPT,
    AgentRuntimeInterrupted,
    run_session,
)


def step(index, *, final=False, source="MODEL", status="DONE", error="", depth=0):
    return SimpleNamespace(
        id=f"session:{index}",
        step_index=index,
        source=source,
        target="TARGET_USER" if final else "TARGET_ENVIRONMENT",
        status=status,
        type="TEXT_RESPONSE",
        depth=depth,
        is_complete_response=final,
        error=error,
        content="Public message",
        content_delta="Public message" if final else "",
    )


class Agent:
    conversation_id = "same-session"

    def __init__(self, turns):
        self.turns = iter(turns)
        self.prompts = []
        self.conversation = self

    async def chat(self, prompt):
        self.prompts.append(prompt)
        self.steps, reason = next(self.turns)
        return SimpleNamespace(stop_reason=reason)

    async def receive_steps(self):
        for event in self.steps:
            if isinstance(event, Exception):
                raise event
            yield event


def test_normal_finish_and_late_task_update_never_reprompt():
    agent = Agent([([step(2, final=True), step(1)], "UNSPECIFIED")])
    events = []
    asyncio.run(run_session(agent, "original", events.append))
    assert agent.prompts == ["original"]
    assert events[-1] == {"type": "session.completed", "conversation_id": "same-session"}
    assert not any(e["type"] == "session.recovery_requested" for e in events)


def test_missing_final_response_recovers_in_same_session_and_records_prompt():
    agent = Agent(
        [
            ([step(1), step(2, source="SYSTEM")], "UNSPECIFIED"),
            ([step(3, final=True)], "UNSPECIFIED"),
        ]
    )
    events = []
    asyncio.run(run_session(agent, "original", events.append))
    assert agent.prompts == ["original", RECOVERY_PROMPT]
    recoveries = [e for e in events if e["type"] == "session.recovery_requested"]
    assert len(recoveries) == 1
    assert recoveries[0]["conversation_id"] == "same-session"
    assert recoveries[0]["prompt"] == RECOVERY_PROMPT
    assert events[-1]["type"] == "session.completed"


def test_repeated_incomplete_turns_are_bounded_and_never_successful():
    agent = Agent([([step(1)], "UNSPECIFIED")] * (RECOVERY_LIMIT + 1))
    events = []
    with pytest.raises(AgentRuntimeInterrupted):
        asyncio.run(run_session(agent, "original", events.append))
    assert len(agent.prompts) == RECOVERY_LIMIT + 1
    assert events[-1]["type"] == "session.interrupted"
    assert not any(e["type"] == "session.completed" for e in events)


@pytest.mark.parametrize("reason", ["QUOTA_EXHAUSTED", "MAX_MODEL_CALLS_EXCEEDED"])
def test_sdk_limit_or_quota_is_reported_without_retry(reason):
    agent = Agent([([step(1)], reason)])
    events = []
    with pytest.raises(AgentRuntimeInterrupted):
        asyncio.run(run_session(agent, "original", events.append))
    assert agent.prompts == ["original"]
    assert events[-1]["sdk_stop_reason"] == reason


def test_fatal_system_error_is_not_a_success_or_an_automatic_recovery():
    agent = Agent(
        [
            (
                [
                    step(1, final=True),
                    step(2, source="SYSTEM", status="ERROR", error="backend failed"),
                ],
                "UNSPECIFIED",
            )
        ]
    )
    events = []
    with pytest.raises(AgentRuntimeInterrupted):
        asyncio.run(run_session(agent, "original", events.append))
    assert agent.prompts == ["original"]
    assert events[-2]["system_error"] == "backend failed"


def test_partial_stream_error_is_recorded_and_propagated_without_replay():
    agent = Agent([([step(1), RuntimeError("stream broke")], "UNSPECIFIED")])
    events = []
    with pytest.raises(RuntimeError, match="stream broke"):
        asyncio.run(run_session(agent, "original", events.append))
    assert agent.prompts == ["original"]
    assert events[-1]["type"] == "session.error"


def test_native_stop_hook_fatal_error_prevents_a_spurious_retry():
    report = {}

    class HookAgent(Agent):
        async def receive_steps(self):
            yield step(1)
            report.update(stop_reason="UNSPECIFIED", error_message="native fatal error")

    agent = HookAgent([([], "UNSPECIFIED")])
    events = []
    with pytest.raises(AgentRuntimeInterrupted):
        asyncio.run(run_session(agent, "original", events.append, report))
    assert agent.prompts == ["original"]
    assert events[-2]["system_error"] == "native fatal error"


def test_old_final_and_nested_response_do_not_hide_incomplete_root_turn():
    agent = Agent(
        [([step(1, final=True), step(2), step(3, final=True, depth=1)], "QUOTA_EXHAUSTED")]
    )
    events = []
    with pytest.raises(AgentRuntimeInterrupted):
        asyncio.run(run_session(agent, "original", events.append))
    assert events[-2]["completed_response"] is False
