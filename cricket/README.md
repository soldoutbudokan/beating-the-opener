# cricket (BBL) — the second control market

Fourth market tested with the beating-the-opener methodology, and the second
**negative result** (with NBA): the one cricket market with free open/close
history — **Big Bash League match odds** — shows an opener that is *not*
exploitable, for an interesting reason: **the close is no better than the
open.** Nothing corrects the opener because no information arrives to correct
it with; the moves that do happen are noise.

## Data

`data/raw/asb/big_bash_league_wayback20231108.xlsx` — aussportsbetting.com's
cumulative BBL file (odds compiled from OddsPortal multi-book averages),
recovered via the Wayback Machine because the live site Cloudflare-blocks
every non-browser client (curl, WebFetch, and archive.org's own crawler — its
save attempt logged a 403). 549 matches, BBL 2011/12–2022/23; **open/close
odds exist only 2018+ → 297 usable matches** (~60/season × 5 seasons). Plain
`Home/Away Odds` columns cover all 12 seasons (n=478 true home games).

Everything below reproduces with `python3 src/wedge_test.py` (run from
`cricket/`).

## Findings

1. **No wedge.** Devigged log-loss on the winner: open 0.6781, close 0.6835 —
   the close is *worse* by 0.005 (wrong sign; t=−1.0). In soccer and WNBA the
   close beat the open decisively; here there is nothing to capture.
2. **Moves don't point at winners.** The line moved in 290/297 matches
   (mean |move| 3.6pp of probability — big moves!) but pointed at the eventual
   winner only **46.2%** of the time (p=0.22). Power note: if those moves were
   signal, close-beats-open would have shown ≈ +0.005 LL; we measured −0.005
   (SE 0.005). "Moves are noise" is the reading the data supports, not merely
   "underpowered".
3. **The noise has a name: the toss.** The market moves toward the toss winner
   55.9% of the time (p=0.052) — but the toss winner wins only **49.2%** of
   matches. The close absorbs toss *noise*, not information. (Fading the
   toss-move is a curiosity for future work, but it is not an opener edge and
   the sample is thin.)
4. **No level bias either.** 2018+ true-home games hinted at underpriced home
   teams (57.6% wins vs 50.6% implied, p=.03; flat-bet-home +7.7% ROI). Tested
   out-of-sample on the disjoint 2011–2017 seasons it **flips sign** (47.5% vs
   52.0%; flat-home ROI −12.4%) and pools to nothing (52.5% vs 51.4%, p=0.65).
   A textbook forking-paths artifact, caught by the OOS split.

## What this does to the thesis

An exploitable opener needs **two** conditions, not one:

1. a *lazy open* (low attention per price), **and**
2. an *informative close* — real information must arrive between open and
   tip, so there is a correction to model and capture.

NBA fails (1): attention floods the market and even the open is sharp. BBL
fails (2): nobody informed moves these lines — the open already contains
everything public, and what arrives later (the toss) turns out to be nearly
worthless for picking winners even though the market prices it. Soccer 1X2
and WNBA props satisfy both, and only there was the opener beatable.

## Caveats & future work

- n=297 with open/close: a soccer-sized wedge (+0.002 LL) would be invisible.
  The current cumulative file (needs a real browser to download from
  [aussportsbetting.com/data](https://www.aussportsbetting.com/data/historical-twenty20-big-bash-results-and-odds-data/))
  adds BBL 2023/24–2025/26, roughly doubling the sample — drop it into
  `data/raw/asb/` alongside the wayback copy and re-run.
- Odds are multi-book *averages*, not one executable price; best-of-book
  could look different (it did in soccer).
- BBL only. IPL/PSL/Hundred/internationals have no free open/close source we
  could find (BetExplorer carries no cricket; Betfair historic needs an
  account; The Odds API historical is paid; OddsPortal would need a heavy
  scrape). The verdict is "no exploitable cricket opener *found with free
  data*", not "cricket is efficient everywhere".
- No live experiment (Phase 4) — nothing cleared the bar. See `PLAN.md` for
  the full phase log.
