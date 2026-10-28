"""Closed v1 catalog; adapters do not own remote execution lifecycles."""

import re

from akrun.adapters.base import InferenceAdapter, TrajectoryAdapter
from akrun.adapters.static_candidate import StaticCandidateHarness
from akrun.adapters.synthetic_verifier import SyntheticVerifierAdapter
from akrun.candidates.git_patch import GitPatchCandidateAdapter
from akrun.errors import ContractError
from akrun.harnesses.claude_code import ClaudeCodeHarness
from akrun.models import ResolvedEpisode
from akrun.trajectories.adapters import ClaudeCodeTrajectoryAdapter


def adapters(
    episode: ResolvedEpisode,
) -> tuple[
    InferenceAdapter, GitPatchCandidateAdapter, SyntheticVerifierAdapter, TrajectoryAdapter | None
]:
    if not re.fullmatch(r"[0-9a-f]{40}", str(episode.task.config.get("base_commit", ""))):
        raise ContractError("task base_commit must be a lowercase Git SHA")
    if (
        (episode.task.identity, episode.task.version) != ("akrun.synthetic.code-task", "1")
        or (episode.candidate.identity, episode.candidate.version) != ("git-patch", "1")
        or (episode.verifier.identity, episode.verifier.version) != ("synthetic-code-task", "1")
    ):
        raise ContractError("unsupported v1 task, candidate or verifier")
    if (episode.harness.identity, episode.harness.version) == ("claude-code", "2.1.205"):
        return (
            ClaudeCodeHarness(),
            GitPatchCandidateAdapter(),
            SyntheticVerifierAdapter(),
            ClaudeCodeTrajectoryAdapter(),
        )
    if (episode.harness.identity, episode.harness.version) == ("static-candidate", "1"):
        return (
            StaticCandidateHarness(),
            GitPatchCandidateAdapter(),
            SyntheticVerifierAdapter(),
            None,
        )
    raise ContractError("unsupported v1 harness")
