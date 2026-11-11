# Developer — the administrators' development page

**Developer** is the dashboard page where an administrator works on code by hand: a file explorer, an editor
and viewer, terminals on the server, and a Claude Code (or Codex) conversation, side by side like an editor.
It was called **Workspace** until 2026-11-11; only the label changed (see [Names in the code](#names-in-the-code)).

This document is the guide to the page: what it is for, how to use it and what it is allowed to do. The full
rebuild specification — every requirement, the API contract, limits, polling intervals, vendored files and their
checksums — is [requirements/WORKSPACE.md](requirements/WORKSPACE.md) (IDs `WS-R-nn`).

## Who can use it

- **Administrators only.** Every route behind the page needs an admin session (403 otherwise); its WebSockets
  check the session cookie, the scheme and the `Origin` (WS-R-02).
- It is the **first page** an administrator sees after signing in (when no other page was open last time).
  On the rail it sits in the **Observability** group, after Machines; Settings → Navigation can move it.

## What it is — and what it is not

Developer is the administrator's **own tool**, not a goal-loop agent (owner's decision, CLAUDE.md "Conventions"):

| | Developer page | Goal-loop agents |
| --- | --- | --- |
| Runs as | the server user, host-wide | executors in `data/worktrees/` |
| Commands | no command filter | `governance.check_command`, the L3 sandbox |
| Claude Code tools | every tool allowed in the opened folder | permissions and autonomy G0–G4 |
| Audit | every session, prompt and file change | every run and tool call |

Consequences:

- An **emergency stop** (Security, `stop`) halts the agents but **not** this page — the administrator can still
  look and repair while agents are stopped.
- Do not route the page through the sandbox, and do not loosen `ClaudeCodeExecutor` / `CLAUDE_BASH` because of it.
- Everything done here is audited: `terminal_open`, `workspace_claude`, `file_write`, `file_delete`, `folder_open`,
  … (Audit log, top bar).

## Layout

```
┌──────────┬──────────────────────────────────────┬──────────────────┐
│ Explorer │ Editor / viewer tabs                 │ Claude Code      │
│          │──────────────────────────────────────│ conversations    │
│          │ Terminal tabs                        │                  │
└──────────┴──────────────────────────────────────┴──────────────────┘
```

Drag the gutters to resize; each window can be **minimised** (a slim strip, click to restore) or **maximised**
(fills the page). Sizes and states are kept in this browser. On a narrow screen the windows stack and the page
scrolls.

The page is built once: switching to another page and back keeps terminals, open files and conversations.

## Explorer

- **Folders:** every project's folder, `data/`, and folders opened with **＋ Open folder…** (kept for all
  administrators until closed with ✕; closing never changes anything on disk).
- **Files:** click opens; right-click for new file/folder, upload, rename, duplicate, cut/copy/paste, compress,
  extract, download, *Open in terminal*, *Run in terminal* (Python, shell, Node, …) and *Ask Claude about this*.
- **Upload:** drag files or whole folders from your computer onto the tree (≤ 50 MB per file).
- **Git:** changed files are marked `M` / `A` / `U` / `D` / `R`; folders with changes get a dot. `.git` is never listed
  and is read-only.
- **+** opens another explorer window on another folder (even `/`), stacked or side by side.

## Editor and viewer

Code with line numbers and syntax colouring, Markdown preview, CSV tables, formatted JSON, images, PDF, Word
documents, audio and video. Every text file can be edited (*Edit*, **Ctrl+S** saves). Find in file with **Ctrl+F**.
Open tabs come back after a reload; files changed on disk (for example by a Claude run) reload unless you have
unsaved edits.

## Terminal

Real interactive shells on the server (`bash`, coloured prompt with the git branch), up to 12 at a time.
A shell keeps running when the browser closes or the connection drops: the tab reconnects from the last byte it
has, and a shell lost to a server restart is replaced in place on the next key. **Ctrl+`** shows and focuses
the terminal. Drop explorer items onto it to type their paths.

This is also where CLI sign-ins happen (`claude`, `codex login`, `copilot login`), as the Models page says.

## Claude Code panel

- **Conversations as tabs**, one per conversation of this browser session (new ones on the far right). Each is
  titled by Haiku after its first prompt. Earlier conversations and other Claude Code sessions running on the host
  are in the **Sessions** window (clock button, `/history`, `/resume`): open, continue, stop or delete them.
- **Agent, model and effort** per prompt: Claude Code or Codex; the CLI's model aliases plus the Models page's
  `claude_code`/`codex` models, each with its own sign-in and base URL. A conversation follows the chosen model's
  sign-in and back.
- **Composer:** Enter sends, Shift+Enter new line, Esc stops, ↑/↓ earlier prompts. Paste or drop images and files to
  attach them; drop explorer items to insert `@path`.
- **Display:** each prompt is a one-line bar pinned to the top of the log; tool calls and thinking fold into one
  "N steps" line; what the agent says and the result always show.
- **`/status`**, **`/tasks`**, **`/agents`** open a drawer: account, plan limits and usage of the chosen model's
  sign-in, background tasks and todos, subagents and custom agents.
- Claude Code runs in the folder open in the explorer with every tool allowed (`acceptEdits`).

The same panel appears in each project's General Manager view (Projects page), working in that project's folder
and defaulting to the GM's model.

## Keyboard shortcuts

| Where | Keys |
| --- | --- |
| Anywhere on the page | Ctrl+` terminal · Ctrl+L Claude · Esc closes a menu |
| Explorer | ↑ ↓ ← → · Enter · F2 rename · Delete · Ctrl+C / X / V · Ctrl+F filter |
| Viewer / editor | Ctrl+F find, Enter / Shift+Enter next / previous · Ctrl+S save |
| Terminal | Ctrl+C copies a selection (Ctrl+Shift+C always) · Ctrl+V paste |
| Claude composer | Enter send · Shift+Enter new line · Esc stop · ↑/↓ history |

## What is kept where

| What | Where |
| --- | --- |
| Opened folders | DB setting `files.extra_roots` (all administrators) |
| Layout, open tabs, conversations, last model/effort | this browser's `localStorage` (`ac.wsp.*`) |
| Attachments | `data/uploads/` |
| Claude Code transcripts and sign-in | the service user's `~/.claude` (or the model's own sign-in folder) |
| Running shells and Claude runs | server memory; a Claude run continues across a restart and is followed from its transcript |

## Names in the code

The rename is a **label change only**. Every identifier keeps the old name, so saved rail layouts, bookmarks
(`#/workspace`), browser state and the API keep working:

| Item | Name (unchanged) |
| --- | --- |
| View id, rail item, URL | `workspace`, `#/workspace` (`prefs.DEFAULT_NAV_GROUPS`, `ac.view`) |
| Front end | `static/workspace.js` (layout, explorer, tabs), `viewer.js`, `console.js`, `claude.js`, `workspace.css`; `window.WSP` |
| Browser storage | `ac.wsp.*` |
| Server | `workspace.py` (Claude Code panel), `terminal.py`, `files.py`; `api.py` sections `# --- workspace: …` |
| API | `/api/workspace/*`, `/api/terminal/*`, `/api/files/*` |
| Audit actions | `workspace_claude`, `workspace_claude_stop`, … |
| Tests | `tests/test_workspace.py`, `tests/test_files.py` |
| Specification | [requirements/WORKSPACE.md](requirements/WORKSPACE.md) |

The visible label lives in `registerView({ id: "workspace", title: "Developer", … })` at the end of
`static/workspace.js`, and in the welcome card shown when no file is open.

**Why ids were kept:** the rail layout saved in the DB (`/api/ui/nav_layout`) stores view ids; renaming the id
would drop the page from every saved layout, and the API paths and audit action names are referenced by tests,
the audit history and the specification.

## Checking it

```
uv run pytest -q tests/test_workspace.py tests/test_files.py
```

Then by hand, as an administrator: the rail shows **Developer**; with no file open the welcome card reads
"Developer"; a terminal prints a prompt; a Claude prompt answers and its tab gets a title. The full manual
checklist is [requirements/WORKSPACE.md §15](requirements/WORKSPACE.md#15-verification-on-the-new-computer).
