# Spec: where an architect's text answer still fails to become the goal's spec.md (goal 84)

This spec is read-only. Every claim cites the base branch at `212acd9`. Pathlib behaviour noted as "pathlib" comes from the language's documented semantics; I did not run it here.

## 1. Data flow today

For an architect task with acceptance `{"type":"file_exists","path":".claude/specs/<slug>/spec.md"}`:

1. **Planning.** `specs.check_plan` (specs.py:62-101) runs on the final plan (loop.py:605).
   - It strips the absolute worktree prefix and `./` from a file_exists path (specs.py:87-90).
   - It rejects another goal's folder and loop files (specs.py:72-81).
   - It writes the normalised path back into the condition (specs.py:99-100).
2. **Effective permission.** The worker computes `writes = contract.writes and "write" in agent.permissions and level >= 2` (loop.py:951). The architect role has only `("read",)` (org.py:82), so an architect run is **always read-only**, even when the plan says `"writes": true`.
3. **Run.** `res, choice = _execute(...)` (loop.py:995). Then `output = res.text if res.ok else text + ERROR` (loop.py:998).
4. **Checkpoint.** `commit, changed = _checkpoint(...) if contract.writes else ("", [])` (loop.py:1001).
   - `changed` is the diff since the previous checkpoint (loop.py:892).
   - It is `None` when the checkpoint failed (loop.py:894-896).
5. **Saving the answer.** `if res.ok:` (loop.py:1011) calls `specs.save_answer(goal, contract, res.text, writes=contract.writes, changed=changed)` (loop.py:1018), then commits only the saved paths (loop.py:1020).
   - Under the lock: `nullcontext()` when `contract.writes` is true, because run_dag already holds it (scheduler.py:82-96). Otherwise it takes `write_lock` (loop.py:1016).
6. **save_answer** (specs.py:109-148):
   - Skips any condition that is not file_exists, or whose path does not end in `ANSWER_FILES`, tested on the raw string (specs.py:125).
   - Strips the prefix and `./` (specs.py:127-130), resolves the path, and requires it to be inside the worktree (specs.py:131-133). It never writes `.git` (specs.py:135).
   - **In the goal's own folder:** it skips `LOOP_FILES`, and for a writing task it skips when `changed is None or rel in changed` (specs.py:138).
   - **Elsewhere:** it skips any `.claude/specs` path, any writing task, and any existing file (specs.py:140).
7. **Acceptance.** `contracts.check` (loop.py:1027). For file_exists: `target = (workdir / cond.path).resolve()`, which must be under the worktree and exist (contracts.py:205-208).
8. **Task result.** `ok = res.ok and all(passed)` (loop.py:1052).
9. **After all tasks, before the gate:** `write_outputs` (loop.py:1472, specs.py:166-190).
   - It treats architect rows keyed `spec`/`specification` as the spec (specs.py:179) and every other architect row as design (specs.py:180).
   - It skips spec.md or design.md only when that file is in some task's `gate["saved_answer"]` (specs.py:173, 183, 185).
   - The gate never re-runs file_exists. It reads the recorded `gate["acceptance"]` (quality.py:235).

## 2. Cases

### (a) `res.ok` is false but the run returned text → NOT A BUG

- `save_answer` is indeed skipped (loop.py:1011). But the task fails anyway: `ok = res.ok and ...` (loop.py:1052). Saving the file would change only the wording of the failure, not the outcome.
- The "useful text" is usually not an answer:
  - With no result event, `text` is the raw stream-json stdout (executors.py:669-671).
  - When StuckWatch ends a run, `text` is the result field, which is often empty (executors.py:666-668).
  - On max-turns or a non-zero exit, `text` is `data["result"]`, which is empty or partial for an `error_max_turns` stop (executors.py:675-679).
- Saving that text in the own folder would also overwrite a good spec.md from an earlier iteration, because the K336 rule replaces stale files (specs.py:137-139). The next iteration would then read a truncated spec.
- **Decision:** keep the `res.ok` guard. No change.
- **Regression test** (locks the behaviour), `test_failed_run_answer_not_saved(monkeypatch)`:
  - Wrap `loop._execute` so that for the task whose `req.data["title"]` is the standard plan's `spec` title, it returns `executors.Result(text=ANSWER, ok=False, error="error_max_turns")`.
  - Run `run_with(monkeypatch, "spec", "spec.md")`.
  - Assert the iteration-1 `spec` task is `failed`, `gate["saved_answer"] == []`, and that its acceptance list holds a failed file_exists entry next to the run error.

### (b) Key aliases in write_outputs: an architect `spec_v2` → NOT A BUG for file_exists; CONFIRMED classification defect

- **When the acceptance names `<own>/spec.md`:** the answer is always written there when `res.ok` is true, the text is non-empty, and the effective run was read-only (always so for an architect, see (c)). The path is: specs.py:137-139 → `_write` → saved, and the commit at loop.py:1020 happens before the check at loop.py:1027. The key does not matter. write_outputs then leaves spec.md alone, because `<own>/spec.md` is in `saved_answer` (specs.py:183).
- **Defect:** `spec_v2` is not in `{"spec","specification"}` (specs.py:179), so it falls into `design` (specs.py:180). design.md is skipped only when *design.md* itself was saved (specs.py:185). Result: the same answer is also written to design.md under a "# Design" header.
- **K336 side:** if `spec_v2` names spec.md but its answer was not saved (for example, a writing run edited spec.md itself), write_outputs puts it into design.md, and an older spec.md is never updated by the fallback.
- **Decision:** yes, the fallback should also count an architect task whose acceptance names `<own>/spec.md`.
- **Change (specs.write_outputs):**
  - Classify an architect row as `spec` when its key is `spec`/`specification`, **or** its saved_answer contains `<own>/spec.md`, **or** its contract's acceptance names `<own>/spec.md` after the same normalisation as save_answer (§3 helper).
  - `design` keeps only the remaining architect rows.
  - Also skip writing spec.md or design.md when any task of this iteration has that path in `gate["files"]`, not only in `saved_answer`. Otherwise a writing run's own spec.md is overwritten by its text answer, because `rel in changed` meant save_answer did not save it (specs.py:138, then specs.py:183).
  - Expected behaviour: a saved or run-written spec.md is never overwritten, and a spec-naming architect answer never appears in design.md.
- **Regression test** `test_spec_v2_answer_is_the_spec_not_design(monkeypatch)`:
  - Plan with the architect task re-keyed `spec_v2`, plus a file_exists on `<own>/spec.md`.
  - Run `run_goal` and assert `gate["saved_answer"] == [path]`.
  - Assert that spec.md holds the answer and that design.md either does not exist or does not contain the answer.

### (c) Writing architect task → CONFIRMED (the fix belongs in loop.py:1018, not specs.py)

- **Run changed spec.md itself:** `rel in changed` → skipped (specs.py:138). The run's own file exists, so the check passes. NOT A BUG; already covered by test_spec_answer.py:60-64.
- **The real defect:** loop.py:1018 passes `writes=contract.writes`, but the run's effective permission is the local `writes` (loop.py:951), which is always false for an architect (org.py:82). The run was told "Do not change files" (loop.py:985-986), yet save_answer treats it as a writing run:
  - With `changed is None` (checkpoint failure, loop.py:894-896), the own-folder spec.md is skipped (specs.py:138), even though the run could not have written it. file_exists then fails.
  - Any non-spec-folder document (such as `docs/architecture.md`) is always skipped (specs.py:140, `or writes`). file_exists always fails.
- **Change (loop.py `work`, line 1018):** pass `writes=writes`, the effective value from line 951.
  - Keep the lock choice on `contract.writes` (loop.py:1016), since run_dag holds the lock by the contract's `writes`.
  - Keep `test_writing_task_with_unknown_changes_keeps_out_of_its_folder` (test_spec_answer.py:149-156) as it is: specs.py:138 still protects a *really* writing run when `changed` is None.
- **Related CONFIRMED defect:** loop.py:1043 sets `blocked = contracts.blockers(output) if contract.writes and changed == []`. It ignores `saved`, so a contract-writes architect whose answer was saved but contains a `BLOCKER:` line is BLOCKER_SKIPped instead of checked.
  - Change: `... and changed == [] and not saved`. This matches loop.py:1031-1033, where a saved answer counts as a change.
- **Regression test** `test_read_only_architect_with_writing_contract_saves(monkeypatch)`:
  - Plan where the `spec` task has `"writes": true` and file_exists on `<own>/spec.md` and on `docs/architecture.md`.
  - Monkeypatch `loop._checkpoint` to return `("", None)` for key `spec`.
  - Run, then assert both paths are in `gate["saved_answer"]`, both files exist, and the task is `done`.

### (d) Path forms

- **Absolute worktree prefix → NOT A BUG.**
  - check_plan strips it (specs.py:87-88) and save_answer strips it (specs.py:127-128).
  - When the spelling differs (a symlink or unresolved path), `root / "/abs"` yields the absolute path (pathlib), and the containment check at specs.py:132 still holds.
  - contracts.py:206 behaves the same way.
- **`./` → NOT A BUG.** Handled at specs.py:89-90 and specs.py:129-130. Tested at test_spec_answer.py:119-123 and in test_spec_paths.
- **Trailing slash `.../spec.md/` → CONFIRMED.**
  - The extension filter runs on the raw string: `"spec.md/".endswith(".md")` is false, so the path is skipped (specs.py:125).
  - check_plan does not reject it, because SPEC_DIR matches the own slug and LOOP_FILE's lookahead allows `/` (specs.py:37-38).
  - contracts.py:206 would pass if the file existed, since pathlib drops the trailing slash. So file_exists always fails.
  - **Change:** add one helper, `specs._normalise(goal, path) -> str`: strip the worktree prefix, strip leading `./`, strip trailing `/`.
    - save_answer calls it *before* the `ANSWER_FILES` test (specs.py:124-130).
    - check_plan uses the same helper (specs.py:86-90), so the rewritten `cond.path` no longer has the slash and is reported in `fixed`.
- **`.MD` / `.Md` → NOT A BUG on Linux.** `.lower()` (specs.py:125) accepts it, and the file is written under exactly the name the check looks up.
  - `target.name in LOOP_FILES` (specs.py:138) is case-sensitive, so `DECISIONS.MD` would be written as a separate file. That is not a loop file on a case-sensitive filesystem, so it does not need to change.
- **Regression test** `test_trailing_slash_saved(goal)`:
  - `save(goal, f"{own(goal)}/spec.md/") == [f"{own(goal)}/spec.md"]`, and the file content equals ANSWER.
  - `contracts.check` with that condition passes.
  - Also assert `save(goal, f"{own(goal)}/x.MD") == [f"{own(goal)}/x.MD"]`.

### (e) Check before save, or a stale iteration-1 file → NOT A BUG

- In the same worker, the save (loop.py:1018) and its commit (loop.py:1020) happen before the check (loop.py:1027).
- A read-only answer, and a writing task's answer for a file it did not touch, overwrite a stale own-folder file (specs.py:137-139; tests at test_spec_answer.py:53-71).
- A stale file can only make file_exists pass, never fail. The task still fails on `res.ok` (loop.py:1052).
- `_reconcile` resets to the checkpoint, which includes the answer commit (loop.py:899, 903-917).
- **Out of scope here:** a *different* task naming another task's document without `depends_on` may be checked before that document is saved. That is a planning matter (see the PROPOSAL below).
- **Regression test** `test_answer_on_disk_before_acceptance_check(monkeypatch)`:
  - Pre-write `<own>/spec.md` with "iteration 1".
  - Wrap `contracts.check` to record `Path(workdir, path).read_text()` at check time for the `spec` task.
  - Run `run_with(monkeypatch, "spec", "spec.md")` and assert the recorded content is the new answer, not "iteration 1".

## 3. Invariants (unchanged, must stay tested)

The loop never writes:

- a `LOOP_FILES` file (specs.py:138; test_spec_answer.py:74-79),
- another goal's spec folder (specs.py:140; test_spec_answer.py:82-85),
- `.git` (specs.py:135; test_spec_answer.py:95-97),
- anything outside the worktree, including through a symlink (specs.py:132; test_spec_answer.py:88-105).

The new `_normalise` helper only strips characters. Containment is still decided after `.resolve()`.

## 4. Verification (required for the developer and for QA)

Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` and paste the summary line. Then run `uv run pytest -q` once and paste its summary line (K449, K501). QA reviews only the Changes below, to stay inside its budget.

## Changes

1. `loop.py` `work`, line 1018: pass the effective `writes` (line 951) to `specs.save_answer`, not `contract.writes`; the lock choice at line 1016 stays on `contract.writes`.
2. `loop.py` `work`, line 1043: BLOCKER_SKIP only when `changed == [] and not saved`.
3. `specs.py`: new `_normalise(goal, path)` (strip the worktree prefix, leading `./`, trailing `/`), used by `check_plan` and by `save_answer` before the `ANSWER_FILES` test.
4. `specs.write_outputs`: an architect row is `spec` when its key is `spec`/`specification`, or its saved_answer or normalised acceptance names `<own>/spec.md`; it never also goes to design.md.
5. `specs.write_outputs`: do not write spec.md or design.md when a task of this iteration has that path in `gate["saved_answer"]` or `gate["files"]`.
6. `tests/test_spec_answer.py`: add the five regression tests from §2 (a)–(e).
7. No change for (a), the absolute-prefix and `./` forms in (d), `.MD` in (d), or (e).

FINDING: loop.py:1018 passes `contract.writes`, but an architect (org.py:82, read-only) never writes, so with `changed` None or a non-spec-folder path its answer is never saved.
FINDING: specs.py:125 tests the extension before normalising, so a trailing-slash path is never saved.
FINDING: specs.py:179-185 writes a spec-naming architect answer into design.md too, and can overwrite a spec.md that a writing run made itself.
FINDING: loop.py:1043 BLOCKER-skips a contract-writes task whose answer was saved.
NEXT: the developer implements Changes 1-6 and pastes both pytest summary lines.
NEXT: QA checks Changes 1-6 against §2 and §3 only.
MESSAGE handoff to developer: implement the Changes list in .claude/specs/0084-save-architect-text-answers-as-the-spec/spec.md; Change 1 is in loop.py, not specs.py; keep test_spec_answer.py:149-156 unchanged.
PROPOSAL: Warn in check_plan when a task's file_exists names another task's document without depends_on :: such a check may run before the answer is saved, or pass on a stale file.
