"""CLI for finite Claude Code episodes and explicit execution recovery."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from akrun.akernel_backend import AkernelBackend
from akrun.datasets.synthetic import SyntheticCodeTaskResolver
from akrun.errors import ContractError, InfrastructureError, RecoveryRequiredError
from akrun.model_access import ModelAccess
from akrun.models import HarnessSpec, resolved_episode_from_dict
from akrun.qualification import qualify
from akrun.runner import EpisodeRunner
from akrun.store import EpisodeStore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--state-dir", type=Path, default=Path(".akrun"))
    result.add_argument("--model-upstream-url", default="https://api.deepseek.com/anthropic")
    result.add_argument("--model-credential-env", default="DEEPSEEK_API_KEY")
    result.add_argument("--version", action="version", version="akrun 0.1.0.dev0")
    commands = result.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser(
        "resolve-synthetic", help="resolve the repository-owned code task"
    )
    resolve.add_argument("row", type=Path)
    resolve.add_argument("--episode-id", required=True)
    resolve.add_argument("--task-image", required=True)
    resolve.add_argument("--task-platform", default="linux/amd64")
    resolve.add_argument(
        "--harness", choices=["claude-code", "static-candidate"], default="claude-code"
    )
    resolve.add_argument("--claude-mount-image")
    resolve.add_argument("--model", default="deepseek-flash")
    resolve.add_argument("--max-turns", type=int, default=20)
    resolve.add_argument("--timeout", type=int, default=600)
    resolve.add_argument("--candidate-file", default="gold.patch")
    resolve.add_argument("--output", type=Path, required=True)
    for command in ["qualify", "run"]:
        subcommand = commands.add_parser(command)
        subcommand.add_argument("episode", type=Path)
    for command in ["status", "inspect", "wait", "resume", "cancel", "cleanup"]:
        subcommand = commands.add_parser(command)
        subcommand.add_argument("episode_id")
        if command in {"wait", "resume"}:
            subcommand.add_argument(
                "--timeout", type=float, default=600 if command == "wait" else 0
            )
    return result


def emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = EpisodeStore(args.state_dir.resolve())
    backend = AkernelBackend()
    runner = EpisodeRunner(backend=backend, store=store)
    episode_id = ""
    try:
        if args.command == "resolve-synthetic":
            config: dict[str, Any]
            if args.harness == "claude-code":
                if not args.claude_mount_image:
                    raise ContractError("--claude-mount-image is required for Claude Code")
                config = {
                    "mount_image": args.claude_mount_image,
                    "model": args.model,
                    "max_turns": args.max_turns,
                }
                version = "2.1.205"
            else:
                config, version = {"candidate_file": args.candidate_file}, "1"
            episode = SyntheticCodeTaskResolver().resolve(
                json.loads(args.row.read_text()),
                source_dir=args.row.resolve().parent,
                episode_id=args.episode_id,
                task_image=args.task_image,
                task_platform=args.task_platform,
                harness=HarnessSpec(
                    args.harness, version, timeout_seconds=args.timeout, config=config
                ),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(episode.as_dict(), indent=2, sort_keys=True) + "\n")
            emit({"episode": str(args.output.resolve()), "spec_digest": episode.digest})
            return 0
        if args.command in {"run", "qualify"}:
            episode = resolved_episode_from_dict(json.loads(args.episode.read_text()))
            episode_id = episode.episode_id
            if args.command == "qualify":
                emit(qualify(episode, backend, store))
                return 0
            access = (
                ModelAccess(
                    model=episode.harness.config["model"],
                    upstream=args.model_upstream_url,
                    credential_env=args.model_credential_env,
                )
                if episode.harness.identity == "claude-code"
                else None
            )
            try:
                verification = runner.run(episode, model=access)
            finally:
                if access is not None:
                    access.close()
        else:
            episode_id = args.episode_id
            record = store.load(episode_id)
            if record is None:
                raise ContractError("episode not found")
            if args.command in {"status", "inspect"}:
                payload = record.as_dict()
                if args.command == "inspect":
                    live: dict[str, Any] = {}
                    for ref in (*record.qualifications, record.inference, record.verification):
                        if ref is not None and record.cleanup.get(ref.stage_id) != "released":
                            try:
                                live[ref.stage_id] = backend.inspect(ref)
                            except RecoveryRequiredError as error:
                                live[ref.stage_id] = {"diagnostic": str(error)}
                    payload["live"] = live
                if record.verification_result:
                    payload["result"] = asdict(runner.result(episode_id))
                emit(payload)
                return 0
            if args.command in {"cancel", "cleanup"}:
                emit(getattr(runner, args.command)(episode_id).as_dict())
                return 0
            verification = runner.resume(episode_id, timeout=args.timeout)
        emit(asdict(verification))
        return 0 if verification.verdict == "passed" else 1
    except KeyboardInterrupt:
        if episode_id and store.load(episode_id) is not None:
            try:
                runner.cancel(episode_id)
            except Exception:
                emit({"diagnostic": "cancellation_cleanup_required", "episode_id": episode_id})
        return 130
    except RecoveryRequiredError as error:
        emit({"diagnostic": "recovery_required", "reason": str(error), "episode_id": episode_id})
        return 3
    except (ContractError, InfrastructureError) as error:
        emit({"diagnostic": "episode_failed", "reason": str(error), "episode_id": episode_id})
        return 2
    except Exception as error:
        # SDK/provider error bodies and URLs may contain credentials. Keep them
        # outside the CLI boundary; execution identities remain in local state.
        emit(
            {
                "diagnostic": "operation_failed",
                "exception_type": type(error).__name__,
                "episode_id": episode_id,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
