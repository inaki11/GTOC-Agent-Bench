"""Observe SDK turns and recover bounded premature idle transitions, not agent decisions."""

RECOVERY_LIMIT = 2
RECOVERY_PROMPT = (
    "The SDK execution turn ended without a completed final response. Continue the current "
    "task in this same conversation. Use gtoc status --json for the remaining budget. "
    "Do not assume that background tasks completed; inspect their state as needed."
)
INTERRUPTED_EXIT_CODE = 75


class AgentRuntimeInterrupted(RuntimeError):
    pass


def value(enum):
    return getattr(enum, "value", enum)


async def run_session(agent, prompt, emit, stop_report=None):
    """Keep the SDK's own loop; retry only idle turns lacking a completed response.

    The public step stream retains completion/error metadata that the text-only
    ChatResponse iterator discards. Never replay tool calls or restart a normal
    voluntary finish. All recovery messages pass through normal model accounting.
    """
    for attempt in range(RECOVERY_LIMIT + 1):
        if stop_report is not None:
            stop_report.clear()
        last_model_index = -1
        final_index = -1
        system_error = None
        last_step = None
        seen = set()
        try:
            response = await agent.chat(prompt)
            # chat() sends the message. Consume the public step stream directly,
            # with one reader, to retain terminal metadata and SYSTEM events.
            async for step in agent.conversation.receive_steps():
                metadata = {
                    "id": step.id,
                    "index": step.step_index,
                    "source": value(step.source),
                    "target": value(step.target),
                    "status": value(step.status),
                    "step_type": value(step.type),
                    "depth": step.depth,
                    "is_complete_response": step.is_complete_response,
                    "error": step.error,
                    "http_code": getattr(step, "http_code", None),
                }
                if step.depth == 0 and value(step.source) == "MODEL":
                    last_model_index = max(last_model_index, step.step_index)
                    if (
                        step.is_complete_response
                        and value(step.status) == "DONE"
                        and value(step.target) == "TARGET_USER"
                    ):
                        final_index = max(final_index, step.step_index)
                if value(step.source) == "SYSTEM" and value(step.status) == "ERROR":
                    system_error = step.error or step.content[:16384]
                key = tuple(metadata.values())
                if key not in seen:
                    seen.add(key)
                    emit(
                        {
                            "type": "sdk.step",
                            **metadata,
                            "content": step.content[:16384],
                            "content_truncated": len(step.content) > 16384,
                        }
                    )
                if (
                    value(step.source) == "MODEL"
                    and value(step.target) == "TARGET_USER"
                    and step.content_delta
                ):
                    emit({"type": "text.delta", "text": step.content_delta})
                last_step = metadata
            response_reason = value(response.stop_reason)
            reason = value((stop_report or {}).get("stop_reason", response_reason))
            system_error = (stop_report or {}).get("error_message") or system_error
        except Exception as exc:
            emit({"type": "session.error", "error_type": type(exc).__name__, "message": str(exc)})
            raise
        completed = final_index >= 0 and final_index >= last_model_index and not system_error
        emit(
            {
                "type": "session.turn_ended",
                "sdk_stop_reason": reason,
                "response_sdk_stop_reason": response_reason,
                "completed_response": completed,
                "last_step": last_step,
                "system_error": system_error,
                "recovery_attempt": attempt,
            }
        )
        if reason == "UNSPECIFIED" and completed:
            emit({"type": "session.completed", "conversation_id": agent.conversation_id})
            return
        if reason == "UNSPECIFIED" and not system_error and attempt < RECOVERY_LIMIT:
            emit(
                {
                    "type": "session.recovery_requested",
                    "reason": "sdk_turn_without_completed_response",
                    "attempt": attempt + 1,
                    "limit": RECOVERY_LIMIT,
                    "conversation_id": agent.conversation_id,
                    "prompt": RECOVERY_PROMPT,
                }
            )
            prompt = RECOVERY_PROMPT
            continue
        emit(
            {
                "type": "session.interrupted",
                "reason": "sdk_turn_without_completed_response"
                if reason == "UNSPECIFIED"
                else "sdk_stop_reason",
                "sdk_stop_reason": reason,
                "conversation_id": agent.conversation_id,
            }
        )
        raise AgentRuntimeInterrupted(f"Antigravity turn interrupted: {reason}")
