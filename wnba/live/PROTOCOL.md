# Live experiment protocol - WNBA props

> # ▶️ EXPERIMENT RE-OPENED — 2026-07-31 (v3, from-scratch talent model)
>
> **Owner decision, 2026-07-31 evening: live betting is re-opened** for the
> v3 programme. The morning's pause of the *anchored* model stands — that
> model stays retired; what bets now is the from-scratch talent model
> (`fp_live.py`: pinned fp-prospective-2 base + news-driven minutes
> overrides). Rules pre-registered here, before the first v3 bet:
>
> - **Bet trigger**: a FanDuel two-way quote (same line both sides,
>   booksum 1.00–1.15) where the news-adjusted model's claimed EV > 10%.
>   Consensus-only edges are context, never bets (untradeable — AUDIT H3).
> - **Stakes**: quarter-Kelly computed on **half the claimed edge** (dev
>   2025: claimed EV realizes ~half — the bucket table in PROGRESS.md),
>   against `bankroll.json` (continues at $98.91), rounded to $0.50,
>   minimum $0.50, **cap 5% of bankroll per bet**, one bet per player per
>   game (highest-EV market only), dedupe against `bets.csv` by pick key,
>   and **total playable stakes capped at 30% of bankroll per sheet**
>   (proportional scale-down — the audit's open-exposure lesson).
> - **Notifications**: the hourly `news-watch` routine sends ONE
>   PushNotification per firing listing NEW qualifying picks
>   (player/market/line/side/FD price/stake). Nothing crosses → no
>   notification. Never re-notify the same pick key.
> - **Availability check before you bet**: the cadence is hourly — late
>   scratches can be missed. Confirm the player is in the lineup near tip;
>   the overrides file is judgement, not gospel.
> - **Fills**: report what you took; the session logs it to `bets.csv`
>   (fields copied from `picks.csv`), commits, pushes. Never invent fills.
>   Every logged row carries **`ev_claimed`** — the model's claim at the
>   price actually taken — so the scoreboard can show claim vs market
>   verdict side by side. See "Reporting fills" below.
> - **Settlement & scoreboard**: `settle_bets.py` next morning (the
>   pre-pause machinery below, audit-hardened). Primary metric **CLV vs
>   close** (raw + shade-adjusted), secondary P&L. `edge-watch` stays
>   **data-only** (archives the closes CLV needs).
> - **Scoring firewall**: `fp-prospective-2` (PROGRESS.md) keeps scoring
>   the BASE model on log loss regardless of betting outcomes; this
>   experiment measures the news-adjusted model's money outcome. Neither
>   contaminates the other.
> - Expectation-setting, pre-registered: dev-2025 analogue of this exact
>   rule (FD, EV>10%) is ~+5-10% ROI; at this bankroll that is cents per
>   day. The experiment is measurement, not income.
>
> ## edge-watch routine — DATA-ONLY (unchanged)
>
> On every firing: run `python3 src/scrape_bettingpros.py` from `wnba/`,
> commit the new archive files (`archive: WNBA lines (data-only mode)`),
> push, end with one line. No picks, no notifications, no settlement —
> those duties live with `news-watch` and the owner's sessions.
>
> ## news-watch routine (v3, with picks + notify — owner re-open 2026-07-31)
>
> Each firing, in order:
>
> 0. **Settlement sweep (owner instruction, 2026-08-03):** if `live/bets.csv`
>    holds any `open` bet for a game before today (ET), run
>    `python3 src/fetch_wehoop.py`, then `python3 src/fetch_espn_box.py`
>    (box-score fallback, owner-approved 2026-08-06 — see below), then
>    `python3 src/scrape_bettingpros.py`,
>    then `python3 src/settle_bets.py`, and commit as
>    `live: settle the <date> slate`. `settle_bets.py` stamps CLV as soon as
>    the game is over (the close exists even when box scores lag) and fills
>    results when wehoop publishes — retry every firing until no stale open
>    rows remain, so the first firing after ~05:00 ET normally completes the
>    previous night's slate. When the fetch brought new box scores, finish
>    the sweep with `python3 src/features.py && python3 src/talent.py
>    --build && python3 src/build_modelset.py` so the panel `fp_live.py`
>    projects from tracks the latest completed games (added 2026-08-03 —
>    nothing else in v3 refreshes the model state). This sweep is the only
>    settlement writer; fills themselves remain owner-reported only.
>
>    **Box-score fallback (owner-approved 2026-08-06).** wehoop publishes in
>    bulk and stalls — it froze at 2026-08-01 for five days while games were
>    played, stranding 20 finished bets as `open`. `src/fetch_espn_box.py`
>    archives ESPN's own finals (the source wehoop scrapes) for the ET dates
>    wehoop is missing, into committed `data/raw/espn_box/<date>.json`, and
>    `settle_bets.py` merges them **only** for uncovered dates — wehoop stays
>    the source of record and reclaims a date the moment it publishes it.
>    Validated before first use: the 2026-08-01 overlap reproduced wehoop
>    exactly (48/48 rows, all settled stats, team abbrs and DNP flags), and
>    reconstructed player points reconcile to the official final score in all
>    24 team-games of 8/2–8/5. Scope is settlement only: `features.py`,
>    `talent.py` and `grade_props.py` still read wehoop alone, so the model
>    panel and the `fp-prospective-2` scoring firewall are untouched — the
>    panel simply stays frozen until wehoop catches up.
>    Related hardening in the same change: the "no box row after 3 days →
>    `void (no box score)`" rule now fires only when a box feed actually
>    covers that game (date + one of the two teams). A missing feed is a data
>    outage, not a DNP; uncovered rows stay `open` and print
>    `BOX_FEED_BEHIND <n>`. Without this, the 8/3 bets would have been voided
>    at $0 P&L on the next firing purely because upstream was late.
> 1. `python3 src/avail_watch.py --fetch` from `wnba/` (v3 T3 capture,
>    owner-approved 2026-08-04): snapshots the ESPN league injury
>    report, today's per-event injury reports, and lineups/DNPs once
>    ESPN populates them, into `data/raw/avail/` (committed — the feed
>    is ephemeral, the archive is the future training set; no model may
>    train on it without a pre-registered QC gate). It prints a
>    structured diff (NEW / UPDATED / CLEARED player statuses) — judge
>    THAT, not headlines alone: statuses `Out` (incl. "Coach's
>    Decision") and cleared/returning players become override entries in
>    `live/projections_overrides.json` per its schema (conservative,
>    sourced `avail:<date>/<hhmm>Z`, author `news-watch`; supersede,
>    never edit). `NO_CHANGE` → no overrides from this step.
> 1b. `python3 src/news_watch.py --fetch` for narrative news the injury
>    feed lags (trades, rest plans, minutes-limit quotes). New items →
>    judge availability/minutes implications; append override entries as
>    above. Both scripts printing `SOURCES_UNREACHABLE` → end with that
>    single line.
> 2. **Always** run `python3 src/fp_live.py` (prices move without news):
>    refreshes `live/projections.csv` and rewrites `live/picks.csv` with
>    rows meeting the bet trigger above.
> 3. If `picks.csv` contains picks with `play=True` whose keys were not in
>    the previous `picks.csv` and are not in `bets.csv`: send ONE
>    PushNotification listing them (player, market, line, side, FD price,
>    stake). Otherwise send nothing.
>    **Also always post the new picks in the chat reply** as a markdown
>    table (player / market / line / side / FD price / stake / EV / tip),
>    plus a second table of the other `play=True` rows still on the sheet
>    for context (owner instruction, 2026-07-31). The push notification is
>    not a substitute — the chat post happens every firing that has new
>    picks, even if the notification tool is unavailable.
>    **Availability annotation (owner-approved 2026-08-04):** any pick
>    whose player appears in the latest `data/raw/avail/` snapshot as
>    `Out` or `Day-To-Day` gets a loud ⚠ marker + status in its table
>    row and in the notification text. Display-only: it does not filter
>    `picks.csv` (changing what gets bet is a separate owner decision) —
>    it exists so a stale-fed player (the Kelsey Plum case, 2026-08-02)
>    is visible before a fill, not after.
> 4. Commit touched files (`news-watch: <n> override(s), <m> pick(s)`),
>    push to main. **No manual bets.csv writes** — fills are owner-reported
>    only; the only settlement is step 0's sweep.
>
> **Scoreboard display rule (owner instruction, 2026-08-03):** a pick whose
> key is already in `bets.csv` belongs to the bet log, not to "On the sheet
> now" — the sheet shows only rows the owner can still act on, so what was
> skipped stays legible. `site/build_site.py` enforces this.
>
> **Open positions:** none. At pause time `live/bets.csv` held 5 bets, all
> `status=settled` (2W-3L, bankroll $98.91 of $100). Nothing needs unwinding.
> If that is ever untrue at pause time, settle open bets manually before
> halting.
>
> `live/picks.csv` still holds the 4 marginal picks (0 strong) from the last
> run before the halt reached `main`. They are **not actionable** — nothing is
> refreshing those prices, and the stale-opener gate they were scored under
> assumes a live refresh loop. Left in place as the record; the scoreboard
> labels them as such. Do not bet them.
>
> **The routine's schedule is still enabled** at the time of writing — the
> pause is enforced by this block alone. It cannot be disabled through the
> agent API (it was created via `http_api`; agents may only update routines
> they created). Toggle it off at
> https://claude.ai/code/routines/trig_01Ko6Py4ar9tw8QoxPYx8tyw for a hard
> stop. **This block only takes effect once it is on `main`,** which is what
> the routine checks out.
>
> Everything below is the pre-pause protocol, kept as the record of what ran.

One-season (2026, through ~October) FanDuel test. **$100 starting bankroll
(separate from the soccer experiment), quarter-Kelly sizing.** Primary
scoreboard is **CLV**, secondary is P&L. Results: [RESULTS.md](../RESULTS.md)
or the
[live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)
(both auto-generated - never hand-edit).

## The routine

| | |
|---|---|
| name | `edge-watch` (WNBA-only since 2026-07-28 — the soccer live experiment was cancelled before launch, see soccer/live/PROTOCOL.md) |
| id / manage | `trig_01Ko6Py4ar9tw8QoxPYx8tyw` - https://claude.ai/code/routines/trig_01Ko6Py4ar9tw8QoxPYx8tyw |
| model | claude-opus-5 |
| schedule | 7x daily at :21 UTC - `21 2,4,6,11,14,18,22 * * *` - see [Why these hours](#why-these-hours) |
| runs | quick pre-check (no games + no open bets -> exit in seconds); else data refresh (`fetch_wehoop` -> `build_props` -> `grade_props` -> `features` -> `build_modelset`) -> `live_pipeline.py` -> **notify immediately** on strong picks -> housekeeping (`scrape_bettingpros` for CLV closes -> `settle_bets.py`, which also rebuilds `docs/index.html`) |
| commits | pushes to main when picks changed or bets settled |
| notifies | push notification ONLY for new strong picks (EV >= 6%) or settlements, and never for a pick already in `bets.csv` - see [No duplicate notifications](#no-duplicate-notifications) |
| reports | whenever `live/picks.csv` is non-empty, the run writes **every** pick as a markdown table at the top of its session reply - see [Pick table](#pick-table-every-run) |

### Pick table (every run)

Any run that produces a non-empty `live/picks.csv` opens its session reply
with a markdown table of **all** rows - marginal (EV >= 3%) as well as strong
- before the run log, commit notes, or anything else. This is separate from
the push notification, which stays capped at 3 strong picks: the table is the
full picture for whoever reads the session afterwards.

Columns: player (team), game, market, side, line, FanDuel price, model
probability, EV, stake, and the skip-if-worse-than price (`min_odds_6pct` for
strong rows, `min_odds_3pct` for marginal). Sort by EV descending and mark
which rows are `strong=True`, and which are already logged in `bets.csv`.

### No duplicate notifications

Before sending a pick notification, drop every candidate whose `key` already
appears in `live/bets.csv` (any status - the user has already acted on it, and
a second ping about it is noise). Notify only on what survives; if nothing
survives, send **no** notification at all, even when `live_pipeline.py`
printed `NEW_PICKS`. Settlement notifications follow the same rule - only
bets that settled on *this* run count, never a re-summary of the standing
ledger.

Dropped picks still belong in the session pick table, marked as already
logged with the price from `bets.csv`, so the reply keeps the full sheet.
Silence is the correct outcome for an hour whose only "new" picks are ones
already filled.

> Resolved 2026-07-26: the cloud environment now allowlists
> `api.bettingpros.com` + `raw.githubusercontent.com` (+ package managers).
> If runs ever fail on a blocked host again, that's where to look.

Notes:
- Props post **overnight ET, not on game-day morning** - measured over the
  4,136 FanDuel-sourced 2026 openers in the archive, 72% are created between
  00:00 and 07:00 UTC (peak 03:00-06:00 UTC = 23:00-02:00 ET), with a
  secondary batch at 16:00-18:00 UTC (noon-2pm ET) and a 6% tail at
  22:00-23:00 UTC. Openers for a given game land a median **36h before tip**,
  so the routine typically sees a prop a full day before it plays. The edge
  IS the stale opener, so run placement tracks that creation curve - see
  [Why these hours](#why-these-hours).
- On a total outage the routine notifies once, drops a `live/outage.json`
  marker (committed) to stay silent on repeat failures, and clears it on the
  next healthy run.
- A push notification is **not** an injury-checked recommendation - the
  routine has no injury feed. See [Injury check](#injury-check-before-you-bet).
- **The pick logic only lists props whose FanDuel price is still at the
  opening line/juice.** Once the line moves, the backtested edge is gone -
  the model does not beat moved prices, so no pick is shown. Don't chase.
- **Picks require a coherent opening quote** - over and under from the same
  book at the same line, booksum in [1.00, 1.15] - and the model's implied
  mean must move toward the bet side. BP stores the two opening records
  independently; a mispaired pair fabricates the EV (see AUDIT.md C1).
- **Only FanDuel-sourced openers are scored** (AUDIT H3): EV computed off
  another book's open is untradeable here, and it makes the stale-price
  gate a same-book comparison.
- `play=False` rows are lower-EV combo markets on a player who already has
  a better row - one bet per player per game is enforced in the sheet, and
  `already_bet=True` rows are excluded from notifications in code.
- **Known selection caveat (AUDIT N2):** a prop whose price never moves all
  day pays CLV = -vig no matter what; the stale-price gate cannot tell
  "hasn't moved yet" from "will never move". Early fills on props that then
  move are where the CLV comes from; expect a drag from the never-movers.
- During the All-Star break / offseason runs print `NO_UPCOMING` - expected.
- The routine never edits `bets.csv`, never places bets, never touches `src/`.

### Why these hours

Changed 2026-07-29 from hourly (24 runs/day) to **7 runs/day** at :21 UTC:
`21 2,4,6,11,14,18,22 * * *`. Three clusters, each doing a different job:

| UTC | ET | why |
|---|---|---|
| 02, 04, 06 | 22:00, 00:00, 02:00 | **posting window** - 72% of FanDuel openers are created 00:00-07:00 UTC, peaking 03:00-06:00. This is where picks are born. |
| 11, 14 | 07:00, 10:00 | **morning + settlement** - prior night's box scores have landed by ~11:00 UTC, so 11 is the settlement/CLV run; 14 is the ET-morning look and catches the noon-ET opener batch. |
| 18, 22 | 14:00, 18:00 | **pre-tip** - last looks before 7-8pm ET tips; 22 also catches the 6% opener tail created 22:00-23:00 UTC. |

Cost of the cut, measured against the 2026 opener-creation distribution: mean
detection lag rises from 0.50h (hourly) to **1.43h**; 81% of openers are still
seen within 2h of creation, 91% within 3h. That is cheap because prices hold
at the opener for hours, not minutes - the one strong pick observed so far
(Clark rebounds u3.5, 2026-07-28) sat at its opening price for **9+ hours**,
from 14:42 to 23:24 UTC. Caveat: that is a single observation from three days
of live history; if a later post-mortem shows picks dying inside an hour, add
hours back to the 02-06 cluster first, since that is where the openers are.

**The 18/22 runs are not news-trading.** They exist to catch late openers and
to give the user a last actionable look, *not* to buy edges created by injury
news - an opener still sitting at its open price *after* news broke is a
**skip** under [Injury check](#injury-check-before-you-bet), not a buy.

## Playing a pick (user)

`live/picks.csv`, sorted by EV:

- `strong=True` (EV >= 6% at FanDuel's current price) - these trigger the
  notification. EV >= 3% rows are listed for completeness.
- `fd_line` / `fd_cost` - the price the model evaluated. If FanDuel now shows
  something worse than `min_odds_6pct` (strong) or `min_odds_3pct` (marginal),
  the line has moved - **skip, don't chase**.
- `stake` - quarter-Kelly at `fd_cost` from the current bankroll:
  `stake = B x 0.25 x (p*o - 1)/(o - 1)`, capped at `0.10 x B`, rounded to
  $0.50. Better price than the sheet -> any Claude session recomputes.
- **One bet per player per game.** Combo markets (points, PRA, pts+reb...)
  on the same player are heavily correlated; play only the highest-EV row
  for that player.
- Props void on DNP at FanDuel - mirrored in settlement.
- **Run the [injury check](#injury-check-before-you-bet) before every fill.**

### Injury check before you bet

**The model is blind to today's news.** Nothing in the pipeline reads an
injury report. Absences enter only through box scores, and the live model
deliberately uses only `absent_prior_ew_min` - teammates who ALSO missed the
previous game, i.e. absences at least two games old (`src/features.py`,
`src/live_pipeline.py:46`). Tonight's announcements are invisible to it.

Check the injury report / beat reporters before every fill and **skip the
pick** if any of these holds:

- **The subject is questionable, or on a minutes restriction.** A DNP is
  harmless - FanDuel voids the prop - but a 14-minute return-from-injury game
  is a live loser on an over. The void rule protects you from a player not
  playing, not from a player playing badly.
- **A >=12-EW-minute teammate is newly out tonight** (played the last game,
  out now). The model hasn't seen it; FanDuel usually has. If the price still
  sits at the opener, the "edge" you're reading is unpriced news, not model
  skill.
- **A regular is returning tonight after 2+ games out.** Worst case: the model
  still counts them absent, so it inflates the subject's projected usage.
  Overs are stale in the wrong direction.
- Anything else that materially changes the rotation - trade, coach announcing
  rest, suspension.

If news breaks *after* the fill: **don't chase and don't hedge.** Let it
settle - CLV records whether the close agreed with you. Add a `notes` entry on
the `bets.csv` row so the post-mortem is easy.

## Reporting fills (user -> any Claude session)

Plain words: *"got Citron assists over 3.5 at +128 for $2"*, *"skipped the
rest"*. Claude then appends one row per fill to `live/bets.csv` with
`status=open`, copying `key`, `event_id`, `market`, `player`, `side`, `line`,
`model_p` from `live/picks.csv` (`match_date` = the pick's `date`, which is
the **ET game date**; `odds_taken`/`stake` as reported; `placed_at` = today),
commits and pushes: `live: log N bets <date>`.

**`ev_claimed` is required on every row** (owner instruction, 2026-08-01).
It is the model's own claim for the bet **at the price actually taken** -
`model_p x decimal_odds - 1`, *not* the sheet's `ev`, which is quoted at the
sheet price and drifts once the fill comes in at something else. Any routine
or session that logs a fill fills it in. If a row is ever written without it,
`settle_bets.py` back-computes it from `model_p` and `odds_taken` on the next
run, so the column must never be left blank in RESULTS.md - a blank there
means `model_p` or `odds_taken` is missing, which is a logging bug to fix,
not a gap to tolerate. It exists so the scoreboard shows the model's claim
next to the market's verdict (`CLV`) on the same bet.

Rules for Claude sessions:
- Never invent or assume a fill; log only what the user explicitly reports.
- Do not edit settled rows; corrections get a `notes` entry.
- Price below `min_odds_3pct` -> log it (user's call) but flag the EV.

## Settlement (automatic)

Box scores land in wehoop within ~a day; the next routine run grades each open
bet (actual stat vs line; DNP -> void, stake returned; exact line -> push).
The game is resolved via the bet's `event_id`: its UTC tip in `events.pkl`
converted to the ET game date, and the box row must belong to one of the
event's two teams - so a slipped settlement can never grade the player's next
game (AUDIT C2). CLV comes from the archived closing snapshot re-expressed at
the bet's own line (consensus close preferred, FanDuel fallback - recorded in
`clv_source`), and only from a **coherent** close: same book, same line for
over and under, booksum in [1.00, 1.15] (AUDIT C1). If no usable close is
archived when the bet settles, `clv` stays blank and later runs backfill it
(AUDIT C3). Updates `live/bankroll.json`, regenerates RESULTS.md. No box row
after 3 days -> voided with a note.

## Scoreboard

Two CLV columns, both stamped at settlement (see README "market over-shade"):

- **`clv`** - vs the raw devigged close, the standard yardstick. The honest
  backtest expectation at the live rule is **~ -3%**: the sheet is mostly
  unders and WNBA closing prices overstate P(over) by ~2pp on average, so
  raw CLV mechanically penalises unders. A raw CLV near -3% is *expected*,
  not evidence of failure; raw CLV well below that is.
- **`clv_cal`** - vs the shade-corrected close. Backtest expectation **~+3%
  at EV>=3%, ~+6% at EV>=6%** - valid only insofar as the measured over-shade
  persists (it drifts; it briefly inverted in Jul 2026).

**Power, stated plainly (AUDIT H7):** at these effect sizes a single season
(~150-400 bets, per-player-game CLV sd ~0.095) cannot statistically separate
the observed CLV from zero - that would need ~4,000 player-games. This
experiment can *reject* a large edge and *measure* a small one; it cannot
prove one. P&L is noisier still: losing money with CLV at expectation is
consistent with the model working; winning with poor CLV is luck.
