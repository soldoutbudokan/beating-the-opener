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
