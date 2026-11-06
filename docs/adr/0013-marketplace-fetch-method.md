# ADR 0013: marketplace-fetch-method

Status: Accepted
Date: 2026-11-06
Decision record: #13 · goal #20 (REQ-002: agent marketplace — discover, evaluate, import external agents)
Category: technical
Supersedes: [0011-marketplace-fetch-method](0011-marketplace-fetch-method.md)

## Context

Keep decision #11 (shallow https git clone) now that the reviews found DNS rebinding and an unbounded clone size?

## Options

- Keep the shallow clone; add a pinned curloptResolve, blob:limit filter, no-checkout plus size check, no lazy fetch, RLIMIT_FSIZE
- Fetch a forge tarball over HTTPS with our own size-bounded HTTP client and resolve-pinning
- Clone inside the bubblewrap sandbox with a network-only egress proxy that refuses non-global addresses

## Criteria

- SSRF resistance
- size bound while fetching
- works for any git host
- implementation cost this iteration

## Decision

Keep the shallow clone and add the pinning and size bounds listed in the first alternative.

## Reasons

The reviewers proposed exactly these mitigations (FACT: review findings at marketplace.py:142/172). git's http.curloptResolve passes the checked address to libcurl's CURLOPT_RESOLVE, which closes the gap between the check and the second lookup (INFERENCE, from the research notes; docs not fetched). A tarball client only works for forge-specific hosts, and an egress proxy is a larger new component.

Evidence:

- src/agent_company/marketplace.py:114-186 (FACT)
- Reviewer findings, iteration 1 (QA + security) (FACT)
- K60 research notes: http.curloptResolve needs git ≥2.37; GIT_NO_LAZY_FETCH needs git ≥2.45 (HYPOTHESIS)

## Consequences

Assumptions:

- The host's git is 2.37 or newer. Older versions are refused, not cloned unpinned.

Limitations:

- The git version on the host wasn't checked: the command needed approval. If git is older than 2.45, GIT_NO_LAZY_FETCH may be ignored. The ls-tree size check and RLIMIT_FSIZE are still in place, but lazy fetch of large blobs would then be bounded only per file.

Confidence: 0.75 (The mitigations are standard. The git version support on this host isn't verified.)
