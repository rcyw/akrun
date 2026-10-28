# Versioned artifact contracts

The akrun namespace starts at schema version 1, independently of Axrun persisted state. Resources use integer milli-CPU and MiB quantities. Sandbox bindings contain immutable image digest, platform, working directory and runtime. Specification digests bind adapter configuration and input hashes; unknown top-level fields and unsupported adapter identities fail closed.

ExecutionRef contains stage_id, sandbox_name, sandbox_id when known, optional command_pid and launch_intent. A missing PID after a lost response is not evidence that inference did not start. Recovery requires the original stage identity and plan digest. Transport sessions and credentials never become execution facts.

CandidateBundle v1 includes task/seed identity, harness/candidate versions, inference stage/Sandbox IDs, role/path/media-type/length/SHA-256 entries and a canonical manifest digest. Patch bundles use the patch role. TrajectoryBundle v1 binds the same identities to validated akrun.trajectory@1 JSONL and usage JSON. Native Claude events remain Sandbox-local; the normalizer strips thinking, signatures and credential material according to its retained policy.

VerificationResult v1 references the candidate digest, verifier identity/version, passed/failed verdict, exit code, output digest and timestamps. Final record reads verify bundle integrity and cross-reference provenance. These checks establish integrity, not an independent signed attestation of a caller-controlled local store.

Missing/duplicate outputs, oversized files, invalid digests, path escapes and symlink bundle members are rejected. Preliminary downloads are staging evidence; only validated bundles and results receive published references.
