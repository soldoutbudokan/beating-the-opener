# Registration: participation repair, bounded uncertainty comparison and prospective points study

Registered when this file first reaches GitHub, before the new source cohort,
comparison or prospective price capture is computed. This is a new source and
state recipe, `wnba-participation-v2`. It does not reopen or amend the stopped
structural experiments, completed props arms or previous uncertainty pilot.
The earlier pilot stopped at its source gate and fitted no new variance scalars.

## Question and fixed order

First preserve actual participation without inventing playing time. Then test
whether posterior rate uncertainty improves a complete points distribution
beyond an uncapped constant dispersion. Finally freeze one candidate under the
selection rule below and measure new forecasts against genuinely observed
prices. Historical development cannot qualify a market-beating model.

Implementation and synthetic checks precede empirical work. Publish this plan
before reading the new cohort, and save the cohort before inspecting counts.
Freeze calibration scalars before either check-season aggregate. Freeze the
selected candidate, inputs, code and prospective contract before future quotes.
Complete these three stages before opening the requested implementation PR.

## Participation and exposure source contract

Reuse the pinned historical source commit, original asset manifest and unchanged
valid team/schedule/completion calculations. Select assets by year before
loading their values: only seasons through 2024 enter repair and comparison.
The original manifest checksum is
`1fedab483827cca1d4887dce270a63e124fe4d4edf3c1786adcff7104025a132`.

Freeze every explicitly non-DNP row with zero or missing displayed minutes in
the through-2024 source, using only identity, season, minutes and DNP metadata.
Do not select this cohort by whether points or other counts are positive.
Retain every raw row, excluded identity, conflict and unknown in the audit.

- Participation is `played`, `dnp` or `unknown`. Explicit non-DNP is played even
  with zero or missing minutes. Positive valid minutes can establish played
  when the DNP field is absent; zero minutes alone cannot establish DNP.
- Keep the raw counts and reported minutes. Exposure is `observed_positive`,
  `unknown`, `certified_zero` or `not_applicable`. Displayed zero alone is not
  proof of zero game-clock exposure. This study certifies no PBP-derived seconds.
  Never substitute an epsilon duration or invented seconds.
- Separate `minute_measurement_eligible` and `rate_measurement_eligible`.
  Confirmed played updates participation even if exposure is unknown. Only
  valid positive minutes update numeric minute measurements; valid counts and
  valid positive exposure are required for a production-rate measurement.
- An explicit DNP can map null counts to zero, preserving the raw nulls. DNP
  conflicting with positive counts or minutes, negative/nonfinite/invalid
  values, ambiguous identity and inconsistent clocks fail their source gate.
  Unknown participation remains unresolved and cannot become a zero outcome.

The new state keeps participation and numeric minute observation counts
separate. Unknown exposure cannot tighten minute confidence. Confirmed played
can initialize position priors and advance rate process uncertainty without a
numeric rate update. Ordinary positive-exposure updates, season resets, team
pace, means, coefficients and the original protected-data guards remain fixed.
Because corrected histories change later state, this is explicitly a new recipe.

## Bounded comparison

Inherit the already frozen box `initial_recipe`, shrinkage 1, trained through
2022. Verify its provenance; do not refit means, priors, minutes or Kalman noise.
Calibration is all eligible competitive 2015–2022 appearances. Primary checking
is 2023; 2024 is a fixed sensitivity. Both are previously used development data.
Do not read 2025 or 2026 for this comparison. Use the existing 24-hours-before-tip
request rule and assumed eight-hour historical availability.

Before fitting, audit the full denominator and reject any competitive source
conflict, invalid numeric value or ambiguous identity. Independently reconcile
all raw appearances in the same 20 schedule-selected games per check season as
the prior pilot, ordered by `SHA256("rate-uncertainty-source-v1:" + game_id)`.
Require at least 99% included and reconciled per sampled season and no consumed
clock or independently reconstructed state failure. Known played with unknown
exposure remains a valid participation observation but no rate innovation.
Keep every omission and unknown denominator. Freeze the count-independent repair
cohort first. A failed source/state gate stops fitting; do not swap the sample.

For each market and eligible known-positive-exposure calibration appearance,
save pregame rate mean r, rate variance v, actual exposure e and count y.
Let mu=e*r, d=y-mu and U=e^2*v. Estimate exactly once:

- F_total = max(1, sum(d^2)/sum(mu)).
- F_noise = max(1, (sum(d^2)-sum(U))/sum(mu)).

Preserve raw estimates and floor activations. Compare the old capped Fano,
uncapped constant F_total, and F_noise plus U on identical repaired histories,
means and rows. Report all four markets, both check years, complete denominators,
Gaussian variance score log(V)+d^2/V, sum(d^2)/sum(V), uncertainty shares and
fixed median/90th-percentile summaries. No outcome-selected subgroup or trimming.

Also compare complete pregame count distributions using the same predicted
minute mixture. Moment-match a negative binomial per component (Poisson at
variance=mean). Constant variance is F_total*mu; the rate variant for points is
F_noise*mu+(predicted minutes*predicted pace)^2*v. Other markets stay constant.
Use predicted exposure only in forecasts; actual exposure is solely a diagnostic
or calibration quantity. Score valid played count outcomes even if actual
exposure is unknown; report those separately. Confirmed DNP is a void. No market
quotes, forecast means, role adjustments or price policy are tuned here.

Choose the rate variant only if all source/state/distribution checks pass and:

1. Neither points moment estimator requires its lower floor.
2. Aggregate U/V_rate is at least 5% in both check years.
3. Points Gaussian variance score improves by at least 0.01 against the
   uncapped constant in 2023 and is no worse in 2024.
4. Points sum(d^2)/sum(V_rate) is in [0.90,1.10] in 2023 and [0.85,1.15] in 2024.
5. Points pregame count negative log likelihood improves by at least 0.005 in
   2023 against the uncapped constant and is no worse in 2024.

If these mechanism gates fail but source/state/distribution correctness passes,
freeze the uncapped constant as an explicitly unqualified research benchmark.
This fallback is fixed in advance, not another search. If correctness fails,
stop candidate activation and identify the missing correction. Preserve negative
findings. Neither selection outcome establishes a betting edge.

Budget: one complete source audit and comparison, at most ten wall-clock minutes,
and one exact saved-input reproduction, also at most ten minutes. No grid, new
mean fit, repeated score peeking or additional empirical attempt. A software
failure must be documented before any separately approved amendment; do not
silently rerun or expand this budget. Synthetic tests are unrestricted.

## Prospective points contract

Freeze exactly the selected points candidate, its scalars and inherited recipe,
code commit, input manifest and this contract before admitting new quotes.
Only after candidate selection may through-2025 history be read as an inference
seed; it cannot influence selection or recalibrate parameters. Protected 2026
historical evaluation windows remain closed. A separate future inference index
may consume only newly observed post-freeze completed games with real source
receipt clocks. Old history/store guards remain unchanged. Record seed staleness
and unknown player state; no backdated availability or reconstructed live claim.

For each player-game, select the first genuinely observed active coherent
same-book/same-line paired points quote across FanDuel (10) and Fanatics (14).
Break exact receipt ties by lower book ID. These are first-observed available
prices, not proven openers. Record all eligible forecasts, including low-edge
ones, and retain every failed or excluded request. No bets are placed.
Admit forecasts only in the final 24 hours and at least 15 minutes before tip.
The quote must have been received no more than five minutes before generation;
provider clocks cannot lie in the future relative to their receipt.

Keep provider quote time, receipt time, forecast generation, durable seal,
scheduled tip and source availability as distinct timezone-aware clocks.
Require a completed durable batch receipt at least 15 minutes before tip. A local write alone is
not a durable seal. Save immutable quote, forecast, input and raw-source
manifests; settlements and corrections append separately and never replace a
prediction. Dedupe player-game admissions. Confirmed DNP refunds, known played
with zero displayed minutes retains counts, unknown participation is unresolved,
and an exact integer-line result is a push.

Endpoint: first 3,000 eligible player-game forecasts, finishing the crossing
America/New_York calendar date, or 2027-10-01T00:00:00Z, whichever comes first.
Success additionally requires at least 60 ET dates and 2,000 settled nonpush
forecasts; otherwise evidence is inadequate. Endpoint counts forecasts, not
winners or conveniently resolved rows. Do not substitute another market.
Wait 14 days after the endpoint for settlement. More than 1% unresolved admitted
forecasts fails the settlement-quality gate; never discard unresolved rows from
that denominator. Record the final settlement version used for evaluation.

Primary metric is paired conditional-nonpush log-loss difference against
proportional no-vig paired price. Calculate a fixed 10,000-replicate ET-date
cluster bootstrap only at endpoint. The fixed economic policy selects a side
only at >=5% quoted net expected value, flat one unit, using quoted decimal
payout including vig. Report a separate 1% per nonvoid-unit cost/slippage stress
and an always-under control on the full admitted population. No stake tuning.

Qualification requires the upper 95% confidence bound for loss difference below
zero and the lower 95% confidence bound for stressed ROI above zero, plus the
sample, source, timing and independent reproduction gates. Partial collections
may report counts and source health but do not repeatedly evaluate performance.
Qualification is evidence under this contract, not proof of future profits.

## Delivery and operation

Test participation, missing exposure, independent state reconstruction, protected
year exclusion, future-outcome invariance, complete distribution mass, price
pairing, clock failures, duplicate admission, immutable sealing, settlement
revisions, pushes/DNP and endpoint logic. Preserve exact reproducible evidence
and all interrupted receipts. Keep fitted artifacts and research outcomes private
under the existing disclosure decision; public code and registration can be
reviewed independently. Do not publish private artifacts through another path.

Unattended activation requires a verified durable model/ledger restore path,
source capture, conflict-safe append, before-tip seal, and recovery after a fresh
workspace. A local smoke test is not proof of that capability. Do not repurpose
the existing operations watch or Claude routine. If activation needs new access
or disclosure permission, finish the concrete candidate, evidence and reviewable
runner first, then state the exact remaining requirement. Do not claim that
prospective recording is running until a real durable run verifies it.
