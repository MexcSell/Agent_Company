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
| A3 | Settings and the Users page are admin-only. `Luca` is not an administrator, so that browser does not show them. Fix: as `Admin`, Users → *Make admin* on `Luca`. (Earlier notes wrongly said this button was missing.) | fixed (checked 2026-11-09: `Luca` is an administrator) |
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
| T5 | Assigning agents to a topic changes who reports in its `/standup`, not who is staffed on its goals. | fixed (V22: assigned agents are staffed first on the topic's goals) |

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

## Added 2026-11-08 (verification phase 1, model registration)

See `docs/verification/phase-1-models.md`.

| # | Problem | Status |
|---|---------|--------|
| V1 | OpenAI's per-minute 429 ("Rate limit reached for <model> ... try again in 1.2s") matched `LIMIT_RE`, so one burst benched the model for 60 minutes as if its quota were used up. | fixed (`(?<!rate )` in `LIMIT_RE`; a used-up quota still benches it) |
| V2 | Deleting a model left routes escalating to it (`Route.escalation`); resolution skipped the name, but the Routes table still showed a model that no longer exists. | fixed (`delete_model` clears it) |
| V3 | A model Test was not audited, though it sends a request at the provider's cost. | fixed (`model_test` audit record) |
| V4 | Deleting a model leaves agents whose own model (`model_pref`) named it; they fall back to their role's route ("not in catalog" in the run's reason), and the agent card still names the removed model. | open (low) |
| V5 | A Test run from the CLI without `prefs.load()` sees none of the DB settings (`enable_claude_code`, `enable_codex`), refuses every CLI model and stores that refusal as the model's status. | open (low; use the Models page or load prefs first) |
| V6 | Five catalog API models (four Claude, OpenRouter Free router) have no key on this host; they show as unavailable and have never passed a Test. | open (configuration) |

## Added 2026-11-08 (verification phase 2, model routing)

See `docs/verification/phase-2-routing.md`. Each run now records `Run.trace` (assigned model, model run, model ID,
provider, address, why, the model the provider reported); `router.trace_issues` names disagreements.

| # | Problem | Status |
|---|---------|--------|
| V7 | With no fallback and the only assigned model disabled or keyless, the offline simulator does the work. That is the documented design, but phase 2's rule allows only configured fallbacks. The trace records it (`provider: offline`) and `trace_issues` flags it. | fixed (2026-11-09, owner's decision "choose the most suitable model among the models": with no configured model usable, `router._most_suitable` takes the catalog's usable models cleared for the work, ranked for the role's tier like Assign models; the simulator only when no real model is usable; a configured model at its limit with another usable no longer pauses the goal) |
| V8 | Codex and Copilot runs do not report which model answered (`served` empty; Codex's `model_id` is empty when it uses the account's default). | open (low) |
| V9 | Every model with a base URL reports provider `local`; the trace's `base_url` tells two such servers apart. | by design |
| V10 | A routing model (`openrouter/openrouter/free`, `openrouter/auto`) answers as whichever model it picked, so `trace_issues` reports "the provider answered as X, not free" on every run. The flag is kept on purpose (the model that served is still not the one named). | open (decide when such a model gets a key) |

## Added 2026-11-08 (verification phase 3, individual agents)

See `docs/verification/phase-3-agents.md`. Seven agents ran alone on their role tasks (live runs 2101–2114); results
were graded by running their code, counting seeded defects and validating plans, not by their own claims.

| # | Problem | Status |
|---|---------|--------|
| V11 | A `command` acceptance condition without `run` (`{"type": "command", "value": "pytest"}`) ran `true` and passed whatever the work was. | fixed (`contracts.Condition` refuses it; the planner is asked again) |
| V12 | Codex's own web tools (`web_search`, the ChatGPT apps server's `search_service.web_run`, browser and computer use) were on in every Codex run: the security and UI/UX agents (no `network`) searched the web, and the research lead used them instead of the platform's tools, then cited a document that did not exist. | fixed (`executors.CODEX_NO_WEB` on every `CodexExecutor` run; live rerun: research used `fetch_url`, UI/UX no web) |
| V13 | Codex's read-only sandbox runs any program for an agent without `shell` (`python3 -c`, as uid 0) and reads outside the worktree; writes and network are blocked. Codex reads files through these commands. | open (threat-model known gap; move a `read`-only agent to Claude Code, or decide how Codex should run it) |
| V14 | Claude Code's `acceptEdits` (writing runs) auto-approves file commands in the worktree (`rm -r __pycache__`) and read-only commands (`git log`, `cat`) outside the run's allow-list; `Run.tools` understates what the run could do. | fixed (2026-11-09: no `acceptEdits`; Edit/Write stay allowed by name, files are deleted and renamed with `git rm`/`git mv` (refused outside the repository), a run with no Bash pattern gets `--disallowedTools Bash`; checked live on Claude Code 2.1.310: `rm`/`mv` refused, edits and the syntax hook work. Left: a run with Bash patterns can still run Claude Code's own read-only commands (`git log`, `cat`) inside the worktree) |
| V15 | The GM's own model `claude_37` is benched until Nov 11; the GM runs on its role route (Claude Code) and `trace_issues` flags every run as a substitution without a configured fallback. | fixed (2026-11-09, owner's approval: Models → Routes → Assign models cleared every agent's own model; the GM runs its role route Claude Code with fallbacks Codex CLI, Copilot, local) |
| V16 | The research tool loop shows its planner only the first 400 characters of each document (`research_agent._material_text`): navigation text on sqlite.org, so it re-fetched the same pages and judged them "partial headers" (31,624 characters were stored). | fixed (2026-11-09: `research_agent._passages` shows each document's lines that hold the question's words, up to `PER_DOC` 1,200 characters, with its URL and stored size; the documents the loop fetched come first in the next round, and the step notes name each query/URL; `tests/test_research_agent.py`) |

## Added 2026-11-09 (verification phase 4, agent communication)

See `docs/verification/phase-4-communication.md`. The GM delegated to the developer through the real loop, offline
(two stub providers, 19 cases) and live (goal #330: GM → developer → QA, 130 s, a handoff message delivered).

| # | Problem | Status |
|---|---------|--------|
| V17 | A task did not record its sender (the planner's id stayed only in the goal's checkpoint, overwritten each iteration) or its creation time. | fixed (`Task.planned_by`, `Task.created_at`; shown in the goal trace) |
| V18 | An agent message to a role that does not exist was stored and never delivered; a `MESSAGE` line with an unknown kind was ignored. Nobody learnt either went nowhere. | fixed (each is an event in the goal's log naming the sender and the line; the misaddressed one is not stored) |
| V19 | `run_goal` checked "queued" and changed the status later: a goal handed out twice at once ran twice and the second run failed it (IllegalTransition). | fixed (`loop._claim`: one conditional UPDATE takes the goal) |
| V20 | The GM's acceptance commands (`python3 -B -m pytest`, `python3 -B -c`) were outside the developer's and QA's allow-lists; both ran plain `pytest`, leaving `__pycache__/` in the worktree. The gate runs the exact commands in L3. | fixed in phase 5 (`executors.PYTEST_FORMS`: `python3 [-B] -m pytest` allowed; `python3 -B -c` stays refused) |

## Added 2026-11-09 (verification phase 5, project registration)

See `docs/verification/phase-5-projects.md`. TEST-PROJECT-001/002 through the API: stored, reopened, updated,
isolated (users, goals, messages, files); offline only.

| # | Problem | Status |
|---|---------|--------|
| V21 | A project's General Manager (`Project.manager_id`) did not plan its goals: the best-ranked GM did. | fixed (`registry.staff(prefer=…)`; `loop._plan`, `_configure` and `rca` pass the project's manager) |
| V22 | A topic's agent list (the plan's "assigned developer/QA") is shown but staffing ignores it; workers come from the project's rooms, and every core agent is in `development`. | fixed (2026-11-09, owner's approval: `registry.staff` ranks a topic's idle assigned agents and their clones first; busy ones are not waited for; `tests/test_topic_staffing.py`) |

## Added 2026-11-09 (verification phase 6, project task execution)

See `docs/verification/phase-6-execution.md`. TEST-PROJECT-003 (project #2, archived) on this server: the order through
the command window, goal #332 (architect → developer → QA, 682 s), a working calculator in its repository.

| # | Problem | Status |
|---|---------|--------|
| V23 | A project's description (its registered requirements) reached no agent; the planner got stack, rooms, deadline, priority, budget and formal requirements only. | fixed (`loop._project_context`: "Description (as registered)") |
| V24 | Task stages had no transition table: a late worker moved a cancelled task on (goal #111, task 691: cancelled → review → failed). | fixed (`workflow.TASK_TRANSITIONS`; `completed`/`cancelled` final; illegal moves refused and logged) |
| V25 | A goal run in a process that ends while a background thread works on it stays `planning` until the next server restart: the phase 4/6 live scripts left smoke-trial eval goals #331 and #333 (cancelled by hand). | fixed (2026-11-09: `Goal.owner` = the process that took it, `loop.process_token` pid + start time; the server's watcher puts a planning/running goal whose process ended back to recovering and resumes it, `loop.reclaim_orphans`; a restart leaves goals another live process runs; `tests/test_workflow.py`) |

## Added 2026-11-09 (verification phases 7 and 8, quality and evolution)

See `docs/verification/phase-7-quality.md` and `phase-8-evolution.md`. Live: this server's QA passed a correct
delivery and failed a buggy and an incomplete one (goals #334–#336), naming each defect; the gate agreed.

| # | Problem | Status |
|---|---------|--------|
| V26 | A cross-family review depends on the routes: QA's own model and the developer's route are both Claude Code, so developer and QA run on the same family (runs 2125/2126). | fixed (2026-11-09: Assign models gives `developer` Claude Code and `qa` Codex CLI, another family) |
| V27 | Playbook experiments dropped the suite's S09-R-05 dimensions (compliance, tokens, tool failures) that route and template experiments keep. | fixed (`evolution.smoke_trial` stores `dimensions` per side) |
| V28 | Playbook promotions rest on a 3-task smoke suite with 1 repeat, no held-out set, and a 3-task live canary; #16 was promoted on 1/3 live. | fixed (2026-11-09, owner's approval: settings `candidate_repeats` 2, `holdout_suite` python-final (hidden checks, run after the smoke suite passes, must not lose or break a solved task), `canary_window` 5, `canary_min_pass` 50 %; `tests/test_evolution.py` V28 tests) |
