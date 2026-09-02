"""What the agent may do without asking a human first.

READ THIS BEFORE WIDENING ANYTHING HERE.

The approval gate exists because `LocalShellBackend.execute` runs
`subprocess.run(..., shell=True)` on this machine with no sandbox, no isolation
and no restrictions — deepagents says so itself, under a "danger" admonition.
`root_dir="."` sets a working directory; it is not a jail.

So the gate was never "the agent can only touch output/". It was "a human reads
every command before it runs". This module gives that up on purpose, because an
iterative analysis is fifteen approval taps and nobody reads the fifteenth.

What that leaves is a SCOPE control, not a security control. These predicates
constrain the *shape* of a call; they say nothing about the *contents* of the
script a command runs, and that script is arbitrary Python written by a model,
free to read this file, the credentials beside it, or the network. The
protection is that you trust the model and you own the machine — not this code.

Why the containment checks are hand-written
-------------------------------------------
`FilesystemPermission` would have owned path containment for us, and that was
the plan. It cannot be used here: deepagents raises outright when `permissions`
is combined with a backend that can execute commands, which is the only kind of
backend this app can use. So containment is ours, and `_under_output` resolves
paths rather than comparing strings, because `output/../secrets.py` is a string
that starts with "output/".

That refusal also caps what is achievable. `interrupt_on` can only choose
between asking and not asking — it cannot refuse. Reading `.env` therefore
raises a card rather than being denied. That is still a real improvement on
today, where `read_file` is ungated and the API keys are one silent tool call
away, but it is a weaker guarantee than a deny rule, and it is the reason this
module is not the last word on the subject.
"""

import os
import re
import shlex
import sys

OUTPUT_DIR = "./output"

# Everything a legitimate `<interpreter> <script>.py` needs, and nothing a shell
# gives meaning to. Token counting alone does NOT catch these: shlex splits on
# whitespace, so `python output/a&calc&b.py` is two tokens and would sail
# through every other check straight into subprocess.run(shell=True).
_SAFE_COMMAND = re.compile(r'^[A-Za-z0-9 _.:/\\"-]+$')

# Reading any of these should never be routine. Not a security boundary — a
# script the agent runs can still open them — but the agent's own file tools
# must not reach them without a human looking.
SENSITIVE = (".env", ".env.example", "checkpoints.sqlite", ".git", ".venv")


def _resolve(path: str) -> str:
    return os.path.realpath(os.path.join(os.getcwd(), path))


def _under_output(path: str, output_dir: str = OUTPUT_DIR) -> bool:
    """True when `path` resolves inside the output directory.

    Resolved, not compared as text, so `output/../secrets.py` is caught.
    """
    root = os.path.realpath(output_dir)
    try:
        return os.path.commonpath([root, _resolve(path)]) == root
    except ValueError:                      # different drive on Windows
        return False


def _argument(request, name: str) -> str | None:
    args = (getattr(request, "tool_call", None) or {}).get("args") or {}
    value = args.get(name)
    return value if isinstance(value, str) and value.strip() else None


def is_routine_execute(request, python_path: str = None,
                       output_dir: str = OUTPUT_DIR) -> bool:
    """True when this `execute` call is the ordinary analysis shape.

    A whitelist of exactly one shape — the pinned interpreter, one `.py` file,
    inside the output directory — because the alternative is blacklisting shell
    metacharacters, which is a game you lose. Anything unparseable or
    unexpected returns False and raises a card: the failure mode has to be
    asking, never running.

    This constrains the command. It does not constrain what the script does.
    """
    interpreter = python_path or sys.executable
    command = _argument(request, "command")
    if command is None:
        return False

    # Before anything else: no character the shell would act on. The command
    # reaches subprocess.run(shell=True), so `&`, `|`, `;`, backticks and
    # `$(...)` all execute -- and none of them need a space around them.
    if not _SAFE_COMMAND.match(command):
        return False

    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:                      # unbalanced quotes
        return False

    if len(tokens) != 2:                    # no flags, no pipes, no second file
        return False

    binary, script = (token.strip('"') for token in tokens)
    if os.path.normcase(binary) != os.path.normcase(interpreter):
        return False
    if not script.lower().endswith(".py"):
        return False
    return _under_output(script, output_dir)


def is_routine_write(request, output_dir: str = OUTPUT_DIR) -> bool:
    """True when this write lands inside the agent's own workspace.

    Covers `write_file` and `edit_file` alike: same operation, same risk.
    """
    path = _argument(request, "file_path")
    return False if path is None else _under_output(path, output_dir)


def is_sensitive_read(request) -> bool:
    """True when a read touches something a human should see going past."""
    path = _argument(request, "file_path")
    if path is None:
        return True                         # unreadable call: ask
    resolved = _resolve(path)
    parts = {part.lower() for part in resolved.replace("\\", "/").split("/")}
    return any(name.lower() in parts for name in SENSITIVE)
