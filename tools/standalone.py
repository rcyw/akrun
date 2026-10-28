#!/usr/bin/env python3
"""Task-owned standalone build/start/stop; never adopts the stock akernel-node."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
from pathlib import Path

SOURCE_HEAD = "039e7a963139fb8ad4636e5fb32ddf5e1003c2c2"
DISK_UUID = "1d3c2702-630d-4dd9-a7a3-ba0a8bde959e"
TASK = "akrun-claude-code-v1"
NODE = f"{TASK}-node"
REGISTRY = f"{TASK}-registry"
DATA = Path(f"/srv/akernel-filestore/{TASK}/runsc-rc3/data")
IMAGE = "akrun-local/all-in-one:claude-code-v1-039e7a9-go1.27.3"
PROJECT = Path(__file__).resolve().parents[1]
LOCAL = PROJECT / "artifacts/standalone"


def run(
    args: list[str],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture, env=env, cwd=cwd)
    return result.stdout.strip() if capture else ""


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("launcher source contract changed; review the adapter")
    return text.replace(old, new)


def prepare(source: Path) -> Path:
    if run(["git", "-C", str(source), "rev-parse", "HEAD"]) != SOURCE_HEAD:
        raise ValueError("standalone source differs from the qualified baseline")
    run(
        ["git", "-C", str(source), "diff", "--exit-code", "--", "builder", "deploy", "src/sandboxd"]
    )
    LOCAL.mkdir(parents=True, exist_ok=True)
    for relative in [
        "start.sh",
        "select-resolver.py",
        "validate-afs.py",
        "orbstack-preflight.sh",
        "config/config.json",
        "config/oss.json",
        "config/oss_auths.json",
        "config/registry.json",
        "config/registry_auths.json",
        "config/sandboxd_config.toml",
    ]:
        content = run(["git", "-C", str(source), "show", f"HEAD:deploy/standalone/{relative}"])
        path = LOCAL / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n")
    path = LOCAL / "start.sh"
    text = replace_once(
        path.read_text(), 'NODE_CONTAINER_NAME="akernel-node"', f'NODE_CONTAINER_NAME="{NODE}"'
    )
    text = replace_once(
        text,
        '        --name "${NODE_CONTAINER_NAME}" \\\n',
        '        --name "${NODE_CONTAINER_NAME}" \\\n'
        "        --cpus 8 --memory 16g --label akrun.task=" + TASK + " \\\n",
    )
    text = replace_once(text, "        --net bridge \\\n", f"        --network {TASK} \\\n")
    text = replace_once(
        text,
        "        -e container=oci \\\n",
        "        -p 127.0.0.1:25000:5000 \\\n        -e container=oci \\\n",
    )
    text = text.replace("-e POD_NAME=akernel-node-local", f"-e POD_NAME={NODE}")
    path.write_text(text)
    return path


def build(source: Path) -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    text = (source / "deploy/scripts/build-image.sh").read_text()
    text = replace_once(
        text,
        ('ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"'),
        "ROOT=" + "'" + str(source).replace("'", "'\"'\"'") + "'",
    )
    text = replace_once(
        text,
        "node_build_args=(\n",
        'node_build_args=(\n  --build-arg "SANDBOXD_BUILD_IMAGE=golang:1.27.3-bookworm"\n',
    )
    path = LOCAL / "build-image.sh"
    path.write_text(text)
    environment = {
        **os.environ,
        "AKERNEL_ENABLE_KATA": "false",
        "AKERNEL_ENABLE_FIRECRACKER": "false",
        "AKERNEL_ENABLE_RUNC": "false",
        "AKERNEL_TARGETARCH": "amd64",
    }
    run(
        [
            "bash",
            str(path),
            "--repository",
            "akrun-local/all-in-one",
            "--tag",
            IMAGE.split(":")[-1],
            "--runtime-image",
            "akrun-local/runtime:claude-code-v1-039e7a9",
        ],
        capture=False,
        env=environment,
        cwd=source,
    )


def inspect(name: str) -> dict[str, object]:
    return json.loads(run(["docker", "inspect", name]))[0]


def sdk_environment() -> dict[str, str]:
    return {
        **os.environ,
        "AKERNEL_SERVER_ADDRESS": "127.0.0.1:28443",
        "AKERNEL_GATEWAY_ADDRESS": "http://127.0.0.1:28080",
        "AKERNEL_TOKEN": (DATA / "token").read_text().strip(),
    }


def start(source: Path) -> None:
    if run(["findmnt", "-n", "-o", "UUID", "-T", str(DATA.parent.parent.parent)]) != DISK_UUID:
        raise ValueError("dedicated filestore disk is not mounted")
    for port in (28443, 28080, 25000):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    for name in (NODE, REGISTRY):
        occupied = subprocess.run(["docker", "container", "inspect", name], capture_output=True)
        if occupied.returncode == 0:
            raise ValueError(f"container name occupied: {name}")
    launcher = prepare(source)
    DATA.mkdir(parents=True, mode=0o700, exist_ok=True)
    if run(["findmnt", "-n", "-o", "UUID", "-T", str(DATA)]) != DISK_UUID:
        raise ValueError("data directory is on the wrong filesystem")
    run(["docker", "network", "create", "--label", f"akrun.task={TASK}", TASK])
    environment = {
        **os.environ,
        "IMAGE": IMAGE,
        "AKERNEL_STANDALONE_DATA_DIR": str(DATA),
        "AKERNEL_CONTROL_BIND": "127.0.0.1",
        "AKERNEL_CONTROL_PORT": "28443",
        "AKERNEL_DATA_BIND": "127.0.0.1",
        "AKERNEL_DATA_PORT": "28080",
        "AKERNEL_ENDPOINT_HOST": "127.0.0.1",
        "AKERNEL_CHUNK_DB_SIZE": "8GiB",
        "AKERNEL_ENV": TASK,
        "AKERNEL_CLUSTER_NAME": TASK,
        "AKERNEL_ENABLE_RUNC": "false",
        "AKERNEL_ENABLE_FIRECRACKER": "false",
    }
    run(["bash", str(launcher)], capture=False, env=environment)
    node = inspect(NODE)
    state = {
        "task_id": TASK,
        "source_head": SOURCE_HEAD,
        "node_id": node["Id"],
        "image_id": node["Image"],
        "data": str(DATA),
        "image": IMAGE,
    }
    (LOCAL / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    registry_id = run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REGISTRY,
            "--network",
            f"container:{NODE}",
            "--label",
            f"akrun.task={TASK}",
            "-v",
            f"{DATA.parent}/registry:/var/lib/registry",
            "registry:2",
        ]
    )
    state["registry_id"] = registry_id
    (LOCAL / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    status()


def status() -> None:
    state = json.loads((LOCAL / "state.json").read_text())
    node = inspect(NODE)
    if node["Id"] != state["node_id"] or node["Image"] != state["image_id"]:
        raise ValueError("owned container identity changed")
    if not any(
        m["Destination"] == "/home/akernel" and m["Source"] == str(DATA) for m in node["Mounts"]
    ):
        raise ValueError("owned data mount changed")
    print(json.dumps(state, indent=2))
    run(
        [
            "docker",
            "exec",
            NODE,
            "findmnt",
            "-T",
            "/home/akernel/filestore",
            "-o",
            "TARGET,SOURCE,FSTYPE",
        ],
        capture=False,
    )


def load_images() -> None:
    refs: dict[str, str] = {}
    for role, image in [
        ("task", "akrun-local/synthetic-code-task:1"),
        ("claude", "akrun-claude-code-rootfs:2.1.205-amd64"),
    ]:
        target = f"127.0.0.1:25000/akrun/{role}:v1"
        run(["docker", "tag", image, target])
        output = run(["docker", "push", target])
        digests = re.findall(r"digest: (sha256:[0-9a-f]{64})", output)
        if not digests:
            raise ValueError("registry did not return an immutable digest")
        refs[role] = f"127.0.0.1:5000/akrun/{role}@{digests[-1]}"
    (LOCAL / "images.json").write_text(json.dumps(refs, indent=2) + "\n")
    print(json.dumps(refs, indent=2))


def stop() -> None:
    state = json.loads((LOCAL / "state.json").read_text())
    status()
    inventory = run([str(PROJECT / ".venv/bin/ak"), "list", "--quiet"], env=sdk_environment())
    if inventory:
        raise ValueError("owned node still has Sandbox allocations")
    if "registry_id" in state:
        if inspect(REGISTRY)["Id"] != state["registry_id"]:
            raise ValueError("registry identity changed")
        run(["docker", "stop", REGISTRY])
        run(["docker", "rm", REGISTRY])
    run(["docker", "stop", "--time", "30", NODE])
    run(["docker", "rm", NODE])
    run(["docker", "network", "rm", TASK])
    state["status"] = "released; data and image evidence retained"
    (LOCAL / "state.json").write_text(json.dumps(state, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["build", "prepare", "start", "status", "load-images", "stop"]
    )
    parser.add_argument("--akernel-checkout", type=Path)
    args = parser.parse_args()
    if args.command in {"build", "prepare", "start"}:
        if args.akernel_checkout is None:
            parser.error("--akernel-checkout is required")
        source = args.akernel_checkout.resolve()
        if args.command == "build":
            prepare(source)
            build(source)
        elif args.command == "prepare":
            print(prepare(source))
        else:
            start(source)
    else:
        {"status": status, "load-images": load_images, "stop": stop}[args.command]()


if __name__ == "__main__":
    main()
