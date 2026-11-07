# Review (iteration 1)

## Review EL-R-04 against the spec (qa, done)

I couldn't run the four required test files: every pytest command was refused with "This command requires approval". So this review checks the code against the spec by hand. The only execution evidence is the developer's reported `26 passed in 20.76s`, and the gate runs the commands again.

## Logic verification

**`rca._causes`** (rca.py:246-276)
- **Normal case:** it reads only the project's approved `operational` lessons that have `report["rca"]` and are not `secret`. It drops checks that start with `task `. For each confirmed cause whose layer is in `LAYERS`, it uses the checks the cause cites, or all remaining checks if it cites none. It keeps a goal only if the goal is failed, belongs to this project, is not an eval or improvement goal, and is not secret. Goals and lessons go into sets.
- **Three goals with one cause:** the cause `("code", "command false")` ends up with three goals.
- **Same goal analysed twice:** a goal analysed alone and again in a streak is stored in a set, so it counts once.
- **Excluded cases:** `layer=""` fails the `LAYERS` test, `task spec` is dropped, an `approved` goal fails `counts()`, and a secret project is excluded both by `Lesson.classification` and by `goal_level`.
- **Error case:** the function only reads and raises nothing of its own. A database error passes up to the `try` in `analyse`.
- **Root cause or symptom:** this fixes the root cause. It is the counting the requirement asks for.

**`rca.propose_rules`** (rca.py:324-371)
- **Normal case:** it reads in one session, closes it, drafts the rules with no session open, then writes in a new session. That session checks for duplicates again before it adds the `Proposal(kind="rule", tag="rule")`. The goals are cited as `#a, #b, #c` in the title, description and `case.rule`, and `log_event(..., s=s)` uses the open session.
- **Fourth goal:** `existing()` finds the earlier proposal, so the cause is not due and nothing is added. The first proposal's `case["goals"]` stays the same.
- **Rejected rule:** `existing()` ignores status, so a rejected cause is never proposed again.
- **Error case:** `project_id is None` returns `[]`, and any other exception is caught by the `try` in `analyse` (rca.py:235-238), which logs it outside the session. Test (f) checks this with a `propose_rules` that raises.

**`rca._draft_rule`** (rca.py:283-317)
- **No instructor:** it returns the template line and `None`.
- **Instructor present:** it checks the budget, picks the model through `router.resolve` with the goal's classification, and builds `executors.Request` (which does have `agent_id` and `goal_id`, executors.py:37-38). `usage.record` accepts `res=` (usage.py:32).
- **Unusable answer:** an answer that is not exactly one line of 20–200 characters that passes the screen gets the template.
- **Error case:** `NotCleared` or a failed run returns the template. In that case the instructor id is lost (`None`), which only affects attribution.
- The read session closes before the model runs, as it should.

**`projects.decide` / `_to_rule` / `set_case` / `settle_proposals`**
- **Normal case:** a person approves. The earlier `restricted()` check (projects.py:1125) is unchanged, so restricted text still needs a person. The rule branch skips the chain guards and both spawn branches (projects.py:1141, 1149), and the status is not set inside the session. Then `_to_rule` runs after the session closes:
  - it reads the proposal;
  - it calls `catalog.add("rule", …)`, which uses its own session and creates an enabled item;
  - it updates the proposal in a second session, assigning a new `case` dict;
  - it posts the message and logs the event outside any session.
- **Export and goals:** `export_claude` writes `.claude/rules/<name>.md`, and `spawned_goal_id` stays `None`.
- **Edge cases:**
  - An agent approving a rule gets `PermissionError`.
  - A rejection sets the status to rejected through the existing path.
  - A name clash with another project's rule retries once with `-<proposal id>` appended to the name.
  - `set_case` refuses rule proposals.
  - `settle_proposals` sends rule proposals to the person-only list ("a project rule"), so the GM never decides them.
- **Error case:** if `catalog.add` fails twice, the `ValueError` is raised while the proposal is still `pending`, because no status was written, so it can be decided again.

**Writes inside an open session:** all of them use that session or run after it closes: `propose_rules` → `log_event(s=s)`; the error log in `analyse` comes after the `with` block; `_to_rule` uses separate sessions with no nesting. I found no second connection opened inside a session.

## Checklist
- **Contract:** EL-R-04 and spec §2–§8 are met in the server code. The two departures are spec §5's `OfflineExecutor._rule` and §8's dashboard labels. Neither changes what the server does.
- **Interfaces:** the changes add to what exists. Approving a rule now returns the proposal from `_to_rule`, and `analyse`'s report has an extra `rules` key that is not stored.
- **Error handling:** good. The guard in `analyse` and the template fallback mean the end of a goal cannot raise.
- **Edge cases:** all the required cases are covered (above). One small race is listed below.
- **Tests:** `tests/test_rule_proposals.py` covers spec §10 (a) to (f), including the full `loop.run_goal` path. The existing `test_rca.py` assertions filter by `goal_id` or count analyses, not rule proposals, so they should still pass. I did not run them myself.
- **Dependencies:** none added.
- **Security:** secret goals are excluded, and the model is chosen with the goal's classification. The drafted line is screened, and only a person can approve a rule.
- **Performance:** one scan of the project's lessons per analysis. That is acceptable.
- **Docs:** the `rca.py` entry in `CLAUDE.md` is updated. I did not open `docs/requirements/EVOLUTION-LOOPS.md`, so I cannot confirm its EL-R-04 row and Status line.

FINDING: src/agent_company/static/projects.js:253-254 | A person sees a rule proposal labelled "Approve as a project" with a "Documented case" button that always fails (`set_case` raises "a rule proposal has no documented case"). Approving still creates the catalog rule correctly | Implement spec §8's dashboard bullets: a `rule` note in `caseNote`, the case button only for `product`/`new_project`, and "Approve as a rule".
FINDING: src/agent_company/executors.py (OfflineExecutor, next to `_rca` at :909) | With an instructor enabled, the offline simulator answers `purpose="rule"` with `_task`'s multi-line text, so the template line is always used. The result is still deterministic, but the instructor path is never tested | Add `_rule(self, req): return req.data.get("fallback", "")` once `executors.py` may be changed (spec §5).
FINDING: src/agent_company/rca.py:350-356 | Two goals of the same project with the same cause can finish their analyses at the same moment. Both write sessions can pass the duplicate check before either inserts (the SELECT runs before the implicit BEGIN), which gives two proposals for one cause. Unlikely, but nothing in the database prevents it | Start the write session with `BEGIN IMMEDIATE`, or check the cause again after `flush`, or keep a unique key per project and cause.
FINDING: src/agent_company/rca.py:316 | When the model run raises, the template fallback returns `None` as the instructor id, so the proposal's `agent_id` loses the instructor | Keep `jid` in the `except` path (set it before `try`).
FINDING: tests (whole group) | I could not run `uv run pytest -q tests/test_rule_proposals.py tests/test_rca.py tests/test_projects.py tests/test_eval_proposals.py` because this session's permission layer refused it. The only execution evidence is the developer's reported `26 passed in 20.76s` | The gate should run the commands again and attach the summary line.

BLOCKER: Bash refused `uv run pytest …` and plain `pytest …` ("This command requires approval"), so this review has no test run of its own.
NEXT: The gate runs the four test files and records the summary line.
NEXT: Once the developer may change `static/projects.js` and `executors.py`, fix the first two FINDINGs.
MESSAGE recommendation to developer: in `rca.propose_rules`, close the dedup race (BEGIN IMMEDIATE or re-query after flush), and keep `jid` in `_draft_rule`'s exception path.

OUTCOME: APPROVED_WITH_COMMENTS
The server-side behaviour matches EL-R-04 and the spec:
- three failed goals with one cause give exactly one pending rule proposal citing them, and a fourth gives none;
- a rejected cause is never proposed again;
- excluded and secret cases don't count;
- the fallback never raises;
- approval by a person gives an enabled, exported catalog rule and spawns no goal.

The UI labels and the simulator method were left out because those files were outside the developer's allowed paths. I couldn't run pytest here, so execution relies on the developer's run and the gate.

VERDICT: PASS

PROPOSAL: Rule label in the Projects page :: rule proposals currently show project wording and a case button that always fails (spec §8).
