# Beating the Opener

One project, one thesis, tested market by market: **soft sportsbooks' opening
lines are inefficient, and the inefficiency is capturable with public data.**
The method is the same everywhere — anchor on the market's own price, model
only the open→close correction (never the raw outcome), prove it out-of-sample
against the close, then bet stale openers in a live experiment scored by CLV.

| market | opener beaten? | wedge captured | live test |
|---|---|---|---|
| NBA moneyline ([nba-win-prob](https://github.com/soldoutbudokan/nba-win-prob), the control) | no — the close is efficient | — | — |
| **soccer 1X2** → [`soccer/`](soccer/) | **yes** — 9/9 OOS seasons (p = 0.0039), +5.2% ROI best-of-book sim | 18% | 2026-27 season, from Aug |
| **WNBA player props** → [`wnba/`](wnba/) | **yes** — both seasons, all 8 markets (p = 6e-12), +10.6% ROI sim | 48% | 🔴 running now |

The NBA result is what makes the rest credible: the same methodology, honestly
applied, says the most liquid market's close is unbeatable. The edge only
appears where attention per price is low — lower-league soccer, prop menus —
and only at the *open*.

## Live experiments

Both run on FanDuel with **$100 bankrolls (one per market), quarter-Kelly
stakes, CLV as the primary scoreboard** (one season of ROI is noise; one
season of CLV is decisive). Hourly cloud routines refresh data, retrain,
score, and notify on strong picks; fills are reported conversationally and
settlement is automatic.

- Soccer: [record](soccer/RESULTS.md) · [protocol](soccer/live/PROTOCOL.md)
- WNBA: [record](wnba/RESULTS.md) · [protocol](wnba/live/PROTOCOL.md)

## Repo layout

Each market is a self-contained subproject (its own `src/`, `data/`, `live/`,
README with the full research writeup, and auto-generated RESULTS.md). Run
scripts from inside the subdirectory. The WNBA line archive under
`wnba/data/raw/bp/` is committed because the upstream source deletes old
seasons — it is irreplaceable.
