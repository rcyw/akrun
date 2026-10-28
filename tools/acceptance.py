#!/usr/bin/env python3
"""Opt-in live controls and bounded model acceptance on the task-owned node."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from akrun.akernel_backend import AkernelBackend, sdk_inventory
from akrun.catalog import adapters
from akrun.errors import RecoveryRequiredError
from akrun.model_access import ModelAccess
from akrun.models import EpisodePhase, ExecutionRef, StagePlan, resolved_episode_from_dict
from akrun.runner import EpisodeRunner
from akrun.store import EpisodeStore

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "artifacts/acceptance"
STATE = EVIDENCE / "state"
CLI = [str(ROOT / ".venv/bin/akrun"), "--state-dir", str(STATE)]


def configure() -> dict[str, str]:
    data = Path("/srv/akernel-filestore/akrun-claude-code-v1/runsc-rc3/data")
    environment = {
        **os.environ,
        "AKERNEL_SERVER_ADDRESS": "127.0.0.1:28443",
        "AKERNEL_GATEWAY_ADDRESS": "http://127.0.0.1:28080",
        "AKERNEL_TOKEN": (data / "token").read_text().strip(),
    }
    os.environ.update(environment)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    return environment


def command(
    arguments: list[str],
    name: str,
    *,
    expected: int = 0,
    environment: dict[str, str] | None = None,
    timeout: int = 600,
) -> None:
    with (EVIDENCE / f"{name}.log").open("w") as log:
        result = subprocess.run(
            CLI + arguments,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if result.returncode != expected:
        raise RuntimeError(
            f"{name}: exit {result.returncode}, expected {expected}; inspect local evidence"
        )


def resolve(episode_id: str, *, candidate: str | None = None) -> Path:
    refs = json.loads((ROOT / "artifacts/standalone/images.json").read_text())
    path = EVIDENCE / f"{episode_id}.json"
    arguments = [
        "resolve-synthetic",
        str(ROOT / "fixtures/synthetic/code-task-v1/row.json"),
        "--episode-id",
        episode_id,
        "--task-image",
        refs["task"],
        "--output",
        str(path),
        "--timeout",
        "480",
    ]
    arguments += (
        ["--harness", "static-candidate", "--candidate-file", candidate]
        if candidate is not None
        else ["--claude-mount-image", refs["claude"], "--model", "deepseek-flash"]
    )
    command(arguments, f"{episode_id}-resolve")
    return path


def empty_inventory() -> None:
    if sdk_inventory():
        raise RuntimeError("task-owned node still has SDK allocations")


def static_controls(prefix: str = "control") -> None:
    summary = {}
    for label, candidate, expected in [
        ("gold", "gold.patch", 0),
        ("bad", "known-bad.patch", 1),
        ("empty", "empty.patch", 1),
    ]:
        episode_id = f"{prefix}-{label}-v1"
        path = resolve(episode_id, candidate=candidate)
        command(["qualify", str(path)], f"{episode_id}-qualify")
        command(["run", str(path)], f"{episode_id}-run", expected=expected)
        command(["status", episode_id], f"{episode_id}-status")
        record = EpisodeStore(STATE).load(episode_id)
        assert (
            record is not None and record.inference is not None and record.verification is not None
        )
        if record.inference.sandbox_id == record.verification.sandbox_id:
            raise RuntimeError("verifier reused the inference Sandbox")
        summary[episode_id] = {
            "inference": asdict(record.inference),
            "verification": asdict(record.verification),
            "cleanup": record.cleanup,
            "exit": expected,
        }
        empty_inventory()
    (EVIDENCE / "static-controls.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("gold, known-bad and empty controls passed; SDK inventory empty")


class DisconnectAfterInference(AkernelBackend):
    """Close a real SDK client after terminal inference, before local publication."""

    def _wait(
        self,
        sandbox: Any,
        execution: ExecutionRef,
        plan: StagePlan,
        artifact_dir: Path,
        *,
        timeout: float,
    ):
        if any(output.path == "/outputs/candidate.patch" for output in plan.outputs):
            deadline = time.monotonic() + timeout
            while not sandbox.files.exists("/run/akrun/result.json"):
                if time.monotonic() >= deadline:
                    raise RecoveryRequiredError("controlled_disconnect_wait_timeout")
                time.sleep(0.25)
            proof = {
                "execution": asdict(execution),
                "started": json.loads(sandbox.files.read("/run/akrun/started.json")),
                "commands": [asdict(value) for value in sandbox.commands.list()],
            }
            (EVIDENCE / "disconnect-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
            sandbox.kill()  # public detached close: retire actual command/file/tunnel transports
            raise RecoveryRequiredError("controlled_client_disconnect_before_publication")
        return super()._wait(sandbox, execution, plan, artifact_dir, timeout=timeout)


def model_control(episode_id: str, *, disconnect: bool) -> None:
    path = resolve(episode_id)
    command(["qualify", str(path)], f"{episode_id}-qualify")
    ledger_path = EVIDENCE / "model-attempts.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    if len(ledger) >= 4 or any(item["episode_id"] == episode_id for item in ledger):
        raise RuntimeError(
            "model allowance exhausted or episode already attempted; inspect existing state"
        )
    ledger.append(
        {
            "episode_id": episode_id,
            "started_at": datetime.now(UTC).isoformat(),
            "model": "deepseek-flash",
            "source_head": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "deadline_seconds": 600,
            "disconnect": disconnect,
        }
    )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    started = time.monotonic()
    try:
        if disconnect:
            # Run the fault boundary in a separate process with a hard wall bound.
            with (EVIDENCE / f"{episode_id}-disconnect.log").open("w") as log:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "disconnect-worker",
                        "--episode-id",
                        episode_id,
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                    check=False,
                )
            if result.returncode != 3:
                raise RuntimeError(
                    "controlled disconnect did not leave the expected recoverable execution"
                )
            before = EpisodeStore(STATE).load(episode_id)
            assert before is not None and before.inference is not None
            if before.phase != EpisodePhase.INFERENCE_RUNNING or before.candidate_manifest:
                raise RuntimeError("disconnect did not precede candidate publication")
            environment = dict(os.environ)
            environment.pop("DEEPSEEK_API_KEY", None)
            command(
                ["resume", episode_id],
                f"{episode_id}-resume-without-model-key",
                environment=environment,
            )
            after = EpisodeStore(STATE).load(episode_id)
            assert after is not None
            proof = json.loads((EVIDENCE / "disconnect-proof.json").read_text())
            if after.inference != before.inference or proof["started"]["launch_count"] != 1:
                raise RuntimeError("recovery changed the original inference identity")
        else:
            command(["run", str(path)], f"{episode_id}-run")
        command(["status", episode_id], f"{episode_id}-status")
        empty_inventory()
        ledger[-1]["result"] = "passed"
    except BaseException:
        ledger[-1]["result"] = "failed"
        # Timeout kills only the caller; explicitly reconcile its persisted Sandbox.
        record = EpisodeStore(STATE).load(episode_id)
        if record is not None and record.phase not in {
            EpisodePhase.COMPLETED,
            EpisodePhase.CANCELLED,
        }:
            command(["cancel", episode_id], f"{episode_id}-failure-cancel", timeout=90)
        raise
    finally:
        ledger[-1]["elapsed_seconds"] = round(time.monotonic() - started, 3)
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"{episode_id}: passed; model attempt {len(ledger)}/4; SDK inventory empty")


def disconnect_worker(episode_id: str) -> int:
    episode = resolved_episode_from_dict(json.loads((EVIDENCE / f"{episode_id}.json").read_text()))
    access = ModelAccess(model="deepseek-flash")
    runner = EpisodeRunner(backend=DisconnectAfterInference(), store=EpisodeStore(STATE))
    try:
        runner.run(episode, model=access)
    except RecoveryRequiredError as error:
        print(str(error))
        return 3
    finally:
        access.close()
    return 2


def cancellation_control(episode_id: str = "cancel-control-v1") -> None:
    path = resolve(episode_id, candidate="gold.patch")
    episode = resolved_episode_from_dict(json.loads(path.read_text()))
    store = EpisodeStore(STATE)
    record = store.initialize(episode)
    if record.phase != EpisodePhase.NEW:
        raise RuntimeError("cancellation control already attempted")
    record.phase = EpisodePhase.INFERENCE_RUNNING
    store.save(record)
    harness, candidate, _, _ = adapters(episode)
    plan = replace(
        harness.plan(episode, candidate.capture_plan(episode)),
        argv=("python3", "-c", "import time; time.sleep(120)"),
        outputs=(),
        timeout_seconds=150,
    )
    ready = threading.Event()
    errors: list[str] = []

    def bound(execution: ExecutionRef) -> None:
        with store.lock(episode_id):
            current = store.load(episode_id)
            assert current is not None
            current.inference = execution
            current.cleanup[execution.stage_id] = "pending"
            store.save(current)
        if execution.command_pid is not None:
            ready.set()

    def execute() -> None:
        try:
            AkernelBackend().execute(
                plan, artifact_dir=EVIDENCE / "cancel-artifacts", on_bound=bound
            )
        except Exception as error:
            errors.append(type(error).__name__)

    worker = threading.Thread(target=execute)
    worker.start()
    if not ready.wait(timeout=90):
        raise RuntimeError("cancellation command never became bound")
    command(["cancel", episode_id], f"{episode_id}-cancel")
    command(["cancel", episode_id], f"{episode_id}-cancel-again")
    worker.join(timeout=30)
    if worker.is_alive() or store.load(episode_id).phase != EpisodePhase.CANCELLED:
        raise RuntimeError("cancellation did not finish")
    empty_inventory()
    (EVIDENCE / "cancellation-control.json").write_text(
        json.dumps(
            {
                "record": store.load(episode_id).as_dict(),
                "observer_errors": errors,
                "control": "model-free real SDK command cancelled through normal CLI",
            },
            indent=2,
        )
        + "\n"
    )
    print("model-free live cancellation and repeated cancellation passed; SDK inventory empty")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=["static", "model", "recovery", "cancel", "disconnect-worker"]
    )
    parser.add_argument("--episode-id")
    args = parser.parse_args()
    configure()
    if args.phase == "static":
        static_controls(args.episode_id or "control")
    elif args.phase == "cancel":
        cancellation_control(args.episode_id or "cancel-control-v1")
    elif args.phase == "disconnect-worker":
        return disconnect_worker(args.episode_id)
    else:
        model_control(
            args.episode_id or f"claude-{args.phase}-v1", disconnect=args.phase == "recovery"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
