# props — multi-sport FanDuel player-prop screen

**Question.** Somewhere in FanDuel's prop menus, is there a market whose
opener is lazy and whose close is informative — the repo's two-gate rule —
with enough FanDuel presence to actually trade it?

**Method.** Backfilled the BettingPros archive (free API, opening line +
per-book closes incl. FanDuel) for four sports before it rolls off
upstream: **MLB 2025+2026 (81,400 offer files), NBA 2025-26 (17,186),
NFL 2025 (3,705), NHL 2025-26 (12,546)** — 421k graded MLB props, 119k
NBA, 14k NFL, 33k NHL. Outcomes from MLB StatsAPI / hoopR / nflverse /
NHL api-web, joined by native game id through a crosswalk (no name+date
joins; ambiguous doubleheaders voided). All gates pre-registered in
PLAN.md before each phase ran; a 500-prop hand audit against fresh
source boxscores found **0 wrong-game joins**.

## Verdicts (Phase 1 wedge, pre-registered gates)

| sport | QC | wedge cells passing both gates | FD share of openers | verdict |
|---|---|---|---|---|
| NBA 2025-26 | 100% mapped/matched | **assists, rebounds, reb_ast, steals** | **35.8%** | **modeled — see below** |
| MLB 2025-26 | 99.6/99.4% | hits (consensus); strikeouts (FD/DK closes) | 2.5% | wedge real, venue empty |
| NFL 2025 | 100/99.4% | none (64 dates — underpowered) | 26.4% | re-screen with 2026 data |
| NHL 2025-26 | 100/87% | none | 5.4% | dead |

MLB detail: the model beat the opener (+0.00027 LL, clustered t=3.1,
22% capture) but FanDuel quotes ~4% of MLB props — the tradeable cell
produced **16 bets in 13 months**. A right model in an empty venue.

## Game markets (Phase 1G, 2026-07-29)

Follow-up question: was the NHL "no" props-only? Screened the archived
*game* markets — NHL ML/puck-line/totals (1,394 games, scores validated
100% vs official finals incl. all 119 shootouts) and WNBA ML/spread/totals
(511 games) — same gates, n floors scaled (PLAN.md Phase 1G).

- **NHL: FAIL, but flagged for a free re-screen.** Opposite texture to NHL
  props (which never move): game lines move (ML 74%) and the close is
  better sport-wide (+0.0030 LL, t 3.1; moves 56% directional, p=2e-6
  pooled) — but no single market clears the per-cell gates with one
  season. Puck line missed only the n floor (77.8% directional, n=90).
  FD sources 46% of game openers, so the venue exists. The 2026-27
  archive doubles n at zero cost; verdict then. Hitting FD's stale game
  prices without a model still pays −vig (N2 CLV −1.8%).
- **WNBA: FAIL, not for power.** Game lines move constantly but the close
  is no better than the open (pooled t ≈ 0; spread same-line negative).
  The WNBA props wedge does not extend to its game markets.

## NBA result (anchor-on-open move model, walk-forward)

Architecture ported from wnba/: opener → implied mean (per-market
Normal/Poisson), HGBR predicts the standardized open→close move, walk-forward
weekly retrains, expanding shade calibration, EV never from the shade alone.
Dev window ends 2026-04-18; one holdout run (playoffs) after gates froze.

Pre-registered scope (the four passing markets), dev:

- model vs open **+0.00118 LL (date-clustered t=6.0)**, capture **29%**,
  calibration |bias| 0.56pp, tripwire clean (loses to the close −0.0029).
- FanDuel tradeable cell (FD-sourced opener, FD close quoted, side agrees
  with the predicted move): **EV≥3%: 291 bets, ROI +15.9% (pg-t 2.6),
  shade-adjusted CLV +6.9% (pg-t 6.2)**; EV≥2%: 410 bets, CLV +5.1%
  (pg-t 5.8). Zero-skill placebo takes **0 bets**.
- Holdout (final 8 weeks, playoffs, one-shot): sign-consistent
  (+0.00265, t=2.9; capture 32%) with a thin trade cell (14 bets).
- One asterisk: the pre-registered n≥400-at-EV≥3% landed at 291 (scope
  shrank the cell); every quality gate cleared with margin.

## MLB pitcher props, from scratch (2026-08-29, PROGRESS.md Market 6)

The from-scratch programme's first MLB run, on the pitcher family
{strikeouts, outs_recorded} — FanDuel quotes a coherent close on 100% of
K props and posts the opener on 43–51%, and the close beats the open
(+0.0038 LL, t=3.1 dev) — so the venue and the wedge both exist. A
K-per-BF / K-per-PA Kalman engine with a strictly-prior expected-lineup
log5 factor beats the EW blend market-free (K MSE −4.3%) and closes 44%
of the incumbent's gap to the opener (+0.034 → **+0.019**, t=6.2 on dev
2025) but not the registered striking-distance gate (≤ +0.010); the
recalibration iteration made it worse. The K opener's implied mean is 7%
more accurate than the engine's — the sharpest prop market in the repo.
Holdout 2026 unspent; no live arm. Gates, verdicts and forward paths:
PROGRESS.md.

## Caveats that survive into any live design

1. The sim buys FanDuel's **opening** price. Live must catch openers
   before they move — the WNBA stale-gate problem. NBA openers post the
   night before; the N2 lag population needs measuring at live cadence.
2. The holdout is playoffs — a different regime (fewer games, sharper
   prices). The dev result is regular-season.
3. One season of NBA data (the API deletes older seasons). The 2026-27
   season re-arms the archive from October; the scraper is idempotent.
4. FanDuel prop limits are low; this is a bankroll-growth edge at best.

**Status: research complete, live NOT armed.** The NBA season ended
2026-06-13; a live experiment could only start Oct 2026 and requires its
own PROTOCOL, an opener-capture study, and explicit approval. NFL gets a
free re-screen as 2026 data accumulates in the archive.

Consolidated findings across all phases (incl. game markets): FINDINGS.md.

Reproduce: `python3 src/scrape_bp.py --sport NBA`, then `fetch_nba.py`,
`build_props.py`, `map_events.py`, `grade_props.py`, `qc_phase0.py`,
`wedge.py`, `features.py`, `build_modelset.py`, `train_eval.py`
(all `--sport NBA`, run from props/). Gates and decision log: PLAN.md.
