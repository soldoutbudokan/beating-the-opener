# PLAN — first-principles pricing (rework, opened 2026-07-31)

Status: **planning only. No live betting. No routines running.**

This document replaces the anchored-on-the-opener programme described in
[README.md](README.md). It is a plan, not a result — nothing here has been
run.

## The decision

*User, 2026-07-31:* the models in this repo take the sportsbook's number as
their starting point and predict a correction to it. They should instead
build their own probability from first principles and use the market only as
the thing they are measured against and settle against.

That is the direction. The live experiment is paused and the `edge-watch`
routine is halted while the rework happens.

## What "first principles" has to mean here

Precise enough to be falsifiable, because the loose version ("don't look at
the odds") is already satisfied by `nba/`, which lost:

> A model prices the event from a generative account of how the event
> produces its outcome — possessions, minutes, usage, shot quality,
> conversion — fit to observed play, and emits a full distribution. Market
> prices enter at exactly two points: **scoring** (is the model better?) and
> **settlement** (what did we get paid?). They are never an input to a
> prediction, never a base to add a correction to, and never a feature.

Two corollaries worth writing down before anyone starts coding:

- No `mu_open`, no opener logits, no `move` target, no `gap_ew`-style
  "distance from the market" feature.
- The benchmark hardens. An anchored model beats the opener by construction
  if its correction has any skill at all. A from-scratch model has to beat
  the opener *outright*, from zero, which is a much higher bar and the one
  this repo has lost three times (`nba/`, `soccer/` v1, `wnba/` v1).

## Read this before writing any code

`nba/README.md`, in full, especially "Why it is not achievable (three upper
bounds)". It is the from-scratch experiment, run carefully, and it says:

- The market's private information is **~82% orthogonal** to all 95
  observables the project could measure.
- A look-ahead oracle — RAPM refit on the held-out seasons, ratings no causal
  system could legitimately have — still loses to the closing line by 0.0097
  (p=0.0001). Perfect player valuation buys about a quarter of the needed gap.
- 14 regime subsets searched, 0 where the model beats the line.

It also documents the failure mode this rework is most exposed to: the first
availability features leaked (box-score presence partly reads out the final
margin), which produced a **+12.3% simulated ROI at real closing prices** that
was entirely artefactual. A from-scratch model has far more surface area for
this than an anchored one, because it touches raw play data everywhere.

`AUDIT.md` is the other required reading — the 2026-07-28 audit found three
separate measurement artefacts (UTC/ET date-join contamination, mispaired
opening quotes, envelope CLV) that had inflated published numbers. Same
lesson: in this domain a good result is a bug until proven otherwise.

## Open questions to settle before Phase 0

These are genuine forks, not rhetorical. They want answers first because they
change what gets built.

1. **Which market?** The from-scratch bar is lowest where the market is
   thinnest and the generative story is simplest. Candidates, roughly in
   order of how tractable the physics is:
   - *WNBA player props* — the generative model is legible (minutes ×
     per-minute rate × opponent adjustment), data is free and already in
     `wnba/data/`, and there are hundreds of prices per slate. Its game lines
     are known dead (`props/` Phase 1G: WNBA closes know nothing its openers
     didn't), but props are a different market.
   - *Soccer 1X2* — the classical from-scratch target (Dixon-Coles, bivariate
     Poisson) with a large literature and 100k+ matches on disk already.
     Also the most heavily attacked market in this list by other people.
   - *BBL cricket* — the only project never to attempt a model. Ball-by-ball
     data (Cricsheet) is free and complete, which suits a generative
     simulation. But `cricket/`'s wedge test found the close is no better
     than the open, which cuts both ways: no correction to capture, and no
     evidence the closing price is especially sharp either.

2. **Beat what, exactly?** The opener, the close, or a book's price at bet
   time? The anchored programme measured against the close and expected to
   lose to it. A from-scratch model that only ties the close is worthless;
   one that beats the *opener* outright is worth something only if openers
   are actually gettable, which the WNBA experiment found to be the binding
   practical constraint anyway.

3. **What kills the project?** The anchored programme's great virtue was
   `props/PLAN.md`: gates written down *before* the run that used them, and
   honoured (MLB was killed on a 16-bet trade cell despite passing its
   quality gates). Whatever replaces it should keep that discipline. A
   from-scratch programme with no pre-registered kill condition will find an
   edge, because it always does.

4. **Does the `nba/` bound generalise?** If the market's edge really is
   private information — injuries, lineups, flow — then it is smallest where
   there is least of it to have. That argues for markets with public,
   slow-moving inputs, and against anything where a beat reporter knows more
   than the box score. Worth deciding explicitly rather than by drift.

## Phase 0 (proposed) — build the honest scoreboard first

Before any model: for the chosen market, assemble the evaluation harness and
establish what a *naive* from-scratch model scores against the opener and the
close. Season-level walk-forward, no market inputs, leakage guard on from day
one (`nba/src/build_dataset_v4.py` fails any feature correlating > 0.12 with
the outcome — port that idea).

If a well-built baseline is not within striking distance of the opener, that
is the answer, and it arrives in days instead of months.

## What is preserved

Nothing is deleted. The four subprojects keep their code, data and writeups:

- `wnba/data/raw/bp/` and `props/data/raw/bp/` are irreplaceable committed
  line archives (upstream deletes old seasons). Any rework still needs them —
  from-scratch models are still *scored* against market prices.
- The research READMEs record measurements, which remain what they were
  measured to be. They now carry banners marking the direction change.
- `nba/` is not superseded by this rework. It is its most relevant prior.

## Log

- **2026-07-31** Live WNBA experiment paused; `edge-watch` halted via the
  block at the top of `wnba/live/PROTOCOL.md`. Anchored programme retired.
  This plan opened. Rework scoped for the following weekend.
