"""Host commands and the small in-container evaluation/status client."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import ROOT
from .lifecycle import predecessor_state, read_status
from .problems import SPECS, client_timeout, specification


class ClientError(ValueError):
    """Preserve controller error details across the CLI boundary."""

    def __init__(self, message, http_status, code="http_error", retryable=False):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.retryable = retryable


def client(route, data=None, headers=None):
    endpoint = os.environ.get("GTOC_ENDPOINT")
    if not endpoint:
        raise ValueError(
            "This command runs inside an evaluation; use 'gtoc validate' for an offline check"
        )
    request = urllib.request.Request(endpoint + route, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(
            request, timeout=client_timeout(os.environ.get("GTOC_PROBLEM", "gtoc12"))
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            details = json.loads(body)
        except ValueError:
            details = {}
        if not isinstance(details, dict):
            details = {}
        message = details.get("error")
        code = details.get("code")
        raise ClientError(
            message if isinstance(message, str) else body or str(exc.reason),
            exc.code,
            code if isinstance(code, str) else "http_error",
            details.get("retryable") is True,
        ) from None


def run_path(value, results):
    path = Path(value)
    return path.resolve() if path.is_dir() else Path(results).resolve() / "runs" / value


def validate_file(path, image, problem="gtoc12"):
    from .runner import docker_json, normalize_engine_info

    engine = normalize_engine_info(docker_json("info", "--format", "{{json .}}"))["Engine"]
    judge_mount = (
        [
            "--mount",
            "type=tmpfs,dst=/judge-tmp,tmpfs-size=128m,tmpfs-mode=0700,U=true,noexec,nosuid,nodev",
        ]
        if engine == "podman"
        else ["--tmpfs", "/judge-tmp:rw,nosuid,nodev,noexec,size=128m,uid=1001,gid=1001,mode=700"]
    )
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--user",
        "1001:1001",
        "--memory",
        "1536m",
        "--pids-limit",
        "32",
        *judge_mount,
        "-e",
        "GTOC_JUDGE_TMP=/judge-tmp",
        "-e",
        "OPENBLAS_NUM_THREADS=1",
        "-e",
        "OMP_NUM_THREADS=1",
        "--entrypoint",
        "/opt/conda/bin/python",
        image,
        "-m",
        "gtoc_bench.validator",
        problem,
    ]
    result = subprocess.run(
        command, input=Path(path).read_bytes(), capture_output=True, timeout=client_timeout(problem)
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return json.loads(result.stdout)


def auth_command(args):
    if args.provider == "antigravity":
        from .antigravity import auth_command as antigravity_auth

        return antigravity_auth(args)
    if args.provider == "claude":
        from .claude_native import DEFAULT_CLAUDE_DIR
        from .claude_native import auth_command as native_auth

        if args.auth_action == "models":
            raise ValueError(
                "Use an explicit Claude model ID; the native client has no catalog command"
            )
        if args.auth_action == "login" and (
            args.no_browser or args.enable_plan or args.port != 1455 or args.timeout != 300
        ):
            raise ValueError(
                "Claude login uses the official interactive flow; login flags apply only to ChatGPT"
            )
        return native_auth(args.auth_action, args.auth_dir or DEFAULT_CLAUDE_DIR, args.profile)

    from .auth import (
        PLAN_SCOPE,
        ChatGPTCredential,
        Profiles,
        http_client,
        logout,
        model_catalog,
        public_session,
    )

    profiles = Profiles(args.auth_dir or "~/.config/gtoc-bench/auth", args.profile)
    with http_client() as connection:
        if args.auth_action == "login":
            from .login import login

            record = login(
                profiles, connection, args.port, not args.no_browser, args.enable_plan, args.timeout
            )
        elif args.auth_action == "models":
            credential = ChatGPTCredential(profiles, connection)
            models = model_catalog(credential, connection)
            print(
                json.dumps(
                    [
                        {
                            k: v
                            for k, v in model.items()
                            if k
                            in {
                                "slug",
                                "display_name",
                                "supported_reasoning_levels",
                                "context_window",
                            }
                        }
                        for model in models
                        if model.get("visibility", "list") == "list"
                    ],
                    indent=2,
                )
            )
            return 0
        elif args.auth_action == "logout":
            confirmed = logout(profiles, connection)
            print("Local session cleared; registration retained.")
            print(
                "Remote revocation confirmed."
                if confirmed
                else "Remote revocation unconfirmed. Disconnect GTOC-Bench in ChatGPT Settings."
            )
            return 0 if confirmed else 1
        else:
            record = profiles.read()
    status = public_session(record, args.profile) | {
        "registered": bool(record.get("client_id")),
        "signed_in": bool(record.get("access_token")) and not record.get("refresh_pending", False),
        "plan_enabled": PLAN_SCOPE in record.get("scopes", []),
        "email": record.get("email"),
        "expires_at": record.get("expires_at"),
        "needs_login": not record.get("access_token") or bool(record.get("refresh_pending")),
    }
    print(json.dumps(status, indent=2))
    if args.auth_action == "login" and not status["plan_enabled"]:
        print("Signed in, but plan use is disabled. Run login with --enable-plan to request it.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gtoc", description="GTOC-Bench phase 1")
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="Manage host-only ChatGPT or official Claude login")
    auth_sub = auth.add_subparsers(dest="auth_action", required=True)
    for action in ("login", "status", "models", "logout"):
        command = auth_sub.add_parser(action)
        command.add_argument("provider", choices=["chatgpt", "claude", "antigravity"])
        command.add_argument("--profile", default="default")
        command.add_argument("--auth-dir")
        if action == "login":
            command.add_argument("--port", type=int, default=1455)
            command.add_argument("--no-browser", action="store_true")
            command.add_argument("--enable-plan", action="store_true")
            command.add_argument("--timeout", type=int, default=300)
            command.add_argument(
                "--bridge-port", type=int, default=8317, help="Private Antigravity bridge port"
            )
    installer = sub.add_parser("install-agent", help="Install the official pinned host client")
    installer.add_argument("agent", choices=["claude_code"])
    provider_installer = sub.add_parser(
        "install-provider", help="Install a checksum-pinned host model bridge"
    )
    provider_installer.add_argument("provider", choices=["antigravity"])
    build = sub.add_parser("build", help="Build the pinned science and agent image")
    variant = build.add_mutually_exclusive_group()
    variant.add_argument("--cuda", action="store_true", help="Build CUDA even on a CPU host")
    variant.add_argument("--cpu", action="store_true", help="Build the CPU-only image")
    build.add_argument("--tag")
    build.add_argument(
        "--base-image",
        help="Update an existing profile using the shared update recipe; requires --tag",
    )
    build.add_argument(
        "--agent",
        choices=["all", "codex", "claude-code"],
        default="all",
        help="Omit unused agent runtimes; scientific tools remain identical",
    )
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--image", default="auto")
    doctor.add_argument("--problem", choices=sorted(SPECS), default="gtoc12")
    doctor.add_argument("--gpus", choices=["auto", "all", "none"], default="auto")
    run = sub.add_parser("run")
    run.add_argument("config")
    run.add_argument("--results", default="results")
    run.add_argument("--detach", action="store_true")
    resume = sub.add_parser(
        "resume", help="Continue a sealed Codex or Antigravity attempt with its remaining budget"
    )
    resume.add_argument("parent_run", help="Path to the finalized parent run")
    resume.add_argument(
        "--memory-gb",
        type=float,
        help="Explicit research continuation with a different RAM allocation; preserves time/tokens and records the resource change",
    )
    resume.add_argument(
        "--config",
        help="Optional config for current credentials/image; model and resources must match",
    )
    resume.add_argument("--results", default="results")
    resume.add_argument("--detach", action="store_true")
    resume.add_argument(
        "--recovery-reason",
        help="Record an explicit research recovery; can reopen agent_finished with remaining budget, preserving the original result",
    )
    resume.add_argument(
        "--workspace-only",
        action="store_true",
        help="Recover Claude Code or SWE-agent files/history into a new conversation, without a native session",
    )
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("run_dir")
    for name in ("status", "stop", "analyze"):
        command = sub.add_parser(name)
        command.add_argument("run_id", nargs="?" if name == "status" else None)
        command.add_argument("--results", default="results")
        if name == "status":
            command.add_argument("--json", action="store_true")
    compare = sub.add_parser(
        "compare", help="Build comparative figures and the historical table from saved evaluations"
    )
    compare.add_argument("--results", default="results")
    compare.add_argument("--output", default="analysis/comparisons/current")
    compare.add_argument("--prices", default=str(ROOT / "analysis/prices.yaml"))
    compare.add_argument("--problem", choices=sorted(SPECS))
    compare.add_argument("--min-wall-seconds", type=float, default=3600)
    compare.add_argument(
        "--wall-seconds",
        type=float,
        help="Filter by the original time budget, including continuations",
    )
    compare.add_argument("--leaderboards", default=str(ROOT / "references/leaderboards"))
    inspect_request = sub.add_parser(
        "inspect-request", help="Read a compressed or legacy request as ordinary JSON"
    )
    inspect_request.add_argument("directory", help="The run's requests/<request-id> directory")
    inspect_request.add_argument(
        "--client", action="store_true", help="Reconstruct the original client body"
    )
    inspect_submission = sub.add_parser(
        "inspect-submission", help="Recover the exact bytes of a compressed or legacy candidate"
    )
    inspect_submission.add_argument("directory", help="The run's submissions/<submission-id> directory")
    inspect_submission.add_argument("--output", help="Write to a new file instead of standard output")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("file")
    evaluate.add_argument("--json", action="store_true")
    evaluate.add_argument("--submission-id", default=None)
    docs = sub.add_parser("docs")
    docs.add_argument("url")
    sub.add_parser("packages", help="Show the permitted package and documentation catalog")
    sub.add_parser("tools", help="Show installed scientific tools, versions and paths")
    validate = sub.add_parser("validate", help="Offline validation, not a benchmark submission")
    validate.add_argument("file")
    validate.add_argument("--image", default="gtoc-bench:phase1-cpu")
    validate.add_argument("--problem", choices=sorted(SPECS), default="gtoc12")
    validate.add_argument(
        "--local",
        action="store_true",
        help="Run the selected judge without Docker (GTOC11 requires Linux amd64)",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "auth":
            return auth_command(args)
        if args.command == "install-agent":
            from .claude_native import install

            print(install())
            return 0
        if args.command == "install-provider":
            from .antigravity import install

            print(install())
            return 0
        if args.command == "evaluate":
            limit = specification(os.environ.get("GTOC_PROBLEM", "gtoc12"))["max_bytes"]
            with Path(args.file).open("rb") as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"Maximum candidate size is {limit} bytes")
            result = json.loads(
                client(
                    "/evaluate",
                    data,
                    {
                        "Content-Type": "application/octet-stream",
                        "X-GTOC-Submission-ID": args.submission_id or uuid.uuid4().hex,
                    },
                )
            )
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "valid" else 2
        if args.command == "docs":
            print(
                client("/docs?" + urllib.parse.urlencode({"url": args.url})).decode(
                    errors="replace"
                )
            )
            return 0
        if args.command == "packages":
            print(client("/packages").decode())
            return 0
        if args.command == "tools":
            from .hardware import tools_inventory

            print(json.dumps(tools_inventory(), indent=2))
            return 0
        if args.command == "status":
            if args.run_id:
                path = run_path(args.run_id, args.results)
                result = read_status(path)
                result["successor"] = predecessor_state(path)
            else:
                result = json.loads(client("/status"))
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "build":
            from .gpu import allocation, image_name
            from .runner import docker_json, normalize_engine_info

            info = normalize_engine_info(docker_json("info", "--format", "{{json .}}"))
            if args.base_image:
                if not args.tag or args.cuda or args.cpu or args.agent != "all":
                    raise ValueError(
                        "--base-image requires --tag and inherits its CPU/CUDA and agent runtimes"
                    )
                plan = {"assigned": "none"}
            elif args.cuda or args.cpu:
                plan = {"assigned": "all" if args.cuda else "none"}
            else:
                plan = allocation("auto", info)
            default_tag = image_name("auto", plan)
            if args.agent != "all":
                default_tag = default_tag.replace(":phase1-", ":" + args.agent + "-")
            tag = args.tag or default_tag
            recipe = "Dockerfile.update" if args.base_image else "Dockerfile"
            build_args = (
                [
                    "--build-arg",
                    "BASE_IMAGE=" + args.base_image,
                ]
                if args.base_image
                else [
                    "--build-arg",
                    "TORCH_VARIANT=" + ("cu128" if plan["assigned"] == "all" else "cpu"),
                    "--build-arg",
                    "AGENT=" + args.agent,
                ]
            )
            command = [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-f",
                str(ROOT / "environments" / recipe),
                *build_args,
                "-t",
                tag,
                str(ROOT),
            ]
            if info["Engine"] == "podman":
                command.insert(2, "--layers")
                # Podman otherwise stages large image blobs in /var/tmp, which
                # can exhaust a disk quota even when the final image fits.
                with tempfile.TemporaryDirectory(prefix="gtoc-image-build-") as scratch:
                    return subprocess.call(command, env=os.environ | {"TMPDIR": scratch})
            return subprocess.call(command)
        if args.command == "validate":
            if args.local:
                if args.problem not in {"gtoc11", "gtoc13", "gtocx"}:
                    raise ValueError(
                        "--local is supported for GTOCX, GTOC11 (Linux amd64) and GTOC13"
                    )
                from .validator import evaluate

                with Path(args.file).open("rb") as stream:
                    data = stream.read(specification(args.problem)["max_bytes"] + 1)
                result = evaluate(data, problem=args.problem)
            else:
                result = validate_file(args.file, args.image, args.problem)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "valid" else 2
        if args.command == "doctor":
            from .gpu import allocation, image_name, probe_container
            from .runner import docker_json, normalize_engine_info
            from .validator import verify_materials

            info = normalize_engine_info(docker_json("info", "--format", "{{json .}}"))
            plan = allocation(args.gpus, info)
            selected = image_name(args.image, plan)
            image = docker_json("image", "inspect", selected)[0]
            if image["Architecture"] != "amd64" or image["Os"] != "linux":
                raise ValueError("The benchmark requires the Linux amd64 image")
            verify_materials(args.problem)
            science = probe_container(image["Id"], plan, info["Engine"])
            print(
                json.dumps(
                    {
                        "docker": info["ServerVersion"],
                        "container_engine": info["Engine"],
                        "rootless": info.get("Rootless"),
                        "docker_architecture": info["Architecture"],
                        "image_id": image["Id"],
                        "selected_image": selected,
                        "image_architecture": image["Architecture"],
                        "gpu_allocation": plan,
                        "science": {
                            key: science[key]
                            for key in (
                                "uid",
                                "torch",
                                "pykep",
                                "cuda_available",
                                "cuda_version",
                                "gpus",
                                "cuda_checks",
                            )
                        },
                        "problem_hashes": "verified",
                        "purpose": "development",
                        "emulation": info["Architecture"] not in {"x86_64", "amd64"},
                    },
                    indent=2,
                )
            )
            return 0
        if args.command in {"run", "resume", "_worker"}:
            from .runner import Runner, create_run

            if args.command in {"run", "resume"}:
                from .config import load_config

                if args.command == "resume":
                    from .continuation import create_continuation

                    config = load_config(args.config or str(Path(args.parent_run) / "launch.json"))
                    path = create_continuation(
                        args.parent_run,
                        config,
                        args.results,
                        workspace_only=args.workspace_only,
                        memory_gb=args.memory_gb,
                        recovery_reason=args.recovery_reason,
                    )
                else:
                    path = create_run(load_config(args.config), args.results)
                if args.detach:
                    log = (path / "controller.log").open("ab")
                    process = subprocess.Popen(
                        [sys.executable, "-m", "gtoc_bench", "_worker", str(path)],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                        close_fds=True,
                    )
                    log.close()
                    print(
                        json.dumps(
                            {"run_id": path.name, "directory": str(path), "pid": process.pid},
                            indent=2,
                        )
                    )
                    return 0
                print(f"Run: {path}", flush=True)
            else:
                path = Path(args.run_dir)
            return Runner(path).run()
        if args.command == "stop":
            from .storage import write_json

            path = run_path(args.run_id, args.results)
            if not (path / "status.json").exists():
                raise ValueError("Run not found")
            write_json(path / "stop.request", {"reason": "operator_cancelled"})
            print(f"Stop requested: {path.name}")
            return 0
        if args.command == "analyze":
            from .analysis import export_audit, export_progress

            path = run_path(args.run_id, args.results)
            print(export_progress(path))
            print(export_audit(path))
            return 0
        if args.command == "inspect-submission":
            from .submission_artifacts import read_submission

            data = read_submission(args.directory)
            if args.output:
                with Path(args.output).open("xb") as stream:
                    stream.write(data)
            else:
                sys.stdout.buffer.write(data)
            return 0
        if args.command == "inspect-request":
            from .request_artifacts import read_request

            print(
                json.dumps(
                    read_request(args.directory, client=args.client), ensure_ascii=False, indent=2
                )
            )
            return 0
        if args.command == "compare":
            from .comparison import export_comparison

            print(
                export_comparison(
                    args.results,
                    args.output,
                    args.prices,
                    minimum_seconds=args.min_wall_seconds,
                    problem=args.problem,
                    wall_seconds=args.wall_seconds,
                    leaderboards=args.leaderboards,
                )
            )
            return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "status": "error",
                        "score": None,
                        "diagnostics": [str(exc)],
                        "error": str(exc),
                        "code": exc.code if isinstance(exc, ClientError) else "client_error",
                        "http_status": exc.http_status if isinstance(exc, ClientError) else None,
                        "retryable": isinstance(exc, ClientError) and exc.retryable,
                    },
                    indent=2,
                )
            )
        else:
            print(f"gtoc: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
