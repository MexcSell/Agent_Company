"""PostToolUse hook for Claude Code runs that write (EL-R-05, after the self-correcting hooks of the evolution-loop
diagrams): after every Edit/Write the edited file is parsed, never run, and a syntax error goes back to the agent
(exit code 2: Claude Code shows stderr to the model), so it corrects the edit before the quality gate sees it.

Run as `python3 -I edit_check.py` with the hook's JSON on stdin, in the run's working directory. A `.py` file is
compiled in memory (`compile`, no .pyc is written); a `.js`/`.mjs`/`.cjs` file is checked with `node --check`.
Paths outside the working directory and other file types are ignored. Fail-open: anything unexpected exits 0.
"""

import json
import os
import shutil
import subprocess
import sys


def check(path: str) -> str:
    """The syntax error in `path`, or '' when it parses (or is not a checked type)."""
    if path.endswith(".py"):
        try:
            with open(path, "rb") as f:
                compile(f.read(), path, "exec", dont_inherit=True)
        except SyntaxError as e:
            return f"{path}:{e.lineno}: SyntaxError: {e.msg}"
        except (OSError, ValueError):
            return ""
        return ""
    if path.endswith((".js", ".mjs", ".cjs")) and shutil.which("node"):
        try:
            out = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return "" if out.returncode == 0 else (out.stderr or out.stdout).strip()[:2000]
    return ""


def main() -> int:
    try:
        event = json.load(sys.stdin)
        path = str((event.get("tool_input") or {}).get("file_path") or "")
        root = os.path.realpath(event.get("cwd") or os.getcwd())
        full = os.path.realpath(os.path.join(root, path))
    except (ValueError, AttributeError, TypeError):
        return 0
    if not path or not full.startswith(root + os.sep) or not os.path.isfile(full):
        return 0
    error = check(full)
    if error:
        print(f"The edit left a syntax error; fix it before you go on:\n{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
