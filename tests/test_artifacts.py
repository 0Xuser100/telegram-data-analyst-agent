"""Finding the figures a run produced, and catching a reply that invents one."""

import os
import time

import pytest

from analyst.plumbing.artifacts import ArtifactCollector

CHAT = 555


@pytest.fixture
def collector(clean_output):
    return ArtifactCollector(clean_output)


def make_image(directory: str, name: str = "plot.png", age: float = 0) -> str:
    path = os.path.join(directory, name)
    open(path, "wb").close()
    if age:
        os.utime(path, (time.time() - age, time.time() - age))
    return path


# --------------------------------------------------------------------------
# finding images
# --------------------------------------------------------------------------

def test_image_named_in_the_reply_is_found(collector, clean_output):
    make_image(clean_output)
    collector.start_run(CHAT)
    found = collector.new_images(CHAT, f"saved to {clean_output}/plot.png")
    assert [os.path.basename(p) for p in found] == ["plot.png"]


def test_unmentioned_image_is_still_found_by_mtime(collector, clean_output):
    """The reply is not supposed to name files, so the scan is the main way."""
    make_image(clean_output, "figure.png")
    collector.start_run(CHAT)
    found = collector.new_images(CHAT, "**Revenue $840** across 5 days")
    assert [os.path.basename(p) for p in found] == ["figure.png"]


def test_images_from_an_earlier_run_are_ignored(collector, clean_output):
    make_image(clean_output, "old.png", age=3600)
    collector.start_run(CHAT)
    assert collector.new_images(CHAT, "done") == []


def test_the_same_image_is_never_sent_twice(collector, clean_output):
    """A run spans several approvals; each delivery must not resend the figure."""
    make_image(clean_output)
    collector.start_run(CHAT)
    assert len(collector.new_images(CHAT)) == 1
    assert collector.new_images(CHAT) == []


def test_non_image_files_are_ignored(collector, clean_output):
    open(os.path.join(clean_output, "analysis.py"), "wb").close()
    collector.start_run(CHAT)
    assert collector.new_images(CHAT, "wrote analysis.py") == []


@pytest.mark.parametrize("name", ["a.png", "b.jpg", "c.jpeg", "d.webp", "e.gif"])
def test_every_supported_extension_is_collected(collector, clean_output, name):
    make_image(clean_output, name)
    collector.start_run(CHAT)
    assert len(collector.new_images(CHAT)) == 1


def test_chats_do_not_share_state(collector, clean_output):
    make_image(clean_output)
    collector.start_run(555)
    collector.start_run(777)
    assert len(collector.new_images(555)) == 1
    assert len(collector.new_images(777)) == 1      # same file, other chat


def test_forget_lets_a_figure_be_sent_again(collector, clean_output):
    """A new conversation should not inherit what the old one delivered."""
    make_image(collector._output_dir)
    collector.start_run(CHAT)
    collector.new_images(CHAT)
    collector.forget(CHAT)
    collector.start_run(CHAT)
    assert len(collector.new_images(CHAT)) == 1


def test_a_missing_output_directory_is_not_an_error(sandbox):
    collector = ArtifactCollector(os.path.join(sandbox, "does-not-exist"))
    collector.start_run(CHAT)
    assert collector.new_images(CHAT, "no chart here") == []


def test_the_clock_is_injectable(clean_output):
    """Deterministic tests need control over "now"."""
    collector = ArtifactCollector(clean_output, clock=lambda: 0)
    collector.start_run(CHAT)
    make_image(clean_output)
    assert len(collector.new_images(CHAT)) == 1


# --------------------------------------------------------------------------
# the honesty check
# --------------------------------------------------------------------------

def test_named_but_missing_file_is_reported(collector):
    warning = collector.warning_for("saved to ./output/nope.png", images_sent=0)
    assert "does not exist" in warning


def test_an_analysis_that_produced_nothing_is_reported(collector):
    """Structural, not lexical. The old check looked for "saved"/"created"
    beside "chart"; the reply voice uses none of those words, so it went quiet
    exactly when it was needed. Every analysis now owes at least two figures,
    so zero figures after a real run is wrong whatever the text says."""
    warning = collector.warning_for("Heart disease leads at 33.7%.",
                                    images_sent=0, analysed=True)
    assert "produced no chart" in warning


def test_no_warning_when_an_image_was_sent(collector):
    assert collector.warning_for("Chart's below.", images_sent=1,
                                 analysed=True) is None


@pytest.mark.parametrize("text", [
    "Hello! Send me a CSV and I'll take a look.",
    "I can analyse spreadsheets — upload one and ask me anything.",
    "",
])
def test_a_reply_that_analysed_nothing_is_not_flagged(collector, text):
    """A greeting legitimately produces no figures."""
    assert collector.warning_for(text, images_sent=0, analysed=False) is None


def test_an_existing_file_is_not_called_missing(collector, clean_output, monkeypatch):
    """Paths in a reply are project-relative, which is what the regex matches."""
    make_image(clean_output)
    monkeypatch.chdir(os.path.dirname(clean_output))
    assert collector.warning_for("saved to ./output/plot.png", images_sent=1) is None
