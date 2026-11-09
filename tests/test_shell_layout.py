"""Offline, text-only checks of the application shell: the bottom bar's grid cascade and the projects hash fallback."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "agent_company" / "static"


def rules(css: str) -> list[tuple[int, str, str]]:
    """(position, selector list, declarations) of each rule outside @media blocks, in source order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, depth, start, sel = [], 0, 0, ""
    for i, c in enumerate(css):
        if c == "{":
            if depth == 0:
                sel = css[start:i].strip()
                body_start = i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                if not sel.startswith("@"):
                    out.append((i, sel, css[body_start:i]))
                start = i + 1
    return out


def columns(body: str) -> str:
    m = re.search(r"grid-template-columns\s*:\s*([^;]+)", body)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def test_bottom_bar_with_labels_is_one_column():
    found = rules((STATIC / "style.css").read_text())
    selectors = lambda sel: [s.strip() for s in sel.split(",")]
    labels = [pos for pos, sel, body in found if '.shell[data-labels="1"]' in selectors(sel) and columns(body)]
    bottom = [(pos, body) for pos, sel, body in found
              if '.shell[data-rail="bottom"][data-labels="1"]' in selectors(sel) and columns(body)]
    assert labels and bottom, "the labelled sidebar and bottom-bar grid rules must both exist"
    pos, body = bottom[-1]
    assert pos > labels[-1], "the bottom-bar override must come after the last labelled-sidebar rule"
    assert columns(body) in {"minmax(0,1fr)", "1fr"}
    plain = [p for p, sel, b in found if '.shell[data-rail="bottom"]' in selectors(sel) and columns(b)]
    assert plain[-1] > labels[-1] and columns(dict((p, b) for p, _, b in found)[plain[-1]]) in {"minmax(0,1fr)", "1fr"}


def test_unknown_project_route_rewrites_the_hash():
    js = (STATIC / "projects.js").read_text()
    start = js.index("async function viewProjects(")
    body = js[start:js.index("\nfunction ", start)]
    assert "writeHash(" in body
    assert '"#/projects"' in body and "`#/projects/${selectedProject}`" in body
    assert re.search(r"/\^#\\/projects\\/\\d\+/\.test\(location\.hash\)", body), "only a project route is rewritten"
    assert re.search(r"writeHash\([^;]*,\s*true\)", body), "the fallback replaces the history entry"
    assert 'store.set("ac.project", selectedProject)' in body
    # Shared globals: app.js (writeHash) loads before projects.js.
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/app.js") < html.index("/static/projects.js")
    assert "function writeHash(" in (STATIC / "app.js").read_text()
