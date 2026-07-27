# cricket — plan & progress

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
- [ ] **Phase 2b — OOS test of the home bias.** Get the current cumulative
  file (adds ~3 seasons, ~180 matches): Wayback SPN failed (their crawler is
  also 403'd), so it must come through a real browser (user's Chrome or user
  downloads it manually from
  https://www.aussportsbetting.com/data/historical-twenty20-big-bash-results-and-odds-data/).
- [ ] **Phase 3 — model.** Only if 2b confirms something. NOTE: with no
  move-wedge, the soccer/WNBA move-model architecture does NOT apply here;
  the candidate edge is a static calibration bias (bet home at open), which
  needs no ML — just OOS validation and a fair-odds check.
- [ ] **Phase 4 — live experiment** only if 2b confirms. BBL is Dec–Jan
  (next season Dec 2026), so live testing waits for the southern summer.

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
