"""Which figures a run produced, and whether the reply lied about one.

Finds files and returns paths. It never sends anything, so it can be tested
with a directory and a string.
"""

import os
import re
import time

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# Paths the agent might mention in prose: ./output/plot.png, `output\a.png`.
_PATH_RE = re.compile(
    r"[\w./\\-]+(?:" + "|".join(e.replace(".", r"\.") for e in IMAGE_EXT) + r")"
)

# A reply claiming a chart it never made. Both patterns must match, so
# "no chart was created" does not trip it.
_CHART_WORD_RE = re.compile(r"\b(chart|plot|figure|dashboard|graph|visuali[sz]ation)\b", re.I)
_MADE_WORD_RE = re.compile(r"\b(saved|created|generated|built|produced|attached|plotted|charted)\b", re.I)

MISSING_FILE_WARNING = (
    "⚠️ The reply mentions {names}, but that file does not exist. The script was "
    "probably never executed — try asking again and approving the execute step."
)
MISSING_CHART_WARNING = (
    "⚠️ That analysis produced no chart. The script was probably never "
    "executed — ask again and approve the run step."
)


def mentioned_paths(text: str) -> list[str]:
    return [os.path.normpath(raw.strip("`'\"()[],"))
            for raw in _PATH_RE.findall(text or "")]


class ArtifactCollector:
    """Tracks, per conversation, which images have already been delivered.

    A run spans the first message through every approval that follows, so the
    start time is set once and images are sent at most once.
    """

    def __init__(self, output_dir: str = "./output", clock=time.time):
        self._output_dir = output_dir
        self._clock = clock
        self._started: dict[object, float] = {}
        self._sent: dict[object, set[str]] = {}

    def start_run(self, key: object) -> None:
        # -1s guards against filesystem mtime granularity.
        self._started[key] = self._clock() - 1
        self._sent[key] = set()

    def forget(self, key: object) -> None:
        self._sent.pop(key, None)

    def new_images(self, key: object, text: str = "") -> list[str]:
        """Images this run produced, in reading order.

        Sorted by filename, because the agent numbers its figures 01_, 02_, …
        for exactly this. Scan order is creation order, which is right only by
        coincidence and breaks the moment a figure is redrawn.
        """
        started = self._started.get(key, 0)
        already = self._sent.setdefault(key, set())

        found = [path for path in mentioned_paths(text) if os.path.isfile(path)]
        if os.path.isdir(self._output_dir):
            for entry in os.scandir(self._output_dir):
                if (entry.is_file()
                        and entry.name.lower().endswith(IMAGE_EXT)
                        and entry.stat().st_mtime >= started):
                    found.append(os.path.normpath(entry.path))

        fresh = []
        for path in sorted(found, key=lambda p: os.path.basename(p).lower()):
            absolute = os.path.abspath(path)
            if absolute not in already:
                already.add(absolute)
                fresh.append(path)
        return fresh

    def warning_for(self, text: str, images_sent: int,
                    analysed: bool = False) -> str | None:
        """A warning to show the user, or None when the reply is honest.

        `analysed` says whether the run actually ran something. A greeting
        legitimately produces no figures; an analysis never does.
        """
        missing = sorted({path for path in mentioned_paths(text)
                          if not os.path.isfile(path)})
        if missing:
            return MISSING_FILE_WARNING.format(names=", ".join(missing))
        if analysed and not images_sent:
            return MISSING_CHART_WARNING
        return None
