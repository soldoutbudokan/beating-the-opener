# beating-the-opener (monorepo)

**WNBA `news-watch` RESTARTED 2026-08-08 with amended instructions**
(owner decision after the first-week audit; it was trigger-disabled for
part of that day). The v3 live-betting protocol (re-opened 2026-07-31)
plus the 2026-08-08 amendments — pick gates (panel/player staleness,
team consistency, FD-opener-only, EV>25% quarantine), shade-table
scoreboard fix, ESPN panel fallback, archive-every-firing — live at the
top of `wnba/live/PROTOCOL.md`. `edge-watch` is retired (its data-only
archiving now runs inside every news-watch firing). What prices
is the talent model + news overrides (`wnba/src/fp_live.py`), FanDuel
EV>10% trigger, pre-registered staking. The anchored-on-the-opener programme
that produced every model here is retired; the rework toward first-principles
pricing is tracked in `PROGRESS.md` (which supersedes `PLAN.md`). Read
`PROGRESS.md` before starting modelling work, and update it with every push.

Subprojects, all now research-only: `wnba/` (player props — the paused live
experiment), `soccer/` (1X2 openers — live experiment cancelled 2026-07-28
before the first bet, post-Pinnacle replay showed no edge), `props/`
(multi-sport MLB/NBA/NFL/NHL prop screen — never had a live phase), and two
controls that returned "no": `nba/` (closing moneyline unbeatable — absorbed
from the `nba-win-prob` repo, and the repo's only from-scratch model) and
`cricket/` (BBL — no wedge exists). Each subproject is self-contained — run
its scripts from inside its directory. Push directly to main (solo project).

Rules that apply everywhere:
- **Do not resume live betting, un-pause the experiment, or re-enable
  `edge-watch` without the user explicitly asking.** The pause is a decision,
  not an outage.
- Live experiment rules live in `<market>/live/PROTOCOL.md` — read the
  relevant one before touching anything under a `live/` directory.
- If the user reports a bet fill, log it as they describe with that market's
  fill logger (`wnba/src/log_fill.py`; see PROTOCOL "Reporting fills"), then
  commit and push. It copies the fields from `live/picks.csv`, stamps
  `ev_claimed` at the price taken and rebuilds the scoreboard. Never
  hand-append to `bets.csv`, and never invent fills. If the market is paused,
  also tell them the model that produced the pick is the paused one.
- `RESULTS.md`, `live/bankroll.json` and `docs/index.html` (the published
  scoreboard, built by `site/build_site.py`) are auto-generated — never
  hand-edit. Change the generator instead, then re-run
  `python3 site/build_site.py` from the repo root. Every writer of
  `bets.csv` (`settle_bets.py`, `log_fill.py`) rebuilds the page via
  `refresh_site()` in `src/live_utils.py`, so a fill logged between
  settlements can't leave a stale scoreboard.
- `wnba/data/raw/bp/` and `props/data/raw/bp/` are irreplaceable committed
  archives (upstream deletes old seasons) — never delete, shrink, or gitignore
  them. The rework still needs them: from-scratch models are still *scored*
  against market prices.
- Research READMEs (`wnba/`, `soccer/`, `nba/`, `cricket/`) record what was
  measured. The 2026-07-31 direction change does not make those measurements
  wrong — don't rewrite the numbers, and don't quietly restate results as
  "invalid". Banners at the top mark what the critique does and does not
  reach.
- Pre-registration discipline carries over: `props/PLAN.md` is the model for
  it — gates written down before the phase that uses them, and honoured even
  when they kill a promising cell.
- **No model here reads injury/team news** — the paused picks were never
  injury-checked. Each PROTOCOL has an "Injury check before you bet" section;
  point the user at it if they ask about a historical pick.
- Routine state (verified 2026-08-08 via the agent API): `edge-watch`
  (`trig_01Ko6Py4ar9tw8QoxPYx8tyw`) has been DISABLED since 2026-07-31
  and is retired — news-watch's step 0 does its archiving now. `news-watch`
  (`trig_01GThXFjtLzfXEH1kqjMYXEF`, hourly at :31) was disabled during
  the 2026-08-08 audit and RE-ENABLED the same day by owner decision,
  with its prompt rewritten around the PROTOCOL amendments. Do not
  enable/disable either without the owner explicitly asking. Manage at
  https://claude.ai/code/routines/
