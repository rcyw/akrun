"""Model-free checks on the actual task and read-only Claude runtime images."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from akrun._atomic_file import atomic_write
from akrun.backend import ExecutionBackend
from akrun.catalog import adapters
from akrun.errors import ContractError, RecoveryRequiredError
from akrun.models import (
    ExecutionRef,
    ImageMountSpec,
    OutputSpec,
    ResolvedEpisode,
    StagePlan,
    canonical_digest,
)
from akrun.store import EpisodeStore


def qualify(
    episode: ResolvedEpisode, backend: ExecutionBackend, store: EpisodeStore
) -> dict[str, object]:
    harness, _, _, _ = adapters(episode)
    with store.lock(episode.episode_id):
        record = store.initialize(episode)
        if record.cancel_requested:
            raise RecoveryRequiredError("episode cancellation requested")
        if record.qualification_result:
            payload = json.loads(Path(record.qualification_result).read_text())
            if canonical_digest(payload) != record.qualification_result_digest:
                raise ContractError("qualification integrity mismatch")
            return payload
    refs: list[ExecutionRef] = []

    def bound(execution: ExecutionRef) -> None:
        with store.lock(episode.episode_id):
            record = store.load(episode.episode_id)
            assert record is not None
            if record.cancel_requested:
                raise RecoveryRequiredError("qualification cancelled")
            existing = {ref.stage_id: ref for ref in record.qualifications}
            existing[execution.stage_id] = execution
            record.qualifications = tuple(existing.values())
            store.save(record)

    for role, binding, resources in (
        ("inference", episode.inference_sandbox, episode.inference_resources),
        ("verification", episode.verification_sandbox, episode.verification_resources),
    ):
        requirements = harness.qualification_requirements(episode)
        mounts = (
            (ImageMountSpec(requirements.claude_mount_image, "/__claude_code"),)
            if role == "inference" and requirements.claude_mount_image
            else ()
        )
        checks = [
            "set -eu",
            "mkdir -p /outputs /run/akrun",
            f'test "$(git rev-parse HEAD)" = {episode.task.config["base_commit"]}',
            'test -z "$(git status --porcelain --untracked-files=all)"',
            "python3 -c 'import json,hashlib,unittest,ssl,socket'",
            (
                "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); "
                'assert s.connect_ex(("1.1.1.1",443)) != 0\''
            ),
        ]
        if mounts:
            checks += [
                "/__claude_code/usr/local/bin/claude --version | grep -F 2.1.205",
                "test -f /__claude_code/opt/claude-code/manifest.json",
                "if touch /__claude_code/.akrun-write-probe 2>/dev/null; then exit 3; fi",
            ]
        checks += ["printf '%s\\n' '{\"qualified\":true}' > /outputs/qualification.json"]
        plan = StagePlan(
            binding,
            ("/bin/sh", "-lc", "\n".join(checks)),
            binding.working_directory,
            (OutputSpec("/outputs/qualification.json", media_type="application/json"),),
            image_mounts=mounts,
            resources=resources,
            timeout_seconds=90,
        )
        result = backend.execute(
            plan,
            artifact_dir=store.root / "artifacts" / episode.episode_id / f"qualify-{role}",
            on_bound=bound,
        )
        refs.append(result.execution)
        try:
            if result.exit_code != 0:
                raise ContractError(f"{role} qualification failed: {result.diagnostic_code}")
        finally:
            backend.cancel(result.execution)
            with store.lock(episode.episode_id):
                record = store.load(episode.episode_id)
                assert record is not None
                record.cleanup[result.execution.stage_id] = "released"
                store.save(record)
    payload: dict[str, object] = {
        "schema_version": 1,
        "spec_digest": episode.digest,
        "sdk_version": "0.2.0rc3",
        "executions": [asdict(ref) for ref in refs],
    }
    path = store.root / "artifacts" / episode.episode_id / "qualification.json"
    atomic_write(path, json.dumps(payload, sort_keys=True).encode())
    with store.lock(episode.episode_id):
        record = store.load(episode.episode_id)
        assert record is not None
        if record.cancel_requested:
            raise RecoveryRequiredError("qualification cancelled before publication")
        record.qualification_result = str(path)
        record.qualification_result_digest = canonical_digest(payload)
        store.save(record)
    return payload
