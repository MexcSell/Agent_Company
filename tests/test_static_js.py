"""Every top-level dashboard script parses: `node --check` on static/*.js (vendor/ is third-party)."""
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_company

STATIC = Path(agent_company.__file__).parent / "static"
SCRIPTS = sorted(STATIC.glob("*.js"))

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed: static/*.js syntax not checked")


def node_check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=60)


def test_scripts_found():
    assert SCRIPTS, f"no *.js in {STATIC}: the static directory moved, nothing would be checked"
    assert all(p.parent == STATIC for p in SCRIPTS)


@pytest.mark.parametrize("path", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_static_script_parses(path):
    p = node_check(path)
    assert p.returncode == 0, f"{path.name}: node --check failed:\n{(p.stderr or p.stdout).strip()[-800:]}"


def test_check_detects_syntax_error(tmp_path):
    bad = tmp_path / "broken.js"
    bad.write_text("function (\n")
    p = node_check(bad)
    assert p.returncode != 0
    assert "SyntaxError" in p.stderr


def test_check_accepts_valid_script(tmp_path):
    good = tmp_path / "ok.js"
    good.write_text("const add = (a, b) => a + b;\nadd(1, 2);\n")
    p = node_check(good)
    assert p.returncode == 0, p.stderr
