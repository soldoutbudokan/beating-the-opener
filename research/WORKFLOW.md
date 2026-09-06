# A faster way to learn whether a model is worth improving

The aim is to spend less work reaching a trustworthy decision. A useful result
can be “this idea did not help.” The expensive failure is finishing a large
experiment and still being unclear about what it established or what to do next.

This workflow is for **new research**. Existing registrations, protected test
periods, source archives and live operating rules remain in force. It does not
restart any routine or authorize betting. It can be used independently of
[the richer-data PR](https://github.com/soldoutbudokan/beating-the-opener/pull/2).

## What the last experiment taught us about the process

The richer WNBA study did several things well: a published plan, a comparable
baseline, historical information cutoffs, source checks, and full reporting of
an unsuccessful information test. Those practices should stay.

It also exposed avoidable work and communication problems:

| What happened | What to do differently |
| --- | --- |
| The study expanded to 124 extra predictors and four models. Several useful data fields, particularly upcoming role information, were unavailable. | Check one representative source sample and one basketball mechanism before expanding the feature set. Missing information should produce an early decision. |
| Older shot data required substantial parsing and reconciliation work; 1,128 games failed the final shot-family checks. | Check old and recent samples before committing to the whole archive. Record whether cleaning costs are justified. |
| A roughly 4,000-word report and 58 changed files made the decision harder to see. | Lead with a short decision brief. Keep the full report and evidence available behind it. |
| Probability scores, returns, overconfidence and an always-under control needed separate explanation after the run. | Specify controls in the plan and generate the same comparison table automatically, including failed models and every fixed betting rule. |
| Retrieval timestamps could invalidate preparation even when the downloaded bytes were unchanged. | For new cache designs, key reuse to the exact input content, relevant code and settings. Store retrieval time separately. Preserve the frozen old experiment's fingerprint rules. |
| Completing the requested PR could sound like delivering a better model. | State separately whether the run completed, the forecast improved, and an independent trial is justified. |

These are observed process weaknesses and proposed remedies. This PR does not
claim a measured reduction in end-to-end research time or a new improvement in
prediction accuracy.

## 1. Start with the decision and the cheapest uncertainty

Before writing a model, read the last decision and inspect the existing errors.
Ask what is actually failing: playing-time forecasts, per-minute production,
the spread of outcomes, historical timing, or the comparison with prices.
A large gap between claimed and actual profit suggests a probability problem;
it does not by itself identify its cause.

Write a short [experiment plan](EXPERIMENT_TEMPLATE.md). Name the change that a
successful result would justify, the baseline, the smallest test and a work
budget. Explain why the new information could change the forecast **before the
price being evaluated**, rather than merely describe the eventual result.

A practical first budget might be one short source investigation and one
candidate against one baseline. Choose the actual limit before starting.
If the budget runs out, record what remains unknown and the next decision.
Do not quietly convert a small question into an unrestricted model search.

## 2. Test a source sample before building around it

Choose the requested sample before inspecting its values: include different
seasons, teams and ordinary missing-data cases. Keep a row for every requested
prediction/source-family pair, including failed joins. Otherwise “99% coverage”
can simply mean that missing players were dropped from the denominator.

The offline helper checks a normalized CSV. The sample extractor is responsible
for mapping each provider's real fields and preserving missing requests.

| Required column | Meaning |
| --- | --- |
| `query_id` | Stable ID for the prediction request |
| `family` | Information being checked, such as `availability` or `shot_profile` |
| `entity_id`, `event_id` | Player/team and target game, resolved without ambiguity |
| `source_id` | The actual source record; blank when no record matched |
| `prediction_at` | Forecast cutoff with an explicit timezone |
| `available_at` | When the source record became usable, with an explicit timezone |
| `matched` | `true`/`false` or `1`/`0` |
| `value` | The requested measurement or category; missing remains blank |

```sh
python tools/research.py source-check sample.csv --expected-rows 500 --min-coverage 0.90 --time-basis observed --output research/work/source-check.json
```

The row count and coverage floor must come from the plan. The helper stops on
duplicate requests, broken identities or timestamps, records available at or
after the prediction, or insufficient usable coverage in **any** family. Missing
matches, values or availability times reduce usable coverage. A small failed
family cannot hide inside a large successful one. Exit code 2 means stop and
inspect the report; it is not a reason to keep only the favorable rows.

If availability is approximated, use `--time-basis assumed --time-note "..."`.
A passing sample then says `PASS_UNDER_ASSUMPTION`, not verified historical
availability. A source's event date, retrieval time and publication time are
different things. The tool cannot establish that a provider's clock is truthful,
that the sample is representative, or that the values reconcile with box scores.
Those checks remain part of the source investigation.

Source checking needs no game outcome and does not establish predictive value.
If the needed historical timestamps do not exist, stop the historical causal
claim. A bounded exploratory test with explicit timing assumptions, or a
separately authorized prospective collection plan, may still be worthwhile.

## 3. Run the smallest fair development comparison

Use the same population, historical cutoff and probability accounting for the
candidate and baseline. Change one thing at a time. For a new data family,
first hold the fitting method fixed. Add model flexibility only after there is
a reason and within the written budget.

Choose settings on earlier development periods. Use cheap validation to decide
whether a larger comparison is worth the cost. A small sample can eliminate a
broken join or an unusable source; it usually cannot establish a small betting
edge. Do not stop on a tiny noisy score difference that lacks the precision
needed for the stated useful improvement.

Before fitting, fix the primary forecast metric, a practically useful effect,
the paired uncertainty calculation, and any economic rules. Controls should
test the actual apparent mechanism: for example, a strategy that largely bets
unders needs an appropriate simple-under comparison. Controls chosen after
the result are exploratory and remain labeled that way.

**Timing tests replace correlation thresholds as the default leakage check for
new work.** A genuinely predictive feature can correlate strongly with its
outcome; that is not proof of leakage. Check that source records predate the
forecast, future outcomes cannot change old predictions, target-game outcomes
and final rosters cannot enter their own forecasts, and unrelated offers cannot
change a player's estimate. The old `PROGRESS.md` correlation rule is historical
context, not a sufficient test for a new experiment. Previously registered runs
and recorded measurements are not rewritten by this clarification.

## 4. Make a full comparison once, then decide

Record the finalized recipe and input versions before opening the designated
comparison. List prior uses of the test period honestly. A new branch or a new
model name does not make 2025 an unused test season. Read the existing protected
2026 registrations before choosing dates; these helpers never run their scorers.

After the full run, verify the stored predictions and selection arithmetic.
Then generate a short brief. The first adapter reads the completed JSON format
from the richer WNBA study; other studies need an explicit adapter before they
can use this command. It does not pretend all historical reports share a schema.

```sh
python tools/research.py review --results path/to/results.json --receipt path/to/evaluation_receipt.json --comparison rich_ridge_minus_box_ridge --evidence-kind reused-development --output research/work/decision.md
```

The helper checks the completed receipt's checksum, paired comparison population,
score differences and betting arithmetic. It displays every model, every fixed
betting rule and every stored timing scenario. It reads summary statistics;
**it does not regenerate forecasts or recompute confidence intervals**. Keep the
experiment's individual-prediction verifier as a separate check.

Use `--minimum-gain` only with the gain fixed in the original plan, or label it
as a retrospective sensitivity. The software cannot prove when the threshold
was chosen or whether a declared test was untouched. A missing threshold remains
missing in the brief. A confidence interval crossing zero is not a clear win,
and positive simulated profit does not overrule the forecast comparison.

Close the loop with one decision: **stop**, **collect a named missing input**, or
**prepare an independent test**. State what would justify reopening a stopped
idea. Existing live protocols and the owner's decisions govern any later live
change; this helper never adopts a model automatically.

## 5. Keep evidence complete and review small

Commit the plan, code, compact metrics, checksums and decision brief. Put detailed
plots and tables behind links. Regenerable row outputs can be bundled together
with their checksums instead of adding dozens of large text diffs.

```sh
python tools/research.py pack --root path/to/results --output research/work/evidence.tar.gz results.json evaluation_receipt.json forecasts_8h.csv.gz forecasts_24h.csv.gz
```

List the files intentionally; the tool does not sweep the repository or gather
raw data automatically. The bundle includes a `MANIFEST.json` with every file's
name, size and SHA-256. Repacking unchanged inputs gives the same bytes. Existing
outputs are never overwritten. Include the detailed bet files and checks needed
for the specific experiment; the four-file command above is only an example.

The temporary `research/work/` folder is ignored. **A local bundle is not
publication.** Before the PR is complete, commit the bundle under a named study's
evidence directory or put it at an authorized durable location and link it from
the brief. Record the recipe's commit and test it can be retrieved. Never delete,
shrink or ignore the project's existing BettingPros or ESPN archives. Preserve
new irreplaceable raw observations under the existing archive rules too.

## What I would prioritize now

1. Use the existing WNBA evidence to distinguish poor probability estimates from
   a specific minutes or opportunity failure. Any new diagnostic using an
   already-seen season is exploratory and cannot retroactively select a winner.
2. Pilot timestamped availability and expected-role information on a small,
   predefined historical sample. Establish access, meaning and coverage before
   committing to collection or another model.
3. If that pilot passes, test one role-related addition against the same simple
   baseline on earlier development data. Verified lineup reconstruction can
   follow if the expected-rotation information makes it useful.

These priorities are judgments, not demonstrated causes of the last result.
The concrete change now is a shorter, repeatable path from a question to evidence
and a decision. No new model comparison is needed to implement that process.
