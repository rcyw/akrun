# Claude Code v1: standalone acceptance

Task: `akrun-claude-code-v1`. Executed 2026-10-28, Asia/Shanghai.

## Result

The complete real flow passed: Claude Code inference through a caller-owned DeepSeek proxy and SDK HttpReverseTunnel, a real `calculator.py` edit, immutable CandidateBundle and TrajectoryBundle publication, tests in a fresh Sandbox, result publication and resource release. All four actual model episodes passed within the authorized four-episode/ten-minute-per-episode allowance. No further model episode was started.

The final production implementation revision is `28755e6f294a1b7538ca2e65edb32b587d5a75c4`; later delivery changes only document the evidence. The separate source-import commit is `ffa186c42f2ee49e0baff938099cd1bebb40a713`, based on cofy-x/axrun `c3a2dcf82abfe00c9fddd65ff79a87dbbfafa77b` with the maintainer’s MIT authorization.

## Fixed inputs

| Input | Actual version |
| --- | --- |
| SDK | Published `akernel-sdk==0.2.0rc3` |
| SDK wheel SHA-256 | `4080af7bb6c6acfbf0b4532bd73564ae0c9e2c6a0d3d17dcffdea2d1921bb180` |
| SDK dependency | Published `adx-environment==0.1.0rc2`, with no source override |
| AKernel source | `039e7a963139fb8ad4636e5fb32ddf5e1003c2c2` |
| sandboxd gitlink | `f800327459a104c04a0689940062fc9bb9e2bb9e` |
| Compiled sandboxd | Go `1.27.3`; binary SHA-256 `abd0ecb4f3adcde5fbc5f770e78e573d75c0f73390762caeacda7fb001ef2f9f` |
| ADX packaged source | `555c7478b38b59246005380355a50cbeca5db475`, from `/opt/adx/current/manifest.json` |
| ADX platform archive | SHA-256 `a0d93b3758797e3894f566c31fec73daccd678676ab4c82d8b78cce36987eca9` |
| Execd archive | SHA-256 `c895a1d2f33336d504e44ae062bb1ef39fb6bedccd8229061611df42fab90310` |
| Final tested node image | `sha256:23a9a2900312b83b8d93eed1a6c8c01154602c4502bb09391d6c48434c632b38` |
| First tested node image | `sha256:8034d89fa543a4c8c6cb28d7e15938d13d9405fce4784335958b5c93a29e42bc` |
| gVisor | Manifest release `release-20260817.0-akernel.5`; binary reports `release-20260817.0-akernel.4-3-g87e8441b6db1` |
| Task image | `127.0.0.1:5000/akrun/task@sha256:245f0ac72e0b40f68dfe7e1720ba4cd6202ae0d0e4f0fba1b6b0574caf4947e8` |
| Claude runtime | `127.0.0.1:5000/akrun/claude@sha256:31d67cb8135313309378e57b213db71e219a74891d9fc34f242bda819cd225cb` |
| Claude / Node | `2.1.205` / `22.23.2`; retained supply-chain lock and mount-rooted interpreter |
| Model | `deepseek-flash`, explicitly selected for every Claude alias |
| Platform | Linux/amd64, runsc; host Python 3.12.3 |

The service build consumes its pinned ADX/Execd archives, rather than the unrelated agent-dx source checkout. The published SDK’s rc2 dependency was verified against this actual server combination. This does not establish compatibility with every ADX release.

## Checks and real scenarios

- Ruff and formatting checks, strict Pyright, whitespace checks, and 72 offline tests passed. Regressions cover lost dispatch responses, missing execution identities, repeated recovery, publication/cancellation races, late creation after cancellation, late qualification binding, keyboard interruption, cleanup retry, corrupt/oversized artifacts, SSE streaming, alias resolution and safe proxy summaries.
- Both task and read-only Claude images passed model-free qualification in actual SDK Sandboxes. Independent Claude image verification checked Claude/Node versions, ELF/interpreter identity and read-only behavior.
- Real static gold passed; known-bad and empty candidates produced normal failed verifier results. Fresh verification Sandbox IDs differed from inference IDs, and SDK inventory was empty after each control.
- Real model-free cancellation passed repeatedly, including after cleanup hardening. Cancellation metadata remained explainable and all task allocations were released.
- A clean wheel installation reported SDK 0.2.0rc3/ADX client 0.1.0rc2, ran its CLI outside the source checkout with PYTHONPATH removed, and completed the full static-gold SDK pipeline without a model credential.

| Model episode | Source | Seconds | Proxy requests¹ | Canonical events | Result |
| --- | --- | ---: | ---: | ---: | --- |
| `claude-model-v1` | `a366f503c65f` | 13.328 | 6 | 25 | passed; resources released |
| `claude-recovery-v1` | `a366f503c65f` | 13.689 | 7 | 30 | passed; resources released |
| `claude-hardened-recovery-v1` | `28755e6f294a` | 12.529 | 6 | 25 | passed; resources released |
| `claude-rebuilt-v1` | `28755e6f294a` | 10.761 | 6 | 25 | passed; resources released |

¹ Counts include the protocol preflight. All observed upstream statuses were HTTP 200. Each candidate changed only `calculator.py` from `return value` to `return value + 1`; the fresh verifier ran the two repository tests successfully.

The two recovery episodes closed a real detached SDK client after remote inference had finished and before local candidate publication. A separate CLI process resumed without DEEPSEEK_API_KEY, retained the original stage and Sandbox IDs, collected the candidate and launched only the fresh verifier. Remote `launch_count` remained 1. This validates recovery at that explicit boundary; it does not claim transparent continuation of an in-flight model request after the original proxy disappears.

## Isolation, disk and final resource state

The node used an independent name/network, 8 CPU and 16 GiB memory. Each stage requested 2000 milli-CPU, 4096 MiB memory and 4096 MiB writable storage; ChunkDB was 8 GiB and filestore was the bounded 50 GiB template. Control/data/registry listened only on 127.0.0.1 ports 28443/28080/25000.

Data was `/srv/akernel-filestore/akrun-claude-code-v1/runsc-rc3/data`, on UUID `1d3c2702-630d-4dd9-a7a3-ba0a8bde959e`. The host backing file and container `/home/akernel/ext4.img` both had device/inode `66310:47`. The active filestore was `/dev/loop0`, backed by that exact file. Docker images/build layers used the existing runtime disk at `/var/lib/docker`, UUID `619f510c-1191-4af5-ac55-b55a16da65bd`.

Before shutdown, SDK inventory and native `sbox list` were both empty. The first node was stopped and replaced by the successfully rebuilt pinned-source image; its sandboxd binary hash matched the first build. The rebuilt node then passed another real Claude episode and the installed-wheel control. Both task-owned node generations and registry containers were removed through the ownership-checked helper. Final node `41e261ee64dafa323076247cc2334deae548ec5f81bff45551d1e7c021f3460f` and registry `3b2579428e29f92275a52a59c77ea5ff49f95edba1bb31113bccca75108b9853` are removed. The task network, three listeners and filestore loop were independently confirmed released.

The task’s data, image inputs, task-owned detached AKernel clone and local evidence are retained for reproducibility. No other task checkout, container, default branch or cache was cleaned or replaced. Model credential value scans found no copy in local evidence; credentials and raw artifacts are excluded from Git.

## Reproduction

Run these from the akrun checkout, using the matching AKernel source and the protected caller environment described in the README:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv build
uv run python tools/standalone.py build --akernel-checkout /path/to/pinned/AKernel
docker buildx build --platform linux/amd64 --load -t akrun-local/synthetic-code-task:1 fixtures/synthetic/code-task-v1
./docker/claude-code-rootfs/verify-source.sh
./docker/claude-code-rootfs/build-local.sh amd64
./docker/claude-code-rootfs/verify-image.sh amd64
uv run python tools/standalone.py start --akernel-checkout /path/to/pinned/AKernel
uv run python tools/standalone.py load-images
uv run python tools/acceptance.py static
uv run python tools/acceptance.py model
uv run python tools/acceptance.py recovery
uv run python tools/acceptance.py cancel
uv run python tools/standalone.py stop
```

The acceptance helper reserves a model-attempt entry before model work and refuses duplicate episode attempts or a fifth model episode. Existing evidence must be inspected rather than overwritten to repeat an ambiguous inference. Ordinary CLI runs use a new episode ID for intentional new inference.

## Failures found and resolved

- Initial development-tool downloads timed out. Retrying the same lock with `UV_HTTP_TIMEOUT=300` completed installation.
- Cropping the imported catalog exposed stale exports and one orphan test decorator; offline checks caught both before model execution.
- Explicit resolver `--model` collided with parent argparse abbreviation. It was fixed and covered by the CLI contract regression before any actual model attempt.
- The shared AKernel checkout advanced during development. The source guard refused it; this task created its own detached 039e7a9 clone without modifying the other checkout.
- The first repeat image build from that clone hit Docker Hub anonymous-token TLS timeout. A later repeat succeeded. Actual tested image IDs and the matching compiled sandboxd hash are recorded above.
- Review found late-create cancellation and keyboard-interruption cleanup gaps. The fixes passed offline regressions, live cancellation, fresh static controls and additional actual Claude acceptance.

Raw evidence and receipts are under `akrun/artifacts/` (local-only, ignored, not a backup), especially `acceptance/summary.json`, `acceptance/model-attempts.json`, the stage/bundle/result records, model request summaries, `final-node-proof.json`, `runtime-storage-proof.json` and `resource-final.json`. Key versions, limits, results and recovery boundaries are preserved here so the conclusion does not depend on those local files.
