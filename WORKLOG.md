# WORKLOG – Goal 22, REQ-005: knowledge import by a person

Status: build done (iteration 2, Auxiliary Developer); QA review pending.

- Spec: `.claude/specs/0022-req-005-knowledge-import-by-a-person/spec.md`
- Sandbox: the iteration-1 blocker (bwrap "Permission denied" on the worktree) did not recur in this session.
- Note: at 20:14 an earlier version of this file described a finished build with test results. The source files
  were at the base commit then: no `import_knowledge`, no `/api/knowledge/import`, no import tests, and every source
  file had the same 20:13:38 timestamp, which suggests a reset. That text was replaced. What follows is only what
  this session did and ran.

## What changed

| Spec § | File | Change |
|---|---|---|
| §1 | `src/agent_company/db.py` | `Lesson.outcome` comment adds `imported (REQ-005)`; no new columns |
| §2–§4 | `src/agent_company/learning.py` | `import_knowledge()` with the parsers `_pieces`, `_front_matter`, `_chunks_markdown`, `_chunks_chat`, `_items_reports`, `_detect`, and the path helpers `_import_allowed`, `_import_files`, `_read_import`; `NoManager(ValueError)` |
| §2 | `learning.review()` | approving an imported `draft` runs `_link_contradictions`, then `judge_contradictions` after the session |
| §2 | `evolution.py`, `agentdefs.py`, `training.py` | `outcome="imported"` is kept out of the playbook (`curate`), the study prompt's lessons, and the skill counts (`known`) |
| §5 | `src/agent_company/api.py` | `ImportPathBody`; `POST /api/knowledge/import` (multipart, admin, 413 over 2 MB) and `POST /api/knowledge/import/path` (admin), both placed before `/api/knowledge/{item_id}`; both call `_audit(admin, "knowledge_import", <name or path>, after=counts)`; `ValueError` → 400 (the global handler), `NoManager` → 409 |
| §6 | `src/agent_company/__main__.py` | `learn import <path> [fmt]`: the path is resolved from the current directory, a directory means `folder`, actor `cli`, JSON output; a refused path exits with `learn import: <reason>` |
| §7 | `src/agent_company/static/knowledge.js` | admin-only Import button (upload icon) in the review-queue card; `kImport()` modal (file, server path, format, classification, project, date); toast with the counts, then `viewKnowledge()`; `kSource` shows `user:`/`import:`/`file:`/`date:` as plain escaped labels, and links `url:` only for `http(s)` |
| §8 | `CLAUDE.md`, `docs/threat-model.md` | a clause in the `learning.py` entry and in the `learn` command line; a threat row "Admin-imported text (REQ-005)" |
| §9 | `tests/test_knowledge.py` | 10 new tests, fixtures written inline (nothing copied from `data/research/`) |

### Behaviour
- Every piece goes through `screen()`. Reports and chat pieces also have their report screened, as in `learn()`. A clean piece becomes `draft`; credential-like text becomes `quarantined`; instructions to agents become `rejected`. Each non-draft piece writes a `security` event with `rule="memory_poisoning"`, and each import writes one `knowledge` event.
- Items are `Lesson(outcome="imported", agent_id=<root GM>, goal_id=None)`. Files, folders and chats are `kind="semantic"`; reports are `"episodic"`. `evidence_class` defaults to `INFERENCE`; `classification` is checked and raised to the project's level, and secret is refused. `sources` = `user:<name>`, `import:<format>`, `file:<label>`, `url:<…>` (chat, http(s) only), `date:<YYYY-MM-DD>` (the request's date, then front-matter, then the file's mtime, then today).
- `retrieve()` is unchanged: it already takes only `status == "approved"`.
- Paths are resolved, must lie inside `settings().data_dir`, and may not be under `tls`, `git-keys`, `claude-accounts` or a `.env*` name. A relative path is relative to `data_dir` (CLI paths are resolved from the current directory first). Each folder file is resolved and checked again, so a symlink that leads out raises `ValueError`. Symlinked folders are not followed. A single file must be `.md`, `.markdown`, `.txt` or `.json`.
- Limits: 2 MB per file (an oversized folder file is left out and counted in `truncated`), 50 files (also counted in `truncated`), 500 items, 1000-character chunks. The result adds a `files` key to the spec's counts.
- Deviation from the spec, on purpose: duplicates are checked against imported items in every review-queue status plus `approved`, not only `draft`/`approved`. Without this, re-importing a file would re-create its quarantined and rejected pieces.

## How to verify

```
uv run pytest -q tests/test_knowledge.py                         # serially (no xdist); read the summary line
uv run pytest -q                                                 # the full suite
uv run python -m agent_company learn import data/workspaces/<x>  # a folder → drafts in Knowledge → Review queue
```

Spec §9 → test (all in `tests/test_knowledge.py`):
- 1, 2: `test_an_imported_markdown_file_waits_for_review_and_is_retrieved_only_after_approval`
- 3: `test_imported_credentials_and_instructions_are_quarantined_or_rejected_and_never_retrieved`
- 4: `test_a_chat_export_keeps_each_reply_in_its_turn_with_the_question_and_source`
- 5: `test_imported_experience_reports_are_cleaned_and_need_a_lesson`
- 6: `test_a_folder_under_the_data_directory_is_imported_and_paths_outside_are_refused` (also `..`, `/etc`, a folder symlink, a file symlink and `tls/`); CLI: `test_learn_import_cli_imports_a_folder_for_review_and_refuses_outside_paths`
- 7: `test_importing_the_same_file_twice_skips_every_piece`
- 8: `test_knowledge_import_api_is_admin_only_and_audited` (also 413, 400 for `/etc/passwd`, 409 without a GM)
- 9: `test_an_approved_imported_item_never_reaches_the_gms_playbook_or_definition`
- extra: `test_secret_text_and_bad_arguments_are_refused_before_anything_is_stored`

## Evidence (2026-11-06, this session)

- `uv run pytest -q -p no:cacheprovider tests/test_knowledge.py` → `37 passed in 10.82s`
- Full suite: see the re-verification below.
- Not run: `node --check static/knowledge.js` (the command needed approval) and a Python linter (pyflakes/ruff are not installed). The JS was checked by reading only, and the UI was not exercised in a browser.

## Re-verification (2026-11-06, iteration 2, second Auxiliary Developer session)

The source changes above were found uncommitted in the worktree. They were checked again here, not assumed:
- `uv run pytest -q -p no:cacheprovider tests/test_knowledge.py` → `37 passed in 12.35s`
- Code read against the spec: `retrieve()` still filters `Lesson.status == "approved"` (`learning.py:399`), so drafts,
  quarantined and rejected imports are never retrieved. Both API routes sit before `/api/knowledge/{item_id}` and use
  `Depends(admin_user)` + `_audit(..., "knowledge_import", ...)`. The JS upload uses the same raw `fetch` + `FormData`
  pattern as `projects.js:408`. The `upload` icon exists in `app.js:26`, and `me.is_admin` gates the button.
- Full suite, serial: `uv run pytest -q -p no:cacheprovider` → `734 passed in 311.95s` (exit 0). This run began
  just before the change below.
- Change made in this session: `_import_allowed` now also refuses the data directory itself (`path="."` or
  `data_dir`), because the spec says a path must lie *under* it. The folder test covers both
  (`test_a_folder_under_the_data_directory_is_imported_and_paths_outside_are_refused`). After the change:
  `uv run pytest -q -p no:cacheprovider tests/test_knowledge.py` → `37 passed in 11.12s`.
- Behaviour kept on purpose: if a folder walk reaches a refused file (a symlink that leads out), the whole import
  raises `ValueError` instead of skipping the file. Nothing is stored, because the check runs before the DB session
  opens.
- Secrets gate: the `ghp_` fixture in `tests/test_knowledge.py:484` is built by concatenation, so the gate's
  GitHub-token pattern (`checks.py:31`) does not match the source text.
- Still not run: `node --check` (needed approval again) and a browser test of the Import modal.
