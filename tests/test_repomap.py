import subprocess

from agent_company import learning, loop, prefs, repomap


def _repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "a.py").write_text("def helper(x):\n    return x\n\n\ndef lonely():\n    pass\n\n\nclass Store:\n"
                                    "    def fetch_all(self):\n        return helper(1)\n")
    (r / "pkg" / "b.py").write_text("from pkg.a import helper, Store\n\nprint(helper(2), Store().fetch_all())\n")
    (r / "pkg" / "c.py").write_text("from pkg.a import helper\n\nhelper(3)\nhelper(4)\n")
    (r / "web.js").write_text("export function renderPanel(el) {\n  if (el) {\n    return 1;\n  }\n}\n"
                              "const makeTab = (id) => id;\n")
    (r / "node_modules").mkdir()
    (r / "node_modules" / "dep.js").write_text("function vendored() {}\n")
    (r / "app.min.js").write_text("function minified(){}\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=r, check=True)
    return r


def test_definitions_used_by_other_files_come_first_within_the_budget(tmp_path):
    r = _repo(tmp_path)
    m = repomap.repo_map(r, "main", "", 4000)
    assert m.index("def helper(x):") < m.index("def lonely():")
    assert "class Store:" in m and "    def fetch_all(self):" in m
    assert "export function renderPanel(el) {" in m and "const makeTab" in m
    assert "if (el)" not in m, "control flow is not a definition"
    assert "vendored" not in m and "minified" not in m
    small = repomap.repo_map(r, "main", "", 60)
    assert len(small) <= 60 and "helper" in small
    # A name the task mentions comes first: a small budget still has it.
    assert "lonely" not in small and "def lonely():" in repomap.repo_map(r, "main", "rename lonely", 60)
    # Read from the commit, not the working tree: an uncommitted edit is not in the map.
    (r / "pkg" / "a.py").write_text("def changed():\n    pass\n")
    assert "changed" not in repomap.repo_map(r, "main", "", 4000)
    assert repomap.repo_map(r, "main", "", 0) == "" and repomap.repo_map(tmp_path / "nope", "main") == ""


def test_a_task_package_gets_the_map_unless_it_is_off(tmp_path):
    r = _repo(tmp_path)
    g = loop.create_goal("Rename helper", "in pkg/a.py", repo_path=str(r), quality_commands=["true"])
    loop._update_goal(g, base_branch="main")  # set when the goal starts
    text = learning.task_references(g, "rename helper")
    assert "<repo_map>" in text and "def helper(x):" in text
    prefs.set_value("repo_map_chars", 0)
    try:
        assert "<repo_map>" not in learning.task_references(g, "rename helper")
    finally:
        prefs.set_value("repo_map_chars", 4000)
