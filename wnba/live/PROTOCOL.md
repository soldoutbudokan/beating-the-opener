# Live experiment protocol - WNBA props

One-season (2026, through ~October) FanDuel test. **$100 starting bankroll
(separate from the soccer experiment), quarter-Kelly sizing.** Primary
scoreboard is **CLV**, secondary is P&L. Results: [RESULTS.md](../RESULTS.md)
or the
[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)
(both auto-generated - never hand-edit).

## The routine

| | |
|---|---|
| name | `wnba-edge-watch` |
| id / manage | `trig_01Ko6Py4ar9tw8QoxPYx8tyw` - https://claude.ai/code/routines/trig_01Ko6Py4ar9tw8QoxPYx8tyw |
| model | claude-opus-5 |
| schedule | hourly at :21 UTC |
| runs | quick pre-check (no games + no open bets -> exit in seconds); else data refresh (`fetch_wehoop` -> `build_props` -> `grade_props` -> `features` -> `build_modelset`) -> `live_pipeline.py` -> **notify immediately** on strong picks -> housekeeping (`scrape_bettingpros` for CLV closes -> `settle_bets.py`, which also rebuilds `docs/index.html`) |
| commits | pushes to main when picks changed or bets settled |
| notifies | push notification ONLY for new strong picks (EV >= 6%) or settlements |

> Resolved 2026-07-26: the cloud environment now allowlists
> `api.bettingpros.com` + `raw.githubusercontent.com` (+ package managers).
> If runs ever fail on a blocked host again, that's where to look.

Notes:
- Props post the **morning of game day** (ET); most picks appear then and
  disappear as FanDuel moves the line. Hourly polling is the point: the edge
  IS the stale opener.
- On a total outage the routine notifies once, drops a `live/outage.json`
  marker (committed) to stay silent on repeat failures, and clears it on the
  next healthy run.
- **The pick logic only lists props whose FanDuel price is still at the
  opening line/juice.** Once the line moves, the backtested edge is gone -
  the model does not beat moved prices, so no pick is shown. Don't chase.
- During the All-Star break / offseason runs print `NO_UPCOMING` - expected.
- The routine never edits `bets.csv`, never places bets, never touches `src/`.

## Playing a pick (user)

`live/picks.csv`, sorted by EV:

- `strong=True` (EV >= 6% at FanDuel's current price) - these trigger the
  notification. EV >= 3% rows are listed for completeness.
- `fd_line` / `fd_cost` - the price the model evaluated. If FanDuel now shows
  something worse than `min_odds_6pct` (strong) or `min_odds_3pct` (marginal),
  the line has moved - **skip, don't chase**.
- `stake` - quarter-Kelly at `fd_cost` from the current bankroll:
  `stake = B x 0.25 x (p*o - 1)/(o - 1)`, capped at `0.10 x B`, rounded to
  $0.50. Better price than the sheet -> any Claude session recomputes.
- **One bet per player per game.** Combo markets (points, PRA, pts+reb...)
  on the same player are heavily correlated; play only the highest-EV row
  for that player.
- Props void on DNP at FanDuel - mirrored in settlement.

## Reporting fills (user -> any Claude session)

Plain words: *"got Citron assists over 3.5 at +128 for $2"*, *"skipped the
rest"*. Claude then appends one row per fill to `live/bets.csv` with
`status=open`, copying `key`, `event_id`, `market`, `player`, `side`, `line`,
`model_p` from `live/picks.csv` (`match_date` = the pick's `date`;
`odds_taken`/`stake` as reported; `placed_at` = today), commits and pushes:
`live: log N bets <date>`.

Rules for Claude sessions:
- Never invent or assume a fill; log only what the user explicitly reports.
- Do not edit settled rows; corrections get a `notes` entry.
- Price below `min_odds_3pct` -> log it (user's call) but flag the EV.

## Settlement (automatic)

Box scores land in wehoop within ~a day; the next routine run grades each open
bet (actual stat vs line; DNP -> void, stake returned; exact line -> push),
computes CLV from the archived closing snapshot re-expressed at the bet's own
line (consensus close preferred, FanDuel fallback - recorded in `clv_source`),
updates `live/bankroll.json`, regenerates RESULTS.md. No box row after 3 days
-> voided with a note.

## Scoreboard

- **CLV primary.** Backtest: stale-open bets at EV>=2% averaged **+5.4% CLV**
  (player-game-clustered t = 2.3). A season of ~150-400 bets resolves a CLV
  edge of that size decisively.
- **P&L secondary.** Prop odds near even money, ~2-3 bets/day: ROI noise over
  a part-season is large. Losing money with clearly positive CLV = the model
  works; winning with negative CLV = luck.
