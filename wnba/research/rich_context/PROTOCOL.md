# Does richer basketball information improve WNBA prop forecasts?

Research plan, written on September 6, 2026, before this experiment's model comparisons are run.

## The question

The current model mostly learns from a player's minutes and box-score totals. This experiment asks whether information about **how those totals happened** helps it predict the next game: where a player shoots, how often teammates create her baskets, when she plays, and what opportunities the opponent tends to allow.

More information does not automatically mean better predictions. We will compare the same kind of model with and without the new information. We will also try one modestly more flexible model. That separates the value of the data from the value of a different fitting method.

This is a research change. It does not change the live model, existing bets, historical results, or any scheduled routine. The requested deliverable is a pull request with code, data-source and quality records, a reproducible comparison, and a plain-language account of what happened. An unsuccessful experiment is a valid result.

## What is fixed before results are examined

- The four primary statistics are points, rebounds, assists, and made three-pointers.
- The four model comparisons below will all be reported. A disappointing model will not disappear from the results table.
- Model choices use basketball outcomes before 2025. Betting prices, betting lines, and market-implied forecasts are never training inputs or fitting targets.
- The main historical prediction time is the timestamp of the paired opening quote used for evaluation. Training examples use 24 hours before scheduled tip-off.
- The source-availability assumption, data exclusions, time splits, small tuning grids, and betting rules below are fixed before scoring.
- No 2026 source files, features, outcomes, or predictions enter this experiment. Existing prospective arms remain untouched.
- The 2025 report is a single, fixed comparison on an already-used development season. It is not a new holdout test and cannot establish a profitable betting edge on its own.

The implementation, source manifest, and this plan must be recorded in a commit before model scores are inspected. An implementation change needed before the first evaluation can amend this plan in that commit. Any later change must be dated, explain the reason, identify which results have already been seen, and preserve the original comparison. Changes after seeing 2025 are a separate exploratory experiment.

## The data and what we can honestly learn from it

Use ESPN-derived player and team box scores from 2003 through 2025 and available play-by-play from 2010 through 2025. Earlier seasons provide history; fitting and comparison periods are narrower, as specified below. Data retrieval may inspect schemas, counts, dates, and reconciliation failures before model fitting. Those checks are not permission to inspect outcome-based model scores early.

Public play-by-play describes shots, rebounds, assists, substitutions, and the game clock. It can add information that a final box score discards. It is not optical player tracking. In particular:

- An assisted made basket does not tell us how many good passes ended in missed shots. We must not label assisted makes as "potential assists."
- Shot coordinates do not tell us how closely a defender contested the shot.
- Historical lineups do not tell us who was expected to play before a later game's opener.
- Public historical files retrieved today may include later corrections. We do not possess their original publication history.

Sources and documentation:

- [SportsDataverse's WNBA data repository](https://github.com/sportsdataverse/wehoop-wnba-data) and [WNBA play-by-play loader](https://wehoop.sportsdataverse.org/reference/load_wnba_pbp.html).
- [wehoop shot-zone documentation](https://wehoop.sportsdataverse.org/reference/wnba_shot_zones.html).
- [pbpstats](https://github.com/dblackrun/pbpstats), which documents WNBA possession and lineup reconstruction. A supported capability is not evidence that our downloaded events support a complete reconstruction.
- [The WNBA's official tracking announcement](https://www.wnba.com/news/wnba-genius-sports-data-tracking), which describes richer information available to teams. This experiment does not claim access to that tracking feed.

### When a previous game becomes usable

For the main experiment, a past game's statistics become available **eight hours after its scheduled start, or one hour after its last timestamped play, whichever is later**. We choose a delay because original box-score and play-by-play publication timestamps are unavailable. A second, fixed sensitivity delays availability until **24 hours after scheduled start**, retaining the later-play check.

These are explicit approximations, not proof that a particular historical file was available at those times. Games with missing or ambiguous start times, unresolved postponements, or inconsistent event identities cannot supply time-sensitive features. A reliable completion timestamp also delays availability until at least an hour after completion. Date-only historical records use a two-day delay and are separately counted.

At each prediction cutoff, query the state after all eligible prior games have been processed. Include the most recent eligible game's update. Do not copy the pregame state stored on its row. Never update any state from the target game before producing its prediction.

The 24-hour sensitivity uses the same chosen model settings and betting rules. It does not trigger another tuning search. Report its coverage and changes in results alongside the main comparison.

### Quality checks before fitting

Keep a manifest with source URLs, downloaded dates, file hashes, seasons, rows, games, and coverage. Record failed and incomplete downloads explicitly.

Identify games and athletes by source IDs; verify the two teams and date when joining different providers. Player-name matching alone cannot silently determine an ambiguous match. Remove exhibitions and All-Star games using schedule metadata.

Before using a game's play-by-play as a feature source:

1. Check event order, periods, clock ranges, duplicate event IDs, and score progression.
2. Reconcile player scoring, made threes, assists, and rebounds against box scores where those events can be assigned. Keep team rebounds distinct from player rebounds.
3. Reconcile made and attempted shots when the event types support it. Record missing coordinates separately from missing shots.
4. Check that inferred playing time and any reconstructed on-court groups are possible. An exact lineup feature requires five identifiable players per team and coherent substitutions. Do not silently fill broken lineups with the final roster.
5. Publish failures and resulting coverage by season and feature family. A failed feature family is missing information, not a string of zeros.

Use strict reconciliation for the feature being constructed. If a source cannot support a feature reliably, omit that feature before model evaluation and record the reason. Preserve the box-only baseline on rows without usable play-by-play. Report both common-coverage comparisons and the wider population with a declared baseline fallback.

## How the new information enters a forecast

Every feature below summarizes eligible past games. None describes what actually happened in the game being predicted.

| Information | Relationship being tested |
| --- | --- |
| Shot mix and distance | Rim shots, other two-pointers, and threes create different scoring and rebound opportunities. Separate attempt volume from shooting success. |
| Assisted makes and shot types | A player's scoring may depend on teammates creating her shots; a passer's assists depend partly on teammates finishing them. |
| Minutes and usage by period | First-quarter involvement and late close-game participation may reveal role changes that a full-game average hides. |
| Prior expected teammate usage | Several players compete for the same shots, passes, and rebounds. Changes in the expected supporting cast may change opportunity. |
| Opponent shot and rebound profile | A player's normal shot mix and rebounding role may work differently against different kinds of opponents. |
| Recent variability and sample size | A stable role and a recent role change should not receive the same confidence. Sparse histories need more shrinkage. |

Use historical rate summaries, recent changes, sample-size indicators, and explicit missingness indicators. Keep the incumbent's exponential weights: 0.18 for fast player histories and 0.05 for slow histories (roughly 3.5- and 13.5-game half-lives). Rich player summaries use 0.18; team summaries use 0.10. These are fixed rather than searched.

The rich feature set may include player shot-zone shares and make rates; assisted-make share; prior period-level shots, assists, rebounds, and playing-time summaries; close-game involvement; opponent shot-zone and miss rates; and expected teammate shot, assist, and rebound demand. The checked-in feature list must state the exact columns and any pre-evaluation omissions.

Expected teammates come from prior appearances and prior estimated minutes. A player absent from the target game's final box score is not a known pregame injury. Do not train on final-game starter flags, DNP reasons, actual target-game minutes, or news records that lack a timestamp before the forecast cutoff.

Do not add thousands of unregularized player-pair or lineup effects. Exact lineup features are optional only if source checks pass before scores are opened; their absence must be stated plainly. There is no requirement to invent an interaction when its data is unreliable.

## The four comparisons

1. **Corrected incumbent.** Preserve its existing talent and mean-model choices. Repair the time-of-prediction state query, replace offer-batch league normalization with a prior-game league context, and calculate push probabilities correctly. Any fitted calibration uses only data before the relevant evaluation period. Its saved settings remain fixed during 2025.
2. **Box-only ridge.** A small regularized regression uses the incumbent forecast plus historical box-score and schedule features. Ridge means fitting with a penalty that discourages large, unstable effects. It controls for the possibility that a new fitting method alone improves the result.
3. **Rich ridge.** The same target, fitting method, tuning grid, and preprocessing as box-only ridge, with the new play-by-play and relationship features added. This is the primary test of richer information.
4. **Rich small-tree model.** A histogram gradient-boosted model uses exactly the rich model's feature columns. Small trees can express relationships such as a player's shot mix mattering differently against different opponents. This tests a modest increase in flexibility, not an unrestricted search for a profitable backtest.

Each statistic has its own fit. Both ridge models predict the difference between the actual count and the incumbent mean. Add that correction to the incumbent mean with a fixed weight of one, then set any resulting mean below 0.02 to 0.02. This keeps the comparison simple: the model learns a correction in points, rebounds, assists, or threes rather than changing the meaning of its target.

The small-tree model predicts the nonnegative outcome mean using Poisson loss, with the incumbent mean included as an input. Although called Poisson loss, this fitting objective does not force the final outcome distribution to have Poisson variance.

Initial fixed grids:

| Choice | Allowed settings |
| --- | --- |
| Ridge penalty after training-only feature scaling | 10, 100, 1,000, 10,000 |
| Tree maximum leaves | 7 or 15 |
| Tree minimum observations per leaf | 100; fixed |
| Tree learning rate and boosting rounds | 0.05 and 150; fixed |
| Tree L2 penalty | 10; fixed |
| Random seed | 20260906; fixed |

Disable random validation splits and automatic early stopping. For ridge, replace missing values with training-period medians, then standardize features using the training mean and standard deviation. Missingness indicators accompany replaced values. The rich tree model receives the identical substantive feature columns, including the incumbent mean. Select each model's setting by the lowest mean Poisson deviance on 2023 player-game outcomes for that statistic. This scores the count forecast without using any betting lines. On an exact tie, choose stronger ridge shrinkage or the smaller tree model. No grid expansion follows an unfavorable result.

## The calendar of fitting and scoring

| Period | Purpose | What may change afterward |
| --- | --- | --- |
| Before 2015 | Build strictly prior history and the incumbent's previously specified priors; no new target-based tuning | Nothing based on later outcomes |
| 2015–2022 | Fit the initial candidate models | Choose only among the listed settings using 2023 |
| 2023 | Select settings by market-free count prediction quality | Refit the selected recipe through 2023 |
| 2024 | Report the pre-2025 chronological validation; estimate pre-2025 distribution calibration from predictions made without that game's outcome | Refit mean models through 2024 with the already chosen settings; freeze calibration |
| 2025 | One final comparison on reused development data | No model or threshold changes within this registered comparison |
| 2026 | Excluded entirely | Existing prospective tests remain untouched |

The 2024 validation is reported even when it fails. It is not a second opportunity to choose new features. First report uncalibrated 2024 count prediction quality from the models fitted through 2023. Then estimate each model's scalar mean correction and count-distribution spread from those saved predictions and outcomes. These are past observations when predicting 2025, but they cannot retrospectively make the corrected 2024 scores an untouched validation result. Do not estimate uncertainty from residuals that merely describe how well an overfit model reproduces its own training rows.

State updates during 2025 may incorporate already-available earlier 2025 games, under the same eight-hour or 24-hour rule. Model coefficients, tuning choices, and calibration remain frozen. This is ordinary chronological forecasting, not fitting on future games.

## Turn a forecast into a price

Save a positive count mean and a full count distribution for each primary statistic. Use the same distribution-calibration procedure for all four primary comparisons. For each model and statistic, estimate a single mean multiplier as total observed counts divided by total predicted counts on the held-forward 2024 predictions. Estimate negative-binomial extra dispersion by count likelihood on the same historical prediction sample. These choices use no odds or line targets. The implementation must record parameter estimates, fallback rules, and tail handling before 2025 is scored.

The default comparison uses a negative-binomial count distribution with calibrated mean from the candidate and extra variance estimated as above, falling back to Poisson if no positive extra variance is supported. The corrected incumbent mean receives the same calibration and distribution treatment in the primary table. Its repaired original distribution is a clearly labeled secondary comparison if implemented. This separates mean-forecast improvements from changing the distribution recipe.

At every quoted line, calculate three probabilities: **win, push, and lose**. They must sum to one. A push returns the stake. Expected profit for a $1 bet is `P(win) × (decimal odds − 1) − P(lose)`.

For a half-point line there is no push. For a whole-number line, evaluate binary over/under log loss conditional on a non-push, and evaluate count-distribution quality on all valid outcomes. Do not discard pushes before generating predictions or selecting bets.

Save the predicted mean, median, fair over/under/push probabilities, and quoted threshold separately. A mean of 17 points is not automatically a fair betting line of 17.

Points-plus-rebounds and similar combinations are secondary only. If included, their spread must preserve historical dependence estimated before 2025; adding independent component variances is not justified. Primary conclusions must stand on the four registered individual statistics even if no combination model is implemented.

## What the results must show

### Forecast quality

Report player-game count error and Poisson deviance before any market comparison. At available market thresholds, report log loss, Brier score, average probability versus observed frequency, and calibration by probability band. Smaller log loss and Brier score mean better probability forecasts.

Use all common eligible quotes across the four core markets for the primary paired comparison. Report rich ridge minus box-only ridge as the primary information test. Report the small-tree model minus rich ridge as the flexibility test, and all candidates against the corrected incumbent. A negative difference in log loss favors the first model. Include the number of quotes and each market's share of the pooled result, then report each market separately. Any equal-market average is a labeled secondary summary, not a replacement for the fixed pooled comparison.

Use 10,000 bootstrap resamples of whole Eastern Time game dates, seed 20260906, to report paired 95% intervals. Each sampled date brings all its games, players, and offers with it. Use the same sampled dates across models. State that this describes uncertainty conditional on the dataset and timing assumptions, not uncertainty about source errors or future market changes.

### Historical betting simulation

Prices are used only after forecasts have been produced. Require a coherent over/under pair from the same named source, same event, same threshold, and compatible timestamp. Preserve bookmaker identity and quote time. Do not relabel a provider's mixed-source opener as a synchronized market consensus.

Run two fixed, separate policies: expected return above **5%**, and above **10%**. For each paired quote compare the over and under and keep the higher expected return. Process quotes in time order and take the **first qualifying quote for each player and game**. If several markets qualify at that same timestamp, take the highest expected return, then break exact ties by market name and side. This permits at most one selection per player per game for each model and threshold rule. Never wait retrospectively for a more attractive market that was published later. Do not buy both sides or count repeated snapshots as separate bets.

Stake $1 per selection. Report bets, wins, losses, pushes, voids, dollars staked, realized profit, claimed expected profit, return on settled stakes including pushes, and over/under mix. Voids return the stake and are excluded from the settled-stake denominator and matched expected-profit total; show their count separately. DNP is not a winning under. Never use an outcome-based exclusion to decide whether a historically offered bet would have been selected.

Compare market probabilities at the same threshold and source. A closing threshold that moved is not directly comparable without an explicitly labeled modeling assumption. Where opener provenance or availability cannot be established, label returns as a historical quote benchmark, not executable fills. There is no claim that these prices were available to this account at the required stake.

Include an offer-level appendix if useful, but it cannot replace the one-selection-per-player policy. Do not select players, months, sides, markets, or thresholds by their 2025 profitability. Predefined market and period breakdowns are descriptions, not separate discoveries.

## What would count as progress?

The main question is whether rich ridge improves on box-only ridge on the same observations, with count prediction and probability scoring pointing in the same direction. Report the size and uncertainty of the change. A higher simulated return alone is insufficient, especially when the probability forecasts worsen or only a few bets explain the result.

A clear failure means preserving the code and evidence, saying which information failed to help, and leaving the live model alone. A promising development result means a candidate for a separately registered future test. It does not justify restarting bets, claiming market superiority, or rewriting earlier results.

## Checks that must pass before the report is accepted

- Adding future games cannot change a past forecast.
- Altering the target game's outcomes cannot change its own pregame features or forecast.
- Forecasting one player alone, duplicating an unrelated offer, or removing a push elsewhere cannot change that player's probabilities.
- A latest eligible game updates the next forecast exactly once; historical replay and the direct state query agree.
- Zero, half-point, and integer thresholds produce coherent count probabilities. Fair odds imply zero expected profit with pushes included.
- Missing or failed data does not silently become an observed zero or a healthy source.
- No fitting or evaluation path can read 2026 inputs without failing explicitly.
- The saved feature list, source hashes, settings, forecast cutoff, probabilities, selected bets, and results are sufficient to reproduce the report.

The forecast deliverable is a historical fair-line file: each eligible quote's predicted mean, median, over/under/push probabilities, and source line. There is no current-2026 prediction run in this scope.

Keep the report human-readable: explain the basketball relationship first, define each necessary statistical term once, and put detailed settings and row-level output in supporting files. Lead with what improved, what did not, and how much confidence the evidence supports.
