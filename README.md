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

## Status

- [x] Repo scaffold
- [ ] Data download + ingestion
- [ ] Baselines (early vs close efficiency)
- [ ] Models
- [ ] Results
