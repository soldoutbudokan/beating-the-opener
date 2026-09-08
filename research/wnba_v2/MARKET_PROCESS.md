# From a working collector to a market comparison

The decision is whether one frozen points model and one fixed price-selection
rule improve on available sportsbook prices after costs. A collector that runs,
a mathematically valid model and a profitable betting process are separate
claims. This continuation of PR #6 improves correctness and observability; it
does not fit another candidate or report an interim winner.

## Correctness repairs

Two synthetic counterexamples identify problems in the prospective implementation:

- A correction to an older game can arrive after a newer game. Availability
  determines which revision is known at a forecast cutoff; game time determines
  the order in which those selected observations update player and team state.
  Processing the corrected older game last can move the player's last game or
  season backward and distort the minutes and production estimates.
- An incomplete box score still has constraints. Known player points cannot
  exceed the final team score, and known minutes cannot exceed the team's
  available minutes plus the existing display-rounding allowance. Missing
  players prevent a complete reconciliation; they do not make impossible known
  totals valid.

The repairs are confined to the future WNBA adapter. Earlier historical study
code, frozen recipes, fitting coefficients, selection gates and recorded results
remain unchanged. Tests include late corrections, earlier-forecast invariance,
cross-season ordering, impossible partial totals and valid incomplete boxes.

**These repairs change inference or source-admission semantics.** They cannot be
inserted into an existing frozen bundle under the storage-only repack amendment.
The active runtime remains pinned to its original implementation. Adopting the
repairs requires a separately recorded correction and a new honest freeze with
verified storage before any new forecasts are admitted. Preserve the old study
and evidence; do not combine studies or backdate their clocks. No historical
comparison, refit, new freeze or deployment is performed by this change.

## Read-only health check

`monitor.py` reads a clean local checkout of the authorized private evidence
repository. It checks committed bytes, the frozen bundle hash, study identity,
batch receipt ancestry and exact sealed bytes before reporting collection and
settlement progress. It makes no source request, Git fetch, push, forecast,
evaluation or model-selection decision.

Run from the code checkout after independently fetching the private evidence
repository with full Git history:

```sh
python -m research.wnba_v2.monitor \
  --data-repo /ABSOLUTE/PRIVATE_EVIDENCE_CHECKOUT \
  --repository OWNER/PRIVATE_EVIDENCE_REPOSITORY
```

Keep the output in that private environment. A health report distinguishes
recent successful empty scans from stale or failed runs, missing seals and
incomplete run receipts. It also reports the registered sample and settlement
progress and whether the endpoint evaluation is due. The default three-hour
freshness limit is an operational alert threshold for an hourly collector,
not a statistical rule. Local Git verification proves consistency with the
checked-out commit; the caller must fetch a current snapshot. A deliberately
old checkout cannot prove the remote collector is current.

Stored probability distributions may be revalidated against their saved inputs;
the monitor does not calculate forecast accuracy, returns or confidence
intervals. Healthy collection never qualifies a model. A scheduled read-only
workflow belongs in the private evidence repository, separately from the
existing collector and the earlier public operations watch. A branch run tests
the workflow; its hourly schedule becomes active only on the default branch.

## Decision sequence

| Stage | Evidence required | Next action |
| --- | --- | --- |
| Source and runtime health | Recent verified receipts, source failures retained, exact identities and valid clocks | Repair a demonstrated operational failure; distinguish an empty schedule from lost coverage |
| Forecast collection | Frozen model, coherent first-observed price pairs, all eligible player-games, durable storage before tip | Keep the fixed rule and preserve exclusions; do not optimize on interim outcomes |
| Outcome collection | Separate revisions, known participation, complete settlement denominator | Resolve missing evidence without deleting inconvenient forecasts |
| Fixed endpoint | 3,000 forecasts, completing the crossing ET date, or the registered calendar cap; then 14 days | Run the registered evaluation once and reproduce its saved inputs independently |
| Qualification | At least 2,000 settled nonpush forecasts on 60 ET dates; at most 1% unresolved; both registered statistical and economic gates | Make the registered stop, insufficient-evidence or qualification-review decision |
| Execution evidence | Separately registered prospective price availability, latency and accepted fills | Determine whether an observed forecast advantage can be captured in practice |

The forecast gate requires the upper 95% confidence bound for paired log-loss
difference against the no-vig quoted price below zero. The economic gate requires
the lower 95% bound for ROI after the additional 1% cost stress above zero, using
the fixed 5% expected-value threshold and flat one-unit stakes. Both use the
registered ET-date bootstrap and include the always-under control in reporting.
No threshold or stake selection follows from interim results.

Hourly observations establish first-observed available prices, not proven
opening prices. Shadow returns cannot establish fills or real profits. A later
execution study must fix those measurements before collecting them; this PR
does not authorize wagers or reinterpret the current study as a live test.

Private deployment observations and fitted evidence remain in their authorized
repository. Public review contains generic code, synthetic checks and these
operating rules.
