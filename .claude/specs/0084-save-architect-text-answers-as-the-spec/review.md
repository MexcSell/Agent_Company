# Review (iteration 2)

## Review the spec-answer fix against the spec (qa, done)

## Logic verification

I only read the code. No test suite was run, because the gate runs it (K336). The scope is `git diff 212acd9..HEAD` of `loop.py`, `specs.py` and `tests/test_spec_answer.py`.

**Change 1, `loop.py:1018` (`save_answer(..., writes=writes, ...)`)**
- **Normal case:** an architect task with `contract.writes=False` and file_exists `<own>/spec.md`. The local `writes` at `loop.py:951` is False. With `res.ok`, the code takes the write lock (`contract.writes` is False) and calls `save_answer(writes=False)`. In `specs.py:137-138`, `rel` starts with `own`, is not a loop file, and the `writes and ...` clause is False, so `_write` runs and `saved=[path]`. The answer is committed at `loop.py:1020`, and only then does `contracts.check` run at `loop.py:1027`. The file exists when it is checked.
- **Edge case:** an architect with `contract.writes=True` and `changed=None` (the checkpoint failed). Before, `writes=True` with `changed is None` meant `continue`, and `docs/architecture.md` was skipped by `or writes` at `specs.py:140`. Now the effective `writes=False`, so both files are saved. The lock stays `nullcontext() if contract.writes`, so the code does not try to take a lock that run_dag already holds and that is not reentrant. The lock choice stays on `contract.writes`, as the spec requires.
- **Root cause:** fixed. The run's real permission now decides, not the plan flag. A genuinely writing run is unchanged: it passes `writes=True`, and `test_writing_task_with_unknown_changes_keeps_out_of_its_folder` is unmodified (the diff removes 0 lines from the test file).
- **Error case:** `res.ok` False skips the whole block at `loop.py:1011`, so `saved=[]`. Then `ok = res.ok and ...` at `loop.py:1053` makes the task `failed`.

**Change 2, `loop.py:1044` (`... and changed == [] and not saved`)**
- **Normal case:** a contract-writes architect whose answer was saved, with a `BLOCKER:` line in its output. `blocked` is now `[]`, so the task is checked and ends `done`.
- **Edge case:** `changed is None` was already excluded and still is.
- **Error case:** a real build with no diff, no saved answer and a BLOCKER line is still BLOCKER_SKIPped.
- **Consistency:** this matches `loop.py:1031-1033`, where a saved answer already counts as a change.

**Change 3, `specs._normalise` (`specs.py:62-70`), used at `specs.py:95` and `specs.py:128`**
- **Normal case:** `<own>/spec.md` comes back unchanged.
- **Edge case:** `<own>/spec.md/` becomes `<own>/spec.md`, and this now happens before the `ANSWER_FILES` test (`specs.py:128-129`), so the answer is saved. `contracts.check` (`contracts.py:206`) resolves `workdir / "…/spec.md/"`, and pathlib drops the trailing slash, so `exists()` is True. `check_plan` also rewrites `cond.path` and reports the fix.
- **Error cases:**
  - `"/"` normalises to `""` and fails the extension test.
  - `../x.md` and absolute paths outside the worktree fail `root not in target.parents` after `.resolve()`.
  - `<own>/decisions.md/` normalises and is then refused by `LOOP_FILES` (tested at `test_spec_answer.py` by `test_loop_file_never_written_in_any_spelling`).
- **Root cause:** fixed. The helper only strips characters; containment is still decided after `.resolve()`.

**Changes 4 and 5, `specs.write_outputs` (`specs.py:166-192`)**
- **Normal case:** an architect `spec_v2` whose acceptance names `<own>/spec.md`. `named` (`specs.py:176-178`) holds the normalised path from `Task.contract["acceptance"]` (a stored `model_dump`, `loop.py:1985`) and from `saved_answer`. So the row is classified as `spec` (`specs.py:184`) and is excluded from `design`. spec.md is in `kept`, so it is not rewritten, and design.md does not get the answer.
- **Edge case:** a spec.md that some task's run changed (`gate["files"]`) is in `kept`, so the fallback leaves it alone (`specs.py:189`, `:191`).
- **Error case:** a failed `spec_v2` run (nothing saved) is now classified as spec, so the fallback writes its failed output into spec.md. That is what the spec asks for (§2(b), "K336 side"), and key `spec` already behaved this way, so it is not a regression introduced here. See the comment finding below.

**§3 invariants**
- No `LOOP_FILES`: `specs.py:138` runs regardless of `writes`.
- No other goal's folder: `specs.py:140` (`.claude/specs` prefix outside `own`).
- No `.git`: `specs.py:135`.
- Nothing outside the worktree or reached through a symlink: `.resolve()` plus the parents check at `specs.py:131-132`.
- `_normalise` cannot bypass any of these, because every check runs on the resolved `target` and `rel`.
- With `writes=False`, outside the own folder only new files are written (`target.exists()`), so an architect cannot overwrite an existing repository document.

**Does each regression test fail without its change?**
- `test_trailing_slash_saved` and `test_check_plan_strips_trailing_slash`: yes. Without Change 3, `"spec.md/".endswith(".md")` is False, so `save` returns `[]`, and the report is empty.
- `test_read_only_architect_with_writing_contract_saves`: yes. Without Change 1, `writes=True` and `changed=None` skip spec.md, and `or writes` skips `docs/architecture.md`.
- `test_saved_answer_with_blocker_is_checked_not_skipped`: yes. Without Change 2, `changed == []` (the architect wrote nothing) plus the BLOCKER line gives BLOCKER_SKIP. The answer is saved even at base, so this test isolates Change 2.
- `test_spec_v2_answer_is_the_spec_not_design`: yes. Without Change 4, `spec_v2` goes into `design` and design.md contains the stripped output.
- `test_write_outputs_keeps_a_spec_md_a_run_wrote`: yes. Without Change 5, `kept` holds only `saved_answer`, so spec.md would be overwritten by the `spec` architect output.
- `test_failed_run_answer_not_saved` (a) and `test_answer_on_disk_before_acceptance_check` (e): these lock in behaviour the spec calls NOT A BUG (Change 7), so they are expected to pass at base too. In (e), the monkeypatched `specs.write_plan` does take effect, because `loop.py:1433` looks it up as a module attribute. So the stale "iteration 1" file really exists and the test is not trivially true.

## Checklist
- **Contract satisfied:** Changes 1-7 are implemented as specified. The lock choice stays on `contract.writes` (`loop.py:1016`).
- **Interfaces compatible:** the `save_answer` signature is unchanged. `check_plan` output only gains the trailing-slash fix.
- **Error handling:** fail-open is unchanged (`loop.py:1023`), and an `OSError` is logged (`specs.py:144`).
- **Edge cases:** trailing slash, `./`, the absolute prefix and `.MD` are covered.
- **Tests:** five spec tests plus two extra (check_plan trailing slash, loop-file spellings). Each fix test fails without its change.
- **Dependencies:** none added.
- **Security:** the §3 invariants hold, and containment still runs after resolve.
- **Performance:** only a per-task acceptance scan in `write_outputs`, which is negligible.
- **Docs:** the docstrings of `check_plan`, `_normalise` and `write_outputs` are updated. CLAUDE.md needs no change.

## Findings
FINDING: src/agent_company/specs.py:184 | (comment) A failed `spec_v2` architect run (`res.ok` False, nothing saved) is now classified as spec, so `write_outputs` writes its failed output over an earlier good spec.md. That is the same risk §2(a) gives for not saving failed text, though key `spec` already behaved this way and the spec asks for the classification | Possible follow-up: leave out rows with `status == "failed"` when other spec rows or an existing spec.md are present.

FINDING: src/agent_company/specs.py:76 | (cosmetic) The new `check_plan` docstring line is about 190 characters, while the surrounding docstrings wrap at about 120. pyproject sets no line-length or E501 rule and other long lines already exist, so the gate is not affected | Rewrap the line at about 120 characters.

OUTCOME: APPROVED_WITH_COMMENTS

NEXT: the gate runs `uv run pytest -q` once and records the summary line (§4).

MESSAGE done to developer: Changes 1-7 in loop.py and specs.py match the spec of goal 84 and the §3 invariants, and each fix test fails without its change; there are two non-blocking comments (`specs.py:184` failed spec_v2 run, `specs.py:76` line length).

PROPOSAL: Leave failed architect rows out of the write_outputs spec fallback :: a failed run's partial text should not replace a good spec.md from an earlier iteration (K336, §2(a)).

VERDICT: PASS
