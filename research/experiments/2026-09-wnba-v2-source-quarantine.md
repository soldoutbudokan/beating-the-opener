# Source quarantine clarification before any variance fit

The corrected participation-v2 run stopped at source validation. Its exact
saved-input reproduction returned the same stop. No scalar, count comparison
or candidate was fitted or selected. Keep both completed stopped runs and the
earlier dependency interruption unchanged.

The independent validator built a unique-player lookup from the entire raw
archive, including unavailable pre-calibration records that the source adapter
had already excluded. A duplicate among those excluded records prevented
validation of the admissible histories. Missing legacy team exposure is also
recorded in the complete source audit. These are source-population issues;
there is no new information about model residuals or comparative performance.

This clarification separates explicitly quarantined unavailable history from
admitted observations. It does not repair missing values, accept an ambiguous
player, change the model history, or relax forecast/outcome timing:

1. Keep every raw row and omission reason. The adapter's existing omissions and
   known noncompetitive exclusions stay unchanged. Do not add an observation
   from an unavailable or conflicting source.
2. A duplicate raw player key may remain in the quarantine only when every row
   with that key is omitted, the rows precede the 2015 calibration population,
   and no matching key is consumed or graded. Otherwise fail the source gate.
   Do not silently choose one duplicate or collapse conflicting values.
3. All competitive 2015–2024 invalid/conflicting player and team rows remain hard
   failures. The same requested-source denominators, 40-game deterministic
   sample and at-least-99% reconciliation requirement remain in force. Known
   noncompetitive rows remain explicit exclusions, including after 2015.
4. Independently reconcile every admitted player and team observation against
   its unique raw source, including counts, participation, minutes, possession
   exposure, duration, opponent, roster count and clocks. A quarantined source
   key cannot enter any forecast state or grade. Preserve the independent
   sampled state reconstruction and all original timing/unit checks.
5. Report quarantine totals by season/cause alongside the admitted population.
   Describe missing older history as a limitation. Passing means the actual
   state inputs reconcile under the assumed historical availability clock;
   it does not mean the raw archive is complete.

Only validation bookkeeping changes. Source normalization, accepted observation
bytes, inherited parameters, state evolution, calibration/check seasons, both
scalar estimators, all model-selection gates, fallback and prospective rules
remain fixed. Synthetic checks and independent review precede the next run.

Remain inside the original compute allowance. Conservatively charge one second
for the dependency interruption and 33 seconds for the completed source-stop
run, leaving a maximum 566 seconds for one continued primary execution in a
new exclusive directory. Charge 32 seconds for its exact reproduction, leaving
568 seconds for one continued exact reproduction. Preserve all previous receipts.
No further retry, fit or threshold change is implied by this clarification.
