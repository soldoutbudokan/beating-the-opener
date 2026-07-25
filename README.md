# WNBA Props

Can a model beat WNBA player-prop lines (points, rebounds, assists, threes, PRA, ...)
at the open — or even the close? Successor to
[beating-the-opener](https://github.com/soldoutbudokan/beating-the-opener) (soccer 1X2
opener: beaten) and [nba-win-prob](https://github.com/soldoutbudokan/nba-win-prob)
(NBA closing moneyline: unbeatable).

**Status: data acquisition.** Results will land here as they exist.

## Data

- **Lines**: BettingPros API archive — per prop: opening line (book + timestamp,
  frequently FanDuel) and last pre-tip line per book (FanDuel, DraftKings, Caesars,
  BetMGM, Fanatics, ...). Coverage: 2025 season + 2026 season to date. The upstream
  archive is ephemeral (2024 is already gone), so raw gzipped JSON is committed under
  `data/raw/bp/`. Earlier seasons (May 2023+) exist only behind The Odds API paywall.
- **Stats**: [wehoop](https://github.com/sportsdataverse/wehoop-wnba-data) player box
  scores 2003-present (minutes, all countable stats) — model features + prop grading.
  Fetched, not committed.

## Why props might be beatable where game lines weren't

- Books post hundreds of prop prices per slate with far less liquidity and attention
  per price than a game line; limits are low, which caps how sharp they need to be.
- Props are void on DNP, so the tail risk that makes basketball modeling hard
  (star sits late scratch) partially self-cancels; what remains is minutes/usage
  allocation, which box-score history measures well.
- The market's own open->close moves tell us how wrong openers are (same wedge
  methodology as the soccer project).

## Reproduce

```
python3 src/scrape_bettingpros.py   # archive lines -> data/raw/bp/ (committed)
python3 src/fetch_wehoop.py         # box scores -> data/wehoop/ (gitignored)
```
