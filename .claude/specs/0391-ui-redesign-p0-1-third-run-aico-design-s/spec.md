# Spec: P0-1 third run (goal #391): carry #337 iteration 2 onto main, then close what is still open

Sources: docs/requirements/AICO-UI.md §0–§1, docs/requirements/WEBSITE.md §3 (WEB-SHELL-04..16 must keep working), and branch `agents/goal-337` at commit `23add3685b` ("Goal 337 iteration 2", 94%). Merge base with main: `00c9a20946683c4b4b5ea40516922e5bc6efcb92`. Main is 35 commits past the merge base.

Labels: **[F]** is a fact I read from git or files in this session. **[I]** is an inference I have not run.

**Diff rule [F].** Read #337 only with three-dot diffs: `git diff main...agents/goal-337 -- <path>` or `git diff main...23add3685b -- <path>`. Read main's side with `git diff 00c9a20 main -- <path>`. The two-dot `git diff main agents/goal-337` shows main's newer tests as deletions (test_cache_free_runs, test_executor_env, test_plan_allowed_paths, test_simulator_switch, test_unittest_forms). Do not apply it.

---

## 1. Per-file carry-over table

`git diff --stat main...23add3685b` lists 23 files [F]. `git diff --stat 00c9a20 main` over the same paths lists 6 files: prefs.py, api.py, agents.js, models.js, WEBSITE.md and AICO-UI.md [F].

### 1a. Taken whole: only #337 changed these since the merge base [F]

To take one: `git checkout 23add3685b -- <path>`.

| File | What #337 delivered (paths under `src/agent_company/`) |
|---|---|
| `static/app.js` (+314/−) | Accent: `applyAccent` (:164). Mirror of the default groups: `NAV_DEFAULT` (:246) and `VIEW_GROUP` (:252). `navGroups` (:254–266) places pages that register later. Sidebar render, status card and user block. Sidebar Edit mode and `readNav` (:365–382). `saveNav`. Hash routes: `writeHash` (:496), `parseHash` (:500), `route` (:504), `hashchange` (:515). `window.setCrumbs` (:521). `initTopSearch` (:546). `openDrawer` / `closeDrawer` (:593). |
| `static/style.css` (+256/−) | update_5 tokens in `:root` (dark), `[data-theme="light"]` and `[data-accent]`. Old names (`--bg`, `--panel`, `--panel-2`, `--accent`, `--ok`, `--warn`, `--bad`, `--info`, `--muted`, `--border`, `--text`) are aliases. Component classes. Grouped sidebar. Collapsed rail, bottom bar and right-rail rules. Bottom-bar specificity fix at :672. |
| `static/workspace.css` (+67/−) | Uses the shared tokens and has no palette of its own. |
| `static/index.html` (+11/−) | Breadcrumbs, search box, `#drawer`. The scripts are unchanged and still get content hashes from `api._index_html`. |
| `static/layouts.js` (+22/−) | `labels:false` means collapsed, `rail:'bottom'` means bottom bar, `rail:'right'` mirrors. |
| `static/admin.js` (+65/−) | Settings → Navigation edits groups. Settings → Appearance → Components preview (`componentsPreview`). |
| `static/projects.js` (+11/−) | `#/projects/<id>` route, crumbs, and the fallback that rewrites an unknown id with `replaceState`. |
| `tests/test_nav_layout.py` (new, 133 lines) | Default and old-form read (:13), round trip with audit (:33), permissions and 400 (:52), limits (:59), malformed values give 400 and change nothing (:77–91), non-string entries are skipped (:94–103), defaults follow UI-D-02 (:106), the JS mirror equals prefs (:120), `degraded_agents` (:129). |
| `tests/test_shell_layout.py` (new) | Bottom bar with labels is one column (:32). An unknown project route rewrites the hash (:46). |
| `tests/test_ui_tokens.py` (new) | No hex colours outside the token blocks (:78), with a planted-colour self-test (:85). Workspace has no palette (:93). Old and new token names (:97). update_5 dark values (:103). The light theme has the same tokens (:112). Accent rules and picker (:118, :122). Component classes (:131). Drawer (:137). |
| `tests/test_index_scripts.py` (new) | Scripts complete and in order (:31). Every script and stylesheet is versioned (:48). |

**Trap [F]:** `NAV_DEFAULT.top` at app.js:246 has no runtime use. `tests/test_company_map.py:24` parses it with a regex (`const NAV_DEFAULT = \{\s*top:\s*\[`). Keep it, and keep it as the first key.

### 1b. Merged by hand: both branches changed these

To merge one: `git diff main...23add3685b -- <path> | git apply --3way`, then resolve and check against the notes below.

| File | Main's change (`git diff 00c9a20 main`) [F] | #337's change [F] | How to merge |
|---|---|---|---|
| `src/agent_company/prefs.py` | `command_timeout` description now says "0 = no limit…" (2 lines). `LIMITS["command_timeout"]` is `(0, 7200)`, was `(10, 7200)`. `max_clones` removed from `SCHEMA` and `LIMITS`. | `import re`. Nav block after `UI_KEYS` (23add36 prefs.py:388–end): `DEFAULT_NAV_GROUPS` (:393), `NAV_DEFAULT_GROUPS`, `NAV_TOPBAR`, `VIEW_GROUP`, `_NAV_ID`, `_nav_ids` (:408), `nav_default` (:422), `normalise_nav` (:427), new `get_ui` (:460) and `set_ui` (:474). | Keep main's SCHEMA and LIMITS. Add #337's import and nav block. **Do not copy #337's prefs.py whole**: that would bring back `max_clones` and the 10-second floor, undoing the owner's no-limit order of 08:04. [I] The hunks do not overlap. Main's side is −1 line, so #337's block sits about 1 line higher. |
| `src/agent_company/api.py` | `NotCleared`/`NoModel` → 409 handler right after the `ValueError` handler (~:905, +4 lines). Project GET wraps `router.resolve` in try/except (~:3305, +3). | `degraded` count and `"degraded_agents"` in `/api/overview` (23add36 :1040/:1048). `ui_get` returns `"default": prefs.nav_default()` for `nav_layout` (:3860). | Keep both. [I] They do not overlap. #337's lines move by about +4 and +7. Nothing else in api.py changes. QA confirmed these are the only two additions (#337 review.md "Checklist"). |
| `src/agent_company/static/agents.js` | Clone merge: `clones`/`manage` vars, the `a-merge-all` button, `mergeClonesModal` (~:29–90, +27 lines). | In `agentModal`: `writeHash("#/agents/"+id)`, a stale-hash guard, `setCrumbs([...pageCrumbs, {label: a.name}])` (~:171–190). | Keep both. #337's lines move by about +27. [F] `writeHash`, `setCrumbs` and `pageCrumbs` come from the carried app.js, so agents.js works only together with it. |
| `docs/requirements/WEBSITE.md` | `max_clones` removed from the settings list (~:478). | §3 rewritten: WEB-SHELL-01..03 replaced, WEB-SHELL-13 reworded, WEB-SHELL-17 added. | Keep both. Then **fix the wording at WEB-SHELL-02** (23add36 WEBSITE.md:59, see §3b). |
| `docs/requirements/AICO-UI.md` | UI-D-03 reworded (:48–50): text colours from the update_4 PDF p20; border `#24324A` and info `#0EA5E9` marked "GM-chosen". | :44 "1100 px" → "1280 px". UI-SHELL-03 "maintenance" → "stopped". Status blocks after UI-D-08 and after UI-SHELL-07. | [I] **Likely real conflict**: the :44 hunk and main's :48–50 hunk are within 3 lines. Keep main's UI-D-03 text, take 1280 and both status blocks. **Fix the UI-D-05 status line** ("old `top/bottom` read as the default groups", see §3b). |

### 1c. Not in play / excluded

- `static/models.js` [F]: only main changed it. Keep main's version.
- `.claude/specs/0337-*/` [F]: spec, shell-design, review, tasks, decisions, requirements. Excluded and must not be changed (a change fails the task). Read them only.
- `docs/adr/0128-owner-of-the-default-navigation-groups.md` [F]: excluded. This goal issues a new ADR through its own decision record. That ADR must say the old-form migration removes hidden ids from their groups, which 0128 does not (#337 review.md).

---

## 2. Behaviour contract (short)

- **Tokens (UI-D-03, UI-SHELL-01).** Dark is the default:
  - bg `#0B1220`, sidebar `#111C2D`, panel `#142033`, elevated `#1E293B`, border `#24324A`
  - text `#F9FAFB`, muted `#9CA3AF`
  - primary `#2563EB`, success `#16A34A`, warning `#D97706`, error `#DC2626`, info `#0EA5E9`

  The light theme uses the same semantic names. Spacing is on a 4px unit. Cards have a 12px radius and controls 8px. Type: body 14px, title 24px, section 16–18px, metadata 12px, Inter/system-ui. The old names are aliases. workspace.css has no palette of its own. The accent is one of indigo, violet, blue, green, amber, rose or grey, stored per browser in `localStorage` `ac.accent` and applied as `--accent`. Theme is light, dark or system.
- **Components (UI-SHELL-02).** `.page-head`, `.kpi`, `.card` + `.card-head` with "View all", `.badge` ok/warn/bad/info/neutral, `.progress`, `.stepper`, `.timeline`, `.drawer` (right side, close button and Escape), `.empty`, `.tabs`. Settings → Appearance → Components renders each one once.
- **Grouped sidebar (UI-D-02, UI-SHELL-03).**
  - Default groups, which are the same in prefs.py `DEFAULT_NAV_GROUPS` and app.js `NAV_DEFAULT.groups`:
    - Company: dashboard, general-manager, projects, goals, teams, agents, companymap, workflows, deliverables
    - Intelligence: research, knowledge, performance, evaluation, models
    - Evolution: evolution, organization, changes
    - System: workspace, machines, security, system
  - Ids without a registered view are not drawn. When such a page registers later, it shows at its default position. An unknown id goes to the end of its group (`v.group`, else `system`).
  - Brand block: "Agent Company · Autonomous Engineering Organization". Badges are kept.
  - Status card: operating, degraded, paused or stopped, from `/api/overview` (`degraded_agents`, `stopped`) and the stop state. User block below it.
  - Collapse is remembered per browser. The sidebar is collapsed by default below 1280px and becomes a bottom bar below 700px.
  - Notifications, Audit, Settings and Users are never in the sidebar (`NAV_TOPBAR`).
- **nav_layout groups form (UI-D-05).**
  - Shape: `GET`/`PUT /api/ui/nav_layout` use `{groups:[{key,title,items:[ids]}], hidden:[ids]}`. GET also returns `default`.
  - Group rules: keys must be one of the four defaults, with no repeats. A group that is not sent is added with no items. Titles are reduced to printable characters, at most 40, falling back to the default title.
  - Id rules: ids must match `[a-z][a-z0-9-]{0,31}`. Top-bar ids and repeats are skipped. At most 40 ids per list.
  - Errors: a malformed value raises `ValueError`, which becomes a 400 (api.py ValueError handler). Nothing is stored or audited.
  - Access: PUT is admin only and audited as `ui_layout`, as before. A stored value that cannot be read falls back to the default on GET.
- **Old-form migration.** A saved `{top,bottom,hidden}` value becomes the **default groups with each hidden id removed from its group, plus the hidden list**. The old `top`/`bottom` order is discarded. The result reads back unchanged (23add36 prefs.py:436–442).
- **Top bar (UI-SHELL-04).**
  - Breadcrumbs through `window.setCrumbs([...])`.
  - Search box "Search projects, goals, agents… (Ctrl+K)": Ctrl/⌘+K, arrow keys, Enter. It searches the sidebar pages and open projects client-side (`/api/projects`, cached for 60 seconds). Server search comes in P1-6.
  - Kept: whoami, Simple/Advanced, stop pill, Notifications/Audit/Settings/Users, estop, My Git, Screen format, theme.
- **Hash routes (UI-SHELL-07).**
  - Routes: `#/<view>`, `#/projects/<id>`, `#/goals/<id>`, `#/agents/<id>`.
  - Writing: `show()` writes the hash with `pushState`, so there is no second render.
  - Back/forward work, and `ac.view` is the fallback when there is no hash.
  - Closing a goal or agent window replaces the hash with the page's hash. An unknown project id is rewritten with `replaceState`.

---

## 3. Status of the review findings

**(a) Non-string group key or item gave 500 instead of 4xx: FIXED in iteration 2 [F].**
- The cause is fixed: `isinstance(key, str)` runs before `key not in defaults` / `key in keys` (23add36 prefs.py:449).
- `_nav_ids` (prefs.py:408–420) checks `isinstance(items, list)` and `isinstance(item, str)` before the regex and set lookups.
- Endpoint regressions in tests/test_nav_layout.py:
  - :77–91 PUTs 19 bad values: keys `[]`, `{}`, `1`, `None`, `['company']`, `{'company':1}`, non-list items, hidden or groups. Each must give 400, and the stored value must be unchanged.
  - :94–103 checks that non-string entries are skipped and that a migrated old value reads back unchanged.
- [F] Verified by #337 QA (review.md, "Logic verification (1)").
- To do here: re-run, no rebuild.

**(b) Future pages need their UI-D-02 positions, not their group's end: FIXED in iteration 2 [F].**
- prefs.py:393 `DEFAULT_NAV_GROUPS` and app.js:246–252 `NAV_DEFAULT` / `VIEW_GROUP` list them in position. `test_nav_layout.py:120` (`test_app_js_mirror_equals_prefs`) keeps the two in step, and `:106` checks the UI-D-02 order.
- `navGroups` (app.js:254–266) inserts a page missing from the saved layout before the next default item present in its group.
- `readNav` (app.js:365–382) puts a page with no view back after the item it followed when an admin saves.

**Still open:**
1. **No automated test of `readNav` placement** (app.js:373–379) [F] (review.md, second FINDING). Fix as follows:
   - Move the placement loop into a top-level pure function in app.js, e.g. `placeUnseen(baseGroups, editedGroups, present, isVisible)`, and have `readNav` call it.
   - Test it through node the way `tests/test_company_map.py:88–104` does: `node -e` with `skipif(shutil.which("node") is None)`.
   - Cases:
     - one missing id goes in after its predecessor (general-manager after dashboard)
     - two missing ids in a row keep their order (general-manager, then teams)
     - no earlier item present → index 0
     - a group missing from the edit → nothing is added
     - a visible or present id is never inserted again
   - [I] If node is absent where the gate runs, this test and `test_static_js` skip, and the integration check passes with less coverage. QA should report whether they skipped.
2. **Docs wording of the old-form migration** [F]. The text says old layouts become "default groups with their hidden list" without saying that hidden ids leave their groups. Fix it in:
   - `docs/requirements/WEBSITE.md` WEB-SHELL-02 (23add36 :59)
   - the AICO-UI.md UI-D-05 status line ("old `top/bottom` read as the default groups")

   New wording: "default groups with each hidden id removed from its group, plus the hidden list". The same mismatch is in `0337-*/spec.md:424, :579` and `shell-design.md:420`. Those files are excluded; leave them as they are.
3. **Integration-stage check**: see §4.

**Known gap, not in scope [F]:** UI-SHELL-06's headless screenshots at 1440/1024/768/390 px were never run. The bottom-bar fix was checked only by reading the CSS cascade (review.md, first FINDING). Keep AICO-UI.md "UI-SHELL-06 **partial**".

---

## 4. Integration test list

[F] Outside #337's four new tests, `tests/test_company_map.py` and `tests/test_company_config.py` are the tests that read app.js and index.html. `test_static_js.py` runs `node --check` on every static/*.js. [I] `tests/test_agent_runs.py` exercises the agents API next to the hand-merged api.py and agents.js. Add it if the run stays short.

The hardening developer task gets this acceptance condition:

```json
{"type": "command", "run": "uv run pytest -q tests/test_nav_layout.py tests/test_shell_layout.py tests/test_ui_tokens.py tests/test_index_scripts.py tests/test_static_js.py tests/test_company_map.py tests/test_company_config.py tests/test_agent_runs.py", "stage": "integration"}
```

Add the new readNav node test to this list if it goes in a separate file.

**Order:** carry (1a whole files, 1b hand merges) → hardening (the `placeUnseen` test, the docs wording, the integration check) → QA, which re-runs the list above plus `uv run pytest -q`. The command timeout is now 0, meaning no limit [F, main prefs.py].

---

FINDING: I could not confirm that the hand-merges are free of conflicts: `git merge-tree` needed approval. The notes in 1b on non-overlapping hunks are [I], and an AICO-UI.md conflict near lines 44–50 is likely.
NEXT: Carry 1a whole and 1b by hand, keeping main's `command_timeout (0, 7200)` and the removal of `max_clones` in prefs.py.
NEXT: Extract `placeUnseen` from `readNav`, add the node test, fix the migration wording in WEBSITE.md and AICO-UI.md, and run the integration command.
MESSAGE handoff to developer: Take prefs.py's nav block from 23add3685b (lines 388 to the end) on top of main's prefs.py; copying the file whole would bring back `max_clones` and the 10 s timeout floor.
MESSAGE fact to qa: Findings (a) and (b) were fixed in #337 iteration 2 (prefs.py:449, test_nav_layout.py:77–103, app.js:246–266/365–382); check the readNav node test did not skip and that 0337-* is untouched.

```json
{"insights": [{"title": "Carry a failed goal's best iteration with three-dot diffs and a per-file merge table", "description": "When a goal restarts from an earlier branch, split the files into ones only that branch changed (checkout whole) and ones both changed (hand-merge, recording main's change); a whole-file copy of a shared file silently reverts main's newer settings.", "evidence": ["prefs.py: whole copy of 23add3685b would restore max_clones and command_timeout floor 10", "git diff main agents/goal-337 shows main's newer tests as deletions"], "expected_benefit": "No silent reverts of owner orders when carrying work over", "confidence": 0.75}]}
```
