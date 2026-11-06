# ADR 0011: marketplace-fetch-method

Status: Accepted
Date: 2026-11-06
Decision record: #11 · goal #20 (REQ-002: agent marketplace — discover, evaluate, import external agents)
Category: technical

## Context

How should an external repository be fetched as untrusted data?

## Options

- shallow git clone with hooks/submodules disabled and a scheme allow-list
- download a release/tarball over HTTPS and extract with the archive guards
- fetch inside the L3 sandbox with network

## Criteria

- nothing from the package executes on the host
- works offline in tests
- provenance (commit sha)
- implementation cost

## Decision

Use a shallow git clone (--depth 1, --no-recurse-submodules, core.hooksPath=/dev/null, https only; file:// only under a test setting) into data/marketplace/<id>/src, with a timeout and size cap. Uploads use the same archive guards.

## Reasons

git clone does not run hooks from the remote. Disabling hooks and submodules removes the remaining execution paths. A clone gives an exact commit sha for provenance, and a local file:// repo makes offline tests easy. Fetching inside the sandbox would need network in the sandbox, which sandbox.py only allows for lock-file installs.

Evidence:

- CLAUDE.md sandbox.py description (network only for lock-file installs) (FACT)
- git clone does not execute repository hooks by itself (FACT)
- offline test fixture via file:// repo (INFERENCE)

## Consequences

Assumptions:

- git is installed on the host
- imports are rare and admin-initiated

Limitations:

- Does not cover private repositories that need credentials
- The size cap is enforced after the shallow clone, not during it

Confidence: 0.75 (Standard practice, but the exact git flags must be checked against the installed git version.)
