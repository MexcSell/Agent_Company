# Review (iteration 1)

## QA review of the draft-approval contradiction fix (qa, done)

## Logic verification

Changed function: `review()` in `src/agent_company/learning.py:768-795`. The only code change is lines 788-790. The old line was `if was == "draft" and i.outcome == "imported":`. The new lines are `g = s.get(Goal, i.goal_id) if i.goal_id else None` followed by `if was == "draft" and not (g and g.origin == "eval"):`.

**Root cause or symptom:** this fixes the root cause. The old guard sent only imported drafts to `_link_contradictions`. Every other draft that became approved joined the approved set without a contradiction check. The new guard checks every draft→approved change and leaves out only eval goals. That is the same exclusion `learn()` uses (`learning.py:674`, `is_eval = bool(g and g.origin == "eval")`, and `:694`).

**Normal case: a non-import draft contradicts an approved item.**
- `tests/test_knowledge.py` test 1 sets this up:
  - `learn(dev, g1, "Use connection pooling…", ["postgres"])` gives an approved item.
  - `_draft(dev, g2, "Never use connection pooling…", outcome="failure")` gives a draft.
- `review(draft, True)` then runs as follows:
  - No ValueError is raised at `:774-777`.
  - `was = "draft"` and the status becomes approved.
  - `g2.origin` is not "eval", so `_link_contradictions(s, i)` runs.
- Why `_contradicts` returns True (`:87-97`):
  - The goals differ (g1 and g2).
  - The technology `{postgres}` is shared.
  - Without the negation words, the token sets are the same, so the Jaccard overlap is ≥ 0.5.
  - Only the draft contains "Never".
- `_link_contradictions` then adds a `KgEdge(src_id=str(draft), rel="CONTRADICTS", state="open")`, logs the event and sends the notification. After `s.flush()` it returns `[e.id]`.
- `edges` keeps that list, and `judge_contradictions(edges)` at `:792` receives the new edge's id after the session closes.
- This case works.

**Edge cases:**
- **Approved again:** `was == "approved"`, so the guard is False. No `_link_contradictions` call and no duplicate edge (test 3, second assert).
- **Imported draft:** `goal_id=None`, so `g=None` and `not (None and …)` is True. It still links (test 2), and REQ-005 behaves as before.
- **Eval-goal draft:** `g.origin == "eval"`, so linking is skipped. The status still becomes approved (test 4). This matches `learn()`.

**Error cases:**
- **Quarantined or superseded:** `ValueError` is raised at `:774-777`, before `was` is read, before the status changes and before any linking. Existing tests cover both: `tests/test_knowledge.py:92` (match "quarantined") and `:225` (match "newer version").
- **Rejecting:** `approve=False`, so the whole `if approve:` block at `:785` is skipped and `edges` stays `[]`. No edge is created (test 3, last assert).

**Do the tests fail without the fix?** Under the old condition (`outcome == "imported"` required):
- **Test 1** (outcome "failure") would get no edge, so `[edge] = …` raises a ValueError on unpacking. It fails on the old code.
- **Test 3** (outcome "success") gets 0 edges at its first `len == 1` assert. It fails on the old code.
- **Test 2** (imported) and **test 4** (eval) would pass on the old code too. They guard against regressions; they don't prove the fix.

So two of the four new tests separate the old code from the new. That matches the developer's report that two tests failed before the fix.

**Other modules:** `git diff --name-only 5d1242d HEAD` lists four files:
- `src/agent_company/learning.py`
- `tests/test_knowledge.py`
- `.claude/specs/0050-contradiction-check-on-approving-drafts/requirements.md`
- `.claude/specs/0050-contradiction-check-on-approving-drafts/tasks.md`

The two `.claude/specs/` files are spec artifacts the loop writes, not modules. No other source module changed.

**Execution evidence:** every pytest call was refused in this session. I tried `uv run pytest -q tests/test_knowledge.py`, `uv run python -m pytest …` and `.venv/bin/python -m pytest …`. What I could check without running anything:
- `.pytest_cache/v/cache/nodeids` lists all four new node ids (lines 293, 294, 295 and 312).
- `.pytest_cache/v/cache/lastfailed` is `{}`.
- Timestamps:

  | File | Last changed |
  |---|---|
  | `tests/test_knowledge.py` | 04:20:50 |
  | `learning.py` | 04:21:00 |
  | `lastfailed` | 04:21:15 |
  | Commit 3d62813 | 04:21:52 |
  | `nodeids` | 04:22:22 |

  The tree is clean, so the files those runs used match HEAD.

Together this shows the new tests were collected and that no test is recorded as failing, against the committed code. It backs up the developer's reported "41 passed / 72 passed", but it is not a run of my own.

## Checklist
- **Contract met:** yes. Every draft→approved change except eval goals now runs `_link_contradictions`, and `judge_contradictions` gets the edge ids.
- **Interfaces compatible:** yes. The signature and return value of `review()` are unchanged.
- **Error handling:** unchanged and correct. The ValueErrors are raised before any change, and the judge keeps its fail-open behaviour (`:199-204`).
- **Edge cases:** re-approval, imports, eval goals and rejection are covered. Rejected→approved is still not checked; it is out of scope (see below).
- **Tests sufficient:** mostly. Two tests separate old from new code and two guard against regressions. Small gap: no test checks that the judge actually ran for the edge created by `review()`.
- **Dependencies:** none added.
- **Security:** no new input surface. Only an administrator calls `review()`, and the judge treats the items as data.
- **Performance:** one extra `s.get(Goal)` per approval, which is negligible. It also runs on re-approval, where `was != "draft"` would have made it unnecessary.
- **Docs:** no CLAUDE.md or threat-model change is needed. The inline comment explains the new rule.

FINDING: tests/test_knowledge.py:663-676 | The normal-case test never checks that `judge_contradictions` received the new edge, so dropping the call at `learning.py:792` for the review path would go unnoticed | Add `assert edge.evidence["judge"]["verdict"] == "unsure"`, re-reading the edge after `review()` (the default `contradiction_judge` is "advise", `config.py:129`), as `test_without_a_model_verdict_the_pair_waits_for_a_person` does.
FINDING: src/agent_company/learning.py:788 | The Goal lookup runs on every approval, including re-approvals and rejected→approved reviews where its result is never used (a small extra query, nothing breaks) | Look up the goal only when `was == "draft"`, e.g. `if was == "draft" and not _is_eval(s, i.goal_id):`.
FINDING: src/agent_company/learning.py:789 | An item that screening rejected and a person then approves (`was == "rejected"`) still joins the approved set without a contradiction check. The developer flagged this too; it is outside this task's draft→approved scope | Follow-up: run the check for every non-approved→approved change except re-approval, with its own test.
FINDING: tests/test_knowledge.py:651-658 | `_draft` writes `Lesson` rows straight to the database because `screen()` never returns "draft". The tests therefore exercise `review()` but not a real producer of non-import drafts, which looks like a test-only path | Add a short comment to the helper explaining why, or add a test once a real draft producer exists.

BLOCKER: Every test run was refused in this QA session (`uv run pytest -q tests/test_knowledge.py`, `uv run python -m pytest -q tests/test_knowledge.py`, `.venv/bin/python -m pytest -q tests/test_knowledge.py`). The execution evidence above comes from the worktree's `.pytest_cache`, not from a run of mine. The gate should run `uv run pytest -q tests/test_knowledge.py tests/test_hypotheses.py tests/test_research_review.py tests/test_requirements.py` itself.

NEXT: The gate runs both acceptance commands against commit 3d62813.
NEXT: Optionally add the judge assertion to the normal-case test (first FINDING).

MESSAGE fact to developer: The fix is already committed as 3d62813 on agents/goal-50 and the worktree is clean, so the "not committed" note in your handoff is out of date.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: Contradiction check on rejected→approved reviews :: a person who approves an item that screening rejected adds it to the approved set without a check, the same gap this goal closed for drafts.

```json
{"insights": [{"title": "QA can show test results from the pytest cache when it may not run tests", "description": "When pytest calls are refused, .pytest_cache/v/cache/nodeids (which tests were collected) and lastfailed (failures recorded), compared by timestamp with the changed files and the commit on a clean tree, show without running anything that the new tests ran and passed against HEAD.", "evidence": [".pytest_cache/v/cache/nodeids:293-312", ".pytest_cache/v/cache/lastfailed = {}"], "expected_benefit": "Fewer gate failures for missing execution evidence in QA sessions that may not run tests", "confidence": 0.6}]}
```
