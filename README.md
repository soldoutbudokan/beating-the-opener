# NBA Win Prob

An NBA win-probability model, benchmarked against the Vegas closing line.

Goal: build a model that is **more efficient than the Vegas line** (moneyline /
spread / total) on a held-out set covering the last three seasons.

**Result: the goal was not achieved, and the evidence says it is not achievable
with this data.** The closing line wins on every market tested, and an
encompassing regression shows the line already contains everything the model
knows. Full write-up in `reports/report.html`.

## Headline numbers

Held out: **2023-24, 2024-25, 2025-26** — 3,962 games, never trained or tuned on.

| Forecast | Log loss | Brier | Acc | vs line | p |
|---|---|---|---|---|---|
| **Vegas closing line** | **0.58180** | 0.19924 | 68.91% | — | — |
| Vegas opening line | 0.59201 | 0.20347 | 68.12% | +0.0103 | <0.0001 |
| Model — Tier B (pre-tip) | 0.59531 | 0.20479 | 67.62% | +0.0135 | <0.0001 |
| Model — Tier A (strict) | 0.60663 | 0.20954 | 66.84% | +0.0248 | <0.0001 |
| Elo baseline | 0.62144 | 0.21590 | 65.45% | +0.0396 | <0.0001 |
| Tier A + line blend | 0.58249 | 0.19942 | 68.75% | +0.0007 | 0.025 |
| Tier B + line blend | 0.58254 | 0.19944 | 68.68% | +0.0007 | 0.019 |

Other markets, same held-out set:

* **Spread** — margin model covers 48.7–50.5% ATS (break-even at −110 is 52.38%).
* **Total** — 48.8–52.5% over/under; MAE 15.0 pts vs the market's 14.3.

Decisive tests:

* **Encompassing regression** `logit(y) ~ logit(market) + logit(model)`:
  model coefficient **−0.08 (p=0.43)** Tier A, **−0.11 (p=0.40)** Tier B.
  The line encompasses the model.
* **Subset search** across 14 regimes (early season, playoffs, back-to-backs,
  heavy injuries, big favourites, coin flips, rest, travel): **0** where the
  model beats the line.

## Why it is not achievable (three upper bounds)

The negative result is backed by bounds, not just by failure to find a win.

**1. Better modelling of these features — ceiling 0.5932.**
`src/diagnose_gap.py` projects `logit(market)` onto `logit(model)`; the residual is
the market's private information and is strongly predictive (coef +1.16, z=9.97).
All 95 observables jointly explain only **17.6%** of it — 82.4% is orthogonal to
everything measured. Perfectly exploiting every feature in hand (optimistic,
in-sample) reaches **0.5932**, still short of 0.5818.

**2. Better player ratings — ceiling 0.5915.**
`src/oracle_bound.py` refits RAPM on the *entire* dataset including the held-out
seasons, so each player gets a rating no causal system could beat. It ranks
sensibly (Curry, Green, Leonard, Tatum, Durant). The oracle model scores
**0.59150**, losing by 0.0097 (p=0.0001). Perfect player valuation buys 0.0038 of
the 0.0135 needed — so **stint-level RAPM from play-by-play cannot close it**,
which is why that build was bounded rather than undertaken.

**3. Capacity — not a tuning problem.**
The sweep preferred *heavier* regularisation (C=0.003), an MLP did worse, and the
same ~0.014 gap reproduces on validation seasons.

**The one input that would matter** is a point-in-time injury report. It is not
retrievable: ESPN's `injuries` block returns today's status regardless of the game
requested (a Jan-2025 game returns Jul-2026 designations), and the per-athlete
injury-history endpoint 404s.

## The leakage finding

The first availability features asked "who appears in tonight's box score". That
version tied the closing line, its blend beat it at p=0.003, and it returned
**+12.3% simulated ROI** at real closing prices — which is not plausible.

Cause: blowouts empty the bench. Teams used 24.3 players between them in 20+
point games vs 19.4 in games inside 5 points, so box-score appearance is partly a
readout of the final margin (`corr(|margin|, n_active) = +0.64`), and the winning
team empties its bench more, leaking direction too.

Fix (`src/availability_v2.py`): restrict every availability judgement to
**established rotation players**, identified by EWMA minutes from *prior* games
only. Their participation does not depend on game flow.
`corr(|margin|, n_missing)` fell from **−0.46 to −0.033**. The edge vanished.
`src/build_dataset_v4.py` runs a leakage guard that fails any feature correlating
above 0.12 with the absolute margin.

## Data

All from public ESPN endpoints, 2014-15 → 2025-26.

| File | Rows | Contents |
|---|---|---|
| `data/raw/games.csv` | 16,466 | results, dates, home/away, season type |
| `data/raw/odds.jsonl` | 15,499 | open/close moneyline, spread, total; consensus + per-book |
| `data/raw/team_box.jsonl` | 30,998 | team box scores (possessions, efficiency) |
| `data/raw/player_box.jsonl` | 400,559 | player minutes, box lines, DNP flags |

Closing moneyline coverage is 99.8%+ in every season; opening lines are available
for all three held-out seasons.

## Method

Point-in-time throughout: every rolling statistic updates only *after* a game's
row is emitted.

* **Base** (`features.py`) — margin-aware Elo with season carryover, EWMA margin
  and points, rest / back-to-back / 3-in-4 / 4-in-6, travel km, timezone shift,
  road-trip length, streaks, win%.
* **Efficiency** (`advanced_features.py`) — possession-based offensive/defensive
  ratings and pace, EWMA with season regression.
* **Opponent-adjusted** — ridge fit of `margin ~ home − away + hfa` over a
  trailing window with exponential decay, refit weekly on finished games only.
* **RAPM** (`rapm.py`) — minutes-weighted APM over 1,678 players, ridge with time
  decay, refit fortnightly.
* **Availability** (`availability_v2.py`) — rotation-restricted talent available
  and talent missing, plus a lineup-adjusted RAPM.

**Information tiers.** Tier A (63 features) uses nothing from tonight's game.
Tier B (94) adds which established rotation players are dressed — public ~30 min
pre-tip and therefore priced into a closing line, but not available a day earlier.

**Protocol.** Seasons ≤2018 train; 2019–2023 walk-forward validation (used for
hyperparameters, stack weights, blend weights); 2024–2026 held out and scored
once. Predictions for season *S* come only from models fit on seasons < *S*.
Models: regularised logistic, three gradient-boosting configs, GBM and ridge
margin regressions mapped through a fitted normal, stacked by logistic
regression. Significance from a 10,000-sample paired bootstrap on per-game
log-loss differences.

Tuning confirmed the model is at its ceiling: the sweep preferred *heavier*
regularisation (C=0.003), an MLP did worse, and the same ~0.014 gap reproduces on
validation seasons.

## Reproducing

```bash
python3 src/fetch_games.py        # ~140 requests, cached
python3 src/fetch_boxscores.py    # 15,499 summaries, resumable
python3 src/fetch_odds.py         # 15,499 odds records, resumable
python3 src/build_dataset_v4.py   # clean feature table + leakage guard
python3 src/final_experiment.py   # tier audit vs the closing line
python3 src/markets_analysis.py   # moneyline, spread, total
python3 src/encompassing.py       # encompassing test + subset search
python3 src/diagnose_gap.py       # what the line knows that the model doesn't
python3 src/oracle_bound.py       # look-ahead player-rating upper bound
python3 src/final_report.py       # writes reports/results.json
```

Requires `pandas`, `numpy`, `scikit-learn`, `scipy`, `requests` (and `torch` for
`src/tune.py` only). Full run is a few minutes after the fetches.

`data/` is gitignored — the raw pulls are ~350MB and the derived tables another
~200MB. All of it regenerates from the three fetch scripts, which cache to disk
and resume, so a fresh clone is one `fetch` sweep away from the published numbers.

## Caveats

* Tier B uses which rotation players are dressed, taken from the box score. That
  is public before tip, but a genuine pre-game inactive-report feed would be
  cleaner. Tier A is the unimpeachable floor and is reported alongside.
* `dataset_v3.csv` / `preds_tiers.csv` retain the **leaky** v1 features and are
  kept only to document the false positive. `dataset_v4.csv` is canonical.
* Recent seasons carry a single book, so line shopping — the realistic
  professional edge — could not be tested.
