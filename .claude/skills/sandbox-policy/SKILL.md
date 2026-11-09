---
name: sandbox-policy
description: Keep every action inside the sandbox.
---

- Commands run only in the goal's worktree through the sandbox; no production hosts, no host paths outside it.
- Refuse and report any step that needs network, secrets or another goal's files.
