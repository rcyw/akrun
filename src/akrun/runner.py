"""Recoverable caller-side inference, artifact publication and fresh verification."""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from akrun._atomic_file import atomic_write
from akrun.adapters._candidate import load_candidate
from akrun.backend import ExecutionBackend
from akrun.catalog import adapters
from akrun.errors import ContractError, InfrastructureError, RecoveryRequiredError
from akrun.models import (
    CandidateBundle,
    EpisodePhase,
    EpisodeRecord,
    ExecutionRef,
    ResolvedEpisode,
    StageResult,
    VerificationResult,
)
from akrun.provenance import validate_candidate_provenance, validate_result_provenance
from akrun.qualification import qualify
from akrun.store import EpisodeStore
from akrun.trajectories.bundle import load_trajectory_bundle


class EpisodeRunner:
    def __init__(self, *, backend: ExecutionBackend, store: EpisodeStore) -> None:
        self.backend, self.store = backend, store

    def run(self, episode: ResolvedEpisode, *, model: Any = None) -> VerificationResult:
        qualify(episode, self.backend, self.store)
        with self.store.lock(episode.episode_id):
            record = self._record(episode.episode_id)
            if record.phase == EpisodePhase.COMPLETED:
                return self.result(episode.episode_id)
            if record.phase != EpisodePhase.NEW or record.cancel_requested:
                raise RecoveryRequiredError("episode already started; inspect, resume or cancel it")
            record.phase = EpisodePhase.INFERENCE_RUNNING
            self.store.save(record)
        harness, candidate, _, _ = adapters(episode)
        plan = harness.plan(episode, candidate.capture_plan(episode))
        try:
            stage = self.backend.execute(
                plan,
                artifact_dir=self._artifacts(episode.episode_id, "inference"),
                on_bound=lambda ref: self._bind(episode.episode_id, "inference", ref),
                model=model,
            )
            return self._inference_finished(episode, stage)
        except BaseException as exc:
            self._failure(episode.episode_id, exc)
            raise
        finally:
            if model is not None:
                atomic_write(
                    self._artifacts(episode.episode_id, "inference") / "model-summary.json",
                    json.dumps(model.summary(), sort_keys=True).encode(),
                )

    def resume(self, episode_id: str, *, timeout: float = 0) -> VerificationResult:
        record = self._record(episode_id)
        episode = self.store.load_spec(episode_id)
        if record.cancel_requested and record.phase != EpisodePhase.COMPLETED:
            raise RecoveryRequiredError("episode cancellation requested; retry cancel")
        if record.phase == EpisodePhase.COMPLETED:
            self.cleanup(episode_id)
            return self.result(episode_id)
        try:
            if record.phase == EpisodePhase.CANDIDATE_READY:
                self._release(episode_id, record.inference)
                return self._verify(episode, self._candidate(episode_id))
            if record.phase not in {
                EpisodePhase.INFERENCE_RUNNING,
                EpisodePhase.VERIFICATION_RUNNING,
            }:
                raise RecoveryRequiredError("episode has no recoverable active execution")
            harness, candidate, verifier, _ = adapters(episode)
            inference = record.phase == EpisodePhase.INFERENCE_RUNNING
            execution = record.inference if inference else record.verification
            if execution is None:
                raise RecoveryRequiredError(
                    "dispatch identity was not confirmed; inference cannot be replayed"
                )
            plan = (
                harness.plan(episode, candidate.capture_plan(episode))
                if inference
                else verifier.plan(episode, self._candidate(episode_id))
            )
            stage = self.backend.recover(
                execution,
                plan,
                artifact_dir=self._artifacts(
                    episode_id, "inference" if inference else "verification"
                ),
                timeout=timeout,
            )
            if stage is None:
                raise RecoveryRequiredError(
                    "original stage is still running; no new inference was submitted"
                )
            return (
                self._inference_finished(episode, stage)
                if inference
                else self._verified(episode, stage)
            )
        except BaseException as exc:
            self._failure(episode_id, exc)
            raise

    def _inference_finished(
        self, episode: ResolvedEpisode, stage: StageResult
    ) -> VerificationResult:
        self._require_stage(episode.episode_id, "inference", stage.execution)
        if stage.exit_code:
            raise InfrastructureError(f"inference failed: {stage.diagnostic_code}")
        _, candidate_adapter, _, trajectory_adapter = adapters(episode)
        candidate = candidate_adapter.build(
            episode, stage, destination=self.store.root / "candidates"
        )
        validate_candidate_provenance(candidate, episode, stage.execution)
        trajectory = (
            trajectory_adapter.build_bundle(
                episode, stage, destination=self.store.root / "trajectories"
            )
            if trajectory_adapter is not None
            else None
        )
        with self.store.lock(episode.episode_id):
            record = self._require_stage(episode.episode_id, "inference", stage.execution)
            record.candidate_manifest = str(Path(candidate.root) / "candidate-manifest.json")
            record.candidate_digest = candidate.digest
            if trajectory is not None:
                record.trajectory_manifest = str(Path(trajectory.root) / "trajectory-manifest.json")
                record.trajectory_digest = trajectory.digest
            record.phase = EpisodePhase.CANDIDATE_READY
            record.diagnostic_code = record.message = ""
            self.store.save(record)
        self._release(episode.episode_id, stage.execution)
        return self._verify(episode, candidate)

    def _verify(self, episode: ResolvedEpisode, candidate: CandidateBundle) -> VerificationResult:
        with self.store.lock(episode.episode_id):
            record = self._record(episode.episode_id)
            if record.phase != EpisodePhase.CANDIDATE_READY or record.cancel_requested:
                raise RecoveryRequiredError("episode no longer accepts verification")
            record.phase = EpisodePhase.VERIFICATION_RUNNING
            self.store.save(record)
        _, _, verifier, _ = adapters(episode)
        stage = self.backend.execute(
            verifier.plan(episode, candidate),
            artifact_dir=self._artifacts(episode.episode_id, "verification"),
            on_bound=lambda ref: self._bind(episode.episode_id, "verification", ref),
        )
        return self._verified(episode, stage)

    def _verified(self, episode: ResolvedEpisode, stage: StageResult) -> VerificationResult:
        self._require_stage(episode.episode_id, "verification", stage.execution)
        _, _, verifier, _ = adapters(episode)
        result = verifier.parse_result(stage)
        validate_result_provenance(result, episode, self._candidate(episode.episode_id))
        with self.store.lock(episode.episode_id):
            record = self._require_stage(episode.episode_id, "verification", stage.execution)
            path, digest = self.store.save_result(result)
            record.verification_result, record.verification_result_digest = str(path), digest
            record.phase, record.completed_at = EpisodePhase.COMPLETED, _now()
            record.diagnostic_code, record.message = result.diagnostic_code, ""
            self.store.save(record)
        self.cleanup(episode.episode_id)
        return result

    def _bind(self, episode_id: str, role: str, execution: ExecutionRef) -> None:
        with self.store.lock(episode_id):
            record = self._record(episode_id)
            phase = (
                EpisodePhase.INFERENCE_RUNNING
                if role == "inference"
                else EpisodePhase.VERIFICATION_RUNNING
            )
            if record.phase != phase or record.cancel_requested:
                raise RecoveryRequiredError("stage cancelled before dispatch")
            existing = getattr(record, role)
            if existing is not None and (
                existing.stage_id != execution.stage_id
                or (existing.sandbox_id and existing.sandbox_id != execution.sandbox_id)
            ):
                raise ContractError("execution identity changed")
            if (
                role == "verification"
                and record.inference is not None
                and (
                    record.inference.sandbox_name == execution.sandbox_name
                    or (
                        execution.sandbox_id and record.inference.sandbox_id == execution.sandbox_id
                    )
                )
            ):
                raise ContractError("verification must use a fresh Sandbox")
            setattr(record, role, execution)
            record.cleanup.setdefault(execution.stage_id, "pending")
            self.store.save(record)

    def _require_stage(self, episode_id: str, role: str, execution: ExecutionRef) -> EpisodeRecord:
        record = self._record(episode_id)
        expected = getattr(record, role)
        phase = (
            EpisodePhase.INFERENCE_RUNNING
            if role == "inference"
            else EpisodePhase.VERIFICATION_RUNNING
        )
        if (
            record.phase != phase
            or record.cancel_requested
            or expected is None
            or expected.stage_id != execution.stage_id
            or expected.sandbox_id != execution.sandbox_id
        ):
            raise RecoveryRequiredError("stage result no longer matches the active episode")
        return record

    def _candidate(self, episode_id: str) -> CandidateBundle:
        record = self._record(episode_id)
        candidate = load_candidate(Path(record.candidate_manifest))
        if candidate.digest != record.candidate_digest:
            raise ContractError("candidate record digest mismatch")
        validate_candidate_provenance(candidate, self.store.load_spec(episode_id), record.inference)
        return candidate

    def result(self, episode_id: str) -> VerificationResult:
        record = self._record(episode_id)
        candidate = self._candidate(episode_id)
        if record.trajectory_manifest:
            trajectory = load_trajectory_bundle(Path(record.trajectory_manifest))
            if (
                trajectory.digest != record.trajectory_digest
                or trajectory.inference_stage_id != candidate.inference_stage_id
                or trajectory.inference_sandbox_id != candidate.inference_sandbox_id
            ):
                raise ContractError("trajectory provenance mismatch")
        result = self.store.load_result(
            record.verification_result, record.verification_result_digest
        )
        validate_result_provenance(result, self.store.load_spec(episode_id), candidate)
        if (
            record.inference is None
            or record.verification is None
            or record.inference.sandbox_id == record.verification.sandbox_id
        ):
            raise ContractError("verification Sandbox provenance mismatch")
        return result

    def _release(self, episode_id: str, execution: ExecutionRef | None) -> None:
        if (
            execution is None
            or self._record(episode_id).cleanup.get(execution.stage_id) == "released"
        ):
            return
        try:
            self.backend.cancel(execution)
        except Exception:
            with self.store.lock(episode_id):
                record = self._record(episode_id)
                record.cleanup[execution.stage_id] = "retry_required"
                self.store.save(record)
            raise InfrastructureError("resource_cleanup_required") from None
        with self.store.lock(episode_id):
            record = self._record(episode_id)
            record.cleanup[execution.stage_id] = "released"
            self.store.save(record)

    def cleanup(self, episode_id: str) -> EpisodeRecord:
        record = self._record(episode_id)
        if record.phase in {EpisodePhase.INFERENCE_RUNNING, EpisodePhase.VERIFICATION_RUNNING}:
            raise RecoveryRequiredError("active episode must be cancelled before cleanup")
        failures = False
        for execution in (*record.qualifications, record.inference, record.verification):
            try:
                self._release(episode_id, execution)
            except InfrastructureError:
                failures = True
        if failures:
            raise InfrastructureError("resource_cleanup_required")
        return self._record(episode_id)

    def cancel(self, episode_id: str) -> EpisodeRecord:
        with self.store.lock(episode_id):
            record = self._record(episode_id)
            terminal = record.phase in {
                EpisodePhase.COMPLETED,
                EpisodePhase.FAILED,
                EpisodePhase.CANCELLED,
            }
            if not terminal:
                record.cancel_requested, record.phase = True, EpisodePhase.CANCELLING
                self.store.save(record)
        self.cleanup(episode_id)
        with self.store.lock(episode_id):
            record = self._record(episode_id)
            if record.phase == EpisodePhase.CANCELLING:
                record.phase, record.diagnostic_code, record.completed_at = (
                    EpisodePhase.CANCELLED,
                    "cancelled",
                    _now(),
                )
                self.store.save(record)
            return record

    def _failure(self, episode_id: str, error: BaseException) -> None:
        with self.store.lock(episode_id):
            record = self._record(episode_id)
            if record.phase in {
                EpisodePhase.COMPLETED,
                EpisodePhase.CANCELLING,
                EpisodePhase.CANCELLED,
            }:
                return
            if isinstance(error, RecoveryRequiredError):
                record.diagnostic_code, record.message = "recovery_required", str(error)
            elif record.phase == EpisodePhase.CANDIDATE_READY and isinstance(
                error, InfrastructureError
            ):
                record.diagnostic_code = "resource_cleanup_required"
            else:
                record.phase, record.completed_at = EpisodePhase.FAILED, _now()
                record.diagnostic_code, record.message = (
                    "stage_failed",
                    "stage failed; inspect local evidence",
                )
            self.store.save(record)
        if record.phase == EpisodePhase.FAILED:
            with suppress(InfrastructureError):
                self.cleanup(episode_id)

    def _record(self, episode_id: str) -> EpisodeRecord:
        record = self.store.load(episode_id)
        if record is None:
            raise ContractError("episode not found")
        return record

    def _artifacts(self, episode_id: str, role: str) -> Path:
        return self.store.root / "artifacts" / episode_id / role


def _now() -> str:
    return datetime.now(UTC).isoformat()
