# Synthetic code task

This is the repository-owned `akrun.synthetic.code-task@1` fixture. Its immutable task image contains Python, Git and the clean `/workspace` repository at commit `69b31ae17c45d731fd5b6fa43660c05aea4a9027`. The fixed build-author metadata is retained from the imported seed so this commit stays reproducible. Runtime does not reconstruct the task workspace by uploading source files.

Build the image, then load it through the task-owned local registry described in the root README:

```bash
docker buildx build --platform linux/amd64 --load \
  -t akrun-local/synthetic-code-task:1 fixtures/synthetic/code-task-v1
uv run python tools/standalone.py load-images
```

Use the returned digest for `akrun resolve-synthetic --task-image`. The inference and verification Sandboxes may use the same immutable image; their writable filesystems remain independent. Claude Code is supplied separately as a read-only runtime image mounted at `/__claude_code` only during inference.

The prompt asks the agent to fix `add_one`. The two repository tests cover positive and negative values. `gold.patch` passes, `known-bad.patch` fails, and `empty.patch` leaves the original failure. These deterministic controls use the same candidate publication and fresh verification path as real Claude Code. Only Linux/amd64 is included in this task's acceptance record; the image definitions also retain their original arm64 build path.
