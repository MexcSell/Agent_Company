# WORKSPACE — the administrators' Workspace (rebuild specification)

Source: the code of 2026-11-05 (HEAD `ed1d51d` plus the working tree; the checksums in
[§14](#14-files-and-vendored-assets) pin the exact files). Not derived from a document in
`/home/luca/update/`. Purpose: everything needed to set up **the same Workspace on another computer**
— either by copying this repository and following [§2](#2-reproduce-on-another-computer), or by
rebuilding it from the requirements below.

**What "the Workspace" is.** The dashboard's home page (`registerView({id: "workspace"}, …, 0)`; the
default view when `ac.view` is unset), admin-only, laid out like an editor:

```
┌─rail─┬───────────── topbar (48 px) ────────────────────────────────────────────┐
│      │ Explorer │ Editor / viewer tabs                       │ Claude Code panel │
│ 56px │ (260 px) │────────────────────────────────────────────│ (400 px)          │
│      │          │ Terminal tabs (260 px)                     │                   │
└──────┴──────────┴────────────────────────────────────────────┴───────────────────┘
```

The **File Manager** view (`id: "files"`, `static/files.js`) is the *same DOM* with the Claude Code
pane hidden (`WSP.viewFileManager` → `viewWorkspace(q, true)` → class `no-claude`).

**IDs and status** follow [README.md](README.md): `WS-R-nn` is a requirement written here; status
`done (symbol)` means the current code meets it. Every requirement ends with an acceptance line; where
a test already checks it, the test is named (`tests/test_workspace.py` unless stated).

## 1. Design constraints (do not "fix" these)

| ID | Constraint | Status |
| --- | --- | --- |
| WS-R-01 | **Owner decision (CLAUDE.md):** the Workspace terminal and Claude Code panel are the administrator's own tools, not goal-loop agents. They run host-wide **as the server user**, with **no command filter**, Claude Code with **every tool allowed** in the opened folder. They are **admin-only** and **every session/prompt is audited**. They are never routed through `worktrees.ensure_sandboxed` / `governance.check_command`, and `ClaudeCodeExecutor` / `CLAUDE_BASH` are not loosened because of them. A rebuild that sandboxes them is a different product. | done (`terminal.py`, `workspace.py`) |
| WS-R-02 | Every Workspace HTTP route depends on `admin_user` (403 `administrators only` otherwise). WebSockets authenticate in `ws_admin`: the sealed session cookie must belong to an admin, the scheme must match `settings().transport` when `X-Forwarded-Proto` is set, and the `Origin` host must equal `Host` or one of `X-Forwarded-Host` — browsers do not apply CORS to WebSockets. | done (`api.ws_admin`) |
| WS-R-03 | The DOM is built **once** and re-attached when the view is shown again, so terminals, open files and conversations survive switching views. `workspace`, `files`, `machines` are in `SELF_UPDATING`: live events never re-render them and `show()` never snapshots them. | done (`viewWorkspace`, `app.js show()`) |

Acceptance: `test_terminal_is_admin_only_and_same_origin`; `tests/test_files.py::test_file_api_is_admin_only_and_audited`.

## 2. Reproduce on another computer

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-04 | **Host:** Linux with `/proc` (session liveness reads `/proc/<pid>/stat`; macOS falls back to `kill(pid, 0)`), `bash`, `git`, Python 3.12–3.13, [`uv`](https://docs.astral.sh/uv/), Node.js + npm, nginx (for other computers). | done |
| WS-R-05 | **Python deps** from `pyproject.toml` / `uv.lock` (`uv sync`): fastapi ≥0.115, uvicorn ≥0.30, sqlalchemy ≥2.0, python-multipart ≥0.0.9, cryptography ≥50.0.2, httpx, jsonschema, python-dotenv, crewai[anthropic]. No front-end build step: plain JS, served as is. | done |
| WS-R-06 | **Agent CLIs**, installed for **the user the service runs as** (systemd unit: `User=root`, `HOME=/root`): `npm i -g @anthropic-ai/claude-code`, then run `claude` once in the Workspace terminal to sign in. Optional: `npm i -g @openai/codex` + `codex login`; `npm i -g @github/copilot` (listed only; it cannot run in the panel yet). The unit's `PATH` must contain `~/.local/bin` (`/root/.local/bin`) — a service gets no login shell. | done (`deploy/agent-company.service`) |
| WS-R-07 | **Service:** copy `deploy/agent-company.service` to `/etc/systemd/system/`, change `WorkingDirectory`, `PATH` (the checkout's `.venv/bin`) and log paths if the checkout is not `/home/luca/Agent_Company`; `systemctl daemon-reload && systemctl enable --now agent-company`. `ExecStart` is the venv's python (`python -m agent_company supervise`), not `uv run`. `KillMode=mixed`, `Restart=always`, `RestartSec=3`. | done |
| WS-R-07a | **`.env`** (copy `.env.example`): `PORT` and `PUBLIC_PORT` both default to 4900 in `config.py`, so behind nginx **`PORT` must differ**. This host's server keys: `HOST=127.0.0.1`, `PORT=4910`, `PUBLIC_PORT=4900`, `MANAGE_NGINX=1`, `TRANSPORT=https` (or `http`), optional `TLS_HOSTS` (extra certificate names), optional `DATA_DIR` (default `./data`). The Workspace needs no model key: the panel uses the `claude`/`codex` sign-in of the service user (`ANTHROPIC_API_KEY`, if set, makes Claude Code use the API key instead, and plan limits disappear). Without nginx, leave `MANAGE_NGINX` unset and `PORT=4900` (then only this computer can register the first account and use it). | done (`config.py`) |
| WS-R-08 | **Network:** the app listens on `HOST:PORT` (`.env`; 127.0.0.1:4910 on this host). With `MANAGE_NGINX=1` the app writes the nginx site itself (Settings → Connection, `transport.nginx_conf`): `listen PUBLIC_PORT` (4900) with HTTPS from its own CA in `data/tls/` (default) or HTTP; `proxy_http_version 1.1`; `Host $http_host`; `X-Forwarded-For`, `X-Forwarded-Proto`; `Upgrade $http_upgrade` and `Connection` via `map $http_upgrade` (**required for the terminal WebSocket**); `proxy_read_timeout`/`proxy_send_timeout 1d`; `proxy_buffering off`; `client_max_body_size 100m`; `/static/vendor/` cached 7 days. A hand-written proxy must forward the same headers, or the terminal closes with 4403. | done (`transport.py`) |
| WS-R-09 | **First account:** the first (admin) account can only be registered from localhost. Open `http://127.0.0.1:PORT` on the host, register, then use other computers. | done |
| WS-R-10 | **State that carries over**: `.env` (it holds API keys: copy it privately, never through git); copy `data/` to keep the rest: `data/agent_company.db` (projects = explorer roots; Setting `files.extra_roots` = folders opened in the Workspace; uploads table), `data/uploads/` (panel attachments), `data/secret.key`, `data/tls/`. Regenerated, need not be copied: `data/terminal/bashrc`, `data/tmp/`. Claude Code's own state lives in `~/.claude` (`CLAUDE_CONFIG_DIR` if set) and `~/.claude.json` of the service user: copy them to keep history and sign-in. Per-browser state (layout, tabs, conversations) is in each browser's `localStorage` ([§12](#12-persistence)) and does not move. | done |

Acceptance (manual, on the new host): `uv sync && uv run pytest -q` passes; `systemctl status agent-company` is active; from a second computer the Workspace opens over `https://<host>:4900`, a terminal prints a prompt, and a Claude prompt answers.

## 3. Shell the Workspace depends on

Provided by the dashboard, not by the Workspace files; a rebuild needs equivalents.

- `app.js`: `$`, `esc` (HTML-escape `& < > " '`), `fmt` (`Intl.NumberFormat`), `store` (localStorage get/set/del that never throws), `api(path, {method, body, fresh})` (JSON fetch, `credentials: "same-origin"`; throws `Error` with `.status`/`.code`; GETs under `/api/(workspace|files|auth|stream|machines/)` are **never cached**), `toast(msg)` (4 s), `icon(name, size)`, `registerView`, `modal`, `iconize` (buttons with known labels become icons). `files.js`: `pickFolder(start)` — modal listing folder names from `/api/files/folders` ("Choose the work folder", `..` row, git pills, *Use this folder* / *Cancel*).
- `style.css` tokens. Light: `--bg #f6f7f9 --panel #fff --panel-2 #f1f3f6 --border #e2e5ea --text #1b1f24 --muted #5f6b7a --accent #2563eb --accent-text #fff --ok #15803d --warn #b45309 --bad #b91c1c --info #0369a1 --shadow 0 8px 30px rgba(15,23,42,.12)`; dark (`[data-theme=dark]` or `prefers-color-scheme: dark` without `[data-theme=light]`): `--bg #0f1115 --panel #161a20 --panel-2 #1d222a --border #2a313b --text #e6e9ee --muted #9aa5b4 --accent #3b82f6 --ok #4ade80 --warn #fbbf24 --bad #f87171 --info #38bdf8 --shadow 0 8px 30px rgba(0,0,0,.5)`; `--mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`; body `14px/1.45 system-ui`. Theme toggle stores `ac.theme`.
- `index.html` loads, in order: `vendor/seti/seti.js`, `app.js`, `layouts.js`, `agents.js`, `models.js`, `projects.js`, `performance.js`, `files.js`, `viewer.js`, `console.js`, `claude.js`, `workspace.js`, `admin.js`, `machines.js` (all `defer`), then `start()`. `GET /` rewrites every `/static/*.js|css` URL to `?v=<sha256[:10]>` and adds `<meta name="ac-build">`; `Cache-Control: no-cache` on `/`.
- `.view.flush` (`padding: 0; overflow: hidden; position: relative`) is set while the Workspace is shown; `.wsp` is `position: absolute; inset: 0` so no pane can grow the page.

## 4. Layout and windows (`workspace.js`, `workspace.css`)

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-11 | Grid of five columns `left | 5px gutter | centre | 5px gutter | right`; defaults left 260 px, right 400 px, terminal height 260 px; centre `minmax(240px, 1fr)`. Centre rows `minmax(120px, 1fr) 5px <term>px`. Gutters are drag handles (pointer capture; `.active` while dragging; accent 45 % on hover): left clamped 160 px … 40 % of width, right 280 px … 60 %, terminal 80 px … centre height − 120. Saved to `ac.wsp.sizes` on release. | done |
| WS-R-12 | Narrow screens (`max-width: 900px`): one column, gutters hidden, explorer 260 px tall, centre 80vh, Claude 80vh, page scrolls. | done |
| WS-R-13 | Each of the four windows (explorer, editor, terminal, claude) has **Minimise** and **Maximise/restore** buttons. Minimised: side windows become a 34 px vertical strip (label in `writing-mode: vertical-rl`, uppercase 11px mono); editor/terminal become a 24 px horizontal strip. Clicking a strip restores. Maximised: the window fills the Workspace, the others and the gutters are hidden; the maximise icon swaps to "restore". Editor **and** terminal minimised (`mid-min`): the centre becomes a 34 px vertical strip and Claude (else the explorer) takes `1fr`. State in `ac.wsp.min` (array) and `ac.wsp.max` (pane or ""). | done |
| WS-R-14 | The terminal panel can be hidden (chevron button, Ctrl+`` ` `` toggles it back); `ac.wsp.termHidden = "1"`. Minimising a hidden terminal shows it. After every layout change terminals are re-fitted now and on the next animation frame. | done |

Acceptance: in a browser, drag each gutter beyond its limit and reload — sizes are clamped and restored; minimise editor + terminal — Claude widens to the free space.

## 5. Explorer

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-15 | **Header** "EXPLORER" (11 px, 700, `.08em`, uppercase, muted) and buttons: New file, New folder, Upload files, Refresh, Collapse folders, Filter (Ctrl+F in the explorer), Split (+), Minimise, Maximise. Below: hidden filter input ("Filter by name…"), a root `<select>` (options `name · path`, last option `＋ Open folder…` → `pickFolder`), and ✕ *Close this folder* shown only for roots opened in the Workspace (confirm "Close … in the explorer? Nothing on disk changes."). | done |
| WS-R-16 | **Roots** = every project's path, then `data`, then `files.extra_roots`, de-duplicated, existing only. The initial root is `ac.wsp.root` if still listed, else the first root not named `data`. | done (`files.roots`) |
| WS-R-17 | **Tree:** `.git` is never listed; folders first, then case-insensitive name. Rows 24 px, indent `6px + depth × 14px`, chevron `›` rotating 90° when open, icon, name (ellipsis), git mark at the right. Tooltip: name · size · git meaning. Classes: `sel` (accent 16 %), `act` (file of the active editor tab), `cut` (opacity .5), `drop` (accent 22 %, dashed outline). Empty root: "Empty folder. Drop files here to upload."; no root: "No folder open." + *Open folder*. Open folders are remembered in `ac.wsp.open` (last 300); a folder is shown only if its whole chain is open. | done |
| WS-R-18 | **Git decorations** from `GET /api/files/git-status`: `M` modified `#d29922` (light theme `#9a6700`), `U` untracked and `A` added `#3fb950`, `D` deleted (`--bad`, struck through), `R` renamed `#58a6ff`, `C` copied, folders containing changes `●` (9 px, `#d29922`). Computed with `git status --porcelain=v1 -z --untracked-files=normal` from the work-tree top; rename/copy entries skip the following original name. | done (`files.git_status`) |
| WS-R-19 | **Mouse/keyboard:** click selects (folder: toggles; file: opens); Ctrl/⌘-click toggles in the selection; Shift-click selects the range from the anchor; click on empty space clears. Keys in the tree: ↑/↓ move, →/← open/close a folder, Enter opens/toggles, F2 rename, Delete delete, Ctrl+C / Ctrl+X / Ctrl+V copy / cut / paste, Ctrl+F filter; Esc in the filter closes it. Filtering keeps matching rows and the folders leading to them ("No file matches the filter."). | done |
| WS-R-20 | **Context menu** (`.ctx`, min 220 px, 8 px radius, clamped to the window; Esc, blur or click outside closes; first enabled item focused). On a row (or the selection): *Open* (Enter; one file) · *Run in terminal (…)* for `py→python3, sh→bash, js/mjs→node, rb→ruby, go→go run, php→php, pl→perl, ts→npx tsx` (sends `cd '<dir>' && <runner> '<file>'\r`) · *New file… / New folder… / Upload files here…* (folders or background) · *Open in terminal* · *Ask Claude about this* / *about N items* · — · *Cut* · *Copy* · *Paste N item(s)* (disabled without clipboard) · *Duplicate* · — · *Copy path(s)* · *Copy relative path(s)* (background: *Copy folder path*) · — · *Download* (one file) / *Download as .zip* · *Compress to .zip* · *Extract here* (one item matching `\.(zip|tar|tgz|tar\.(gz|bz2|xz)|tbz2|txz)$`) · — · *Rename…* (F2) · *Delete* / *Delete N items* (danger, red) · *Refresh* (background). Leading and doubled separators are dropped. | done |
| WS-R-21 | **Inline names** (new file, new folder, rename) like VS Code: an input row in the tree; the name without its extension is pre-selected; Enter or blur accepts, Esc cancels; a new file opens in the editor. **Duplicate** names `name copy.ext`, then `name copy 2.ext`, … . **Delete** confirms ("Delete X? The folder is deleted with everything in it." / "Delete N items? …") and closes tabs of deleted files. **Move** by drag (Ctrl/Alt-drag copies) or cut/paste; never into itself or its own folder; open tabs follow renamed/moved paths. | done |
| WS-R-22 | **Upload**: the button / menu item opens a multi-file picker; files and whole folders dropped from the computer upload recursively (folders created first; `webkitGetAsEntry`, entries read before the first `await`). Name clashes ask first ("Replace N existing file(s) in … ?", "Replace or merge …?"). Progress in the status line ("Uploading 3/7: name"). Max 50 MB per file. | done |
| WS-R-23 | **Extra panes** (+): each has its own root `<select>` (registered roots, or `＋ Open folder from / …` → `pickFolder("/")`, which registers the folder as a root), Refresh, Collapse, Close; click opens files/toggles folders; context menu *Open*, *Open in terminal*, *Copy path*, *Ask Claude about this*; rows drag their path. Pane roots persist in `ac.wsp.panes`. | done |
| WS-R-24 | **Refresh policy:** re-list the root and every visible open folder; re-render only if names/sizes/mtimes changed. Poll every `10 s × (1 + floor(idle/3))`, max 60 s, where `idle` counts unchanged refreshes; never while the page is hidden or a context menu is open; window focus refreshes at once and resets `idle`. A root that cannot be listed shows "This folder is not available." | done |
| WS-R-25 | **File icons** are VS Code's Seti theme (`vendor/seti/seti.js` = `window.SETI` `{d: id→[glyph, dark, light], e: ext, n: name, l: language, f: default}`, font `seti.woff`). Lookup: exact file name → extensions longest first (`test.js` before `js`; `.bashrc` → `bashrc`) → language id of the last extension (`LANG`/`LANG_NAMES` tables, `.env*` → `dotenv`) → default `e023`. Glyph drawn at 150 % of the icon box, dark colour in the dark theme. Folders are inline SVG (closed / open variants), coloured by name: `src lib app #4f8fe8`, `test tests spec #43a047`, `docs doc #2aa6d6`, `.git #f05032`, `.github config #6d8086`, `node_modules #7cb342`, `static public assets #fb8c00`, `images #26a69a`, `data #8e5cc8`, `scripts bin #3d9a2c`, `.venv venv #3776ab`, `dist build #a67c00`, other `#d9a53f`. | done |

Acceptance: `tests/test_files.py::test_listing_read_write_create_move_delete`, `::test_paths_stay_inside_the_roots`, `::test_folder_picker_lists_names_only`; `test_compress_extract_and_open_folder`; `test_download_endpoints`.

## 6. Editor and viewer (`viewer.js`)

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-26 | **Tabs** (34 px, `--panel-2` bar; active tab `--panel` with a 2 px accent top inset): icon, name, × (● when dirty). Middle-click closes; closing a dirty tab confirms. Tab menu: *Close*, *Close others*, *Close all*, —, *Copy path*, *Reveal in explorer*, *Ask Claude about this*. Open tabs and the active one persist in `ac.wsp.tabs` `{open: [paths], active}` and reopen on load. With no tab, a welcome card ("Workspace", tips list incl. Ctrl+`` ` `` terminal · Ctrl+L Claude · Ctrl+S save). | done |
| WS-R-27 | **Kinds.** The server's `files.KINDS` (by suffix, then file name; else MIME `image/ audio/ video/`; else `file`) wins; the client `kindOf` (`KIND` table, `dockerfile`→docker, `makefile`→make) is the fallback. Binary kinds: image, pdf, docx, audio, video, archive, sheet, slides. Text over 2 MB or with a NUL byte or invalid UTF-8 is "binary" (info card + Download). | done (`files.read`) |
| WS-R-28 | **Modes** (segmented icon buttons): markdown *Preview*/*Source*, csv *Table*/*Source*, json *Formatted*/*Source*, svg *Image*/*Source*, other text *Code*; every editable text kind also *Edit* (textarea; Tab inserts 4 spaces in Python, else 2; Ctrl/⌘+S saves via `PUT /api/files/write`; *Save* button enabled when dirty). Toolbar: breadcrumbs (relative path, `›`), Find (Ctrl+F; F3/Shift+F3), Word wrap (code/source), zoom −/+/Fit for images, PDF and SVG image (×1.25 / ×0.8, 0.1–8), Copy path, Reload (confirms over unsaved edits), Download. | done |
| WS-R-29 | **Views:** code with a sticky line-number gutter (`13px/1.5 mono`, wrap hides numbers); Markdown preview (max 900 px, relative links open in the Workspace, images through `/api/files/raw`); CSV/TSV table (sticky header and `#` column, first 5000 rows, numeric cells right-aligned, 8 rotating column colours); JSON pretty-printed (invalid JSON: "Not valid JSON: …" then source); images on a checkerboard with `W × H px`; audio/video `<video|audio controls>`; PDF with pdf.js (pages lazily rendered by `IntersectionObserver`, margin 600 px, fit width ≤ 2×, `isEvalSupported: false` for CVE-2024-4367); DOCX with mammoth on a white 816 px page (links other than http/mailto/# removed, non-`data:` images removed); archives an info card. | done |
| WS-R-30 | **Find in file:** every match highlighted with the CSS Custom Highlight API (`::highlight(wv-find)` amber 35 %, `wv-find-cur` `#f59e0b`); text split across syntax tokens is matched as one; line numbers ignored; up to 10 000 matches ("N of M", "No results"); Enter / Shift+Enter step; in Edit mode the textarea selection moves instead. | done |
| WS-R-31 | **Syntax colouring**: one combined regex per language family (clike, hashlike, lua, python, shell, sql (case-insensitive), json, yaml, toml, html, css, markdown, diff, log, docker, make), token classes `t-kw t-str t-num t-com t-fn t-type t-lit t-builtin t-deco t-op t-key t-tag t-attr t-var t-head t-add t-del t-hunk t-err t-warn t-info t-dbg t-time t-prop t-sel t-punct t-strong t-em`, colours GitHub-like light/dark (`workspace.css` lines 2–25). Files ≥ 600 000 chars are not coloured. | done |
| WS-R-32 | **Markdown renderer** (`W.md`, used by the viewer and Claude answers): fenced code with language bar + *Copy*, ATX headings with slug ids, `---` rules, blockquotes and GitHub callouts (`[!NOTE] TIP IMPORTANT WARNING CAUTION`), pipe tables with alignment, nested and ordered lists (with `start`), task lists, inline code, images, links (external `target=_blank rel=noopener noreferrer`, relative → open in Workspace), autolinks, `***`/`**`/`*`/`_`/`~~`, hard breaks. All text is escaped first. | done |
| WS-R-33 | After a Claude run finishes, clean tabs are reloaded from disk and the explorer refreshes; dirty tabs are kept. A pending app update reloads the page on the next view switch only when no tab is dirty. | done |

Acceptance: `test_kinds_cover_documents_archives_and_media`; manual: open a `.md`, `.csv`, `.pdf`, `.docx`, an image; edit and save a `.py` with Ctrl+S.

## 7. Terminal (`console.js`, `terminal.py`)

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-34 | **Client:** xterm.js + fit addon, loaded on demand. Options: font `ui-monospace, "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace`, 13 px, lineHeight 1.15, `cursorBlink`, scrollback 5000, `macOptionIsMeta`, `rightClickSelectsWord`. Themes follow the page theme live (dark bg `#0d1117` fg `#d6dde6` cursor `#58a6ff`; light bg `#fff` fg `#1f2328` cursor `#0969da`; full 16-colour palettes in `console.js`). | done |
| WS-R-35 | **Bar** (32 px): tabs (icon `>_`, title from the shell's title escape, else the cwd's last part; × kills after a confirm "Kill this terminal and everything running in it?"), New terminal (in the explorer root), Reconnect, Clear, Minimise, Maximise, Hide (Ctrl+`` ` ``). Dead tabs are struck through; lost tabs are red. On load, every live server session is re-attached; none → one new terminal. "Open in terminal" opens one in that folder. | done |
| WS-R-36 | **Keys:** Ctrl/⌘+C copies when text is selected (Ctrl+Shift+C always); Ctrl/⌘+V pastes through the browser; Ctrl+`` ` `` is left to the page. Dropping explorer items or text types their paths, single-quoted unless `^[\w./-]+$`, space-separated. | done |
| WS-R-37 | **Server shells:** `os.openpty()`, `bash --rcfile data/terminal/bashrc -i` (other shells `-i`) from `$SHELL` (default `/bin/bash`), new session + controlling TTY, `TERM=xterm-256color COLORTERM=truecolor AGENT_COMPANY_TERMINAL=1`, `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` removed from the environment. The rc file sources `/etc/bash.bashrc` and `~/.bashrc`, adds colour aliases (`ls grep egrep diff ip`), `GCC_COLORS`, and the prompt `[✗ status ]user@host:cwd (branch) $ ` (green / blue / magenta / cyan; the typed command yellow, reset by `PS0`). Limits: at most 12 live shells; 256 KB scrollback kept for replay; a shell with nobody attached for 6 h is closed; an exited one is forgotten 60 s later. cwd defaults to `$HOME`; must be a folder. Close sends SIGHUP then SIGKILL to the process group. | done |
| WS-R-38 | **WebSocket** `/api/terminal/sessions/{sid}/ws` (`ws://` or `wss://` by page scheme, `binaryType = arraybuffer`). Server → client: the replay buffer as one binary frame, then output as binary frames (bursts coalesced), and text `{"t":"exit","code":n}` when the shell ends. Client → server: text `{"t":"in","data":"…"}`, `{"t":"resize","cols":c,"rows":r}` (clamped 2–1000 × 2–500), or binary input. Close codes: **4403** not an admin / wrong Origin / wrong scheme, **4404** no such shell. The client resets the screen before the first frame, reconnects with backoff `min(8000, 500·2ⁿ)` ms up to 8 tries, then prints the reason and waits for Enter or the Reconnect button; after 4404 it opens a new shell in the same folder. Lost connections reconnect by themselves when the tab becomes visible or the network returns. The shell keeps running when the socket closes. | done |

Acceptance: `test_terminal_session_runs_commands_and_replays`, `test_terminal_is_admin_only_and_same_origin`.

## 8. Claude Code panel (`claude.js`, `workspace.py`)

| ID | Requirement | Status |
| --- | --- | --- |
| WS-R-39 | **Header** (min 41 px): logo (✳ `#d97757` Claude, ◎ `#10a37f` Codex, ⬡ `#8250df` Copilot), agent `<select>` (an uninstalled agent is disabled "(not installed)"; locked once a conversation has a session), status dot (green idle / pulsing `--claude` busy), Effort `<select>` (Claude: default low medium high xhigh max; Codex: default low medium high; hidden when only default), Model `<select>` (Claude: Default model, Opus, Sonnet, Haiku; Codex: default), Compact (`/compact`, Claude only, idle with a session), Sessions (clock), Status (pulse), New conversation (+), Minimise, Maximise. Under 560 px panel width (container query) effort and model wrap onto their own row. | done |
| WS-R-40 | **Conversation tabs:** one per conversation of this browser; new ones on the **far right** (an empty current conversation is reused). An earlier conversation (restored, opened or continued from the sessions window) opens scrolled to its **beginning**; one that is working, or a prompt just sent, follows its end. Title = the conversation's title, else the first prompt's first line (40 chars), else "New conversation"; titled by Haiku after the first prompt (`make_title`: `claude -p --model haiku --no-session-persistence --max-turns 1 --tools "" --output-format text`, 45 s timeout, 2–6 words, fallback the first line ≤ 48 chars) and sent as `--name` on every later prompt. Each tab has its own draft text and attachments and its own log element (switching shows it without re-rendering). Close a busy tab → confirm and stop. Host sessions of other processes get a tab only while opened from the sessions window; closing such a tab only hides it (`…closed`, forgotten when the session ends). Middle-click / Delete closes; Enter/Space selects. | done |
| WS-R-41 | **Composer:** textarea 64–260 px auto-height; placeholder "Ask Claude Code to read, change, run or explain anything in this folder…  Enter sends · Shift+Enter new line · paste or drop images · /status /tasks /agents /history". Enter sends, Shift+Enter new line, Esc stops, ↑/↓ walk earlier prompts. Paste, drop or the paperclip attach files (`/api/uploads`: ≤ 10 MB, `image/ text/ application/json application/pdf application/x-yaml`, ≤ 10 per prompt; chips with thumbnails; sending waits for pending uploads). Dropping explorer items inserts `@relative/path` (absolute when the conversation's folder is not the explorer root). Round send button (gradient `--claude` → violet) turns into a red pulsing Stop while busy. `/status /tasks /agents /history /resume` are answered by the panel itself. Without a folder: "Open a folder in the explorer first". | done |
| WS-R-42 | **Rendering** of normalised events: `init` (one line: model · N tools · folder), `thinking` (collapsible, purple left border, open while running), `text` (Markdown + Copy), `tool_use` (collapsible card, left border by tool tone: Bash green, Read blue, Edit/MultiEdit/Write/NotebookEdit orange, Glob/Grep/LS teal, WebFetch/WebSearch purple, Task pink, TodoWrite yellow, other grey; summary per tool; body: Bash command, Edit/MultiEdit −/+ diff, Write content, TodoWrite checklist ✓ ◐ ○, else JSON; its result "Output"/"Error" with Copy; ✓ / ✗ / spinner), `result` ("✓ Done · N turns · s · $cost" + *Copy answer*), `prompt`, `notice` (background task finished), `compact` ("Conversation compacted: a → b tokens"), `error`, `log`. Subagent events (`parent`) are indented 16 px. Open/closed state of cards survives re-renders. Empty conversation: logo, name, "Works in the folder open in the explorer, with full access…", four suggestions (Explain how this project is structured · Run the tests and fix what fails · Find TODOs and summarise them · Review the latest git changes). Claude not installed: an error with the install command. | done |
| WS-R-43 | **Runs** (`POST /api/workspace/claude`): `claude -p --output-format stream-json --verbose --permission-mode acceptEdits --allowedTools Bash,Read,Edit,MultiEdit,Write,Glob,Grep,LS,WebFetch,WebSearch,NotebookEdit,TodoWrite,Task [--add-dir <folder of each attachment>] (--session-id <new uuid> | --resume <id>) [--name <title>] [--model m] [--effort e] [--append-system-prompt <GM prompt>]`, prompt on stdin (attachments appended as "Attached files (open them with the Read tool): - path (name)"), cwd = the conversation's folder, own process group, environment without `CLAUDECODE`/`CLAUDE_CODE_ENTRYPOINT`. Codex: `codex exec --json --skip-git-repo-check -s workspace-write [--add-dir …] [-m m] [-c model_reasoning_effort=e] (resume <id> - | -)`. A session id that is busy in another process is refused; a session whose first prompt failed before writing a transcript starts afresh under its id. At most 40 runs kept (oldest running one is stopped). Stop = SIGTERM to the process group. The client polls the run every 600 ms (2 s after an error); a 404 (server restarted) switches the tab to the session's transcript. | done |
| WS-R-44 | **Normalisation** of stream-json lines (`workspace.normalise`): `system/init` → init (session, model, cwd, tools count, permission mode, version, MCP servers, agents, slash commands), `system/compact_boundary` → compact, assistant `text`/`thinking`/`tool_use`, user `tool_result` (not replays; ≤ 8000 chars), `result` → result (cost, duration, turns, tokens in = input + cache read + cache creation, out). Strings clipped at 20 000 chars. Non-JSON lines → log; a non-zero exit without a result → error with stderr (last 4000 chars). Codex `thread.started / item.* / turn.*` map to the same kinds. | done |
| WS-R-45 | **Sessions window** (Sessions button, `/history`, `/resume`; Esc closes; overlays the panel below the header): tabs *Running* (every live Claude Code session on the host — this panel's first — with *Open* and *Stop*; stop of another process confirms and sends SIGTERM) and *History* (every transcript, live first, newest first; search over title, folder, prompts, id, branch; up to 300 shown of the total; *Open*, *Continue* (ended: resume in a tab, `--resume`), *Delete* (ended: transcript, subagent transcripts and `~/.claude/file-history/<id>` after a confirm)). Rows: dot, title, badges (tab in this panel / VS Code / Terminal / SDK / Desktop / JetBrains / working), folder · ⎇ branch, last prompt, "active … ago · up … · size · pid · id". | done |
| WS-R-46 | **Host sessions** are read from Claude Code's own files, never written to: registry `~/.claude/sessions/<pid>.json` (alive only if `/proc/<pid>/stat` field 22 equals `procStart`; the title helper's pids are excluded; cached 3 s), transcripts `~/.claude/projects/<folder>/<session>.jsonl` read incrementally (first open reads the last 2 MB, "Earlier history not loaded"; 400 events kept; 64 watched), subagents `…/<session>/subagents/agent-*.meta.json`, tasks `~/.claude/tasks/<session>/*.json`, custom agents `~/.claude/agents/*.md` and `<cwd>/.claude/agents/*.md`. Titles: custom title → AI title → this server's title → first prompt (cached 30 s). An ended session opened in a tab is read-only until a prompt continues it; a session still busy in another process can be followed live ("Still working in its own process … following it live") and stopped. | done |
| WS-R-47 | **Status drawer** (`/status /tasks /agents`, max 48 % height). */status*: status + current tool, session, started from (entrypoint, pid, uptime), folder + branch, model, effort, permissions, context tokens (last request), output tokens, last prompt, last activity, Claude Code version, account (API key / Claude account email + organisation / not signed in); then **plan limits** as 44 px rings (5-hour session, weekly, weekly per model; colour by severity warning / critical / exceeded, also a badge; resets at …) read from `https://api.anthropic.com/api/oauth/usage` with the host's `~/.claude/.credentials.json` token (header `anthropic-beta: oauth-2025-04-20`; cached 60 s; on 429 the last answer is kept; the token never leaves the server); then **usage** today / this week (00:00 UTC, Monday) over every transcript: tokens, cost at the Models catalog's prices (cache read 0.1×, 5-min write 1.25×, 1-h write 2× input), resets in …, per-model breakdown on hover. */tasks*: background commands and background subagents, todo list, Claude's task list, then other sessions on the host with background work or open todos. */agents*: subagents (running first, type, model, tokens, tool calls, duration), Claude Code on this host (sessions, working, subagents running), custom agents, available agent types. With a Models-page `claude_code` model that has its own sign-in chosen in the model menu, the drawer follows it: account, plan limits and usage of that sign-in (`/api/workspace/claude-usage?model=`), its live sessions and its custom agents (`/api/workspace/claude-activity?model=`); the Model row shows the model for the next prompt and the one last used. With Codex or Copilot it names that agent and its version, without Claude usage. | done |

Acceptance: `test_normalise_maps_stream_to_display_events`, `test_claude_panel_runs_resumes_and_attaches_images`, `test_codex_effort_is_a_config_override`, `test_make_title_asks_haiku_without_tools_and_falls_back`, `test_claude_panel_reports_missing_cli`, `test_claude_sessions_list_only_live_processes`, `test_claude_session_status_tasks_and_agents`, `test_claude_session_that_ended_reports_unfinished_work`, `test_claude_history_lists_running_and_ended_sessions_and_deletes`, `test_claude_session_stop_ends_the_process`, `test_claude_usage_counts_today_and_this_week_once_per_message`, `test_subagents_and_tasks_beyond_the_transcript_read_and_the_host_overview`, `test_plan_limits_from_the_claude_sign_in`.

## 9. API contract

All routes need an admin session (`admin_user`) except `/api/uploads*` (any signed-in user). Errors are
JSON `{"detail": …}`: `FileError`/`ValueError` 400, `PermissionError` 403, `KeyError` 404, upload too
big 413, agent CLI missing 503. "Audit" = `governance.audit("user:<name>", action, target, …, severity)`.

| Method & path | Body / query | Returns | Audit |
| --- | --- | --- | --- |
| GET `/api/files/roots` | — | `[{name, path, opened?:"1"}]` | — |
| POST `/api/files/roots` | `{path}` | `{name, path}` | `folder_open` high |
| DELETE `/api/files/roots` | `?path` | `{ok}` | `folder_close` |
| GET `/api/files/list` | `?path` | `{path, parent, items:[{name, path, kind, dir, size, modified}]}` | — |
| GET `/api/files/git-status` | `?path` | `{abs path: M\|A\|D\|U\|R\|C\|dot}` | — |
| GET `/api/files/read` | `?path` | `{path, kind, size, binary, text}` | — |
| GET `/api/files/raw` | `?path&download=1` | the file; inline with `X-Content-Type-Options: nosniff`, `CSP: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'` | `file_download` (download only) |
| GET `/api/files/archive` | `?paths=…&paths=…` | temporary zip (`<name>.zip` / `download.zip`), deleted after sending | `file_download` |
| POST `/api/files/compress` | `{paths (≥1), name?}` | `{path, size}`; zip beside the first item, unique `name (2).zip` | `file_compress` |
| POST `/api/files/extract` | `{path}` | `{path}`; new folder beside it; tar with `filter="data"` | `file_extract` |
| PUT `/api/files/write` | `{path, text}` | `{path, size}` | `file_write` |
| POST `/api/files/create` | `{path, folder}` | `{path}` | `file_create` |
| POST `/api/files/move` | `{src, dest, copy}` (`copy` is the alias of `keep_source`) | `{path}`; `dest` a folder → inside it | `file_move` / `file_copy` |
| POST `/api/files/delete` | `{paths}` | `{deleted}`; roots cannot be deleted | `file_delete` high |
| POST `/api/files/upload` | `?folder`, multipart `file` | `{path, size}`; ≤ 50 MB | `file_upload` |
| GET `/api/files/folders` | `?path` | `{path, parent, folders:[{name, path, git}], git}`; names only, anywhere, ≤ 500, no dot-folders | — |
| GET `/api/terminal/sessions` | — | `[{id, cwd, owner, title, alive, exit_code, attached, created}]` | — |
| POST `/api/terminal/sessions` | `{cwd, cols 2–1000 =120, rows 2–500 =32}` | session | `terminal_open` high |
| DELETE `/api/terminal/sessions/{sid}` | — | `{ok}` | `terminal_close` |
| WS `/api/terminal/sessions/{sid}/ws` | see WS-R-38 | | |
| GET `/api/workspace/claude` | — | `{available, path, models, version, account, providers:[{id, label, available, models, efforts, version, install}]}`; warms the usage scan | — |
| POST `/api/workspace/claude` | `{prompt 1–100 000, cwd, session_id, model, effort ≤16, title ≤200, attachments ≤10, provider claude\|codex\|copilot, project_id?}` | run `{id, prompt, cwd, status, provider, session_id, title, model, effort, started, ended, images, events, next}` | `workspace_claude` high |
| GET `/api/workspace/claude/{run_id}` | `?after=n` | run with events from `n` | — |
| POST `/api/workspace/claude/{run_id}/stop` | — | `{status}` | `workspace_claude_stop` |
| GET `/api/workspace/claude-sessions` | — | live host sessions `[{pid, session_id, cwd, name, status busy\|idle, entrypoint, kind, version, started, updated, title}]` | — |
| GET `/api/workspace/claude-sessions/{id}` | `?after=n` | status snapshot (WS-R-46/47) + events after `n`, `next` | — |
| POST `/api/workspace/claude-sessions/{id}/stop` | — | `{session_id, pid, panel}`; refuses the server's own pid | `workspace_claude_session_stop` high |
| GET `/api/workspace/claude-history` | `?q (≤200)&limit 1–1000 =200` | `{total, items}` | — |
| DELETE `/api/workspace/claude-history/{id}` | — | `{session_id, deleted}`; refuses a live session | `workspace_claude_session_delete` high |
| GET `/api/workspace/claude-usage` | — | `{daily, weekly, now, files, plan}` | — |
| GET `/api/workspace/claude-activity` | `?cwd` | `{sessions, busy, agents_running, tasks_running, agent_types, configured_agents}` | — |
| POST `/api/uploads` | multipart `file` | `{id, name, content_type, size}`; stored as `data/uploads/<uuid>-<safe name>` | — |
| GET `/api/uploads/{id}` | — | the file, `nosniff`, `CSP: default-src 'none'` | — |

**Path rule** (`files.resolve`): resolved (symlinks followed) and equal to or under a root, no NUL;
anything under `.git` is read-only; session ids must match `[0-9a-fA-F-]{8,64}`; model names
`default|opus|sonnet|haiku` or `[\w.\-\[\]]{2,80}`.

## 10. Keyboard shortcuts

| Where | Keys |
| --- | --- |
| Workspace (anywhere) | Ctrl+`` ` `` show/focus terminal · Ctrl+L focus Claude (not inside the terminal) · Esc closes a context menu |
| Explorer tree | ↑ ↓ ← → · Enter · F2 · Delete · Ctrl/⌘+C X V · Ctrl/⌘+F filter (Esc closes) · Shift/Ctrl-click |
| Viewer | Ctrl/⌘+F find · Enter / Shift+Enter / F3 / Shift+F3 next/previous · Esc closes find |
| Editor (Edit mode) | Ctrl/⌘+S save · Tab indent (4 Python, else 2) |
| Editor tabs / Claude tabs | middle-click close · Claude tabs: Enter/Space select, Delete close |
| Terminal | Ctrl/⌘+C copy with selection (Ctrl+Shift+C always) · Ctrl/⌘+V paste · Enter after "disconnected"/"exited" reconnects/closes |
| Claude composer | Enter send · Shift+Enter newline · Esc stop · ↑/↓ prompt history · Esc closes the sessions window |

## 11. Polling and timing

| What | Interval |
| --- | --- |
| Explorer refresh | 10 s, backing off to 60 s while nothing changes; paused while hidden or a menu is open; on focus at once |
| Claude run | 600 ms while running; 2 s after an error |
| Host sessions list | 3 s (5 s while the page is hidden or the panel detached) |
| Selected session detail | 1.5 s busy, 4 s otherwise; 2 s while the drawer waits for a first session |
| Sessions window history | 15 s while open; search debounced 300 ms |
| Usage (`/status`) | 30 s · host activity (`/tasks`, `/agents`) 4 s |
| Conversation title | retried every 2 s, at most 16 tries |
| Server caches | sessions 3 s · titles 30 s · plan limits 60 s · prices 60 s |

## 12. Persistence

Browser `localStorage` (per browser and origin):

| Key | Value |
| --- | --- |
| `ac.view` | last view id (default `workspace`) · `ac.theme` `light`/`dark` |
| `ac.wsp.root` | explorer root path |
| `ac.wsp.open` | JSON array of open folders (last 300) |
| `ac.wsp.sizes` | `{"left":260,"right":400,"term":260}` |
| `ac.wsp.termHidden` | `"1"` or `""` |
| `ac.wsp.min` / `ac.wsp.max` | JSON array of minimised panes / maximised pane or `""` (`explorer editor terminal claude`) |
| `ac.wsp.panes` | JSON array of extra explorer pane roots |
| `ac.wsp.tabs` | `{"open":[paths],"active":path}` |
| `ac.wsp.claude.model` / `.effort` / `.provider` | last choices |
| `ac.wsp.claude.convs` | `[{id, session, cwd, title, provider, runs:[last 30 run ids]}]` |
| `ac.wsp.claude.tab` / `.closed` | selected conversation id / hidden host-session ids |
| `ac.wsp.claude.runs` / `.session` / `.cwd` | legacy single-conversation keys, read once and migrated |

Server memory (lost on restart, by design): terminal shells' registry (the shells themselves die with the
service), Claude runs (`RUNS`, 40), transcript watchers. A conversation running across a restart continues
in its own process group and is followed from its transcript.

## 13. Limits

| Limit | Value |
| --- | --- |
| Text opened in the viewer | 2 MB |
| Explorer upload / panel attachment | 50 MB per file / 10 MB, 10 per prompt |
| Terminals | 12 live, 256 KB replay, 6 h unattended, 5000 lines scrollback in the browser |
| Claude runs kept | 40; event field 20 000 chars; tool result 8000; system prompt 20 000 |
| Transcripts | first read 2 MB from the end; 400 events per session; 64 watched; history head 64 KB + tail 128 KB |
| CSV table / find matches | 5000 rows / 10 000 matches |

## 14. Files and vendored assets

Copy `src/agent_company/static/vendor/` **verbatim**; nothing is fetched from a CDN at run time.

| File | Upstream | Version |
| --- | --- | --- |
| `vendor/xterm.js`, `xterm.css` | xterm.js (MIT), UMD build | not stamped in the file: match by checksum |
| `vendor/xterm-addon-fit.js` | xterm-addon-fit (MIT) | not stamped |
| `vendor/pdf.min.js`, `pdf.worker.min.js` | pdf.js (Apache-2.0) | 3.11.174 |
| `vendor/mammoth.browser.min.js` | mammoth.js (BSD-2) | not stamped |
| `vendor/seti/seti.js`, `seti.woff`, `LICENSE.txt` | microsoft/vscode `extensions/theme-seti` (MIT); `seti.js` generated from `vs-seti-icon-theme.json` | — |
| `vendor/novnc/` | noVNC (MPL-2.0) — Machines view, not the Workspace | 1.7.0 |

Checksums (whole files, 2026-11-05):

| File | sha256 |
| --- | --- |
| `static/workspace.js` (layout, explorer, editor tabs) | `c0c0faf8d3f5fa280d7ad3c945c164011474bcaddfdd642380b6038411ba4a4b` |
| `static/viewer.js` (viewer, syntax colouring, Markdown) | `a6afeebd3509c916e0d69c3a0bf4797718e32bd6a9fd177b95e58b6cbf4beb3c` |
| `static/console.js` (terminal client) | `6957151ef2930f3ea80dc4cd164391a7c859b276951a54124333b069e9787cfe` |
| `static/claude.js` (Claude Code panel) | `2a36d442f3cd1962f1a3a62cb8d773e89dc919b1e8cb40e0161efd2758ed289f` |
| `static/workspace.css` | `113010f4c988638e8c032755993e7c101d6c9b9c94a5af007d589c0fee6ebc22` |
| `static/index.html` | `f6434b94cdf673726346abeafa8ee9f9bd8f0814648a72bba36af278d8860bd0` |
| `vendor/xterm.js` | `1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495` |
| `vendor/xterm.css` | `ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6` |
| `vendor/xterm-addon-fit.js` | `bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089` |
| `vendor/pdf.min.js` | `5b5799e6f8c680663207ac5b42ee14eed2a406fa7af48f50c154f0c0b1566946` |
| `vendor/pdf.worker.min.js` | `feabdf309770ed24bba31a5467836cdc8cf639c705af27d52b585b041bb8527b` |
| `vendor/mammoth.browser.min.js` | `deb07bf230d1cb3e190bc5adc6743f35c6531b6571d1e5469b24f452a7f0f4ab` |
| `vendor/seti/seti.js` | `c0d908d2b9b1809babe659bc2f6fbb37b5d8454dc91978eb5055d1df81113150` |
| `vendor/seti/seti.woff` | `b127762058f89b37b08d76185e3b0558f5c28859e39a3840a27cb28b9739d6e2` |
| `vendor/seti/LICENSE.txt` | `64ea77f841c630b2e5bcd524212754877e47d64b66407466205870b818753546` |

Server modules: `files.py`, `terminal.py`, `workspace.py`, the Workspace sections of `api.py`
(`ws_admin`, request models, `# --- file manager`, `# --- workspace: terminals`, `# --- workspace: Claude
Code panel`), `projects.save_upload/uploads/upload_path`, `transport.nginx_conf`.

Check a copy: `cd src/agent_company/static && sha256sum workspace.js viewer.js console.js claude.js workspace.css index.html vendor/*.js vendor/*.css vendor/seti/*` and compare with the table (a later change to the code changes its hash; update this table with it).

## 15. Verification on the new computer

1. `uv sync && uv run pytest -q tests/test_workspace.py tests/test_files.py` — all pass (offline, temp `DATA_DIR`).
2. Checksums of [§14](#14-files-and-vendored-assets) match.
3. Sign in as the admin; the Workspace is the first page; the explorer lists the projects and `data`.
4. *Open folder…* any folder; it stays after a reload (DB setting `files.extra_roots`).
5. The terminal shows the coloured prompt with the git branch; `claude --version` works in it; reload the page — the same shell comes back with its output.
6. From a second computer over nginx: the terminal connects (no 4403), uploads of 50 MB pass (`client_max_body_size 100m`).
7. Send "Explain how this project is structured" in the Claude panel: tool cards stream, "✓ Done" appears, the tab gets a short title; `/status` shows the account and plan limits.
8. Audit log lists `terminal_open`, `workspace_claude`, `file_write` for these actions.
