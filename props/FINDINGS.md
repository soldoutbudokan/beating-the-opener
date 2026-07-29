# props — consolidated findings

Complete record of the multi-sport FanDuel screen run 2026-07-28/29:
four sports of player props (Phases 0–3) plus the game-market follow-up
(Phase 1G). Every gate below was pre-registered in PLAN.md before the
phase that used it ran; nothing was moved after the fact. This file is a
summary — PLAN.md is the source of truth for gates, timestamps, and the
decision log; results/*.csv hold the full per-cell tables.

## The question and the method

Is there a FanDuel market whose **opener is lazy** and whose **close is
informative** (the repo's two-gate rule), with enough FanDuel presence to
trade? Method inherited from wnba/: anchor on the market's open, predict
the open→close move, never the raw outcome — both prior raw-outcome
attempts in this repo (wnba v1, the nba/ closing-line control) lost to
the market.

## Data assets built (permanent value regardless of verdicts)

- **BettingPros archive, 4 sports, 114,837 offer files** (slim schema,
  byte-identical parse vs raw, −69% size): MLB 2025-26 (81,400), NBA
  2025-26 (17,186), NFL 2025 (3,705), NHL 2025-26 (12,546). Upstream
  deletes seasons after ~13–19 months — this data is otherwise gone.
- Outcomes from native sources (MLB StatsAPI, hoopR, nflverse, NHL
  api-web), joined by **native game id through a crosswalk** — never
  name+date (AUDIT H1/C2). Ambiguous MLB doubleheader ties voided, never
  guessed. 500-prop hand audit vs fresh boxscores: **0 wrong-game joins**.
- Game scores dual-validated for Phase 1G: NHL 1,394/1,394 agree with
  official finals **including all 119 shootout games** (BP settles
  SO-inclusive, the betting convention); WNBA 512/512 agree with wehoop.

## Phase 1 — props wedge screen (per-sport verdicts)

| sport | passing cells | FD share of openers | verdict |
|---|---|---|---|
| NBA 2025-26 | **assists, rebounds, reb_ast, steals** | 35.8% | modeled (below) |
| MLB 2025-26 | hits; strikeouts (FD/DK closes) | 2.5% | wedge real, venue empty |
| NFL 2025 | none | 26.4% | underpowered (64 dates) — re-screen 2026 |
| NHL 2025-26 | none | 5.4% | props market is FROZEN (see below) |

- **NHL props' distinctive null**: lines move on only **1.3%** of 55k
  props (assists 0.06%). The close is a photocopy of the open (dLL t≈0).
  When lines DO move they point at the result 63% of the time — movement
  is informative, there just isn't any. Nothing for a move model to learn.
- NFL flag for later: a 17.1% stale-FD lag cell exists; one 285-game
  season is just too small to gate on.

## Phase 2/3 — the NBA result (the found edge)

Walk-forward anchored move model on the four passing markets, dev window
ending 2026-04-18, holdout run exactly once:

- Beats the open **+0.00118 LL, date-clustered t = 6.0**, captures 29% of
  the open→close wedge; calibration |bias| 0.56pp; does NOT beat the
  close (tripwire clean); leakage guard clean.
- FanDuel tradeable cell (FD-sourced opener, FD close, side agrees with
  the predicted move): **EV≥3%: 291 bets, ROI +15.9% (pg-t 2.6),
  shade-adjusted CLV +6.9% (pg-t 6.2)**; EV≥2%: 410 bets, CLV +5.1%
  (pg-t 5.8). Zero-skill placebo: **0 bets**.
- Holdout (playoffs): sign-consistent (+0.00265, t 2.9, capture 32%).
- Honest asterisk: the registered n≥400 at EV≥3% landed at 291 (the
  4-market scope shrank the cell); every quality gate cleared with margin.
- **NOT armed live.** Season ended 2026-06-13; any live design must first
  measure opener capture at live cadence (the sim buys FD's opening
  price) and get explicit approval.

MLB coda: the model beat the opener there too (+0.00027, t 3.1, 22%
capture) but FD quotes ~4% of MLB props → 16 tradeable bets in 13 months.
A right model in an empty venue — H3's lesson, not repeated.

## Phase 1G — game markets (NHL + WNBA), 2026-07-29

Prompted by: "was the NHL no props-only? could a bottom-up game model
work?" Screened NHL ML/puck-line/totals (1,394 games) and WNBA
ML/spread/totals (511 games), same gates, n floors scaled and
pre-registered with an explicit power note.

**NHL — FAIL per the registered rule, flagged for a free re-screen.**
Opposite market texture to NHL props: game lines MOVE (ML 74%, totals
30%) and the close is better sport-wide (+0.00295 LL, clustered t 3.1;
moves 56.2% directional, p=1.6e-6; consistent across consensus/FD/DK
closes). But no single market cleared its per-cell gates on one season:
- moneyline: directional gate passed (55.6%, p=4.1e-4); informative-close
  dLL +0.0041 (5× threshold) but t_dt 2.3 → p≈0.02, needed <0.01.
- puck line: missed ONLY the n floor — **77.8% directional (p=1.1e-7) on
  90 moves** vs the registered 150.
- pooled row passes both gates on all three close sources — recorded as a
  FLAG, not a pass (ML/PL rows from the same game are correlated; the
  rule pre-committed to per-market cells).
- FD sources **45.7%** of coherent NHL game openers — the venue exists
  (NHL props: 5.4%). N2: 10.9% of FD-sourced openers sit stale while
  consensus moves, but hitting the stale price without a model pays
  **−1.8% CLV** — staleness alone is still −vig.
- Disposition: the 2026-27 archive doubles n at zero marginal cost
  (scraper armed, idempotent). Verdict settles then. If ML/PL pass, a
  bottom-up player/goalie model becomes a candidate FEATURE SET for
  predicting the open→close move — never a raw-outcome model.

**WNBA — FAIL, and not for power.** Game lines move constantly (70-76%)
but the close is no better than the open: pooled same-line dLL t ≈ 0.0,
spread same-line dLL negative (−0.0026, t −2.6), pooled directional 54.1%
at p=0.0067 (needed <0.001). FD sources 62.9% of openers — plenty of
venue, nothing to trade. The WNBA props wedge does NOT extend to its game
lines.

## Cross-cutting lessons (the taxonomy this screen bought)

1. **Three market textures, and only one is exploitable by this method:**
   - *Frozen* (NHL props): openers never move; close = open; no wedge.
   - *Noisy movers* (WNBA game lines): constant movement, none of it
     informative; close ≈ open in skill; no wedge.
   - *Informative movers* (NBA props, MLB props, NHL game lines): the
     close beats the open — the only texture worth modeling.
2. **Wedge ≠ tradeable.** MLB had the wedge and no FanDuel venue; NHL
   games have the venue and (probably) the wedge but not the sample size.
   Both gates AND the venue must clear before a model is worth building.
3. **Stale prices pay −vig everywhere measured** (MLB, NHL games, WNBA
   games): lag populations exist (10-11%) but hitting them blindly loses.
   Any live edge needs the model's direction, not just the lag (AUDIT N2).
4. **Props are the lazy corner; game lines are the sharp one** — except
   in the NHL, where it inverts: props are frozen solid while game lines
   carry real information flow (goalie news, presumably).
5. Raw-outcome modeling stays 0-for-2 and untested here by design: every
   null in this file bounds the open→close mechanism only.

## Re-screen calendar

- **Oct 2026**: NBA archive re-arms (live decision is the user's, gated
  on an opener-capture study + PROTOCOL). NHL game markets re-screen as
  2026-27 data accrues (target: 2× n on ML and puck line).
- **Sep–Dec 2026**: NFL 2026 season doubles its archive; re-screen.
- MLB: no action — venue empty is not fixable by more data.
- WNBA game markets: closed.

## Pointers

- Gates, timestamps, decision log: `PLAN.md`. Write-up: `README.md`.
- Per-cell tables: `results/wedge_<sport>.csv`,
  `results/wedge_games_{nhl,wnba}.csv`.
- Pipeline: `src/` (scrape_bp → fetch_* → build_props / build_games_wnba
  → map_events → grade_props / qc_game_scores → wedge / wedge_games →
  features → build_modelset → train_eval).
