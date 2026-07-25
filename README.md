# Beating the Opener

**Thesis:** FanDuel's soccer 1X2 markets — especially lower-division leagues (EFL League One/Two,
European second divisions, smaller national leagues) — are largely copied from market-leader prices
early in the week and are *beatable at the time they're posted*, even if the closing line is not.

This project builds a model and tests, over 13+ seasons and ~20 leagues (all carried by FanDuel),
whether public information available at posting time produces probabilities that are **provably more
efficient than the early/opening line**, with the closing line as the honest benchmark.

Predecessor project: [nba-win-prob](https://github.com/soldoutbudokan/nba-win-prob) concluded the
NBA closing moneyline is not beatable with public data. This project targets a softer market and the
*opening* price, where the efficiency literature (and line-movement evidence) says edges actually exist.

## Data

[football-data.co.uk](https://www.football-data.co.uk/) historical CSVs:

- **Early odds** (`PSH/PSD/PSA`, `B365H/...`, `MaxH/AvgH`): collected Friday afternoon for weekend
  games / Tuesday for midweek — a proxy for the line shortly after posting.
- **Closing odds** (`PSCH/PSCD/PSCA`, since 2012-13; full multi-book closing since 2019-20).
- ~22 divisions: England E0–E3 + Conference, Scotland SC0–SC3, Germany D1–D2, Italy I1–I2,
  Spain SP1–SP2, France F1–F2, Netherlands, Belgium, Portugal, Turkey, Greece.

Raw data is not committed; run `python3 src/download_data.py` to fetch it.

## Method (planned)

1. De-vig early and closing odds (proportional + Shin) → baseline probabilities.
2. Confirm the sanity result: closing beats early on out-of-sample log loss (the market itself
   "beats the opener" — so the opener is not the efficient price).
3. Build walk-forward models (Elo/Dixon-Coles goal model + gradient boosting, with and without
   the early odds as a feature).
4. Test: model log loss < early-line log loss out-of-sample, paired significance tests per match.
5. Betting simulation at early prices (with real vig, and at best-of-book prices) + closing line
   value (CLV) measurement.
6. Honest benchmark vs the close.

## Results so far

**The early line is provably not the efficient price.** On 100,584 matches (2012-13 → 2025-26)
with both Pinnacle early and closing odds, Shin-devigged closing probabilities beat early
probabilities on log loss by **+0.0031** (paired t = 12.6, p ≈ 2e-36), consistently across all
five league tiers. The average home win probability moves 2.2–2.7 points between the early
collection and the close. Whatever information moves the line is not in the early price — the
question is how much of it a model can capture at posting time.

| tier | n | early LL | close LL | close edge |
|---|---|---|---|---|
| top 5 leagues | 21,329 | 0.96615 | 0.96344 | +0.0027 |
| second divisions | 24,693 | 1.04349 | 1.04107 | +0.0024 |
| lower England (E2/E3/EC) | 19,231 | 1.03858 | 1.03574 | +0.0028 |
| Scotland | 7,248 | 0.99317 | 0.98913 | +0.0040 |
| smaller top flights (N/B/P/T/G) | 16,425 | 0.95901 | 0.95436 | +0.0046 |

Pinnacle's early overround is also systematically wider than at the close (e.g. 3.5% → 3.2% in
lower England) — the book itself is less confident in its early prices.

## Status

- [x] Repo scaffold
- [x] Data download + ingestion (138,810 matches, 396 files, 22 divisions, 2008-09 → 2025-26)
- [x] Baselines: close beats early, p ≈ 2e-36 → the wedge exists
- [ ] Walk-forward models vs the early line
- [ ] Betting simulation + CLV
- [ ] Final writeup
