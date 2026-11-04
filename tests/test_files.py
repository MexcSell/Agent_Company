import secrets

import pytest
from fastapi.testclient import TestClient

from agent_company import files, projects
from agent_company.api import create_app


@pytest.fixture
def root(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    (p / "src").mkdir()
    (p / "src" / "a.py").write_text("print('hi')\n")
    (p / "README.md").write_text("# Hi\n")
    projects.create("Files", path=str(p))
    return p


def test_listing_read_write_create_move_delete(root):
    d = files.listing(str(root))
    names = [i["name"] for i in d["items"]]
    assert names[:2] == [".git", "src"] and "README.md" in names
    assert {i["name"]: i["kind"] for i in d["items"]}["README.md"] == "markdown"
    assert files.read(str(root / "src/a.py"))["text"] == "print('hi')\n"
    files.write(str(root / "src/b.py"), "x = 1\n")
    files.create(str(root / "docs"), folder=True)
    files.move(str(root / "src/b.py"), str(root / "docs"))
    assert (root / "docs/b.py").exists() and not (root / "src/b.py").exists()
    files.move(str(root / "docs/b.py"), str(root / "src"), copy=True)
    assert (root / "src/b.py").exists() and (root / "docs/b.py").exists()
    assert files.delete([str(root / "docs")]) == [str(root / "docs")]
    with pytest.raises(files.FileError):
        files.create(str(root / "src/a.py"))  # exists


def test_paths_stay_inside_the_roots(root, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("no")
    for bad in [str(outside), str(root / ".." / "secret.txt"), "/etc/passwd"]:
        with pytest.raises(files.FileError):
            files.read(bad)
    (root / "link").symlink_to(outside)
    with pytest.raises(files.FileError):
        files.read(str(root / "link"))
    with pytest.raises(files.FileError):
        files.write(str(root / ".git" / "config"), "x")
    with pytest.raises(files.FileError):
        files.delete([str(root)])
    with pytest.raises(files.FileError):
        files.move(str(root / "src"), str(root / "src" / "inner"))
    big = root / "big.bin"
    big.write_bytes(b"\x00" * 10)
    assert files.read(str(big))["binary"] is True


def test_folder_picker_lists_names_only(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y.txt").write_text("1")
    d = files.folders(str(tmp_path))
    assert [f["name"] for f in d["folders"]] == ["x"] or "x" in [f["name"] for f in d["folders"]]
    assert all("y.txt" != f["name"] for f in d["folders"])


def test_file_api_is_admin_only_and_audited(root):
    with TestClient(create_app(start_background=False)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        assert any(r["path"] == str(root) for r in app.get("/api/files/roots").json())
        assert app.put("/api/files/write", json={"path": str(root / "n.txt"), "text": "hey"}).status_code == 200
        assert app.get(f"/api/files/read?path={root / 'n.txt'}").json()["text"] == "hey"
        up = app.post(f"/api/files/upload?folder={root}", files={"file": ("u.txt", b"up", "text/plain")})
        assert up.status_code == 200 and (root / "u.txt").read_bytes() == b"up"
        assert app.get("/api/files/read?path=/etc/hostname").status_code == 400
        assert app.post("/api/files/delete", json={"paths": [str(root / "n.txt")]}).status_code == 200
        actions = [r["action"] for r in app.get("/api/audit").json()["records"]]
        assert {"file_write", "file_upload", "file_delete"} <= set(actions)
        member = TestClient(app.app)
        s = secrets.token_urlsafe(32)
        member.post("/api/auth/register", json={"name": "member", "device_secret": s})
        uid = next(u["id"] for u in app.get("/api/users").json() if u["name"] == "member")
        app.post(f"/api/users/{uid}/approve")
        member.post("/api/auth/device", json={"device_secret": s})
        assert member.get(f"/api/files/list?path={root}").status_code == 403
