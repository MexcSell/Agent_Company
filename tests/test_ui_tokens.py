"""AICO design tokens (UI-D-03, UI-SHELL-01/02): every colour of style.css and workspace.css is a token.

A hex colour may appear only in a token block: a rule whose every selector is `:root`, optionally followed by
`[data-theme="…"]`/`[data-accent="…"]`. vendor/ is never scanned (only the two files are read) and the inside of
`url(...)` is blanked, so %23-encoded colours in SVG data URIs and font URLs do not count.
"""
import re
from pathlib import Path

import pytest

import agent_company

STATIC = Path(agent_company.__file__).parent / "static"
FILES = ["style.css", "workspace.css"]
TOKEN_SELECTOR = re.compile(r'^:root(\[data-(theme|accent)="[a-z-]+"\])*$')
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# (file, selector) -> reason; empty: no rule outside the token blocks may carry a hex colour.
ALLOW: dict[tuple[str, str], str] = {}

COLOR_TOKENS = ["--color-bg", "--color-sidebar", "--color-panel", "--color-elevated", "--color-border", "--color-text",
                "--color-muted", "--color-primary", "--color-success", "--color-warning", "--color-error", "--color-info"]
OTHER_TOKENS = ["--space-1", "--radius-card", "--radius-control", "--fs-body", "--fs-title", "--fs-section", "--fs-meta",
                "--font"]
OLD_NAMES = ["--bg", "--panel", "--panel-2", "--border", "--text", "--muted", "--accent", "--accent-text", "--ok",
             "--warn", "--bad", "--info"]
ACCENTS = ["indigo", "violet", "blue", "green", "amber", "rose", "grey"]
COMPONENTS = [".page-head", ".kpi", ".card-head", ".view-all", ".badge", ".badge.ok", ".badge.warn", ".badge.bad",
              ".badge.info", ".badge.neutral", ".progress", ".stepper", ".timeline", ".drawer", ".empty", ".tabs"]


def clean(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"url\((?:\"[^\"]*\"|'[^']*'|[^)]*)\)", "url()", css)


def rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) of every style rule, entering the body of any at-rule that holds rules."""
    out, i, n = [], 0, len(css)
    while i < n:
        open_ = css.find("{", i)
        if open_ < 0:
            break
        head = css[i:open_]
        if ";" in head and head.strip().startswith("@"):  # a statement at-rule (@import …;) before the block
            head = head[head.rindex(";") + 1:]
        depth, j = 1, open_ + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[open_ + 1:j - 1]
        sel = head.strip()
        if sel.startswith("@") and "{" in body:
            out += rules(body)
        else:
            out.append((sel, body))
        i = j
    return out


def values(body: str) -> list[str]:
    return [d.split(":", 1)[1] for d in body.split(";") if ":" in d]


def is_token_block(sel: str) -> bool:
    return all(TOKEN_SELECTOR.match(s.strip()) for s in sel.split(","))


def parsed(name: str) -> list[tuple[str, str]]:
    return rules(clean((STATIC / name).read_text()))


def defined(name: str, selector: str) -> set[str]:
    return {m for sel, body in parsed(name) if sel == selector for m in re.findall(r"(--[\w-]+)\s*:", body)}


@pytest.mark.parametrize("name", FILES)
def test_no_hex_outside_token_blocks(name):
    bad = [f"{name}: {sel} -> {c}" for sel, body in parsed(name)
           if not is_token_block(sel) and (name, sel) not in ALLOW
           for v in values(body) for c in HEX.findall(v)]
    assert not bad, "hex colours outside the token blocks:\n" + "\n".join(bad)


def test_scanner_finds_a_planted_colour():
    css = clean(':root { --a: #fff; } .x { color: #123456; background: url("data:image/svg+xml,%23abcdef"); }'
                ' @media (max-width: 1px) { .y { border-color: #abc; } } @keyframes k { to { color: red; } }')
    found = [(s, c) for s, b in rules(css) if not is_token_block(s) for v in values(b) for c in HEX.findall(v)]
    assert found == [(".x", "#123456"), (".y", "#abc")]
    assert is_token_block(':root[data-theme="light"]') and not is_token_block(':root[data-theme="dark"] .x')


def test_workspace_has_no_palette_of_its_own():
    assert not [sel for sel, _ in parsed("workspace.css") if sel.startswith(":root") and "." not in sel]


def test_root_defines_new_tokens_and_old_names():
    root = defined("style.css", ":root")
    missing = [t for t in COLOR_TOKENS + OTHER_TOKENS + OLD_NAMES if t not in root]
    assert not missing, missing


# UI-D-09: the _webdesign "Lumen Desk" DESIGN.md colours (background, surface, border, text, muted, primary, accent);
# status colours are not in that file and keep the AICO values where they pass 4.5:1 on the surface.
DARK = {"--color-bg": "#0B1016", "--color-sidebar": "#151C24", "--color-panel": "#151C24", "--color-border": "#2B323B",
        "--color-text": "#F0F3F7", "--color-muted": "#A9B2BE", "--color-primary": "#74B1FF", "--color-on-primary": "#111111",
        "--color-accent": "#A79FFF", "--color-success": "#16A34A", "--color-warning": "#D97706", "--color-error": "#F87171",
        "--color-info": "#0EA5E9"}
LIGHT = {"--color-bg": "#F6F9FE", "--color-sidebar": "#E8EFF9", "--color-panel": "#E8EFF9", "--color-border": "#C5CFDB",
         "--color-text": "#1B232C", "--color-muted": "#5A6471", "--color-primary": "#046ACD", "--color-on-primary": "#FFFFFF",
         "--color-accent": "#7E73D4", "--color-success": "#166534", "--color-warning": "#92400E", "--color-error": "#B91C1C",
         "--color-info": "#0369A1"}
DESIGN_TOKENS = ["--radius-sm", "--radius-md", "--radius-lg", "--radius-full", "--shadow-sm", "--shadow-md", "--shadow-lg",
                 "--duration-fast", "--duration-base", "--duration-slow", "--ease", "--control-height", "--text-xs",
                 "--text-sm", "--text-base", "--text-lg", "--text-xl", "--border-width"]


def block(selector: str) -> str:
    return "".join(body for sel, body in parsed("style.css") if sel == selector)


def contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("selector,palette", [(":root", DARK), (':root[data-theme="light"]', LIGHT)])
def test_theme_holds_the_design_file_values(selector, palette):
    body = block(selector)
    for token, value in palette.items():
        assert re.search(rf"{token}:\s*{value};", body, re.I), (selector, token)


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_text_contrast_on_the_surface(palette):
    """Body, muted and status text 4.5:1 on cards; the primary button's label 4.5:1; the focus ring 3:1."""
    surface = palette["--color-panel"]
    for token in ["--color-text", "--color-muted", "--color-success", "--color-warning", "--color-error", "--color-info"]:
        assert contrast(palette[token], surface) >= 4.5, token
    assert contrast(palette["--color-on-primary"], palette["--color-primary"]) >= 4.5
    assert contrast(palette["--color-accent"], palette["--color-bg"]) >= 3


def test_design_tokens_focus_ring_and_reduced_motion():
    assert not [t for t in DESIGN_TOKENS if t not in defined("style.css", ":root")]
    css = clean((STATIC / "style.css").read_text())
    assert re.search(r":focus-visible\s*\{\s*outline:\s*2px solid var\(--color-accent\);\s*outline-offset:\s*2px", css)
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_light_theme_defines_the_same_colour_tokens():
    light = defined("style.css", ':root[data-theme="light"]')
    assert set(COLOR_TOKENS) <= light, set(COLOR_TOKENS) - light


@pytest.mark.parametrize("accent", [a for a in ACCENTS if a != "blue"])
def test_accent_rules(accent):
    assert "--accent" in defined("style.css", f':root[data-accent="{accent}"]')


def test_accent_names_in_the_picker():
    css = (STATIC / "style.css").read_text()
    js = (STATIC / "app.js").read_text()
    for a in ACCENTS:
        assert f'.accent-swatch[data-accent="{a}"]' in css
        assert f'"{a}"' in js
    assert "ac.accent" in js


def test_component_classes_exist():
    selectors = " ".join(sel for sel, _ in parsed("style.css"))
    missing = [c for c in COMPONENTS if not re.search(re.escape(c) + r"(?![\w-])", selectors)]
    assert not missing, missing


def test_drawer_markup_and_helpers():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert 'id="drawer"' in html and 'id="drawer-close"' in html and 'id="drawer-body"' in html
    assert "function openDrawer(" in js and "function closeDrawer(" in js
    assert 'class="badge"' not in js  # the rail's count bubble is .nav-count; .badge is the status badge
