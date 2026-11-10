"""Native CLI adapters; no agent loop or optimization strategy lives here."""

import json
import os
from pathlib import Path


def cache_environment(workspace="/workspace"):
    home = Path(workspace)
    return {
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "MPLCONFIGDIR": str(home / ".cache/matplotlib"),
    }


def prepare_caches(workspace="/workspace"):
    """Run as the agent, so restored paths never acquire supervisor privileges."""
    env = cache_environment(workspace)
    directories = [env["XDG_CONFIG_HOME"], env["XDG_CACHE_HOME"], env["MPLCONFIGDIR"]]
    directories.append(str(Path(env["XDG_CACHE_HOME"]) / "fontconfig"))
    for name in directories:
        path = Path(name)
        if path.is_symlink():
            raise ValueError(f"Agent cache must be a regular directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)


def launch_spec(config, workspace="/workspace", resume_session=None, model_catalog_path=None):
    agent, model = config["agent"]["id"], config["model"]
    if resume_session and agent not in {"codex", "antigravity"}:
        raise ValueError("This agent does not support session continuation")
    home = Path(workspace)
    from .provider_wait import model_timeout

    timeout = model_timeout(config)
    endpoint = "http://127.0.0.1:8765"
    env = {
        "HOME": str(home),
        **cache_environment(workspace),
        "PATH": str(home / ".local/bin")
        + ":/opt/conda/bin:/opt/node/bin:/opt/agents-python/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/opt/gtoc/src",
        "GTOC_BENCH_ROOT": "/opt/gtoc",
        "GTOC_PROBLEM": config["problem"],
        "GTOC_ENDPOINT": endpoint,
        "PIP_INDEX_URL": endpoint + "/packages/simple/",
        "PIP_TRUSTED_HOST": "127.0.0.1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PKG_CONFIG_PATH": "/opt/conda/lib/pkgconfig",
        "PYTHONUSERBASE": str(home / ".local"),
        "TMPDIR": "/tmp",
        "NO_PROXY": "127.0.0.1,localhost",
        "GTOC_RUN_TOKEN": "run-scoped-local-only",
    }
    for name in (
        "LD_LIBRARY_PATH",
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "KMP_AFFINITY",
        "GTOC_HOST_ARCH",
    ):
        if name in os.environ:
            env[name] = os.environ[name]
    if agent == "codex":
        env["CODEX_HOME"] = str(home / ".codex")
        directory = home / ".codex"
        directory.mkdir(exist_ok=True)
        content = "\n".join(
            [
                'model_provider = "bench"',
                f"model = {json.dumps(model['id'])}",
                f"model_reasoning_effort = {json.dumps(model['reasoning'])}",
                'approval_policy = "never"',
                'sandbox_mode = "danger-full-access"',
                'web_search = "disabled"',
                "check_for_update_on_startup = false",
                "[model_providers.bench]",
                'name = "GTOC-Bench gateway"',
                f'base_url = "{endpoint}/v1"',
                'env_key = "GTOC_RUN_TOKEN"',
                'wire_api = "responses"',
                "requires_openai_auth = false",
                "request_max_retries = 0",
                *([f"stream_idle_timeout_ms = {int(timeout * 1000)}"] if timeout else []),
                f"stream_max_retries = {config['recovery']['max_transient_retries']}",
                "supports_websockets = false",
                "[model_providers.bench.capabilities]",
                "external_web_access = false",
                'remote_compaction = "unsupported"',
                "[analytics]",
                "enabled = false",
                "[feedback]",
                "enabled = false",
                "",
            ]
        )
        (directory / "config.toml").write_text(content)
        if model_catalog_path:
            from .codex_metadata import selected_metadata

            metadata = selected_metadata(config, json.loads(Path(model_catalog_path).read_text()))
            prefix = ["model_catalog_json = " + json.dumps(str(model_catalog_path))]
            for source, setting in (
                ("context_window", "model_context_window"),
                ("auto_compact_token_limit", "model_auto_compact_token_limit"),
            ):
                value = metadata.get(source)
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        raise ValueError(f"Invalid {source} in the declared Codex metadata")
                    prefix.append(f"{setting} = {value}")
            (directory / "config.toml").write_text("\n".join(prefix) + "\n" + content)
        command = [
            "codex",
            "exec",
            *(["resume", resume_session] if resume_session else []),
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-",
        ]
    elif agent == "claude_code":
        if timeout:
            env["API_TIMEOUT_MS"] = str(int(timeout * 1000))
        env.update(
            {
                "ANTHROPIC_BASE_URL": endpoint,
                "ANTHROPIC_API_KEY": "run-scoped-local-only",
                "ANTHROPIC_MODEL": model["id"],
                "ANTHROPIC_SMALL_FAST_MODEL": model["id"],
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model["id"],
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model["id"],
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model["id"],
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_UPDATES": "1",
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "ENABLE_TOOL_SEARCH": "false",
            }
        )
        if model["max_output_tokens"] is not None:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(model["max_output_tokens"])
        command = [
            "claude",
            "--print",
            "--bare",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            model["id"],
            "--effort",
            model["reasoning"],
            "--dangerously-skip-permissions",
            "--disallowedTools",
            "WebSearch,WebFetch",
        ]
    else:
        if resume_session:
            env["GTOC_RESUME_SESSION"] = resume_session
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "LITELLM_TELEMETRY": "False",
                "TIKTOKEN_CACHE_DIR": "/opt/tiktoken-cache",
                "MSWEA_COST_TRACKING": "ignore_errors",
                "MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "1",
                "SWE_AGENT_CONFIG_DIR": "/opt/gtoc/environments/swe-tools",
                "SWE_AGENT_TOOLS_DIR": "/opt/gtoc/environments/swe-tools",
                "SWE_AGENT_TRAJECTORY_DIR": "/workspace",
                "OPENHANDS_SUPPRESS_BANNER": "1",
            }
        )
        path = home / ".gtoc-agent.json"
        path.write_text(json.dumps(config))
        interpreter = (
            "/opt/antigravity-python/bin/python"
            if agent == "antigravity"
            else "/opt/agents-python/bin/python"
        )
        command = [interpreter, "-m", "gtoc_bench.python_agents", str(path)]
    return command, env
