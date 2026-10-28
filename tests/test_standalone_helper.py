from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "standalone_helper", Path(__file__).parents[1] / "tools/standalone.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def test_launcher_adapter_preserves_isolation_and_container_identity(tmp_path, monkeypatch):
    template = (
        'NODE_CONTAINER_NAME="akernel-node"\n'
        '        --name "${NODE_CONTAINER_NAME}" \\\n'
        "        --net bridge \\\n"
        "        -e container=oci \\\n"
        "-e POD_NAME=akernel-node-local\n"
    )

    def source(args, **kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return helper.SOURCE_HEAD
        if "show" in args:
            return template if args[-1].endswith("/start.sh") else "{}"
        return ""

    monkeypatch.setattr(helper, "run", source)
    monkeypatch.setattr(helper, "LOCAL", tmp_path / "prepared")
    launcher = helper.prepare(tmp_path)
    text = launcher.read_text()
    assert 'NODE_CONTAINER_NAME="akrun-claude-code-v1-node"' in text
    assert "--network akrun-claude-code-v1" in text
    assert "--cpus 8 --memory 16g" in text
    assert "-p 127.0.0.1:25000:5000" in text
    assert "-e container=oci" in text
    assert "POD_NAME=akernel-node-local" not in text


def test_changed_launcher_contract_is_rejected_before_execution():
    with pytest.raises(ValueError, match="source contract changed"):
        helper.replace_once("changed source", "expected", "replacement")
    with pytest.raises(ValueError):
        helper.replace_once("expected expected", "expected", "replacement")


def test_wrong_source_is_rejected_without_creating_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "run", lambda *args, **kwargs: "different-commit")
    monkeypatch.setattr(helper, "LOCAL", tmp_path / "never-created")
    with pytest.raises(ValueError, match="qualified baseline"):
        helper.prepare(tmp_path)
    assert not helper.LOCAL.exists()
