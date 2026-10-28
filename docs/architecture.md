# Execution and recovery

Resolved episodes bind task/runtime image digests, platform, working directory, adapter identities, input hashes and SDK resource quantities. The closed catalog produces stage plans. EpisodeRunner owns phase transitions and publication; AkernelBackend translates plans to public SDK operations. Adapters do not start Sandboxes.

A stage ID and Sandbox name are durable before creation. The returned Sandbox ID is saved before input transfer, and dispatch intent precedes supervisor launch. No code imports private SDK modules or calls ADX services. The SDK's public `ak list --quiet` supplies inventory before named recovery and after deletion.

The uploaded supervisor uses an exclusive stage claim, a bounded process group and an atomic terminal manifest. Output collection checks stage/Sandbox identity, plan digest, file bounds and hashes. Content-addressed CandidateBundle and TrajectoryBundle directories are atomically published. A fresh verifier receives only validated candidate data and runner-owned verifier code.

Record locks cover transitions rather than remote command lifetimes. A cancellation intent prevents later binding or publication. Uncertain dispatch/observation retains the original identity. Resume collects that original terminal stage or continues a published candidate into verification; unavailable evidence remains explicit uncertainty.

The caller-side loopback proxy injects credentials into allowed Anthropic request paths and forwards SSE incrementally. SDK HttpReverseTunnel supplies the Sandbox URL. Health and protocol preflight precede inference. Reconnecting an in-flight model request after the caller process disappears is outside the v1 guarantee.

Cleanup state is separate from verifier verdict. A published result remains inspectable while deletion needs retry. Cancellation and cleanup target persisted task-generated names; the standalone operator helper checks exact owned container identities, data mount and empty inventory before stop.
