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
| 1 | WNBA player props | `wnba/` | revisit | first holdout FAIL was early-season/expansion composition; **`fp-prospective-1` registered** on Aug-2026+ props (late-season dev analogue: parity, +3.8% ROI at t=1.8); data-only archiver running |
| 2 | BBL cricket match odds | `cricket/` | parked | team model ties opener (G1 PASS), G2 calibration gate FAIL; **holdout unspent**, awaiting ball-by-ball data (owner action) |
| 3 | Soccer 1X2 | `soccer/` | parked | **control** — G1 fail after both iterations (+0.0164 vs ≤+0.015); calibration excellent; **holdout unspent** |
| 4 | NHL (game lines + player props) | `props/` | — | queued — **data-blocked**: api-web unreachable in this environment; try GitHub mirrors (e.g. hockeyR-data) or owner network action |
| 5 | NBA player props | `props/` | parked | **control** — G1 fail both iterations (+0.0247); calibration fine, discrimination gap; **holdout unspent** (awaits Stage D injury data) |

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
- **C — complete (2026-07-31). Held-out verdict: FAIL.** Final model:
  Stage B core + per-stat bias/home/shrinkage/σ all fit on pre-eval-season
  play data only, and a 0.5/0.5 Normal+NegBin distribution (selected on
  pre-2025 play data with synthetic lines, never on dev — the symmetric
  Normal overstates P(over) on right-skewed counts).
  - Dev 2025: model − open **+0.00469** (t=1.8), calibration **+0.41pp** —
    within striking distance; reb_ast beat the opener (−0.024).
  - **Held-out 2026 (scored once, as registered): model − open +0.01763
    (clustered t=4.1), calibration −2.44pp → the from-scratch model does
    NOT beat the opener.** The dev bright spot (reb_ast) did not replicate
    (+0.007). ROI at consensus open: +11.1% (t=0.8) EV>2%, +13.8% (t=0.9)
    EV>5% — **t < 1 = noise per the pre-registered rule**, and a model that
    loses on LL while showing +14% ROI is the classic artefact pattern.
    Placebo: 0 bets both seasons.
  - ~~Post-hoc reading: calibration drift~~ **Corrected by the revisit
    diagnostics below — the drift hypothesis was tested and rejected.**

### Market 1 revisit (2026-07-31, owner-directed) — POST-HOC diagnostics + prospective registration

All 2026 numbers in this subsection are **post-hoc** (the registered holdout
was spent above) and are never claimable as results.

- **Drift hypothesis rejected**: weekly in-season expanding recalibration
  (`fp_model.py --expanding`) moves 2026 only +0.01763 → +0.01711 and the
  combo calibrations barely improve. The failure is not trackable bias.
- **What actually explains 2026**: (a) *expansion churn* — props on the
  three new 2026 franchises (GSV/PDX/TOR) show gap +0.036 vs +0.0135 for
  incumbent teams; (b) *season phase* — the archive's 2026 data ends
  July 30, so the spent holdout was **entirely early/mid season**, where
  EW-feature models are information-starved. Dev 2025 by phase: May–mid-Jun
  **+0.0216**, mid-Jun–Jul **+0.0040**, **Aug–Oct +0.00006 — parity with
  the opener**. Like-for-like early season, 2026 (+0.0220) ≈ 2025 (+0.0216):
  the model didn't get worse in 2026; the sample composition changed.
- v2 candidates tested on dev and NOT adopted (all hurt or were neutral:
  weekly expanding recal +0.00584 vs frozen +0.00469; opponent-3PA defense
  hurt threes; presumed-absent availability ~neutral). They remain in
  `fp_model.py` behind `--expanding`. **The registered model stays v1.**

**Prospective registration `fp-prospective-1` — registered 2026-07-31,
before any qualifying data exists:**
- Population: standard eval convention (matched, non-void, coherent
  consensus open, no push), **date ≥ 2026-08-01** — data that does not yet
  exist, accruing via the data-only archiver (owner-approved this session:
  the `edge-watch` routine now runs `scrape_bettingpros.py` + commit only —
  no picks, no notifications, no bets; PROTOCOL block rewritten).
- Model: `wnba/src/fp_model.py` v1 mode as of this commit, params frozen at
  the pre-2026 fit. No further changes count for this registration.
- **Primary (parity)**: LL(model) − LL(open) ≤ **+0.003**. **Stretch
  (superiority)**: < 0 at clustered t ≤ −2.
- **ROI**: flat $1 at consensus open, EV > 5%: ROI > 0 with clustered t
  reported (t ≥ 2 = signal); placebo column mandatory.
- **Tripwire**: beats same-line close by > 0.001 at t > 3 → leakage
  investigation.
- Evaluate **once**, when n ≥ 3,000 or the 2026 season ends, whichever
  first. Context (dev analogue, Aug–Oct 2025): gap +0.00006 (t=0.0),
  ROI EV>5% +3.82% (t=1.8), placebo 0 bets.
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

**Stage A complete (2026-07-31)** — with a **data blocker**: cricsheet.org
and web.archive.org are outside this environment's network allowlist and no
GitHub mirror carries BBL ball-by-ball, so the generative player-level model
waits on Stage D (owner action: allow cricsheet.org in the environment's
network policy, or commit `bbl_json.zip` from a local download to
`cricket/data/raw/cricsheet/`). Stage B proceeds **team-level** from the
committed xlsx (549 matches 2011–2023: results, scores, wickets, overs,
venue). Constraint registered: **the opener predates the toss** — toss and
batted-first are unknowable at open time and are banned as model inputs.

Benchmark (`src/fp_benchmark.py`; population = H/A winner + full open/close,
297 matches; forecast = devigged two-way P(home)):

| split | n | LL(open) | LL(close) | home rate | implied P(home) |
|---|---|---|---|---|---|
| dev (2018–2020 seasons) | 177 | 0.70380 | 0.70696 | 0.565 | 0.510 |
| holdout (2021–2022) | 120 | 0.64012 | 0.64888 | 0.600 | 0.508 |

Coin-flip LL = 0.69315: **on dev the opener is worse than a coin flip.**
The softest benchmark in the repo — but n is tiny, so gates are power-aware.

**Gates — registered 2026-07-31, before any fp model code.**
- **G1 (dev)**: LL(model) − LL(open) ≤ **+0.010** → Stage C; fail after at
  most two feature iterations → control #3 confirmed for from-scratch too.
- **G2 (dev)**: |mean P(home) − home rate| ≤ **5pp** (n=177; binomial noise
  alone is ±3.7pp).
- **G3 tripwire (dev)**: model beats the open by > 0.005 at t > 2 →
  leakage/artefact investigation before proceeding (note: with an opener
  this weak a legitimate win is possible; investigate, don't assume).
- **Stage C (holdout, scored once)**: report LL(model) − LL(open) with
  paired date-clustered t (n=120 — t ≤ −1 suggestive, t ≤ −2 significant);
  flat-stake ROI at the multi-book-average open for EV > 2% / 5%, with the
  devigged-open placebo column.

- **B — complete (2026-07-31): gate-stopped, holdout preserved.**
  `src/fp_model.py`: walk-forward Elo + net-run-rate ratings, blended, all
  hyperparameters tuned on the pre-odds 2011–2017 era only. Dev results:
  - **G1 PASS**: model − open **+0.00205** (t=0.3) — a from-scratch
    team-level model built in an afternoon is statistically
    indistinguishable from the BBL opener (consistent with Stage A's
    finding that the dev-era opener is worse than a coin flip).
  - **G2 FAIL after the two allowed iterations**: calibration −6.3pp vs
    the 5pp gate. Root cause: the training era (2011–2017) shows ~no home
    advantage, so no honest walk-forward fit can predict the dev era's
    56.5% home rate — and the market itself is 5.5pp out on the same bar.
    (Gate-design lesson recorded: a raw-pp gate at n=177 sits inside
    binomial noise; a z-test gate would have been better. The gate as
    registered is honoured regardless.)
  - **Consequence**: Stage C not run — the 120-match holdout is UNSPENT,
    preserved for the player-level ball-by-ball model once Stage D
    unblocks. This market is parked, not killed: the benchmark is the
    softest in the repo and the wedge test says the close adds nothing,
    so a genuinely better pricer has room to show it.
- **D candidates**: BBL ball-by-ball (blocked, above); pool player ratings
  across T20 leagues once Cricsheet is reachable; grow the odds benchmark
  (OddsPortal via Wayback — also currently blocked).

## Market 3 — Soccer 1X2 (`soccer/`) — moved up (data on disk; NHL blocked)

Classical from-scratch target. Goals for ~22 leagues (incl. lower divisions)
already on disk (`soccer/data/raw/`, 396 CSVs) with avg-book early/close
prices in the same files.

**Stage A complete (2026-07-31).** Benchmark (`src/fp_benchmark.py`):
population = FTR + full 3-way early-average (EAvg, opener proxy) and
closing-average odds, proportional devig, multiclass LL. Closing-average
coverage starts 2019, so dev is effectively 2019-20 .. 2021-22.

| split | n | LL(open) | LL(close) | open−close (clustered t) |
|---|---|---|---|---|
| dev (…–2021-22) | 22,358 | 1.00769 | 1.00392 | +0.00377 (t=7.9) |
| holdout (2022-23 – 2025-26) | 30,912 | 0.99885 | 0.99552 | +0.00333 (t=8.0) |

The average market is superbly calibrated (implied within 1pp of realized
for H/D/A on dev) — the hardest benchmark so far.

**Gates — registered 2026-07-31, before any fp model code.**
- **G1 (dev)**: LL(model) − LL(open) ≤ **+0.015** → Stage C; fail after at
  most two feature iterations → record as control, move on. (Context: v1's
  odds-fed GBM lost to the opener by 0.022; Dixon-Coles-class models
  typically land within ~0.01–0.02 of market prices.)
- **G2 (dev)**: per-outcome calibration |mean P − rate| ≤ **2pp** for each
  of H, D, A (independent-Poisson draw underestimation must be corrected
  from training data, not by eyeballing dev).
- **G3 tripwire (dev)**: beating the close by > 0.001 at clustered t > 3 →
  leakage investigation.
- **Stage C (holdout, scored once)**: LL(model) − LL(open) with clustered
  t (≤ −2 = the opener is beaten); flat-stake ROI at EAvg prices for
  EV > 2% / 5% with devigged-open placebo.
- Model tuning restricted to seasons ≤ 2018-19 (pre-benchmark era, ~60k
  matches with results), walk-forward within it.

- **B — complete (2026-07-31): G1 FAIL after both allowed iterations →
  control recorded, holdout unspent.** `src/fp_model.py`: walk-forward
  multiplicative attack/defence Poisson per league, diagonal draw
  inflation, tuned on ≤2018-19 only.
  - Iteration 1: model − open **+0.01678** (t=15.4) vs gate ≤ +0.015;
    calibration ≤ 0.1pp on all three outcomes.
  - Iteration 2 (extended grid + team-keyed ratings surviving promotion/
    relegation — the tuner rejected team-keying on train): **+0.01643**
    (t=15.2). Gate honoured → **from-scratch soccer is a control**.
  - Context: the market-blind Poisson still out-does v1's odds-fed GBM
    (−0.022 vs the opener); the average opener is simply sharp. The
    model's calibration is excellent — the gap is discrimination, not bias.
  - Holdout (2022-23 – 2025-26) deliberately UNSPENT — available to a
    future richer model under a NEW registration (shots/xG covariates are
    already in the CSVs; FM priors are a Stage D candidate).
- **D candidates**: Football Manager crowdsourced player/club attributes as a
  lower-division prior (a starting point, not a be-all-end-all), understat
  xG, Transfermarkt squad values; Sonnet waves to collect/normalize
  lower-division sources football-data doesn't carry.

## Market 4 — NHL (`props/`) — demoted while data-blocked

Known problem: betting data exists only for the archived 2025-26 season
(12.5k prop files + game lines); **and NHL api-web is unreachable from this
environment**, so historical outcomes are blocked too. Try GitHub mirrors
(e.g. hockeyR-data parquet) or owner network action.

- **A**: multi-season historical game/player data (api-web when reachable,
  or a GitHub mirror); benchmark from the BP archive; gates.
- **B/C**: (a) game model — Poisson goals, team strength, goalie effects
  (the puck line was Phase 1G's near-miss); (b) player props (SOG / points /
  assists) via the Market-1 architecture through `props/src/dist_utils.py`.
- **D candidates**: historical NHL odds beyond 2025-26 (public odds archives,
  Wayback) — likely mandatory here, not a fallback; shift/TOI data for a
  real ice-time model.

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

**Stage A complete (2026-07-31 session 2).** Pipeline rebuilt end-to-end
(hoopR fetch → build_props → map_events 100.0% → grade → panel 84,840
player-games from 2023-24 → modelset 119,490 coherent-open props).
Benchmark (`props/src/fp_benchmark.py`; conventions as WNBA; dev ends
2026-02-28 per props/PLAN.md, holdout = 2026-03-01+ incl. playoffs):

| split | n | LL(open) | LL(close, same line) | open−close (t) | over rate | implied |
|---|---|---|---|---|---|---|
| dev | 81,348 | 0.67761 | 0.67406 (n=62,736) | +0.00135 (t=5.9) | 0.4675 | 0.4793 |
| holdout | 38,142 | 0.67736 | 0.66983 (n=25,444) | +0.00323 (t=7.1) | 0.4670 | 0.4783 |

~1.2pp over-shade (smaller than WNBA's ~2pp).

**Gates — registered 2026-07-31 (session 2), before any fp model code.**
Model inputs: panel feature columns only; the line is scoring-threshold
only; σ/NegBin params from `dist_params_nba.json` (already fit strictly
pre-odds by build_modelset). Two model iterations allowed; frozen vs
in-season-expanding calibration counts as one iteration choice.
- **G1 (dev)**: LL(model) − LL(open) ≤ **+0.010** → Stage C.
- **G2 (dev)**: |mean P(over) − realized| ≤ **2.5pp**.
- **G3 tripwire (dev)**: beats same-line close by > 0.001 at t > 3 →
  leakage investigation.
- **Stage C (holdout, scored once)**: LL(model) − LL(open) clustered t
  (≤ −2 = opener beaten); flat $1 ROI at consensus open EV > 2% / 5% with
  clustered t + devigged-open placebo; regular-season vs playoffs reported
  separately (secondary).

**Stage B — complete (2026-07-31 session 2): G1 FAIL after both allowed
iterations → control recorded, holdout unspent.** `props/src/fp_model.py`
(WNBA port: per-game EW blends × pace/defense, pre-odds calibration,
Normal+NegBin mixture):
- Iteration 1 (frozen pre-odds calibration): model − open **+0.02469**
  (t=11.5) vs gate ≤ +0.010; calibration fine (−0.78pp; ≤2.2pp per market).
- Iteration 2 (weekly expanding calibration): **+0.02469** — identical.
  The gap is discrimination, not bias: the NBA market prices minutes/roles/
  matchups far better than per-game EWs can. ROI negative (−1.6 to −2.1%,
  t≈−3), placebo 0 bets. Gate honoured → **NBA props from-scratch is a
  control**; the 38k-prop holdout is UNSPENT, reserved for a model with
  genuinely new inputs (Stage D injury reports being the identified one).
- Cross-market pattern now complete and consistent with `nba/`'s thesis —
  the from-scratch gap tracks market attention: WNBA late-season **parity
  (+0.00006)** → BBL **tie (+0.002)** → soccer **+0.016** → NBA props
  **+0.025**.

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
- **2026-07-31** — Market 1 Stage C complete and **held-out FAILED**: dev
  +0.00469 / cal +0.41pp, but 2026 (scored once) +0.01763 (t=4.1) with a
  −2.44pp calibration drift the frozen season-level fit couldn't track.
  ROI +11–14% at t<1 recorded as noise per the pre-registered rule.
  Verdict stands; revisit only via a prospective post-2026-07-31 holdout
  or Stage D data. Market 1 parked. Next: Market 2 (BBL cricket) Stage A.
- **2026-07-31** — Market 2 Stage A complete: benchmark from the committed
  xlsx (297 matches; **dev-season opener is worse than a coin flip**,
  LL 0.70380 vs 0.69315), gates registered. Data blocker recorded:
  cricsheet.org + web.archive.org outside the network allowlist, no GitHub
  mirror → ball-by-ball waits on owner action; Stage B proceeds team-level.
  Also found: NHL api-web unreachable from this environment → Market 3
  (NHL) is data-blocked; soccer (data on disk) moves ahead of it, per the
  flexibility flagged in the approved plan.
- **2026-07-31** — Market 2 Stage B complete, **gate-stopped**: Elo+NRR
  blend ties the opener on dev (+0.00205, t=0.3, G1 PASS) but fails the
  G2 calibration gate (−6.3pp vs 5pp) after the two allowed iterations —
  the pre-odds training era carries no home advantage to learn. Holdout
  deliberately UNSPENT; market parked pending ball-by-ball data (Stage D,
  owner action). Next: Market 3 (soccer, data on disk) Stage A.
- **2026-07-31** — Market 3 Stage A complete (53,270-match benchmark, gates
  registered and pushed before model code) and Stage B complete: Poisson
  attack/defence model **fails G1 after both allowed iterations** (+0.01643
  vs ≤ +0.015, t=15.2) with near-perfect calibration → soccer recorded as
  a from-scratch control; holdout unspent. Session pauses here: NHL and
  cricket ball-by-ball await owner data actions; NBA props is the next
  runnable leg (hoopR mirrors are on raw.githubusercontent, like wehoop).
- **2026-07-31 (session 2)** — Market 1 revisit: drift hypothesis rejected
  post-hoc; 2026 FAIL decomposed into expansion churn (+0.036 on GSV/PDX/
  TOR) and season phase (Aug–Oct 2025 = parity +0.00006). **fp-prospective-1
  registered** on Aug-2026+ props before any qualifying data exists;
  edge-watch converted to data-only archiver (owner decision; PROTOCOL
  block rewritten — still no picks/notifications/bets). Next: NBA props.
