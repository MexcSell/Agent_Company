---
name: security-review
description: Review a change for exploitable weaknesses.
---

- Trace untrusted input to sinks: SQL, shell, file paths, templates, URLs (SSRF), deserialisation.
- Check authn/authz on every new endpoint or action; secrets in code, logs and errors.
- Each finding: the attack, the input, the impact, the fix. No theoretical findings without a path.
