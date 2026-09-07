# Registration: does the rate state contain useful predictive uncertainty?

Status: registered when this document reaches GitHub, before any pilot source
or residual computation. It is separate from the two stopped structural
experiments and the completed saved-forecast diagnosis.

## Decision and mechanism

The frozen model maintains a posterior variance for each per-possession rate
and uses it in its Kalman update. Its final count distribution uses the rate
mean and a fixed market Fano factor, without separately carrying rate variance
into the predictive mixture. The global Fano was fitted from one-step count
residuals, so some rate uncertainty may already be absorbed in it. Adding the
posterior term directly could double count uncertainty.

Decide whether a variance-aware count-distribution study is worth implementing.
First check the units, source/state reconstruction, the magnitude and calibration
of the rate uncertainty, and whether its varying size contributes information
beyond an uncapped constant variance multiplier. No market price or new model
mean, minutes model, betting policy or live process is evaluated here.

## Fixed recipe and data

- Use the already frozen `initial_recipe` from `validation_1.json` in the final
  original fit bundle: box variant, shrinkage 1, fitted through 2022. Verify that
  its recipe hash, training cutoff and original fit provenance agree. Do not
  refit the rate process, its priors, minutes coefficients or state-noise terms.
- Retain historical source observations through 2024 only. Select source-manifest
  assets by year before loading their values; do not open 2025 or 2026 assets.
  Reuse the pinned source commit and unchanged normalization/clock definitions.
  No raw source download is needed if the attested historical cache is intact.
- Calibration observations are all competitive observed player-box appearances
  from 2015 through 2022. Primary checks use all such 2023 appearances; 2024 is
  a fixed descriptive sensitivity using the same through-2022 recipe and the
  same noise scalars. Do not refit after looking at 2023.
- The original 2023 recipe comparison and subsequent 2024/2025 development are
  known prior uses. Both check seasons are reused development, not independent
  holdouts. Shrinkage 2 is outside this pilot; there is no recipe selection.
- Use the existing 24-hours-before-tip training request rule and assumed
  8-hour historical data availability. Select past records by their existing
  availability and event clocks; target box outcomes may only grade the saved
  pregame state. Final target rosters, minutes and counts cannot enter that state.

The denominator begins with every raw source appearance in each requested
season. Report distinct player-game identities, malformed/missing requests,
noncompetitive exclusions, normalized rows, explicit DNPs and played rows.
Missing records are not DNP. Normalized observed appearances are not a complete
pregame roster and are not the quoted-prop population.

## Source and state checks before aggregate residuals

Select 20 game IDs in each of 2023 and 2024 by ascending
`SHA256("rate-uncertainty-source-v1:" + game_id)` from schedule IDs alone.
Retain every requested appearance in those games, including failures. For each:

1. Reconcile game, player, team and source identities. Verify positive team
   duration/possession exposure, nonnegative integer counts and finite minutes.
   Explicit zero-minute rows must have zero counts; their rate innovations are
   undefined and must not enter the played denominator.
2. Verify source clocks against the existing schedule/PBP completion rule and
   confirm every state-consumed record predates the pregame request and belongs
   to another game. Timing remains assumed historical availability.
3. Reproduce pregame rate mean and variance from only that player's eligible
   chronological history using an independent implementation of the frozen
   rate update. Check both arrays within 1e-10 relative/absolute tolerance.
   No target outcome is needed for this identity check.
4. Document units: rate is counts per estimated possession; its variance has
   squared rate units; multiplying by exposure squared gives count variance.
   The box exposure is actual minutes times actual team estimated possessions
   divided by game duration. This is a retrospective analysis denominator,
   never information supplied to a pregame forecast.

Require at least 99% usable/reconciled requested source appearances in each
sampled season, reporting every omission. Require all consumed clocks and all
reconciled state identities to pass. Any negative/nonfinite rate variance,
nonpositive played exposure or impossible grade fails the pilot. Stop before
estimating noise scalars if these checks fail; do not substitute another sample.

## Predetermined variance estimators

For each played calibration appearance and each of the existing four markets,
save pregame rate mean `r`, posterior variance `v`, actual exposure `e` and count
`y`. Let `mu = e*r`, residual `d = y-mu`, and rate-uncertainty term `U = e^2*v`.
Use the posterior variance exactly as returned by the frozen state. Do not add
an unregistered future process-noise projection or alter the Kalman update.

Estimate these two scalars once on 2015–2022, separately for each fixed market:

- `F_total = max(1, sum(d^2) / sum(mu))`.
- `F_noise = max(1, (sum(d^2) - sum(U)) / sum(mu))`.

These are two fixed moment estimators for a descriptive variance comparison,
not a grid search or fitted count-probability candidate. Record the uncensored
ratios and any floor activation. The uncapped `F_total` is a required control:
it prevents a win caused only by removing the old upper bound of 2 from being
attributed to rate uncertainty.

Freeze the two scalars before computing aggregate 2023/2024 check results.
Compare three variance calculations with identical `mu`, residual and rows:

1. Original constant control: `V_old = frozen_Fano * mu`.
2. Uncapped constant control: `V_constant = F_total * mu`.
3. Separate observation/rate variance: `V_rate = F_noise * mu + U`.

The rate-aware calculation describes innovation variance conditional on actual
exposure. It is not a forecast with known minutes, a complete count distribution
or an estimate of market performance. Means, observation history and rates are
identical across the three calculations.

## Fixed reports and gates

Report calibration and each check year separately, with all four market totals
and no new player, team, role, prediction-size or outcome-selected groups:

- Raw/requested/normalized/played/DNP/excluded counts and source-check results.
- The two fitted scalars, original Fano, raw estimates and floor activations.
- Mean residual; each variance's mean; `sum(d^2)/sum(V)`; and average Gaussian
  variance score `log(V) + d^2/V`, with paired rate-minus-control differences.
- Mean rate-uncertainty share `mean(U/V_rate)` and aggregate share
  `sum(U)/sum(V_rate)`. Report both so tiny-exposure rows cannot dominate an
  interpretation of the aggregate variance budget.
- Minimum/maximum and fixed median/90th-percentile summaries of `v`, `U` and
  `U/V_rate`, preserving all extreme rows in the evidence. No outlier trimming.
- Mean-error and variance identities, input/recipe hashes and a complete receipt.

Points are the sole primary mechanism. Other markets remain reported controls;
none can replace points after results. To justify constructing one variance-aware
points candidate, require all of the following:

1. All source/state/units checks pass and neither points moment estimator needs
   its lower floor. A floor means this simple observation/rate separation is
   insufficiently reconciled; do not repair it within this pilot.
2. Aggregate rate-uncertainty share is at least 5% in both 2023 and 2024. Smaller
   magnitude is insufficient for this proposed mechanism's development cost.
3. The 2023 points variance score improves by at least 0.01 against the uncapped
   constant control, and the same comparison is no worse in 2024.
4. Points `sum(d^2)/sum(V_rate)` lies in [0.90, 1.10] for 2023 and [0.85, 1.15]
   for 2024. These are engineering screening thresholds set before this pilot,
   not new market-performance criteria or statistical confidence intervals.

A failure ends with stop this variance change, or collect a specifically named
missing source if source checks failed. Passing only the old capped control is
insufficient. Passing all gates justifies a separate registered candidate study;
it does not establish calibrated count probabilities, an edge or independence.

## Budget and evidence

Limit: one complete pilot execution and one exact saved-input
reproduction, at most ten wall-clock minutes each. Zero new rate/minutes fits,
zero tuning grids, zero bootstrap/model selection, and zero 2025/2026 or market
evaluation. The two explicit moment scalars per market are the only estimates.
Freeze their bytes after calibration and before either check-year aggregate.

Preserve exclusive output directories, all requested-source rows, pregame rate
checkpoints, calibration/scalar freeze, check rows, compact results and receipts.
Synthetic tests must cover rate-state reconstruction, units/exposure scaling,
DNP/missing denominators, future-outcome invariance, floor/negative-variance
handling, fixed-control equality, protected-year exclusion and overwrite refusal.
Save a short decision explaining whether a rate-aware points distribution is
worth constructing. Public implementation/registration and private evidence
remain separated under the existing disclosure decision.
