# Review (iteration 2)

## Review the implementation (qa, done)

## Logic verification

I couldn't run pytest or `node --check` myself, because every Bash invocation of them needed approval. The developer says the full suite passed (`734 passed in 311.95s`) and `tests/test_knowledge.py` passed after their last change (`37 passed`). I read every test listed below against the code.

**`learning.import_knowledge` (learning.py:354-447)**
- **Normal case** (`TWO_SECTIONS` sent as `notes.md` with fmt `auto`):
  - `_detect` returns `markdown` because the file has no `## User` line.
  - `_front_matter` removes the front matter and reads the date `2024-03-01`.
  - `_chunks_markdown` makes two sections, because the `# Deploy notes` heading has no body and joins the next section.
  - `screen()` approves each piece, and the code turns that into `draft` (:430). The sources are user, format, file and date.
  - `retrieve()` builds its pool only from `status == "approved"` (:399), so a draft is never returned. `review(approve=True)` makes the item retrievable.
  - Test :466-480 asserts the item is not retrieved before review, is retrieved after, and that the second draft still is not.
  - This fixes the cause: the status is set when the item is created, and `retrieve()` is the single gate. It does not just hide the symptom.
- **Edge cases:**
  - In a chat export, a `## Lesson 1` heading inside a reply stays in that reply, because only exact `## User|ChatGPT|Assistant|Claude` lines start a turn. Tags are removed (test :499).
  - Importing the same file again skips every piece (`known` set, :416-421; test :557).
  - A report without a lesson is counted as `skipped` (test :509).
- **Error cases:**
  - A secret project or classification, an unknown format or a bad date raises `ValueError` before the session opens, or before `s.add` for the secret project. Test :563 asserts that no draft exists afterwards.
  - With no root GM, `NoManager` is raised and the API answers 409 (test :611).
- **Credentials and instructions:** a credential-like piece is `quarantined` and an instruction to agents is `rejected`. A security event is logged and nothing is retrieved (test :483).

**`_import_allowed` / `_import_files` (learning.py:306-351)**
- **Normal case:** a folder under `data/workspaces/x`. `os.walk` skips dot-folders, and only `.md`/`.txt` files are read, so `main.py` and `.git/HEAD.md` are left out (test :524-538).
- **Edge cases:**
  - These paths are all refused: a relative path inside `data_dir`, `..`, `/etc`, `.`, `data_dir` itself, `tls/ca.md`, and a folder symlink that leads out. The test covers all of them.
  - Each file is resolved again, so a file symlink that leads out is refused.
- **Error case:** a missing path or a wrong file suffix raises `ValueError`. The API's `ValueError` handler (api.py:846) turns that into a 400, and the test asserts 400 for `/etc/passwd`.

**API (api.py:2188-2213)**
- Both routes require `admin_user` and are audited as `knowledge_import`.
- The upload reads at most 2 MB + 1 byte and answers 413 above the limit.
- Test :589 covers 200, the audit row, 413, 400, a 401/403 for a non-admin on both routes, and 409.

**Other changes**
- **`review()` (learning.py:757-780):** contradiction linking now runs when an imported `draft` is approved. `judge_contradictions(edges)` is called after the session closes, as the session rule requires. An empty list is the normal path for every other item.
- **Exclusions:** the definition file (`agentdefs.study`), `evolution.curate` and `training.gaps` skill counts now skip `outcome="imported"`. Test :614 checks the playbook and the study prompt. I also grepped every other `Lesson.status == "approved"` site:
  - `kpis.py:92` counts only `kind == "research"`, and imports are `semantic`/`episodic`.
  - `mentoring.py:115` filters by material ids.
  - Both are unaffected.
- **CLI (`__main__.py:329`):** relative paths resolve against the current directory and are then checked against `data_dir`. A path outside it ends in `SystemExit` (test :573).
- **`knowledge.js`:** I read it but couldn't run it.
  - Every interpolated value goes through `esc()` or `Number()`.
  - A `url:` source becomes a link only for `http(s)`. Before, any scheme was linked, which is a small hardening of existing code.
  - The upload uses the same raw `fetch` + `FormData` pattern as `projects.js:408`.
  - `ib`, `me` and the `upload` icon all exist.

## Checklist
- **Contract:** met. Text/Markdown files, folders, chat exports and reports are all imported, each with a source and a date. Items are screened, wait in the review queue, and are not retrieved until approved. The API sits next to `/api/knowledge/*` with `_audit`, the CLI is `learn import`, and the button is in knowledge.js. The tests show that an import creates pending items and that `retrieve()` ignores them until approval.
- **Interfaces:** additive only. No schema change, since `outcome` is still String(16) and `"imported"` fits.
- **Error handling:** fine. `ValueError` → 400, `NoManager` → 409, oversize → 413, and all validation happens before any write.
- **Security:** imported text is never run. Paths are confined to `data_dir`, and key and sign-in folders are refused. Both routes are admin-only and audited. There is a threat-model row, and CLAUDE.md is updated.
- **Performance:** a minor concern, see the third finding.
- **Docs:** CLAUDE.md, `docs/threat-model.md` and `WORKLOG.md` are updated.

## Findings
FINDING: src/agent_company/learning.py:346 | In a folder import, one file symlink that leads out of `data/` makes the whole import fail with `ValueError`. Nothing is stored, so this is safe, but an admin importing an old workspace with one such link gets nothing and only a terse error. | Count such files as `left`/skipped and continue, or name the file in the error message. Keep refusing a top-level path that leads out.

FINDING: src/agent_company/learning.py:174 | Contradiction linking runs only when `was == "draft"`. If a person approves an imported item that was `rejected` (screening was wrong and they override it), the item is never checked for contradictions. | Use `was != "approved"` instead of `was == "draft"`.

FINDING: src/agent_company/learning.py:340-344 | `os.walk` lists the whole tree before the 50-file limit applies. Importing a large folder such as `workspaces` with `node_modules` walks every file. | Stop walking once more than `IMPORT_MAX_FILES` files are found, or skip `node_modules`, `__pycache__` and `.venv`.

FINDING: src/agent_company/static/knowledge.js:467-496 | The Import window has not been checked with `node --check` or tried in a browser, by the developer or by me. | Someone with approval runs `node --check src/agent_company/static/knowledge.js` and tries one upload and one path import in the browser.

NEXT: Run `node --check` on knowledge.js and do a browser smoke test of the Import window.
NEXT: Optionally fix the three small learning.py findings above; none of them blocks the merge.
MESSAGE recommendation to developer: in a folder import, skip a file symlink that leads out of `data/` instead of stopping the import, and use `was != "approved"` for imported items in `review()` (learning.py:174, :346).

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
