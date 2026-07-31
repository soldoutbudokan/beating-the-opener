# PROGRESS — from-scratch pricing, five markets

Opened 2026-07-31. This is the living tracker for the first-principles rework
— it replaces [PLAN.md](PLAN.md) and is updated with **every push**. Status:
**research only. No live betting. No routines running.**

## The programme

*Owner direction, 2026-07-31:* build models from **public data only** — player
data, team data, or both — and see if they price these markets more
efficiently than the books' opening/closing lines. The lines are never inputs
to a model. They are only (a) the benchmark the model is scored against and
(b) the price sheet for held-out ROI simulation. Book identity no longer
matters (the line-shopping/getability question is set aside); benchmarks use
**consensus/average devigged prices**.

The falsifiable definition (carried from PLAN.md):

> A model prices the event from a generative account of how the event
> produces its outcome — possessions, minutes, usage, deliveries, goals —
> fit to observed play, and emits a full distribution. Market prices enter at
> exactly two points: **scoring** and **settlement**. They are never an input
> to a prediction, never a base to add a correction to, and never a feature.

Ban list: no `mu_open`, no opener logits, no `move` targets, no
"distance-from-market" features, no market-derived columns of any kind in a
model's input.

## Market order and status

Easiest → hardest, tackled one at a time:

| # | market | where | stage | status |
|---|--------|-------|-------|--------|
| 1 | WNBA player props | `wnba/` | C | **in progress** — A done; B passed G1+G2 |
| 2 | BBL cricket match odds | `cricket/` | — | queued |
| 3 | NHL (game lines + player props) | `props/` | — | queued |
| 4 | Soccer 1X2 | `soccer/` | — | queued |
| 5 | NBA (player props; game lines only with a new idea) | `props/`, `nba/` | — | queued |

## Common protocol (all markets)

Each market runs the same stage template. **A stage's gates are written here
and pushed before the stage that uses them runs** (the `props/PLAN.md`
discipline — honoured even when they kill a promising cell).

- **Stage A — harness.** Assemble data; build the benchmark table
  (consensus/average devigged open and close); pre-register gates. Push.
- **Stage B — baseline.** A simple generative baseline (no market inputs
  anywhere). Score vs open and close. If it is not within the pre-registered
  striking distance of the opener, record the kill and move to the next
  market. Push.
- **Stage C — full model.** Only if B's gate passes: the richer generative
  model, walk-forward, held-out slice scored once, flat-stake ROI sim at
  quoted average prices (EV > 2% / 5%) with a zero-skill placebo column.
  Push.
- **Stage D — dataset creation.** If the model stalls short of the benchmark
  and the gap points at observables the ready-made public sources don't
  carry, build the missing datasets before declaring the market a control:
  merge/scrape additional public sources (Wayback for deleted archives,
  official APIs, crowdsourced data), or supervised waves of Sonnet subagents
  for high-volume collect/extract/clean work. Any created dataset passes
  pre-registered QC gates (coverage thresholds, dual-source agreement,
  hand-audited sample) before a model may touch it, and is committed if the
  source is ephemeral.

Cross-cutting rules:

- **Leakage guard from day one**: port `nba/src/build_dataset_v4.py`'s guard
  (fail any feature correlating > 0.12 with the outcome).
- **Tripwire**: beating the *close* by > 0.001 LL at t > 3 triggers a leakage
  investigation, not a celebration. In this domain a good result is a bug
  until proven otherwise (AUDIT.md).
- **Protocol**: walk-forward (train < season S to predict S); final held-out
  slice scored exactly once; significance date-clustered.
- Live betting stays paused throughout; nothing under any `live/` directory
  is touched; no routines are re-enabled.

## Market 1 — WNBA player props (`wnba/`)

The most legible generative story (minutes × per-minute rate × opponent),
data already committed (`wnba/data/raw/bp/`), hundreds of prices per slate.

- **A**: rebuild props/panel/modelset from the committed BP archive + wehoop
  (existing `build_props.py` / `grade_props.py` / `features.py` /
  `build_modelset.py`); benchmark table of coherent consensus open/close;
  register gates below.
- **B — complete (2026-07-31), G1 and G2 PASS.** `wnba/src/fp_model.py`:
  blend(per-game EW, rate × minutes) with opponent pace/defense factors,
  fixed constants, σ(μ) refit on the pre-2025 panel, absence features
  excluded (they condition on tonight's box score — the nba/ leakage trap).
  Dev season 2025, coverage 99.8%:
  - **G1**: LL(model) − LL(open) = **+0.00804** (clustered t=2.6) ≤ +0.010 → PASS
  - **G2**: calibration **−1.89pp** ≤ 2.5pp → PASS
  - **G3**: model − close = +0.00864 (loses to the close; no tripwire)
  - Per market: reb_ast already beats the opener outright (−0.021, n=1,163);
    worst are assists (+0.017, cal −5.6pp) and rebounds (+0.014, cal −3.9pp).
- **C**: hierarchical shrinkage, positional defense, rest/home; ROI sim.
  Report raw and shade-aware benchmarks (the market's ~2pp over-shade,
  AUDIT N1, is free edge for a calibrated from-scratch model).
- **D candidates**: historical availability/starting-lineup data (Wayback),
  play-by-play-derived on/off and matchup data via wehoop.

**Stage A complete (2026-07-31).** Pipeline rebuilt from the committed BP
archive + wehoop (31,099 graded props → modelset 29,595 rows). Benchmark
(`src/fp_benchmark.py`; population = matched, non-void, coherent open, no
push; over = actual > open_line):

| split | n | LL(open) | LL(close, same line) | open−close (clustered t) | over rate | implied P(over) |
|---|---|---|---|---|---|---|
| 2025 (dev) | 13,367 | 0.68786 | 0.68773 (n=10,803) | +0.00113 (t=2.1) | 0.467 | 0.488 |
| 2026 (held-out) | 12,339 | 0.68689 | 0.68679 (n=8,255) | +0.00282 (t=4.7) | 0.475 | 0.488 |

The ~2pp over-shade reproduces on the rebuilt data.

**Gates — registered 2026-07-31, before any fp model code was written.**
Model inputs: panel feature columns only (strictly prior-game, shift-then-ewm).
The quoted line enters only as the threshold the model's distribution is
evaluated at (scoring), never as a feature. Distribution parameters fit on
box-score history only.

*Stage B, evaluated on dev (2025) only:*
- **G1 (striking distance)**: LL(model) − LL(open) ≤ **+0.010** on the dev
  eval population → proceed to Stage C. Fails after at most two feature
  iterations → record WNBA-props-from-scratch as a control, move to Market 2.
- **G2 (calibration)**: |mean P(over) − realized over rate| ≤ **2.5pp** on
  dev, any calibration fitted from pre-2025 play data only.
- **G3 (tripwire)**: model beats the same-line close by > 0.001 LL at
  clustered t > 3 → halt and investigate leakage before proceeding.

*Stage C, held-out (2026), scored once:*
- **Primary**: LL(model) ≤ LL(open) with date-clustered t ≥ 2 → the opener
  is beaten outright, from scratch.
- **ROI**: flat $1 at consensus open prices, EV > 2% and > 5% tiers,
  date-clustered t reported (positive at t ≥ 2 = tradeable signal; t < 1 =
  noise, reported as such). Also scored vs the same-line close and vs the
  shade-aware yardstick. No live-betting implication either way.

## Market 2 — BBL cricket (`cricket/`)

Ball-by-ball Cricsheet data suits a generative simulation; the committed asb
xlsx provides the odds benchmark (297 matches with open+close 2018+, 478 with
odds). Pipeline pattern from the owner's TheTilt template (Cricsheet download
→ parse deliveries → features → win-prob model).

- **A**: ingest Cricsheet BBL; per-match panel; benchmark tables; gates sized
  for the small n (power-limited — plausible control #3).
- **B**: pre-match win prob from batting/bowling ratings + venue effects,
  Elo floor.
- **D candidates**: pool player ratings across all T20 leagues (Cricsheet has
  them); grow the odds benchmark by scraping BBL odds history (OddsPortal
  via Wayback) — benchmark n is the binding constraint here, so D may come
  before deeper modelling.

**Gates**: *to be registered at Stage A.*

## Market 3 — NHL (`props/`)

Known problem: betting data exists only for the archived 2025-26 season
(12.5k prop files + game lines). So: train on past seasons' public data, test
against the archived season.

- **A**: fetch multi-season historical game/player data via NHL api-web
  (extend `fetch_nhl.py` / `fetch_nhl_finals.py`); benchmark from the BP
  archive; gates.
- **B/C**: (a) game model — Poisson goals, team strength, goalie effects
  (the puck line was Phase 1G's near-miss); (b) player props (SOG / points /
  assists) via the Market-1 architecture through `props/src/dist_utils.py`.
- **D candidates**: historical NHL odds beyond 2025-26 (public odds archives,
  Wayback) — likely mandatory here, not a fallback; shift/TOI data for a
  real ice-time model.

**Gates**: *to be registered at Stage A.*

## Market 4 — Soccer 1X2 (`soccer/`)

Classical from-scratch target. Goals + shots for ~20 leagues (incl. lower
divisions) are already on disk (`soccer/data/raw/`, 396 CSVs) with avg-book
early/close prices in the same files — 9 test seasons.

- **A/B**: Dixon-Coles / bivariate Poisson attack–defence model, never seeing
  odds; Elo/EW machinery from `features.py` as covariates.
- **D candidates**: Football Manager crowdsourced player/club attributes as a
  lower-division prior (a starting point, not a be-all-end-all), understat
  xG, Transfermarkt squad values; Sonnet waves to collect/normalize
  lower-division sources football-data doesn't carry.

**Gates**: *to be registered at Stage A.*

## Market 5 — NBA (`props/` + `nba/`)

- Game lines: `nba/` already ran this experiment and produced a *bounded*
  negative (line's private info ~82% orthogonal to 95 observables; look-ahead
  oracle still loses). Not re-fought without a genuinely new input.
- Real target: **NBA player props** — committed 17k-file 2025-26 archive +
  hoopR outcomes; from-scratch player model ported from Market 1. Untested
  territory.
- **D candidates**: the input `nba/` proved was missing and unretrievable
  from ESPN — **point-in-time injury/inactive reports** — via Wayback
  captures of the official NBA injury report (a natural Sonnet-wave job:
  thousands of snapshot fetch/parse tasks, hand-audited sample as QC).

**Gates**: *to be registered at Stage A.*

## Prior art (read before modelling)

- `nba/README.md` — the from-scratch control: the three upper bounds and the
  leakage finding (+12.3% simulated ROI manufactured by box-score
  availability). The bar, quantified.
- `AUDIT.md` — the measurement-bug taxonomy (UTC/ET joins, mispaired two-way
  quotes, whole-number-line off-by-one, envelope CLV, shade drift). The new
  harness encodes these as checks, not memories.
- `props/PLAN.md` — the pre-registration model: gates before runs, honoured.
- Research READMEs (`wnba/`, `soccer/`, `nba/`, `cricket/`) — what was
  measured stands; the 2026-07-31 direction change does not rewrite it.

## Push log

- **2026-07-31** — Programme opened. PROGRESS.md created (recreated from
  PLAN.md per owner direction); PLAN.md reduced to a pointer; README/CLAUDE
  references updated. Next: Market 1 Stage A.
- **2026-07-31** — Market 1 Stage A complete: WNBA pipeline rebuilt from the
  committed archive (25,706-prop eval population), benchmark table computed
  (`wnba/src/fp_benchmark.py`), Stage B/C gates registered above **before any
  model code**. Next: Stage B baseline (`wnba/src/fp_model.py`).
- **2026-07-31** — Market 1 Stage B complete: from-scratch baseline within
  striking distance of the opener on dev (+0.00804, G1 PASS; calibration
  −1.89pp, G2 PASS; loses to the close, no tripwire). Proceeding to Stage C
  (shrinkage, per-stat opponent adjustment, home/rest; then the one held-out
  run + ROI sim). The 2026 season remains unscored.
