"""PostToolUse hook for Claude Code runs that write (EL-R-05, after the self-correcting hooks of the evolution-loop
diagrams): after every Edit/Write the edited file is parsed, never run, and a syntax error goes back to the agent
(exit code 2: Claude Code shows stderr to the model), so it corrects the edit before the quality gate sees it.

Run as `python3 -I edit_check.py` with the hook's JSON on stdin, in the run's working directory. A `.py` file is
compiled in memory (`compile`, no .pyc is written); a `.js`/`.mjs`/`.cjs` file is checked with `node --check`.
Paths outside the working directory and other file types are ignored. Fail-open: anything unexpected exits 0.

With file arguments it is the syntax check of agent runs (`cli`, the CLAUDE_CHECK/COPILOT_CHECK entry): options are
refused, only files inside the working directory are checked, and a missing `node` fails instead of passing.
"""

import json
import os
import shutil
import subprocess
import sys

JS = (".js", ".mjs", ".cjs")


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
    if path.endswith(JS) and shutil.which("node"):
        # preloads (`--require x` in NODE_OPTIONS) would run code before the check
        env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
        try:
            out = subprocess.run(["node", "--check", os.path.abspath(path)], capture_output=True, text=True,
                                 timeout=20, env=env)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return "" if out.returncode == 0 else (out.stderr or out.stdout).strip()[:2000]
    return ""


def cli(args: list[str]) -> int:
    """The check command of agent runs (`python3 -I edit_check.py <files>`): the platform builds node's command line
    itself, so no argument can add a flag. 0 all parse, 1 a syntax error or a refused argument, 2 no files, 3 no node."""
    if not args:
        print("usage: edit_check.py <files>", file=sys.stderr)
        return 2
    root, rc = os.path.realpath(os.getcwd()), 0
    for arg in args:
        full = os.path.realpath(os.path.join(root, arg))
        if arg.startswith("-"):
            print(f"{arg}: option not allowed")
            rc = max(rc, 1)
        elif not full.startswith(root + os.sep) or not os.path.isfile(full):
            print(f"{arg}: not a file in the working directory")
            rc = max(rc, 1)
        elif not full.endswith((".py", *JS)):
            print(f"{arg}: not a checked file type")
            rc = max(rc, 1)
        elif full.endswith(JS) and not shutil.which("node"):
            print(f"{arg}: node not found, the check did not run")
            rc = 3
        else:
            error = check(full)
            print(f"{arg}: {error}" if error else f"OK {arg}")
            rc = max(rc, 1) if error else rc
    return rc


def main() -> int:
    if len(sys.argv) > 1:
        return cli(sys.argv[1:])
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
