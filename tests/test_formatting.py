"""Text formatting: Markdown to Telegram HTML, splitting, sizes, paths."""

import pytest

from analyst.plumbing import formatting as fmt
# --------------------------------------------------------------------------
# to_html
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("markdown", "expected"), [
    ("**bold**", "<b>bold</b>"),
    ("*italic*", "<i>italic</i>"),
    ("# Heading", "<b>Heading</b>"),
    ("### Deep heading", "<b>Deep heading</b>"),
    ("`code`", "<code>code</code>"),
    ("- item", "• item"),
    ("* item", "• item"),
    ("+ item", "• item"),
])
def test_markdown_subset_is_converted(markdown, expected):
    assert fmt.to_html(markdown) == expected


def test_fenced_block_becomes_pre():
    assert fmt.to_html("```python\nx = 1\n```") == "<pre>x = 1\n</pre>"


def test_html_in_the_model_reply_is_escaped():
    assert fmt.to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_code_span_contents_are_not_reinterpreted():
    assert fmt.to_html("`**not bold**`") == "<code>**not bold**</code>"


def test_blank_lines_between_sections_are_preserved():
    assert fmt.to_html("**Revenue**\n\n- one\n- two") == "<b>Revenue</b>\n\n• one\n• two"


def test_asterisk_inside_a_word_is_not_italics():
    assert fmt.to_html("2*3*4") == "2*3*4"


@pytest.mark.parametrize("value", ["", None])
def test_empty_input_is_safe(value):
    assert fmt.to_html(value) == ""


def test_realistic_reply_renders_as_expected():
    reply = "**Revenue $840 over 5 days**\n\n- Widget A leads at 50.6%\n- `output` untouched"
    assert fmt.to_html(reply) == (
        "<b>Revenue $840 over 5 days</b>\n\n"
        "• Widget A leads at 50.6%\n"
        "• <code>output</code> untouched"
    )


def test_strip_html_gives_plain_text():
    assert fmt.strip_html("<b>hi</b> <code>x</code>") == "hi x"


# --------------------------------------------------------------------------
# chunk_text
# --------------------------------------------------------------------------

def test_short_text_is_one_chunk():
    assert fmt.chunk_text("hello") == ["hello"]


def test_every_chunk_respects_the_limit():
    text = "\n".join(f"line {i}" for i in range(2000))
    chunks = fmt.chunk_text(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= fmt.MAX_MESSAGE for chunk in chunks)


def test_chunking_loses_nothing():
    text = "\n".join(f"line {i}" for i in range(2000))
    assert "".join(fmt.chunk_text(text)) == text


def test_a_single_enormous_line_is_split():
    text = "x" * (fmt.MAX_MESSAGE * 2 + 17)
    chunks = fmt.chunk_text(text)
    assert all(len(chunk) <= fmt.MAX_MESSAGE for chunk in chunks)
    assert "".join(chunks) == text


def test_split_prefers_line_boundaries():
    text = ("a" * 100 + "\n") * 100
    assert all(chunk.endswith("\n") for chunk in fmt.chunk_text(text)[:-1])


def test_limit_is_configurable():
    assert fmt.chunk_text("abcdef", limit=2) == ["ab", "cd", "ef"]


# --------------------------------------------------------------------------
# human_size
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("size", "expected"), [
    (0, "0 B"), (512, "512 B"), (1023, "1023 B"),
    (1024, "1.0 KB"), (2048, "2.0 KB"),
    (1024 * 1024, "1.0 MB"), (int(1.5 * 1024 * 1024), "1.5 MB"),
])
def test_human_size(size, expected):
    assert fmt.human_size(size) == expected


# --------------------------------------------------------------------------
# tidy_path
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("/output/analysis.py", "output/analysis.py"),
    ("./output/analysis.py", "output/analysis.py"),
    ("output/analysis.py", "output/analysis.py"),
    (r"output\analysis.py", "output/analysis.py"),
    ("  ./data/sales.csv  ", "data/sales.csv"),
])
def test_paths_are_normalised(raw, expected):
    assert fmt.tidy_path(raw) == expected


def test_absolute_path_inside_the_project_becomes_relative(sandbox):
    assert fmt.tidy_path(f"{sandbox}/output/plot.png") == "output/plot.png"


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_missing_path_is_labelled(raw):
    assert fmt.tidy_path(raw) == "(unnamed file)"


# --------------------------------------------------------------------------
# shorten_interpreter
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("command", "expected"), [
    (r"D:\proj\.venv\Scripts\python.exe ./out/a.py", "python ./out/a.py"),
    ("/usr/bin/python3.12 ./out/a.py", "python ./out/a.py"),
    ("/usr/local/bin/python3 -X utf8 a.py", "python -X utf8 a.py"),
    ("python ./out/a.py", "python ./out/a.py"),
    ("ls -la", "ls -la"),
    ("", ""),
])
def test_interpreter_path_collapses_to_python(command, expected):
    assert fmt.shorten_interpreter(command) == expected


def test_exe_suffix_is_not_left_behind():
    """Regression: one [\\d.]* class swallowed the dot before "exe" and
    rendered "pythonexe ./out/a.py"."""
    out = fmt.shorten_interpreter(r"C:\py\.venv\Scripts\python.exe a.py")
    assert out == "python a.py"


# --------------------------------------------------------------------------
# safe_filename
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("sales.csv", "sales.csv"),
    ("../../secret.json", "secret.json"),
    (r"..\..\..\windows\system32\evil.csv", "evil.csv"),
    ("/etc/passwd", "passwd"),
    ("weird name!.csv", "weird_name_.csv"),
    ("émojis🙂.csv", "_mojis_.csv"),
    ("", "upload"),
    (None, "upload"),
])
def test_upload_names_cannot_escape_the_data_directory(raw, expected):
    assert fmt.safe_filename(raw) == expected


def test_long_upload_name_is_truncated():
    assert len(fmt.safe_filename("x" * 500 + ".csv")) == 80


@pytest.mark.parametrize("raw", ["../../a", "a/b/c.csv", r"C:\x\y.csv"])
def test_safe_filename_never_returns_a_path(raw):
    name = fmt.safe_filename(raw)
    assert "/" not in name and "\\" not in name
