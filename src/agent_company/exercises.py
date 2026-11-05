"""Generated practice exercises and final evaluation sets (SPEC-13 S13-R-02).

A person asks for exercises for a skill (`learn generate`, Evolution → Learning plans); the instructor writes them
in the eval-suite format with a reference solution per task. The harness, not the model, decides what is kept: a task
stays only when its text passes the knowledge screen, its commands pass the command policy, at least one of its
checks fails on the bare seed files (the exercise tests something) and all of them pass once the reference solution
is laid over the seed (it can be solved). Both runs happen in a scratch directory under `data/worktrees/` through
`contracts.run_command`, so model-written commands get the L3 sandbox like any check. A final set must not repeat a
title or a check of the skill's practice suites (its checks never reach a trainee, LE-021).

Kept tasks are written to `data/evals/<name>.json` (never into the repository's `evals/`); the reference solutions
go to `data/evals/solutions/`, which no prompt reads. A generated practice suite can be used at once; a generated
final set certifies agents, so it is a draft until an administrator approves it (`approve`). Generation costs model
money, so nothing generates on its own: gaps only point at it.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.config import settings
from agent_company.db import log_event, now

KINDS = ("practice", "final")
MAX_TASKS, MAX_FILES, MAX_FILE_CHARS, MAX_CHECKS = 6, 10, 20_000, 5
CHECK_TIMEOUT = 60
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


def generated_dir() -> Path:
    return settings().data_dir / "evals"


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _prompt(skill: str, kind: str, count: int, existing: list[str]) -> str:
    hidden = ("This is a FINAL evaluation set: the trainee never sees the checks, so the description must state "
              "everything the checks verify. Make the tasks a little harder than practice. "
              if kind == "final" else "These are PRACTICE exercises: the trainee may see the checks and retry. ")
    return (f"Write {count} small, self-contained programming exercises that train and test the skill '{skill}'. "
            + hidden +
            "Each exercise starts from a tiny repository (`files`), is solved by changing it (`solution`: the full "
            "text of every file the reference solution writes), and is checked by shell commands (`checks`) that exit 0 "
            "only when the exercise is solved, e.g. python3 -c \"from mod import f; assert f(2) == 4\". Checks must not "
            "need the network, must fail on the starting files, and must run in under a minute.\n"
            + (f"Do not repeat these existing exercises: {'; '.join(existing[:40])}.\n" if existing else "")
            + "Answer with ONE fenced ```json block: {\"tasks\": [{\"name\": \"kebab-case\", \"title\": ..., "
              "\"description\": ..., \"files\": {path: text}, \"solution\": {path: text}, \"checks\": [cmd, ...]}]}")


def _files(value: Any, what: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_FILES:
        raise ValueError(f"{what} must be a map of at most {MAX_FILES} files")
    out = {}
    for rel, text in value.items():
        rel = str(rel).strip()
        parts = Path(rel).parts
        if not rel or rel.startswith(("/", "~")) or ".." in parts or (parts and parts[0] == ".git"):
            raise ValueError(f"{what}: path not allowed: {rel[:80]}")
        if not isinstance(text, str) or len(text) > MAX_FILE_CHARS:
            raise ValueError(f"{what}: {rel[:80]} must be text of at most {MAX_FILE_CHARS} characters")
        out[rel] = text
    return out


def _shape(raw: Any) -> dict[str, Any]:
    """A task as the model wrote it, checked for form; raises ValueError with the reason it is dropped."""
    from agent_company.governance import check_command
    from agent_company.learning import screen

    if not isinstance(raw, dict):
        raise ValueError("not an object")
    name = str(raw.get("name") or "").strip().lower()
    if not _NAME.match(name):
        raise ValueError(f"bad name {name[:40]!r}")
    title, description = str(raw.get("title") or "").strip(), str(raw.get("description") or "").strip()
    if not title or len(title) > 200 or len(description) > 2000:
        raise ValueError("needs a title (at most 200 characters) and a description of at most 2000")
    checks = raw.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS or \
            not all(isinstance(c, str) and c.strip() and len(c) <= 500 for c in checks):
        raise ValueError(f"needs 1 to {MAX_CHECKS} check commands")
    for c in checks:
        refused = check_command(c)
        if refused:
            raise ValueError(f"check refused by the command policy: {refused}")
    files, solution = _files(raw.get("files") or {}, "files"), _files(raw.get("solution") or {}, "solution")
    if not solution:
        raise ValueError("no reference solution")
    status, why = screen("\n".join([title, description, *files.values()]))  # the text reaches agents' prompts
    if status != "approved":
        raise ValueError(f"text {status}: {why}")
    return {"name": name, "title": title, "description": description, "files": files, "solution": solution,
            "checks": [c.strip() for c in checks]}


def _lay(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            raise ValueError(f"path escapes the scratch directory: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _verify(task: dict[str, Any]) -> None:
    """Run the checks on the bare seed (one must fail) and on seed + solution (all must pass)."""
    from agent_company.contracts import run_command

    root = settings().worktrees_dir / f"exercise-{task['name']}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    try:
        _lay(root, task["files"] or {"README.md": f"# {task['name']}\n"})
        if all(run_command(c, root, CHECK_TIMEOUT)[0] for c in task["checks"]):
            raise ValueError("every check passes on the starting files: the exercise tests nothing")
        _lay(root, task["solution"])
        for c in task["checks"]:
            ok, out = run_command(c, root, CHECK_TIMEOUT)
            if not ok:
                raise ValueError(f"the reference solution fails `{c[:120]}`: {out[-300:]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _existing(skill: str) -> tuple[set[str], list[str], set[str], set[str]]:
    """(task names, titles, practice titles, practice checks) of the suites that name the skill."""
    from agent_company import evaluation
    from agent_company.training import _slug

    names: set[str] = set()
    titles: list[str] = []
    p_titles: set[str] = set()
    p_checks: set[str] = set()
    for x in evaluation.list_suites():
        if _slug(skill) not in {_slug(k) for k in x["skills"]}:
            continue
        data = evaluation.load_suite(x["name"])
        for t in data["tasks"]:
            names.add(t["name"])
            titles.append(t["title"])
            if x["kind"] == "practice":
                p_titles.add(_norm(t["title"]))
                p_checks.update(_norm(c) for c in t.get("checks") or [])
    return names, titles, p_titles, p_checks


def generate(skill: str, kind: str = "practice", count: int = 3, requested_by: str = "system") -> dict[str, Any]:
    """Ask the instructor for `count` exercises, keep the ones the harness verifies and store them as a suite."""
    from agent_company import executors, router, usage
    from agent_company.db import Agent, session
    from agent_company.training import _slug

    skill = _slug(skill)
    if not skill:
        raise ValueError("name the skill to generate exercises for")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    count = max(1, min(MAX_TASKS, int(count)))
    names, titles, p_titles, p_checks = _existing(skill)
    with session() as s:
        judge = s.scalars(select(Agent).where(Agent.role == "instructor").order_by(Agent.id)).first()
        if judge is None:
            raise ValueError("no instructor agent writes exercises")
        author_id, author, pref = judge.id, judge.name, judge.model_pref
    choice = router.resolve("instructor", pref)
    req = executors.Request(purpose="exercises", role="instructor", prompt=_prompt(skill, kind, count, titles),
                            agent_id=author_id, data={"skill": skill, "kind": kind, "count": count},
                            system=f"You are {author}, the instructor who writes the company's training exercises.")
    res = executors.for_choice(choice, settings().command_timeout).run(req)
    usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                 output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok, error=res.error,
                 agent_id=author_id, purpose="exercises", res=res)
    if not res.ok:
        raise ValueError(f"the instructor wrote no exercises: {res.error[:300]}")
    from agent_company.contracts import extract_json

    try:
        raw = extract_json(res.text)
    except ValueError as e:
        raise ValueError(f"the instructor's answer has no exercises JSON: {e}") from e
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for item in (raw.get("tasks") if isinstance(raw, dict) else None) or []:
        label = str(item.get("name") if isinstance(item, dict) else "?")[:40]
        try:
            task = _shape(item)
            if task["name"] in names:
                raise ValueError("an exercise of this name exists")
            if kind == "final" and (_norm(task["title"]) in p_titles or {_norm(c) for c in task["checks"]} & p_checks):
                raise ValueError("repeats a practice exercise: a final set must be unseen")
            _verify(task)
        except ValueError as e:
            dropped.append({"name": label, "reason": str(e)[:500]})
            continue
        names.add(task["name"])
        kept.append(task)
        if len(kept) >= count:
            break
    log_event("training", f"{author} generated {len(kept)} {kind} exercise(s) for {skill}, dropped {len(dropped)}",
              skill=skill, dropped=dropped[:10])
    if not kept:
        raise ValueError("no generated exercise passed verification: "
                         + "; ".join(f"{d['name']}: {d['reason']}" for d in dropped[:5]))
    name = f"gen-{skill.replace('_', '-')}-{kind}-{now().strftime('%Y%m%d%H%M%S')}"
    suite = {"name": name, "kind": kind, "skills": [skill],
             "description": f"{kind.capitalize()} exercises for {skill}, written by {author} and verified by the "
                            "harness (each check fails on the seed and passes with the reference solution).",
             "tasks": [{k: v for k, v in t.items() if k != "solution"} | {"max_iterations": 2} for t in kept],
             "generated": {"by": author, "model": choice.name, "at": now().isoformat(), "requested_by": requested_by,
                           "kept": len(kept), "dropped": dropped, "approved_by": "" if kind == "final" else "auto",
                           "approved_at": ""}}
    out = generated_dir()
    (out / "solutions").mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(suite, indent=1))
    (out / "solutions" / f"{name}.json").write_text(json.dumps({t["name"]: t["solution"] for t in kept}, indent=1))
    return {"name": name, "kind": kind, "skill": skill, "tasks": [t["name"] for t in kept], "dropped": dropped,
            "approved": kind != "final"}


def approve(name: str, actor: str) -> dict[str, Any]:
    """An administrator accepts a generated final set as a certification standard."""
    path = generated_dir() / f"{Path(name).name}.json"
    if not path.exists():
        raise KeyError(f"generated suite {name}")
    data = json.loads(path.read_text())
    gen = data.get("generated") or {}
    if gen.get("approved_by"):
        raise ValueError("this suite is already approved")
    gen.update(approved_by=actor[:128], approved_at=now().isoformat())
    data["generated"] = gen
    path.write_text(json.dumps(data, indent=1))
    log_event("training", f"{actor} approved the generated final set {data['name']}")
    return {"name": data["name"], "kind": data.get("kind"), "approved": True}


def generate_in_background(skill: str, kind: str, count: int, requested_by: str) -> None:
    """The API's path: generation and verification take minutes; the requester is notified of the outcome."""
    from agent_company.governance import notify
    from agent_company.training import _spawn

    def work() -> None:
        try:
            out = generate(skill, kind, count, requested_by)
            notify(f"Generated {kind} exercises for {out['skill']}: {out['name']}",
                   f"{len(out['tasks'])} kept, {len(out['dropped'])} dropped"
                   + ("; an administrator approves a final set before plans use it" if not out["approved"] else ""),
                   level="info", source="training", admin_only=True)
        except Exception as e:  # noqa: BLE001 - reported, never raised in a thread
            notify(f"Generating {kind} exercises for {skill} failed", str(e)[:1000], level="warning",
                   source="training", admin_only=True)

    _spawn(work)
