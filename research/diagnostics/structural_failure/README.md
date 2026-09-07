# Describe the two completed structural failures

`research.diagnostics.structural_failure` reads the original saved forecast
bundles. It verifies their completed receipts, bound artifact and implementation
hashes, recipe/lock identities, fixed population and original score totals.
It does not refit a model, reprice a forecast or rerun the earlier bootstrap.
The full distribution/source reproduction already accompanies each original run.

Run from the repository root with both original completed run directories:

```sh
python -m research.diagnostics.structural_failure \
  --run /path/to/structural-evaluation-1-final \
  --run /path/to/structural-evaluation-2 \
  --output research/work/structural-failure-diagnosis \
  --registration research/experiments/2026-09-autonomous-process-diagnosis.md \
  --registration-commit 4dce14fd6449a40bbe0787f467de5c57dbdb2191
```

The registration commit must be locally retrievable and match the supplied
registration file. The output directory must not exist. The process refuses
2026 rows, unexpected markets, changed quotes/grades or a run exceeding its
ten-minute computation budget. Reproduce into a different new directory using
the same command and compare `results.json`, `note.md` and `receipt.json` bytes.
The registration permits one primary execution and one exact reproduction.

## Read the results

- **Quote loss:** preserves the original weighting of every quoted prop.
  Pushes and nonparticipants remain counted but do not enter binary loss.
  Positive model-minus-opener loss is worse. Each market/bucket contribution is
  its summed excess loss divided by the entire settled population, so the
  signed contributions add back to the original overall result.
- **Minutes:** uses the earliest available quote, then quote ID, for each
  player-game. Participation is separate. Conditional minutes errors include
  only players who played, so their MAE differs from the original unconditional
  minutes MAE.
- **Counts:** uses the earliest quote for each player-game-market, selected
  before splitting into the fixed predicted-count-mean buckets. Repeated books
  or lines cannot multiply an outcome across count cells. Count diagnostics
  include pushes and exclude nonparticipants.
- **Spread:** predicted count variance equals average within-minute count
  variance plus variance across the existing minutes mixture. Squared residuals
  divided by predicted variance describe disagreement between error and spread.
  Bias also raises that ratio; it does not establish that wider distributions
  would improve probability scores.
- **Mean-error identity:** the saved constant predicted per-minute rate gives
  `count error = rate × minutes error + (rate × actual minutes − actual count)`.
  The two terms may offset each other. Their mean squares require the reported
  cross term to sum to total mean squared error. This identity cannot isolate
  a causal minutes problem or evaluate a model given perfect minutes.

All four markets and six original mean buckets appear, including empty cells
with null estimates. No new cutpoints, confidence intervals, betting thresholds
or model ranking are introduced. Changing only DNP probability leaves the
conditional-on-playing prop probability unchanged. The stopped experiment
remains stopped; any next candidate requires a separate registered study.

Private results and source hashes are not automatically published by this tool.
