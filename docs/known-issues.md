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
| P14 | The "cost > 2× estimate" rule (factor: setting `cost_overrun_factor`) needs an estimate: the order's `estimated_cost_usd` or the planner's. Goals with neither are only limited by `goal_budget_usd`. | by design (S14-R-03) |

## Added 2026-11-05 (project management, step 3)

| # | Problem | Status |
|---|---------|--------|
| M1 | Blockers and generated action items are reconciled after every goal and when a project's Plan tab or `/status` is read, not on a timer: a goal that crosses `approval_blocker_hours` while nobody looks shows as a blocker only on the next read. Overdue notifications have the same timing. | by design |
| M2 | Milestone command criteria run on the project's current HEAD (its checked-out branch), not on a commit recorded with the milestone. | by design |
| M3 | Only administrators edit milestones, risks, action items and insights; other users with access to the project see the Plan tab read-only. | by decision |
| M4 | Insights are read only from fenced ```json blocks holding `{"insights": [...]}`; an agent that writes them as bare JSON or prose is not recorded. | by design (S05-R-03) |

## Added 2026-11-05 (topics)

| # | Problem | Status |
|---|---------|--------|
| T1 | `/summary` runs the GM's model once (Claude Code by default, so the server's Claude subscription). Like the GM's proposal triage, that run is not recorded in usage, so goal budgets and the cost rule do not see it. Offline it returns the extractive draft only and finds no new decisions. | by design (S05-R-07) |
| T2 | Goals of one topic run one after another (G-R-07). A goal stuck in `queued` in a topic (for example one queued by `gm goal` while the server was down, until the watcher picks it up) holds every later goal of that topic; paused goals and goals waiting for approval do not. | by design |
| T3 | `/stop` cancels the project's goals in every topic, whichever topic it is sent in; `/status` in a topic other than `general` lists only that topic's goals, blockers are project-wide. | by design |
| T4 | Databases made before topics get the `topic` column (all old messages and goals in `general`) but not its index; filtering is by project first, so this only matters for very long histories. | open (low) |
| T5 | Assigning agents to a topic changes who reports in its `/standup`, not who is staffed on its goals. | open (staffing by topic is not specified) |

## Added 2026-11-05 (agent versions and health, SPEC-02)

| # | Problem | Status |
|---|---------|--------|
| R1 | Skill levels grown by experience (`learning`) are not configuration versions, so rolling back to an older version also resets the skill levels to that version's. | by design (S02-R-01) |
| R2 | A degraded agent is never staffed, so it gets no runs that could make it healthy again: it stays degraded until an administrator resets its health (agent card) or changes its model (a task it is still running can heal it by succeeding). Staffing uses a clone in the meantime; each retry is a run, so one outage that fails three retries in a row degrades the agent. | by design (S02-R-02) |
| R3 | Runs that end with "terminated by signal" (an emergency stop or a cancel) do not count as executor errors; neither does a failed quality gate. A provider that crashes its CLI with a signal is therefore not detected by the error count. | by design |
| R4 | `model_pref` must now name a catalog model or an existing agent; an edit setting any other text gets 400. Preferences stored before keep working until the agent is edited. | by design (AT-008) |
| R5 | A rollback restores the model preference of that version; if that model was deleted since, the rollback is refused. | by design |
| R6 | During a provider outage, staffing clones the degraded agent once per task; the clone asks for the same model, so it may degrade too. Clones are merged and retired when their goal ends. When the cause is the model's failed test, staffing fails instead of cloning. | mitigated |

## Added 2026-11-05 (classification, SPEC-14 S14-R-05)

| # | Problem | Status |
|---|---------|--------|
| K1 | Hosted models (Claude Code included) are cleared only up to `internal` by default. A project or order marked `confidential` or higher fails at planning until an administrator clears a model for it (Models → "Receives up to") or a local model is routed. | by design |
| K2 | The administrator's own Claude Code panel (Projects → General Manager, the Workspace) is not filtered by classification: it is the owner's tool, not an agent (CLAUDE.md). | by decision |
| K3 | Whether a model is "local" is read from its address (localhost, 127.0.0.1, ::1) or provider (Ollama, vLLM); a model on another machine of your own network counts as hosted until it is cleared by hand. | by design |
| K4 | A classification chosen for an order applies to the files attached to it; a file uploaded and never sent keeps the level chosen at upload. | by design |

## Added 2026-11-05 (source monitoring, ASEAO06 S06-R-02)

| # | Problem | Status |
|---|---------|--------|
| W1 | Sources are only visited while the rotation runs (Settings → `rotation_interval` > 0); with it at 0 (the default) per-source intervals do nothing and only the Run buttons fetch. | by design |
| W2 | The rotation now wakes every 5 minutes (or its interval, if shorter) and takes one due source per wake-up, so every source is visited once per its interval; before, one source was visited per rotation interval in turn. With many hourly sources a wake-up still serves only one. | by design |
| W3 | A change is "relevant" when the new page contains one of the subject's words anywhere, not when the changed lines do. | open (low) |

## Added 2026-11-05 (per-agent concurrency and quotas, SPEC-02 S02-R-03)

| # | Problem | Status |
|---|---------|--------|
| Q1 | A quota stops new tasks, not a run already going: an agent can end the day above its quota by the cost of the tasks it was running when it crossed it. | by design |
| Q2 | Quota days are UTC days. A goal paused for a quota resumes on the goal queue's watcher (every 5 s, server only), not from the CLI. | by design |
| Q3 | Only the base agent's quota counts; a quota set on a clone is ignored (clones take their base's limits). | by design |
| Q4 | Costs come from the model catalog's prices; runs on models without a price (Claude Code on a subscription, the simulator) cost $0 and never use a quota. | open (quotas need priced models) |
