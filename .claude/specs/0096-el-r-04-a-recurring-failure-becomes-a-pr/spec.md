# Spec: recurring-failure rule proposals (EL-R-04)

Folder: `.claude/specs/0096-el-r-04-a-recurring-failure-becomes-a-pr/spec.md`. Requirements: EL-R-04, S07-R-02.

## 1. Behaviour in one paragraph

Each root-cause analysis (`rca.analyse`) ends by calling `rca.propose_rules(project_id)`. That function collects causes from every approved RCA knowledge item of the project. A cause is a pair `(layer, check)`. It counts the distinct failed goals for each cause. When a cause reaches `settings().rule_proposal_min_goals` goals (default 3) and the project has no rule proposal for that cause yet, it creates one pending `Proposal(kind="rule")` that cites those goals. The instructor drafts the one-line rule. If it cannot, a deterministic template line is used. When a person approves the proposal, it becomes an enabled catalog `rule` item. `catalog.export_claude` and `gm configure` already write enabled rules to `.claude/rules/<name>.md`, so no export code changes.

## 2. Cause key and counting (`rca._causes`)

`_causes(s, project_id) -> dict[tuple[str, str], dict]`. It runs in the caller's session and only reads.

- **Lessons used:** `Lesson.project_id == project_id`, `kind == "operational"`, `status == "approved"`, `classification != "secret"`, and `(report or {}).get("rca")`. Order them by `Lesson.id`.
- **Checks of the item:** `watch = [c for c in report["watch"] if not c.startswith("task ")]`. Task keys belong to one plan, so they are excluded, as `_streak` already does.
- **Pairs:** for each `c` in `report["confirmed"]` whose `c["layer"] in selfevolve.LAYERS`:
  - `cited = [r[6:] for r in c["evidence"] if r.startswith("check:") and r[6:] in watch]`
  - the pairs are `(c["layer"], chk)` for each `chk` in `cited or watch`.
  - Unclassified causes (`layer == ""`) and CONTRIBUTING entries never count.
- **Goals of a pair:** the item's `report["goals"]`, kept only when all of these hold:
  - the `Goal` exists and has `project_id == project_id`;
  - `Goal.status == "failed"`. Streak analyses also cover goals that are `approved` or `awaiting_approval`, and those are not failed goals.
  - `Goal.origin not in ("eval", "improvement")`;
  - `learning.goal_level(s, g) != "secret"`.
- **Result per cause:** `{"goals": set[int], "lessons": set[int]}`, unioned over all items. A goal analysed twice (once alone, once in a streak) counts once.

## 3. Threshold setting

- `config.py`: field `rule_proposal_min_goals: int = 3` next to `max_proposal_goals` (line 44), and `rule_proposal_min_goals=_int("RULE_PROPOSAL_MIN_GOALS", 3)` in `load_settings` (line 179).
- `prefs.py`: description `"rule_proposal_min_goals": (int, "Failed goals with one RCA cause before a project rule is proposed (EL-R-04)")` (line 22), and range `(2, 20)` in the ranges dict (line 115).

## 4. Trigger: `rca.propose_rules(project_id: int | None) -> list[int]`

It returns the ids of the proposals it created. It never raises. It works in three phases so that no session is open during a model run (SQLite allows one writer):

1. **Read** (`with session() as s`):
   - Return `[]` when `project_id is None`.
   - Get `causes = _causes(s, project_id)`.
   - Read `existing = {(c["cause"]["layer"], c["cause"]["check"]) for rule proposals of the project}`. This covers every `Proposal` with `project_id == project_id` and `kind == "rule"`, whatever its status.
   - Keep `due = sorted(k for k, v in causes.items() if len(v["goals"]) >= min_goals and k not in existing)`, so the order is deterministic.
2. **Draft** (no session open): for each due cause, `line = _draft_rule(layer, check, sorted(goals))` (§5).
3. **Write** (a new `with session() as s`, one per call):
   - For each due cause, check the dedup again (same query as `existing`).
   - Add the Proposal (§6) and `s.flush()`.
   - Log `log_event("rca", f"rule proposal {p.id} for {layer}/{check} from goals …", goal_id=latest, s=s)`.

**Call site:** at the end of `rca.analyse`, after the `with session()` block that stores the item has closed and before `return report`:

```python
try:
    report["rules"] = propose_rules(project_id)   # project_id captured inside the block as row.project_id
except Exception as e:  # noqa: BLE001 - a rule proposal never breaks the analysis or the goal's ending
    log_event("error", f"rule proposals of goal #{goal_id}: {type(e).__name__}: {e}", goal_id=goal_id)
```

`propose_rules` also catches its own exceptions per cause: a failed draft falls back to the template. The `try` above is the last guard. `report["rules"]` is not written back to `Lesson.report`; it only appears in the return value.

## 5. Drafting: `rca._draft_rule(layer, check, goal_ids) -> str`

This is the seam tests may monkeypatch.

- **Template** (deterministic; it must pass `learning.screen`):
  `TEMPLATE = "Before handing work back, make sure the check '{check}' passes; it failed for a {layer} cause in goals {ids}."`
  `{ids}` is `"#a, #b, #c"`. The check text is cut to 120 characters and stripped of newlines.
- **Model draft**, following `exercises.py:169-181`:
  1. `judge = first Agent with role == "instructor" and enabled`, ordered by id. If there is none, use the template.
  2. If `_out_of_budget(max(goal_ids))` is true, use the template.
  3. `choice = router.resolve("instructor", judge.model_pref, classification=level)`, where `level` is the latest goal's `goal_level`. `router.NotCleared` falls back to the template.
  4. `req = executors.Request(purpose="rule", role="instructor", agent_id=judge.id, goal_id=max(goal_ids), system=f"You are {judge.name}, the instructor.", prompt=..., data={"fallback": template, "layer": layer, "check": check})`. The prompt is: "Write ONE line, a rule for `.claude/rules/` that would have prevented this cause: layer {layer}, failing check '{check}', goals {ids}. Answer with the line only."
  5. `res = executors.for_choice(choice, settings().command_timeout).run(req)`, then `usage.record(..., purpose="rule", goal_id=...)` as in exercises.py.
- **When the answer counts as usable:** `res.ok` is true, and `res.text.strip()` is exactly one non-empty line after stripping a leading `-*#` and a `RULE:` prefix. The line is 20–200 characters, and `learning.screen(line)[0] == "approved"`. Otherwise use the template. Any exception also falls back to the template.
- **Offline executor:** add `OfflineExecutor._rule(self, req) -> str: return req.data.get("fallback", "")` in `executors.py`, next to `_rca`. Without it the simulator answers with `_task`'s multi-line text. With it, offline runs are deterministic even when an instructor is enabled.

## 6. The Proposal

```python
Proposal(goal_id=max(goals), project_id=project_id, agent_id=judge_id_or_None, kind="rule", tag="rule",
         status="pending",
         title=f"Rule: {line}"[:256],
         description=(f"{line}\n\nCause: {layer} layer, failing check '{check}', confirmed by root-cause analyses "
                      f"{', '.join(f'K{k}' for k in sorted(lessons))} in goals {', '.join(f'#{g}' for g in goals)}."),
         case={"cause": {"layer": layer, "check": check}, "goals": sorted(goals), "rule": line, "name": name,
               "lessons": sorted(lessons)})
```

- **Name:** `name = f"rca-{layer}-{slug(check)}"[:64].rstrip("-.")`. `slug` lowercases the check and replaces each run of characters outside `[a-z0-9_.-]` with `-`, so the name matches `catalog.NAME_RE`.
- **Fields stay within limits:** `tag` and `kind` are ≤16 characters (`"rule"`). `case` is the existing JSON column, so no schema change is needed.

## 7. Deduplication

- Any `kind="rule"` Proposal of the same project whose `case["cause"]` equals `{layer, check}` blocks a new one, whatever its status. A rejected rule is therefore never proposed again.
- **A 4th failure with the same cause leaves the existing proposal unchanged** (recommended and specified). Its `case["goals"]` stays the goals that reached the threshold, so the result is deterministic and a pending proposal a person is reading does not change under them. The later goals stay visible in the RCA items.
- The dedup is per project. Two projects can each have a proposal for the same cause (§8 handles the catalog name clash).

## 8. Decision: `projects.decide` and `projects._to_rule`

In `decide`:

- Keep the restricted check as it is: an agent cannot approve restricted text.
- Add `if approve and kind == "rule" and not by_human: raise PermissionError("a project rule changes every agent's instructions: a person decides it")`. Approved rules go into the global catalog and are exported to every project's `.claude/rules/`, so the decision belongs to a person, like a definition change (G-R-19).
- Extend the guard bypass from `if kind == "project": pass` to `if kind in ("project", "rule"): pass`. Otherwise `_spawned_from`/`_depth` would turn an approval into a rejection.
- Change the status line to `if kind != "project" and not (kind == "rule" and approve):`. Rejection then only marks the proposal rejected, through the existing path, and the existing "rejected" post follows.
- Make both `spawn` branches conditional on `kind not in ("project", "rule")`. A rule never spawns a goal.
- After the session: `if kind == "rule" and approve: return _to_rule(proposal_id, decided_by)`.

New `_to_rule(proposal_id, decided_by)`, modelled on `_to_project`:

1. Read the proposal and its `case` in one session.
2. Outside any session, call `catalog.add("rule", name, description=f"From RCA: {layer}/{check} in goals #a, #b, #c", content=f"- {line}\n\nEvidence: {description}", source=f"proposal:{proposal_id}", created_by=decided_by)`. On `ValueError` because the name exists, retry once with `f"{name[:56]}-{proposal_id}"`. `CatalogItem.enabled` defaults to `True`.
3. In a second session: `p.status = "approved"`, `p.decided_by = decided_by`, and `p.case = {**case, "catalog_id": item["id"], "name": item["name"]}`. Assign a new dict: in-place JSON changes are not saved.
4. Post `"Proposal approved by …: rule '<name>' added to the catalog."` and log the event.

**Other functions:**
- `set_case`: refuse with `ValueError("a rule proposal has no documented case")` when `p.kind == "rule"`. `"rule"` stays out of `PROPOSAL_KINDS`, so no kind can be switched to rule.
- `proposal_dict` and `list_proposals`: no change. `case_missing("rule", …)` already returns `[]`.
- `settle_proposals`: `rca.after_goal` runs inside `_run` (`_notify_goal`), before `settle_proposals` in `run_goal`'s `finally`. A rule proposal whose `goal_id` is the ending goal is therefore settled there. Add `Proposal.kind == "rule"` next to `CASE_KINDS` in the `projects_` query, giving `Proposal.kind.in_((*CASE_KINDS, "rule"))`, with the reason text "a project rule". The proposal then goes to the "needs a person" list and is never GM-triaged.

**Dashboard (`static/projects.js:253-254`):**
- `caseNote` returns `" · <span title=\"Becomes a catalog rule for .claude/rules/\">rule</span>"` for `p.kind === "rule"`.
- Show the "Documented case" button only for `product`/`new_project`.
- The approve label is `p.kind === "rule" ? "Approve as a rule" : …`.

## 9. Files and functions to change

| File | Change |
|---|---|
| `src/agent_company/rca.py` | `_causes`, `_draft_rule`, `TEMPLATE`, `propose_rules`, call at the end of `analyse` |
| `src/agent_company/executors.py` | `OfflineExecutor._rule` |
| `src/agent_company/config.py`, `prefs.py` | `rule_proposal_min_goals` |
| `src/agent_company/projects.py` | `decide` (rule branch), `_to_rule`, `set_case` refusal, `settle_proposals` person-only |
| `src/agent_company/static/projects.js` | rule label and no case button |
| `tests/test_rule_proposals.py` | new |
| `docs/requirements/EVOLUTION-LOOPS.md` | EL-R-04 table row and `**Status:**` → done (`rca.propose_rules`, `projects._to_rule`) |
| `CLAUDE.md` | one clause in the `rca.py` entry |

## 10. Tests: `tests/test_rule_proposals.py`

**Setup:** an autouse `no_queue` fixture as in `tests/test_rca.py`. A helper does the following:

- `pid = projects.create("P")["id"]`.
- Create goals with `loop.create_goal(..., project_id=pid)` and set `Goal.status = "failed"` in a session.
- `fail(gid, layer="code", check="command false")` inserts `Lesson(agent_id=<any agent>, goal_id=gid, project_id=pid, text="rca", outcome="analysis", kind="operational", status="approved", report={"rca": True, "goals": [gid], "watch": [check], "confirmed": [{"cause": "x", "evidence": [f"check:{check}"], "layer": layer}]})`.

Tests assert on `case["cause"]`, `case["goals"]`, the `"Rule: "` title prefix and the `#id` citations in the description. They do not assert on the drafted wording.

- (a) Three failed goals, one cause → `propose_rules(pid)` returns one id. Exactly one pending `kind="rule"` proposal exists, with `case["goals"] == [g1, g2, g3]` and `"#g1, #g2, #g3"` in its description.
- (b) A fourth goal with the same cause → `propose_rules` returns `[]`, there is still one proposal, and its `case["goals"]` is unchanged. This also holds after rejecting it with `projects.decide(id, False, "tester")`.
- (c) Two goals → no proposal. Also: a `"task spec"` check, `layer=""`, a goal with status `approved`, and a goal of a `secret` project never count.
- (d) Three goals with `layer="prompt"`, or with another check, give a separate proposal. The original cause keeps its single proposal.
- (e) `projects.decide(id, True, "tester")` → status `approved`, no goal spawned (`spawned_goal_id is None`), an enabled catalog rule named `case["name"]`. `catalog.export_claude(tmp_path, [])` writes `.claude/rules/<name>.md` containing the rule line. Also: `decide(id, True, "GM", by_human=False)` raises `PermissionError`.
- (f) The end-to-end path: `rca.analyse` on the third goal, with the offline `_rca` stub as in `test_rca.py`, creates the proposal. A `propose_rules` that raises (monkeypatched) does not stop `analyse` from returning its report.

**Commands:** `uv run pytest -q tests/test_rule_proposals.py`, then `uv run pytest -q tests/test_rca.py tests/test_projects.py tests/test_eval_proposals.py`.

MESSAGE handoff to developer: run `propose_rules` after `analyse`'s session closes. Note that `rca.after_goal` runs before `settle_proposals` (loop.py:1988 inside `_run`, loop.py:1288 in the `finally`), so a rule proposal is settled for the goal that just ended, and it must go to the person-only list.
FINDING: `static/projects.js:253-254` treats every kind that is not `follow_up` as a project proposal, which would show a rule as "Approve as a project" with a "Documented case" button.
NEXT: implement §9 and run the two pytest commands in §10, reporting their summary lines.
