# Live FanDuel WNBA props - results

> At a glance: **[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)** - same numbers, plus the open picks and the backtest evidence.

Quarter-Kelly, $100 starting bankroll, picks from the [wnba-props model](README.md). CLV = `p_close(at bet line) x decimal_odds - 1`: whether the bets beat the closing price. `CLV*` re-expresses the close with the measured market over-shade removed (WNBA prop prices overstate P(over) by ~2pp on average, so raw CLV mechanically penalises unders - see AUDIT.md N1). Both converge far faster than ROI. A blank `CLV*` means no shade table existed at stamp time; it is backfilled by a later run, never silently stamped at zero shade.

`EV said` is the model's own claim for that bet at the price actually taken (`model_p x decimal_odds - 1`). Read it against `CLV`: the model's claim vs the market's verdict on the same bet. A large positive `EV said` next to a negative `CLV` means the market never came to us - the claimed edge was not visible to anyone else. Note CLV's break-even is not zero: paying a two-way price and seeing no line movement scores about `1/booksum - 1`, i.e. roughly -5% to -7% at typical prop prices, so `CLV` near -6% means the line simply did not move.

**Bankroll: $103.69** (start $100)

| metric | value |
|---|---|
| settled | 90 (47W-43L), 0 push, 2 void, 12 open |
| staked | $90.00 |
| P&L | $+3.69 (+4.1% ROI) |
| mean EV said (model) | +23.42% (n=90) |
| mean CLV (vs close) | -4.34% (n=84) |
| mean CLV* (shade-adj) | -3.07% (n=84) |
| closing line moved | 16 of 84 stamped (the rest closed at the bet line: CLV ≈ vig there) |
| model calibration | expected 56.2W vs observed 47W (z=-2.04) |
| Model-expected P&L | $+21.53 |
| CLV-expected P&L | $-3.65 |

CLV t-stat: -4.52 (iid); -2.78 clustered by match date (13 dates)

Calibration reads the model's own claims against results: expected wins = sum of `model_p` over settled bets. A negative z means the claimed probabilities are running hot (the audit's under-side finding); it converges much faster than ROI.

| date | player | market | side | line | odds | stake | EV said | actual | result | P&L | CLV | CLV* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-08-12 | Courtney Vandersloot | points | under | 9.5 | -120 | 1.0 | +22.7% |  |  |  |  |  |
| 2026-08-12 | Kayla McBride | points | over | 15.5 | -114 | 1.0 | +21.6% |  |  |  |  |  |
| 2026-08-12 | Nyara Sabally | points | under | 13.5 | -114 | 1.0 | +20.6% |  |  |  |  |  |
| 2026-08-12 | Megan DiLeo | threes | under | 1.5 | -114 | 1.0 | +19.4% |  |  |  |  |  |
| 2026-08-12 | Natasha Howard | rebounds | over | 6.5 | 108 | 1.0 | +18.8% |  |  |  |  |  |
| 2026-08-12 | Napheesa Collier | threes | over | 1.5 | -106 | 1.0 | +15.7% |  |  |  |  |  |
| 2026-08-12 | Marina Mabrey | rebounds | over | 3.5 | 142 | 1.0 | +14.0% |  |  |  |  |  |
| 2026-08-12 | Gabby Williams | rebounds | over | 2.5 | -102 | 1.0 | +13.6% |  |  |  |  |  |
| 2026-08-12 | Arike Ogunbowale | threes | under | 1.5 | 148 | 1.0 | +11.3% |  |  |  |  |  |
| 2026-08-12 | Paige Bueckers | assists | under | 5.5 | 116 | 1.0 | +21.5% |  |  |  |  |  |
| 2026-08-12 | Olivia Miles | points | over | 19.5 | -102 | 1.0 | +21.0% |  |  |  |  |  |
| 2026-08-12 | Courtney Williams | points | over | 12.5 | -118 | 1.0 | +12.9% |  |  |  |  |  |
| 2026-08-11 | Dearica Hamby | points | over | 13.5 | -113 | 1.0 | +21.8% | 19 | won | +0.88 |  |  |
| 2026-08-11 | Sonia Citron | threes | under | 1.5 | -136 | 1.0 | +15.7% | 1 | won | +0.74 |  |  |
| 2026-08-11 | Nneka Ogwumike | points | over | 15.5 | -114 | 1.0 | +10.3% | 18 | won | +0.88 |  |  |
| 2026-08-11 | Dearica Hamby | rebounds | over | 6.5 | -136 | 1.0 | +11.5% | 4 | lost | -1.00 |  |  |
| 2026-08-11 | Aliyah Boston | rebounds | under | 8.5 | 114 | 1.0 | +10.2% | 7 | won | +1.14 | -11.3% | -5.2% |
| 2026-08-11 | Michaela Onyenwere | threes | under | 1.5 | -114 | 1.0 | +21.6% | 0 | won | +0.88 |  |  |
| 2026-08-11 | A'ja Wilson | rebounds | over | 9.5 | 110 | 1.0 | +13.0% | 13 | won | +1.10 |  |  |
| 2026-08-10 | Jade Melbourne | points | under | 9.5 | -110 | 1.0 | +16.8% | 9 | won | +0.91 | -19.2% | -16.1% |
| 2026-08-10 | Rhyne Howard | assists | over | 3.5 | -112 | 1.0 | +11.2% | 7 | won | +0.89 | -6.6% | -9.9% |
| 2026-08-09 | Cecilia Zandalasini | assists | under | 2.5 | -162 | 1.0 | +33.2% | 2 | won | +0.62 | -8.7% | -6.0% |
| 2026-08-09 | Nneka Ogwumike | points | over | 14.5 | 100 | 1.0 | +19.4% | 16 | won | +1.00 | -4.5% | -7.8% |
| 2026-08-09 | Kahleah Copper | threes | under | 1.5 | 146 | 1.0 | +16.6% | 2 | lost | -1.00 | +0.6% | +5.9% |
| 2026-08-08 | Carla Leite | assists | under | 7.5 | 104 | 1.0 | +51.1% | 2 | won | +1.04 | -8.1% | -4.6% |
| 2026-08-08 | Bridget Carleton | points | under | 15.5 | 104 | 1.0 | +40.0% | 24 | lost | -1.00 | +3.1% | +6.6% |
| 2026-08-08 | Aliyah Boston | rebounds | under | 8.5 | 112 | 1.0 | +31.6% | 11 | lost | -1.00 | -9.2% | -3.1% |
| 2026-08-08 | Courtney Vandersloot | rebounds | under | 3.5 | -128 | 1.0 | +31.2% | 3 | won | +0.78 | -4.2% | +0.9% |
| 2026-08-08 | Natasha Cloud | assists | under | 6.5 | -125 | 1.0 | +28.2% | 4 | won | +0.80 | +3.8% | +6.8% |
| 2026-08-08 | Megan DiLeo | points | under | 15.5 | -102 | 1.0 | +26.9% | 11 | won | +0.98 | +0.1% | +3.5% |
| 2026-08-08 | Caitlin Clark | assists | under | 8.5 | 110 | 1.0 | +24.5% | 11 | lost | -1.00 | -3.7% | -0.1% |
| 2026-08-08 | NaLyssa Smith | points | under | 12.5 | -130 | 1.0 | +23.0% | 14 | lost | -1.00 | -8.0% | -5.0% |
| 2026-08-08 | A'ja Wilson | assists | under | 3.5 | 114 | 1.0 | +20.4% | 1 | won | +1.14 | +8.4% | +12.1% |
| 2026-08-08 | Olivia Miles | threes | over | 1.5 | 140 | 1.0 | +16.0% | 3 | won | +1.40 | +0.2% | -4.9% |
| 2026-08-08 | Jackie Young | rebounds | under | 4.5 | 112 | 1.0 | +38.7% | 5 | lost | -1.00 | -8.0% | -1.9% |
| 2026-08-08 | Courtney Vandersloot | assists | under | 6.5 | -158 | 1.0 | +30.7% | 6 | won | +0.63 | -11.5% | -8.7% |
| 2026-08-08 | Natasha Cloud | points | under | 12.5 | 100 | 1.0 | +32.4% | 17 | lost | -1.00 | -5.5% | -2.1% |
| 2026-08-08 | Dominique Malonga | points | under | 18.5 | -125 | 1.0 | +23.7% | 21 | lost | -1.00 | -10.0% | -7.0% |
| 2026-08-07 | Angel Reese | threes | under | 0.5 | 118 | 1.0 | +93.4% | 0 | won | +1.18 | +9.0% | +13.8% |
| 2026-08-07 | Diamond Miller | points | under | 11.5 | -114 | 1.0 | +28.9% | 9 | won | +0.88 | +7.3% | +10.4% |
| 2026-08-07 | Paige Bueckers | assists | over | 5.5 | 110 | 1.0 | +22.9% | 1 | lost | -1.00 | +9.9% | +6.3% |
| 2026-08-07 | Leila Lacan | assists | under | 4.5 | 122 | 1.0 | +20.0% | 4 | won | +1.22 | +18.4% | +22.2% |
| 2026-08-07 | Rhyne Howard | points | under | 17.5 | -130 | 1.0 | +17.7% | 19 | lost | -1.00 | -7.8% | -4.8% |
| 2026-08-07 | Jessica Shepard | rebounds | over | 11.5 | 114 | 1.0 | +16.0% | 11 | lost | -1.00 | +17.6% | +11.4% |
| 2026-08-06 | Caitlin Clark | assists | under | 8.5 | 100 | 1.0 | +16.9% | 8 | won | +1.00 | -4.9% | -1.5% |
| 2026-08-06 | Jackie Young | rebounds | under | 4.5 | -125 | 1.0 | +15.6% | 4 | won | +0.80 | -16.4% | -11.2% |
| 2026-08-06 | Aliyah Boston | rebounds | under | 8.5 | -122 | 1.0 | +14.7% | 12 | lost | -1.00 | -18.6% | -13.3% |
| 2026-08-06 | Nyara Sabally | points | under | 13.5 | -108 | 1.0 | +45.0% | 17 | lost | -1.00 | -6.0% | -2.8% |
| 2026-08-06 | Natasha Howard | rebounds | over | 6.5 | 124 | 1.0 | +42.5% | 6 | lost | -1.00 | -7.6% | -13.8% |
| 2026-08-06 | Carla Leite | assists | under | 8.5 | -158 | 1.0 | +37.9% | 11 | lost | -1.00 | -7.6% | -4.9% |
| 2026-08-06 | Kayla McBride | points | over | 17.5 | -113 | 1.0 | +29.2% | 15 | lost | -1.00 | -5.8% | -8.9% |
| 2026-08-06 | Courtney Williams | rebounds | over | 4.5 | 122 | 1.0 | +26.0% | 8 | won | +1.22 | -0.4% | -6.8% |
| 2026-08-06 | Maria Conde | rebounds | over | 4.5 | 108 | 1.0 | +25.0% | 6 | won | +1.08 | -6.7% | -12.7% |
| 2026-08-06 | Chelsea Gray | threes | over | 1.5 | 110 | 1.0 | +22.2% | 3 | won | +1.10 | -7.8% | -12.3% |
| 2026-08-06 | Olivia Miles | threes | over | 1.5 | 138 | 1.0 | +21.9% | 3 | won | +1.38 | -7.7% | -12.7% |
| 2026-08-06 | Rae Burrell | threes | under | 1.5 | 104 | 1.0 | +18.4% | 3 | lost | -1.00 | -4.8% | -0.3% |
| 2026-08-06 | NaLyssa Smith | points | under | 11.5 | -110 | 1.0 | +16.2% | 14 | lost | -1.00 | -14.8% | -11.6% |
| 2026-08-06 | Julie Allemand | assists | under | 6.5 | 100 | 1.0 | +23.8% | 12 | lost | -1.00 | -12.9% | -9.5% |
| 2026-08-06 | Megan DiLeo | rebounds | under | 4.5 | 102 | 1.0 | +22.4% | 3 | won | +1.02 | -5.8% | +0.1% |
| 2026-08-05 | Kamilla Cardoso | assists | over | 2.5 | 154 | 1.0 | +29.0% | 3 | won | +1.54 | +32.5% | +28.1% |
| 2026-08-05 | Jonquel Jones | threes | under | 1.5 | 124 | 1.0 | +22.1% | 1 | won | +1.24 | -6.5% | -1.6% |
| 2026-08-05 | Rae Burrell | points | under | 15.5 | -108 | 1.0 | +20.4% | 13 | won | +0.93 | -9.8% | -6.5% |
| 2026-08-05 | Erica Wheeler | assists | under | 6.5 | -158 | 1.0 | +18.9% | 5 | won | +0.63 | -5.3% | -2.6% |
| 2026-08-05 | Sabrina Ionescu | assists | under | 5.5 | -102 | 1.0 | +18.8% | 6 | lost | -1.00 | -9.5% | -6.1% |
| 2026-08-05 | Natisha Hiedeman | assists | under | 5.5 | -132 | 1.0 | +17.1% | 6 | lost | -1.00 | -1.1% | +1.8% |
| 2026-08-05 | Angel Reese | assists | over | 2.5 | 148 | 1.0 | +16.3% | 4 | won | +1.48 | +6.7% | +2.6% |
| 2026-08-05 | Rhyne Howard | assists | over | 3.5 | 136 | 1.0 | +25.3% | 8 | won | +1.36 | +18.0% | +14.0% |
| 2026-08-05 | Rhyne Howard | rebounds | over | 3.5 | 106 | 1.0 | +18.2% | 6 | won | +1.06 | -6.6% | -12.5% |
| 2026-08-05 | Shakira Austin | assists | under | 2.5 | 106 | 1.0 | +17.9% | 2 | won | +1.06 | -14.1% | -10.7% |
| 2026-08-05 | Natasha Cloud | points | under | 12.5 | -114 | 1.0 | +21.1% | 15 | lost | -1.00 | -21.2% | -18.1% |
| 2026-08-04 | Maria Conde | rebounds | over | 4.5 | 108 | 1.0 | +23.0% | 6 | won | +1.08 | -26.4% | -31.8% |
| 2026-08-03 | Kahleah Copper | points | under | 21.5 | -106 | 1.0 | +24.4% | 31 | lost | -1.00 | -0.7% | +2.6% |
| 2026-08-03 | Chelsea Gray | threes | over | 1.5 | 110 | 1.0 | +20.5% | 1 | lost | -1.00 | -5.4% | -10.0% |
| 2026-08-03 | Rhyne Howard | points | under | 16.5 | -110 | 1.0 | +21.4% | 19 | lost | -1.00 | -6.2% | -3.0% |
| 2026-08-03 | Angel Reese | rebounds | under | 11.5 | -102 | 1.0 | +20.0% | 16 | lost | -1.00 | -5.3% | +0.5% |
| 2026-08-03 | Natisha Hiedeman | assists | under | 5.5 | -130 | 1.0 | +17.8% | 7 | lost | -1.00 | -7.0% | -4.0% |
| 2026-08-03 | Sabrina Ionescu | assists | under | 5.5 | -106 | 1.0 | +16.4% | 5 | won | +0.94 | -11.2% | -7.9% |
| 2026-08-03 | Jackie Young | rebounds | under | 4.5 | -152 | 1.0 | +24.7% | 11 | lost | -1.00 | -6.4% | -1.7% |
| 2026-08-03 | Sydney Taylor | threes | under | 2.5 | 106 | 1.0 | +20.3% | 3 | lost | -1.00 | -0.2% | +4.3% |
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
| 2026-07-31 | Caitlin Clark | assists | under | 9.5 | -152 | 1.0 | +25.7% | 10 | lost | -1.00 | -7.2% | -4.4% |
| 2026-07-31 | Aliyah Boston | points | under | 17.5 | -122 | 1.0 | +25.7% | 14 | won | +0.82 | -3.2% | -0.1% |
| 2026-07-31 | Angel Reese | rebounds | under | 11.5 | 102 | 1.0 | +23.6% | 13 | lost | -1.00 | -6.6% | -0.8% |
| 2026-07-31 | Bridget Carleton | rebounds | over | 3.5 | 104 | 1.0 | +22.2% | 3 | lost | -1.00 | -5.7% | -11.6% |
| 2026-07-31 | Awa Fam | rebounds | over | 5.5 | -118 | 1.0 | +19.2% | 9 | won | +0.85 | -10.0% | -15.4% |
| 2026-07-31 | Megan DiLeo | points | under | 14.5 | -113 | 1.0 | +15.6% | 5 | won | +0.88 | -9.8% | -6.7% |
| 2026-07-31 | Natisha Hiedeman | threes | under | 2.5 | -205 | 1.0 | +15.0% | 0 | won | +0.49 | -7.6% | -4.5% |
| 2026-07-31 | Michaela Onyenwere | rebounds | under | 2.5 | 130 | 1.0 | +15.0% | 4 | lost | -1.00 | -1.0% | +5.6% |
| 2026-07-28 | Carla Leite | assists | over | 6.5 | 128 | 1.0 | +7.6% | 3 | lost | -1.00 | -3.9% | -8.0% |
| 2026-07-28 | Caitlin Clark | threes | over | 2.5 | 108 | 1.0 | +6.8% | 4 | won | +1.08 | +7.3% | +3.2% |
| 2026-07-28 | Kayla McBride | points | over | 17.5 | -120 | 1.0 | +5.0% | 18 | won | +0.83 | -7.7% | -11.2% |
| 2026-07-28 | Emily Engstler | threes | over | 1.5 | 194 | 1.0 | +31.7% | 1 | lost | -1.00 | -10.3% | -15.1% |
| 2026-07-28 | Caitlin Clark | rebounds | under | 3.5 | 106 | 1.0 | +6.7% | 4 | lost | -1.00 | -6.1% | -0.7% |
