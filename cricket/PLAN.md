# cricket — plan & progress

> **Note, 2026-07-31.** This project closed before any model was built, so the
> repo-wide move to first-principles pricing ([`../PLAN.md`](../PLAN.md))
> leaves its verdict intact: the finding here is about the *market* (BBL
> closes are no better than BBL openers; moves track toss noise), not about
> any model architecture. It is therefore the one subproject where a
> from-scratch attempt is genuinely untested — ball-by-ball data (Cricsheet,
> free and complete) suits a generative simulation. Cuts both ways, though:
> no informative close also means no evidence the closing price is
> particularly sharp, so there may be less here to beat *and* less to learn
> from beating it. Listed as a rework candidate in `../PLAN.md`.

Goal: replicate the beating-the-opener methodology for a cricket market —
prove (or honestly disprove) that some cricket opener is inefficient vs the
close, using only free public data. Working notes live here; check boxes as
phases complete so progress survives across sessions.

## Status

- [x] **Phase 0 — data scoping.** Winner: **aussportsbetting.com BBL file**
  (open/min/max/close odds compiled from OddsPortal). Ruled out: BetExplorer
  (no cricket at all), Betfair historic (Cloudflare 403 to curl, WebFetch AND
  the Wayback crawler; needs account anyway), The Odds API (historical =
  paid). Cricsheet confirmed reachable for outcomes if ever needed.
- [x] **Phase 1 — archive.** `data/raw/asb/big_bash_league_wayback20231108.xlsx`
  committed (live site Cloudflare-blocks all non-browser clients — got it via
  the Wayback snapshot of 2023-11-08). 549 matches BBL 2011/12–2022/23;
  open+close coverage only 2018+ (297 matches, ~60/season × 5 seasons).
- [x] **Phase 2 — wedge test.** `src/wedge_test.py`. **No move-wedge:**
  close does NOT beat open (−0.005 LL, wrong sign, n.s.); moves point at the
  winner only 46% (n.s. below coin-flip); market moves toward the toss winner
  55.9% (p=.05) but toss winners win only 49.2% → the close absorbs toss
  NOISE. BBL ≠ soccer/WNBA: the close never corrects the open.
  **But a possible LEVEL bias:** true-home teams (neutrals excluded, n=236)
  win 57.6% vs open-implied 50.6% (binom p=.03); flat-bet-home ROI +7.7% at
  open / +9.7% at close (t≈1.3–1.5, n.s.). Formed after peeking → needs
  out-of-sample confirmation on BBL 2023/24–2025/26 seasons.
- [x] **Phase 2b — OOS test of the home bias: REJECTED.** No browser needed
  after all — the pre-2018 seasons (plain odds columns, n=242, disjoint from
  the hypothesis-forming 2018+ sample) are a free OOS set. The bias FLIPS
  SIGN there (home 47.5% vs 52.0% implied; flat-home ROI −12.4%, t=−2.0) and
  pools to nothing over 12 seasons (52.5% vs 51.4%, p=0.65). Forking-paths
  artifact; hypothesis dead.
- [x] ~~Phase 3 — model~~ / ~~Phase 4 — live~~ **CLOSED — nothing cleared
  the bar.** Verdict: BBL is control #2 (see README.md). No move-wedge to
  model, no level bias to bet. Optional future top-up: current cumulative
  xlsx (adds 2023/24–2025/26, doubles the open/close sample) needs a real
  browser — user can download from
  https://www.aussportsbetting.com/data/historical-twenty20-big-bash-results-and-odds-data/
  and drop it in `data/raw/asb/` alongside the wayback copy; re-run
  `src/wedge_test.py`. Also logged for the future: fade-the-toss-move
  curiosity (market prices the toss at 55.9% move-alignment; toss wins only
  49.2% of matches — not an opener edge, thin sample).

## Decisions & findings log

- 2026-07-26: project started; scoping.
- 2026-07-26: BBL chosen (only free open/close cricket source found). Wedge
  test says the opener is NOT beatable via the move channel — if cricket has
  an edge it's the uncorrected home-price level bias. OOS data needed.
- 2026-07-26: power note — n=297 detects only large LL effects (SE≈.005);
  mean |move| is 3.6pp of prob, so if moves were signal we'd have seen
  close-beats-open ≈ +.005; we saw −.005. Moves being noise is the reading
  most consistent with the data, not just "underpowered".

## Constraints

- Free data only (user has not opted into paid tiers).
- Cricket-specific wrinkles to keep in mind: two-way market (no draw in T20;
  draws exist in Tests — pick format accordingly), rain/DLS voids, toss is a
  mid-stream information shock (odds move at toss ~30 min before start —
  "close" must be pre-toss or post-toss consistently), franchise leagues
  (IPL/BBL/PSL/Hundred) likely softer + better data than internationals.
