"""The workspace: a shell backend plus the sample CSV."""

import csv
import os

from analyst.plumbing import backend as bk
def test_importing_the_module_writes_nothing(sandbox):
    """Creating the backend must not touch the disk; the old version wrote the
    sample CSV as an import side effect."""
    assert bk.backend is not None


def test_the_backend_can_run_commands(sandbox):
    """LocalShellBackend, not FilesystemBackend: without execution the agent can
    write a script but never run it."""
    assert hasattr(bk.create_backend(), "execute")


def test_ensure_directories_creates_both(sandbox):
    bk.ensure_directories()
    assert os.path.isdir("data")
    assert os.path.isdir("output")


def test_ensure_sample_data_writes_a_readable_csv(sandbox):
    # Built here, not at import: LocalShellBackend resolves its root when it is
    # constructed, and the module was imported before the sandbox chdir.
    path = bk.ensure_sample_data(bk.create_backend())
    assert path == "./data/sales_data.csv"

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    assert rows[0] == ["Date", "Product", "Units Sold", "Revenue"]
    assert len(rows) == len(bk.SAMPLE_ROWS)


def test_the_sample_totals_are_what_the_tests_expect(sandbox):
    """840 and 33 appear in the end-to-end assertions."""
    revenue = sum(int(row[3]) for row in bk.SAMPLE_ROWS[1:])
    units = sum(int(row[2]) for row in bk.SAMPLE_ROWS[1:])
    assert (revenue, units) == (840, 33)


def test_ensure_sample_data_is_repeatable(sandbox):
    target = bk.create_backend()
    bk.ensure_sample_data(target)
    first = os.path.getsize("data/sales_data.csv")
    bk.ensure_sample_data(target)
    assert os.path.getsize("data/sales_data.csv") == first
