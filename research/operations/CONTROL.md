# Operating status and notifications

The controller answers two separate questions: did collection work, and is a
prospectively qualified model available for shadow review? Healthy data alone
does not establish a useful forecast. The initial model registry is empty.

`control.py` reads a saved collector report and an explicit policy. It makes no
network requests, fits no model, opens no protected outcomes and places no order.
Its output always records `mode: research-shadow` and
`actionable_bet_notifications: false`.

```sh
python -m research.operations.control \
  --health data/operations/health.json \
  --policy research/operations/policy.json \
  --previous /tmp/previous-operations-status.json \
  --output data/operations/runs/RUN_ID/control/status.json \
  --decisions data/operations/runs/RUN_ID/control/decisions.jsonl
```

Omit `--previous` on the first run. Both output paths must be new. The publisher
can then copy the new status to `data/operations/status.json`; that latest
pointer is separate from the immutable run record. Exit code 2 means a degraded
operating status was recorded. A workflow must preserve that output before
reporting its failure. An absent or unreadable input produces a visible blocked
status, rather than silently falling back to success.

## Data contract

Input schema: `collection-health-v1`. Required run fields are `run_id`,
`started_at`, `completed_at`, `status` and `sources`. Each policy-required source
needs `status`, `checked_at` and `received_at`. All clocks require explicit
timezones. The policy supplies the maximum response age and permitted clock
skew; the controller does not trust a source's precomputed age. A failed current
request stays failed even when an older successful response exists.
Source receipt and check times must lie inside the recorded run, in order:
start, receipt, check, completion. A stale completed run remains stale even if
nested source fields contain newer timestamps. The run completion must satisfy
every required source's maximum age.

Source states `OK` and `NO_EVENTS` describe successful collection. `NO_EVENTS`
means the collector validated an empty eligible event set; it provides no quote.
`DEGRADED`, `FAILED`, missing sources, stale responses, unsupported schemas and
invalid clocks block eligibility.

For each reported market, `offers` and `any_book_coherent_pairs` count distinct
offers. Each book reports `coherent_pairs`, split into disjoint `fresh_pairs`,
`stale_pairs` and `missing_quote_clock_pairs`. Counts must reconcile. Offers with
no coherent two-sided pair, or paired offers with no fresh book, create an
incident. One unavailable bookmaker does not erase fresh coverage at another.
The status names books with some fresh pairs; it does **not** assert that every
offer from those books is executable. A future signal evaluator must still
validate its exact event, player, market, line, book, price and timestamp.

Use `required_markets` in a source's policy to ensure missing market reports
cannot disappear from the denominator. Reports must retain these market keys
with zero counts on a valid empty slate.

## Model eligibility

Policy schema: `operations-policy-v1`. It contains `policy_id`,
`required_sources`, `max_clock_skew_seconds` and `models`. A model entry binds a
stable `model_id`, exact `recipe_sha256`, its required sources and one state:

| State | Meaning in this controller |
| --- | --- |
| `registered` | A study is recorded; no qualification is inferred. |
| `collecting` | Its prospective observations are being collected. |
| `evaluation_due` | Its registered endpoint is recorded as reached; emit one evaluation reminder. |
| `stopped` | The study stopped or failed; no validated signal is allowed. |
| `validated` | Check a recorded qualification attestation and current source health. |

Lifecycle states are explicit records maintained by the registered study's
own process. The controller does not infer an endpoint from raw prices, market
closures, repeated pick-sheet observations or an old development score.

A `validated` entry needs an `operations-qualification-v1` attestation containing:

- Matching model and recipe identities; SHA-256 identities for the registration
  and completed receipt; an HTTPS link to the receipt at a full GitHub commit.
- `evidence_kind: independent-prospective`, with registration and recipe freeze
  before evaluation, and completion before the recorded decision.
- `independent_reproduction: PASS`, `decision: VALIDATE`, and explicit true checks
  for every registered gate, verified timing, accounted costs, unused evaluation
  data, and frozen forecast and execution rules.
- A registered uncertainty method and a net-return interval whose lower bound
  is above zero after costs. A point estimate alone does not qualify.

This is a deliberately strict attestation contract, **not a new research gate
retroactively applied to old experiments**. It describes what the operating
process will require before labeling a future model qualified. Every study
must still pass its own original requirements and respect protected data.

The controller validates this recorded attestation's structure and consistency.
It does not download the linked receipt, recompute performance, prove that the
period was untouched or certify the attestation's truth. Adding a registry entry
therefore requires independently checked evidence through the normal research
review process. Software tests and reused development results cannot supply that
evidence. The tests use a visibly synthetic attestation, never a real qualified
model. This version creates eligibility/status notifications; a future frozen
signal adapter is still needed before generating individual shadow opportunities.

## Alert behavior

Output schema: `operations-status-v1`. `data_status` is `DATA_HEALTHY` or
`DATA_DEGRADED`; `model_status` is independently `MODEL_READY` or
`MODEL_NOT_READY`. No registered model is a normal model state, not a source
outage. Any active operating incident blocks readiness.

Each issue has a stable `incident_id` derived from its cause and subject. A new
issue emits `INCIDENT_OPENED`; an unchanged issue retains its first-seen time
and emits nothing; clearing it emits `INCIDENT_RECOVERED`. Changes in run ID,
request age or repeated failed attempts do not create a new incident. Healthy
runs with an empty model registry produce no event.

Model readiness and withdrawal emit their own transitions. A transition into
`evaluation_due` emits a reminder, once per unchanged model recipe and state.
Every new event has a stable saved `event_id`, a `run_id`, a timestamp and a plain
language summary. `recent_events` retains the last 48 hours, and active incidents
remain visible regardless of age. A separate notification watcher should
remember delivered event IDs and check `generated_at` itself: a collector that
never starts cannot write its own failure report.

`decisions.jsonl` contains only the new transition events for that immutable run.
It can be empty. It must not be rewritten on a retry. Invalid prior status is
reported as an incident and blocks readiness; corrupt history is not treated
as an empty healthy history.
