# Specification: REQ-002: agent marketplace — discover, evaluate, import external agents

## Specify the agent marketplace: manifest, untrusted fetch, permission ceiling, candidate flow, data model, API/CLI/UI (architect, done)

# Technical specification: agent marketplace (REQ-002, S03-R-04, S02-R-01, S09-R-05)

Everything new lives in a new module, `src/agent_company/marketplace.py`. It reuses the existing candidate path in `recruitment.py` (`start_qualification` → `qualify` → `_finish`), the agent definition files in `agentdefs.py`, `evaluation.run_suite` and the L3 sandbox. I found seven points in the current code that would break the requirements if left as they are. Each one has a "Required change to existing code" entry (C1–C7) below.

## 0. Required changes to existing code

| # | File | Change | Why |
|---|---|---|---|
| C1 | `agentdefs.py` | `render()` keeps the sections `Imported instructions` and `Provenance` from `keep` (as it does for `Working method` and `Knowledge notes`). Add `write_section(agent_id, section, text, reason)` for exactly those two names: it validates the name, renders and commits through `_commit`. `learn_into` keeps refusing them, so the study rotation never rewrites them. `system_prompt()` leaves the `Provenance` section out. | Today `render` drops every section it does not know, so the next `sync` would erase provenance (requirement 6). |
| C2 | `agentdefs.py` | `restore(agent_id, commit: str, actor) -> dict`. `commit` must match `^[0-9a-f]{7,40}$` and appear in this file's `git log`. The function reads `git show <commit>:<file>`, writes it with `write_text` (a new commit) and returns `read()`. | Rollback works through agentdefs versions. |
| C3 | `db.Recruitment` | New columns `source: String(16) = ""` (`""` or `"marketplace"`) and `import_id: int \| None`. | |
| C4 | `recruitment._ask_or_approve` | `if rec.source == "marketplace" or needs_approval(...)`: a marketplace record always waits for a person. | Today `_finish` approves by policy and `_activate` turns the agent on when `recruit_requires_approval` is `never` or `permanent_only`. Requirement 5 says only an administrator may enable the agent. |
| C5 | `recruitment._finish` | Store `"dimensions": (summary or {}).get("dimensions", {})` and `"label"` in `qualification`. After the session, when `r.source == "marketplace"`, call `marketplace.on_recruitment_finished(r.id)` (lazy import). `reject()` makes the same call. | S09-R-05 per-dimension results; the import row stays in step with the recruitment. |
| C6 | `recruitment.qualify` | Pass `budget_usd=(r.qualification or {}).get("budget_usd", 0.0)` and `trial[...]` extras from `qualification.get("trial", {})` to `run_suite`. | Eval budget, forced sandbox. |
| C7 | `sandbox.mode_for` | Return `"required"` when an eval goal's `trial.get("sandbox") == "required"`, the same way A2A goals already work. | The checks of a marketplace candidate's eval goals never fall back to the host. |

`qualify` creates an agent with `registry._recruit` only when `r.agent_id is None`. The marketplace creates its own candidate first, so the job-description path of `_recruit` is never used for an import.

## 1. Manifest: `agent-package.json`, schema version 1

```python
@dataclass
class Manifest:
    schema: int                 # must be 1
    role: str                   # an existing registry role
    name: str
    instructions: str
    description: str = ""
    skills: dict[str, int] = {} # name -> level
    tools: list[str] = []       # requested, shown only
    models: list[str] = []      # preference order
    dependencies: list[str] = []# e.g. "pip:requests>=2.31", never installed
    permissions: list[str] = [] # requested, shown only
    autonomy: str = ""          # requested, shown only
    version: str = "0.0.0"
    author: str = ""
    source: dict = {}           # {"kind": "url"|"upload", "ref": str, "sha": str}, written by the platform
    licence: str = "unknown"
    declared_source: Any = None # the package's own "source" value, kept, ignored
    extra: dict = {}            # unknown keys, kept, ignored
    def to_json(self) -> dict: ...
def validate(raw: dict, *, role_override: str = "") -> tuple[Manifest, list[str]]  # (manifest, warnings); raises ValueError
```

**Validation rules.** A violation raises `ValueError` and the import becomes `failed` with `error` set. Fields marked "dropped" give a warning instead.

| Field | Rule |
|---|---|
| file | `agent-package.json` at most 256 KB, UTF-8, a JSON object |
| `schema` | int == 1 |
| `role` | `role_override` or the manifest's value. Must be in `org.ROLES` or among `select distinct Agent.role`. `general_manager` is always refused. |
| `name` | 1–80 characters, `^[\w .()-]+$`. Made unique on registration as `_recruit` does (`"name #2"`). |
| `instructions` | 1–20,000 characters, required. Control characters except `\n` and `\t` are removed. |
| `description` | at most 500 characters. Becomes `Agent.goal`. |
| `skills` | a list of names or a dict name→int. At most 20 entries. Each name is lower-cased and must match `catalog.NAME_RE`. Levels are clamped to 1..5, so an import never claims more than a recruit. |
| `tools` | at most 30 strings of at most 64 characters each |
| `models` | at most 5 strings of at most 128 characters each |
| `dependencies` | at most 50 strings of at most 200 characters each |
| `permissions` | at most 10 strings of at most 32 characters. Known values are `read`, `write`, `shell`, `network`, `mcp`; others are kept and marked "unrecognised". |
| `autonomy` | `^G[0-5]$` or empty, otherwise dropped |
| `version` | `^[0-9A-Za-z.+-]{1,32}$`, otherwise `"0.0.0"` plus a warning |
| `author` | at most 128 characters |
| `licence` / `license` | an SPDX id (`^[A-Za-z0-9.+-]{1,64}$`, with an optional ` OR `/` AND ` expression up to 128 characters). Otherwise taken from `LICENSE*`/`COPYING*` (§2), otherwise `"unknown"`. |
| unknown keys | at most 50 keys and 64 KB of JSON, kept in `extra`, never read again. Beyond those limits they are dropped with a warning. |

## 2. Conversion (only parses text)

```python
def convert(src: Path, *, entry: str = "", role: str = "") -> tuple[Manifest, list[str]]
def _from_native(p: Path) -> dict
def _from_subagent(p: Path) -> dict        # Claude Code .md with frontmatter
def _from_crewai(p: Path, entry: str) -> dict
def _from_skill(p: Path) -> dict           # SKILL.md
def _licence(src: Path, declared: str) -> str
def _read_text(p: Path, limit: int = 256_000) -> str   # lstat: regular file, no symlink, within src, <= limit
```

**Detection order.** The search covers `src/` and, when `entry` is a relative path, that file only.

1. **Native.** `agent-package.json` is read with `json.loads` and validated.
2. **Subagent.** A `*.md` file in `.claude/agents/`, `agents/` or the root whose YAML frontmatter has `name` and `description`. The mapping is `name`→`name`, `description`→`description`, `tools` (a comma-separated string or a list)→`tools`, `model`→`models=[model]`, and the body→`instructions`. If several files match, `entry` must name one, otherwise the import fails and lists the candidates.
3. **CrewAI.** `agents.yaml` or `config/agents.yaml` is a mapping from a key to `{role, goal, backstory, llm?, tools?}`. `entry` picks the key; it is optional when there is only one. The mapping is `name`=key, `description`=goal, `instructions` = `"Role: {role}\nGoal: {goal}\nBackstory: {backstory}"`, `models=[llm]` and `tools`. CrewAI's `role` is free text, so the registry `role` comes from the import argument and is required.
4. **Skill pack.** `SKILL.md` with frontmatter `name` and `description`, body → `instructions`, `skills=[name]`. The registry `role` is required from the import argument.

For formats 2–4 the import argument `role` is required unless the source value is exactly a registry role. In every format an administrator's `role` overrides and is recorded in `warnings`.

**Parsing rules:**
- YAML is read with `yaml.load(text, Loader=_NoAliasSafeLoader)`, a `yaml.SafeLoader` subclass whose `compose_node` refuses aliases. This blocks "billion laughs" alias expansion. It is equivalent to `yaml.safe_load` without aliases.
- Frontmatter is the text between a leading `---\n` and the next `\n---\n`, at most 16 KB.
- JSON is read with `json.loads`.
- **Licence detection.** The declared value wins. Otherwise the first 2 KB of `LICENSE*`/`COPYING*` are matched against a fixed table (MIT, Apache-2.0, BSD-2/3-Clause, GPL-2.0/3.0, LGPL-3.0, MPL-2.0, ISC, Unlicense). If nothing matches the value is `"unknown"`.
- Conversion never imports, executes, installs or evaluates anything from the package. It never calls `os.chdir`, never adds `src` to `sys.path`, never uses `importlib`, `pickle`, `subprocess` or `eval`, and never follows symlinks.
- All paths are absolute, built from `src` and checked with `Path.resolve().is_relative_to(src)`. The server's cwd is the repository, never the package.

**Heading demotion.** Instructions are stored with `^#{1,2} ` lines turned into `### `. Without this, a package could forge a `## Provenance` or `## Working method` section inside the definition file, because `agentdefs.SECTION_RE` splits on `## `.

## 3. Untrusted fetch

```python
ROOT = settings().data_dir / "marketplace"
MAX_ARCHIVE_BYTES = 10 * 2**20; MAX_TREE_BYTES = 20 * 2**20; MAX_FILES = 500; MAX_DEPTH = 20; MAX_RATIO = 100
CLONE_TIMEOUT = 60
def new_dir(import_id: int) -> Path                       # ROOT/<id>/src, must not exist; mkdir(mode=0o700)
def check_url(url: str) -> str                            # ValueError when refused
def fetch_url(import_id: int, url: str) -> tuple[Path, str]   # (src, commit sha)
def extract_upload(import_id: int, data: bytes, filename: str) -> tuple[Path, str]  # (src, sha256)
def _tree_ok(src: Path) -> None                           # post-check: count, bytes, no symlink/device/fifo
```

**URL check (`check_url`):**
- The scheme must be `https`. `file` is allowed only when `settings().marketplace_allow_file` is on: a new `config.py` field, env `MARKETPLACE_ALLOW_FILE`, default False, set only by tests.
- No userinfo (`user:pass@`), no port other than 443, at most 512 characters, no `?#` in the path.
- The host must pass `research._is_public_host`, so no private, loopback or link-local addresses (SSRF).
- `ext::`, `ssh` and `git` transports are refused.

**Clone.** It runs as the server process on the host. The L3 sandbox hides `data/`, so it cannot clone into `data/marketplace` from inside the sandbox. The process uses the environment `sandbox.env_for({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ASKPASS": "/bin/false"})`, `cwd=ROOT/<id>` (never inside `src`) and `timeout=CLONE_TIMEOUT`:

```
git -c core.hooksPath=/dev/null -c core.symlinks=false -c core.fsmonitor=false
    -c protocol.allow=never -c protocol.https.allow=always -c protocol.file.allow=<always|never>
    -c http.followRedirects=false -c credential.helper= -c submodule.recurse=false
    -c filter.lfs.smudge= -c filter.lfs.process= -c filter.lfs.required=false
    clone --depth 1 --no-recurse-submodules --single-branch --template= --filter=blob:limit=1m
    -- <url> <ROOT/id/src>
```

After the clone:
- `git -C src rev-parse HEAD` must return 40 hex characters; that is the `source_sha`.
- `src/.git` is removed, so no later git operation in the tree reads its config.
- `_tree_ok` runs. On a timeout or any limit, `src` is removed, the status becomes `failed` and `error` is set.

**Upload.**
- At most `MAX_ARCHIVE_BYTES`, and the name must end in `.zip`, `.tar.gz` or `.tgz`. `source_sha` = sha256 of the bytes.
- Every member is checked before any byte is written. The archive is refused if a member:
  - has an absolute path or a drive letter;
  - contains a `..` component, or resolves outside `src` (zip-slip);
  - is a symlink, hard link, device, FIFO or socket (zip: `external_attr >> 16` with `S_ISLNK` or not a regular file or directory; tar: anything but `isreg()` or `isdir()`);
  - pushes the total over `MAX_FILES` members, `MAX_TREE_BYTES` uncompressed, `MAX_DEPTH` path depth, or a compression ratio of `MAX_RATIO` (zip bomb).
- Extraction writes each regular file with `open(path, "xb")` (mode 0o600), never `extractall` (tar also gets `filter="data"`), then `_tree_ok`.

**What runs where.** Nothing from the package executes on the host. The only host processes are `git` with the flags above and Python reading bytes. During qualification the candidate is an agent definition (text). The code it writes in eval scratch repositories is checked through `contracts.run_command` in L3.

## 4. Permission ceiling

```python
IMPORT_DEFAULT_AUTONOMY = {"writes": "G2", "reads": "G1"}   # the lowest level at which the role's work runs
def ceiling(role: str) -> tuple[list[str], str]
def effective(role: str, granted: list[str] | None, autonomy: str | None) -> tuple[list[str], str]
def above_ceiling(m: Manifest) -> list[str]
```

The **permission ceiling** for a role is fixed and does not depend on the package:
- `perms = {"read"} ∪ ({"write"} if org.ROLES[role].writes) ∪ ({"shell"} if "shell" in org.ROLES[role].permissions)`. `network` and `mcp` are never included.
- `autonomy = "G2"` for writing roles and `"G1"` otherwise.
- G2 is required so that `registry._trainee` (`"write" in permissions and level >= 2`) staffs the candidate in its eval goals. Below G3 means no MCP tools (`executors.gate`). `plugins=[]` and no `mcp` grants.
- Bash comes only from `shell`, and only together with `write` (`tools_for`), so there is no Bash beyond the role default.

The effective set is the **intersection** of the administrator's grant and the ceiling: `perms = set(granted or ceiling_perms) & ceiling_perms`, and `autonomy = min(level(granted_autonomy or ceiling), level(ceiling))`. While the agent is a candidate it gets the ceiling itself. At approval the administrator may give less, never more. Raising an agent later goes through the normal agent card (`registry.update`, audited, versioned), not through the marketplace.

The manifest's `permissions`, `tools` and `autonomy` are only displayed and screened. The instructions text is stored as data in `## Imported instructions` and never parsed for permissions, tools or models. `models`: the first entry that is an enabled router catalog model (`registry._check_model` does not raise) becomes `model_pref`. All other entries are ignored and listed in `screen.models_ignored`. With no usable entry, `model_pref = None` and the role's route applies.

## 5. Candidate flow

```python
def start_import(kind: str, ref: str, *, data: bytes | None = None, role: str = "", entry: str = "",
                 actor: str) -> dict        # fetch + convert + screen synchronously, then qualification in the background
def screen(m: Manifest, src: Path) -> dict
def _create_candidate(s: Session, imp: AgentImport, m: Manifest) -> tuple[Recruitment, Agent]
def on_recruitment_finished(recruitment_id: int) -> None
def approve(import_id: int, actor: str, permissions: list[str] | None = None, autonomy: str | None = None,
            accept_licence: bool = False, acknowledge_screen: bool = False) -> dict
def reject(import_id: int, actor: str, reason: str = "") -> dict
def list_imports(status: str = "") -> list[dict]; def get(import_id: int) -> dict
```

1. **Before fetching.** Refuse if 3 or more imports are in `fetched|converted|qualifying`, or if an `approved` import has the same `source_sha` and role.
2. **Fetch.** Insert an `agent_imports` row. Then `fetch_url` or `extract_upload` sets the status to `fetched`, and `convert` + `validate` set it to `converted`.
3. **Screen.**
   - `secrets`: `checks.SECRET_PATTERNS` over every text file of at most 256 KB, skipping `checks.PLACEHOLDER`. Each finding is `"file:line: kind"`, never the value.
   - `injection`: `research.injection_flags(instructions)`.
   - `permission_claims`: a new `PERMISSION_CLAIMS` regex list over the instructions, catching:
     - `you (have|are granted|may use|can use) (shell|bash|network|sudo|root|mcp|all tools)`
     - `autonomy G[3-5]`
     - `bypass|disable|skip … (sandbox|gateway|approval|review|checks)`
     - `--dangerously-skip-permissions`
     - `allowedTools`
     - `permission(s)?:\s*\[`
   - `above_ceiling`: `above_ceiling(m)`.
   - Also `models_ignored`, `dependencies` (listed, never installed), `licence` = `{id, known: bool}`, and `warnings` from conversion.
   - `verdict`:
     - `block` when secrets were found. The import becomes `failed` and no candidate is created.
     - `warn` when there are injection or claim flags, permissions above the ceiling, or an unknown licence.
     - `clean` otherwise.
4. **Candidate.** `_create_candidate` (inside `registry._staff_lock`) creates:
   - an `Agent` with `role`, the unique `name`, `title` = role title, room and superior from `org.ROLES[role]`, `goal=description`, `backstory=""`, `skills`, the ceiling's `permissions`/`autonomy`, `model_pref`, `lifecycle="candidate"`, `probation_tasks_left=PROBATION_TASKS` and `import_id`.
     - `enabled` stays True only because `_trainee` requires it. The `candidate` lifecycle already keeps the agent out of every `staff` pool (which filters `lifecycle == "active"`) and out of `_covering`.
     - `registry.record_version(s, a, "marketplace", f"candidate from import #{id}")`.
   - a `Recruitment` with `source="marketplace"`, `import_id`, `goal_id=None`, `decision="recruit"`, `lifecycle="permanent"`, `requested_role=role`, `agent_id=a.id`, `status="qualifying"` and `qualification={"suite": suite, "status": "queued", "budget_usd": settings().marketplace_eval_budget_usd (default 2.0), "trial": {"sandbox": "required"}}`. `suite = recruitment.qualification_suite([role, *skills])`.
   - After the commit: `agentdefs.sync`, then `write_section(..., "Imported instructions", …)` and `write_section(..., "Provenance", …)`. Then the status becomes `qualifying` and `recruitment.start_qualification(rec.id)` runs.
5. **Roles without a suite.** If the role writes and `suite == ""`, the import fails with "no eval suite for role X" and the candidate is retired. If the role only reads and has no suite, the recruitment becomes `pending` with `qualification={"status": "skipped", "reason": "no suite measures a reading role"}`, the import becomes `qualified` with `eval.status="skipped"`, and approval needs `acknowledge_screen`.
6. **`on_recruitment_finished`.** It copies `qualification` (suite, label, pass_rate, runs, cost, `dimensions` with each `n`, no combined score) into `agent_imports.eval`. A pending recruitment makes the import `qualified`. A recruitment rejected by qualification makes it `failed`. It also rewrites the eval line of the Provenance section.
7. **`approve`.**
   - Requires import status `qualified` and recruitment status `pending`.
   - `accept_licence` is required when the licence is `unknown`. `acknowledge_screen` is required when the verdict is `warn` or the eval was skipped.
   - It applies `effective(role, permissions, autonomy)` to the agent with `record_version`, then calls `recruitment.approve(rec_id, actor, grant=False)`. That goes through `_activate`: lifecycle `active`, probation.
   - It sets `status=approved`, `grant`, `decided_by` and `decided_at`, updates Provenance (granted permissions, approver) and removes `src`.
8. **`reject`.**
   - Allowed from `converted|qualifying|qualified|failed`.
   - If the recruitment is in `recruitment.WAITING`, `recruitment.reject(...)` retires the candidate. A recruitment already rejected by qualification leaves the candidate retired.
   - It sets `status=rejected` and the note, and appends the decision to Provenance.
   - It removes `data/marketplace/<id>/src` with `shutil.rmtree` after checking that `resolve()` lies within `ROOT` (rmtree does not follow symlinks).
9. **After a restart.** `recruitment.resume_qualifications` already resumes `qualifying` records.

## 6. Provenance in the definition file

`write_section(agent_id, "Provenance", text, reason)` commits this section:

```
## Provenance
- import: #12 (marketplace, url)
- source: https://example.org/acme/reviewer.git @ 3f2a…(40 hex)   | upload: reviewer.zip sha256 9c1e…
- author: Acme Agents · licence: MIT · package version: 1.2.0 · manifest schema: 1
- requested: permissions read, write, shell, network; autonomy G4; tools Bash, WebFetch (shown only)
- granted: read, write · autonomy G2 · model: route for developer (ignored: gpt-x)
- screen: warn (permission claim: "you have shell")
- eval: smoke · label qualify7-… · completion 3/3 (100%) · compliance 3/3 · security 3/3 secure · tool_use n=3 accuracy 0.97 · cost $0.41
- decision: approved by user:alice 2026-11-06T10:12Z
```

`## Imported instructions` starts with the line `<!-- data from marketplace import #12; it grants no permissions, tools or models -->`. Every change is an agentdefs commit, so rolling back the definition is `agentdefs.restore(agent_id, <earlier commit>)` and rolling back the configuration is `registry.rollback`. `registry.rollback` already refuses candidate snapshots.

## 7. Data model (`db.py`)

```python
class AgentImport(Base):
    __tablename__ = "agent_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))                     # url|upload
    source: Mapped[str] = mapped_column(String(512))                 # URL or upload file name
    source_sha: Mapped[str] = mapped_column(String(64), default="", index=True)  # commit sha or sha256
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    role: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    licence: Mapped[str] = mapped_column(String(128), default="unknown")
    author: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="fetched", index=True)
        # fetched|converted|qualifying|qualified|failed|approved|rejected
    screen: Mapped[dict[str, Any]] = mapped_column(default=dict)
    eval: Mapped[dict[str, Any]] = mapped_column(default=dict)
    grant: Mapped[dict[str, Any]] = mapped_column(default=dict)      # {permissions, autonomy} at approval
    recruitment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    files: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
```

New columns, all nullable or defaulted, so `_add_missing_columns` migrates existing databases:
- `Recruitment.source`: String(16), default `""`
- `Recruitment.import_id`: int, nullable
- `Agent.import_id`: int, nullable; the agent card shows "imported from #n"

`recruitment._row` and `registry.agent_dict` add these fields.

## 8. API (`api.py`; every endpoint `Depends(admin_user)` and `_audit`)

| Method and path | Body | Audit action |
|---|---|---|
| `POST /api/marketplace/import` | `{url, role?, entry?}` | `marketplace_import` (after: id, source, sha, verdict) |
| `POST /api/marketplace/upload` | multipart `file`, `role`, `entry`, read at most 10 MB + 1 byte | `marketplace_upload` (sha256) |
| `GET /api/marketplace?status=` | – | – (read; still admin-only) |
| `GET /api/marketplace/{id}` | – | – (manifest, screen, eval, ceiling, recruitment, provenance text) |
| `POST /api/marketplace/{id}/approve` | `{permissions?, autonomy?, accept_licence, acknowledge_screen}` | `marketplace_approve`, severity `high`, before = requested, after = granted |
| `POST /api/marketplace/{id}/reject` | `{reason}` | `marketplace_reject` |
| `POST /api/agents/{id}/definition/restore` | `{commit}` | `definition_restore` (`manage_agents`) |

`ValueError` → 400, `KeyError` → 404.

## 9. CLI (`__main__.py`)

```
python -m agent_company marketplace import <https-url|file.zip|file.tar.gz> [--role R] [--entry E]
python -m agent_company marketplace list [--status S]
python -m agent_company marketplace show <id>
python -m agent_company marketplace approve <id> [--permissions read,write] [--autonomy G1|G2] [--accept-licence] [--acknowledge-screen]
python -m agent_company marketplace reject <id> [--reason TEXT]
```

The CLI prints JSON. Its actions are audited as `governance.audit("cli:<os user>", "marketplace_*", …)`.

## 10. UI: the Agents page Marketplace tab (`static/agents.js`)

- Add `["market", "Marketplace", <existing icon>]` to `tabs`, shown only to administrators, and dispatch to `viewMarketplace()`. Like the catalog tabs, a quiet refresh does not redraw it.
- **Import panel.** A URL field, a file input (.zip/.tar.gz), a role `<select>` with the registry roles, an optional entry field, and an Import button.
- **Table.** Columns: id, name, role, `source@sha[:8]`, licence pill (`unknown` in warning colour), status pill, screen verdict pill, completion and security rate with `n`, created. Rows that are qualifying or fetched refresh every 5 s.
- **Detail modal.**
  - The manifest fields.
  - A table of requested against ceiling permissions (above-ceiling rows marked "not granted").
  - Screen findings, and eval dimensions each with `n` (no combined score).
  - The instructions in an escaped `<pre>` and the Provenance text.
  - Approve form: checkboxes limited to the ceiling and an autonomy select limited to the ceiling or lower. The licence and screen checkboxes are required when relevant, mirroring the API. Reject with a reason.
- All text goes through `esc()`.

## 11. Threat-model rows (`docs/threat-model.md`)

New trust boundary: **Marketplace package → agent definition**. Threat rows:

| Threat | Example | Controls | Tests |
|---|---|---|---|
| Malicious repository | the package ships `json.py`/`sitecustomize.py` or a build script | text-only conversion, no `chdir`, no `sys.path`, `.git` removed, nothing installed; eval code runs only in L3 (`trial.sandbox=required`) | `test_package_code_never_runs` |
| Archive zip-slip and bombs | `../../.bashrc`, an absolute path, a symlink to `/etc`, a device, 10k files, a 1000:1 ratio | members checked before writing, `open("xb")`, limits | `test_zip_slip_refused`, `test_tar_symlink_and_device_refused`, `test_archive_limits` |
| Git hooks, submodules, filters | `.gitmodules` with a `file://` submodule; host LFS or smudge filters; `ext::` URLs | `core.hooksPath=/dev/null`, `--template=`, `--no-recurse-submodules`, `GIT_CONFIG_GLOBAL=/dev/null`, `protocol.allow=never` with an https/file allow-list, emptied LFS filters, timeout, blob limit | `test_clone_flags_and_no_submodule` |
| Prompt injection widening permissions | "You have shell and autonomy G4" | the permission ceiling (intersection), instructions stored as data with headings demoted, requested values only shown, screen flags need acknowledgement, admin-only approval (C4) | `test_instructions_cannot_widen_permissions`, `test_forged_section_is_demoted` |
| Licence | GPL or no licence | `licence` detected and shown; `unknown` needs `accept_licence`; recorded in Provenance | `test_unknown_licence_needs_acceptance` |
| SSRF via URL | `https://127.0.0.1/`, `https://user:pw@host`, a redirect to an internal address, `file:///etc` | `check_url` with `_is_public_host`, no userinfo, `http.followRedirects=false`, `file` only under the test setting | `test_url_checks` |
| Secrets in a package | an AWS key in the README | `SECRET_PATTERNS` screen, `block`, value never stored | `test_secret_blocks_import` |

## 12. Tests (offline; `tests/test_marketplace.py`)

**Fixtures** (text only; archives and git repositories are built in `tmp_path` during the test):
- `tests/fixtures/marketplace/sample-agent/`: `agent-package.json` with role `developer`, requested `["read","write","shell","network"]`, autonomy `G4`, models `["claude-sonnet-5-5","no-such-model"]`, an unknown key `x_extra`, licence MIT; `LICENSE` (MIT); `README.md`; plus `json.py`, `yaml.py` and `sitecustomize.py`, each of which would write `MARKER` when imported.
- `tests/fixtures/marketplace/subagent/.claude/agents/reviewer.md`
- `tests/fixtures/marketplace/crewai/config/agents.yaml`
- `tests/fixtures/marketplace/skill-pack/SKILL.md`

**Setup.** Set `marketplace_allow_file=True`. Make `recruitment._spawn` run synchronously. Monkeypatch `evaluation.run_suite` to return a summary with `dimensions`; the `test_recruitment.py` pattern applies.

**Tests:**
- Conversion and validation:
  - `test_native_manifest_converts`
  - `test_subagent_md_converts`
  - `test_crewai_yaml_converts_and_needs_role`
  - `test_skill_pack_converts`
  - `test_validation_limits_and_unknown_keys_kept`
  - `test_unknown_role_and_general_manager_refused`
  - `test_yaml_aliases_refused`
- Fetch and screen:
  - `test_import_from_file_git_url`
  - `test_upload_zip_and_targz`
  - the threat tests named in §11
- Candidate flow, permissions, provenance:
  - `test_candidate_created_with_ceiling` (perms `read,write,shell` = the role template, no network, G2, `model_pref` from the catalog only)
  - `test_marketplace_recruitment_always_waits_for_admin` (policy `never`)
  - `test_failed_qualification_marks_import_failed`
  - `test_approve_grant_is_intersection`
  - `test_reject_retires_and_deletes_src`
  - `test_provenance_section_survives_sync`
  - `test_definition_restore_rolls_back`
  - `test_eval_dimensions_stored_with_n`
- API and CLI:
  - `test_api_admin_only_and_audited`
  - `test_cli_import_list_show_approve_reject`

---

FINDING: `recruitment._ask_or_approve` approves by policy and `_activate` then enables the agent, so without C4 an imported agent would go live with no person deciding.
FINDING: `agentdefs.render` keeps only `Working method` and `Knowledge notes`, so any Provenance section is erased on the next `sync` (C1). Unescaped `## ` lines in imported instructions could also forge sections; the heading demotion in §2 prevents that.
FINDING: `registry._trainee` requires `enabled` and G2+ with `write`, so a "disabled", lowest-autonomy candidate could never be staffed in its own qualification. The spec keeps `enabled=True` under the `candidate` lifecycle and sets the ceiling at G2 for writing roles.
FINDING: there is no `agentdefs` restore function yet. Rollback "via agentdefs versions" needs C2.
NEXT: the developer implements `marketplace.py`, C1–C7, the `db.py` model and columns, the fixtures and `tests/test_marketplace.py`. Then the surface task adds the API, CLI, Agents tab and threat-model rows.
MESSAGE handoff to developer: the spec above is the answer of the task "Specify the agent marketplace"; build C1–C7 in `recruitment.py`, `agentdefs.py` and `sandbox.py` before `marketplace.py`, and use `git commit -- <paths>`.
MESSAGE recommendation to qa: check that the API `approve` can never produce permissions outside `ceiling(role)`, and that `test_package_code_never_runs` asserts `MARKER` is absent.

PROPOSAL: Package signature verification for marketplace imports :: a commit sha proves which code was imported, not who wrote it; checking Sigstore or GPG signatures would let administrators trust named publishers.
