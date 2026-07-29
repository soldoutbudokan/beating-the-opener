# Live FanDuel WNBA props - results

> At a glance: **[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)** - same numbers, plus the open picks and the backtest evidence.

Quarter-Kelly, $100 starting bankroll, picks from the [wnba-props model](README.md). CLV = `p_close(at bet line) x decimal_odds - 1`: whether the bets beat the closing price. `CLV*` re-expresses the close with the measured market over-shade removed (WNBA prop prices overstate P(over) by ~2pp on average, so raw CLV mechanically penalises unders - see AUDIT.md N1). Both converge far faster than ROI.

**Bankroll: $98.91** (start $100)

| metric | value |
|---|---|
| settled | 5 (2W-3L), 0 push, 0 void, 0 open |
| staked | $5.00 |
| P&L | $-1.09 (-21.8% ROI) |
| mean CLV (vs close) | +nan% (n=0) |
| mean CLV* (shade-adj) | - |
| CLV-expected P&L | $+0.00 |

| date | player | market | side | line | odds | stake | actual | result | P&L | CLV | CLV* |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-28 | Carla Leite | assists | over | 6.5 | 128 | 1.0 | 3 | lost | -1.00 |  |  |
| 2026-07-28 | Caitlin Clark | threes | over | 2.5 | 108 | 1.0 | 4 | won | +1.08 |  |  |
| 2026-07-28 | Kayla McBride | points | over | 17.5 | -120 | 1.0 | 18 | won | +0.83 |  |  |
| 2026-07-28 | Emily Engstler | threes | over | 1.5 | 194 | 1.0 | 1 | lost | -1.00 |  |  |
| 2026-07-28 | Caitlin Clark | rebounds | under | 3.5 | 106 | 1.0 | 4 | lost | -1.00 |  |  |
