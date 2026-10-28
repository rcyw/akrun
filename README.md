# akrun

An MIT Python agent runner using the published **AKernel SDK 0.2.0rc3**:

```text
model-free qualification → Claude Code 2.1.205 → immutable candidate + trajectory
                        → fresh verification Sandbox → result → cleanup
```

The caller owns orchestration, durable JSON records and the model credential. AKernel owns isolated execution. Inference and verification have separate writable filesystems. The repository-owned `add_one` task also supports deterministic gold, known-bad and empty candidates without a model.

## Install and check

Use Python 3.11+, uv, Docker and a compatible AKernel deployment. The standalone helper targets Linux/amd64 and selects Go 1.27.3 for sandboxd; the runner is Python.

```bash
uv sync --locked
uv run ruff check .
uv run pyright
uv run pytest
uv build
```

The lock uses the published SDK's actual `adx-environment==0.1.0rc2` dependency, with no source override. Live results are recorded separately in [the acceptance record](docs/validation/2026-10-28-claude-code-v1.md).

## Local standalone and images

The operator helper consumes AKernel `039e7a963139fb8ad4636e5fb32ddf5e1003c2c2` and its clean fixed sandboxd submodule. Review its host constants on another machine. Its data profile requires the documented dedicated disk UUID. It uses an independent node/network, loopback ports 28443/28080/25000, 8 CPU/16 GiB, and `/srv/akernel-filestore/akrun-claude-code-v1/runsc-rc3/data`. Occupied names/ports are rejected.

```bash
uv run python tools/standalone.py build --akernel-checkout /path/to/AKernel
docker buildx build --platform linux/amd64 --load \
  -t akrun-local/synthetic-code-task:1 fixtures/synthetic/code-task-v1
./docker/claude-code-rootfs/verify-source.sh
./docker/claude-code-rootfs/build-local.sh amd64
./docker/claude-code-rootfs/verify-image.sh amd64
uv run python tools/standalone.py start --akernel-checkout /path/to/AKernel
uv run python tools/standalone.py load-images
```

The task-owned registry shares the node's network namespace; its blobs live on the dedicated disk. The node consumes digest references from `artifacts/standalone/images.json` through `127.0.0.1:5000`. Images need no external publication.

Configure the SDK without printing its token:

```bash
akrun_data=/srv/akernel-filestore/akrun-claude-code-v1/runsc-rc3/data
source "$akrun_data/sdk.env"
read -r AKERNEL_TOKEN < "$akrun_data/token"
export AKERNEL_TOKEN
```

For another deployment, use its `AKERNEL_SERVER_ADDRESS`, optional `AKERNEL_GATEWAY_ADDRESS`, and protected token. The SDK's TLS settings apply unchanged.

## Resolve, qualify and run

Keep `DEEPSEEK_API_KEY` in the caller environment. The default upstream is `https://api.deepseek.com/anthropic`; the primary model and all Claude aliases resolve explicitly to `deepseek-flash`. Use `--model` to select another explicit model. A short-lived loopback proxy injects credentials only into upstream requests; credentials never enter Sandbox configuration, argv or bundles. Claude uses the SDK reverse tunnel's returned HTTP URL.

Replace the digest placeholders with the references returned by `load-images`:

```bash
uv run akrun resolve-synthetic fixtures/synthetic/code-task-v1/row.json \
  --episode-id add-one-claude \
  --task-image '127.0.0.1:5000/akrun/task@sha256:YOUR_TASK_DIGEST' \
  --claude-mount-image '127.0.0.1:5000/akrun/claude@sha256:YOUR_CLAUDE_DIGEST' \
  --timeout 480 --max-turns 20 --output artifacts/add-one-claude.json
uv run akrun qualify artifacts/add-one-claude.json
uv run akrun run artifacts/add-one-claude.json
uv run akrun status add-one-claude
uv run akrun inspect add-one-claude
```

Qualification calls no model. It checks both actual task images, clean Git base, Python capabilities, blocked external networking and the read-only Claude runtime on inference. `run` performs or reuses matching qualification. Proxy health and model-protocol preflight precede agent dispatch. The verifier applies only the CandidateBundle patch to a fresh clean task image and runs its tests. Default stage resources are 2000 milli-CPU, 4096 MiB memory and 4096 MiB writable storage.

For deterministic controls, use `--harness static-candidate --candidate-file gold.patch`, `known-bad.patch`, or `empty.patch`, omitting the Claude mount. Every intentional new inference uses a new episode ID.

## Recovery and cleanup

```bash
uv run akrun resume add-one-claude
uv run akrun wait add-one-claude --timeout 120
uv run akrun cancel add-one-claude
uv run akrun cleanup add-one-claude
```

Stage identity and Sandbox name are saved before creation; dispatch intent is saved before command submission. The remote supervisor claims a stage once and atomically records terminal outputs. Recovery observes the original named Sandbox, command snapshots and stage journal. Missing/mismatched execution or uncertain dispatch returns `recovery_required` and never submits another inference.

The SDK has no public command reattachment API. A completed original stage can be collected and continued through fresh verification. If the caller process and model proxy disappear during inference, transparent continuation of an in-flight model request is not guaranteed; inspect/wait/cancel the original execution. A wait timeout leaves it running. No checkpoint/restore capability is required.

Cancellation is durable before deletion and wins publication races. Failed deletion stays retryable; `cleanup` handles completed, failed or cancelling episodes, while active execution uses `cancel`. A published verifier verdict survives cleanup failures. Deletion is confirmed using the SDK's public inventory CLI. `status` is local; `inspect` also queries live facts.

State defaults to ignored `.akrun/`; a global `--state-dir PATH` selects another directory. Candidate and trajectory bundles are content-addressed, size/hash checked and atomically published with execution provenance. Exit codes are 0 for passed/control success, 1 for a failed verdict, 2 for operation/contract failure, 3 for required recovery and 130 for keyboard cancellation.

Drain episodes before stopping the task-owned node:

```bash
uv run python tools/standalone.py status
uv run python tools/standalone.py stop
```

Stop verifies recorded container identities, data mounts and empty SDK inventory, then removes this task's node, registry and network. Data, images and evidence remain available. Shared resources are preserved.

## Boundaries and provenance

The v1 catalog supports Claude Code/static-candidate, the synthetic Git task and its verifier. Kova, other agents, large benchmark suites and checkpoint/restore are outside this release. Egress defaults to blocked, with model traffic through the reverse tunnel.

See [architecture](docs/architecture.md), [artifact contracts](docs/contracts.md), [source provenance](SOURCE.md) and [inherited Apache-2.0 notices](THIRD_PARTY_LICENSES/Apache-2.0.txt). Runtime/dependency licenses remain applicable to their components.
