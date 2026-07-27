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
| notifies | push notification ONLY for new strong picks (EV >= 6%) or settlements, and never for a pick already in `bets.csv` - see [No duplicate notifications](#no-duplicate-notifications) |
| reports | whenever `live/picks.csv` is non-empty, the run writes **every** pick as a markdown table at the top of its session reply - see [Pick table](#pick-table-every-run) |

### Pick table (every run)

Any run that produces a non-empty `live/picks.csv` opens its session reply
with a markdown table of **all** rows - marginal (EV >= 3%) as well as strong
- before the run log, commit notes, or anything else. This is separate from
the push notification, which stays capped at 3 strong picks: the table is the
full picture for whoever reads the session afterwards.

Columns: player (team), game, market, side, line, FanDuel price, model
probability, EV, stake, and the skip-if-worse-than price (`min_odds_6pct` for
strong rows, `min_odds_3pct` for marginal). Sort by EV descending and mark
which rows are `strong=True`, and which are already logged in `bets.csv`.

### No duplicate notifications

Before sending a pick notification, drop every candidate whose `key` already
appears in `live/bets.csv` (any status - the user has already acted on it, and
a second ping about it is noise). Notify only on what survives; if nothing
survives, send **no** notification at all, even when `live_pipeline.py`
printed `NEW_PICKS`. Settlement notifications follow the same rule - only
bets that settled on *this* run count, never a re-summary of the standing
ledger.

Dropped picks still belong in the session pick table, marked as already
logged with the price from `bets.csv`, so the reply keeps the full sheet.
Silence is the correct outcome for an hour whose only "new" picks are ones
already filled.

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
- A push notification is **not** an injury-checked recommendation - the
  routine has no injury feed. See [Injury check](#injury-check-before-you-bet).
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
- **Run the [injury check](#injury-check-before-you-bet) before every fill.**

### Injury check before you bet

**The model is blind to today's news.** Nothing in the pipeline reads an
injury report. Absences enter only through box scores, and the live model
deliberately uses only `absent_prior_ew_min` - teammates who ALSO missed the
previous game, i.e. absences at least two games old (`src/features.py`,
`src/live_pipeline.py:46`). Tonight's announcements are invisible to it.

Check the injury report / beat reporters before every fill and **skip the
pick** if any of these holds:

- **The subject is questionable, or on a minutes restriction.** A DNP is
  harmless - FanDuel voids the prop - but a 14-minute return-from-injury game
  is a live loser on an over. The void rule protects you from a player not
  playing, not from a player playing badly.
- **A >=12-EW-minute teammate is newly out tonight** (played the last game,
  out now). The model hasn't seen it; FanDuel usually has. If the price still
  sits at the opener, the "edge" you're reading is unpriced news, not model
  skill.
- **A regular is returning tonight after 2+ games out.** Worst case: the model
  still counts them absent, so it inflates the subject's projected usage.
  Overs are stale in the wrong direction.
- Anything else that materially changes the rotation - trade, coach announcing
  rest, suspension.

If news breaks *after* the fill: **don't chase and don't hedge.** Let it
settle - CLV records whether the close agreed with you. Add a `notes` entry on
the `bets.csv` row so the post-mortem is easy.

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
