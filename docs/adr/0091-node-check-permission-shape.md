# ADR 0091: node-check-permission-shape

Status: Accepted
Date: 2026-11-07
Decision record: #91 · goal #86 (Allow `node --check src/agent_company/static/*.js` for agent sessions)
Category: technical

## Context

How should agent sessions be allowed to run the JS syntax check without letting node run code?

## Options

- Raw prefix rule `Bash(node --check:*)` only
- Raw prefix plus a governance refusal of the -r/--require/--import/-e/-p/--loader flags
- Only a wrapper `python3 -I edit_check.py <files>` that runs node --check per file

## Criteria

- Matches the literal goal command
- Cannot run project code
- Small change, easy to test

## Decision

Raw prefix plus a refusal of the code-running flags. The architect confirms or switches to the wrapper in design.md.

## Reasons

The goal names `node --check src/agent_company/static/*.js`, so a wrapper alone would not satisfy it. A prefix rule alone would admit `node --check -r ./x.js`, which preloads a module and may run code.

Evidence:

- src/agent_company/executors.py:592 (FACT)
- src/agent_company/executors.py:357 (FACT)
- src/agent_company/edit_check.py:6 (FACT)
- K619 (FACT)
- `--require` executing under --check (HYPOTHESIS)

## Consequences

Assumptions:

- Claude Code prefix rules match the command start only

Limitations:

- Whether node runs --require modules under --check is not verified here; QA must test it

Confidence: 0.65 (The cause in the gateway is a FACT; how node behaves with these flags is untested.)
