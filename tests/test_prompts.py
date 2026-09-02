"""The prompts are behaviour: the chat reply format, the chart-choice menu and
the never-claim-unverified-work rules all live here rather than in code. These
tests pin the parts that regressed in the past.
"""

import sys

import pytest

from analyst.agent.prompts import ANALYSIS_PROMPT, SYSTEM_RULES, UPLOADED_FILE_TASK

RENDERED = SYSTEM_RULES.format(python_path=sys.executable, output_dir="./output")

# The prompt is hard-wrapped, so a phrase that reads as one line in the source
# is split by a newline and two spaces. Assert against this when the phrase is
# longer than a few words.
FLAT = " ".join(RENDERED.split())


# --------------------------------------------------------------------------
# templates render
# --------------------------------------------------------------------------

def test_system_rules_render_with_exactly_these_placeholders():
    """A stray brace in the prompt breaks .format at import time in production,
    so this is the cheapest possible guard."""
    assert sys.executable in RENDERED
    assert "./output" in RENDERED
    assert "{" not in RENDERED and "}" not in RENDERED


def test_uploaded_file_task_renders():
    task = UPLOADED_FILE_TASK.format(file_path="./data/x.csv", output_dir="./output")
    assert "./data/x.csv" in task
    assert "{" not in task


def test_analysis_prompt_renders():
    message = ANALYSIS_PROMPT.format_messages(
        data_path="./data/sales.csv",
        python_path=sys.executable,
        output_dir="./output",
        plot_name="plot.png",
    )[0]
    assert "./data/sales.csv" in message.content
    assert "plot.png" in message.content


@pytest.mark.parametrize("missing", ["python_path", "output_dir"])
def test_system_rules_need_both_variables(missing):
    kwargs = {"python_path": "py", "output_dir": "./output"}
    kwargs.pop(missing)
    with pytest.raises(KeyError):
        SYSTEM_RULES.format(**kwargs)


# --------------------------------------------------------------------------
# execution rules
# --------------------------------------------------------------------------

def test_the_interpreter_is_mandated():
    assert "Never use 'python', 'python3', 'uv'" in RENDERED


def test_delegation_is_forbidden():
    """A sub-agent cannot execute commands with this backend, so a delegated
    run silently produces nothing."""
    assert "Do NOT use the task tool" in RENDERED


def test_inline_code_is_forbidden():
    """The approval card shows a file; `python -c` hides the code inside a
    command string, so nothing reviewable reaches the person deciding."""
    assert "Never pass code inline" in RENDERED
    assert "heredoc" in RENDERED


def test_package_installation_is_forbidden():
    assert "Do NOT try to install packages" in RENDERED


def test_small_talk_does_not_trigger_an_analysis():
    assert "greeting" in RENDERED.lower()


# --------------------------------------------------------------------------
# reply format — the complaint that started this
# --------------------------------------------------------------------------

def test_file_paths_are_banned_from_the_reply():
    assert "Do NOT mention file paths" in RENDERED


def test_saved_to_phrasing_is_banned():
    assert 'never write\n  "saved to ..."' in RENDERED or "saved to ..." in RENDERED


def test_numbered_section_headings_are_banned():
    assert "no numbered section headings" in FLAT


def test_the_reply_sounds_like_a_colleague():
    assert "like a colleague who just did the work" in FLAT
    assert "no bullet lists" in RENDERED
    assert "no emoji" in RENDERED


def test_the_main_finding_is_still_bold():
    """One anchor survives the move away from a report format."""
    assert "on its own line in bold" in FLAT


def test_judgement_calls_belong_in_the_body():
    """The sentence explaining what was filtered and why is what makes the
    numbers trustworthy; a footnote is not where it goes."""
    assert "judgement call" in RENDERED
    assert "most valuable one you will write" in FLAT


def test_a_quality_problem_becomes_the_headline():
    assert "qualify the analysis, it is the headline" in FLAT


def test_dull_data_may_be_called_dull():
    assert "manufactured insight" in RENDERED


def test_a_truncated_run_must_admit_it():
    assert "limit stopped your run early" in FLAT


def test_the_length_cap_is_gone():
    assert "900 characters" not in RENDERED
    assert "There is no length limit" in FLAT


# --------------------------------------------------------------------------
# looking at the data — raw rows must not enter the conversation
# --------------------------------------------------------------------------

def test_reading_the_raw_data_file_is_forbidden():
    """A 60-column CSV read with read_file filled the context three times over
    and forced three compactions in five seconds."""
    assert "Do NOT open a data file with read_file" in RENDERED


@pytest.mark.parametrize("fact", ["shape", "dtypes", "df.head(10)", "df.describe()"])
def test_the_inspect_step_prints_compact_facts(fact):
    assert fact in RENDERED


def test_wide_files_get_a_narrower_summary():
    assert "more than 25 columns" in RENDERED
    assert "transpose" in RENDERED.lower()


def test_the_agent_is_told_to_plan_and_revise():
    """The planning tool has been wired in all along and the prompt never
    mentioned it, so the agent worked one pass and stopped."""
    assert "write_todos" in RENDERED
    assert "REVISE that plan" in FLAT


def test_the_loop_has_a_stop_condition():
    """Without one an iterative agent circles until a bound kills it."""
    assert "stop telling you anything new" in FLAT


def test_findings_are_written_down_not_remembered():
    """Compaction replaces the conversation; the filesystem survives it."""
    assert "findings.md" in RENDERED
    assert "compacted" in RENDERED


def test_one_pass_is_no_longer_prescribed():
    assert "One inspect step is enough" not in RENDERED


def test_aggregates_must_be_looked_for_before_summing():
    """The failure that started this: summing a file that mixes national
    totals with per-state rows produced a figure three times too large,
    reported with no hedge."""
    assert "AGGREGATES HIDING IN THE DATA" in RENDERED
    assert "before you sum" in RENDERED.lower()


def test_the_inspect_step_counts_distinct_values():
    """Distinct-value counts are how the aggregate row becomes visible."""
    assert "number of distinct values in every categorical column" in FLAT


def test_the_denominator_must_be_stated():
    assert "which denominator" in RENDERED


def test_the_answer_starts_with_what_the_data_is():
    assert "Open with what the data is" in FLAT


# --------------------------------------------------------------------------
# chart choice — the figure must follow the dataset
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape", [
    "line", "area", "bar", "pie", "donut", "scatter", "bubble",
    "histogram", "box", "violin", "heatmap", "lollipop", "stacked bar",
])
def test_the_chart_menu_offers_every_shape(shape):
    assert shape in RENDERED.lower()


def test_repeating_the_same_figure_is_discouraged():
    assert "Do not draw the same figure every" in RENDERED


@pytest.mark.parametrize("rule", [
    "date column",          # time series
    "categorical",          # ranked bar
    "two numerics",         # scatter
    "correlation matrix",   # heatmap
])
def test_column_shapes_map_to_chart_types(rule):
    assert rule in RENDERED.lower()


def test_pie_charts_are_capped():
    assert "six slices at most" in RENDERED


def test_every_analysis_has_a_floor_of_two_figures():
    """One figure meant data quality was never shown, and invisible data
    quality is how a confident wrong number gets out."""
    assert "AT LEAST two figures" in FLAT
    assert "data-quality figure" in RENDERED
    assert "main comparison" in RENDERED


@pytest.mark.parametrize("panel", ["trend", "distribution", "correlation",
                                   "segment comparison", "rate against count"])
def test_the_conditional_panels_each_name_their_trigger(panel):
    assert panel in RENDERED


def test_an_unsupported_panel_is_left_out_not_drawn_empty():
    assert "LEFT OUT" in RENDERED
    assert "never drawn empty" in FLAT


def test_the_figure_count_is_capped():
    assert "Five figures is the ceiling" in FLAT


def test_thin_data_gets_one_figure_and_an_explanation():
    assert "two columns, or thirty rows" in FLAT


def test_figures_are_named_in_reading_order():
    """Delivery sorts by name, so the name is what fixes the order."""
    assert "01_quality.png" in RENDERED
    assert "reading order" in RENDERED


def test_one_figure_is_no_longer_prescribed():
    assert "Build ONE figure" not in RENDERED


def test_the_seaborn_palette_trap_is_a_positive_instruction():
    """It was already warned about, and the model tripped it anyway, costing an
    edit and a re-run. A warning that does not prevent the error it names is
    not pulling its weight."""
    assert "ALWAYS pass `hue=<same column as x>, legend=False`" in FLAT


def test_show_is_forbidden():
    """plt.show() blocks forever in a headless run."""
    assert "Do NOT call plt.show()" in RENDERED


# --------------------------------------------------------------------------
# verification rules
# --------------------------------------------------------------------------

def test_unverified_results_are_forbidden():
    assert "NEVER CLAIM UNVERIFIED WORK" in RENDERED


def test_writing_is_distinguished_from_running():
    assert "Writing a script is not running it" in RENDERED


def test_a_rejected_run_must_be_admitted():
    assert "If an execute call is rejected" in RENDERED


# --------------------------------------------------------------------------
# the uploaded-file task
# --------------------------------------------------------------------------

def test_upload_task_defers_to_the_chart_rules():
    task = UPLOADED_FILE_TASK.format(file_path="./data/x.csv", output_dir="./output")
    assert "chart-choice rules" in " ".join(task.split())


def test_upload_task_asks_for_an_inspect_step_first():
    task = UPLOADED_FILE_TASK.format(file_path="./data/x.csv", output_dir="./output")
    assert "inspect it with a small script" in " ".join(task.split())


def test_upload_task_requires_the_script_to_actually_run():
    task = UPLOADED_FILE_TASK.format(file_path="./data/x.csv", output_dir="./output")
    assert "run the script" in task


def test_upload_task_asks_for_the_chat_format():
    task = UPLOADED_FILE_TASK.format(file_path="./data/x.csv", output_dir="./output")
    assert "no file paths" in task


def test_cli_prompt_no_longer_forces_a_fixed_dashboard():
    message = ANALYSIS_PROMPT.format_messages(
        data_path="d", python_path="p", output_dir="o", plot_name="n.png",
    )[0].content
    assert "Swap any panel" in message
    assert "no numbered section headings" in message
