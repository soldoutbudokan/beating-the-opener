# Live FanDuel WNBA props - results

> At a glance: **[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)** - same numbers, plus the open picks and the backtest evidence.

Quarter-Kelly, $100 starting bankroll, picks from the [wnba-props model](README.md). CLV = `p_close(at bet line) x decimal_odds - 1`: whether the bets beat the closing price. `CLV*` re-expresses the close with the measured market over-shade removed (WNBA prop prices overstate P(over) by ~2pp on average, so raw CLV mechanically penalises unders - see AUDIT.md N1). Both converge far faster than ROI.

`EV said` is the model's own claim for that bet at the price actually taken (`model_p x decimal_odds - 1`). Read it against `CLV`: the model's claim vs the market's verdict on the same bet. A large positive `EV said` next to a negative `CLV` means the market never came to us - the claimed edge was not visible to anyone else. Note CLV's break-even is not zero: paying a two-way price and seeing no line movement scores about `1/booksum - 1`, i.e. roughly -5% to -7% at typical prop prices, so `CLV` near -6% means the line simply did not move.

**Bankroll: $95.68** (start $100)

| metric | value |
|---|---|
| settled | 23 (10W-13L), 0 push, 2 void, 23 open |
| staked | $23.00 |
| P&L | $-4.32 (-18.8% ROI) |
| mean EV said (model) | +21.64% (n=23) |
| mean CLV (vs close) | -4.70% (n=31) |
| mean CLV* (shade-adj) | -4.57% (n=31) |
| Model-expected P&L | $+5.43 |
| CLV-expected P&L | $-1.46 |

CLV t-stat: -5.91 (iid); -3.62 clustered by match date (5 dates)

| date | player | market | side | line | odds | stake | EV said | actual | result | P&L | CLV | CLV* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-08-06 | Caitlin Clark | assists | under | 8.5 | 100 | 1.0 | +16.9% |  |  |  |  |  |
| 2026-08-06 | Jackie Young | rebounds | under | 4.5 | -125 | 1.0 | +15.6% |  |  |  |  |  |
| 2026-08-06 | Aliyah Boston | rebounds | under | 8.5 | -122 | 1.0 | +14.7% |  |  |  |  |  |
| 2026-08-05 | Kamilla Cardoso | assists | over | 2.5 | 154 | 1.0 | +29.0% |  |  |  |  |  |
| 2026-08-05 | Jonquel Jones | threes | under | 1.5 | 124 | 1.0 | +22.1% |  |  |  |  |  |
| 2026-08-05 | Rae Burrell | points | under | 15.5 | -108 | 1.0 | +20.4% |  |  |  |  |  |
| 2026-08-05 | Erica Wheeler | assists | under | 6.5 | -158 | 1.0 | +18.9% |  |  |  |  |  |
| 2026-08-05 | Sabrina Ionescu | assists | under | 5.5 | -102 | 1.0 | +18.8% |  |  |  |  |  |
| 2026-08-05 | Natisha Hiedeman | assists | under | 5.5 | -132 | 1.0 | +17.1% |  |  |  |  |  |
| 2026-08-05 | Angel Reese | assists | over | 2.5 | 148 | 1.0 | +16.3% |  |  |  |  |  |
| 2026-08-05 | Rhyne Howard | assists | over | 3.5 | 136 | 1.0 | +25.3% |  |  |  |  |  |
| 2026-08-05 | Rhyne Howard | rebounds | over | 3.5 | 106 | 1.0 | +18.2% |  |  |  |  |  |
| 2026-08-05 | Shakira Austin | assists | under | 2.5 | 106 | 1.0 | +17.9% |  |  |  |  |  |
| 2026-08-05 | Natasha Cloud | points | under | 12.5 | -114 | 1.0 | +21.1% |  |  |  |  |  |
| 2026-08-04 | Maria Conde | rebounds | over | 4.5 | 108 | 1.0 | +23.0% |  |  |  |  |  |
| 2026-08-03 | Kahleah Copper | points | under | 21.5 | -106 | 1.0 | +24.4% |  |  |  | -0.7% | -0.7% |
| 2026-08-03 | Chelsea Gray | threes | over | 1.5 | 110 | 1.0 | +20.5% |  |  |  | -5.4% | -5.4% |
| 2026-08-03 | Rhyne Howard | points | under | 16.5 | -110 | 1.0 | +21.4% |  |  |  | -6.2% | -6.2% |
| 2026-08-03 | Angel Reese | rebounds | under | 11.5 | -102 | 1.0 | +20.0% |  |  |  | -5.3% | -5.3% |
| 2026-08-03 | Natisha Hiedeman | assists | under | 5.5 | -130 | 1.0 | +17.8% |  |  |  | -7.0% | -7.0% |
| 2026-08-03 | Sabrina Ionescu | assists | under | 5.5 | -106 | 1.0 | +16.4% |  |  |  | -11.2% | -11.2% |
| 2026-08-03 | Jackie Young | rebounds | under | 4.5 | -152 | 1.0 | +24.7% |  |  |  | -6.4% | -6.4% |
| 2026-08-03 | Sydney Taylor | threes | under | 2.5 | 106 | 1.0 | +20.3% |  |  |  | -0.2% | -0.2% |
| 2026-08-02 | Aliyah Boston | rebounds | under | 8.5 | -118 | 1.0 | +33.4% | 8 | won | +0.85 | -9.3% | -4.4% |
| 2026-08-02 | Olivia Miles | rebounds | over | 4.5 | 118 | 1.0 | +34.1% | 4 | lost | -1.00 | -3.2% | -8.8% |
| 2026-08-02 | Kelsey Plum | assists | over | 4.5 | -114 | 1.0 | +29.0% |  | void (DNP) | +0.00 |  |  |
| 2026-08-02 | Janelle Salaun | assists | under | 1.5 | -114 | 1.0 | +28.6% | 2 | lost | -1.00 | -6.1% | -2.1% |
| 2026-08-02 | Kayla McBride | points | over | 17.5 | -110 | 1.0 | +28.3% | 10 | lost | -1.00 | -11.1% | -14.9% |
| 2026-08-02 | Leila Lacan | assists | under | 4.5 | 110 | 1.0 | +18.4% | 2 | won | +1.10 | -2.6% | +1.8% |
| 2026-08-02 | Marina Mabrey | points | under | 19.5 | -108 | 1.0 | +15.9% |  | void (DNP) | +0.00 |  |  |
| 2026-08-01 | Kahleah Copper | threes | under | 1.5 | 146 | 1.0 | +38.7% | 2 | lost | -1.00 | -3.9% | +1.1% |
| 2026-08-01 | A'ja Wilson | assists | under | 3.5 | 102 | 1.0 | +28.9% | 4 | lost | -1.00 | +1.7% | +5.3% |
| 2026-08-01 | Jackie Young | assists | under | 7.5 | -146 | 1.0 | +23.3% | 5 | won | +0.68 | +3.9% | +6.7% |
| 2026-08-01 | Jonquel Jones | assists | over | 2.5 | 110 | 1.0 | +22.6% | 5 | won | +1.10 | -0.4% | -4.1% |
| 2026-08-01 | Sabrina Ionescu | assists | under | 5.5 | -108 | 1.0 | +21.7% | 10 | lost | -1.00 | -0.5% | +2.9% |
| 2026-07-31 | Caitlin Clark | assists | under | 9.5 | -152 | 1.0 | +25.7% | 10 | lost | -1.00 | -7.2% | -7.2% |
| 2026-07-31 | Aliyah Boston | points | under | 17.5 | -122 | 1.0 | +25.7% | 14 | won | +0.82 | -3.2% | -3.2% |
| 2026-07-31 | Angel Reese | rebounds | under | 11.5 | 102 | 1.0 | +23.6% | 13 | lost | -1.00 | -6.6% | -6.6% |
| 2026-07-31 | Bridget Carleton | rebounds | over | 3.5 | 104 | 1.0 | +22.2% | 3 | lost | -1.00 | -5.7% | -5.7% |
| 2026-07-31 | Awa Fam | rebounds | over | 5.5 | -118 | 1.0 | +19.2% | 9 | won | +0.85 | -10.0% | -10.0% |
| 2026-07-31 | Megan DiLeo | points | under | 14.5 | -113 | 1.0 | +15.6% | 5 | won | +0.88 | -9.8% | -9.8% |
| 2026-07-31 | Natisha Hiedeman | threes | under | 2.5 | -205 | 1.0 | +15.0% | 0 | won | +0.49 | -7.6% | -7.6% |
| 2026-07-31 | Michaela Onyenwere | rebounds | under | 2.5 | 130 | 1.0 | +15.0% | 4 | lost | -1.00 | -1.0% | -1.0% |
| 2026-07-28 | Carla Leite | assists | over | 6.5 | 128 | 1.0 | +7.6% | 3 | lost | -1.00 | -3.9% | -8.0% |
| 2026-07-28 | Caitlin Clark | threes | over | 2.5 | 108 | 1.0 | +6.8% | 4 | won | +1.08 | +7.3% | +3.2% |
| 2026-07-28 | Kayla McBride | points | over | 17.5 | -120 | 1.0 | +5.0% | 18 | won | +0.83 | -7.7% | -11.2% |
| 2026-07-28 | Emily Engstler | threes | over | 1.5 | 194 | 1.0 | +31.7% | 1 | lost | -1.00 | -10.3% | -15.1% |
| 2026-07-28 | Caitlin Clark | rebounds | under | 3.5 | 106 | 1.0 | +6.7% | 4 | lost | -1.00 | -6.1% | -0.7% |
