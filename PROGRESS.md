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
| 4 | NHL player props | `props/` | parked | **control** — G1 fail both iterations (+0.0191); shots/blocked individually inside the gate; **holdout unspent** |
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
