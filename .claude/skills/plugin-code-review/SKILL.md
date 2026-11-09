---
name: code-review
description: Reviews a diff for bugs, security problems and missed requirements.
---

- Read the task's acceptance conditions first, then the diff, then the code around it.
- Look for: wrong logic and edge cases, missed requirements, unhandled errors, injection and authz gaps, secrets, races, missing or weak tests.
- Each finding: file:line, what fails, a concrete input that shows it, severity. No style nits unless asked.
- Say PASS only when nothing blocks; never approve unverified claims.
