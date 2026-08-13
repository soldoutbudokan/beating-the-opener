# site/ — the published scoreboard

`build_site.py` renders the live WNBA files (plus the archived soccer record)
into **`docs/index.html`**, a self-contained page (no assets, no build step,
stdlib only). Three tabs:

- **Live** — the WNBA props dashboard: bankroll, tiles, cumulative P&L vs
  CLV-expected P&L over time, running mean CLV (raw + shade-adjusted CLV*),
  the before/since split around the 2026-08-08 pick gates, the CLV band vs
  the backtest, the current sheet, and a client-side **filterable bet log**
  (market / side / result / era / player search, with a live summary line).
- **Evidence** — the four-market research ledger and the WNBA betting sim.
- **Archive** — the cancelled soccer 1X2 experiment (backtest curve + sim)
  and the retired opener-anchored WNBA era. Old `#soccer` / `#wnba` hash
  links are aliased to the new tabs by the page's JS.

The masthead has a light/dark toggle (top right). It stamps `data-theme` on
`<html>` — the same attribute every colour token block is scoped to — and
persists the choice in `localStorage` (`theme`); with no stored choice the
page follows `prefers-color-scheme`. A tiny head script applies the stored
choice before first paint so there is no flash of the wrong theme.

```
python3 site/build_site.py                 # -> docs/index.html
python3 site/build_site.py --fragment /tmp/x.html   # body-only copy
open docs/index.html                       # preview locally
```

Inputs, all read straight off disk: `<market>/live/bankroll.json`,
`live/bets.csv`, `live/picks.csv`, `live/picks_meta.json`, plus
`soccer/results/cum_pnl.csv` for the archived backtest curve. Published
research numbers (the ledger and the two simulation tables) are constants at
the top of the script — sources are each subproject's README.

Process-change markers on the time charts come from the `EVENTS` constant
(`V3_LIVE` 2026-07-31, `GATES` 2026-08-08); `GATES` also drives the era
filter and the before/since comparison cards. Add future protocol changes to
`EVENTS` rather than annotating charts by hand.

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
  properties at the top of `CSS`. The two chart series colours are the first
  two slots of that palette — series identity is carried by the legend and
  endpoint dots, never by colour alone.
- CLV appears twice everywhere on purpose: raw (vs the close as quoted) and
  CLV* (vs the shade-adjusted close, AUDIT.md N1). Raw is structurally
  negative for an under-heavy sheet; CLV* is the fair yardstick. Don't
  "simplify" one of them away.

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
