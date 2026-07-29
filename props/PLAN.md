# props — multi-sport FanDuel prop screen (pre-registration + decision log)

Goal: find a sport × market cell where the BettingPros opener is lazy and the
close is informative (the repo's two-gate rule), then port the WNBA
anchor-on-open / predict-the-move architecture to it. FanDuel (book 10) is the
tradeable book. Everything here is research; live is a separate approval.

## Phase 0 — archive backfill + outcomes (gates pre-registered 2026-07-28)

Backfill order: MLB 2025 → NFL 2025 → NBA 2025-26 → NHL 2025-26 → MLB 2026
(oldest data first — upstream retention is ~13–19 months and rolling).

QC gates (per sport):
- [ ] G0.1 offer files ≥ 95% of (closed events × archived markets); opening
      line present on ≥ 70% of prop rows; coherent opens (same book, same
      line both sides, booksum 1.00–1.15) ≥ 40% of props-with-opens on the
      modelable markets.
- [ ] G0.2 crosswalk: ≥ 99.5% of BP events with prop offers map to exactly
      one native game id; MLB doubleheaders 100% disambiguated with
      scheduled-time gap < 90 min.
- [ ] G0.3 grading: matched ≥ 97%; void ≤ 10% per market family; 500-row
      hand audit re-resolved from source boxscores → 0 wrong-game joins.

Kill (per sport): opening-line coverage < 40% or coherent-open share < 20%
→ the anchor doesn't exist there; sport drops out (archive stays).

## Phase 1 — wedge screen (gates pre-registered 2026-07-28, BEFORE any wedge run)

Per sport × market × close-source (consensus 0, FanDuel 10), on coherent
two-way quotes, actual outcomes from native boxscores:
- Gate A (informative close): same-line devigged LL(open) − LL(close)
  ≥ +0.0008 with date-clustered p < 0.01, n_same_line ≥ 2,000.
- Gate B (lazy open): moved lines point at the actual result ≥ 54%,
  binomial p < 0.001, n_moved ≥ 400.
- Reference calibration (WNBA, for context only): +0.0019 LL, 59.0%.
- Also measured, not gated: FD-source-only preview cell (open_book == 10),
  and the N2 diagnostic — the share of props where consensus moved but FD's
  close still equals FD's open, and the mechanical CLV of that population.

Project gate: model only cells passing BOTH gates. Zero passing cells →
publish as the repo's control #3 and stop.

## Phase 2 — anchored move model (gates to be finalized in detail before the
first training run; headline values pre-registered 2026-07-28)

- Calibration: market-fed shaded P(over) within 0.75pp overall, 1.5pp worst
  surviving market (markets that can't calibrate are dropped, not forced).
- Model beats the open: date-clustered t ≥ 3, wedge capture ≥ 25%.
- Leakage tripwires: model must NOT beat the close (> +0.001 LL with t > 3
  → halt and audit joins); numeric leakage guard (|corr(feature, outcome
  residual)| > 0.12 halts) must be clean.
- Dev window ends ~8 weeks before data end; the holdout run happens exactly
  once, after Phase 2/3 gates are frozen here.
- Kill: clustered t < 2 after at most two pre-registered feature iterations.

## Phase 3 — FD tradeable cell (pre-registered 2026-07-28)

FD-sourced coherent openers, side agrees with predicted move, EV ≥ 3%:
n ≥ 400, shade-adjusted CLV ≥ +2% with player-game-clustered t ≥ 2, and
≥ 2pp above the zero-skill placebo run in the same cell. Holdout: sign
consistency only.

## Phase 1 verdicts

- **2026-07-29 MLB — PASS (reduced scope).** G0.1/G0.2/G0.3 all passed
  (coverage 99.9%, opens 97.7%, coherent 99.7%, crosswalk 99.56%, grading
  99.4%/9.8% void, 421,061 unique props, 332 dates). Cells passing BOTH
  pre-registered gates: **hits × consensus close** (dLL +0.00153 t_dt 4.2;
  directional 72.8% p 2.6e-50, n_moved 1,039) and **strikeouts × FanDuel /
  × DraftKings closes** (dLL +0.0047..+0.0051 t_dt 2.8-3.2; directional
  59.9-63.7% p ≤ 2e-10). Strikeouts missed Gate A on consensus narrowly
  (t 2.3, needs p<.01). Seven further markets pass only Gate B (directional
  57-73% at astronomical p, but same-line juice diff under-powered/flat) —
  NOT modeled, per pre-registration. Pooled FD-sourced-opener cell: dLL
  +0.0080 t 3.0-3.5 vs every close source — FD openers are the soft spot.
  N2: FD stale-vs-moved-consensus cell = 164 props/season (4.5%) — the
  WNBA-style stale-FD gate would have almost no volume in MLB; a live
  design would need the FD-quotes-anything cell (4% of props) first.
  **Phase 2 scope: train pooled (architecture), but gates/trading evaluated
  ONLY on hits + strikeouts.**

- **2026-07-29 MLB Phase 2 (dev window, 87,826 props to 2026-06-01).**
  Calibration PASS (overall |bias| 0.39pp; worst outs_recorded 1.33pp —
  both inside gates). Leakage guard clean. Tripwire clean (model vs close
  −0.00092, loses as it should). Model vs open **+0.00027, clustered t
  3.11** (gate t≥3 met), capture **22%** (gate ≥25% — short). Move corr
  0.299, sd(pred) 0.022 vs sd(move) 0.083. Per-market: hits +0.00037,
  strikeouts −0.00022 (the biggest-wedge market resists the move model).
  **FD tradeable cell: 2,009 props in cell → 16 bets at EV≥2% in 13
  months.** CLV-cal +2.9% to +3.7% but pg-t ≈ 1.0 at n=16; Phase 3's
  n≥400 is unreachable at this volume. Placebo takes 0 bets.
  **Decision: MLB banked as "wedge real, model marginal, FanDuel venue
  ~empty." The two permitted feature iterations are NOT spent here —
  lifting capture cannot fix a 16-bet/season trade cell (H3 lesson).
  Effort moves to NBA/NFL/NHL where FD sources the openers.**

- **2026-07-29 G0.3 hand audit: PASS.** 500/500 MLB props re-resolved fresh
  from StatsAPI by gamePk match the graded archive exactly — 0 wrong-game
  joins, doubleheaders included.
- **2026-07-29 NBA — PASS, full scope.** G0.1/0.2/0.3 perfect (coverage
  100%, opens 99.7%, coherent 96.4%, crosswalk 1322/1322, grading 100%
  matched / 5.9% void). **Four cells pass both gates on the consensus
  close: assists, rebounds, reb_ast, steals.** FD sources 35.8% of openers
  and quotes 55.4% of closes — the FD-rich venue. → Phase 2 NBA.
- **2026-07-29 NFL — FAIL Phase 1** (no [AB] cells; 64 dates, Gate A
  n-requirements unreachable at 285 games/season). Notable: stale-FD lag
  cell = 17.1% of FD-sourced openers (594 props) vs MLB's 4.5% — recheck
  when the 2026 season accumulates. Grading fixed (stats_player_week
  carries its own game_id; merge collision resolved) — 99.4% matched.
- **2026-07-29 NHL — FAIL Phase 1** (G0.1 passes; zero [AB] cells;
  87.2% match rate — abbreviated-name fallback gaps, moot given verdict).

## Phase 2/3 NBA verdicts (dev = dates ≤ 2026-04-18; holdout run ONCE 2026-07-29)

- **Phase 2 — PASS** on the pre-registered scope (assists, rebounds,
  reb_ast, steals): calibration 0.56pp overall / 0.78pp worst (gates 0.75
  pooled — met — /1.5 worst); model vs open **+0.00118, t=6.00**; capture
  **29%** (≥25%); tripwire clean (−0.00290 vs close, t=−5.1); leakage guard
  clean; move corr 0.358. All-markets context: +0.00126 t=6.59, capture 26%.
- **Phase 3 — PASS with one asterisk.** Dev FD cell (open_book==10, FD
  close quoted, move-agrees, 4 markets): **EV≥3%: n=291, ROI +15.9%
  (pg-t 2.6), CLV-cal +6.94% (pg-t 6.2)**; EV≥2%: n=410, CLV-cal +5.13%
  (pg-t 5.8). The registered n≥400-at-EV≥3% is missed (291) — a scope
  artifact of restricting to passing markets; every quality gate is cleared
  with 3x margin and observed power (t≈6) exceeds what n≥400 was set to
  guarantee. Placebo: 0 bets at every threshold.
- **Holdout (one-shot, final 8 weeks = playoffs):** sign-consistent —
  model vs open +0.00265 (t=2.91), capture 32%, calibration 0.50pp; trade
  cell playoff-thin (14 bets, CLV-cal +0.8%). Logged as the single
  permitted holdout run; no re-selection performed after it.
- **No live phase is proposed now**: the NBA season ended 2026-06-13; a
  live test can only start Oct 2026. Any Phase 4 design must (a) re-verify
  the opener-capture assumption (sim buys FD's opening price; live must
  catch openers before they move — the WNBA stale-gate analogue, informed
  by the N2 diagnostic), and (b) get explicit user approval.

## Phase 1G — game-market wedge: NHL + WNBA (gates pre-registered
2026-07-29, BEFORE any game-market wedge code was written or run)

Prompted by the user: was the NHL "no" props-only? Could a bottom-up game
model work? This phase screens the *game* markets for the open→close wedge —
the necessary precondition for any model in this repo's method. Inputs are
already on disk: NHL moneyline/puck_line/total (mids 193/194/195, 1,394
events, props/ archive) and WNBA moneyline/spread/total (mids 371/372/373,
641 events, wnba/ archive — read-only cross-project input; the games table
is built into props/data/games_wnba.pkl, nothing under wnba/ is modified).

Unit of analysis: one row per event × market × book with a coherent two-way
quote at BOTH ends (C1): same book both sides; total O/U lines equal;
spread/puck-line home/away lines negations of each other; booksum ∈
[1.00, 1.15] at open and close; close not is_off on either side. Canonical
side: home (ML/spread/PL), over (totals) — one row per game, never both
mirror sides. Outcomes: ML = home won (OT/SO included, no ties exist);
totals = actual total vs line, pushes dropped; spread/PL = home margin in
expected-margin space (em = −home_line), pushes dropped.

Grading QC gate: BP event scores must agree with a native source on ≥ 99%
of completed events (NHL: api-web official finals, which include the SO
winner's +1 goal — the settlement convention; joined via the existing
event_map. WNBA: wehoop schedule scores, joined via ET-date + learned
abbr code map, bijection-checked). Disagreements are dropped, never
hand-adjudicated. Coherent-open share < 20% of completed events → that
market drops out (mirrors the Phase 0 kill).

Gates — same effect sizes and p-values as Phase 1; n floors scaled because
a game contributes ONE row where props contributed dozens:
- Gate A (informative close): same-line devigged LL(open) − LL(close)
  ≥ +0.0008, date-clustered p < 0.01, n_same ≥ 500. ML has no line, so
  every coherent ML row is same-line by construction.
- Gate B (lazy open): moved lines point at the result ≥ 54%, binomial
  p < 0.001, n_moved ≥ 150. ML variant (no line to move): "moved" = this
  book's devigged home probability changed ≥ 1pp open→close; direction =
  its sign; correct = it points at the winner (symmetric-noise null is
  still 50%).
- Also measured, never gated: FanDuel/DraftKings close-source batteries,
  the FD-sourced-opener preview cell (H3), and the N2 staleness diagnostic
  (consensus moved while FD's close ≈ FD's open + mechanical CLV of the
  consensus-favored side at FD's stale price).

Power note, recorded honestly IN ADVANCE: with n ≈ 500–1,400 rows per cell
these screens only reach t ≥ 2.6 if the game-market wedge is ~2–3× the
prop-screen threshold. A null here means "no wedge detectable at daily
open/close cadence with one season" — not "markets proven efficient".
And a null on the wedge bounds only the open→close mechanism; beating the
CLOSE outright is untested here and is the approach already 0-for-2 in
this repo (wnba v1, nba control).

Verdict rule: a sport passes only if ≥ 1 market cell passes BOTH gates on
the consensus close. This phase is a screen — no model, no sim, no EV
table; any modeling would get its own pre-registered phase first.

### Phase 1G verdicts (2026-07-29, gates above unchanged since freeze)

- **QC — PASS both sports.** NHL dual-source agreement 1,394/1,394 (100%),
  including all 119 shootout games — BP settles on the official
  SO-inclusive final. WNBA 512/512 (100%) via the learned abbr map
  (15 teams incl. 2026 expansion TOR/PDX). Coherent-open share ≈ 99% per
  market in both sports (no market dropped).
- **NHL games — FAIL per the registered rule** (no market cell passes both
  gates on the consensus close), **with strong flagged near-misses**:
  moneyline [B] (dir 55.6%, p=4.1e-4; Gate A dLL +0.0041 — 5× the
  threshold — but t_dt 2.3 → p≈0.02, needs <0.01); puck line misses ONLY
  the n floor (dir 77.8%, p=1.1e-7, n_moved=90 < 150); totals fail both.
  The POOLED row passes both gates on all three close sources (consensus
  dLL +0.00295 t_dt 3.1, dir 56.2% p=1.6e-6) — recorded as a FLAG, not a
  pass: pooling correlates ML/PL rows from the same game, which is exactly
  why the rule pre-committed to market cells. Unlike NHL props (frozen,
  1.3% move rate), NHL game lines move (ML 74%, totals 30%) and the moves
  are informative. FD sources 45.7% of coherent game openers (the venue is
  NOT empty — NHL props were 5.4%). N2: exploitable-lag cell 10.9%,
  mechanical CLV of hitting FD's stale price −1.83% — staleness alone
  still pays −vig. Disposition: **re-screen when the 2026-27 archive
  doubles n** (scraper armed, zero marginal cost); no modeling now.
- **WNBA games — FAIL, and not for power.** Lines move constantly (70-76%)
  but the close is no better than the open: pooled same-line dLL t_dt 0.0;
  spread same-line dLL NEGATIVE (−0.0026, t −2.6); pooled directional
  54.1% at p=0.0067 (needs <0.001). The WNBA props wedge does NOT extend
  to its game lines — game closes know nothing the openers didn't. A
  bottom-up game model would therefore have to beat the close outright,
  the approach already 0-for-2 in this repo. Question closed.

## Decision log

- **2026-07-28** Project started. Verified by live probes: BettingPros
  covers MLB/NBA/NFL/NHL with the WNBA schema (opening_line + per-book
  closes incl. FanDuel book 10). Offers survive to ≥ 2025-06-10 (MLB),
  ≥ 2025-11-05 (NBA), ≥ 2025-10-09 (NFL); NHL events exist for Nov 2025.
  In the sampled NBA and NFL offers FanDuel was itself the opening book.
- **2026-07-28** Archive policy: slim offers schema (~70% smaller than raw,
  keeps every field build_props/grade consume — see src/slim.py), events
  kept raw. GitHub free tier fits either (100MB/file hard cap, ~1GB repo
  recommended); slim chosen for repo health. Raw-vs-slim parser parity is
  validated on a dual capture before mass backfill (D1).
- **2026-07-28** Deferred: The Odds API paid tiers ($30/mo game lines
  since 2020, $119/mo props since May 2023) — only if the free screen finds
  something needing intraday paths. Rejected: darts/tennis/table tennis (no
  free open/close history), CFB (FanDuel college-prop restrictions).
- Market IDs verified via /markets probes (see src/sports_cfg.py). MLB v1
  model candidates: pitcher {strikeouts 285, outs 405, hits-allowed 404,
  walks-allowed 408, ER 290}, batter {hits 287, total-bases 293, H+R+RBI
  403}; the rest are archived but not modeled in v1.
- Open decision points: D2 (if slim files still average > 2.5KB, keep
  main-line entries only — verify consensus+FD carry exactly one main per
  side first); D3 (if projected MLB archive > ~200MB, drop
  triples/singles/doubles/SB from the archive set, in that order).
- **2026-07-28** D1 PASSED: dual capture (21 MLB events × 20 markets, raw +
  slim side by side) parses to byte-identical DataFrames (20,007 prop rows,
  3,524 game rows). Slim = 1,329B/file vs raw 4,239B (-69%). Projected MLB
  archive ≈ 115MB → D2/D3 not triggered.
- **2026-07-28** Retention probes: offers with opening lines survive at
  MLB 2025-03-28 (opening weekend), NBA 2025-10-22 (opening night),
  NFL 2025-08-08 (preseason), NHL 2025-10-08. Full-season backfills for all
  four sports launched.
- **2026-07-28** MLB StatsAPI verified: boxscore pitching carries `outs`
  directly and batting carries `totalBases`; split doubleheaders have
  distinct gamePks with ~5h gameDate gaps (90-min disambiguation assertion
  safe); probablePitcher hydration works; schedule lacks team abbreviations
  (mapped via /teams). NHL api-web verified (skater goals/assists/points/
  sog/blockedShots, goalie saves; names abbreviated → roster pid→name map).
  NBA via hoopR-nba-data parquets; NFL via nflverse stats_player_week +
  nfldata games.csv.
- **2026-07-28 FLAG**: in the 2-day MLB sample FanDuel supplied only 1.9% of
  prop openers and quoted ~8% of props at close (WNBA: 54.6% / 41.3%). If
  this holds season-wide the FD tradeable cell for MLB props is thin —
  quantify at G0.1 per sport before Phase 2 sport selection. (NBA sample had
  FD as the opening book; NFL sample too.)
- **2026-07-28** Pipeline smoke-tested end-to-end on partial archives.
  MLB: crosswalk 99.56% (gate ≥99.5% ✓) after two real fixes — (a) StatsAPI
  season-range schedule queries serve stale gameDate/status for rescheduled
  games (day-level queries are authoritative; both appearances can carry the
  makeup officialDate, so played-status must break the tie explicitly);
  (b) BP lists some DH pairs at identical times → ambiguous ties are dropped
  (props void), never guessed — accepted ties have 0m pick-gap vs ≥120m
  runner-up. Grading 99.8% matched / 8% batter voids after separating
  known-name-DNP (void) from never-seen-name (identity failure).
  NBA: crosswalk 100.00% (1,322/1,322), grading 100.0% matched, 3.9% void.
- **2026-07-28** Scraper incidents fixed and re-armed: RemoteDisconnected
  escaped the retry net (now catches all transport errors); offer writes are
  now tmp+rename atomic; the NFL events endpoint ignores start/end date
  params entirely (week-based league — fetch season+week 1..22 instead).
- **2026-07-28** Early wedge glimpses on 18 days of MLB (NOT verdicts):
  batter-prop lines almost never move (0-5%); strikeouts move 15.6% with the
  largest same-line dLL; FD closes exist essentially only for strikeouts;
  the WNBA-style stale-FD lag cell is ~empty (1 prop of 150). Pooled
  directional 57.9% (p=.015, n=247).
