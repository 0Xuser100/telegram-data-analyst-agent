"""The agent's workspace: a local shell plus the sample data file.

Creating the backend and creating sample data are separate calls, so importing
this module writes nothing to disk.
"""

import csv
import io
import os

from deepagents.backends import LocalShellBackend

DATA_DIR = "./data"
OUTPUT_DIR = "./output"
SAMPLE_CSV = "data/sales_data.csv"

SAMPLE_ROWS = [
    ["Date", "Product", "Units Sold", "Revenue"],
    ["2025-08-01", "Widget A", 10, 250],
    ["2025-08-02", "Widget B", 5, 125],
    ["2025-08-03", "Widget A", 7, 175],
    ["2025-08-04", "Widget C", 3, 90],
    ["2025-08-05", "Widget B", 8, 200],
]


def create_backend(root_dir: str = ".") -> LocalShellBackend:
    """A backend that can both read files and run commands.

    LocalShellBackend, not FilesystemBackend: without command execution the
    agent can write a script but never run it.
    """
    return LocalShellBackend(root_dir=root_dir)


def ensure_directories() -> None:
    for directory in (DATA_DIR, OUTPUT_DIR):
        os.makedirs(directory, exist_ok=True)


def ensure_sample_data(target: LocalShellBackend | None = None) -> str:
    """Write the demo CSV, so a fresh clone has something to analyse."""
    ensure_directories()
    buffer = io.StringIO()
    csv.writer(buffer).writerows(SAMPLE_ROWS)
    payload = buffer.getvalue().encode("utf-8")
    (target or backend).upload_files([(SAMPLE_CSV, payload)])
    return f"./{SAMPLE_CSV}"


# One shared backend for the app. Building it touches nothing on disk.
backend = create_backend()


if __name__ == "__main__":
    print("wrote", ensure_sample_data())
