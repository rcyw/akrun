# Akrun development

Akrun is an MIT Python episode runner built exclusively on the published AKernel SDK. The supported flow is Claude Code or a static candidate, immutable CandidateBundle and TrajectoryBundle, and a fresh verification Sandbox. Inference and verification never share a writable filesystem.

Use public `akernel_sdk` APIs for execution, files, mounts, networking and cleanup. Do not import SDK private modules or call backend services directly. Persist a stage identity and dispatch intent before remote mutations; an ambiguous inference is inspected or cancelled, never silently submitted again. Model credentials stay in caller memory and reach the model only through the caller-owned loopback proxy and SDK HttpReverseTunnel. Tunnel sessions are ephemeral.

Use a project-local uv environment and the locked published SDK. Run `uv run ruff check .`, `uv run pyright`, `uv run pytest`, `uv build` and `git diff --check` before completion. Keep live tests opt-in and report them separately from offline checks. Preserve user changes. Do not commit credentials, episode state, generated artifacts or raw logs. Use conventional commits with a prose body and one Signed-off-by trailer.
