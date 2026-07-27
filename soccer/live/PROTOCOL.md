# Live experiment protocol

One-season (2026-27) FanDuel test of the model. **$100 starting bankroll,
quarter-Kelly sizing, flat discipline.** Primary scoreboard is **CLV**,
secondary is P&L — see [Scoreboard](#scoreboard) for why. Results live in
[RESULTS.md](../RESULTS.md) and on the
[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#soccer)
(both auto-generated — never hand-edit).

## The routine

A scheduled cloud agent runs the pipeline hourly:

| | |
|---|---|
| name | `fanduel-edge-watch` |
| id / manage | `trig_01FKR2Rv98CZho9cUGXvLJiA` — https://claude.ai/code/routines/trig_01FKR2Rv98CZho9cUGXvLJiA |
| model | claude-opus-5 |
| schedule | hourly at :51 UTC |
| runs | `src/live_pipeline.py` (fresh data → retrain → score fixtures → `live/picks.csv`), then `src/settle_bets.py` (results/CLV → `RESULTS.md`, `live/bankroll.json`, `docs/index.html`) |
| commits | pushes to main only when picks changed or bets settled |
| notifies | push notification ONLY for strong picks (avg-book EV > 1%) or settlements; never for a pick already in `bets.csv` — see [No duplicate notifications](#no-duplicate-notifications); quiet otherwise |
| reports | whenever `live/picks.csv` is non-empty, the run writes **every** pick as a markdown table at the top of its session reply — see [Pick table](#pick-table-every-run) |

### Pick table (every run)

Any run that produces a non-empty `live/picks.csv` opens its session reply
with a markdown table of **all** rows — the sub-threshold ones as well as
`strong=True` — before the run log, commit notes, or anything else. This is
separate from the push notification, which stays capped at the strong picks:
the table is the full picture for whoever reads the session afterwards.

Columns: fixture (`home` v `away`), `div`, `date`, `side` (H/D/A), `model_p`,
`avg_odds`, `max_odds`, `ev_at_avg`, `min_odds_5pct` (the playable price),
`min_odds_2pct`, and `stake_at_min5`. The sheet already arrives sorted by
`ev_at_avg` descending — keep that order and mark which rows are `strong`, and
which are already logged in `bets.csv`.

### No duplicate notifications

Before sending a pick notification, drop every candidate whose `key`
(`div|date|home|away|side`) already appears in `live/bets.csv` (any status —
the user has already acted on it, and a second ping about it is noise). Notify
only on what survives; if nothing survives, send **no** notification at all,
even when the pipeline reports changed picks. Settlement notifications follow
the same rule — only bets that settled on *this* run count, never a
re-summary of the standing ledger.

Dropped picks still belong in the session pick table, marked as already logged
with the price from `bets.csv`, so the reply keeps the full sheet. Silence is
the correct outcome for an hour whose only "new" picks are ones already
filled.

> Resolved 2026-07-26: the cloud environment now allowlists
> `www.football-data.co.uk` (+ package managers). On a total outage the
> routine notifies once, commits a `live/outage.json` marker to avoid
> repeat pings, and clears it on the next healthy run.

Notes:
- New picks can only appear **~2×/week** (football-data refreshes fixture odds
  Friday afternoon + Tuesday). The hourly cadence just catches refreshes fast
  and settles promptly. To change cadence/model, edit the routine at the link
  above.
- Until the 2026-27 season files exist on football-data (early August 2026),
  runs will report "no upcoming fixtures" — expected, not a bug.
- A push notification is **not** an injury-checked recommendation — the
  routine has no team-news feed. See
  [Injury check](#injury-check-before-you-bet).
- The routine never edits `bets.csv`, never places bets, never touches `src/`.
- Leagues assumed on FanDuel: `FANDUEL_LEAGUES` in `src/live_pipeline.py` —
  edit that list if FanDuel doesn't carry something (e.g. E3, SC0).

## Playing a pick (user)

`live/picks.csv` columns that matter:

- `min_odds_5pct` — the FanDuel price that gives ≥5% EV at the model
  probability. **FanDuel at or above this → playable** (conservative default).
- `min_odds_2pct` — the ≥2% EV price (aggressive; more bets, thinner edge).
- `stake_at_min5` — suggested quarter-Kelly stake at that minimum price.
- `strong` — True when even an average book clears +1% EV (these trigger the
  notification; the rest of the sheet is worth a scan if you have time).

**Run the [injury check](#injury-check-before-you-bet) before every fill.**

### Injury check before you bet

**The model is team-level and has no squad data at all.** Its features are
Elo, EWMA goals for/against, EWMA shots on target, form, rest days, division,
and the opening prices (`src/features.py`) — there is no lineup, minutes, or
player-availability input anywhere in the pipeline, because the football-data
CSVs don't carry one. So every bit of injury knowledge the model has is
secondhand, inherited from the opener: **whatever the market knew when the
price posted.** News that broke afterwards is invisible to it until it shows
up in results weeks later — and since picks fire precisely when FanDuel is
still at that stale opener, this is the model's sharpest blind spot.

Check team news before every fill and **skip the pick** if:

- A first-choice keeper or the side's main goalscorer was ruled out after the
  opener posted.
- Two or more regular starters are newly out or suspended.
- The manager has signalled rotation, or there's a cup/European tie within
  ~3 days either side. Note `rest_h`/`rest_a` count days since the last
  *league* match in the dataset, so midweek cup fixtures are invisible to the
  model — it will read a tired, rotated side as fully rested.
- The opener looks stale for an obvious non-injury reason too: manager sacked,
  points deduction, ownership chaos.

If news breaks *after* the fill: **don't chase and don't hedge.** Let it
settle — CLV records whether the close agreed. Add a `notes` entry on the
`bets.csv` row.

Sizing (quarter-Kelly, from current bankroll `B` in `live/bankroll.json`):

```
stake = B × 0.25 × (p·o − 1)/(o − 1)      capped at 0.10 × B
```

where `p` = `model_p` and `o` = the price you actually get. Better price than
the sheet → recompute (any Claude session will do it). Typical stakes at $100
bankroll: **$1–3**. Round to the nearest $0.50; skip if the formula gives
< $0.50. FanDuel team spellings differ from football-data's — always log with
the sheet's names, not FanDuel's.

## Reporting fills (user → any Claude session)

Report in plain words, e.g. *"got Wigan home at 2.60 for $2"*, *"took the
draw in Zaragoza–Malaga at 3.4, $1.50"*, *"skipped the rest"*. Claude then:

1. Appends one row per fill to `live/bets.csv` with `status=open`, copying
   `key`, team names, `div`, `match_date`, `model_p` from `live/picks.csv`;
   `odds_taken` and `stake` as reported; `placed_at` = today.
2. Commits and pushes: `live: log N bets <date>`.

Rules for Claude sessions:
- Never invent or assume a fill; log only what the user explicitly reports.
- Do not edit settled rows; corrections get a `notes` entry, not a rewrite.
- If the reported price is below `min_odds_2pct`, log it anyway (it's the
  user's call) but mention the EV is below threshold.

## Settlement (automatic)

Results and closing odds arrive in the football-data CSVs within ~1-2 days of
each match; the next routine run then fills `result`, `pnl`,
`clv = p_close × odds_taken − 1` (closing probs devigged via Shin; Pinnacle
close if available, else market-average close — recorded in `clv_source`),
updates the bankroll, and regenerates RESULTS.md. Postponed/unmatched games
get flagged in `notes` after 7 days — resolve manually (void → set status
`void`, pnl 0, with a note).

## Scoreboard

- **CLV is primary.** Per the backtest (README), single-book bets at ~+2-5% EV
  should average **+1% to +3% CLV**. At one season's volume (~150-600 bets,
  σ≈10%/bet) a real CLV edge shows up at ~3σ; the t-stat is printed in
  RESULTS.md.
- **P&L is secondary.** ROI noise over one season is ±6% or more — a losing
  season with clearly positive CLV is a *successful* test of the model (and
  vice versa: profit with negative CLV is luck).
- CLV-expected P&L (Σ stake × clv) in RESULTS.md is the "what you should have
  won" line to compare actual P&L against.
