# Decisions: Run pytest without leaving cache folders in the worktree

## Iteration 1: FAIL
- fix_env (developer): done
- qa_review (qa): done
- fix_env-security (qa): done

Findings:
- stage integration: mandatory by the goal's test strategy (risk high): no check of this stage ran

Next: fix the findings (loop back to Build).

## Iteration 2: FAIL
- integration_test (developer): done
- qa_review (qa): done
- integration_test-security (qa): done

Findings:
- boundaries: tests/test_cache_free_runs.py: disables or skips a test: pytest.skip(f"bubblewrap cannot start a sandbox here: {sandbox.available()[1]}")

Next: fix the findings (loop back to Build).

## Iteration 3: PASS
- drop_runtime_skip (developer): done
- qa_review (qa): done

Next: approval and merge.
