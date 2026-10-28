from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SUPERVISOR = Path(__file__).parents[1] / "src/akrun/fixtures/stage_supervisor.py"


def spec(tmp_path, script, *, timeout=5, limit=100):
    output = tmp_path / "out"
    payload = {
        "stage_id": "one-stage",
        "sandbox_id": "sdk-one",
        "plan_digest": "digest",
        "argv": [sys.executable, "-c", script],
        "cwd": str(tmp_path),
        "env": {},
        "timeout_seconds": timeout,
        "outputs": [{"path": str(output), "media_type": "text/plain", "max_bytes": limit}],
    }
    path = tmp_path / "stage.json"
    path.write_text(json.dumps(payload))
    return path, output


def launch(path):
    return subprocess.run(
        [sys.executable, str(SUPERVISOR), "--spec", str(path), "--stage-id", "one-stage"],
        capture_output=True,
        timeout=15,
    )


def test_remote_claim_prevents_duplicate_inference(tmp_path):
    path, output = spec(tmp_path, "from pathlib import Path; p=Path('out'); p.write_text('one')")
    assert launch(path).returncode == 0
    output.write_text("unchanged")
    assert launch(path).returncode == 126
    assert output.read_text() == "unchanged"
    assert json.loads((tmp_path / "started.json").read_text())["launch_count"] == 1


def test_timeout_terminates_child_group_and_records_terminal_reason(tmp_path):
    path, _ = spec(
        tmp_path,
        (
            "import os,time; from pathlib import Path; "
            "Path('pid').write_text(str(os.getpid())); time.sleep(60)"
        ),
        timeout=0.1,
    )
    assert launch(path).returncode == 124
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["diagnostic_code"] == "stage_timeout"
    pid = int((tmp_path / "pid").read_text())
    assert not Path(f"/proc/{pid}").exists()


def test_supervisor_rejects_oversized_and_symlink_outputs(tmp_path):
    path, _ = spec(
        tmp_path, "from pathlib import Path; Path('out').write_text('too long')", limit=2
    )
    assert launch(path).returncode == 125
    assert (
        json.loads((tmp_path / "result.json").read_text())["diagnostic_code"]
        == "stage_output_invalid"
    )
