"""Configure the released agents; their own implementations own the agent loops."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from . import ROOT
from .provider_wait import model_timeout


def model_settings(config):
    anthropic = config["provider"]["protocol"] == "anthropic"
    return {
        "model": ("anthropic/" if anthropic else "openai/") + config["model"]["id"],
        "api_key": "run-scoped-local-only",
        "base_url": "http://127.0.0.1:8765" + ("" if anthropic else "/v1"),
    }


def openhands(config, prompt):
    from openhands.sdk import LLM, Conversation
    from openhands_cli.utils import get_default_cli_agent

    settings = model_settings(config)
    llm = LLM(
        **settings,
        num_retries=0,
        max_output_tokens=config["model"]["max_output_tokens"],
        **({"timeout": model_timeout(config)} if model_timeout(config) else {}),
    )
    if llm.uses_responses_api() != (config["provider"]["protocol"] == "responses"):
        raise ValueError("OpenHands model capabilities disagree with provider.protocol")
    agent = get_default_cli_agent(llm)
    # Use the official SDK loop with CLI defaults, without downloading public skills.
    conversation = Conversation(
        agent=agent,
        workspace="/workspace",
        persistence_dir="/workspace/openhands",
        callbacks=[lambda event: print(event.model_dump_json(), flush=True)],
        visualizer=None,
        max_iteration_per_run=1000000,
        delete_on_close=False,
    )
    try:
        conversation.send_message(prompt)
        conversation.run()
        if conversation.state.execution_status.value != "finished":
            raise RuntimeError("OpenHands stopped: " + conversation.state.execution_status.value)
    finally:
        conversation.close()


def mini_swe_agent(config, prompt):
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    settings = model_settings(config)
    model = LitellmModel(
        model_name=settings["model"],
        cost_tracking="ignore_errors",
        model_kwargs={
            "api_base": settings["base_url"],
            "api_key": settings["api_key"],
            "num_retries": 0,
            **({"timeout": model_timeout(config)} if model_timeout(config) else {}),
            "max_tokens": config["model"]["max_output_tokens"],
        },
    )
    agent = DefaultAgent(
        model,
        LocalEnvironment(
            cwd="/workspace",
            timeout=660 if config["problem"] in {"gtocx", "gtoc11", "gtoc13"} else 120,
        ),
        system_template="You are a helpful assistant that can interact with a computer.",
        instance_template=(
            "{{task}}\nUse the bash tool to execute commands; each command starts a new shell. "
            "Evaluate candidates with gtoc evaluate. To finish, execute only "
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT."
        ),
        cost_limit=0,
        step_limit=0,
        output_path=Path("/workspace/mini-trajectory.json"),
    )
    result = agent.run(prompt)
    if result.get("exit_status") != "Submitted":
        raise RuntimeError("mini-SWE-agent stopped: " + str(result.get("exit_status")))


def swe_agent(config, prompt):
    from sweagent.run.run_single import RunSingle, RunSingleConfig

    settings = model_settings(config)
    output = Path("/workspace/swe-agent")
    if output.exists():
        # A restored workspace contains the previous native trajectory. Keep it
        # available as data and let the official client record a fresh segment.
        output = Path(tempfile.mkdtemp(prefix="swe-agent-recovery-", dir="/workspace"))
    options = RunSingleConfig(
        env={
            "deployment": {"type": "local"},
            "repo": None,
            "post_startup_commands": ["cd /workspace"],
        },
        agent={
            "type": "default",
            "model": {
                "name": settings["model"],
                "api_base": settings["base_url"],
                "api_key": settings["api_key"],
                "per_instance_cost_limit": 0,
                "max_input_tokens": 0,
                "max_output_tokens": config["model"]["max_output_tokens"],
                "retry": {"retries": 1},
                "completion_kwargs": {
                    "num_retries": 0,
                    "drop_params": True,
                    **({"timeout": model_timeout(config)} if model_timeout(config) else {}),
                },
            },
            "templates": {
                "system_template": "You are a helpful assistant that can interact with a computer.",
                "instance_template": "{{problem_statement}}\nUse bash for commands and finish to end this run.",
                "next_step_template": "{{observation}}",
            },
            "tools": {
                "bundles": [{"path": str(ROOT / "environments/swe-tools/finish")}],
                "submit_command": "finish",
                "enable_bash_tool": True,
                "execution_timeout": 660
                if config["problem"] in {"gtocx", "gtoc11", "gtoc13"}
                else 120,
                "total_execution_timeout": int(config["budget"]["wall_seconds"]),
                "filter": {"blocklist": [], "blocklist_standalone": [], "block_unless_regex": {}},
            },
        },
        problem_statement={"type": "text", "text": prompt, "id": config["problem"]},
        output_dir=output,
    )
    runner = RunSingle.from_config(options)
    runner.run()
    if runner.agent.info.get("exit_status") != "submitted":
        raise RuntimeError("SWE-agent stopped: " + str(runner.agent.info.get("exit_status")))


def antigravity(config, prompt):
    from google.antigravity import Agent, LocalOpenAIAgentConfig, types
    from google.antigravity.hooks import hooks, policy

    from .antigravity_runtime import run_session

    stop_report = {}

    @hooks.stop
    async def on_stop(data):
        stop_report.update(data.model_dump(mode="json"))
        print(
            json.dumps({"type": "sdk.stop_requested", "data": data.model_dump(mode="json")}),
            flush=True,
        )
        return types.StopHookResult()

    @hooks.pre_tool_call_decide
    async def before_tool(data):
        print(
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": data.id,
                        "server": "antigravity",
                        "tool": str(data.name),
                        "arguments": data.args,
                    },
                }
            ),
            flush=True,
        )
        return types.HookResult(allow=True)

    @hooks.post_tool_call
    async def after_tool(data):
        result = data.model_dump(mode="json") if hasattr(data, "model_dump") else str(data)
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": getattr(data, "id", None),
                        "result": result,
                    },
                }
            ),
            flush=True,
        )

    async def run():
        environment = {"OPENAI_API_KEY": "run-scoped-local-only"}
        if os.environ.get("GTOC_HOST_ARCH") in {"aarch64", "arm64"}:
            environment["ANTIGRAVITY_HARNESS_PATH"] = (
                "/opt/antigravity-runtime/linux-aarch64/localharness"
            )
        options = LocalOpenAIAgentConfig(
            model=config["model"]["id"],
            base_url="http://127.0.0.1:8765/v1",
            workspaces=["/workspace"],
            save_dir="/workspace/antigravity",
            app_data_dir="/workspace/.antigravity",
            conversation_id=os.environ.get("GTOC_RESUME_SESSION"),
            session_continuation_mode=(
                types.SessionContinuationMode.RESUME
                if os.environ.get("GTOC_RESUME_SESSION")
                else types.SessionContinuationMode.CREATE_ONLY
            ),
            capabilities=types.CapabilitiesConfig(
                enabled_tools=types.BuiltinTools.minimal(), enable_subagents=False
            ),
            policies=[policy.allow_all()],
            hooks=[before_tool, after_tool, on_stop],
            retry_config=types.RetryConfig(api_retry=types.ModelAPIRetryConfig(max_retries=0)),
            env=environment,
        )
        async with Agent(options) as agent:
            session_logged = False

            def log_session():
                nonlocal session_logged
                if agent.conversation_id and not session_logged:
                    print(
                        json.dumps(
                            {
                                "type": "session.started",
                                "conversation_id": agent.conversation_id,
                                "resumed": bool(os.environ.get("GTOC_RESUME_SESSION")),
                            }
                        ),
                        flush=True,
                    )
                    session_logged = True

            def emit(event):
                log_session()
                print(json.dumps(event), flush=True)

            if os.environ.get("GTOC_RESUME_SESSION"):
                emit(
                    {
                        "type": "session.restored",
                        "conversation_id": agent.conversation_id,
                        "requested_session_id": os.environ["GTOC_RESUME_SESSION"],
                        "restored_steps": len(agent.conversation.history),
                        "process_memory_restored": False,
                    }
                )
            await run_session(agent, prompt, emit, stop_report)

    asyncio.run(run())


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    prompt = sys.stdin.read()
    {
        "openhands": openhands,
        "mini_swe_agent": mini_swe_agent,
        "swe_agent": swe_agent,
        "antigravity": antigravity,
    }[config["agent"]["id"]](config, prompt)


if __name__ == "__main__":
    from .antigravity_runtime import INTERRUPTED_EXIT_CODE, AgentRuntimeInterrupted

    try:
        main()
    except AgentRuntimeInterrupted:
        sys.exit(INTERRUPTED_EXIT_CODE)
