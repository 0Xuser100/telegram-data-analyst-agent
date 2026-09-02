"""What runs without asking, and what still stops.

The gate exists because the agent has a real shell on this machine. Narrowing
it is a scope decision, not a security one, so these tests are the record of
exactly how narrow: one command shape auto-approves and everything else, however
harmless it looks, raises a card.
"""

import sys

import pytest

from analyst.agent import policy


def request_for(command: str):
    """The shape HumanInTheLoopMiddleware hands a `when` predicate."""
    class Request:
        tool_call = {"name": "execute", "args": {"command": command}}
    return Request()


def routine(command: str) -> bool:
    return policy.is_routine_execute(request_for(command))


PY = sys.executable


# --------------------------------------------------------------------------
# the one shape that runs without asking
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    f"{PY} output/analysis.py",
    f"{PY} ./output/analysis.py",
    f"{PY} output/sub/deeper.py",
    f'"{PY}" "output/name with spaces.py"',
])
def test_the_analysis_shape_runs_without_asking(command):
    assert routine(command) is True


def test_the_interpreter_is_matched_regardless_of_case():
    """Windows paths differ in case between what we pin and what the model
    echoes back; a card on every run would defeat the whole change."""
    assert routine(f"{PY.upper()} output/analysis.py") is True


# --------------------------------------------------------------------------
# everything else still asks
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    f"{PY} output/a.py | tee /tmp/x",           # pipe
    f"{PY} output/a.py && rm -rf .",            # chained
    f"{PY} output/a.py; cat .env",              # sequenced
    f"{PY} output/a.py > out.txt",              # redirect
    f"{PY} -c 'import os; os.system(\"x\")'",   # inline code
    f"{PY} output/a.py --flag",                 # extra argument
    f"{PY}",                                    # interpreter alone
])
def test_a_command_of_any_other_shape_still_asks(command):
    assert routine(command) is False


@pytest.mark.parametrize("path", [
    "analysis.py",                  # project root
    "../analysis.py",               # above the project
    "output/../secrets.py",         # traversal back out
    "data/analysis.py",             # the data directory
    "/etc/analysis.py",             # absolute, elsewhere
])
def test_a_script_outside_the_output_folder_still_asks(path):
    assert routine(f"{PY} {path}") is False


@pytest.mark.parametrize("command", [
    "python output/a.py",           # not the pinned interpreter
    "python3 output/a.py",
    "uv run output/a.py",
    "bash output/a.sh",
    "curl https://example.com",
])
def test_another_binary_still_asks(command):
    assert routine(command) is False


def test_a_script_that_is_not_python_still_asks():
    assert routine(f"{PY} output/analysis.txt") is False


# --------------------------------------------------------------------------
# malformed input must fail closed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["", None, '"unbalanced'])
def test_anything_unparseable_asks_rather_than_running(command):
    """A predicate that throws would take the run down; one that guesses would
    run something nobody read. It asks."""
    assert routine(command) is False


def test_a_call_without_a_command_asks():
    class Request:
        tool_call = {"name": "execute", "args": {}}
    assert policy.is_routine_execute(Request()) is False


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

def write_request(path):
    class Request:
        tool_call = {"name": "write_file", "args": {"file_path": path}}
    return Request()


@pytest.mark.parametrize("path", ["output/analysis.py", "./output/chart.png",
                                  "output/sub/findings.md"])
def test_a_write_into_the_workspace_runs_without_asking(path):
    assert policy.is_routine_write(write_request(path)) is True


@pytest.mark.parametrize("path", [
    "analysis.py",                  # project root
    "../escape.py",                 # above the project
    "output/../secrets.py",         # traversal back out of the workspace
    "data/tampered.csv",            # the input data
    ".env",                         # credentials
])
def test_a_write_anywhere_else_still_asks(path):
    assert policy.is_routine_write(write_request(path)) is False


def test_a_write_with_no_path_asks():
    class Request:
        tool_call = {"name": "write_file", "args": {}}
    assert policy.is_routine_write(Request()) is False


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

def read_request(path):
    class Request:
        tool_call = {"name": "read_file", "args": {"file_path": path}}
    return Request()


@pytest.mark.parametrize("path", [".env", "./.env", "checkpoints.sqlite",
                                  ".git/config", ".venv/pyvenv.cfg"])
def test_reading_a_secret_raises_a_card(path):
    """read_file has always been ungated, so the API keys were one silent tool
    call away. A card is weaker than a refusal -- interrupt_on cannot refuse --
    but it is the difference between visible and invisible."""
    assert policy.is_sensitive_read(read_request(path)) is True


@pytest.mark.parametrize("path", ["data/sales.csv", "output/findings.md",
                                  "README.md"])
def test_reading_ordinary_files_stays_silent(path):
    assert policy.is_sensitive_read(read_request(path)) is False


def test_a_read_with_no_path_asks():
    class Request:
        tool_call = {"name": "read_file", "args": {}}
    assert policy.is_sensitive_read(Request()) is True


# --------------------------------------------------------------------------
# shell metacharacters without spaces
# --------------------------------------------------------------------------

@pytest.mark.parametrize("script", [
    "output/a&calc&b.py",           # & chains on cmd
    "output/a|x.py",                # | pipes
    "output/a$(id)b.py",            # $( ) substitutes
    "output/a;rm.py",               # ; sequences
    "output/a`id`.py",              # backticks substitute
    "output/a>out.py",              # > redirects
    "output/a\nb.py",               # a newline is a command separator
])
def test_a_metacharacter_inside_the_token_still_asks(script):
    """Token counting alone does not catch these. shlex splits on whitespace,
    so `python output/a&calc&b.py` is exactly two tokens and passed every
    other check -- straight into subprocess.run(shell=True), which runs the
    `calc` between the ampersands."""
    assert routine(f"{PY} {script}") is False


# --------------------------------------------------------------------------
# the output directory is configurable
# --------------------------------------------------------------------------

def test_a_configured_output_directory_is_honoured(tmp_path, monkeypatch):
    """build_agent takes an output_dir and puts it in the prompt. If the
    predicate keeps resolving ./output, every legitimate call is refused and
    auto-approval silently reverts to a card on every step."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "figures").mkdir()

    class Write:
        tool_call = {"name": "write_file", "args": {"file_path": "figures/a.py"}}

    assert policy.is_routine_write(Write(), output_dir="./figures") is True
    assert policy.is_routine_write(Write()) is False        # the default still refuses


def test_a_configured_interpreter_is_honoured():
    class Run:
        tool_call = {"name": "execute",
                     "args": {"command": "/usr/bin/python3 output/a.py"}}

    assert policy.is_routine_execute(Run(), python_path="/usr/bin/python3") is True
    assert policy.is_routine_execute(Run()) is False


# --------------------------------------------------------------------------
# the path shape the tool schema actually asks for
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/output/01_quality.py", "/output/sub/a.png"])
def test_a_backend_absolute_path_is_still_routine(path):
    """deepagents runs its backend in virtual mode, where a leading "/" means
    the backend root -- and the write_file schema tells the model its path
    "must be absolute". os.path.join(cwd, "/output/a.py") discards the cwd, so
    containment failed and every routine write raised a card."""
    class Write:
        tool_call = {"name": "write_file", "args": {"file_path": path}}
    assert policy.is_routine_write(Write()) is True


def test_a_backend_absolute_path_outside_the_workspace_still_asks():
    class Write:
        tool_call = {"name": "write_file", "args": {"file_path": "/secrets.py"}}
    assert policy.is_routine_write(Write()) is False


def test_a_backend_absolute_script_is_still_routine():
    class Run:
        tool_call = {"name": "execute",
                     "args": {"command": f"{PY} /output/01_inspect.py"}}
    assert policy.is_routine_execute(Run()) is True
