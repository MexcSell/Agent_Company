The checks are done. `spec.md` doesn't exist yet, so the current `_save_answer` will save my answer to it. `.claude/` isn't excluded from `commit_all`. Here is the specification.

# Spec: `specs.save_answer` saves a role's answer at a task's acceptance path

## 1. Root cause

- **FACT:** A read-only task (`writes=False`) gets no Edit or Write tools. In `executors.py:350` `gate` keeps `writes=False`, and the isolation level is L1/L0. An architect therefore cannot create `.claude/specs/<slug>/spec.md` itself.
- **FACT:** The only `.claude`-specific exception in `gate` is the GM's configure step (`executors.py:341-346`). It lets a run without the `write` permission write. It does not limit where a run that has `write` may write.
- **INFERENCE (not shown in this repo):** A *writing* Claude Code or Codex run also fails to create files under `.claude/` because of the CLI's own protected-path or sandbox behaviour, not because of our gate. That fits the goal statement, but nothing in `executors.py` refuses the path. So a `file_exists` condition under `.claude/specs/<slug>/` on a writing task fails every iteration.
- **FACT:** The fallback `loop._save_answer` (`loop.py:816-830`) is called only `if res.ok and not contract.writes` (`loop.py:1017`). It skips any target that already exists (`loop.py:825`). In iteration 2 the answer therefore never replaces the iteration-1 file, and `file_exists` passes on stale content. This is K336, where `qa_review` read the iteration-1 `spec.md`.

## 2. New function in `specs.py`

```python
ANSWER_FILES = (".md", ".markdown", ".txt", ".rst")   # moved from loop.py

def save_answer(goal, contract, text: str, *, writes: bool, changed: list[str]) -> list[str]:
```

`loop.py` keeps `ANSWER_FILES = specs.ANSWER_FILES` as an alias. This creates no import cycle, because `specs.py` imports only `db`.

**Rules.** Each condition in `contract.acceptance` is checked in turn. Anything that fails a check is skipped, never raised:

1. **Empty answer:** if `text.strip()` is empty, write nothing and return `[]`.
2. **Condition type and extension:** only `cond.type == "file_exists"` with `cond.path.lower().endswith(ANSWER_FILES)` is considered.
3. **Path normalisation:** remove a leading `str(goal.worktree)+"/"` and any leading `./`, the same way `check_plan` does.
4. **Containment:** `root = Path(goal.worktree).resolve()`, `target = (root / path).resolve()`.
   - Skip unless `root in target.parents`. This also stops `../` escapes and symlinks that lead outside.
   - Skip if `".git" in rel.parts`.
   - `rel = target.relative_to(root).as_posix()` is the resolved path, so a symlinked folder is judged by where it really points.
5. **Inside the goal's own folder** (`rel` starts with `relative_folder(goal) + "/"`):
   - Skip if `target.name in LOOP_FILES`.
   - Skip if `target.is_dir()`.
   - **Read-only (`writes=False`):** write, and overwrite an existing file. This fixes K336.
   - **Writing (`writes=True`):** write only if `rel not in changed`, meaning the run did not produce the file. A stale file from an earlier iteration that the run did not touch is overwritten.
6. **Any other `.claude/specs/<X>/`** (`rel.split("/")[:2] == [".claude", "specs"]`): never written. This backs up `check_plan`.
7. **Anywhere else:** the current behaviour stays. Write only if `writes` is false and the target does not exist.
8. **Writing the file:** `_write(target, text)` creates parent folders and adds a trailing newline. An `OSError` is caught for that path and logged with `log_event("error", …, goal_id=goal.id)`. That condition then fails in `contracts.check` by itself.
9. **Return value:** the list of `rel` paths written.

## 3. Call site in `loop.py`

Replace `loop.py:1017-1021` with a call that runs for both kinds of task after `_checkpoint` (`loop.py:1008`) and before `contracts.check`:

```python
if res.ok:
    saved = specs.save_answer(goal, contract, res.text, writes=contract.writes, changed=changed)
    if saved:
        if contract.writes:
            commit, _ = _checkpoint(goal.id, goal.iteration, f"{contract.key} answer")
        log_event("task", f"saved {agent.name}'s answer as {', '.join(saved)}", ...)
```

**Commit decision (checked against `_checkpoint`, `loop.py:888-906`):** the saved file is committed by a second checkpoint commit. It cannot be in the task's own checkpoint, because `changed` only exists after that commit. Leaving it for the gate's `commit_all` (`quality.py:264`) is wrong for three reasons:

- A later writing task's checkpoint would sweep the file into its own commit and its `changed`. That later task's `out_of_scope` limits could then fail on it.
- `_reconcile` resets to `checkpoint.commit` after a restart, which would drop the file.
- `quality.evaluate` would only commit it after `write_outputs`, too late for dependents.

The second commit is safe because `scheduler.run_dag` (`scheduler.py:93-95`) holds the worktree's write lock for the whole worker of a writing task. The commit therefore contains only this file, and the second `_checkpoint` moves `Goal.checkpoint.commit` forward.

Two further points:

- `commit` (the variable) is overwritten so `gate["commit"]` names the newest commit. `changed` stays the run's own list, so `contracts.limits` and `gate["files"]` don't count the loop's write.
- Read-only tasks stay uncommitted until a later commit, as today. They run without the lock, so committing from them could race (see FINDING).

The call is fail-open. Wrap it in `try/except Exception` and `log_event("error", …)`, like `_spec_step`.

## 4. Interaction with `write_outputs`

`write_outputs` (`specs.py:121-142`) runs after all tasks and before the gate.

- **Architect task with key `spec` or `specification`:** `spec.md` is rewritten as `# Specification: <goal>` + `## <title> (architect, <status>)` + `t.output`. `t.output` is the same `res.text`, capped to its last 50 000 characters (`loop.py:1027`). **The content is kept, inside that frame.**
- **Failed task:** `write_outputs` still writes the output, with `ERROR:` appended. `save_answer` never runs for one (`res.ok` is false).
- **Key `spec_v2` (the K336 shape):** `write_outputs` writes to `design.md` and leaves `spec.md` alone. The fresh `spec.md` comes from `save_answer`.
- **Timing:** `save_answer` now runs before the task's status is set to done, so a dependent `qa_review` reads the new file during the iteration rather than after it ends.
- **Path `design.md` named by a non-`spec` architect task:** `write_outputs` replaces it with every design task's output. This task's answer is one of those sections.

## 5. Tests: `tests/test_spec_answer.py`

Use `SimpleNamespace` goals with `tmp_path` worktrees, in the style of `tests/test_spec_paths.py`. Use a git-initialised worktree for the loop test.

**Normal cases**
1. A read-only task with `<own>/spec.md` writes the answer and returns `[".../spec.md"]`.
2. A writing task with `<own>/notes.md`, where `changed=[]`, writes the file.

**Edge cases**
3. A stale `<own>/spec.md` from iteration 1 is overwritten by a read-only task.
4. For a writing task with the path in `changed`, the run's file is kept and `[]` is returned.
5. A writing task whose path is not in `changed` overwrites a stale file.
6. `<own>/tasks.md` (a LOOP_FILES name) is not written.
7. `.claude/specs/other-goal/spec.md` is not written, whether `writes` is true or false.
8. `<own>/../../../x.md` and `../outside.md` (escapes) are not written.
9. A `.git/x.md` path is not written.
10. A symlink in the worktree pointing outside it is not followed.
11. `text=""` and `text="  \n"` write nothing.
12. A `.py` path is ignored.
13. `./<own>/spec.md` and an absolute-worktree-prefix path are normalised and written.
14. Outside the spec folder, `docs/x.md` is written once by a read-only task and is never overwritten. A writing task never writes it.
15. `loop.ANSWER_FILES is specs.ANSWER_FILES`.

**Error cases**
16. A target `<own>/spec.md` that is an existing directory: no exception, `[]` is returned, and an `error` Event is logged. `contracts.check` file_exists still passes on it, which is an existing quirk. Alternatively, make the parent folder read-only, which gives an `OSError` that is logged.

**Loop integration**
17. A writing task with an `OfflineExecutor` and `<own>/spec.md` as acceptance: the file is in HEAD after the task, under the commit message `Goal N iteration I: <key> answer`, and `Goal.checkpoint.commit` equals HEAD.

---

FINDING: Files saved by read-only tasks (today and after this change) stay uncommitted until a later writing task's checkpoint takes them. They then appear in that task's `changed`, which can trip its `out_of_scope` limits, and `_reconcile` drops them on a restart.
FINDING: `executors.gate` has no `.claude/` path rule. That writing CLI runs cannot write `.claude/` is an inference about Claude Code and Codex, not something this repo enforces.
NEXT: The developer implements `specs.save_answer` plus the call site in §3, and adds `tests/test_spec_answer.py` covering cases 1-17.
NEXT: QA checks rules 4-7 against `relative_folder(goal)` and confirms the second checkpoint commit by running case 17.
MESSAGE handoff to developer: The spec is at `.claude/specs/0033-let-specs-py-write-role-provided-spec-fi/spec.md`. Call `save_answer` after `_checkpoint` (`loop.py:1008`) and commit the saved files with a second `_checkpoint` for writing tasks only.
PROPOSAL: Commit read-only answers under the write lock :: Today they ride along with an unrelated task's checkpoint, which blurs which task changed what and can fail that task's out_of_scope limit.
