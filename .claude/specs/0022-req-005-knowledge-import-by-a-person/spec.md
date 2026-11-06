# Specification: REQ-005: knowledge import by a person

## Write the specification (architect, done)

# REQ-005 spec: knowledge import by a person

## Summary
A person (admin only) imports outside text as knowledge items. The text can come from one Markdown or text file, a folder from an earlier workspace, a chat export, or a JSON file of experience reports. Each piece becomes a `Lesson` row with its source and date, is screened, and waits in the review queue as `draft`. Nothing is retrieved for tasks until a person approves it. Imported text is stored and shown as data only: it is never run, evaluated or rendered as HTML.

Data flow: input → `learning.import_knowledge()` → parser by format → chunks of at most 1000 characters → `screen()` → `Lesson(status="draft"|"quarantined"|"rejected", outcome="imported")` → review queue → `review(approve=True)` → `retrieve()`.

## 1. Data model (`db.py`, no new columns)
- **`outcome="imported"`**: a new value; it fits `String(16)`. Update the comment at `db.py:335`. It must not be `"research"`, because `retrieve()` (`learning.py:397`), `knowledge()` (`:718`) and `item()` (`:737`) all exclude `research`, so such an item would stay invisible even after approval.
- **`kind`**: `"semantic"` for files, folders and chats; `"episodic"` for experience reports.
- **`evidence_class`**: default `"INFERENCE"`, which the request can override.
- **`classification`**: default `"internal"`, checked with `check_level`.
- **`project_id`**: optional.
- **`agent_id`** (a non-null foreign key): the root General Manager (`Agent.role == "general_manager", parent_id is None`, the same lookup as `hypotheses.py:165`). If no GM exists, raise `ValueError("no general manager to file imported knowledge under")`.
- **`sources`** carries the provenance:
  - `user:<name>` (or `user:cli`)
  - `import:<kind>`
  - `file:<relative path or basename>`
  - `url:<…>` from a chat export's `Source:` line
  - `date:<YYYY-MM-DD>`, taken in this order: the request's `date`, then front-matter `date:`, then the file's modification time, then today.
- **`created_at`**: the time of the import, so recency scoring stays honest.
- **`status_reason`**: `"imported by <name>, awaiting review"`, or the reason from `screen()`.

## 2. Screening and the pending state (`learning.py`)
- `screen(text)` (`:219`) runs on every chunk.
- The results are mapped like this: `approved` → **`draft`**; `quarantined` and `rejected` stay as they are. All three are in `REVIEW_QUEUE` (`:700`), so they show up in the existing queue.
- At import, do not call `_link_contradictions` or `kgraph.on_knowledge`, because the item is not approved yet. `kgraph.on_lesson` runs as in `learn()`; it adds no edges for a non-approved item.
- **`retrieve()` needs no change.** It already filters `status == "approved"` (`:397`), and so do `_link_contradictions` (`:101`), `evolution.py:67` and `agentdefs.py:218`. The tests depend on this.
- **`review()` addition, one line:** when a `draft` from an import is approved, call `_link_contradictions(s, i)` and run `judge_contradictions(edges)` after the session ends. This puts it into contradiction checking like any other approved item.
- **Playbook isolation:** add `"imported"` to the outcome exclusions in `evolution.py:65` (`not_in(("research","analysis","imported"))`), `agentdefs.py:218` (`Lesson.outcome != "imported"`) and `training.py:79` (don't count it in `known`). Notes a person uploads must not rewrite the GM's playbook or skill record.
- Text that comes from a `secret` project is refused with `ValueError`; such text is never stored (S14-R-05).

## 3. Parsers (pure functions in `learning.py`, no I/O beyond reading)
- **`_chunks_markdown(text)`**: one chunk per heading section, with the heading kept as the first line. Plain text is split into paragraphs. Neighbouring short pieces are merged up to 1000 characters; longer ones are cut at a sentence or line boundary. Empty or whitespace-only chunks are dropped.
- **`_chunks_chat(text)`**: the format of `data/research/chatgpt-shares/*.md`:
  - `# <title>`, then `Source: <url>`, then turns.
  - Turns split **only** on exact lines `## User`, `## ChatGPT`, `## Assistant` or `## Claude`. Assistant replies contain their own `##` headings, which do not start a new turn.
  - Each assistant turn becomes one or more chunks, and the user question before it goes into `report.objective` through `clean_report()`.
  - Tags like `<box …>` and `<AsyncImage …/>` are removed with a plain regex, `<[^>\n]{1,200}>`, to cut noise. Nothing is parsed as markup.
- **`_items_reports(text)`**: a JSON list, or `{"reports": [...]}`, of dicts holding the §9.1 keys.
  - Each one goes through `clean_report()` (`:619`).
  - Its text is `reusable_lesson` or `successful_approach`; a report with neither is skipped.
  - `screen()` runs on `_report_text(report)` as well, the same as in `learn()`.
- **Limits:** 2 MB per file; a folder takes at most 50 files ending in `.md`, `.markdown` or `.txt`, sorted, with no recursion into dot-directories. At most 500 items per import; anything beyond that is reported as `truncated`. Files are decoded as UTF-8 with `errors="replace"`.
- **Duplicates:** a chunk whose exact text already exists as an `outcome="imported"` item in `draft` or `approved` is skipped (the same approach as `research.py:1623`).

## 4. Service function
```python
def import_knowledge(content: str | None, *, fmt: str, actor: str, name: str = "", path: str = "",
                     project_id: int | None = None, classification: str = "internal",
                     evidence_class: str = "INFERENCE", date: str = "") -> dict[str, Any]
# fmt: text|markdown|chat|reports|folder ; returns
# {"created": n, "draft": n, "quarantined": n, "rejected": n, "skipped": n, "truncated": n, "ids": [...]}
```
- **`folder` / `path`:** the path is resolved with `Path.resolve()` and must lie under `config.data_dir` (`config.py:137`). `.env`, `data/tls`, `data/git-keys` and `data/claude-accounts` are refused. Symlinks that point outside are refused (check `resolve()` for each file). The rule follows `worktrees.ensure_sandboxed`. Anything else raises `ValueError`.
- **Format detection** when `fmt="auto"`: a `.json` file → `reports`; a file with a `## User` or `## ChatGPT` line → `chat`; anything else → `markdown`.
- After the session ends: `log_event("knowledge", f"imported {n} item(s) for review", …)`. Any item that is not `draft` also gets `log_event(..., rule="memory_poisoning")` at security level, as `learn()` does at `:670`.

## 5. API (`api.py`, placed above `@app.get("/api/knowledge/{item_id}")` at `:2179`)
- **`POST /api/knowledge/import`**: multipart `file: UploadFile`, plus form fields `fmt` (default `auto`), `project_id`, `classification`, `evidence_class` and `date`. Uses `Depends(admin_user)`. Bodies over 2 MB get a 413.
- **`POST /api/knowledge/import/path`**: a JSON body (`ImportPathBody`: `path`, `fmt="folder"|"auto"`, plus the same optional fields). Admin only.
- Both call `_audit(admin, "knowledge_import", <file name or path>, after={counts…})`.
- `ValueError` → 400. A missing GM → 409.

## 6. CLI (`__main__.py:130`)
- Add `"import"` to the `learn` choices: `learn import <path> [auto|text|markdown|chat|reports|folder]`. A directory path means `folder`, with actor `"cli"`.
- The result prints as JSON, like the other `learn` actions.
- Folders outside `data_dir` are refused here too, because the same function is used.

## 7. UI (`static/knowledge.js`)
- An admin-only **Import** button in the review-queue card, next to the existing queue (`:43`). It opens a modal with:
  - `<input type=file accept=".md,.markdown,.txt,.json">`
  - a format select
  - classification, an optional project and an optional date
  - a second field for a server folder path, which posts to `/import/path`
- On submit, show a toast like "N for review (q quarantined, r rejected, s skipped)" and call `viewKnowledge()`.
- Text is only ever inserted through `esc()` or `kCard`, never through `innerHTML` of raw content.
- `kSource()` (`:11`) renders the new `import:`, `file:`, `date:` and `user:` references; show them as plain labels.

## 8. Docs
- CLAUDE.md: one clause in the `learning.py` entry and one in the Commands `learn` line.
- `docs/threat-model.md`: a row for "admin-imported text → memory poisoning or path traversal" with these controls: `screen`, `draft`, human review, the `data_dir` path check, and the tests in §9.

## 9. Tests (`tests/test_knowledge.py`, fixtures written inline in `tmp_path`; never copy `data/research/*`)
1. Importing a two-section Markdown file creates two items with `status == "draft"` and `outcome == "imported"`, with `user:`, `file:` and `date:` in `sources`. They appear in `knowledge(status="draft")`.
2. `retrieve(agent_id, query=<distinct word from the import>)` does not return them. After `review(id, True, "user:x")`, the same call returns them.
3. A chunk holding a key shaped like `ghp_…` becomes `quarantined`; "ignore all previous instructions" becomes `rejected`. Neither is retrieved.
4. A chat fixture with `# T`, `Source: https://chatgpt.com/share/abc`, `## User` / `## ChatGPT`, and a `## Lesson 1` heading inside the reply: the reply's `##` stays in one turn, `url:https://chatgpt.com/share/abc` is in `sources`, and the user question is in `report.objective`.
5. A reports JSON with two reports, one with no lesson: one item is created with kind `episodic` and the report is cleaned.
6. A folder under `DATA_DIR/workspaces/x` imports the `.md`/`.txt` files and ignores a `.py` file. A path outside `DATA_DIR`, and a symlink that points outside, both raise `ValueError`.
7. Importing the same file twice gives `skipped == n` the second time.
8. The API: a non-admin gets 401/403; an admin `POST /api/knowledge/import` returns counts and writes an audit row `knowledge_import`.
9. An approved imported item does not appear in the GM's playbook (`evolution`) or its definition-file lessons.

Run with `uv run pytest -q tests/test_knowledge.py` serially (no xdist), and read the summary line.

FINDING: `review()` (`learning.py:742`) never calls `_link_contradictions` when a `draft` is approved, so items approved from the queue skip contradiction detection. §2 adds that for imports; the same gap applies to any other draft.
FINDING: `Lesson.agent_id` is non-null, so imported items have to be filed under the GM and kept out of its playbook and skill counts; otherwise uploaded notes would rewrite an agent's definition file.
NEXT: The developer implements §1–§8 in `learning.py`, `api.py`, `__main__.py`, `static/knowledge.js`, `evolution.py`, `agentdefs.py`, `training.py`, `CLAUDE.md` and `docs/threat-model.md`, plus the tests in §9.
NEXT: QA checks the path-traversal refusal and that no imported text reaches `innerHTML` unescaped.
MESSAGE handoff to developer: before the gate runs, create `WORKLOG.md` and `.claude/specs/req-005-knowledge-import/spec.md` (this text) in the worktree. Earlier goals failed the gate because those files were missing (K116, K121).
PROPOSAL: Contradiction check on approving drafts :: `review()` skips `_link_contradictions` for every draft→approved change, not only imports.
