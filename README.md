# Beating the Opener

**[→ Live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/)** —
bankrolls, CLV, open positions and the evidence behind them, at a glance.
Rebuilt automatically every time a bet settles.

One project, one thesis, tested market by market: **soft sportsbooks' opening
lines are inefficient, and the inefficiency is capturable with public data.**
The method is the same everywhere — anchor on the market's own price, model
only the open→close correction (never the raw outcome), prove it out-of-sample
against the close, then bet stale openers in a live experiment scored by CLV.

| market | opener beaten? | wedge captured | live test |
|---|---|---|---|
| NBA moneyline ([nba-win-prob](https://github.com/soldoutbudokan/nba-win-prob), control #1) | no — even the open is sharp | — | — |
| **soccer 1X2** → [`soccer/`](soccer/) | **yes** — 9/9 OOS seasons (p = 0.0039), +5.2% ROI best-of-book sim | 18% | 2026-27 season, from Aug |
| **WNBA player props** → [`wnba/`](wnba/) | **yes** — both seasons, all 8 markets (p = 6e-12), +10.6% ROI sim | 48% | 🔴 running now |
| cricket BBL match odds → [`cricket/`](cricket/) (control #2) | no — **no wedge exists**: the close is no better than the open; moves are toss noise | — | — |

The two control results are what make the middle rows credible: the same
methodology, honestly applied, returns "no" twice. An exploitable opener
needs both a *lazy open* (low attention per price) **and** an *informative
close* (real information arriving before tip, so there's a correction to
capture). NBA fails the first — attention floods the market. BBL cricket
fails the second — the lines move plenty, but toward toss noise, not
winners. Lower-league soccer and WNBA prop menus satisfy both.

## Live experiments

Both run on FanDuel with **$100 bankrolls (one per market), quarter-Kelly
stakes, CLV as the primary scoreboard** (one season of ROI is noise; one
season of CLV is decisive). Hourly cloud routines refresh data, retrain,
score, and notify on strong picks; fills are reported conversationally and
settlement is automatic.

- Soccer: [scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#soccer)
  · [record](soccer/RESULTS.md) · [protocol](soccer/live/PROTOCOL.md)
- WNBA: [scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)
  · [record](wnba/RESULTS.md) · [protocol](wnba/live/PROTOCOL.md)

## Repo layout

Each market is a self-contained subproject (its own `src/`, `data/`, `live/`,
README with the full research writeup, and auto-generated RESULTS.md). Run
scripts from inside the subdirectory. The WNBA line archive under
`wnba/data/raw/bp/` is committed because the upstream source deletes old
seasons — it is irreplaceable.

`site/build_site.py` renders both markets' live files into `docs/index.html`
(the scoreboard above) — see [`site/README.md`](site/README.md). Each
`settle_bets.py` run regenerates it, so the page never drifts from the CSVs.
