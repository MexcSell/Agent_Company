"""Diff view (UI-D-09, _webdesign diff-viewer screens): a unified diff shown inline or side by side.

diffSplitRows is cut out of agents.js and run in node: each hunk restarts both line counters, context lines appear on
both sides, a removed run is paired row by row with the added run after it, and a longer run leaves gaps.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_company

STATIC = Path(agent_company.__file__).parent / "static"

DIFF = """--- a/agent.md
+++ b/agent.md
@@ -3,4 +3,5 @@ intro
 keep
-old one
-old two
+new one
+new two
+new three
 tail
"""


def _function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed: diff rows not computed")
def test_split_rows_pair_removed_and_added_lines():
    fn = _function((STATIC / "agents.js").read_text(), "diffSplitRows")
    script = fn + "\nconsole.log(JSON.stringify(diffSplitRows(require('fs').readFileSync(0, 'utf8'))));"
    p = subprocess.run(["node", "-e", script], input=DIFF, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    assert rows[0] == {"hunk": "@@ -3,4 +3,5 @@ intro"}
    assert rows[1] == [[3, "keep", ""], [3, "keep", ""]]
    assert rows[2] == [[4, "old one", "del"], [4, "new one", "add"]]
    assert rows[3] == [[5, "old two", "del"], [5, "new two", "add"]]
    assert rows[4] == [None, [6, "new three", "add"]]
    assert rows[5] == [[6, "tail", ""], [7, "tail", ""]]
    assert len(rows) == 6


def test_candidate_screens_use_the_switchable_view():
    agents = (STATIC / "agents.js").read_text()
    evolution = (STATIC / "evolution.js").read_text()
    assert "${diffView(c.diff)}" in agents and "${diffView(c.diff)}" in evolution
    assert '"ac.diffmode"' in agents
    css = (STATIC / "style.css").read_text()
    assert re.search(r'\.diff-view\[data-mode="inline"\] \.diff-split', css)
