from __future__ import annotations

import json
from pathlib import Path

import pytest

from akrun.cli import main
from akrun.errors import ContractError
from akrun.harnesses.claude_code import resolve_claude_code_spec
from akrun.models import HarnessSpec, ResourceSpec, resolved_episode_from_dict


def test_resolve_cli_roundtrips_closed_public_contract_without_sdk_connection(tmp_path, capsys):
    row = Path(__file__).parents[1] / "fixtures/synthetic/code-task-v1/row.json"
    output = tmp_path / "episode.json"
    assert (
        main(
            [
                "resolve-synthetic",
                str(row),
                "--episode-id",
                "cli-test",
                "--task-image",
                "registry/task@sha256:" + "a" * 64,
                "--claude-mount-image",
                "registry/claude@sha256:" + "b" * 64,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    raw = json.loads(output.read_text())
    episode = resolved_episode_from_dict(raw)
    assert episode.digest == json.loads(capsys.readouterr().out)["spec_digest"]
    assert episode.harness.config["model"] == "deepseek-flash"
    assert "environment_id" not in output.read_text()
    raw["undeclared_backend_setting"] = "value"
    with pytest.raises(ContractError, match="unknown"):
        resolved_episode_from_dict(raw)


@pytest.mark.parametrize("value", [0, -1, True, "1000"])
def test_invalid_sdk_resource_values_fail_before_creating_a_sandbox(value):
    with pytest.raises(ContractError):
        ResourceSpec(cpu=value)


def test_invalid_claude_runtime_config_does_not_select_hidden_fallback():
    with pytest.raises(ContractError, match="unknown"):
        resolve_claude_code_spec(HarnessSpec("claude-code", "2.1.205", config={"provider": "auto"}))
