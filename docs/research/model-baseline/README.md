# Model swap and depth baseline

Evidence for [issue #4](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/4),
part of the map in [issue #1](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/1).

Two runs, **identical prompt, identical data, identical harness**, differing only
in the model. Nothing in `src/` was changed and `.env` was never written to; the
model was overridden per process. Each run used its own temp sandbox, with every
approval auto-granted.

Data: `data/NCHS_-_Leading_Causes_of_Death__United_States.csv`, 10,868 rows x 6
columns (year, cause, state, deaths, age-adjusted rate). Chosen because it
contains two aggregation traps — `State == "United States"` totals alongside
per-state rows, and an `All causes` row alongside specific causes — so a shallow
`groupby` silently double-counts.

| | `gpt-4.1-mini-2025-04-14` | `gpt-5.6-luna` |
|---|---|---|
| Wall clock | 58.7 s | 111.2 s |
| Approval rounds | 7 | 15 |
| Tool calls | 9 | 29 (12 `execute`, 8 `ls`) |
| Messages in final state | 20 | 56 |
| Input tokens | 68,219 | 261,542 |
| Output tokens | 1,904 | 5,967 |
| Figures produced | 2 | 1 |
| **Analysis correct** | **no** | **yes** |

## What "incorrect" means

`gpt-4.1-mini` ran `df.groupby('Cause Name')['Deaths'].sum()` over the whole
file, summing national totals together with all fifty states and the `All
causes` aggregate on top. It reported "about 168 million deaths" and "'All
causes' itself representing 56.8% of deaths" with no hedge. Both figures are
artefacts of double counting. The true all-cause total is 47.7 M.

`gpt-5.6-luna` opened with `us = df[df['State'].eq('United States')]`, split the
aggregate from the specific causes, and computed two separate denominators —
naming in the printed header which denominator each share uses. It added IQR
outlier detection, carried the age-adjusted rate alongside raw counts, and
excluded the `All causes` series from the chart with a comment explaining that
it dwarfs the others. None of that was requested by the prompt.

## Files

- `*.json` — the full record per run: timings, token counts, the tool-call
  sequence, and the final reply.
- `*--*.py` — the scripts each model wrote, verbatim. The clearest evidence.
- `*--*.png` — the figures each produced.
- `_harness.py` — the runner, kept so the comparison can be repeated.
