# Known issues

Problems found while setting up and running Agent Company, with their status.
Update this file when one is fixed or a new one is found.

Status: **open** (not solved), **mitigated** (worked around, risk remains), **fixed**.

## GitHub and Git

| # | Problem | Status |
|---|---------|--------|
| G1 | Every push from the website used the server's single GitHub login (`gh`, account `thomaschang0912`), whatever user or project. Pushing to a repository that account cannot write to fails with 403. This is how pushing this repository to `MexcSell/Agent_Company` failed on 2026-11-04. | fixed for this repository (deploy key, see G2); website projects: see G3 |
| G2 | This repository pushes through its own deploy key (`~/.ssh/id_ed25519_agent_company`, set in `.git/config` only). The key lives on the server; whoever has root can push with it. | mitigated |
| G3 | Users have a Git name, email and branch (Users → Git), but no GitHub credentials. Work is committed under the right person but pushed with the server login. | fixed: each user has their own GitHub token or SSH key (Git settings); see `gitsync.py` |
| G4 | Whose settings a push uses. Decision: automatic push after approval uses the user who ordered the work; a manual Sync uses the person who clicked it. Without a credential of their own, the server login is used and the result says so. | by design |
| G5 | Only administrators can set Git settings, so every user has to ask an administrator to change their name, email or branch. | fixed: users edit their own (Git button next to their name) |

## Connection (HTTPS / HTTP)

| # | Problem | Status |
|---|---------|--------|
| C1 | In HTTP mode the device secret crosses the network unencrypted on every visit. An eavesdropper can sign in as that user permanently. Sealing the cookie does not help against this. HTTPS is the default for this reason. | open (by design, HTTP is optional) |
| C2 | The certificate comes from the company's own CA. Every computer has to install the CA once; without it browsers warn and Chrome turns its cache off, which makes pages slow. | mitigated (download and instructions on Settings → Connection) |
| C3 | The server certificate is renewed only when the server starts (it is valid 825 days, renewed when under 30 days remain). A server that runs for more than two years without a restart could serve an expired certificate. | open (low) |
| C4 | Tabs opened before a scheme switch run the old JavaScript and do not follow the hand-off; they must be reloaded once. `claude.js` stops polling on 401. | open (low) |
| C5 | No HSTS on purpose: it would make the HTTP option unusable for the HSTS lifetime. Do not add it while the HTTP option exists. | by design |
| C6 | During the first switch to nginx, the old server process picked up the new `.env` while still holding port 4900, so nginx briefly proxied to itself. | fixed (`transport.apply` refuses PORT == PUBLIC_PORT) |
| C7 | nginx (user `www-data`) cannot read `/home/luca` (mode 750), so it cannot serve `/static` from disk. | mitigated (static files are proxied through the app with cache headers) |
| C8 | `systemctl reload nginx` returns before the new workers serve, so a browser switching at that moment could hit the old mode. | fixed (`transport._wait_ready`) |

## Accounts and access

| # | Problem | Status |
|---|---------|--------|
| A1 | The first account becomes administrator (with a root shell in the Workspace). With the port open to the internet, a stranger could have claimed it. | fixed (first account only from localhost) |
| A2 | Creating that first account needs a port forward (SSH or VS Code) to `localhost:4900`. | by design |
| A3 | Settings and the Users page are admin-only. `Luca` is not an administrator, so that browser does not show them. Fix: as `Admin`, Users → *Make admin* on `Luca`. (Earlier notes wrongly said this button was missing.) | open (needs an administrator's click) |
| A4 | `localStorage` is per origin (scheme + host + port), so the same browser on `http://`, `https://`, `localhost` and the IP address counts as different computers. The hand-off covers HTTP ↔ HTTPS only. | open (low) |

## Server (outside this project)

| # | Problem | Status |
|---|---------|--------|
| S1 | The firewall (ufw) is inactive and the server has a public IP. Besides Agent Company, these listen on every interface: Samba (139, 445), nginx (80, 8081), 3000, 4800, 8000, 8082, k3s (6443, 10250), Cockpit (9090). Check that each is meant to be public, or block it at the hosting provider's firewall. | open |

## Added 2026-11-04 (evening)

| # | Problem | Status |
|---|---------|--------|
| N1 | GitHub Copilot's CLI is not installed on this server, so the Workspace panel lists Copilot as "not installed". Once installed (`npm i -g @github/copilot`) it still needs a streaming-JSON adapter like Codex's (`workspace.normalise_codex`). | open |
| N2 | `enable_claude_code` was switched on so the GM (Claude Code) works: goals and the GM's `.claude/` configure step (one Claude Code run per goal) use the server's Claude subscription. Turn it off in Settings to stop that; the GM then falls back to the simulator (no API key in `.env`). | by decision |
| N3 | The Machines "interval between screen transmissions" is enforced in the browser (noVNC's update requests are delayed); the VNC server itself still sends whatever changed when asked. | by design |
| N4 | Machine screen settings are kept per browser (localStorage), not per machine on the server. | by design |
| N5 | Learning rewrites only the *Working method* and *Knowledge notes* sections of an agent's definition; mission, skills, model and permissions come from the agent's card and overwrite manual edits of those sections on the next sync. | by design |
| N6 | Non-administrators now see only projects they created (plus ones an administrator grants, or "all"). Users whose permissions were saved before keep `projects: "all"`. | by decision |
| N7 | The File Manager is the Workspace's windows without Claude Code: both pages share one set of open files, terminals and explorer panes. | by design |
| N8 | Explorer panes added with "+" refresh only on their refresh button (the main explorer polls). | open (low) |

## Added 2026-11-05 (safety and pipeline quality)

| # | Problem | Status |
|---|---------|--------|
| P1 | Pausing a goal takes effect at the next step boundary: tasks already started finish first (an emergency stop, by contrast, ends their processes). | by design |
| P2 | The executor output limit (5 M characters) cuts the output after the run has ended; it does not stop a run while it produces output. The timeout does stop it. | open (low) |
| P3 | Under E5, goals created during the stop stay queued; the queue's watcher offers them again every 5 s and they are held until the stop is lifted. | by design |
| P4 | A restart resumes the planned iteration but does not reset the worktree to the last recorded commit: a task cut off mid-write leaves its partial edits, and its rerun starts on top of them. | fixed (checkpoint commits, `loop._reconcile`) |
| P5 | Reviews must now contain a "Logic verification" section and an approving OUTCOME; a model that answers with a bare `VERDICT: PASS` fails the gate until it follows the checklist in its prompt. | by design (S08-R-03) |
| P6 | Proposals mentioning permissions, budgets, routes, settings, security, audit, credentials and similar words are left to a person even under the `superiors` policy; the word list is deliberately broad. | by design (S14-R-02) |
| P7 | Acceptance commands are written by the planner, so three commands refused by the policy in one goal pause the planner, normally the company's General Manager (E1). Until an administrator lifts that stop (System → Narrower stops, or `python -m agent_company stop --lift --level E1 --scope <id>`), every new goal fails at planning with "every general_manager agent is paused". | by design (S14-R-03) |

## Added 2026-11-05 (step 1 and 2 leftovers)

| # | Problem | Status |
|---|---------|--------|
| P8 | A goal whose budget runs out is now **paused**, not failed (`budget_exhausted_action`, Settings; set it to `stop` for the old behaviour). It stays paused until an administrator raises the budget and resumes it; resuming while still over budget is refused. | by design (S04-R-09) |
| P9 | The time budget counts per run of the loop: a resumed goal (after a pause or a restart) gets a fresh time window. | open (low; per-goal budgets are S04-R-09) |
| P10 | The goal branch now has one commit per finished writing task plus one for the plan (`Goal N iteration M: <task>`), as well as the gate's. The merge into the base branch is still one merge commit. | by design (S04-R-05) |
| P11 | A conflict review (S04-R-03) runs after the iteration's other tasks, so the implementation may already follow the rejected recommendation; the review then fails the gate and the next iteration corrects it, at the cost of one more iteration. | by design |
| P12 | With `candidate_smoke` on (default), each new candidate playbook runs the smoke suite twice (current and candidate playbook: 6 goals with `repeats` 1). On real models this costs model time; it starts only when an agent's success rate falls below 50 % over 6 tasks. The offline simulator fails the smoke suite on purpose, so offline both sides score 0 and the candidate goes on to its live trial. | by design (S13-D-02) |
| P13 | While a smoke trial runs its candidate side, every eval goal (including one an administrator starts with `eval`) uses that agent's candidate playbook. Run benchmarks when no smoke trial is running (Evolution page). | open (low) |
| P14 | The "cost > 2× estimate" rule needs an estimate: the order's `estimated_cost_usd` or the planner's. Goals with neither are only limited by `goal_budget_usd`. | by design (S14-R-03) |
