# Claude Code v1 implementation and standalone acceptance

Task: `akrun-claude-code-v1`. Started 2026-10-28 (Asia/Shanghai).

## Inputs and boundary

The separate source-import commit is `ffa186c42f2ee49e0baff938099cd1bebb40a713`, from cofy-x/axrun `c3a2dcf82abfe00c9fddd65ff79a87dbbfafa77b`. Implementation uses the published `akernel-sdk==0.2.0rc3`, whose declared ADX dependency is `adx-environment==0.1.0rc2`; the lock is resolved from the package index, with no source replacement.

Standalone source is AKernel `039e7a963139fb8ad4636e5fb32ddf5e1003c2c2` and its clean fixed sandboxd submodule. The node build selects Go 1.27.3 and runsc only. ADX platform and Execd archives retain their Dockerfile URLs and SHA-256 pins. The existing other-task `akernel-node` is protected; this task uses its own name, network, endpoints and data profile.

The dedicated disk UUID is `1d3c2702-630d-4dd9-a7a3-ba0a8bde959e`. Data is `/srv/akernel-filestore/akrun-claude-code-v1/runsc-rc3/data`; control/data/registry bind loopback ports 28443/28080/25000. Node budget is 8 CPU/16 GiB; Sandbox defaults are 2000 mCPU, 4096 MiB memory and 4096 MiB explicit writable storage. ChunkDB is 8 GiB; filestore is the bounded 50 GiB template.

Claude Code remains 2.1.205, Node 22.23.2, and the rootfs supply-chain lock is retained. DeepSeek uses its Anthropic-compatible endpoint and explicit `deepseek-flash` aliases. Model credentials remain caller-only. The model acceptance allowance is at most four actual inference episodes, each bounded to ten minutes, including repair attempts.

## Progress and evidence

- The task image was built locally.
- The Claude runtime source and independent read-only image checks passed, including Claude/Node versions and mount-rooted interpreter.
- Node image construction, offline gates and all live scenarios are in progress. No model inference has been executed yet.
- Initial development-tool downloads timed out; the same locked dependencies are being retried with a larger HTTP timeout.

Raw evidence is under `akrun/artifacts/` and the workspace task index; these are local-only, ignored, and not a backup. No offline or image-build result is an end-to-end acceptance claim. Final commands, image digests, results, limitations and resource state will be appended after execution.

## 17:25 +08:00 — offline checks and fixed source

The first offline gate passed: 63 tests, strict Pyright with zero errors, Ruff, and diff whitespace checks. Claude rootfs independent image verification passed. The original full node image build succeeded with source revision 039e7a9 and Go 1.27.3.

Another task advanced the shared AKernel checkout to e0a5a5e94fe479982fde9db6e6f0c18250af8253. This task prepared its own detached clone at 039e7a9 and sandboxd f800327459a104c04a0689940062fc9bb9e2bb9e. The source guard correctly refused launching from the moved checkout before creating any resources. No other-task checkout was changed.

A repeat build from the fixed clone encountered Docker Hub anonymous-token TLS handshake timeout while resolving the already-built Go image. The original successful node image remains available. Its revision label is 039e7a9, and a targeted Git diff confirms the moved source's builder, deploy, fixed sandboxd, Makefile and Docker context rules are identical to the pinned clone. The repeat network failure is retained separately; runtime acceptance uses the originally built exact image, not a claim that the repeat build passed.
