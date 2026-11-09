Both findings are closed and every test run passed, but the required evidence file is missing. I wasn't allowed to write `.claude/specs/0391-ui-redesign-p0-1-third-run-aico-design-s/evidence.md`: the Write tool and `cp` both asked for permission because the path is a "sensitive file". I didn't try to get around that. The full text is in `/tmp/goal391-evidence/evidence.md` and needs to be copied to the spec folder, otherwise the `file_exists` acceptance check fails.

**Finding (a), non-string input to `PUT /api/ui/nav_layout`: already closed, no code change.**
- `prefs.py` checks the type of every group key (:449–450), item and hidden entry (`_nav_ids` with `strict=True`, :411, :456, :458) and title (:454) before any lookup. A bad value gives 400 and the stored layout stays as it was. The old `top`/`bottom` values are only tested for presence, never read.
- Existing endpoint tests already cover keys, items and hidden entries (`test_malformed_values_are_400_and_change_nothing`, `test_non_string_entries_are_400_and_change_nothing`, `test_legacy_non_string_entries_are_skipped`).
- I added `test_non_string_title_falls_back_to_the_default` to `tests/test_nav_layout.py` (6 cases). A title like `[]` or `{}` gives 200 with the default title, never a 500.

**Finding (b), placing pages registered later: fixed in this task.** The sidebar and the editor used different rules:
- **The sidebar** (`navGroups`, also used by Settings → Navigation) put a missing page before the next default page present, or at the group's end.
- **The editor** (`placeNav`) puts it after the page it follows in the defaults, or first in the group.

After an admin reorders a group, the sidebar showed `general-manager` at the top of Company instead of after `dashboard`. Two new node tests in `tests/test_nav_place.py` failed on the old code (2 failed, 4 passed). `navGroups` (`app.js:254–266`) now calls `placeNav`, so both use the same rule. Settings → Navigation uses these same two functions, so it is fixed too.

One side effect: a page that sets its own group but is listed in the defaults now goes to its default group. No page sets a group today, so nothing visible changes.

**Test runs** (node installed, no skips; times from pytest's summary line):

| Run | Result |
|---|---|
| Unit acceptance command | 33 passed in 14.10s |
| Integration acceptance command (8 files) | 90 passed in 27.31s |
| Full `uv run pytest -q -rs` | 1456 passed in 712.13s (0:11:52) |

The full run started before I added the 6 title cases, so those ran only in the unit and integration runs. It had no failures, so there is nothing pre-existing to report. At almost 12 minutes it would have hit #337's old 600 s timeout.

**Files changed:** `src/agent_company/static/app.js`, `tests/test_nav_place.py`, `tests/test_nav_layout.py`. API contracts, permissions, `_audit` calls, the content-hashed static URLs and quiet live redraws are unchanged.

**Not done:** the UI-SHELL-06 screenshots at 1440/1024/768/390 px; AICO-UI.md keeps it partial.

FINDING: The developer session can't write under `.claude/specs/0391-…/` (permission prompt for a "sensitive file"), so `evidence.md` exists only at `/tmp/goal391-evidence/evidence.md` and the `file_exists` acceptance check will fail until it is copied.
FINDING: The sidebar (`navGroups`) and the editor (`placeNav`) placed new pages differently once a group was reordered; fixed by making `navGroups` call `placeNav`, with two new node tests.
NEXT: Copy `/tmp/goal391-evidence/evidence.md` to `.claude/specs/0391-ui-redesign-p0-1-third-run-aico-design-s/evidence.md`, then rerun the acceptance checks.
NEXT: QA and security review against the evidence file; the integration and full runs above are on this tree.

MESSAGE request to general-manager: Copy /tmp/goal391-evidence/evidence.md into .claude/specs/0391-ui-redesign-p0-1-third-run-aico-design-s/evidence.md; this session needs a permission it was not given to write that path.
MESSAGE fact to qa: Evidence is in /tmp/goal391-evidence/evidence.md (spec-folder copy pending); the changed lines are app.js:254-266 (navGroups now calls placeNav), :365-376 (placeNav) and :377-389 (readNav).
MESSAGE fact to qa: node ran with no skips, the integration set gave 90 passed in 27.31s, and the full suite gave 1456 passed in 712.13s.

```json
{"insights": [{"title": "Agent sessions cannot write .claude/ paths, so spec artifacts must be written by the loop", "description": "A developer task told to write .claude/specs/<goal>/evidence.md cannot: Write and cp both raise a 'sensitive file' permission prompt. Acceptance conditions that need a file there fail unless the loop writes it from the task output, or the file goes under an allowed path.", "evidence": ["Write to .claude/specs/0391-…/evidence.md: 'requested permissions to edit … which is a sensitive file'", "cp from /tmp to the same path: same refusal"], "expected_benefit": "Plans stop giving developers acceptance conditions they cannot meet", "confidence": 0.8}, {"title": "Test that the rendering and saving paths use the same placement rule", "description": "When the same ordering rule exists in a render path and a save path, test both with the same reordered input; testing only one hid that the sidebar and editor disagreed.", "evidence": ["test_nav_place.py tested only placeNav; navGroups put general-manager at index 0 after a reorder"], "expected_benefit": "Catches sidebar/editor ordering mismatches before review", "confidence": 0.7}]}
```

PROPOSAL: Let the loop place task-produced spec artifacts :: acceptance conditions on `.claude/specs/<goal>/` files can't be met by agent sessions that are refused that path
