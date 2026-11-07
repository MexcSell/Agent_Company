# Decisions: Cache label counts per finished goal

## Iteration 1: FAIL
- impl_label_cache (developer): done
- qa_review (qa): done
- qa_full_suite (qa): failed

Findings:
- review qa_review: reviewer: FAIL
- task qa_full_suite: `uv run pytest -q` exit 1 (L3)
nds": ["test -f WORKLOG.md"]}).json()["id"]
            up = app.post("/api/uploads", files={"file": ("note.txt", b"hello", "text/plain")}).json()
            assert app.get(f"/api/uploads/{up['id']}").content == b"hello"
            r = app.post(f"/api/projects/{pid}/

Next: fix the findings (loop back to Build).
