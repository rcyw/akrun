"""Finite stages using only the published AKernel SDK and its public CLI."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from akernel_sdk import Mount, NetworkPolicy
from akernel_sdk.sandbox import Sandbox

from akrun.errors import ContractError, InfrastructureError, RecoveryRequiredError
from akrun.models import (
    Artifact,
    ExecutionRef,
    StageNetworkPolicy,
    StagePlan,
    StageResult,
    canonical_digest,
)

_ROOT = "/run/akrun"


def sdk_inventory() -> set[str]:
    """Inventory is exposed by the SDK's public CLI, not a Python list API."""
    result = subprocess.run(
        [sys.executable, "-m", "akernel_sdk.cli", "list", "--quiet"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RecoveryRequiredError("sandbox_inventory_unavailable")
    return set(result.stdout.splitlines())


class AkernelBackend:
    def __init__(
        self,
        *,
        sandbox_factory: Callable[..., Any] = Sandbox,
        delete_sandbox: Callable[[str], None] = Sandbox.delete,
        inventory: Callable[[], set[str]] = sdk_inventory,
        poll_interval: float = 0.25,
    ) -> None:
        self._factory = sandbox_factory
        self._delete = delete_sandbox
        self._inventory = inventory
        self.poll_interval = poll_interval

    def execute(
        self,
        plan: StagePlan,
        *,
        artifact_dir: Path,
        on_bound: Callable[[ExecutionRef], None],
        model: Any = None,
    ) -> StageResult:
        stage_id = uuid.uuid4().hex
        execution = ExecutionRef(stage_id, f"akr-{stage_id}")
        # The name is durable even if sandbox creation's response is lost.
        on_bound(execution)
        sandbox = None
        try:
            tunnel = model.start() if model is not None else None
            sandbox = self._factory(
                image=plan.sandbox.image,
                runtime=plan.sandbox.runtime,
                cpu=plan.resources.cpu,
                memory=plan.resources.memory,
                storage_mb=plan.resources.storage_mb,
                name=execution.sandbox_name,
                detached=True,
                idle_timeout=max(1800, plan.timeout_seconds + 300),
                mounts=[
                    Mount(target=m.target, image_url=m.image, read_only=True)
                    for m in plan.image_mounts
                ],
                network_policy=(
                    NetworkPolicy.block()
                    if plan.network_policy == StageNetworkPolicy.DENY_ALL
                    else None
                ),
                reverse_tunnel=tunnel,
            )
            execution = replace(execution, sandbox_id=sandbox.id)
            on_bound(execution)
            sandbox.files.make_dir(_ROOT)
            identity = {
                "stage_id": execution.stage_id,
                "sandbox_id": execution.sandbox_id,
                "plan_digest": canonical_digest(asdict(plan)),
            }
            sandbox.files.write(f"{_ROOT}/identity.json", json.dumps(identity))
            for item in plan.inputs:
                source = Path(item.source)
                if item.archive or source.is_symlink() or not source.is_file():
                    raise ContractError("stage input must be a regular file")
                if item.sha256 and _sha256(source) != item.sha256:
                    raise ContractError("stage input digest mismatch")
                sandbox.files.make_dir(str(Path(item.target).parent))
                sandbox.files.copy_from_local(str(source), item.target)
            supervisor = Path(__file__).parent / "fixtures/stage_supervisor.py"
            sandbox.files.make_dir("/opt/akrun")
            sandbox.files.copy_from_local(str(supervisor), "/opt/akrun/stage_supervisor.py")
            env = dict(plan.env)
            if model is not None:
                model.preflight(sandbox)
                env["ANTHROPIC_BASE_URL"] = sandbox.reverse_tunnel.url
            spec = {
                **identity,
                "argv": list(plan.argv),
                "cwd": plan.cwd,
                "env": env,
                "outputs": [asdict(output) for output in plan.outputs],
                "timeout_seconds": plan.timeout_seconds,
            }
            sandbox.files.write(f"{_ROOT}/stage.json", json.dumps(spec))
            execution = replace(execution, launch_intent=True)
            on_bound(execution)
            command = shlex.join(
                [
                    "python3",
                    "/opt/akrun/stage_supervisor.py",
                    "--spec",
                    f"{_ROOT}/stage.json",
                    "--stage-id",
                    stage_id,
                ]
            )
            try:
                handle = sandbox.commands.run(command, background=True)
            except Exception:
                raise RecoveryRequiredError("stage_dispatch_outcome_unknown") from None
            execution = replace(execution, command_pid=handle.pid)
            on_bound(execution)
            result = self._wait(
                sandbox, execution, plan, artifact_dir, timeout=plan.timeout_seconds + 30
            )
            if result is None:
                raise RecoveryRequiredError("stage_wait_timeout")
            return result
        finally:
            try:
                if sandbox is not None:
                    sandbox.kill()  # detached=True closes clients, preserving recoverability
            finally:
                if model is not None:
                    model.close()

    def _open(self, execution: ExecutionRef) -> Any:
        if not execution.sandbox_id or execution.sandbox_id not in self._inventory():
            raise RecoveryRequiredError("original_sandbox_unavailable")
        sandbox = self._factory(name=execution.sandbox_name, detached=True)
        try:
            if sandbox.id != execution.sandbox_id:
                raise RecoveryRequiredError("sandbox_identity_changed")
            raw = json.loads(sandbox.files.read(f"{_ROOT}/identity.json"))
            if raw["stage_id"] != execution.stage_id or raw["sandbox_id"] != execution.sandbox_id:
                raise RecoveryRequiredError("stage_identity_changed")
        except Exception:
            sandbox.kill()
            raise RecoveryRequiredError("original_stage_identity_unconfirmed") from None
        return sandbox

    def recover(
        self, execution: ExecutionRef, plan: StagePlan, *, artifact_dir: Path, timeout: float = 0
    ) -> StageResult | None:
        sandbox = self._open(execution)
        try:
            return self._wait(sandbox, execution, plan, artifact_dir, timeout=timeout)
        finally:
            sandbox.kill()

    def _wait(
        self,
        sandbox: Any,
        execution: ExecutionRef,
        plan: StagePlan,
        artifact_dir: Path,
        *,
        timeout: float,
    ) -> StageResult | None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sandbox.files.exists(f"{_ROOT}/result.json"):
                    return self._collect(sandbox, execution, plan, artifact_dir)
                commands = sandbox.commands.list()
                live = any(p.running and execution.stage_id in p.command for p in commands)
                if not live:
                    raise RecoveryRequiredError("stage_outcome_unconfirmed")
            except (RecoveryRequiredError, ContractError):
                raise
            except Exception:
                raise RecoveryRequiredError("stage_observation_lost") from None
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval)

    def _collect(
        self, sandbox: Any, execution: ExecutionRef, plan: StagePlan, artifact_dir: Path
    ) -> StageResult:
        raw = json.loads(sandbox.files.read(f"{_ROOT}/result.json"))
        if (
            raw.get("stage_id") != execution.stage_id
            or raw.get("sandbox_id") != execution.sandbox_id
            or raw.get("plan_digest") != canonical_digest(asdict(plan))
        ):
            raise ContractError("stage result provenance mismatch")
        code = raw.get("exit_code")
        if type(code) is not int:
            raise ContractError("invalid stage exit code")
        declared = {output.path: output for output in plan.outputs}
        outputs = raw.get("outputs")
        if not isinstance(outputs, list):
            raise ContractError("invalid stage output manifest")
        seen: set[str] = set()
        artifacts: list[Artifact] = []
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for raw_item in cast(list[object], outputs):
            if not isinstance(raw_item, dict):
                raise ContractError("invalid output entry")
            item = cast(dict[str, Any], raw_item)
            path = item.get("path")
            if not isinstance(path, str) or path not in declared or path in seen:
                raise ContractError("unexpected or duplicate stage output")
            seen.add(path)
            expected = declared[path]
            size = item.get("size_bytes")
            digest = item.get("sha256")
            if (
                type(size) is not int
                or not 0 <= size <= expected.max_bytes
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or item.get("media_type") != expected.media_type
            ):
                raise ContractError("invalid output digest, length or media type")
            if sandbox.files.get_info(path).size != size:
                raise ContractError("remote output length changed")
            destination = artifact_dir / f"{len(artifacts):02d}-{Path(path).name}"
            fd, temporary = tempfile.mkstemp(prefix=".download-", dir=artifact_dir)
            os.close(fd)
            try:
                sandbox.files.copy_to_local(path, temporary)
                downloaded = Path(temporary)
                if downloaded.stat().st_size != size or _sha256(downloaded) != digest:
                    raise ContractError("downloaded output integrity mismatch")
                with downloaded.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(downloaded, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
            artifacts.append(Artifact(path, str(destination), size, digest, expected.media_type))
        if code == 0 and seen != set(declared):
            raise ContractError("required stage output missing")
        return StageResult(execution, code, str(raw.get("diagnostic_code", "")), tuple(artifacts))

    def inspect(self, execution: ExecutionRef) -> dict[str, Any]:
        sandbox = self._open(execution)
        try:
            return {
                "sandbox_id": sandbox.id,
                "commands": [asdict(command) for command in sandbox.commands.list()],
                "terminal": sandbox.files.exists(f"{_ROOT}/result.json"),
            }
        finally:
            sandbox.kill()

    def cancel(self, execution: ExecutionRef) -> None:
        if execution.sandbox_id and execution.sandbox_id not in self._inventory():
            return
        # Exact task-generated names were persisted before creation; deletion also
        # resolves a lost creation response, when no physical ID was returned.
        self._delete(execution.sandbox_name)
        if execution.sandbox_id and execution.sandbox_id in self._inventory():
            raise InfrastructureError("sandbox_deletion_unconfirmed")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
