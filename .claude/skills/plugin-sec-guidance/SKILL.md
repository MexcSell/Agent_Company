---
name: sec-guidance
description: Security guidance for code and infrastructure.
---

- Inputs: validate at the boundary; parameterised queries; no shell strings from user data.
- Secrets: never in code, logs or output.
- Authz: every action checks who may do it.
- Dependencies: pinned, audited; network calls only to checked addresses.
