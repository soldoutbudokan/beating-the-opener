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
| 1 | WNBA player props | `wnba/` | **LIVE** | talent engine beats opener on dev (Aug–Oct −0.0077, ROI +8.6%); **betting re-opened 2026-07-31** (FD EV>10%, protocol-pinned); news-watch live (news → overrides → picks → notify); fp-prospective-1/2 scoring firewalled |
| 1b | WNBA game lines | `wnba/` | prospective | v1 + v2 **ML head = control** (v2 GV2-1 +0.020 vs ≤+0.010 after the one-ball rework halved v1's gap); v2 **spread head passed dev gates** (+0.0072, cal −1.5pp) → `fp-games-prospective-1` accruing on games 2026-08-01+, scored at season end; no betting |
| 2 | BBL cricket match odds | `cricket/` | done | **control #3 confirmed** — player model passed dev gates, holdout FAIL (+0.052, t=3.6, ROI −34%); benchmark n=297 is structurally too small; future cricket → IPL pending odds source |
| 3 | Soccer 1X2 | `soccer/` | parked | **control** — G1 fail after both iterations (+0.0164 vs ≤+0.015); calibration excellent; **holdout unspent** |
| 4 | NHL player props | `props/` | **striking distance, unproven** | Registrations N + N2 complete 2026-08-24: talent engine then attempts (Corsi) engine closed 83% of the shots/blocked gap (pooled +0.0078 t=5.7 → **+0.0014 t=1.4** — statistically indistinguishable from the opener; blocked +0.0002 t=0.1); spend condition (dev ≤ 0.000) not met → **holdout unspent**; next: information layer (lineups/PP/goalies), 2026-27 prospective arm (owner decision) |
| 5 | NBA player props | `props/` | parked | **control** — G1 fail both iterations (+0.0247); calibration fine, discrimination gap; **holdout unspent** (awaits Stage D injury data) |
| 6 | MLB pitcher props | `props/` | **control at the serious-effort level** | registration K run 2026-08-29: QC PASS, K-G1 PASS (engine beats the EW blend on K and outs market-free), **K-G2 FAIL both iterations** — dev cell +0.03419 (incumbent) → **+0.01924 (t=6.2)** it.1 → +0.02150 it.2 (not adopted) vs gate ≤ +0.010; spend condition unmet → **2026 holdout unspent**; no prospective arm, no live arm |

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
- **Beyond-BBL scope (owner direction, 2026-07-31 session 2)**: once
  cricsheet.org + aussportsbetting.com are allowlisted (owner approved),
  expand to (a) all-T20 pooled player ratings feeding the BBL model, and
  (b) IPL match odds as their own Stage A/B market. The owner's TheTilt
  template (Cricsheet download → parse deliveries → features → win-prob
  ensemble) is the implementation guide — checked this session: it commits
  only small lookup JSONs, so it's a pipeline pattern, not a data source.

### Cricket data unblocked (2026-07-31 session 3)

cricsheet.org opened: all six T20 archives fetched and parsed
(`src/fp_ingest.py`, gitignored/regenerable) — BBL 662, IPL 1,243, PSL 357,
CPL 407, T20 Blast 1,570, T20Is 5,591 = **9,830 matches, 2.25M deliveries,
2005–2026**. aussportsbetting.com itself still 403s from this container,
but Wayback (open) confirms: the Feb-2023 BBL file we hold is the newest
real capture, and **aussportsbetting has no IPL file at all** — IPL odds
need a proper Stage-D source (OddsPortal-via-Wayback scrape; queued). So
BBL remains the scoreable cricket market; every league feeds the ratings.

**Player-model registration — registered 2026-07-31 (session 3), before
the model is evaluated on dev.** Model class: per-player batting/bowling
ratings from cross-league ball-by-ball (time-decayed, walk-forward),
aggregated over a strictly-prior **expected XI** (appearance-weighted from
each team's previous matches — never tonight's XI or toss, which postdate
the opener); venue/home effects; all tuning on pre-2018 data only. Fresh
gates (the team-model's iterations are spent and its G2 was mis-sized —
recorded 2026-07-31):
- **G1 (dev 2018–2020)**: LL(model) − LL(open) ≤ **+0.010**; at most two
  iterations.
- **G2 (dev)**: |mean P(home) − realized home rate| ≤ **2× binomial σ**
  (≈7.4pp at n=177) — sized to noise this time, openly.
- **G3 tripwire (dev)**: beats the open by > 0.005 at t > 2 → investigate.
- **Stage C (the reserved 120-match holdout, scored once)**: LL gap with
  paired t (≤ −1 suggestive, ≤ −2 significant); flat $1 ROI at the
  multi-book-average open, EV > 2% / 5%, with devigged-open placebo.

**IPL odds hunt (2026-07-31 session 3) — every reachable route is dry.**
Attempted and dead, so nobody re-treads: (a) OddsPortal via Wayback —
season and match pages ARE archived 2010+, but odds were script-loaded
and the archived HTML contains empty odds containers (verified on 2011-
and 2014-era page versions; the `fb.oddsportal.com` data feeds were never
captured); (b) BetExplorer via Wayback — IPL pages not archived at all;
(c) GitHub — no public IPL odds dataset findable; (d) Kaggle — outside
the network allowlist; (e) aussportsbetting — publishes no IPL file
(verified via CDX). **The good route is Betfair's historical exchange
data (historicdata.betfair.com): free with a Betfair account, real traded
IPL prices back to ~2016, timestamped (gives true open AND close). Owner
action: download the cricket files and commit them under
`cricket/data/raw/betfair/`; the Stage A benchmark + registration for IPL
is then a rerun of the BBL machinery.**

**Player-model verdict (2026-07-31 session 3): dev gates PASSED (G1
+0.00635, G2 −6.6pp within ±7.5pp), holdout FAILED decisively** — scored
once per the registration: model − open **+0.05162 (t=3.6)**, calibration
−9.7pp, ROI at the average open **−34% (t≈−3)**, placebo 0 bets. The
dev-era "opener worse than a coin flip" did not persist: the 2021-22
opener was sharp (LL 0.640) while the model, honestly fit on a training
era with no home advantage, sat near coin. **BBL from-scratch = control
#3 confirmed**, now from the modelling side as well as the wedge side.
Structural lesson recorded: with only 297 odds-matched matches, even a
good model could not have proven itself here — future cricket work should
target markets with larger odds samples (IPL, pending the Stage-D odds
scrape). Both holdout and iterations are spent for BBL.

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
environment**, so historical outcomes are blocked too.

**Mirror probe (2026-07-31 session 2):** training-era data IS reachable —
`sportsdataverse/fastRhockey-data` has NHL player_box/team_box/pbp/schedules
parquet for 2011–2024 (verified via raw.githubusercontent), and
`danmorse314/hockeyR-data` has pbp 2010-11–2023-24. **Neither carries the
2025-26 eval season**, so grading the archived odds still needs
api-web.nhle.com — owner approved adding it to the allowlist this session;
Stage A starts the moment it's reachable (training fetch can even start
from the mirror now, but there is nothing to grade against until then).

- **A**: multi-season historical game/player data (api-web when reachable,
  or a GitHub mirror); benchmark from the BP archive; gates.
- **B/C**: (a) game model — Poisson goals, team strength, goalie effects
  (the puck line was Phase 1G's near-miss); (b) player props (SOG / points /
  assists) via the Market-1 architecture through `props/src/dist_utils.py`.
- **D candidates**: historical NHL odds beyond 2025-26 (public odds archives,
  Wayback) — likely mandatory here, not a fallback; shift/TOI data for a
  real ice-time model.

**Stage A complete (2026-07-31 session 3).** api-web opened; pipeline
ported end-to-end (fetch 2024-25 + 2025-26 → panel 106,397 player-games →
modelset **55,338 coherent-open props**: shots/points/assists/goals/
blocked/saves). Benchmark (`fp_benchmark.py --sport NHL`; dev ends
2026-02-28):

| split | n | LL(open) | LL(close, same line) | open−close (t) |
|---|---|---|---|---|
| dev | 37,520 | 0.61667 | 0.61608 (n=37,023) | +0.00022 (t=1.3) |
| holdout | 17,818 | 0.61230 | 0.61139 (n=17,519) | +0.00039 (t=1.4) |

**The NHL close adds ~nothing over the open** (lines rarely move — the
Phase 1 finding, reproduced). So here the from-scratch model competes with
the market's *only* number, and there is no sharper close to warn of
leakage — the tripwire is correspondingly weaker evidence either way.

**Gates — registered 2026-07-31 (session 3), before any fp model code
ran on dev.** Conventions as NBA (inputs = panel features only; σ/NegBin
fit strictly pre-odds, i.e. on the 2024-25 season):
- **G1 (dev)**: LL(model) − LL(open) ≤ **+0.010**; two iterations
  (frozen vs weekly-expanding calibration is one choice).
- **G2 (dev)**: |mean P(over) − realized| ≤ **2.5pp**.
- **G3 tripwire (dev)**: beats same-line close by > 0.001 at t > 3 →
  investigate (weak here, see above).
- **Stage C (holdout, scored once)**: LL gap clustered t (≤ −2 = opener
  beaten); flat $1 ROI at consensus open EV > 2% / 5% + devigged-open
  placebo.

**Stage B — complete (2026-07-31 session 3): G1 FAIL after both allowed
iterations → control recorded, holdout unspent.** `fp_model_nhl.py`:
frozen +0.01921 (t=10.5), weekly-expanding +0.01914 — identical story to
NBA. Per market, the physics shows through: **shots +0.0084 and
blocked +0.0068 are inside the gate on their own** (volume stats, EW-
predictable), while goals (+0.032), saves (+0.032, cal −7pp) and assists
(+0.024) carry the miss — rare events where the market's shooting-talent
and goalie-matchup priors beat per-game EWs. ROI −7% (t=−9), placebo 0
bets. The 17,818-prop holdout is UNSPENT — a future registration could
legitimately target the shots/blocked cell, which was competitive on dev.

### Market 4 revisit — NHL talent engine (registration N) — gates registered 2026-08-24, before any engine code exists

Owner ask (2026-08-24): apply LESSONS.md to a new backtestable market
(cricket/NBA/NHL preferred). Decision — NHL shots/blocked first (see
"New-market exploration" below for the probe results and the cricket/NBA
paths): it is the only named market with committed odds (the 2025-26 BP
archive → 55,338-prop modelset recipe), reachable outcomes (api-web), an
UNSPENT 17,818-prop holdout, a cell already inside the Stage-B gate
(shots +0.0084, blocked +0.0068), and an untested port of the repo's one
validated modelling advance — the WNBA Kalman talent engine (T1: 7/7
market-free, dev +0.00469 → −0.00059, the source of the live arm).
Motivating gap in the incumbent: `fp_model_nhl.py` has **no rate × TOI
path at all** (pure per-game EW blend; `toi_min_ewf/ews` sit unused in
the panel), so the port introduces the same structural upgrade T1 did.

**Design class (fixed now).** Walk-forward scalar Kalman per
(player, stat) on per-TOI-minute rates, the T1 architecture adapted:
obs y = stat/toi_min, R = rvar/toi_min, prediction written pre-update
(leak-free by construction); position-group career-trajectory curves by
the T1 delta method with NHL sizing fixed before tuning (groups
{C, L→F, R→F, D}; BUCKET=40, MAX_BUCKET=20 → flat past 800 career
games); season key derived from game_id (NHL seasons straddle the new
year — `dt.year` is banned as the offseason-inflation key); offseason
state inflation ×10 as T1. Skater stats sog/blk are the candidates;
g/a/p reported for diagnostics; goalies out of scope (saves stays a
control). Career gp counts games within 2010-11+ (pre-2010 veterans
enter mid-career — the same truncation WNBA accepted at 2003).

**Data (acquisition, not modelling).** Training era 2010-11–2023-24
skater boxes fetched from api-web itself (verified this session:
`gamecenter/{id}/boxscore` serves 2011+ with sog/blockedShots/toi in
the modern schema — single source end to end, no mirror schema risk),
gitignored/regenerable like the eval-era fetch. **QC gate before any
tuning (pre-registered):** (a) per-season coverage ≥ 98% of that
season's api-web schedule (types 2+3); (b) dual-source agreement vs the
independent fastRhockey-data mirror parquet on a random 300-skater-game
sample in each of 2013-14, 2018-19, 2022-23: exact match ≥ 98% of
{g, a, sog, blk} cells, toi within 0.5 min on ≥ 95% of rows. Fail →
investigate and fix before the engine sees anything.

**Isolation rule.** `data/nhl/` (eval fetch), the panel, the modelset
and the benchmark builders are UNTOUCHED — Stage-B baselines must
reproduce byte-comparably. The talent module reads the historical fetch
from its own directory, runs the filter over history+eval combined, and
emits `talent_{st}` per (pid, game_id) joined only at scoring time (the
wnba `--talent` pattern). Any new column entering the modelset would
re-run the |corr| > 0.12 leakage guard (none is planned).

- **Tuning**: q/p0 grids and offseason mult as T1; curves, rvar and all
  grid scoring on games **before 2019-07-01 only** (≤ 2018-19).
- **N-G1 (market-free)**: walk-forward next-game prediction on seasons
  2019-20 through 2023-24, skater rows with toi_min ≥ 5: MSE of
  per-game stat prediction — engine path = talent_rate ×
  (0.6·toi_min_ewf + 0.4·toi_min_ews) vs the incumbent per-game blend
  0.6·{st}_ewf + 0.4·{st}_ews (same alphas f=0.15/s=0.05,
  shift-then-ewm, computed identically in the module). Engine must
  win on sog AND blk. **Cell rule fixed now: the registered cell for
  N-G2/Stage C = the subset of {shots, blocked_shots} that passes
  N-G1; if neither passes → stop market-free, nothing touches dev or
  holdout.**
- **N-G2 (dev = eval props ≤ 2026-02-28)**: `fp_model_nhl.py --talent`
  gives cell markets the μ path W_RATE·(per-game blend) +
  (1−W_RATE)·(talent_rate × toi_blend), fillna cross-fallbacks as the
  WNBA path; W_RATE and every calibration constant (c, home, NB r) fit
  strictly on pre-odds rows (< 2025-10-07). Gate: pooled cell
  LL(model) − LL(open) must improve on the same-data Stage-B baseline
  (recomputed at evaluation time on the rebuilt pipeline; registered
  2026-07-31 values shots +0.0084 / blocked +0.0068), AND pooled
  |mean P(over) − realized| ≤ 2.5pp. **At most two iterations**
  (iteration 2 = engine-aware pre-odds recalibration / W_RATE refit,
  still pre-odds-only).
- **N-G3 tripwire**: beats the same-line close by > 0.001 LL at
  |t| > 3 → halt, leakage investigation (weak evidence here — the NHL
  close ≈ its open — investigate anyway).
- **Stage C (holdout = props 2026-03-01+, scored ONCE) — spend
  condition registered now: run only if N-G2's pooled dev gap ≤
  0.000.** Market 1's lesson applied: its Stage C spent a holdout from
  "striking distance" (+0.00469) and failed; the talent engine earned
  its live arm at dev −0.00059. "Improved but still positive" → record,
  park, holdout stays unspent. If run: pooled + per-stat LL gap vs open
  with date-clustered t (≤ −2 = the opener beaten from scratch); flat
  $1 ROI at consensus open, EV > 2% / 5%, clustered t, devigged-open
  placebo; no-move share reported for CLV context (AUDIT N2). Research
  only — no live-betting implication under any outcome.

**Rebuild + baseline recompute (2026-08-24, before any talent run).**
Pipeline rebuilt end-to-end in a fresh container (eval fetch 2,788
games, 0 failures; panel 106,397 player-games — identical to the
2026-07-31 build). The eval population GREW 55,338 → 62,070: today's
post-season roster files resolve player names the mid-July fetch could
not, so grading match rates rose to 94.9–99.6% (blocked 99.6, shots
99.0; the old-era rate was 87.2%). More matches → more rows survive
`matched`; the benchmark shifts with the population and every number
below is the same-data baseline the gates compare against:

| split | n | LL(open) | LL(close, same line) | open−close (t) | over rate | implied |
|---|---|---|---|---|---|---|
| dev (≤ 2026-02-28) | 42,146 | 0.61165 | 0.61100 (n=41,594) | +0.00025 (t=1.5) | 0.393 | 0.387 |
| holdout (2026-03-01+) | 19,924 | 0.60560 | 0.60428 (n=19,586) | +0.00050 (t=2.0) | 0.373 | 0.377 |

Incumbent Stage-B model recomputed on this data (frozen pre-odds cal):
pooled +0.01998 (t=11.4; July: +0.01921), **shots +0.00814 (n=9,222),
blocked_shots +0.00702 (n=4,170)** — the registered N-G2 baselines,
pooled cell ≈ +0.00779. Reproduction is clean; the July per-stat values
(+0.0084/+0.0068) sit within population-shift distance. Also noted for
the record: dev **blocked_shots carries a 3.1pp over-shade** (implied
P(over) 0.519 vs realized 0.488) — a WNBA-like structural miscalibration
inside the registered cell; saves shades 3.1pp the other way and goals
2.5pp (both outside the cell). n-multiplicity caveat applies; the
devigged-open placebo column remains the control for any shade-driven
ROI.

**QC verdict (2026-08-24): PASS.** Training fetch landed 17,799/17,799
scheduled final games (100.0% coverage, every season 2010-11–2023-24,
0 failed requests). Dual-source vs the fastRhockey mirror on the three
registered seasons: 300/300 joined each; stat cells 100.0%/100.0%/99.92%
(one cell in 1,200 — consistent with a late official scoring change);
toi within 0.5 min on 100% of rows. Gates (≥98% / ≥95%) cleared with
room. `qc_nhl_hist.py` prints `QC_PASS`; tuning unlocked.

**N-G1 verdict (2026-08-24): PASS 5/5 → registered cell =
{shots, blocked_shots}, both alive.** 741,136 skater-games, 2,815
players, seasons 2010-11–2025-26. Walk-forward next-game per-game-stat
MSE, 2019-20..2023-24 (n=227,194 per stat, toi ≥ 5):
sog **1.9348 vs blend 1.9696** (−1.8%), blk **0.9092 vs 0.9318**
(−2.4%); diagnostics g −2.8%, a −2.7%, p −2.6% — the engine wins
everywhere, same as T1's 7/7. Grid-boundary note, recorded again
exactly as T1 had to: the tuner chose the lowest process noise AND
lowest p0 in the grid (q=1e-4, p0=0.05) on every stat — maximum
regression/stability. The grid was not widened post-hoc; noted as a
candidate for a future registration, not this one.

**N-G2 verdict (2026-08-24): PASS on the improvement gate after
iteration 1; iteration 2 not adopted; Stage C spend condition NOT met →
holdout UNSPENT, exactly as registered.**
- Iteration 1 (`--talent`, W_RATE + c/home/dispersion fit pre-odds):
  the pre-odds fit chose **w_rate ≈ 0** (shots 0.05, blocked 0.00) —
  pure talent_rate × toi_blend, the per-game EW blend discarded, same
  as WNBA. Dev cell: **pooled +0.00227 (clustered t=2.30, cal
  −0.43pp)** vs the same-data baseline **+0.00779 (t=5.72)** — 71% of
  the cell gap closed. Per stat: **blocked_shots +0.00068 (t=0.40) —
  statistical parity with the opener**; shots +0.00299 (t=2.44).
  Full-slate calibration −1.43pp. N-G3: model is BEHIND the same-line
  close on the cell (+0.00314, t=3.2) — no tripwire. Cell-only ROI at
  consensus open: EV>2% −1.53% (t=−1.1), EV>5% **+0.24% (t=0.1) =
  noise, reported as such**; placebo 0 bets. (Incumbent cell ROI was
  ≈ −7%.)
- Iteration 2 (`--lin`, engine-aware linear μ recalibration fit
  pre-odds; motivated by a dev diagnostic showing monotone μ-scale
  bias, low-μ −5.1pp / high-μ +2.1pp): shots +0.00297 (no change),
  blocked +0.00101 (slightly worse), calibration worse (−2.0/−2.4pp).
  **Not adopted; iteration 1 is the model of record.** Reading: the
  panel-wide pre-odds linear fit does not transfer to the prop-priced
  population — the residual low-μ bias lives in rows where the market
  prices role/TOI changes our participation history lags, i.e. the
  WNBA M lesson reproduced in NHL: what remains is an **information
  gap (ice time/lineups), not an estimator gap**.
- Where this leaves NHL: shots/blocked went from "control at +0.0078
  (t=5.7)" to **"striking distance, unproven" (+0.0023, t=2.3)** with
  blocked at parity — the same designation as the WNBA 1b-v2 spread
  head. Both N-G2 iterations and the two Stage-B iterations are spent;
  the 19,924-prop holdout is preserved for a model that reaches dev
  ≤ 0.000 under a fresh registration. Recorded forward paths (each
  needs its own registration): (a) **prospective 2026-27 arm** — the
  iteration-1 model is frozen and scoreable, but no routine archives
  NHL odds, so accrual needs an owner decision to extend the archiver;
  (b) **shot-attempts observation model** — pbp with attempts exists
  on the fastRhockey mirror; Corsi-as-observation is the standard
  de-noising of SOG rates and targets exactly the shots gap;
  (c) **the T3 analogue** — point-in-time projected-lineups/PP-unit/
  TOI capture, the information prize the M-lesson points at.

### Registration N2 — shots via an attempts observation model (Corsi de-noising) — gates registered 2026-08-24, before any N2 code exists

Motivation: N left shots at +0.00299 (t=2.44) with the M-lesson reading
(information gap); but one *estimator* upgrade remains untried and is
the canonical one for this exact stat — SOG is a thinned sample of shot
attempts (~2.3× the event count), so filtering on attempts should
estimate the underlying rate better than filtering on SOG alone.
Verified this session before registering: api-web pbp serves attributed
`shot-on-goal`/`missed-shot`/`blocked-shot`/`goal` events back to 2011
in the modern schema (goals are separate events; blocked events carry
both shooter and blocker ids).

**Data.** Per-player-game attempt counts aggregated in-flight from
api-web pbp for all training+eval games (raw payloads not retained;
aggregates gitignored/regenerable). **QC gate before any tuning:**
(a) pbp coverage ≥ 98% of fetched games per season; (b) internal
consistency — pbp-derived SOG (goal + shot-on-goal events) equals the
boxscore `sog` on ≥ 97% of skater-game rows, and attempts ≥ boxscore
`sog` on ≥ 99.9% of rows, in each of 2013-14, 2018-19, 2022-23 and
2025-26. Investigate any failure before the engine sees anything.

**Engine class (fixed now).** The usg/eff two-state template from the
WNBA game model: per player, `att` = attempts per TOI-minute (obs
attempts/toi, R = rvar_att/toi) and `og` = on-goal fraction (obs
sog/attempts, R = rvar_og/attempts, update only when attempts > 0);
predicted SOG/min = att × og. Curves/rvar/q/p0 per state fit and tuned
strictly pre-2019-07-01 (grids and conventions exactly as N). Delivery:
`talent_sog` replaced by the att×og prediction; blocked_shots keeps the
N iteration-1 path; W_RATE/c/home/dispersion refit pre-odds as N it. 1.

- **N2-G1 (market-free)**: same window/rows/metric as N-G1 — must beat
  the N engine's sog per-game MSE **1.9348** (EW blend 1.9696 reported
  alongside).
- **N2-G2 (dev)**: pooled cell (shots via N2, blocked via N) must
  improve on N's **+0.00227**; |pooled cal| ≤ 2.5pp; at most two
  iterations (it. 2 = engine-aware pre-odds recalibration choice).
- **Tripwire and Stage C unchanged from N**: spend condition pooled
  dev ≤ 0.000; holdout scored once with ROI, placebo, no-move share.
- Multiple-testing note, stated openly: this is a second registration
  against the same dev season — dev is dev-grade only, reused; the
  19,924-prop holdout stays single-shot and is the only place a
  market-beat claim can come from.

**N2 verdicts (2026-08-24, both iterations spent).**
- **QC PASS**: pbp fetched for all 20,591 games (0 failures), 100%
  coverage every season; SOG identity ≥ 99.94% on all four registered
  seasons; attempts ≥ sog on 100% of rows; attempts/SOG ratio 1.84–2.10
  (the de-noising headroom). Data-semantics fix caught before tuning:
  a played skater with no attempt events in a covered game is a TRUE
  ZERO, not missing — without the fill the att state never updates
  down for fringe players.
- **N2-G1 PASS**: per-game SOG MSE 2019-20..2023-24 (n=227,194):
  attempts engine **1.92591** vs N engine 1.93476 vs EW blend 1.96963.
- **N2-G2: PASS on the improvement gate; spend condition NOT met →
  holdout UNSPENT.** Iteration 1 (att×og rate into the N delivery
  path, w_rate[shots]=0.10): shots +0.00299 → **+0.00185**, pooled
  cell **+0.00149**. Iteration 2 (`--disp`, NegBin r by pre-odds
  threshold likelihood — MoM r folds model error into conditional
  variance and suppresses P(over); fitted r: shots 14.2, blocked 6.5):
  blocked +0.00068 → **+0.00022 (t=0.12, cal −0.2pp)**, shots +0.00188
  (t=1.62), **pooled cell +0.00136 (clustered t=1.40, cal −1.22pp) —
  adopted as the N2 model of record.** vs same-line close +0.00219
  (t=2.26): no tripwire. Cell ROI at consensus open: EV>2% −1.49%
  (t=−1.0), EV>5% +1.68% (t=0.9) = noise; placebo 0 bets.
- **Where the NHL revisit ends (2026-08-24)**: across one session the
  cell went +0.00779 (t=5.72) → N +0.00227 (t=2.30) → N2 **+0.00136
  (t=1.40)** — 83% of the gap closed, and the pooled cell is now
  *statistically indistinguishable from the opener* (blocked
  effectively AT the opener at +0.00022). The registered spend
  condition (point estimate ≤ 0.000) is honestly unmet, and rightly
  so: a model indistinguishable from the market would not survive the
  holdout's t ≤ −2 superiority test either. All iteration budgets
  (Stage-B ×2, N ×2, N2 ×2) are spent; the 19,924-prop holdout is
  preserved for a model that shows dev superiority under a fresh
  registration. The remaining candidates, in order of the evidence:
  the T3-analogue information layer (projected lineups/PP units/
  starting goalies — the M-lesson prize), a 2026-27 prospective arm
  (needs an owner decision to archive NHL odds next season), and
  engine refinements that would need genuinely new structure
  (PP/ES-split states, opponent-conditioned og) rather than more
  recalibration.

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

## Market 1 v3 programme (owner-directed, 2026-07-31 session 4)

Owner's diagnosis: recent-form averages are structurally too shallow —
mean regression cripples them, and props are ultimately a minutes
question. Build order: **T1** DARKO-style talent/trajectory engine for
per-minute rates; **T2** distributional minutes engine with non-linear
per-minute extrapolation; **T3** news-driven override layer + hourly
news-watch routine (free sources first: ESPN endpoints + league injury
page); **T4** `fp-prospective-2` registered at v3 lock, alongside the
running v1 arm. The old "no model reads injury news" rule is superseded
for this programme by owner direction (live betting stays paused; the
news layer feeds logged projections, never bets).

**T1 gates — registered 2026-07-31 (session 4), before the engine runs:**
- **T1-G1 (market-free)**: walk-forward next-game per-minute rate
  prediction, minutes-weighted squared error on 2015–2024 panel rows
  (min ≥ 10), hyperparameters tuned on pre-2015 only: the talent engine
  must beat the fast/slow EW blend on ≥ 4 of the 6 core stats
  (poi/reb/ass/tpm/ste/tur), else fix before it touches props.
- **T1-G2 (dev)**: swapped into the prop model, dev-2025
  LL(model) − LL(open) must improve on v1's **+0.00469**; at most two
  iterations. Standard tripwire vs close unchanged.

**T1 — complete (2026-07-31 session 4). Both gates PASSED, decisively.**
- **T1-G1**: the Kalman talent engine beats the EW blend on **7/7** stats
  at walk-forward next-game rate prediction (2015–2024, by 5–10% each).
  The mean-regression critique validated empirically. (Note: the tuner
  chose the lowest process-noise in the grid on every stat — maximum
  regression/stability. Grid-boundary note recorded.)
- **T1-G2**: dev-2025 with `--talent`: LL(model) − LL(open) =
  **−0.00059** — the model beats the opener over the FULL dev season
  (v1: +0.00469). Edges the close (−0.00070, t=−0.3; tripwire NOT
  tripped — threshold is <−0.001 at |t|>3). Calibration −0.46pp.
  ROI at consensus open: **+4.5% (t=2.9)** EV>2%, **+6.5% (t=3.9)**
  EV>5% (n=5,708); placebo 0 bets.
- By phase (dev): May–mid-Jun +0.0184 (still behind early — the news gap
  T2/T3 target), mid-Jun–Jul −0.0014, **Aug–Oct −0.00767 (t=−2.1), ROI
  +8.6% (t=3.1)** — the model beats the opener outright in exactly the
  window the prospective test runs on.
- Discipline reminder: these are dev numbers. The claim that counts is
  prospective (below).

**`fp-prospective-2` — registered 2026-07-31 (session 4), before any
qualifying data exists.** Deviation from the phase plan, reasoned openly:
the season is running now and every archived day is prospective sample,
so the strongest *tested* model is locked immediately rather than after
T2/T3 (which become separately-registered upgrades if they beat this on
dev):
- Population: standard eval convention, **props dated ≥ 2026-08-01**
  (same window as fp-prospective-1, which continues as the v1 baseline
  arm).
- Model: `fp_model.py --talent` at this commit — talent.pkl built by
  `talent.py --build` with curves/params fit pre-2025, v1 frozen
  calibration path otherwise. Pinned; no further changes count.
- **Primary**: LL(model) ≤ LL(open) with clustered t ≤ −2 (superiority —
  the dev Aug–Oct analogue earned aiming higher than parity this time).
- **ROI**: flat $1 at consensus open, EV>5%: ROI > 0 with clustered t
  reported (t ≥ 2 = signal); placebo mandatory. Tripwire vs close as
  always.
- Evaluate **once**, when n ≥ 3,000 or the 2026 season ends. Dev
  analogue for context: Aug–Oct 2025 gap −0.00767 (t=−2.1), ROI EV>5%
  +8.56% (t=3.1).

**Data-integrity fix (ASG leak) — registered 2026-08-03, before taking
effect.** `features.py`'s All-Star filter was an enumerated blocklist of
team codes; the 2026 captains' codes (SPO/COOP) were missing, so the
2026-07-25 All-Star Game box rows entered the panel and deflated minutes
and per-game stat EWs for every participant for the two post-break
weeks. Found post-morteming the v3 bets: bet players averaged +3.4
minutes over the model's estimate (league-wide bias −0.5), 13 of the
first 27 v3 fills were ASG participants, and first-order clean
re-pricing put 4 of them below the 10% trigger. Fix: drop game_ids the
schedule flags `ALLSTAR` (all years) plus SPO/COOP in the code fallback.
No model parameters change. Registration hygiene for
fp-prospective-1/2, which were pinned on the polluted pipeline: archived
predictions (props dated 2026-08-01..03) stand as produced; from
**2026-08-04** the same pinned models score on the clean panel, and the
season-end evaluation reports the two windows separately. Live sheets
repriced post-fix the same day (see bets.csv notes for affected open
fills).

**T2 gates — registered 2026-07-31 (session 4), before the minutes
engine runs on dev.** Design: distributional minutes (EW mean + variance
fit per starter-status/level bucket on pre-2025 data, presumed-absent
boost), within-player rate-vs-minutes curvature (per-stat slope, fit
pre-2025), conditional σ (residuals given actual minutes, pre-2025), and
P(over) integrated over a 5-point minutes grid.
- **T2-G1 (dev)**: LL(model) − LL(open) must improve on T1's **−0.00059**;
  at most two iterations.
- **T2-G2 (dev)**: |calibration| ≤ 2.5pp. Tripwire vs close unchanged.
- If passed: registered as **`fp-prospective-3`** on props dated after its
  lock push (T1's prospective-2 keeps running for comparison).

**T2 — complete (2026-07-31 session 4): GATE FAILED after both allowed
iterations → not adopted.** Iteration 1 (conditional-σ calibration):
−0.00027, cal −2.0pp. Iteration 2 (end-to-end grid calibration):
−0.00030, cal −2.1pp. Neither improves on T1's −0.00059. Reading: a
purely statistical minutes distribution adds variance without adding
information — exactly the owner's point that minutes are the hard,
*subjective* part. The T2 machinery (distributional minutes, rate-vs-
minutes curvature, grid integration) stays in the code behind
`--minutes`, unadopted by default: it becomes the delivery mechanism for
T3's news-driven minutes overrides, and any override-driven variant gets
its own registration before touching unseen data. No fp-prospective-3;
the pinned T1 model remains the live arm.

**T3 systematized (2026-08-04, owner-approved: "what you call T3 makes
sense").** `wnba/src/avail_watch.py`: point-in-time availability
capture — league injury report (status/type/return-date, incl.
"Coach's Decision"), per-event injury reports, and lineups/DNP-reasons
once ESPN populates them — archived to committed `data/raw/avail/`
(change-deduped snapshots; the feed is ephemeral, the archive is the
future training set). The script prints a structured NEW/UPDATED/
CLEARED diff each firing, which the news-watch routine now judges into
overrides (PROTOCOL step 1 rewritten; headlines demoted to step 1b).
**No model may train on this archive without a pre-registered QC gate.**
Also fixed en route: ESPN's edge 403s the old `news_watch.py` header
combination (bare Mozilla UA, no Accept) — every routine firing since
the allowlist opened had been silently `SOURCES_UNREACHABLE`; both
scripts now send an honest UA + `Accept: */*` and the feed works from
this container. Effects reach the routine when this lands on main.
First snapshot committed: 2026-08-04 15:09Z, 35 players (12 Coach's
Decision entries — the pure minutes signal the M experiment proved the
market knows and our history can't see).

**T3 — infrastructure armed (2026-07-31 session 4).**
- `wnba/live/projections_overrides.json` (append-only, schema in-file) —
  the news layer's log AND its future scorecard.
- `wnba/src/news_watch.py` — fetch/diff utility over free sources (ESPN
  WNBA news API); prints new items for the routine to judge; graceful
  `SOURCES_UNREACHABLE` path verified.
- **news-watch routine** `trig_01GThXFjtLzfXEH1kqjMYXEF`, hourly at :31,
  fresh session per firing, no notifications; scope pinned by the new
  section in `wnba/live/PROTOCOL.md` (no picks/notifications/bets, ever).
- **Owner action required**: allowlist `site.api.espn.com` and
  `www.espn.com` — until then every firing ends with one quiet
  `SOURCES_UNREACHABLE` line.
- **T3b (next build)**: `wnba/src/fp_live.py` — tonight's-slate projection
  sheet from the pinned talent model + overrides through the T2 minutes
  machinery, logged and timestamped; the routine already knows to run it
  once it exists. Any override-driven variant gets its own registration
  before it may be scored. Spot-check backtest of the news layer: done
  against its own first live weeks of logged overrides (honest version of
  the owner's "spot-checks; adjust as the experiment is live").

**Minutes decomposition diagnostic (2026-08-04) — POST-HOC, never
claimable; motivates a T2-retry aimed at the minutes MEAN.** Question
(owner): is projecting playtime the fundamental way to win this market?
`wnba/src/fp_minutes_diag.py` reprices dev 2025 with the pinned
fp-prospective-2 configuration, swapping in oracle inputs (leakage by
construction — build-prioritisation numbers only):
- As-is reproduces the registered dev gap (−0.00068 vs −0.00059; drift
  from the refreshed archive + ASG fix).
- **Oracle minutes** (actual minutes through the existing fp_live
  override mechanism): gap **−0.068** (t=−15) — ~9× the Aug–Oct edge,
  ~100× the full-season edge, calibration +0.1pp.
- Oracle per-minute rates: −0.368 — but that is ≈ knowing the outcome
  (realized shooting variance has no pre-game information to capture),
  so it bounds variance, not addressable edge. Minutes info DOES exist
  pre-game (availability, starters, rotations) — minutes are the
  dominant *addressable* term.
- Walk-forward minutes blend on dev prop rows: **MAE 4.18 min** (sd
  5.4). LL gap by |minutes error|: 0–2 min → **−0.0136** (model beats
  opener clearly); 2–4 → −0.0064; 4–7 → −0.0027; **7+ (17% of props) →
  +0.0338** — the model's whole loss vs the opener is concentrated in
  the big-minutes-miss tail. When minutes are right, the talent rates
  already win.
- Reading: T2 failed because it improved the minutes *variance* model,
  not the minutes *mean/information*. The winnable piece is the
  pre-game-knowable component of minutes error. Proposed next build
  (NOT registered — gates to be pushed before any code runs, per
  protocol): (a) a minutes engine modelling *share of available team
  minutes* (compositional, 200-min conservation — the one-ball insight
  applied to playtime) with Kalman dynamics like the talent engine
  (minutes still use the EW blend the talent engine beat 7/7 on rates)
  plus structural covariates EWs can't see (starter-status transitions,
  rest/b2b, returnee ramp, expected-blowout garbage time from the
  fp_games2 margin); gate M-G1 market-free (beat MAE 4.18 walk-forward),
  gate M-G2 dev LL improve on −0.00059, two iterations, adoption →
  fp-prospective-3; (b) systematic point-in-time starting-lineup +
  availability capture in news-watch (turns overrides into trainable
  data); (c) the already-flagged live staleness gate (owner decision —
  changes what gets bet).

**Minutes-engine gates (M) — registered 2026-08-04, before any engine
code exists (owner go: "Let's try it then").** Design class: walk-forward
Kalman filter on each player's **share of team minutes**, renormalized at
prediction time over the presumed-available set (players who appeared in
the team's previous game — strictly prior participation, never tonight's
box score); season-boundary state inflation; structural factors (returnee
ramp by games-since-return, rest/b2b) fit on ≤2014 residuals only. All
hyperparameters tuned on pre-2015 data only (the T1 convention).
- **M-G1 (market-free)**: walk-forward next-game minutes MAE on played
  panel rows 2015–2024 where the incumbent blend is defined (min_ewf
  notna): the engine must beat the W_FAST fast/slow EW blend on the same
  rows. Context: blend MAE = 4.18 min on dev-2025 prop rows. No
  iteration cap (T1-G1 convention: fix before it touches props), but
  every variant tunes on pre-2015 only.
- **M-G2 (dev 2025)**: engine minutes delivered through the existing
  override mechanism (per-game EWs scaled by the minutes ratio, minutes
  estimate replaced — the fp_live.py path, i.e. exactly what the oracle
  measured): LL(model) − LL(open) must **improve on the same-data as-is
  baseline** (−0.00068 on the current rebuild; registered T1 value
  −0.00059), |calibration| ≤ 2.5pp. **At most two iterations** (frozen
  cal vs engine-aware recalibration counts as the iteration choice).
  Tripwire unchanged: beats same-line close by > 0.001 at |t| > 3 →
  leakage investigation.
- If both pass: **`fp-prospective-3`** registered at the lock push, on
  props dated after it (scoring-only; fp-prospective-1/2 continue as
  comparison arms). The live betting sheet stays on the pinned
  prospective-2 model — switching what gets *bet* is a separate owner
  decision, not part of this registration.
- 2026 remains post-hoc for any diagnostic rerun; nothing here may claim
  it.

**M verdict (2026-08-04): M-G1 PASSED, M-G2 FAILED after both allowed
iterations → engine NOT adopted; no fp-prospective-3; both live arms
and the betting sheet unchanged.** `wnba/src/minutes_engine.py` +
`fp_model.py --mineng`.
- Engine as built: share-of-team-minutes Kalman (q=2e-4, r=2e-3,
  season-boundary inflation 5e-3, p0=0.04 — all tuned pre-2015,
  interior optima confirmed), walk-forward league team-total tracker
  (totals drifted 195.4 pre-2015 → ~201 modern; a frozen constant is a
  systematic low bias), ≤2014-fit returnee-ramp/b2b factors, and a
  per-level combination with the EW blend tuned pre-2015 (engine weight
  0.8–1.0 for ≤28-min players, 0.3 for 28+ starters). Dev-honesty note:
  the first M-G1 run iterated team-by-team, which leaks a traded
  player's later-team games into earlier-team predictions — caught,
  fixed to global date order, and re-tuned before anything touched dev.
- **M-G1 PASS**: 2015–2024 walk-forward MAE **4.818 vs blend 5.096**
  (−0.28 min); dev-2025 4.771 vs 5.013; on dev *prop* rows 4.166 vs
  4.176 with bias +0.07 vs +0.32. The engine is a genuinely better
  market-free minutes predictor.
- **M-G2 FAIL**: iteration 1 (frozen cal) +0.01257 — the pre-2015 t_hat
  level bias shifted every mu low (cal −2.27pp). Iteration 2
  (engine-aware recalibration, level bias gone, cal +0.54pp):
  **+0.00009 vs the required improvement on −0.00068**. Gate honoured.
- Post-hoc phase diagnostic (dev): the engine helps exactly where
  information is scarce — May–mid-Jun +0.0177 → +0.0148 — and *hurts*
  Aug–Oct (−0.0078 → −0.0054), the window the prospective arms score
  on. Adoption would have traded away the live edge for an early-season
  improvement that still loses to the opener.
- **Lesson, extending T2's**: T2 showed a better minutes *variance*
  model doesn't price props better; M shows even a better minutes
  *mean* (M-G1-verified) doesn't either. The opener already embeds the
  information that moves minutes — who is out, who starts, why — so
  statistical reshuffling of participation history cannot out-price it
  in-window; the oracle prize (−0.068) is an *information* prize, not
  an estimator prize. The winnable path stays T3: point-in-time
  availability/lineup capture (news-watch already logs overrides;
  systematic pre-tip starter/availability snapshots are the missing
  dataset), plus the Wayback historical-lineup Stage-D candidate to
  make such inputs trainable. Unspent candidates recorded, each needing
  a fresh registration: rate-path-only delivery (the ratio-scaled
  per-game path is where the engine's residual noise leaks in);
  engine-based rotations for the fp_games2 spread head; engine as the
  base the fp_live news overrides modify (betting change = owner
  decision).

## Market 6 — MLB pitcher props (`props/`) — owner-directed 2026-08-29

Owner ask (2026-08-29): take the WNBA lessons to another market with
gettable odds and public data, ideally tradeable at FanDuel, and backtest.
Screen run this session from this machine (statsapi.mlb.com, SBR and
Polymarket are reachable here; the BettingPros key serves no sport beyond
the five already archived, so a *new* sport would need a new odds source):

- **NFL props** (season opens 2026-09-10): one archived season, 64 dates,
  every market fails the informative-close gate at current power — the
  re-screen waits on 2026 data, as FINDINGS.md already says.
- **NBA props**: the Stage-D injury-report job, season opens late October.
- **MLB pitcher props — chosen.** The archive already holds them and the
  old-era "venue empty" verdict was a property of the *anchored move
  model's* trade cell (FD-sourced opener ∧ FD close ∧ side agrees →
  16 bets), not of the market: on the fp population FanDuel quotes a
  coherent close on **100% of strikeout props and posts the opener itself
  on 43–51%**, the close beats the open on Ks in both seasons (+0.0038
  t=3.1 dev; +0.0049 t=2.6 holdout — the two-gate wedge, reproduced),
  openers post the night before (median ≈ 11pm ET), and FD's K line is
  still at the open at close 88% of the time (the market moves the juice,
  rarely the half-integer line). Two seasons are archived, the season is
  running (through 2026-09-27 + playoffs), and the generative story is the
  cleanest in the repo: **K = batters faced × K-rate**, i.e. the WNBA
  minutes × per-minute-rate architecture with BF as "minutes", the
  opposing lineup as the defense factor, and the posted lineup/pitch-count
  news as the T3-analogue information layer.

**Stage A complete (2026-08-29).** Pipeline unchanged from the 2026-07-28
build (`fetch_mlb` → `build_props` → `map_events` → `grade_props` →
`features` → `build_modelset`; graded 2025-03-18 .. 2026-07-27; K/outs
book-0 match 99.4%, void 4.8% — pitcher props void unless started).
Benchmark (`fp_benchmark.py --sport MLB --markets …`; conventions as
Markets 1/4/5: matched, non-void, coherent consensus open, no push; over =
actual > open_line; **dev = 2025 season, holdout = 2026**):

| market | split | n | LL(open) | open−close same line (t, n) | over rate | implied | FD close | FD-sourced open |
|---|---|---|---|---|---|---|---|---|
| strikeouts | dev 2025 | 4,847 | 0.68854 | **+0.00378 (t=3.1, n=4,211)** | 0.502 | 0.497 | 100% | 43% |
| strikeouts | holdout 2026 | 3,141 | 0.68556 | +0.00494 (t=2.6, n=2,471) | 0.497 | 0.497 | 100% | 51% |
| outs_recorded | dev 2025 | 1,671 | 0.68195 | +0.00055 (t=0.5, n=1,443) | 0.486 | 0.507 | 99% | 2% |
| outs_recorded | holdout 2026 | 2,932 | 0.68382 | +0.00279 (t=1.7, n=2,269) | 0.514 | 0.508 | 100% | 0% |
| pitcher family (5 mkts) | dev 2025 | 14,843 | 0.68587 | +0.00161 (t=3.4) | 0.485 | 0.496 | — | 14% |

Unlike WNBA there is **no over-shade on Ks** (implied 0.497 vs realized
0.502); outs carries a 2pp over-shade on dev that does not persist on
holdout. hits_allowed / walks_allowed / earned_runs have no FanDuel quote
in the archive at all — reported as diagnostics only, never a cell.
Registered pitcher family for this market: **{strikeouts, outs_recorded}**.

### Registration K — pitcher talent engine — gates registered 2026-08-29, before any engine code exists

**Data (acquisition, not modelling).** Training era 2015–2022 fetched
from statsapi itself (`fetch_mlb.py --seasons 2015 … 2022`, same parser as
the 2023–2026 fetch — single source end to end), gitignored/regenerable;
plus `people` handedness (pitchHand/batSide) for every id in the panel.
**QC gate before any tuning (pre-registered):** (a) per-season boxscore
coverage ≥ 98% of that season's scheduled finals (types R + postseason);
(b) independent-aggregation agreement: for a random 60 pitcher-seasons in
each of 2016, 2019, 2022, the boxscore-summed season totals of {K, BF,
outs} equal statsapi's own `stats=season` totals on ≥ 98% of cells (that
endpoint is computed upstream from the official play-by-play, so a missed
or double-counted game shows up as a mismatch). Fail → investigate and
fix before the engine sees anything.

**Engine class (fixed now).** The T1/N template, adapted:
- Per-pitcher scalar Kalman on `kr` = K per batter faced (obs k/bf,
  R = rvar/bf), all appearances (starts and relief) feed the state; career
  curves by the delta method over career *appearances* (BUCKET=30,
  MAX_BUCKET=15 → flat past 450 games), groups {SP, RP} by prior-season
  majority role (rookies SP); season key = the schedule season (not
  `dt.year` — MLB postseason is the same key anyway); offseason inflation
  ×10; grids and tuning exactly as N.
- Per-batter scalar Kalman on `br` = K per plate appearance (obs so/pa,
  R = rvar/pa), one group, BUCKET=100 games, MAX_BUCKET=12.
- Workload ("minutes"): the pre-start BF estimate `bf_hat`. Three
  candidate paths, **selected once by tuning-era next-start MSE (starts
  before 2021-01-01), never on dev**: (i) the incumbent EW blend
  0.6·bf_ewf + 0.4·bf_ews; (ii) a Kalman level filter on BF per start;
  (iii) pitches-per-start Kalman ÷ pitches-per-BF Kalman.
- Opponent: strictly-prior expected lineup = the opponent's starting nine
  (batting-order slots 100–900) from its most recent game against a
  starter of the same hand within the last 10 days, else its last game —
  never tonight's lineup (the nba/ availability leak) — lineup K
  propensity = mean of the nine `br` states (rookies at the league prior);
  combined with the pitcher's `kr` by **log5 against the walk-forward
  league K/PA rate**, with one scalar γ on the lineup term (γ=1 = pure
  log5) fit on ≤2020 starts.
- Park: per-venue K factor, shrunk, refit walk-forward on prior seasons
  only. No umpire, weather or pitch-tracking inputs in this registration
  (Stage-D candidates, listed below).
- Distributions: strikeouts = Binomial(BF, p) mixed over BF ~ discretised
  Normal(bf_hat, σ_bf(bf_hat)) with σ_bf fit ≤2020 — the generative
  distribution — with NegBin (r by pre-odds threshold likelihood, the N2
  machinery) as the alternative; **choice made on ≤2020 threshold
  likelihood with synthetic half-integer lines, never on dev.**
  outs_recorded = empirical conditional distribution of outs given the
  predicted mean (bucketed), fit ≤2020 and refit walk-forward — the
  inning-boundary multimodality (mass at 15/18) is why a Normal is wrong
  at 15.5/16.5/17.5 lines. Calibration constants (c, home) fit strictly
  pre-odds (< 2025-03-18).
- The consensus line enters only as the scoring threshold. No market-
  derived column of any kind is an input; talent columns are joined at
  scoring time (the wnba `--talent` pattern) so no new column enters the
  modelset and the |corr| > 0.12 leakage guard is unaffected.

**Isolation rule.** `data/mlb/` 2023–2026 parquets, `panel_mlb.pkl`,
`modelset_mlb.pkl`, `dist_params_mlb.json` and `fp_benchmark.py`'s
population are untouched by the engine; the benchmark above must
reproduce byte-comparably at evaluation time.

- **K-G1 (market-free)**: walk-forward next-start prediction on starts
  2021-03-01 .. 2024-12-31 (bf ≥ 10): per-start MSE of K and of outs —
  engine path (kr × bf_hat × lineup/park factors; outs from the selected
  workload path) vs the incumbent per-game EW blend 0.6·{k,outs}_ewf +
  0.4·{k,outs}_ews (MLB alphas f=0.25/s=0.08, shift-then-ewm, computed
  identically in the module). **Cell rule fixed now: the registered cell
  for K-G2/Stage C = the subset of {strikeouts, outs_recorded} whose
  stat passes K-G1; if neither passes → stop market-free, nothing touches
  dev or holdout.** No iteration cap on the market-free stage (the T1
  convention: fix before it touches props), but every variant tunes on
  ≤2020 only.
- **K-G2 (dev = 2025 props)**: `fp_model_mlb.py --talent`: cell markets
  priced from the engine; the incumbent EW-blend path (per-game blend ×
  opponent factor (opp_so_pa_ew/league)^0.5, pre-odds calibration —
  the Market-4/5 Stage-B class) computed on the same rows as the
  same-data baseline and reported alongside. Gates: pooled cell
  LL(model) − LL(open) ≤ **+0.010** (striking distance, the standard G1)
  AND improve on the same-data EW baseline, AND pooled |mean P(over) −
  realized| ≤ **2.5pp**. **At most two iterations**; iteration 2 must
  come from this menu, fixed now: (a) engine-aware pre-odds
  recalibration (linear μ or threshold-likelihood dispersion, the N/N2
  option), (b) platoon-split states (kr vs LHB/RHB, br vs LHP/RHP from
  play-by-play), (c) workload path switch among the three candidates.
- **K-G3 tripwire**: beats the same-line close by > 0.001 LL at |t| > 3
  → halt, leakage investigation (the K close is genuinely sharper than
  its open here, so this tripwire carries real weight, unlike NHL's).
- **Stage C (holdout = 2026 props, scored ONCE) — spend condition
  registered now: run only if K-G2's pooled dev cell gap ≤ 0.000** (the
  Market-1 lesson made a rule, as in N). If run: pooled + per-stat LL gap
  vs open with date-clustered t (≤ −2 = the opener beaten from scratch);
  flat $1 ROI at consensus open, EV > 2% / 5%, clustered t, devigged-open
  placebo; no-move share for CLV context; **and the FanDuel cell**: rows
  with a coherent FD quote, priced at FD's close (the price available
  after the opener), EV > 5% / 10% tiers with clustered t and placebo —
  the venue question answered on the same sheet. "Improved but still
  positive" → record, park, holdout stays unspent.
- **Prospective arm `k-prospective-1`** (registered at the lock push, if
  K-G2 passes): props dated after the push, scored once at season end
  from a post-hoc `scrape_bp.py --sport MLB` (BettingPros serves
  historical openers for ~13 months, so no daily routine is needed for a
  *scoring* arm). Same metrics as Stage C. **No live betting off this
  market under any outcome without a separate owner decision**, and any
  live design starts from a fresh PROTOCOL (opener-capture timing, the
  MOVED_OFF_OPEN/STALE gates, lineup-posting cadence) — none of that is
  registered here.
- **Stage-D candidates recorded now**, each needing a fresh registration:
  point-in-time posted lineups and probable-pitcher changes (the T3
  analogue), umpire K-zone effects (officials are in the boxscore),
  Statcast whiff/CSW rates as a de-noised observation model (the N2
  analogue: pitches → swings → whiffs → K), minor-league priors for
  debut starters.
- Multiple-testing note, stated openly: dev 2025 is reused by both
  iterations; the 2026 holdout is single-shot and the only place a
  market-beat claim can come from.


**Registration K verdicts (2026-08-29, all budgets spent).**
- **QC gate: FAIL then PASS — and it earned its keep.** First run: coverage
  100% but the 2016 independent-aggregation check landed at 96.1% (7/180
  cells, four relievers short by 1–2 BF/outs). Cause found by per-game
  comparison against statsapi's game logs: `fetch_mlb.py` fetched only
  `status == "Final"` games, so **rain-shortened "Completed Early" games
  (59 across 2015–2026) were skipped** — and the coverage check used the
  same denominator, so it could not see them. Fixed (`FINAL_STATUSES`,
  applied to the eval-era data too: 2025 +5 games, 2026 +2 — the
  modelset/benchmark were built from the July fetch and are unchanged;
  a rebuild would move a handful of grades). Second run: coverage
  100.0% every season 2015–2026 (24,653 games, 0 failed requests);
  agreement 2016 99.4% (one remaining single-out mismatch, consistent
  with a late official scoring change), 2019 100%, 2022 100% →
  `QC_PASS`. Handedness fetched for all 4,239 ids (`fetch_mlb_people.py`).
- **K-G1 PASS on both stats** (`talent_mlb.py`; 237k pitcher-games, 572k
  batter-games, 2,758 pitchers). Walk-forward next-start MSE 2021–2024
  (n=18,697 starts, bf ≥ 10): **K 5.1895 vs EW blend 5.4204 (−4.3%)**,
  **outs 13.979 vs 14.068 (−0.6%)**. Decomposition (all tuning-era
  choices): the expected-lineup log5 term is worth 5.30 → 5.19 (γ=0.5
  tuned ≤2020; lineup coverage 99.9%); the workload path selected on
  ≤2020 next-start BF MSE was **(iii) pitches-per-start Kalman ÷
  pitches-per-BF Kalman** (13.851 vs level-BF 13.860 vs EW 13.888).
  Grid-boundary note, recorded for the fourth time: every rate state
  chose the lowest process noise and p0 in the registered grid
  (q=1e-4, p0=0.05); the level filters chose the top of theirs (p0=1.0).
- **K-G2 iteration 1 (`fp_model_mlb.py --talent`): FAIL on striking
  distance, PASS on the rest.** Same-data incumbent EW baseline
  **+0.03419 (t=8.8)**, cal −1.9pp. Engine: pooled cell **+0.01924
  (clustered t=6.2)**, 44% of the gap closed; calibration +0.78pp
  pooled — but **outs +6.4pp**, strikeouts −1.2pp; per market
  strikeouts +0.01708 (t=5.2), outs +0.02545 (t=3.9). K distribution
  chosen on ≤2020 threshold likelihood: NegBin (r=31; 0.64760 vs the
  Binomial-over-BF mixture 0.64800 — the generative mixture lost
  narrowly; r refit pre-odds = 21). K-G3: behind the same-line close
  (+0.0215, t=6.8) — no tripwire. ROI at consensus open EV>5% −1.65%
  (t=−1.0); FD cell at FD close EV>10% n=1,960 ROI −3.7% (t=−1.6) with
  mean claimed EV +21% — the claimed edges are the model's error, not
  the market's; placebo 0 bets in every cell. No-move share 15.7%.
- **Iteration 2 (menu a, `--recal`): NOT adopted — worse.** Fit strictly
  pre-odds: K μ' = 0.795 + 0.813·μ, outs μ' = 5.33 + 0.685·μ (both
  reduce 2023–24 MSE out-of-sample), outs distribution on the last 4
  seasons (validated 2023–24: tails within ±2pp vs +2.6/+4.2pp for the
  full window), NegBin r refit on the recalibrated μ (329 ≈ Poisson).
  Dev: pooled **+0.02150 (t=4.9)**, strikeouts +0.01770 (cal +0.5pp),
  outs +0.03240 (cal +4.4pp). Calibration improved and log loss got
  worse: the pre-odds fit on *all* starts (openers, bulk relievers,
  spot starters — where μ is genuinely unreliable) shrinks the mean
  toward the average, which costs discrimination on the prop-priced
  population of regular starters. **The NHL N iteration-2 lesson
  reproduced: a panel-wide pre-odds recalibration does not transfer to
  the priced population.** Iteration 1 stands as the model of record.
- **Spend condition (dev ≤ 0.000) not met → Stage C not run; the
  2026 holdout (3,141 K + 2,932 outs props) is UNSPENT.** No
  `k-prospective-1`, no live design, nothing in `live/`.
- **Where the gap is (post-hoc diagnostic, reported only, never
  claimable):** on dev outs props the opener's implied mean is no
  better than the engine's (MSE 14.43 vs 14.47) — that gap is
  distribution *shape* on a selected population; on strikeouts the
  opener's mean is **7% better** (4.89 vs 5.28) — real discrimination
  the box-score panel cannot see. The K market is the sharpest prop
  market this programme has met: the incumbent's +0.034 sits above
  NBA's +0.025, and the engine's +0.019 is where NBA's baseline started.
- **Forward paths, each needing a fresh registration:** (1) calibration
  and distribution fit on the *prop-shaped* population (regular
  starters with bf_hat ≥ 20 and ≥ 10 prior starts) — the outs head's
  +4–6pp calibration and the shrinkage failure both point there;
  (2) the N2-analogue observation model from Statcast (pitches → swings
  → whiffs → K; CSW%/stuff as the de-noised K-rate observation) — the
  only candidate that targets the 7% mean gap; (3) point-in-time
  lineup/pitch-count capture (the T3 analogue) for a prospective arm.
  MLB stays a research market; the season-end scrape of the archive
  (BP retains ~13 months) keeps the option of scoring (1)/(2) on
  2026 data open.

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

## Market 1b — WNBA game lines from the player model (owner-directed)

Sum the talent engine's player projections over strictly-prior expected
rotations → team points → margin → P(home). Circularity guard (owner's
concern, resolved by construction): talent states carry NO team/opponent
adjustments — pace/defense factors are applied exactly once, at the game
level. No odds inputs anywhere. Benchmark: consensus (book-0) moneyline
open/close from the archived `games.pkl` (15,771 ML rows). Context:
props/ Phase 1G found the WNBA game close adds nothing over its open —
here that means beating the opener = beating the market's only number.

**Gates — registered 2026-07-31 (session 5), before any game-model code
runs on dev.** Rotation = previous game's lineup with prior EW minutes,
normalized to 200 team-minutes (never tonight's roster — the nba/
availability leak). home_adv and σ fit on ≤2024 games only, walk-forward.
- **GG1 (dev = played 2025 + 2026 games)**: LL(model) − LL(open ML) ≤
  **+0.010**; at most two iterations. (Played-2026 is dev-grade only —
  the props Stage C spent that season.)
- **GG2 (dev)**: |mean P(home) − home win rate| ≤ **2pp**.
- **Tripwire**: beating the close by > 0.005 at t > 2 → investigate.
- If GG1+GG2 pass: prospective registration (`fp-games-prospective-1`) on
  games after the lock push; betting integration only after that test, as
  its own owner decision.

**Verdict (2026-07-31 session 5): GG1 FAILED after both allowed
iterations → game lines are a control.** Iteration 1 (previous-game
rotation): +0.04790 (t=3.5). Iteration 2 (appearance-EW expected
rotation): **+0.04043 (t=3.0)**, calibration +3.5pp — nowhere near the
+0.010 gate, and the market's close ≈ its open here (0.604 vs 0.609).
Reading: WNBA **game** lines are efficient even though the **props** are
soft — the prop edge is mostly distribution-level calibration (the
over-shade) plus player-detail, which largely cancels when aggregated to
a game outcome the market watches closely. Confirms props/ Phase 1G from
the modelling side. Parked; the player model stays where its edge is.

## Market 1b-v2 — possession-based game model (owner-directed rebuild)

The owner's diagnosis of the v1 failure (2026-07-31 evening): summing
per-minute scoring talents ignores that **there is one ball** — usage is
conserved, a team gets a fixed number of possessions and players share
them; redistributed usage is absorbed at *lower* efficiency; defense
should be a real per-possession quantity, not raw points allowed; and a
margin model's native market is the **spread**, not the moneyline —
"maybe this won't lead to a market beat, but if we ARE to beat it, it
will take much more serious attention to detail." GG1's two iterations
are spent; this is a NEW experiment with fresh gates. The v1 verdict
stands.

**Architecture.** Team points = possessions × points-per-possession.
Possessions from both teams' EW pace (form chosen on ≤2024 fit). Talent
engine extended with two new Kalman states per player (tuned like the
existing ones, params fit <2025-01-01): `usg` = (FGA + 0.44·FTA + TOV)
per minute, `eff` = points per possession used. Rotation = v1
iteration-2 machinery (appearance-EW top 10, 200 min, strictly prior).
One-ball constraint: player possession demands (minutes × usg) are
renormalized to the game's possession count; the usage–efficiency
tradeoff γ (efficiency lost per unit of forced extra usage) is fit on
≤2024 player-games, set to 0 if statistically noise. Defense applied
exactly once at game level as opponent points-allowed-per-possession vs
league (exponent fit ≤2024; circularity guard unchanged: talent states
carry no team/opponent context). Margin head: scale/home-adv/σ fit
≤2024; margin distribution Normal vs Student-t chosen on ≤2024
likelihood. Two market heads from the same margin: P(margin>0) vs
devigged consensus open ML; P(margin>−open spread) vs devigged open
spread price (book-0 spread rows — 15,518 archived, first use). No odds
inputs anywhere.

**Gates — registered 2026-07-31 (session 6), before any v2 code is
scored on dev.** Dev = played 2025–26 games (dev-grade only). Max two
iterations; all scored together each time:
- **GV2-1 (ML)**: LL(model) − LL(open ML) ≤ **+0.010**.
- **GV2-2 (spread)**: LL(model cover) − LL(open spread) ≤ **+0.010**.
- **GV2-3 (calibration)**: |mean P(home) − home win rate| ≤ **2pp**.
- **GV2-4 (report only)**: MAE(predicted margin, closing spread).
- **Tripwire**: beating either close by > 0.005 at t > 2 → investigate.
- Pass GV2-1 or GV2-2 → prospective registration
  (`fp-games-prospective-1`) on post-lock games; betting integration
  only as a separate owner decision. Fail both → game lines confirmed
  as a control at the serious-effort level; park permanently.

**Verdict (2026-07-31 session 6, both iterations spent).**
`wnba/src/fp_games2.py`. Train-only fits: plays = 7.3 + 1.019·poss_hat;
usage–efficiency β came out **wrong-signed** (+0.049 pts/play per unit
over-usage, t=2.1 — efficiency *rising* under forced usage is the
shot-selection artifact, not a causal tradeoff) → β := 0 per the
registered noise rule, documented; defense exponent 1.0; margin σ≈12.3.
- **Iteration 1** (margin head fit on all ≤2024): GV2-1 +0.02215
  (t=1.9) FAIL; GV2-2 **+0.00994 PASS**; GV2-3 +3.48pp FAIL. Diagnosis
  on train data only: WNBA home advantage collapsed ~3.3 pts
  (pre-2015) → ~1.1–1.5 pts (2021–24); the all-history fit (2.80)
  baked a stale home edge into every game.
- **Iteration 2** (margin head fit on 2020–2024; sole change):
  **GV2-1 +0.02008 (t=2.0) FAIL** → the moneyline head is a control,
  consistent with v1 and props Phase 1G — WNBA ML is efficient.
  **GV2-2 +0.00723 (t=0.6) PASS. GV2-3 −1.46pp PASS.** GV2-4: MAE
  (margin vs close spread) 3.40 pts. No tripwires (loses to both
  closes).
- Reading, stated honestly: the one-ball rework halved v1's ML gap
  (+0.040 → +0.020) and the **spread head sits within striking
  distance of the opener while calibrated** — but "pass" here means
  *close enough to keep alive*, not *beats the market*: the model is
  still 0.007 behind the open on dev.

**`fp-games-prospective-1` — registered 2026-07-31 (session 6), before
any post-lock game data exists.** Population: WNBA games dated
**2026-08-01 or later**, scored once at season end (expected n ≈
150–250 incl. playoffs) from the archived book-0 consensus opens
(the bp archive carries open_line/open_cost per event; no live capture
needed). Metric: spread head only — LL(model cover prob at open line)
− LL(devigged open spread price), date-clustered t, pushes dropped;
predictions from `fp_games2.py` exactly as committed at this push
(walk-forward states may absorb new box scores; no refits, no
parameter changes). Claim thresholds: ≤ 0.000 = the from-scratch game
model matches/beats the WNBA spread opener (first market-level claim
for a game model in this repo); > +0.010 = kill the spread head too.
In between = park as "striking distance, unproven". **No betting off
this head under any outcome without a separate owner decision.**

## Live betting re-opened (owner decision, 2026-07-31 evening)

The owner explicitly re-opened live WNBA betting for the v3 programme
("we should re-open live betting"). Pre-registered rules live at the top
of `wnba/live/PROTOCOL.md`: FanDuel coherent quote, news-adjusted claimed
EV > 10%, quarter-Kelly on half the claimed edge (dev claimed-vs-realized
≈ 2×), $0.50 rounding, 5%-per-bet and 30%-per-sheet caps, one bet per
player per game, owner-reported fills, CLV-primary scoreboard via the
audited settle machinery. Dev-2025 EV-bucket table that motivated the
10% trigger: 2–5% → −2.6%; 5–10% → +1.7%; 10–15% → +3.4%; 15–25% →
+9.3% (t=2.8); 25%+ → +15.3% (t=3.9) — claimed edge realizes ~half.
`news-watch` (hourly) now refreshes picks and notifies on new qualifying
rows; `edge-watch` stays data-only. The fp-prospective-1/2 LL
registrations are firewalled from betting outcomes.

- **2026-07-31 (session 5)** — First v3 fills: owner took 8 of 17 picks,
  flat $1 each ($8; deliberate deviation from sheet Kelly stakes, noted
  per row in bets.csv). Scoreboard un-paused: site rebuilt with v3 live
  copy and an honest CLV band (v3 dev EV>10% cell: realized ROI +10.0%,
  CLV vs raw close **−4.6%** — negative by construction for an
  under-heavy sheet fading the over-shade; clv_cal is the fair
  yardstick). Settlement of the 8 opens runs after tonight's games.
- **2026-08-02 (settlement session)** — 2026-08-01 slate settled (5 bets,
  2W-3L, −$1.22): **the first day whose CLV came in positive — raw
  +0.16%, shade-adjusted +2.38%**, against −4.14% (07-28) and −6.39%
  (07-31). Cumulative: 18 settled (8W-10L), −$3.27 on $18 staked
  (−18.2% ROI), bankroll $96.73, mean CLV −3.95% / CLV* −3.94%,
  mean claimed EV **+19.73%**. The claim-vs-verdict gap is the whole
  story so far: the model says +19.7% a bet, the closing line says
  −4.0%, and at n=18 the clustered t on CLV is −1.80 (3 dates) — far
  too little to separate either number from noise (AUDIT H7 power note
  applies exactly as written). The 7 bets on the 2026-08-02 slate stay
  open: wehoop's mirror had no 08-02 box scores at settlement time and
  the closes for events 2696/2697 were still inside the scraper's
  tip+5h archiving cushion.
- **Live-sheet bug found 2026-08-02 — stale players are not gated.**
  `fp_live.py:latest_states()` keys each player's state off
  `panel.groupby("nname").tail(1)` — the last game they ever played,
  with **no recency check**. BettingPros' offers payload for event 2695
  (LAS@PDX) carried a `Kelsey Plum` record tagged team `PHO`; Plum's
  last box score is **2026-06-21** (42 days stale) and ESPN lists her
  Out on Phoenix, so she was on neither roster in that game. The sheet
  still projected 5.88 assists off her June form and emitted
  `2695_assists_kelsey plum_over` at **+29.0% claimed EV**, which was
  filled. `build_fixture` compounded it: `my_team` (Phoenix) matches
  neither side, so the row was built as an away player against
  Portland. The bet will void on the no-box-row-after-3-days path
  (stake returned), so the cost here is a wasted slot, not a loss —
  but the same hole would happily price any long-absent player the
  feed still lists. Every other player on the current sheet is ≤ 8 days
  stale, so a days-since-last-game gate (~10–14 days) in
  `latest_states()` isolates exactly this case. **Not applied this
  session** — it changes what gets bet, which is an owner decision, not
  a scoreboard update. **Applied 2026-08-08** (owner decision, below).
- **2026-08-08 — first-week audit → routines paused + harness fixes
  (owner-directed).** The audit's decisive number: on the 59 settled v3
  bets the model's own `model_p` predicted 37.1 wins, 28 happened
  (binomial z = −2.48; unders alone z = −3.03, overs z = 0.0), while
  the market's vig-inclusive implied probabilities predicted 29.9
  (z = −0.49) — the model's under claims fail their own calibration
  test while the market passes. Mechanistic findings: (1) the panel
  froze at 08-01 for a week (wehoop stall) while picks kept flowing —
  claimed EV per slate rose 20%→30% mechanically as the market priced
  games the model hadn't seen (Carla Leite μ pinned at ~5.85 while FD
  walked her assists line 5.5→8.5 and the "EV" hit +51%); (2) the v1
  opener-only/don't-chase rule had been dropped in v3 — fills happened
  at lines 1–2 pts off the open, and no committed code reproduces the
  "FD EV>10% ≈ +5–10%" dev analogue (roi_sim prices consensus opens
  only); (3) the stale-player hole (Plum) was still open; (4)
  `market_shades()` returned `{}` in fresh containers, so `clv_cal`
  was silently stamped equal to raw `clv` from 08-03 on — the one
  pre-registered +3–6% falsifier wasn't being computed; (5) 46 of 57
  stamped bets closed at the bet line (CLV ≈ vig by construction,
  AUDIT N2); (6) 40 of 78 bets were re-entries of 18 frozen
  disagreements. Owner rulings: fix 1, 2, 4, 5 of the audit's list;
  re-bet repetition stays allowed (real-edge hypothesis); model
  changes (Kalman process-noise floor etc.) only via owner + strong
  session after the routine FLAGS role changes — no silent model
  edits. **Fixes landed this push:** pick gates in `fp_live.py`
  (PANEL_STALE via events.pkl, STALE_PLAYER >14d, TEAM_MISMATCH/
  TEAM_CHANGED, MOVED_OFF_OPEN with FD-open coherence + 15¢ juice
  tolerance, SUSPECT_EV >25% quarantine; advisory ROLE_MIN?/ROLE_START?
  owner-review flags; blocked rows stay listed, play=False, stake 0);
  shade fix in `settle_bets.py` (committed `live/shade_table.json`,
  stale-table fallback, blank-not-zero `clv_cal`, broken-era rows
  re-stamped); scoreboard adds no-move share + model-calibration z
  (RESULTS.md and site); ESPN fallback extended to the panel
  (schema-2 archives with full stat lines/ids, `features.py` appends
  wehoop-missing dates; validated 197/197 rows, 2/~3,700 cells diff —
  one late official scoring change; wehoop reclaims dates on publish).
  Registration hygiene: `fp-prospective-1/2` untouched — season-end
  evaluation rebuilds from wehoop-complete data, pinned models
  unchanged; the gates change what gets BET, never what gets SCORED.
  `news-watch` disabled at the trigger level (owner, 2026-08-08);
  `edge-watch` found already off since 07-31. Open at pause: 17 bets
  (8/8–8/9 games) needing closes + settlement, currently manual.

## New-market exploration (2026-08-24, owner-directed)

Owner ask: apply the lessons (LESSONS.md/AUDIT.md/this file) to another
backtestable market — cricket/NBA/NHL named, Polymarket floated as a
possible cricket odds proxy "used carefully". Session findings:

- **Network probes (this environment)**: polymarket.com and all three
  Polymarket APIs (clob/gamma/data) are egress-blocked (proxy 403);
  aussportsbetting.com still blocked; cricsheet.org, api-web.nhle.com,
  web.archive.org and raw.githubusercontent.com are reachable.
- **Decision — NHL first** (registration N under Market 4): the only
  named market runnable end-to-end today, with the shots/blocked cell
  and the unspent holdout as the pre-existing wedge, and the T1 talent
  engine as the untested upgrade.
- **Cricket stays odds-blocked.** Owner actions (either unblocks it):
  (a) the good route — Betfair historical exchange data downloaded to
  `cricket/data/raw/betfair/` (real traded IPL prices, timestamped,
  ~2016+); or (b) allowlist the Polymarket domains. Polymarket-as-proxy
  caveats, recorded now so a future benchmark is designed honestly: its
  niche-sport books are thin, so the recorded price is a stale
  last-trade/mid you could not have filled at size (an edge measured
  against it is an upper bound, not an expectation); there is no
  "opener" — the benchmark timestamp must be pre-registered (pre-toss
  for cricket, per the BBL toss-noise finding); fees/slippage and UMA
  resolution risk apply; and cricket coverage only begins ~2025, so n
  lands near BBL's structurally-too-small 297 (the recorded reason
  control #3 could never have proven anything). A Polymarket benchmark
  can screen for gross mispricing; it cannot certify a tradeable edge.
- **NBA props**: the identified path remains Stage D point-in-time
  injury/inactive reports via Wayback (reachable) against the unspent
  38k holdout — a large supervised-wave collection job, queued behind
  NHL, not started this session.
- **MLB props — flagged as the untouched archive.** The from-scratch
  programme never ran MLB (markets 1–5 skip it): the old-era kill was
  FanDuel *availability* (16 bets/13mo), not measured edge, and the
  committed archive is the repo's largest (327 MB, 13 months of offers).
  Blocked today: statsapi.mlb.com is egress-refused, so outcomes can't
  be graded from this environment. Owner action if wanted: allowlist
  statsapi.mlb.com — then MLB Stage A (benchmark + shade table + gates)
  is a rerun of the NHL machinery on the biggest archive we own.
- **Also noted for later**: the old-era wedge FLAGGED NHL *game*
  markets (pooled gate A+B true; puck line 77.8% directional on 90
  moves; goalie-news information flow per props/FINDINGS.md) — a
  from-scratch NHL game model (Poisson goals + goalie states vs
  ML/puck-line openers) is the natural follow-up registration if the
  props cell dies, using the same archived game-market offers.

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
- **2026-07-31 (session 2)** — Market 5 A+B complete: NBA props from-scratch
  is a **control** (both iterations +0.02469 vs ≤ +0.010; calibration clean;
  holdout unspent). Data probes: NHL training mirrors confirmed
  (fastRhockey-data 2011–2024) but no mirror has the 2025-26 eval season —
  api-web allowlist still required; cricsheet/aussportsbetting still
  blocked (owner has approved allowlisting; not yet in effect); TheTilt is
  a pipeline template, not a data source. Programme state: WNBA prospective
  test accruing via data-only archiver; BBL/soccer/NBA holdouts all
  unspent, each waiting on genuinely new inputs.
- **2026-07-31 (session 3)** — allowlist opened (cricsheet, api-web,
  wayback; aussportsbetting still 403 but Wayback covers it). Cricket:
  six-league Cricsheet ingest, player model registered → dev gates passed,
  **reserved holdout FAILED (+0.052, t=3.6, ROI −34%) → control #3
  confirmed**; benchmark n=297 recorded as structurally too small; IPL
  odds queued as Stage-D scrape. NHL: pipeline ported (panel/modelset/
  families), 55k-prop benchmark, **control (+0.0191 both iterations)** with
  shots/blocked individually inside the gate; holdout unspent.
  **Every runnable market has now been through the gauntlet.** Open
  threads: WNBA `fp-prospective-1` (accruing, verdict ≈ season end), the
  NHL shots/blocked cell and NBA injury-report Stage D (each would need a
  fresh registration against its unspent holdout), IPL odds scrape.
- **2026-07-31 (session 6)** — Market 1b-v2 registered: possession-based
  game-model rebuild per the owner critique (one-ball usage conservation,
  usage–efficiency tradeoff, per-possession defense, spread benchmark
  first use). GV2 gates above pushed before any v2 code scores on dev.
- **2026-07-31 (session 6b)** — Market 1b-v2 run: ML head fails both
  iterations (control confirmed at the serious-effort level); spread
  head passes dev gates → `fp-games-prospective-1` registered on games
  2026-08-01+ (spread open, season-end scoring, no betting). Home-adv
  collapse (3.3 → ~1.3 pts) identified and documented; β wrong-sign
  artifact recorded.
- **2026-08-06 (news-watch session)** — Settlement unblocked: wehoop had
  published nothing since games of 2026-08-01, leaving 20 finished bets
  (8/3–8/5) stuck `open` and 3 days from being wrongly voided as
  "no box score". Added `wnba/src/fetch_espn_box.py` — ESPN finals for ET
  dates wehoop lacks, archived to committed `data/raw/espn_box/`, merged by
  `settle_bets.py` for uncovered dates only (wehoop reclaims a date when it
  publishes it). Validated before use: 2026-08-01 overlap reproduced wehoop
  exactly (48/48 rows, zero stat/abbr/DNP diffs); reconstructed points
  reconcile to official finals in all 24 team-games of 8/2–8/5; ET date
  assignment matches wehoop's schedule on all 12 games incl. UTC rollovers.
  All 20 bets settled (+$1.32, bankroll $97.00). Also hardened: the 3-day
  auto-void now requires the game to be covered by a box feed, so a data
  outage can never book itself as a DNP. **Settlement-only** — `features.py`,
  `talent.py`, `grade_props.py` stay wehoop-only, so `fp-prospective-2`
  scoring is untouched. Open issue: the model panel `fp_live.py` projects
  from is still frozen at 2026-08-01 for the same upstream reason; extending
  the fallback to the panel would mean reconstructing ~100 feature columns
  from a second source and is NOT done — an owner decision, not a routine one.
- **2026-08-04 (session 2b)** — T3 systematized per owner approval:
  `avail_watch.py` point-in-time availability/lineup capture into
  committed `data/raw/avail/` with structured diffs; PROTOCOL
  news-watch step 1 rewritten around it; ESPN 403 header bug fixed in
  `news_watch.py` (the routine had been silently source-blind). First
  snapshot committed. QC-gate-before-training rule recorded.
- **2026-08-04 (session 2)** — Minutes engine built and gated:
  **M-G1 PASS** (walk-forward MAE 4.818 vs blend 5.096, tuned pre-2015;
  team-iteration leakage caught and fixed before dev), **M-G2 FAIL both
  iterations** (+0.01257 frozen cal; +0.00009 engine-aware recal vs
  required < −0.00068) → not adopted, no fp-prospective-3, live arms
  untouched. Phase diagnostic: helps May–mid-Jun, hurts Aug–Oct.
  Lesson recorded: the minutes prize is an information prize (T3
  lineups/news), not an estimator prize.
- **2026-08-04** — Minutes decomposition diagnostic (post-hoc, above):
  oracle minutes → dev gap −0.068; model-vs-open loss concentrated in
  the |minutes error| > 7 tail; minutes blend MAE 4.18. Minutes engine
  programme proposed (share-of-team-minutes Kalman + structural
  covariates + lineup capture); gates NOT yet registered — awaiting
  owner go, then gates push before any engine code runs.
- **2026-08-02 (settlement session)** — Data refresh (wehoop + BP
  archive, 60 new offer files for events 2692-2695), settled the 5
  2026-08-01 bets with CLV stamped, regenerated RESULTS.md +
  docs/index.html. First positive-CLV day (raw +0.16%, shade-adj
  +2.38%). Recorded the `fp_live.py` stale-player hole surfaced by the
  Kelsey Plum fill (details in the live-betting section above); the fix
  is left for an owner decision because it changes what gets bet. Still
  open: the 7 bets on the 2026-08-02 slate, which need the next
  wehoop refresh and the 2696/2697 closes.
- **2026-08-08 (audit session)** — First-week audit delivered (findings
  in the live-betting section above); owner paused `news-watch` at the
  trigger level and directed the fix set: pick gates + reinstated
  opener-only rule (`fp_live.py`), shade-table fix + no-move share +
  calibration z on the scoreboard (`settle_bets.py`,
  `site/build_site.py`), ESPN fallback extended from settlement into
  the model panel (`fetch_espn_box.py` schema 2 + `features.py`;
  validated 197/197 player rows on the 7/30–8/1 wehoop overlap, 2 stat
  cells of ~3,700 differ from one late official scoring change), and
  role-change advisory flags in picks (model changes remain owner+
  strong-session territory — the routine flags, never fixes). Re-bet
  repetition deliberately NOT gated (owner: repeated disagreement may
  be a real edge). fp-prospective-1/2 registrations untouched.
- **2026-08-08 (audit session, later)** — Owner: measure the open
  slates, restart the routine with updated instructions, push to main.
  `news-watch` prompt rewritten around the amendments (archive every
  firing — edge-watch's duty folded in; panel refresh from either box
  source; gates respected in notifications; gated rows listed
  separately; ROLE_*? flags surfaced for owner review, never acted on)
  and RE-ENABLED. Audit branch merged to main. The 17 open bets (8/8–
  8/9 slates) settle via the restarted routine's sweep.
- **2026-08-10 (news-watch firing + owner follow-up)** — Routine firing
  rebuilt the entire model state from scratch (fresh container: deps and
  every `data/*.pkl` absent), wehoop still stalled at 2026-08-01 with the
  ESPN fallback carrying the panel to 08-09, no `PANEL_STALE`. Then the
  owner filled Hamby rebounds over 6.5 @ -136 on a player-game that
  already carried an open points bet, and asked that it not repeat.
  **Gate 6 `PLAYER_ALREADY_BET` added to `fp_live.py`** (PROTOCOL
  Amendments): the one-bet-per-player-per-game cap has been in the
  2026-07-31 registration all along, but was only enforced *within* a
  sheet — `already_bet` matched the exact pick key, and the
  `drop_duplicates(["player","date"])` sort puts playable rows first, so
  once the bet market went `already_bet=True` a different market on the
  same player-game outranked it and was offered as playable next firing.
  Now blocked against any *open* bet in the same event under another key.
  `log_fill.py` had already detected the case and written it to `notes`
  but printed nothing; it now prints a `WARNING`, and the blocking flag
  means such a fill needs an explicit `--stake`. Verified by reproducing
  the exact pre-fill state (rebounds row correctly withheld) and by a
  synthetic second bet on A'ja Wilson (her clean $1.50 playable row
  flipped to play=False with the reason on the sheet); `bets.csv`
  restored byte-identical from the committed log afterwards.
  **This is a defect fix, not a model change** — no projection, price or
  EV moved, only which rows are presented as playable, so no fresh
  registration is implied. **Distinct from the 2026-08-08 decision to
  leave re-bet repetition ungated**: that concerns betting the same
  player/market/side again, which stays allowed; this concerns a *second,
  correlated* market on one player-game. Both Hamby bets stand (owner's
  call, recorded in the row's notes).
- **2026-08-13 (site redesign, owner-directed)** — `docs/index.html`
  rebuilt around the one live market. Soccer moved off the main view into
  an Archive tab (old `#soccer`/`#wnba` links alias across); the Live tab
  gains cumulative P&L vs CLV-expected P&L and running mean CLV
  (raw + CLV*) time charts with dashed process-change rules at 2026-07-31
  (v3 live) and 2026-08-08 (pick gates), a before/since-the-gates
  comparison, a filterable bet log (market/side/result/era/player, live
  summary), and a CLV* column the page had been missing. Presentation
  only — no metric definitions changed; generator is still
  `site/build_site.py` (markers live in its `EVENTS` constant).
- **2026-08-17 (limits research, owner-directed)** — Question: can
  FanDuel betting limits be scraped as a market-efficiency proxy?
  Findings: BettingPros carries no limit data anywhere (verified live
  against `/offers`, `/markets`, `/books` — no limit/stake/wager fields);
  no public FanDuel endpoint publishes limits either; the only readout is
  the authenticated betslip, which reveals the max wager when an
  oversized stake is typed (owner-confirmed) — and that number is
  `min(house market cap, account cap)`, so it degrades silently if the
  account gets profiled. Built `tools/fd_limits/` (owner-machine-only
  Playwright probe): types $1M into a small stratified sample of markets,
  records the revealed max to `fd_limits.csv`, and archives every
  betslip-related API response so a direct-API version can follow; hard
  guard against ever placing a bet; deliberately unscheduled and outside
  every routine. Decision gate written into its README: if the max is
  flat across markets, the signal doesn't exist at FD — stop probing.
  Also recorded: the committed BP offers archive keeps 11–14 books per
  prop, so cross-book count/dispersion/vig/open→close-move efficiency
  proxies are already buildable with no new scraping; and this remote
  environment egress-blocks fanduel.com and pinnacle.com (whose guest API
  publishes true per-market `maxRiskStake` — the standard limit signal,
  US-geo-blocked, unverified for WNBA props). Research only; no live
  implication, no model change. **Protocol amended the same day (owner
  instruction): "Limit capture" section added to `wnba/live/PROTOCOL.md`**
  — for playable (ungated) picks the owner captures the betslip max
  wager at the slip, sessions log it to `wnba/live/limits.csv`
  (hand-append log, schema in header). Observation only, pre-registered
  as such: never a gate, never a model input, never changes what gets
  bet without a fresh registration.
- **2026-08-17 (site additions, owner-directed)** — presentation only,
  no metric definitions changed (`site/build_site.py`): **"The record,
  sliced"** block on the Live tab — the bet log cut by market, by
  over/under (the split that caught the unders failure, now permanently
  visible), and by claimed-EV bucket (the live audit of `ev_claimed`
  against results; dev said claims realize ~half) — all using the era
  cards' aggregation so every number reconciles with the log filters;
  **tip times (ET)** on the "On the sheet now" table; and an **Observed
  FanDuel limits** table that renders only once `wnba/live/limits.csv`
  has rows, so the new protocol duty has a home on the scoreboard
  without empty scaffolding meanwhile.
- **2026-08-17 (site: Players tab, owner-directed)** — `docs/index.html`
  gains a "What the model thinks" view built from the committed
  projection archive (`wnba/live/projections.csv`, the sheet
  `fp_live.py` appends every news-watch firing): one row per player with
  the latest news-adjusted per-game projection per market
  (PTS/REB/AST/3PM/PRA) shaded by league percentile among players priced
  in the last 14 days, a season-arc points sparkline, leaderboard cards,
  and a one-claim-per-player "argues with the market" list from the
  newest firing (claims above 25% rendered quarantined, mirroring the
  pick gates). Display semantics: an OUT override zeroes `mu_news` on
  the sheet, so the player view falls back to `mu_base` for that row and
  shows an OUT chip — availability news is not a talent opinion; players
  unpriced for over 14 days render unshaded + "stale" (the `fp_live`
  staleness gate, mirrored). Presentation only — no metric definitions
  changed, no model touched; generator still `site/build_site.py`
  (stdlib-only, deterministic, timestamps from the data). The tab
  refreshes on the normal `refresh_site()` path (settlement / fill
  logging / manual rebuild), so it can lag the hourly sheet between
  settlements; wiring a rebuild into the news-watch routine would be a
  routine change and stays an owner decision.

- **2026-08-19 (live-path repair, news-watch firing; no model change)** —
  the 09:31 ET firing hit a fresh container (all derived pickles are
  gitignored) plus a flaky upstream, and both had to be fixed before a
  sheet could be produced. (a) **BettingPros 504s**: a 40-request sweep
  of the offers endpoint returned `HTTP 504 Gateway Timeout` on 7 of 40
  calls, randomly spread across events/markets, and `live_pipeline.get()`
  had no retry — one failure aborted the whole fetch, so `fp_live.py`
  produced nothing on four consecutive attempts. `get()` now retries 5×
  with 2/4/8/16s backoff at a 45s timeout and still **raises** if a
  request never succeeds: a partial offer set would silently change which
  rows survive the one-bet-per-player-game cap, so it must fail loudly
  rather than skip. No projection, price, EV or gate is touched by this.
  (b) **Container rebuild**: `data/panel.pkl` and friends were absent, so
  the firing ran the full chain (`fetch_wehoop` → `fetch_espn_box` →
  `build_props` → `grade_props` → `features` → `talent --build` →
  `build_modelset`) before pricing; the panel came back current through
  2026-08-18 (106,332 player-games) and no `PANEL_STALE` flag appears on
  the sheet. Python deps (numpy/pandas/scipy/scikit-learn/pyarrow) also
  had to be installed in the container.
- **2026-08-24 (new-market exploration, owner-directed)** — Registration
  N pushed: NHL talent-engine revisit of the shots/blocked cell, gates
  above registered **before any engine code exists** (data-source QC
  gate, pre-2019-07-01 tuning era, N-G1 market-free vs the incumbent EW
  blend, N-G2 dev vs the recomputed Stage-B baseline, and a holdout
  spend condition of dev ≤ 0.000 — the Market-1 lesson made a rule).
  Exploration section added: Polymarket egress-blocked (cricket proxy
  route needs an owner allowlist or the Betfair download; proxy caveats
  recorded), NBA Stage D queued behind NHL, NHL game markets noted as
  the follow-up candidate. Eval-era NHL fetch rerun clean (2,788 games,
  0 failed); training-era 2010-11–2023-24 api-web fetch + QC next, then
  the engine build.
- **2026-08-24 (registration N run + verdict)** — QC PASS (17,799/17,799
  games, dual-source 99.9–100%); N-G1 PASS 5/5 (engine beats the EW
  blend on every stat, cell = shots+blocked); N-G2 iteration 1 PASS on
  the improvement gate (dev cell +0.00779 → **+0.00227 t=2.3**, blocked
  at parity +0.00068 t=0.4, cal −0.43pp, cell EV>5% ROI +0.24% t=0.1 =
  noise, placebo 0 bets, no tripwire); iteration 2 (linear μ recal) not
  adopted (no improvement, calibration worse). **Spend condition dev ≤
  0.000 not met → Stage C not run, 19,924-prop holdout preserved.**
  NHL moves control → "striking distance, unproven"; the residual gap
  diagnosed as information (role/TOI news), reproducing the WNBA M
  lesson. Forward paths + owner actions recorded in the Market 4
  revisit section and the exploration section.
- **2026-08-24 (registration N2 run + verdict)** — pbp attempts data
  fetched (20,591 games, 0 failures), QC PASS (identities ≥ 99.94%,
  attempts/SOG 1.84–2.10×; true-zero semantics fix caught pre-tuning);
  N2-G1 PASS (1.92591 vs N 1.93476 vs EW 1.96963); N2-G2 it.1 pooled
  +0.00149, it.2 (--disp threshold-likelihood dispersion) **+0.00136
  (t=1.40)** adopted — the cell is now statistically indistinguishable
  from the opener, but the spend condition (≤ 0.000) is honestly unmet
  → **holdout unspent**, all iteration budgets closed. Session arc:
  +0.00779 (t=5.72) → +0.00227 (t=2.30) → +0.00136 (t=1.40). Next
  paths recorded (information layer; 2026-27 prospective arm = owner
  decision on archiving NHL odds).
- **2026-08-24 (lessons-learned doc, owner-requested)** — `LESSONS.md`
  added at the repo root: the programme's cross-market lessons distilled
  from AUDIT.md, this file, the subproject READMEs and the live record
  (measurement artifacts, zero-skill placebos, CLV's non-zero break-even,
  calibration-z as the fast tripwire, regime dependence, gates-as-code,
  stale-data-looks-like-edge, invariants against full history). Documentation
  only — no model, gate, metric definition or generated file touched. The
  post-gates snapshot quoted in it (112 settled, 66W–46L, +14.9% ROI,
  calibration z −0.23, raw CLV −2.98% vs the registered ≈ −3%) was
  recomputed this session from `bets.csv` and reconciles with RESULTS.md.

- **2026-08-28 (news-watch firing, container rebuild)** — The 23:35 ET
  firing landed in a fresh container: Python deps (pandas, pyarrow, scipy,
  scikit-learn) had to be installed and every gitignored derived file was
  absent, so the full chain ran before pricing (`scrape_bettingpros` →
  `fetch_wehoop` → `fetch_espn_box` → `build_props` → `grade_props` →
  `features` → `talent --build` → `build_modelset`), same as 2026-08-21.
  Panel came back current through 2026-08-26 (106,790 player-games) and no
  `PANEL_STALE` flag appears on the sheet. Settlement sweep printed
  `NOTHING_TO_SETTLE` (no `BOX_FEED_BEHIND`, no `SHADE_UNAVAILABLE`); the
  one pre-today open bet, DeWanna Bonner assists under 1.5 for 2026-08-25,
  has no box row in either feed and sits inside `settle_bets.py`'s 3-day
  grace window, so it stays open until a later firing settles or voids it.
  Nothing in the model, the gates or the pricing path was changed.

- **2026-08-29 (new market, owner-directed: "apply the WNBA lessons to
  another market … back test")** — Screen from this machine (statsapi,
  SBR, Polymarket reachable; BP key covers no new sport): **MLB pitcher
  props chosen** over NFL (one underpowered season, opens 09-10) and NBA
  (late October, Stage-D job). Stage A complete on the existing pipeline:
  `fp_benchmark.py` gains the MLB split (dev 2025 / holdout 2026) and
  venue columns; benchmark table + **registration K** (QC gate, engine
  class, K-G1/G2/G3, Stage-C spend condition, FD cell, prospective arm,
  Stage-D list) pushed **before any engine code**. Training-era statsapi
  fetch (2015–2022) started; QC next, then `talent_mlb.py`.
- **2026-08-29 (registration K run + verdict)** — Training-era statsapi
  fetch 2015–2022 complete (0 failures); **QC gate caught the
  Completed-Early skip in `fetch_mlb.py`** (59 games), fixed, re-run →
  PASS; K-G1 PASS on K (−4.3% MSE) and outs; K-G2 it.1 +0.01924 (t=6.2)
  vs gate ≤ +0.010 → FAIL; it.2 (linear μ recal + windowed outs
  distribution, pre-odds) +0.02150 → not adopted; spend condition unmet
  → **holdout unspent, MLB pitcher props = control at the serious-effort
  level.** Code: `props/src/talent_mlb.py`, `fp_model_mlb.py`,
  `qc_mlb_hist.py`, `fetch_mlb_people.py`; `fp_benchmark.py` MLB split.
  Forward paths recorded (prop-shaped calibration population, Statcast
  observation model, lineup capture). Cricket odds-source hunt started
  the same session (owner: "try the cricket thing in stages").
