# cricket — plan & progress

Goal: replicate the beating-the-opener methodology for a cricket market —
prove (or honestly disprove) that some cricket opener is inefficient vs the
close, using only free public data. Working notes live here; check boxes as
phases complete so progress survives across sessions.

## Status

- [ ] **Phase 0 — data scoping (make-or-break).** Find a free source of
  historical *opening + closing* cricket odds. Candidates:
  - BetExplorer (`betexplorer.com/cricket/`) — sister site of OddsPortal,
    shows true opening odds per bookmaker; check scrapeability + history depth
  - Betfair historical data (`historicdata.betfair.com`) — Basic tier is
    free; cricket included; needs account, may be geo-blocked from Canada
  - The Odds API — has cricket (IPL/BBL/T20I/ODI/Tests) but free tier is
    current-odds only; would mean *forward archiving* and waiting weeks
  - Cricsheet (`cricsheet.org`) — free ball-by-ball for all major
    leagues/formats since 2000s: this is the outcomes side, assumed solved
- [ ] **Phase 1 — archive.** Whatever source wins, scrape and COMMIT raw
  data immediately (lesson from WNBA: line data is ephemeral upstream).
- [ ] **Phase 2 — wedge test.** Same-market open vs close log-loss; do line
  moves point at outcomes? If no wedge → write it up as a control (like NBA)
  and stop.
- [ ] **Phase 3 — model.** Anchor on open-implied probability, predict the
  open→close MOVE (never the raw outcome — v1 lesson, twice learned),
  benchmark vs close, clustered inference.
- [ ] **Phase 4 — live experiment** only if Phase 2/3 clear the bar:
  stale-opener rule, quarter-Kelly, $100 bankroll, CLV scoreboard, hourly
  routine — mirror soccer/wnba protocols.

## Decisions & findings log

- 2026-07-26: project started. Nothing decided yet; scoping first.

## Constraints

- Free data only (user has not opted into paid tiers).
- Cricket-specific wrinkles to keep in mind: two-way market (no draw in T20;
  draws exist in Tests — pick format accordingly), rain/DLS voids, toss is a
  mid-stream information shock (odds move at toss ~30 min before start —
  "close" must be pre-toss or post-toss consistently), franchise leagues
  (IPL/BBL/PSL/Hundred) likely softer + better data than internationals.
