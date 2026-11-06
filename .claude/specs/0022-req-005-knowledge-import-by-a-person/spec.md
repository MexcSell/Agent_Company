# Specification: REQ-005: knowledge import by a person

## Write the specification (architect, done)

# REQ-005 spec: knowledge import by a person (iteration 2)

## Why iteration 1 failed
The spec was not the cause. The developer's sandbox could not open the worktree: `bwrap: Can't find source path /home/luca/Agent_Company/data/worktrees/goal-22: Permission denied`. So no file was written, including the `WORKLOG.md` that the developer contract checks for (`executors.py:588`, `strategies.py:356`).

I checked this round:
- The branch has only plan and decision commits since iteration 1, so the code anchors below still hold.
- The spec is already saved in `.claude/specs/0022-req-005-knowledge-import-by-a-person/spec.md`.
- I could not find the cause of the permission error. My session may not read file details above the worktree, so I couldn't check folder owners. That stays a blocker for whoever runs the sandbox.

The spec below is unchanged in substance. The one addition is step 0.

## 0. Order of work for the developer
1. **First**, write `WORKLOG.md` at the worktree root with the Write tool, not with Bash, so it exists even if a later command fails.
2. Implement §1–§8.
3. Run the tests in §9 serially (`uv run pytest -q tests/test_knowledge.py`, no `-n`) and record the summary line in `WORKLOG.md`.

## Summary
A person (admin only) imports outside text as knowledge items. The text can come from a Markdown or text file, a folder from an earlier workspace, a chat export, or a JSON file of experience reports. Each piece becomes a `Lesson` row with its source and date, is screened, and waits in the review queue as `draft`. Nothing is retrieved for tasks until a person approves it. Imported text is data only: it is never run, evaluated or rendered as HTML.

Data flow: input → `learning.import_knowledge()` → parser by format → chunks of at most 1000 characters → `screen()` → `Lesson(status=draft|quarantined|rejected, outcome="imported")` → review queue → `review(approve=True)` → `retrieve()`.

## 1. Data model (`db.py`, no new columns)
- **`outcome="imported"`**: a new value; it fits `String(16)`. Update the comment at `db.py:335`. It must not be `"research"`, because `retrieve()` (`learning.py:397`), `knowledge()` (`:718`) and `item()` (`:737`) all exclude `research`, so such an item would stay invisible even after approval.
- **`kind`**: `"semantic"` for files, folders and chats; `"episodic"` for experience reports.
- **`evidence_class`**: default `"INFERENCE"`, which the request can override.
- **`classification`**: default `"internal"`, checked with `check_level`.
- **`project_id`**: optional.
- **`agent_id`** (a non-null foreign key): the root General Manager, found as at `hypotheses.py:165`. If there is none, raise `ValueError("no general manager to file imported knowledge under")`.
- **`sources`** carries the provenance:
  - `user:<name>` (or `user:cli`)
  - `import:<format>`
  - `file:<relative path>`
  - `url:<…>` from a chat export's `Source:` line
  - `date:<YYYY-MM-DD>`, taken in this order: the request's `date`, then front-matter `date:`, then the file's modification time, then today.
- **`created_at`** is the time of the import. **`status_reason`** is `"imported by <name>, awaiting review"`, or the reason from `screen()`.

## 2. Screening and the pending state (`learning.py`)
- `screen(text)` (`:219`) runs on every chunk.
- Results are mapped like this: `approved` → **`draft`**; `quarantined` and `rejected` stay as they are. All three are in `REVIEW_QUEUE` (`:700`).
- At import, do not call `_link_contradictions` or `kgraph.on_knowledge`. `kgraph.on_lesson` runs as in `learn()`; it adds no edges for an item that isn't approved.
- **`retrieve()` needs no change.** It already filters `status == "approved"` (`:397`), as do `_link_contradictions` (`:101`), `evolution.py:67` and `agentdefs.py:218`.
- **`review()` addition:** when a `draft` is approved, call `_link_contradictions(s, i)` and run `judge_contradictions(edges)` after the session ends.
- **Playbook isolation:** add `"imported"` to the outcome exclusions in `evolution.py:65`, `agentdefs.py:218` (`Lesson.outcome != "imported"`) and the `known` count in `training.py:79`. Uploaded notes must not rewrite the GM's playbook or its skill record.
- Text from a `secret` project raises `ValueError` and is never stored (S14-R-05).

## 3. Parsers (pure functions in `learning.py`)
- **`_chunks_markdown(text)`**: one chunk per heading section, with the heading kept. Plain text is split into paragraphs. Short pieces are merged up to 1000 characters; longer ones are cut at a sentence or line boundary. Empty chunks are dropped.
- **`_chunks_chat(text)`**: the `data/research/chatgpt-shares/*.md` format:
  - `# <title>`, then `Source: <url>`, then turns.
  - Turns split **only** on exact lines `## User`, `## ChatGPT`, `## Assistant` or `## Claude`. Other `##` headings inside a reply stay in that turn.
  - Each assistant turn becomes one or more chunks, and the user question before it goes into `report.objective` through `clean_report()`.
  - Tags like `<box …>` and `<AsyncImage …/>` are removed with the regex `<[^>\n]{1,200}>`. Nothing is parsed as markup.
- **`_items_reports(text)`**: a JSON list, or `{"reports": [...]}`.
  - Each report goes through `clean_report()` (`:619`).
  - Its text is `reusable_lesson` or `successful_approach`; a report with neither is skipped.
  - `screen()` also runs on `_report_text(report)`, as in `learn()`.
- **Limits:** 2 MB per file; a folder takes at most 50 files ending in `.md`, `.markdown` or `.txt`, sorted, skipping dot-directories. At most 500 items per import; the rest are counted as `truncated`. Files are decoded as UTF-8 with `errors="replace"`.
- **Duplicates:** text that already exists exactly as an `imported` item in `draft` or `approved` is skipped (the same approach as `research.py:1623`).

## 4. Service function
```python
def import_knowledge(content: str | None, *, fmt: str, actor: str, name: str = "", path: str = "",
                     project_id: int | None = None, classification: str = "internal",
                     evidence_class: str = "INFERENCE", date: str = "") -> dict[str, Any]
# fmt: auto|text|markdown|chat|reports|folder
# -> {"created","draft","quarantined","rejected","skipped","truncated","ids"}
```
- **Paths:** `resolve()` the path; it must lie under `config.data_dir`. Refuse `.env`, `data/tls`, `data/git-keys` and `data/claude-accounts`, and refuse symlinks that point outside (resolve each file). Anything else raises `ValueError`. The rule follows `worktrees.ensure_sandboxed`.
- **`auto` detection:** a `.json` file → `reports`; a file with a `## User` or `## ChatGPT` line → `chat`; anything else → `markdown`.
- After the session ends, log `log_event("knowledge", …)`. Items that are not `draft` also get a security event with `rule="memory_poisoning"` (as at `:670`).

## 5. API (`api.py`, above `/api/knowledge/{item_id}` at `:2179`)
- **`POST /api/knowledge/import`**: multipart `file: UploadFile` (pattern at `:2876`), plus form fields `fmt`, `project_id`, `classification`, `evidence_class` and `date`. Uses `Depends(admin_user)`. Files over 2 MB get a 413.
- **`POST /api/knowledge/import/path`**: an `ImportPathBody` JSON body (`path`, `fmt`, plus the same optional fields). Admin only.
- Both call `_audit(admin, "knowledge_import", <name or path>, after=counts)`.
- `ValueError` → 400. A missing GM → 409.

## 6. CLI (`__main__.py:130`, `:282`)
- Add `"import"` to the `learn` choices: `learn import <path> [auto|text|markdown|chat|reports|folder]`. A directory path means `folder`, with actor `"cli"`, and the result prints as JSON.
- The path rules from §4 apply here too.

## 7. UI (`static/knowledge.js`)
- An admin-only **Import** button in the review-queue card (`:43`). It opens a modal with:
  - a file input (`.md,.markdown,.txt,.json`)
  - a format select
  - classification, project and date
  - a field for a server folder path, which posts to `/import/path`
- Afterwards, show a toast with the counts and call `viewKnowledge()`.
- Imported text is shown only through `esc()` or `kCard`. `kSource()` (`:11`) shows `import:`, `file:`, `date:` and `user:` references as plain labels.

## 8. Docs
- `CLAUDE.md`: one clause in the `learning.py` entry and one in the `learn` command line.
- `docs/threat-model.md`: a row for "admin-imported text → memory poisoning or path traversal" with these controls: `screen`, `draft`, human review, the `data_dir` path check, and the tests in §9.

## 9. Tests (`tests/test_knowledge.py`, fixtures written inline in `tmp_path`; never copy `data/research/*`)
1. A two-section Markdown file creates two items with `status == "draft"` and `outcome == "imported"`, with `user:`, `file:` and `date:` in `sources`. They appear in `knowledge(status="draft")`.
2. `retrieve(agent_id, query=<distinct word>)` does not return them. After `review(id, True, "user:x")`, it does.
3. A chunk holding a key shaped like `ghp_…` becomes `quarantined`; "ignore all previous instructions" becomes `rejected`. Neither is retrieved.
4. A chat fixture (`# T`, `Source: https://chatgpt.com/share/abc`, `## User` / `## ChatGPT`, and `## Lesson 1` inside the reply) keeps the reply in one turn. Its `sources` include `url:https://chatgpt.com/share/abc`, and the question is in `report.objective`.
5. A reports JSON with two reports, one with no lesson, creates one `episodic` item with a cleaned report.
6. A folder under `DATA_DIR/workspaces/x` imports its `.md`/`.txt` files and ignores `.py`. A path outside `DATA_DIR`, and a symlink pointing outside, raise `ValueError`.
7. Importing the same file twice gives `skipped == n` the second time.
8. The API: a non-admin is refused; for an admin, `/api/knowledge/import` returns counts and writes the audit row `knowledge_import`.
9. An approved imported item does not reach the GM's playbook (`evolution`) or its definition-file lessons (`agentdefs`).

BLOCKER: The developer's sandbox cannot open the worktree (`bwrap: Can't find source path /home/luca/Agent_Company/data/worktrees/goal-22: Permission denied`, also from `/tmp`). Until a person or the coordinator fixes worktree permissions or the bubblewrap bind, any build attempt fails the same way, whatever the spec says.
FINDING: The last three failures recorded as "WORKLOG.md does not exist" (K121, K136, K138) were sandbox failures, not spec defects. The gate puts the blame on the spec task, which hides the real cause.
FINDING: `review()` (`learning.py:742`) does not run `_link_contradictions` when a draft is approved; §2 adds this.
NEXT: Fix worktree access for the developer's sandbox, then run the build with step 0 first.
NEXT: QA checks the path-traversal refusal, escaping in `knowledge.js`, and the isolation in §2.
MESSAGE handoff to developer: the spec is in `.claude/specs/0022-req-005-knowledge-import-by-a-person/spec.md`; write `WORKLOG.md` with the Write tool before anything else, and stop with a BLOCKER line if the sandbox still refuses the worktree.
MESSAGE request to general-manager: someone needs to fix the bubblewrap "Permission denied" on `data/worktrees/goal-22` before iteration 2's build; another spec revision cannot fix it.

```json
{"insights": [{"title": "Sandbox errors are blamed on the spec task", "description": "When the developer's bwrap sandbox cannot open the worktree, no WORKLOG.md is written and the gate records the failure under 'Write the specification', so later spec attempts get blamed and repeated for no gain.", "evidence": ["blackboard engineering/build v1: bwrap Permission denied on goal-22", "K121, K136, K138: same WORKLOG.md failure on spec tasks", "executors.py:588 acceptance file_exists WORKLOG.md"], "expected_benefit": "Fewer wasted spec iterations; infrastructure faults reach a person sooner", "confidence": 0.7}]}
```

PROPOSAL: Classify sandbox start-up failures as infrastructure :: a bwrap "Can't find source path" should raise an incident and pause the goal instead of failing the spec and developer tasks over and over.
