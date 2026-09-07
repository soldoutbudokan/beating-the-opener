# Scheduled source collection

`collection.py` collects public market data and reports coverage. It does not
load a forecasting model, produce picks, place bets, run a Claude routine, or
write any existing live/canonical archive.

```sh
python -m research.operations.collection \
  --repo . --output research/operations/data \
  --run-id YOUR_UNIQUE_RUN_ID --probe-resolved
```

Omit `--probe-resolved` for ordinary hourly collection. Run the probes daily or
manually when checking retention. `--source bettingpros` and
`--source polymarket` select one source. Python's standard HTTP client handles
requests; pandas and pyarrow are needed only for the initial read of existing
Polymarket parquet files. `BETTINGPROS_API_KEY` can override the public client
key already committed in the existing live pipeline. The collector extracts
that constant without importing or executing the pipeline. No request headers,
credentials, response bodies or unsanitized exception messages enter job logs.

## Preserved evidence and rebuildable state

Every invocation reserves `runs/<run-id>/`. A completed ID returns the saved
report without making requests or changing files. An interrupted ID cannot be
reused. A process lock prevents concurrent writers; the operating system releases
the lock if the process dies. A new run can recover data using the last successful
cursor without deleting an interrupted run.

| Path under the output directory | Meaning |
| --- | --- |
| `runs/<id>/started.json` | Start clock, selected sources and options |
| `runs/<id>/<source>/requests.jsonl` | URL, public query, request and receipt clocks, status, validation outcome and body hash |
| `runs/<id>/<source>/responses/*.body.gz` | Exact HTTP entity bytes, including error bodies and any partial bytes received before a failed read; request metadata says whether each body is complete |
| `runs/<id>/polymarket/metadata.json.gz` | Selected metadata observed in that run |
| `runs/<id>/polymarket/prices.jsonl.gz` | Every distinct incremental/backfill price observation, including conflicting values and out-of-request-window rows; probe responses remain in their raw request records |
| `runs/<id>/polymarket/price-revisions.jsonl.gz` | Changed prices at previously observed timestamps within the retained overlap |
| `runs/<id>/polymarket/market-attempts.jsonl` | Clocked attempts used for fair scheduling, with hash-verified references in derived state |
| `runs/<id>/polymarket/checkpoints.jsonl` | Completed market intervals, metadata, retained values and pending gaps; survives later failure |
| `runs/<id>/polymarket/history-tasks.json` | Requested ranges, row counts, receipt clocks and empty/out-of-range diagnostics |
| `runs/<id>/report.json` | Immutable run health, coverage and evidence hashes |
| `state.json` | Rebuildable successful cursors, recent observed values and last-success clocks |
| `health.json` | Rebuildable latest run report |

Only `state.json` and `health.json` are replaced, atomically. A failed or degraded
source never advances its whole-source cursor or last-success clock. Verified
per-market intervals are separately checkpointed, with hashes linking to immutable
`checkpoints.jsonl` bytes. Later runs validate those bytes before using partial
progress; unresolved gaps remain pending and health remains degraded. Raw requests survive
partial collection, even when derived tables cannot be completed. Files from the
original archives are read-only bootstrap inputs, with hashes recorded in state.
The collector does not copy or rewrite their two-year history each hour. Already
closed markets with existing canonical or verified checkpoint history are skipped
by hourly history collection; daily resolved probes separately test retention.
Newly discovered closures, transitions from open to closed, and pending gaps
remain tasks regardless of the age of the closure.

Commit the immutable run directories and derived state through the scheduled
workflow's restricted persistence step. A successful local write alone does not
prove that collection is durable or that the schedule fired.

## BettingPros coverage

The collector requests future, nonclosed WNBA events for today and tomorrow
UTC, then all pages for the eight markets already used by the live pipeline:
points, rebounds, assists, threes, PRA, points/assists, points/rebounds and
rebounds/assists. Wrong event/market IDs, duplicate or missing pagination,
malformed active quotes and request failures fail the source. A valid empty
future slate reports `NO_EVENTS`; events with zero offers report `FAILED`.

Coverage counts distinct event/player/market pairs, by bookmaker. Book 10 is
FanDuel; book 14 is Fanatics, matching the committed archive mapping. A coherent
pair requires unique active-main over and under quotes from the same book at
the same line, with combined implied probabilities between 1.00 and 1.15.
Ambiguous duplicate offers and multiple active-main lines do not form a pair.
Both provider quote clocks must be within two hours, with at most 60 seconds
of future tolerance, to count as fresh. Receipt freshness is reported separately.
These clocks and rules are monitoring criteria, not a change to live pick gates.

Every complete BP report contains all eight markets, even on an empty slate.
Each book's fresh, stale and missing-clock counts partition its coherent-pair
count. Missing coverage or no fresh pairs in a requested market degrades health.
This distinguishes an absent FanDuel quote, an unavailable whole market, an old
quote and an HTTP failure. Successful collection does not make a candidate model
eligible for deployment.

## Polymarket scope and recovery

Discovery uses the documented [events keyset endpoint](https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination),
following `next_cursor` as `after_cursor`. A final page may omit the cursor, as
confirmed by the actual source smoke capture. It requests all active cricket events,
closed events in a recent 14-day end-date window, and exact metadata for previously
open markets absent from those pages. Cursor loops and page limits fail closed.
The existing repository's `is_match_winner` function is loaded unchanged and its
source hash recorded. That textual selector admits some non-winner props; this
collector does not silently redefine either protected prospective arm.

[Price history](https://docs.polymarket.com/api-reference/markets/get-prices-history)
is incremental: start from the last saved observation with a two-hour overlap,
request at most 13 days per market at the original ten-minute fidelity, and retain
all returned values. Previously open markets that closed during an extended
outage still receive terminal-history requests. A missing earlier interval is an
explicit backfill gap and prevents whole-source success. One bounded earlier
interval per market per run works through pending gaps while verified current
collection continues. An empty backfill remains pending and cannot be marked
recovered just because the request succeeded. An empty terminal interval remains
pending even if an older backfill succeeds. The last successfully requested
interval end (`collected_through`) is separate from the last returned price
timestamp, so a quiet interval does not repeatedly recreate an already captured
gap. Original and revised
values remain distinct observations. Out-of-request-window ticks are preserved,
counted and excluded from the cursor; downstream analyses must enforce their own
information cutoff.

Optional probes choose at most five resolved markets deterministically, one per
closure-age group: under 7 days, 7–30, 30–90, 90–365, and at least 365 days. Each
requests a fixed seven-day pre-closure interval. A probe succeeds only if data
inside that requested interval returns. Empty history or unconfirmed current
resolved status degrades health. This checks availability across ages, not exact
agreement with every old archive row. Closure is not verified resolution time and
never establishes a prospective endpoint. No protected arm is scored.

## Bounds and health contract

Each source has a six-minute budget, a 12-second socket timeout, at most three
attempts per request, and a 500-request cap. Streaming reads check the overall
deadline between chunks and preserve partial bodies if interrupted. Discovery
allows at most 30 pages per open/closed query and a chunk of 350 market history tasks;
BettingPros allows 20 upcoming events and ten offer pages per event/market.
Markets are ordered by their oldest verified attempt clock, with unvisited
markets first and stable ID ties. A task or request budget leaves an explicit
`history_backlog`; it never marks the source complete. Hash-verified attempt
journals survive failed runs, so later runs visit deferred markets instead of
repeating the same prefix. A per-market HTTP failure preserves its error and
allows other bounded tasks to finish, while the source remains failed.
Requests run sequentially with a short pause and bounded retries. These are well
below the published [Polymarket request limits](https://docs.polymarket.com/api-reference/rate-limits).
A source deadline may overrun by one socket-read timeout; the workflow should use
a longer outer timeout so the run report and failed raw evidence can be saved.

`collection-health-v1` reports `OK`, `DEGRADED`, or `FAILED` globally. Each source
additionally permits `NO_EVENTS`. It provides `checked_at`, `received_at`,
`last_success_at`, `last_success_age_seconds`, and `max_age_seconds=10800`.
All clocks include UTC offsets. Missing clocks remain null; a successful HTTP
connection cannot refresh a failed source's success clock. Model control must
also check pair freshness and its own registered eligibility requirements.

The process exits nonzero for `FAILED`. `DEGRADED` preserves its explicit status
for the separate health/notification controller. Neither status is silently
converted into a healthy source or usable model input.
