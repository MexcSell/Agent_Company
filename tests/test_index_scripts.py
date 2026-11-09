"""The build-free shell loads every first-party script, in dependency order."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

import agent_company
from agent_company.api import _index_html

STATIC = Path(agent_company.__file__).parent / "static"


class Shell(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.refs = []
        self.ids = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.ids.add(attrs.get("id"))
        if tag == "script" and "src" in attrs:
            self.scripts.append(attrs)
        for key in ("src", "href"):
            if attrs.get(key, "").startswith("/static/"):
                self.refs.append(attrs[key])


def test_index_scripts_complete_and_ordered():
    html = (STATIC / "index.html").read_text()
    shell = Shell(html)
    expected = {"/static/" + p.name for p in STATIC.glob("*.js")}
    first_party = [s for s in shell.scripts if not s["src"].startswith("/static/vendor/")]
    counts = Counter(s["src"] for s in first_party)
    assert all(counts[p] == 1 for p in expected)
    assert set(counts) == expected
    assert all("defer" in s for s in first_party)
    assert all((STATIC / ref.removeprefix("/static/")).is_file() for ref in shell.refs)
    scripts = [s["src"] for s in first_party]
    assert scripts[0] == "/static/app.js"
    assert scripts.index("/static/layouts.js") > 0
    assert html.index("() => start()") > max(html.index(s["src"]) for s in shell.scripts)
    assert {"drawer", "crumbs", "top-search", "view-title"} <= shell.ids


def test_server_versions_every_script_and_stylesheet():
    shell = Shell(_index_html())
    assert {"/static/style.css", "/static/workspace.css"} <= {r.split("?")[0] for r in shell.refs}
    assert all(re.fullmatch(r"/static/.+\.(?:js|css)\?v=[0-9a-f]{10}", r) for r in shell.refs)
