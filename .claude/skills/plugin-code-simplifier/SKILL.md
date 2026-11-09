---
name: code-simplifier
description: Simplifies recently changed code without changing behaviour.
---

- Only the changed code: remove duplication, dead code and needless nesting; inline one-use helpers.
- Behaviour stays identical: the tests pass before and after, unchanged.
- Match the surrounding style; do not rename public names.
