# Live experiment protocol

One-season FanDuel test of the model. $100 starting bankroll, quarter-Kelly flat
discipline. Judged primarily on **CLV**, secondarily on P&L (see RESULTS.md).

## The loop

1. **Hourly cloud routine** ("fanduel-edge-watch") runs `src/live_pipeline.py`
   (fresh data → retrain → score fixtures → `live/picks.csv`) then
   `src/settle_bets.py` (results/CLV → `RESULTS.md`). It commits and pushes when
   something changed, and notifies on strong picks (avg-book EV > 1%) or
   settlements. New picks appear ~2x/week (Fri + Tue odds refreshes).
2. **User** checks FanDuel for notified picks. A pick is playable if FanDuel's
   price ≥ `min_odds_5pct` (conservative) or ≥ `min_odds_2pct` (aggressive).
   Suggested stake: quarter-Kelly at the price actually taken.
3. **User reports fills** in any Claude session, e.g. *"got Wigan home at 2.60
   for $2"* / *"skipped the rest"*.
4. **Claude (any session) logs the bet**: append a row to `live/bets.csv` with
   `status=open`, using the exact `key` and team names from `live/picks.csv`
   (never FanDuel's spelling), `stake` and `odds_taken` as reported, `model_p`
   from the picks sheet, `placed_at` = today. Commit + push:
   `live: log N bets <date>`.
5. Settlement is automatic on later routine runs (results + closing odds arrive
   in the data within a day or two of each match).

## Rules for Claude sessions

- Never invent a fill; only log what the user explicitly reports.
- Recompute the stake suggestion at the reported price if it differs from the
  sheet: `stake = bankroll * 0.25 * (p*o - 1)/(o - 1)`, capped at 10% bankroll.
- Do not edit settled rows; corrections get a note, not a rewrite.
- `bets.csv` columns: key, placed_at, match_date, div, home, away, side,
  odds_taken, stake, model_p, status, result, clv, clv_source, pnl, notes.
