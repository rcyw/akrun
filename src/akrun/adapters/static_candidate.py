"""Deterministic model-free harness used for candidate contract acceptance."""

from __future__ import annotations

from dataclasses import dataclass

from akrun.adapters.base import HarnessQualificationRequirements
from akrun.errors import ContractError
from akrun.models import (
    CandidateCapturePlan,
    HarnessRuntimeRequirements,
    ResolvedEpisode,
    StagePlan,
)


@dataclass(frozen=True, slots=True)
class StaticCandidateHarness:
    version: str = "1"
    name: str = "static-candidate"

    @property
    def runtime_requirements(self) -> HarnessRuntimeRequirements:
        return HarnessRuntimeRequirements()

    def qualification_requirements(
        self, episode: ResolvedEpisode
    ) -> HarnessQualificationRequirements:
        self._validate(episode)
        return HarnessQualificationRequirements()

    def plan(self, episode: ResolvedEpisode, capture: CandidateCapturePlan) -> StagePlan:
        self._validate(episode)
        return StagePlan(
            sandbox=episode.inference_sandbox,
            argv=(
                "/bin/sh",
                "-lc",
                "\n".join(
                    (
                        "set -eu",
                        "mkdir -p /outputs /run/akrun",
                        capture.setup_script,
                        capture.finalize_script,
                    )
                ),
            ),
            cwd=episode.inference_sandbox.working_directory,
            inputs=capture.inputs,
            outputs=capture.outputs,
            resources=episode.inference_resources,
            network_policy=episode.inference_network,
            timeout_seconds=episode.harness.timeout_seconds,
            labels={"akrun.stage": "inference", "akrun.agent": self.name},
        )

    def _validate(self, episode: ResolvedEpisode) -> None:
        if (episode.harness.identity, episode.harness.version) != (self.name, self.version):
            raise ContractError("static candidate harness requires static-candidate@1")
