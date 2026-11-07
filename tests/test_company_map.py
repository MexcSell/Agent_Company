"""Company map view (COMPANY-DESIGN §4): wiring in index.html and app.js, and the §3 lifecycle mapping through node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_company

STATIC = Path(agent_company.__file__).parent / "static"
VIEW = STATIC / "companymap.js"


def test_index_loads_view_after_app():
    html = (STATIC / "index.html").read_text()
    assert '<script defer src="/static/companymap.js"></script>' in html
    assert html.index("/static/app.js") < html.index("/static/companymap.js") < html.index("start()")


def test_nav_default_lists_view_after_agents():
    app = (STATIC / "app.js").read_text()
    m = re.search(r"const NAV_DEFAULT = \{\s*top:\s*\[([^\]]*)\]", app)
    assert m, "NAV_DEFAULT.top not found in app.js"
    ids = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
    assert ids[ids.index("agents") + 1] == "companymap"


def test_view_registers_and_escapes():
    js = VIEW.read_text()
    assert 'registerView({ id: "companymap"' in js
    assert "esc(" in js
    assert "method:" not in js, "the Company map is read-only"


CASES = [
    ("queued", [], "discover"),
    ("planning", [], "plan"),
    ("awaiting_approval", [], "learn"),
    ("running", [{"role": "developer", "stage": "in_progress", "status": "running"}], "execute"),
    ("running", [{"role": "developer", "stage": "completed", "status": "done"},
                 {"role": "qa", "stage": "in_progress", "status": "running"}], "critique"),
    ("running", [{"role": "developer", "stage": "completed", "status": "done"},
                 {"role": "qa", "stage": "completed", "status": "done"}], "validate"),
    ("running", [], "plan"),
    ("paused", [{"role": "developer", "stage": "review", "status": "done"}], "critique"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed: lifecycle mapping not run")
def test_lifecycle_step_through_node():
    script = """
const vm = require("vm"), fs = require("fs");
const ctx = { registerView() {}, esc: String, api: async () => ({}), $: () => null, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(([status, tasks]) => ctx.cmLifecycleStep({ status, iteration: 1 }, tasks))));
"""
    p = subprocess.run(["node", "-e", script, str(VIEW), json.dumps(CASES)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == [want for _, _, want in CASES]


def test_report_detail_shows_tool_steps():
    app = (STATIC / "app.js").read_text()
    assert "function reportToolSteps(" in app
    assert "reportToolSteps(r.tool_steps)" in app


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed: tool steps not rendered")
def test_tool_steps_render_escaped_through_node():
    """The step shape of research_agent.plan_and_collect: a round with tool calls, a round that stopped."""
    app = (STATIC / "app.js").read_text()
    m = re.search(r"^function reportToolSteps\(.*?^}\n", app, re.S | re.M)
    assert m, "reportToolSteps not found in app.js"
    esc_line = next(line for line in app.splitlines() if line.startswith("const esc = "))
    steps = [{"round": 1, "model": "codex", "sufficient": False, "reason": "need papers", "refused": [],
              "results": [{"action": "search_papers", "query": "<b>reflexion</b>", "documents": [4, 5],
                           "titles": ["Reflexion"], "errors": []},
                          {"action": "fetch_url", "url": "http://x", "documents": [], "titles": [],
                           "errors": ["refused <private> address"]}]},
             {"round": 2, "model": "codex", "results": [], "stopped": "limit <hit>"}]
    script = f"""{esc_line}
{m.group(0)}
const a = process.argv[1] ? reportToolSteps(JSON.parse(process.argv[1])) : null;
console.log(JSON.stringify({{ html: a, none: reportToolSteps(undefined), empty: reportToolSteps([]) }}));
"""
    p = subprocess.run(["node", "-e", script, json.dumps(steps)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["none"] == "" and out["empty"] == ""
    html = out["html"]
    assert "Tool steps (4)" in html  # two calls, the reason of round 1, the stop of round 2
    assert "search_papers" in html and "fetch_url" in html and "2 document(s)" in html
    assert "&lt;b&gt;reflexion&lt;/b&gt;" in html and "<b>" not in html
    assert "refused &lt;private&gt; address" in html and "stopped: limit &lt;hit&gt;" in html
