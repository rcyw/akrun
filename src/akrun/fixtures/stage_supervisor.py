"""Self-contained, at-most-once stage supervisor uploaded through the SDK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def stop_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)
    # Descendants may survive their original leader; always retire the group.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=3)


def execute(spec_path: Path, stage_id: str) -> int:
    spec = json.loads(spec_path.read_text())
    if spec["stage_id"] != stage_id:
        raise ValueError("stage identity mismatch")
    root = spec_path.parent
    try:
        (root / "claimed").mkdir()
    except FileExistsError:
        return 126
    journal = root / "result.json"
    identity = {key: spec[key] for key in ("stage_id", "sandbox_id", "plan_digest")}
    atomic_json(root / "started.json", {**identity, "pid": os.getpid(), "launch_count": 1})
    process: subprocess.Popen[bytes] | None = None

    def interrupted(_signum: int, _frame: object) -> None:
        raise InterruptedError("stage cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    exit_code, diagnostic = 125, "stage_supervisor_failed"
    outputs: list[dict[str, Any]] = []
    started = time.time()
    try:
        with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                spec["argv"],
                cwd=spec["cwd"],
                env={**os.environ, **spec["env"]},
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=spec["timeout_seconds"])
                diagnostic = "" if exit_code == 0 else "stage_command_failed"
            except subprocess.TimeoutExpired:
                exit_code, diagnostic = 124, "stage_timeout"
            finally:
                stop_group(process)
        for output in spec["outputs"]:
            path = Path(output["path"])
            if not path.exists() and exit_code != 0:
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > output["max_bytes"]:
                    raise ValueError("invalid declared output")
                digest = hashlib.sha256()
                size = 0
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > output["max_bytes"]:
                        raise ValueError("output exceeds limit")
                    digest.update(chunk)
            outputs.append(
                {
                    "path": str(path),
                    "sha256": digest.hexdigest(),
                    "size_bytes": size,
                    "media_type": output["media_type"],
                }
            )
    except InterruptedError:
        if process is not None:
            stop_group(process)
        exit_code, diagnostic = 130, "stage_cancelled"
    except Exception:
        exit_code, diagnostic = 125, "stage_output_invalid"
    atomic_json(
        journal,
        {
            **identity,
            "exit_code": exit_code,
            "diagnostic_code": diagnostic,
            "outputs": outputs,
            "started_at": started,
            "completed_at": time.time(),
        },
    )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    args = parser.parse_args()
    return execute(args.spec, args.stage_id)


if __name__ == "__main__":
    raise SystemExit(main())
