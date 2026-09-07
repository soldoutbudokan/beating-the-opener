# Registration: reliable collection and diagnosis of frozen forecast errors

Status: registered when this document reaches GitHub, before the diagnostic
reads individual saved outcomes. This is a descriptive follow-up, not another
candidate in the original two-attempt structural experiment.

## Decision and owner direction

On September 7, 2026, the owner requested continued work toward a model and an
operating process that together can beat the market with almost no human
intervention, using any suitable notification system. The owner also authorized
merging. This permits implementing and activating data collection and health
notifications. It does not establish an edge or authorize placing wagers.

The immediate decisions are whether collection is dependable, which specific
forecast component deserves a separately registered next study, and what
evidence an unattended research process must require before surfacing a model
as validated. The previous recipes, attempt limits, locks and results remain
unchanged. Existing protected game and cricket experiments retain their endpoints.

## Operational work

Use a scheduled GitHub runner for source capture, with bounded requests, actual
request/response clocks, raw response retention, coherent quote coverage by
book and market, immutable run directories and a health record. Separate source
absence from failed requests. Never infer protected experiment eligibility from
raw market counts or closure timestamps. Capture public market observations;
do not publish private fitted parameters or model results through this workflow.

Persist new capture files with append-only checks and ordinary fast-forward Git
updates. Preserve the existing BettingPros/ESPN/Polymarket archives. A separate
notification watch checks missed or failed collection, rather than relying on
a collector to report its own failure to start. Alerts report concrete changes
or problems, not repetitive healthy status. No live routine or model is activated.

## Frozen descriptive diagnostic

Inputs are the two already completed structural run directories, their saved
forecasts, receipts and frozen sources. Use every original quote in both 8-hour
and 24-hour scenarios. Check completed receipts, all bound artifact hashes,
implementation/recipe/lock identities and identical quote populations. No new
2026 data, fitting, forecast replacement, repricing, bootstrap selection or
altered decision threshold enters this diagnostic.

Report each recipe and timing scenario, preserving all quotes, voids and pushes:

1. Overall participation and conditional minutes accuracy, deduplicated to the
   earliest quote for each player-game.
2. The existing four markets and existing predicted-count-mean buckets:
   [0,3), [3,6), [6,10), [10,15), [15,20), [20,infinity). Retain empty cells
   with no estimated accuracy. Also report market totals and overall totals.
3. Original quote-weighted, nonpush model-minus-proportional-opener log loss,
   predicted-over minus observed-over probability, and each cell's signed
   contribution to total excess loss. The cells must sum back to saved totals.
4. Earliest-quote unique player-game-market count/minutes predictions and errors,
   predicted count variance against squared residuals, and the distribution's
   existing within-minute versus between-minute variance components.
5. The descriptive identity for count-mean error: predicted count minus actual
   count equals predicted per-minute rate times minutes error, plus predicted
   rate times actual minutes minus actual count. Report closure and the squared
   error/cross-term identity. This is algebra applied to saved predictions,
   not a counterfactual forecast or causal identification of a failing component.

Participation is reported separately: changing only the final probability of
not playing cannot repair probabilities scored conditional on playing.
All grouping choices above are fixed before this follow-up computation. These
data have already been examined; the result is post-result diagnosis and cannot
turn the old development season into an independent test or select a winner.

## Budget, verification and output

At most one full descriptive execution and one exact saved-input reproduction,
each bounded to ten wall-clock minutes. Zero new model fits or model candidates.
Synthetic tests cover duplicate handling, group accounting, distribution
variance identities, algebraic closure, altered inputs and overwrite refusal.
Save append-only compact JSON, a plain-language note and a receipt. Reproduce
substantive result bytes into a different directory. Private result artifacts
remain in the authorized private evidence location pending public disclosure
approval; publish implementation and this registration normally.

The diagnosis must end with one of: a concrete next-study hypothesis and its
needed source check; a named missing input to collect; or stop this approach.
A model change requires a new registration and a future independent evaluation
before any market-beating claim or validated-model notification.
