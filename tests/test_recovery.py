from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from akrun.adapters._candidate import load_candidate
from akrun.datasets.synthetic import SyntheticCodeTaskResolver
from akrun.errors import ContractError, InfrastructureError, RecoveryRequiredError
from akrun.models import Artifact, EpisodePhase, ExecutionRef, HarnessSpec, StageResult
from akrun.runner import EpisodeRunner
from akrun.store import EpisodeStore

FIXTURE = Path(__file__).parents[1] / "fixtures/synthetic/code-task-v1"


def episode(episode_id: str = "test", *, claude: bool = False):
    harness = (
        HarnessSpec(
            "claude-code",
            "2.1.205",
            config={
                "model": "deepseek-flash",
                "mount_image": "registry/claude@sha256:" + "2" * 64,
            },
        )
        if claude
        else HarnessSpec("static-candidate", "1", config={"candidate_file": "gold.patch"})
    )
    return SyntheticCodeTaskResolver().resolve(
        json.loads((FIXTURE / "row.json").read_text()),
        source_dir=FIXTURE,
        episode_id=episode_id,
        task_image="registry/task@sha256:" + "1" * 64,
        task_platform="linux/amd64",
        harness=harness,
    )


class Backend:
    """Protocol fake; live SDK acceptance is deliberately separate."""

    def __init__(self, root: Path):
        self.root = root
        self.results = {}
        self.calls = []
        self.deleted = []
        self.lose_inference = False
        self.fail_cleanup = False
        self.verdict = True
        self.on_inference = None

    def execute(self, plan, *, artifact_dir, on_bound, model=None):
        stage_id = str(len(self.calls) + 1)
        role = (
            "inference" if "/outputs/candidate.patch" in [o.path for o in plan.outputs] else "other"
        )
        self.calls.append((role, plan))
        ref = ExecutionRef(stage_id, f"name-{stage_id}")
        on_bound(ref)
        ref = replace(ref, sandbox_id=f"sandbox-{stage_id}", launch_intent=True, command_pid=42)
        on_bound(ref)
        if role == "inference" and self.on_inference is not None:
            self.on_inference()
        artifacts = []
        for index, output in enumerate(plan.outputs):
            if output.path.endswith("candidate.patch"):
                data = (FIXTURE / "gold.patch").read_bytes()
            elif output.path.endswith("verification.json"):
                candidate_digest = plan.argv[plan.argv.index("--candidate-digest") + 1]
                data = json.dumps(
                    {"resolved": self.verdict, "candidate_digest": candidate_digest}
                ).encode()
            else:
                data = b"{}"
            path = self.root / f"{stage_id}-{index}"
            path.write_bytes(data)
            artifacts.append(
                Artifact(
                    output.path,
                    str(path),
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    output.media_type,
                )
            )
        result = StageResult(ref, 0, "", tuple(artifacts))
        self.results[stage_id] = result
        if role == "inference" and self.lose_inference:
            self.lose_inference = False
            raise RecoveryRequiredError("dispatch_response_lost")
        return result

    def recover(self, execution, plan, *, artifact_dir, timeout=0):
        return self.results.get(execution.stage_id)

    def cancel(self, execution):
        if self.fail_cleanup and execution.stage_id == "4":
            self.fail_cleanup = False
            raise RuntimeError("transient deletion failure")
        self.deleted.append(execution.sandbox_name)

    def inspect(self, execution):
        return {}


def setup(tmp_path):
    backend = Backend(tmp_path)
    store = EpisodeStore(tmp_path / "state")
    return EpisodeRunner(backend=backend, store=store), backend, store


def test_lost_dispatch_recovers_original_inference_and_is_idempotent(tmp_path):
    runner, backend, store = setup(tmp_path)
    backend.lose_inference = True
    with pytest.raises(RecoveryRequiredError):
        runner.run(episode())
    original = store.load("test").inference
    assert store.load("test").phase == EpisodePhase.INFERENCE_RUNNING
    assert runner.resume("test").verdict == "passed"
    assert store.load("test").inference == original
    assert len([role for role, _ in backend.calls if role == "inference"]) == 1
    assert runner.resume("test").verdict == "passed"
    assert len(backend.calls) == 4  # two qualifications, one inference, one verifier
    assert set(store.load("test").cleanup.values()) == {"released"}


def test_missing_dispatch_identity_never_resubmits(tmp_path):
    runner, backend, store = setup(tmp_path)
    record = store.initialize(episode())
    record.phase = EpisodePhase.INFERENCE_RUNNING
    store.save(record)
    with pytest.raises(RecoveryRequiredError, match="cannot be replayed"):
        runner.resume("test")
    assert backend.calls == []


def test_cancel_wins_race_with_inference_publication(tmp_path):
    runner, backend, store = setup(tmp_path)
    backend.on_inference = lambda: runner.cancel("test")
    with pytest.raises(RecoveryRequiredError):
        runner.run(episode())
    record = store.load("test")
    assert record.phase == EpisodePhase.CANCELLED
    assert record.candidate_manifest == ""
    assert len(backend.calls) == 3
    assert runner.cancel("test").phase == EpisodePhase.CANCELLED


def test_cleanup_retries_preserve_published_verdict(tmp_path):
    runner, backend, store = setup(tmp_path)
    backend.fail_cleanup = True
    with pytest.raises(InfrastructureError, match="cleanup_required"):
        runner.run(episode())
    assert store.load("test").phase == EpisodePhase.COMPLETED
    assert store.load("test").cleanup["4"] == "retry_required"
    assert runner.result("test").verdict == "passed"
    runner.cleanup("test")
    assert set(store.load("test").cleanup.values()) == {"released"}


def test_verifier_failure_is_a_published_result(tmp_path):
    runner, backend, store = setup(tmp_path)
    backend.verdict = False
    assert runner.run(episode()).verdict == "failed"
    assert store.load("test").phase == EpisodePhase.COMPLETED
    assert set(store.load("test").cleanup.values()) == {"released"}


def test_candidate_corruption_fails_closed(tmp_path):
    runner, _, store = setup(tmp_path)
    runner.run(episode())
    bundle = load_candidate(Path(store.load("test").candidate_manifest))
    (Path(bundle.root) / bundle.files[0].bundle_path).write_bytes(b"tampered")
    with pytest.raises(ContractError, match="integrity"):
        runner.result("test")


def test_claude_aliases_are_materialized_without_model_tier_fallback():
    resolved = episode(claude=True)
    for key in [
        "default_opus_model",
        "default_sonnet_model",
        "default_haiku_model",
        "subagent_model",
    ]:
        assert resolved.harness.config[key] == "deepseek-flash"


def test_spec_digest_prevents_changed_episode_reuse(tmp_path):
    runner, _, _ = setup(tmp_path)
    resolved = episode()
    runner.run(resolved)
    with pytest.raises(RecoveryRequiredError, match="digest differs"):
        runner.run(replace(resolved, metadata={"changed": "yes"}))


def test_public_json_schemas_match_published_artifacts(tmp_path):
    runner, _, store = setup(tmp_path)
    runner.run(episode())
    record = store.load("test")
    root = Path(__file__).parents[1] / "schemas"
    for name, path in [
        ("resolved-episode-v1", store.spec_path_for("test")),
        ("candidate-bundle-v1", Path(record.candidate_manifest)),
        ("verification-result-v1", Path(record.verification_result)),
    ]:
        jsonschema.validate(
            json.loads(path.read_text()), json.loads((root / f"{name}.schema.json").read_text())
        )
