"""Text formatting for chat messages. Pure functions, no I/O."""

import html
import os
import re

# Telegram rejects messages over 4096 characters; leave room for our own markers.
MAX_MESSAGE = 3900

# Any path ending in a python executable, so a 60-character interpreter path
# reads as plain "python". Version and .exe are matched separately.
_INTERPRETER_RE = re.compile(r"\S*[/\\]python(?:\d+(?:\.\d+)*)?(?:\.exe)?\b", re.I)


def chunk_text(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on line boundaries where possible so code blocks stay readable."""
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:                   # a single enormous line
            out.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            out.append(current)
            current = ""
        current += line
    if current:
        out.append(current)
    return out


def to_html(text: str) -> str:
    """Convert the model's Markdown into the small HTML subset Telegram takes.

    HTML rather than MarkdownV2, which needs a dozen characters escaped. Code
    spans are pulled out first so their contents are left alone.
    """
    vault: list[str] = []

    def stash(rendered: str) -> str:
        vault.append(rendered)
        return f"\x00{len(vault) - 1}\x00"

    text = re.sub(r"```[a-zA-Z]*\n?(.*?)```",
                  lambda m: stash(f"<pre>{html.escape(m.group(1))}</pre>"),
                  text or "", flags=re.S)
    text = re.sub(r"`([^`\n]+)`",
                  lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)

    text = html.escape(text)

    # [ \t] rather than \s: \s matches newlines, which would swallow the blank
    # lines that separate sections.
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*(.+?)[ \t]*$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"^([ \t]*)[-*+][ \t]+", r"\1• ", text, flags=re.M)

    for i, rendered in enumerate(vault):
        text = text.replace(f"\x00{i}\x00", rendered)
    return text


def strip_html(text: str) -> str:
    """Plain-text fallback for when Telegram rejects our markup."""
    return re.sub(r"<[^>]+>", "", text or "")


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def tidy_path(raw: str) -> str:
    """Project-relative and forward-slashed: the model writes paths three
    different ways and none is worth showing in full."""
    path = str(raw or "").replace("\\", "/").strip()
    cwd = os.getcwd().replace("\\", "/").rstrip("/")
    if path.lower().startswith(cwd.lower()):
        path = path[len(cwd):]
    return path.lstrip("./") or "(unnamed file)"


def shorten_interpreter(command: str) -> str:
    """`D:\\proj\\.venv\\Scripts\\python.exe x.py` -> `python x.py`."""
    return _INTERPRETER_RE.sub("python", command or "")


def safe_filename(name: str) -> str:
    """Strip any path component: ../../secret.json must not escape the data dir."""
    name = os.path.basename(name or "upload")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "upload"
