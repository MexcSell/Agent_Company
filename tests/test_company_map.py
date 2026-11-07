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
    for banned in ("method:", "fetch(", "XMLHttpRequest"):
        assert banned not in js, f"the Company map is read-only: {banned} found"


def api_call_args(js):
    """The argument text of each `api(...)` call, split at its top-level commas."""
    calls = []
    for m in re.finditer(r"\bapi\(", js):
        depth, args, start, i = 1, [], m.end(), m.end()
        while depth:
            c = js[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 1:
                args.append(js[start:i])
                start = i + 1
            i += 1
        calls.append([*args, js[start:i - 1]])
    return calls


def test_view_api_calls_take_only_a_path():
    calls = api_call_args(VIEW.read_text())
    assert len(calls) >= 2, "the view loads its data through api()"
    assert all(len(a) == 1 for a in calls), f"an api() call with options (a write?): {calls}"
    assert api_call_args('api("/x", { method: "POST" })') == [['"/x"', ' { method: "POST" }']]


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
    ("recovering", [{"role": "developer", "stage": "in_progress", "status": "running"}], "execute"),
    ("done", [], "plan"),  # not a live status: the view never lists it, the function stays total
    ("running", [{"role": "developer", "stage": "created", "status": "pending"}], "execute"),
    ("running", [{"role": "developer", "stage": "completed", "status": "done"},
                 {"role": "developer", "stage": "blocked", "status": "pending"}], "execute"),
    ("running", [{"role": "developer", "stage": "completed", "status": "done"},
                 {"role": "qa", "stage": "assigned", "status": "assigned"}], "critique"),
    # the caller keeps only the goal's current iteration: tasks of an earlier one leave nothing, so plan
    ("running", [{"role": "developer", "stage": "completed", "status": "done", "iteration": 1},
                 {"role": "qa", "stage": "in_progress", "status": "running", "iteration": 1}], "plan"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed: lifecycle mapping not run")
def test_lifecycle_step_through_node():
    # The goal is in iteration 2; a task without an iteration belongs to it. The filter is the one
    # viewCompanyMap applies before cmLifecycleStep (t.iteration === g.iteration).
    script = """
const vm = require("vm"), fs = require("fs");
const ctx = { registerView() {}, esc: String, api: async () => ({}), $: () => null, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(([status, tasks]) => {
  const g = { status, iteration: 2 };
  const current = tasks.map((t) => ({ iteration: 2, ...t })).filter((t) => t.iteration === g.iteration);
  return ctx.cmLifecycleStep(g, current);
})));
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
