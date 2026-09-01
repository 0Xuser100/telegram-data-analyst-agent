"""Terminal front-end: one analysis run, approvals typed at the prompt.

Run with:  uv run analyst-cli  (from the repository root)
"""

import sys

from analyst.config import apply_tracing_env, get_settings

settings = get_settings()
apply_tracing_env(settings)          # before the graph is imported

from langchain_core.utils.uuid import uuid7  # noqa: E402

from analyst.agent.builder import OUTPUT_DIR, agent  # noqa: E402
from analyst.conversation.approvals import ConsoleApprover  # noqa: E402
from analyst.plumbing.backend import ensure_sample_data  # noqa: E402
from analyst.conversation.delivery import final_text  # noqa: E402
from analyst.agent.prompts import ANALYSIS_PROMPT  # noqa: E402
from analyst.agent.runner import AgentRunner, TooManyApprovals, run_to_completion  # noqa: E402

DATA_FILE = "./data/sales_data.csv"
PLOT_NAME = "sales_plot.png"

# A stuck loop must not run forever; a real run needs two to four rounds.
MAX_APPROVAL_ROUNDS = 12


def run_analysis(runner: AgentRunner, approver: ConsoleApprover, thread_id: str) -> str:
    """Start the run, then approve or reject until the agent is done.

    The loop itself lives in runner.run_to_completion, which the end-to-end
    test drives with an approver that always says yes.
    """
    messages = ANALYSIS_PROMPT.format_messages(
        data_path=DATA_FILE,
        python_path=sys.executable,
        output_dir=OUTPUT_DIR,
        plot_name=PLOT_NAME,
    )
    try:
        result = run_to_completion(runner, thread_id, messages, approver,
                                   max_rounds=MAX_APPROVAL_ROUNDS)
    except TooManyApprovals as exc:
        raise SystemExit(str(exc)) from exc
    return final_text(result)


def main() -> None:
    ensure_sample_data()
    runner = AgentRunner(agent)               # silent: the prompts are the feedback
    answer = run_analysis(runner, ConsoleApprover(), str(uuid7()))

    print("\n" + "=" * 60)
    print(answer or "The agent produced no text reply.")
    print("=" * 60)
    print(f"Files written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
