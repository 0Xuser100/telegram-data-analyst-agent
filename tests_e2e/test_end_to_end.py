"""One real run, start to finish, on gpt-4.1-mini-2025-04-14.

The agent inspects the sample CSV, writes a script, runs it, and charts it. This
drives the same loop the terminal front-end uses — `run_to_completion` — with an
approver that always says yes, which is the one thing a real user would not do.

    RUN_E2E=1 uv run pytest tests_e2e -v -s

Costs a few cents and needs the network.
"""

import glob
import os
import sys

import pytest
from langchain_core.utils.uuid import uuid7

from conftest import EXPECTED_MODEL

MAX_APPROVALS = 12          # a runaway loop must not spend money forever


class LoudApprover:
    """Approves everything and says what it approved.

    Satisfies the same Approver shape as the terminal `ConsoleApprover`, with
    the human replaced by "yes".
    """

    def __init__(self):
        self.decisions: list[dict] = []
        self.rounds = 0

    def ask(self, actions) -> None:
        self.rounds += 1
        print(f"[e2e] approving step {self.rounds}: {[a.name for a in actions]}")
        self.decisions = [{"type": "approve"} for _ in actions]


# --------------------------------------------------------------------------
# configuration (no API call)
# --------------------------------------------------------------------------

def test_the_configured_model_is_used(live_agent):
    assert live_agent.model.model_name == EXPECTED_MODEL


def test_compaction_is_set_for_this_model(live_agent):
    assert live_agent.summarizer._lc_helper.trigger == ("tokens", 40_000)
    assert live_agent.summarizer._lc_helper.keep == ("messages", 6)


# --------------------------------------------------------------------------
# one cheap round trip
# --------------------------------------------------------------------------

def test_the_model_answers(live_agent):
    reply = live_agent.model.invoke("Reply with the single word: OK")
    assert "ok" in reply.content.lower()


# --------------------------------------------------------------------------
# the whole thing
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def analysis(live_agent):
    """Run the real analysis once, approving every step, and return the reply."""
    from delivery import final_text
    from prompts import UPLOADED_FILE_TASK
    from runner import AgentRunner, TooManyApprovals, run_to_completion

    task = UPLOADED_FILE_TASK.format(
        file_path="./data/sales_data.csv", output_dir="./output"
    )
    print(f"\n[e2e] model={live_agent.model.model_name} python={sys.executable}")

    runner = AgentRunner(live_agent.agent)
    try:
        result = run_to_completion(runner, str(uuid7()), task, LoudApprover(),
                                   max_rounds=MAX_APPROVALS)
    except TooManyApprovals as exc:
        pytest.fail(str(exc))

    reply = final_text(result)
    print(f"\n[e2e] reply:\n{reply}\n")
    return reply


def test_the_run_finishes_with_a_reply(analysis):
    assert analysis.strip()


def test_a_script_was_written(analysis):
    """The prompt forbids inline `python -c`, so the code must be in a file the
    approval card can show."""
    assert glob.glob("./output/*.py"), "the agent never wrote a script"


def test_a_chart_was_produced(analysis):
    """A saved figure is proof the script really ran, not just that it was
    written."""
    charts = glob.glob("./output/*.png")
    assert charts, "no chart in output/"
    assert os.path.getsize(charts[0]) > 5_000


def test_the_real_numbers_are_reported(analysis):
    """The sample CSV totals 33 units and $840, so a correct run says 840."""
    assert "840" in analysis.replace(",", "")


def test_the_reply_says_what_the_data_is(analysis):
    """The prompt asks it to open with the shape of the data. The wording is the
    model's own — "5 rows", "5 records", "5 sales records" — so this checks the
    count and a row-ish noun in the opening sentence."""
    opening = analysis.lower()[:160]
    assert "5" in opening
    assert any(word in opening for word in ("row", "record", "entr", "observation"))


def test_the_reply_follows_the_chat_format(analysis):
    """The rules in SYSTEM_RULES: no file paths, no numbered section headings,
    and no labelling the headline instead of writing it."""
    lowered = analysis.lower()
    assert "./output" not in lowered
    assert ".png" not in lowered
    assert "1. data overview" not in lowered
    assert "headline:" not in lowered


def test_the_reply_stays_short(analysis):
    assert len(analysis) < 1500, f"reply was {len(analysis)} characters"
