# Live FanDuel WNBA props - results

> At a glance: **[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)** - same numbers, plus the open picks and the backtest evidence.

Quarter-Kelly, $100 starting bankroll, picks from the [wnba-props model](README.md). CLV = `p_close(at bet line) x decimal_odds - 1`: whether the bets beat the closing price. `CLV*` re-expresses the close with the measured market over-shade removed (WNBA prop prices overstate P(over) by ~2pp on average, so raw CLV mechanically penalises unders - see AUDIT.md N1). Both converge far faster than ROI.

**Bankroll: $97.95** (start $100)

| metric | value |
|---|---|
| settled | 13 (6W-7L), 0 push, 0 void, 12 open |
| staked | $13.00 |
| P&L | $-2.05 (-15.8% ROI) |
| mean CLV (vs close) | -5.52% (n=13) |
| mean CLV* (shade-adj) | -6.38% (n=13) |
| CLV-expected P&L | $-0.72 |

CLV t-stat: -4.22 (iid); -4.69 clustered by match date (2 dates)

| date | player | market | side | line | odds | stake | actual | result | P&L | CLV | CLV* |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-08-02 | Aliyah Boston | rebounds | under | 8.5 | -118 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Olivia Miles | rebounds | over | 4.5 | 118 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Kelsey Plum | assists | over | 4.5 | -114 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Janelle Salaun | assists | under | 1.5 | -114 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Kayla McBride | points | over | 17.5 | -110 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Leila Lacan | assists | under | 4.5 | 110 | 1.0 |  |  |  |  |  |
| 2026-08-02 | Marina Mabrey | points | under | 19.5 | -108 | 1.0 |  |  |  |  |  |
| 2026-08-01 | Kahleah Copper | threes | under | 1.5 | 146 | 1.0 |  |  |  |  |  |
| 2026-08-01 | A'ja Wilson | assists | under | 3.5 | 102 | 1.0 |  |  |  |  |  |
| 2026-08-01 | Jackie Young | assists | under | 7.5 | -146 | 1.0 |  |  |  |  |  |
| 2026-08-01 | Jonquel Jones | assists | over | 2.5 | 110 | 1.0 |  |  |  |  |  |
| 2026-08-01 | Sabrina Ionescu | assists | under | 5.5 | -108 | 1.0 |  |  |  |  |  |
| 2026-07-31 | Caitlin Clark | assists | under | 9.5 | -152 | 1.0 | 10 | lost | -1.00 | -7.2% | -7.2% |
| 2026-07-31 | Aliyah Boston | points | under | 17.5 | -122 | 1.0 | 14 | won | +0.82 | -3.2% | -3.2% |
| 2026-07-31 | Angel Reese | rebounds | under | 11.5 | 102 | 1.0 | 13 | lost | -1.00 | -6.6% | -6.6% |
| 2026-07-31 | Bridget Carleton | rebounds | over | 3.5 | 104 | 1.0 | 3 | lost | -1.00 | -5.7% | -5.7% |
| 2026-07-31 | Awa Fam | rebounds | over | 5.5 | -118 | 1.0 | 9 | won | +0.85 | -10.0% | -10.0% |
| 2026-07-31 | Megan DiLeo | points | under | 14.5 | -113 | 1.0 | 5 | won | +0.88 | -9.8% | -9.8% |
| 2026-07-31 | Natisha Hiedeman | threes | under | 2.5 | -205 | 1.0 | 0 | won | +0.49 | -7.6% | -7.6% |
| 2026-07-31 | Michaela Onyenwere | rebounds | under | 2.5 | 130 | 1.0 | 4 | lost | -1.00 | -1.0% | -1.0% |
| 2026-07-28 | Carla Leite | assists | over | 6.5 | 128 | 1.0 | 3 | lost | -1.00 | -3.9% | -8.0% |
| 2026-07-28 | Caitlin Clark | threes | over | 2.5 | 108 | 1.0 | 4 | won | +1.08 | +7.3% | +3.2% |
| 2026-07-28 | Kayla McBride | points | over | 17.5 | -120 | 1.0 | 18 | won | +0.83 | -7.7% | -11.2% |
| 2026-07-28 | Emily Engstler | threes | over | 1.5 | 194 | 1.0 | 1 | lost | -1.00 | -10.3% | -15.1% |
| 2026-07-28 | Caitlin Clark | rebounds | under | 3.5 | 106 | 1.0 | 4 | lost | -1.00 | -6.1% | -0.7% |
