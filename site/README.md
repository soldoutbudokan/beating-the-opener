# site/ — the published scoreboard

`build_site.py` renders both markets' live files into **`docs/index.html`**, a
self-contained page (no assets, no build step, stdlib only): live bankrolls,
CLV against the backtest expectation, open positions, the bet log, current
picks, and the four-market evidence ledger.

```
python3 site/build_site.py                 # -> docs/index.html
python3 site/build_site.py --fragment /tmp/x.html   # body-only copy
open docs/index.html                       # preview locally
```

Inputs, all read straight off disk: `<market>/live/bankroll.json`,
`live/bets.csv`, `live/picks.csv`, `live/picks_meta.json`, plus
`soccer/results/cum_pnl.csv` for the backtest curve. Published research
numbers (the ledger and the two simulation tables) are constants at the top of
the script — sources are each subproject's README.

`EXTRA_PAGES` republishes standalone write-ups that live with their subproject
— currently `nba/reports/report.html` → `docs/nba-report.html`, linked from the
evidence ledger. Pages only serves `docs/`, hence the copy; it is refreshed on
every build so it cannot drift from the original. Keep such a page
self-contained and give it an absolute back-link, since it is served from a
different directory than the one it lives in.

Notes for future edits:

- **`docs/index.html` is generated — never hand-edit it.** Both
  `settle_bets.py` scripts call this builder as their last step, so hand edits
  are overwritten within the hour.
- The page is written **only when its content changes**, and every timestamp on
  it comes from the data rather than the clock, so idle routine runs produce no
  commit churn.
- Colours match the matplotlib charts in `*/src/make_chart.py` (blue = model,
  orange = market); both light and dark themes are defined as CSS custom
  properties at the top of `CSS`.

## Publishing

GitHub Pages serves it at
<https://soldoutbudokan.github.io/beating-the-opener/>. One-time setting:
**Settings → Pages → Build and deployment → Deploy from a branch → `main`
/ `docs`.** Pages needs the repo to be public on the free plan. Until that is
switched on, the links from the READMEs 404 and the page can only be opened
locally.

`docs/.nojekyll` turns off Jekyll: the page is already final HTML, and Jekyll
would otherwise try to parse any `{{ … }}` that ends up in a bet log or in the
inline script.
