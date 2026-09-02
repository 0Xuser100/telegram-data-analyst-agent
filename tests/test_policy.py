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
