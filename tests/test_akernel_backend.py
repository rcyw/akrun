from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from akrun.akernel_backend import AkernelBackend
from akrun.errors import ContractError, RecoveryRequiredError
from akrun.models import OutputSpec, SandboxBinding, StagePlan


class Files:
    def __init__(self):
        self.data = {}

    def make_dir(self, path):
        return True

    def write(self, path, value):
        self.data[path] = value.encode() if isinstance(value, str) else value

    def read(self, path):
        return self.data[path].decode()

    def exists(self, path):
        return path in self.data

    def copy_from_local(self, local, remote):
        self.data[remote] = Path(local).read_bytes()

    def copy_to_local(self, remote, local):
        Path(local).write_bytes(self.data[remote])

    def get_info(self, path):
        return SimpleNamespace(size=len(self.data[path]))


class SDK:
    def __init__(self, plan):
        self.plan = plan
        self.sandboxes = {}
        self.start_calls = 0
        self.lose_response = False
        self.snapshots = []
        self.live_only = False

    def create(self, **kwargs):
        name = kwargs["name"]
        if name in self.sandboxes:
            return self.sandboxes[name]
        files = Files()
        sandbox = SimpleNamespace(
            id=f"sdk-{name}",
            files=files,
            kill=lambda: None,
            reverse_tunnel=kwargs.get("reverse_tunnel"),
        )

        def start(command, **options):
            self.start_calls += 1
            assert self.snapshots[-1].launch_intent
            identity = json.loads(files.read("/run/akrun/identity.json"))
            if not self.live_only:
                value = b"candidate bytes"
                files.write("/outputs/candidate.patch", value)
                files.write(
                    "/run/akrun/result.json",
                    json.dumps(
                        {
                            **identity,
                            "exit_code": 0,
                            "outputs": [
                                {
                                    "path": "/outputs/candidate.patch",
                                    "size_bytes": len(value),
                                    "sha256": hashlib.sha256(value).hexdigest(),
                                    "media_type": "text/x-diff",
                                }
                            ],
                        }
                    ),
                )
            if self.lose_response:
                raise ConnectionError("response was lost")
            return SimpleNamespace(pid=999)

        sandbox.commands = SimpleNamespace(
            run=start,
            list=lambda: [
                SimpleNamespace(
                    pid=999, command="stage " + self.snapshots[-1].stage_id, running=self.live_only
                )
            ],
        )
        self.sandboxes[name] = sandbox
        return sandbox

    def inventory(self):
        return {sandbox.id for sandbox in self.sandboxes.values()}

    def delete(self, name):
        self.sandboxes.pop(name, None)


def setup():
    plan = StagePlan(
        SandboxBinding("test/image@sha256:" + "a" * 64, "linux/amd64", "/workspace"),
        ("true",),
        "/workspace",
        (OutputSpec("/outputs/candidate.patch", media_type="text/x-diff"),),
    )
    sdk = SDK(plan)
    backend = AkernelBackend(
        sandbox_factory=sdk.create,
        delete_sandbox=sdk.delete,
        inventory=sdk.inventory,
        poll_interval=0,
    )
    return plan, sdk, backend


def test_lost_start_response_uses_original_sdk_execution(tmp_path):
    plan, sdk, backend = setup()
    sdk.lose_response = True
    with pytest.raises(RecoveryRequiredError, match="dispatch_outcome_unknown"):
        backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    original = sdk.snapshots[-1]
    assert original.launch_intent and original.command_pid is None
    result = backend.recover(original, plan, artifact_dir=tmp_path)
    assert result.execution.stage_id == original.stage_id
    assert sdk.start_calls == 1
    assert sdk.snapshots[0].sandbox_id == ""  # identity before any remote mutation


def test_live_recovery_only_observes_and_wait_timeout_does_not_cancel(tmp_path):
    plan, sdk, backend = setup()
    sdk.live_only, sdk.lose_response = True, True
    with pytest.raises(RecoveryRequiredError):
        backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    assert backend.recover(sdk.snapshots[-1], plan, artifact_dir=tmp_path) is None
    assert sdk.start_calls == 1 and len(sdk.sandboxes) == 1


@pytest.mark.parametrize(
    "field,value", [("sha256", "0" * 64), ("size_bytes", 100_000_000), ("plan_digest", "bad")]
)
def test_corrupt_manifest_cannot_be_published(tmp_path, field, value):
    plan, sdk, backend = setup()
    sdk.lose_response = True
    with pytest.raises(RecoveryRequiredError):
        backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    sandbox = next(iter(sdk.sandboxes.values()))
    raw = json.loads(sandbox.files.read("/run/akrun/result.json"))
    (raw if field == "plan_digest" else raw["outputs"][0])[field] = value
    sandbox.files.write("/run/akrun/result.json", json.dumps(raw))
    with pytest.raises(ContractError):
        backend.recover(sdk.snapshots[-1], plan, artifact_dir=tmp_path)
    assert not list(tmp_path.glob("[0-9]*"))


def test_missing_sandbox_is_not_recreated(tmp_path):
    plan, sdk, backend = setup()
    backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    sdk.delete(sdk.snapshots[-1].sandbox_name)
    with pytest.raises(RecoveryRequiredError, match="unavailable"):
        backend.recover(sdk.snapshots[-1], plan, artifact_dir=tmp_path)
    assert not sdk.sandboxes


def test_cancel_is_idempotent_and_confirms_sdk_inventory(tmp_path):
    plan, sdk, backend = setup()
    backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    backend.cancel(sdk.snapshots[-1])
    backend.cancel(sdk.snapshots[-1])
    assert not sdk.inventory()


def test_rejected_identity_publication_does_not_orphan_sandbox(tmp_path):
    plan, sdk, backend = setup()

    def reject_bound(execution):
        if execution.sandbox_id:
            raise RecoveryRequiredError("cancel arrived during create")

    with pytest.raises(RecoveryRequiredError):
        backend.execute(plan, artifact_dir=tmp_path, on_bound=reject_bound)
    assert not sdk.inventory()
    assert sdk.start_calls == 0


def test_cancel_refuses_another_stage_identity(tmp_path):
    plan, sdk, backend = setup()
    backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    sandbox = next(iter(sdk.sandboxes.values()))
    identity = json.loads(sandbox.files.read("/run/akrun/identity.json"))
    identity["stage_id"] = "another-task"
    sandbox.files.write("/run/akrun/identity.json", json.dumps(identity))
    with pytest.raises(RecoveryRequiredError, match="identity_changed"):
        backend.cancel(sdk.snapshots[-1])
    assert len(sdk.inventory()) == 1


def test_terminal_failure_retains_bounded_diagnostic_logs(tmp_path):
    plan, sdk, backend = setup()
    sdk.lose_response = True
    with pytest.raises(RecoveryRequiredError):
        backend.execute(plan, artifact_dir=tmp_path, on_bound=sdk.snapshots.append)
    sandbox = next(iter(sdk.sandboxes.values()))
    result = json.loads(sandbox.files.read("/run/akrun/result.json"))
    result["exit_code"] = 7
    sandbox.files.write("/run/akrun/result.json", json.dumps(result))
    sandbox.files.write("/run/akrun/stderr.log", b"command failed")
    recovered = backend.recover(sdk.snapshots[-1], plan, artifact_dir=tmp_path)
    assert recovered.exit_code == 7
    assert Path(recovered.stderr_path).read_bytes() == b"command failed"
