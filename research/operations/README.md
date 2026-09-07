# Market research operations

The owner requested a model and operating process that together can outperform
market prices with almost no human intervention. This layer automates source
capture, persistence, health checks and notifications. The model must separately
earn its performance claim; successful collection does not qualify a forecast.

## What runs

`Market data collection` runs hourly at minute 17 UTC, on an explicit manual
dispatch, and after operations-code changes reach main. It runs on a GitHub
hosted Linux runner, with no dependency on the owner's Mac. Public repositories'
scheduled workflows can be delayed or disabled after inactivity, so a separate
notification watch checks for missed collection.

The collector records BettingPros WNBA events and eight core/combination markets,
plus active/recent Polymarket cricket metadata and incremental price history.
It reports coherent active same-book/same-line over/under pairs separately for
FanDuel and Fanatics. A valid empty schedule differs from a failed request or
missing expected prices. Resolved-market backfill probes run at bootstrap and
at the 03:17 UTC daily capture, with a bounded deterministic sample.

Raw responses, actual request and response clocks, requested task coverage and
failure evidence live in immutable `data/operations/runs/<run-id>/` directories.
Source failures preserve what was received and prevent unsafe cursor advance.
Provider data outside requested time ranges remain recorded and explicitly
flagged; downstream evaluators must enforce their own information cutoffs.
Raw capture counts do not determine a protected experiment's eligible sample.

Only derived `health.json`, `state.json` and `status.json` summarize the latest
run. The publisher verifies immutable run files, restricts every write to the
operations-data paths, preserves unrelated repository files, and retries ordinary
fast-forward pushes when main advances. It never force-pushes or shrinks the old
BettingPros, ESPN or Polymarket archives. Fresh source records are persisted on
main rather than relying on expiring workflow artifacts as the only copy.

## Operating status and notifications

`control.py` keeps data health separate from model status. Missing, failed,
incomplete, stale or future-dated source records produce incidents. Repeated
incidents retain their identity; recovery produces a separate event. Recent
events persist for 48 hours so an independent hourly watcher can observe them.
Required sources become stale after three hours under the initial policy.

The model registry starts empty. A model can be registered, collecting,
evaluation_due, stopped or validated. Validation requires explicit pinned
independent prospective evidence, its registration/freeze clocks, reproduction
and statistical/economic checks. The controller checks that recorded attestation;
it does not independently establish the truth of its evidence or reproduce the
underlying model. Tests and historical development scores cannot qualify a model.
This version reports operational incidents and research readiness, not bet picks.

An independent [ChatGPT notification watch](NOTIFICATIONS.md) reads the published status and checks
its age even if the GitHub workflow never starts. It reports new failures,
recoveries and research-state changes. It does not change model parameters,
routine states, betting rules, protected endpoints or the evaluation record.
Notification activation and the first hosted run must be verified after the
workflow is merged; committed YAML alone is not evidence that it ran.

## Local commands

Install the pinned dependencies from `research/engine/requirements.txt`, then:

```sh
python -m research.operations.collection --repo . --output data/operations --run-id NEW-ID
python -m research.operations.control --health data/operations/health.json --policy research/operations/policy.json --output NEW-STATUS.json --decisions NEW-DECISIONS.jsonl
python -m research.operations.publish --repo . --output data/operations --run-id NEW-ID --dry-run
python -m unittest discover -s tests -q
```

The workflow places control outputs under the current run's `control/` directory
and binds the latest status to that immutable copy. The collector prefers a
`BETTINGPROS_API_KEY` environment variable; otherwise it can read the key already
committed in the legacy source without executing that source. It never stores
or logs request authentication headers. No brokerage or betting credentials are
needed or used.

## Current work and limits

PR #4 was reviewed and merged at
`784b398233a2254a23ecc34df74498074d334e79`. The [follow-up registration](../experiments/2026-09-autonomous-process-diagnosis.md)
defines the separate descriptive diagnosis of saved forecasts. Original recipes,
locks, completed results and private evidence remain intact. This operating layer
does not rerun a primary experiment or activate a live model.

GitHub documents [scheduled-run limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and [automatic disabling after inactivity](https://docs.github.com/en/actions/managing-workflow-runs/disabling-and-enabling-a-workflow).
Its [artifact retention policy](https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization)
is why committed source records are the primary durable archive.
