# Live experiment protocol

One-season (2026-27) FanDuel test of the model. **$100 starting bankroll,
quarter-Kelly sizing, flat discipline.** Primary scoreboard is **CLV**,
secondary is P&L — see [Scoreboard](#scoreboard) for why. Results live in
[RESULTS.md](../RESULTS.md) (auto-generated — never hand-edit).

## The routine

A scheduled cloud agent runs the pipeline hourly:

| | |
|---|---|
| name | `fanduel-edge-watch` |
| id / manage | `trig_01FKR2Rv98CZho9cUGXvLJiA` — https://claude.ai/code/routines/trig_01FKR2Rv98CZho9cUGXvLJiA |
| model | claude-opus-5 |
| schedule | hourly at :51 UTC |
| runs | `src/live_pipeline.py` (fresh data → retrain → score fixtures → `live/picks.csv`), then `src/settle_bets.py` (results/CLV → `RESULTS.md`, `live/bankroll.json`) |
| commits | pushes to main only when picks changed or bets settled |
| notifies | push notification ONLY for strong picks (avg-book EV > 1%) or settlements; quiet otherwise |

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
