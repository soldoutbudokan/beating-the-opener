# Experiment: can this programme run without changing its evidence?

Status: **registered before Phase 0 measurements**. Programme phase 0,
2026-09-07. No model fitting or development scoring is authorized by this plan.

**Decision this would change:** start the structural WNBA development experiment
only with explicit source-time, identity and protected-window boundaries. Where
an old prospective recipe cannot be recovered, preserve the window and name the
missing artifact rather than silently score a repaired or refitted model.

**Existing evidence:** issue #1, PR #2 and its final review, and the workflow
merged in PR #3. PR #2's quoted-population distribution was too dispersed; its
null play-by-play comparison does not test a structural minutes mechanism.
2025 has repeatedly informed development. It is not an independent test.

**Smallest comparison:** an audit of six validity requirements across the live
WNBA path, the frozen PR #2 adapter and cricket. No candidate model. Human news
overrides are evaluated only if the relevant protected arms have been scored
as registered; missing outcomes never imply zero minutes or DNP.

**Source pilot before analysis:** enumerate the committed availability, news,
override, price and recipe assets at main `d2b5e685c`; compare the PR #2 code at
`64acad8248ef6536ef7c655d733293f6763263cc`. Preserve every raw file. Inspect source
schemas before reading outcome values. Fetch only missing data needed for a
registered count or exact replay, into isolated scratch paths; record failures.

**Development and protected periods:** the props arms begin 2026-08-01 and stop
at 3,000 standard eligible non-push props or season end, whichever first. Keep
the 2026-08-01..03 ASG amendment segment separate from 2026-08-04 onward. The
game arm begins 2026-08-01 and ends at season end including playoffs, not merely
the regular-season finale. Cricket ends at 300 registered markets or
2027-06-30. Resolve its arm-one registration/recipe history explicitly. Use
resolution time, not match date, for the registered cricket population.

For each arm, report both an exact qualified n when reconstructible and any
count-only upper bound separately. Neither an offer count nor a live bet count
is the registered n. Do not compute a forecast score merely to discover n.
If endpoint/recipe/data is unresolved, label scoring BLOCKED and release no
protected outcomes. A window becomes development data only after its complete
registered one-time result is durably recorded. Overlapping windows remain
protected until every relevant arm releases them.

**Human baseline, if unlocked:** include each append-only forecast entry, not
just the final update. Require a unique event/player match and forecast time
strictly before tip. Report all requested, excluded, unresolved and matched
counts; out-playing fraction; MAE and signed error (estimate minus actual) for
in/questionable; closed-interval coverage of explicit questionable ranges.
No inferred ranges. Require at least 30 resolved entries and 10 player-games
per reported status; smaller cells receive counts only. Retain superseded
entries and report a latest-before-tip sensitivity separately. Unlocked
August 2026 results are exploratory because live feedback already exists.

**Useful improvement / gates:**

1. AUDIT: all six requirements and all three implementations have source-backed
   status and a concrete research repair / owner-only live proposal.
2. CLOCKS: each consumed timestamp has an explicit parser and assertion tests;
   UTC and Eastern schedule representations must name the same instant;
   date-only values never silently become publication times.
3. WINDOWS: each arm has its original endpoint, recipe references, count status
   and release rule. Blocked arms remain sealed. Score a due arm exactly once
   only with its recoverable registered recipe and required artifacts.
4. NEWS: enumerate the archive and eligible human-baseline denominator; zero
   unlocked rows is a legitimate wait decision, not a failed prediction result.
5. RECEIPT: generated review validates result, evidence and implementation
   hashes; an independent rerun reproduces count evidence and refuses overwrite.

**Budget and stop rule:** one Phase 0 PR; at most two audit/reconstruction passes
per gate; at most 90 minutes of data reconstruction and no tuning. A missing
frozen calibration, source snapshot, original prediction segment or ambiguous
endpoint is a recorded blocker. Do not pay for Phase 1 fitting until its own
registration and shared invariance harness are published. Do not build news
extraction until the structural calibration and historical source pilot pass.

**Before measurement:** commit and push this plan and the `process-audit-v1`
review adapter; open the Phase 0 PR before result files exist. Synthetic tests
are allowed before registration; real source inventory follows the push.

**Publication:** `python tools/research.py review --adapter process-audit-v1
--results research/audits/2026-09-process-audit/results.json --receipt
research/audits/2026-09-process-audit/receipt.json --evidence-kind
reused-development --output research/audits/2026-09-process-audit/decision.md`.
Independent reproduction command and source manifest accompany the results.
Main report: `research/audits/2026-09-process-audit.md`; a plain-language note,
compact evidence and PROGRESS entry complete the PR. Nothing under `wnba/live/`
or in routine scripts is changed. No routine is enabled/disabled.

## Complete after the registered audit

**What happened:** the audit inventoried 242 availability snapshots and 554
overrides. Of 221 uniquely timed dated entries, 26 were at/after tip. A count-only
ESPN proxy exceeds the props endpoint, but the exact wehoop count, frozen
calibration and original opener-arm forecast artifacts are not recoverable from
this checkout. No protected arm was scored and no news accuracy was estimated.
The two audit passes changed only the detail of saved timing evidence, not counts.

**Decision:** collect the missing frozen inputs and pinned wehoop schedules.
The [audit](../audits/2026-09-process-audit.md) records all five endpoints,
the triage, prior 2025 uses, owner-only archive proposal and next conditions.
Phase 1 fitting and later phases have not started. There is no new model claim.

**Verification:** 26 offline checks and independent source recount pass; 1,302
source-file hashes verified. Review command above generates the decision brief.
Pre-results registration: `5bfd24d0f669eba88b793d4153ea4a3ebd4556be`.
