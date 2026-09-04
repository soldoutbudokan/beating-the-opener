# Beating the Opener

**[→ Live scoreboard](https://soldoutbudokan.github.io/beating-the-opener/)** —
bankrolls, CLV, open positions and the evidence behind them, at a glance.
Rebuilt automatically every time a bet settles.

**New here, or not technical? Read [OVERVIEW.md](OVERVIEW.md) first** — a
plain-language guide to what the model does, how the live and research
processes work, where the results stand, and what the 2026-09-04 audit
([issue #1](https://github.com/soldoutbudokan/beating-the-opener/issues/1))
found.

> ## ▶️ WNBA live betting RE-OPENED — 2026-07-31 (v3, from-scratch)
>
> The anchored architecture was retired on the morning of 2026-07-31 (its
> record: $98.91 of $100 over 5 bets). The same day, its from-scratch
> replacement — a Kalman **talent model** with news-driven minutes overrides,
> no market inputs — beat the opener on the 2025 dev season and went live
> under a pre-registered protocol ([wnba/live/PROTOCOL.md](wnba/live/PROTOCOL.md)):
> FanDuel coherent quotes, claimed EV > 10%, capped Kelly staking, CLV-primary
> scoring. Two routines run it: `news-watch` (hourly: news → minutes
> overrides → picks → notification) and `edge-watch` (close archiver).
>
> The model's *forecasting* claim is judged separately from the betting:
> `fp-prospective-1/2` in [PROGRESS.md](PROGRESS.md) score it on log loss
> against the opener over props dated 2026-08-01+, registered before that
> data existed. The anchored-era research record and what its retirement
> does and does not invalidate: [Anchoring: what it cost](#anchoring-what-it-cost).
> `nba/` remains the bounded negative control worth reading first.

One project, one thesis, tested market by market: **soft sportsbooks' opening
lines are inefficient, and the inefficiency is capturable with public data.**
The method was the same everywhere — anchor on the market's own price, model
only the open→close correction (never the raw outcome), prove it out-of-sample
against the close, then bet stale openers in a live experiment scored by CLV.
**That method is retired as of 2026-07-31** (see the banner above); what
follows is the record of what it produced.

| market | opener beaten? | wedge captured | live test |
|---|---|---|---|
| NBA moneyline → [`nba/`](nba/) (control #1) | no — even the open is sharp | — | — |
| **soccer 1X2** → [`soccer/`](soccer/) | **yes in the Pinnacle era** — 9/9 OOS seasons (p = 0.0039); but the post-Pinnacle replay (the live regime) shows no edge over its own anchor | 18% | cancelled before launch — no edge in the live regime |
| **WNBA player props** → [`wnba/`](wnba/) | **yes on log loss** (clustered t = 4.8) — but the FanDuel-tradeable cell is ~+3% ROI at t ≈ 0.5 | 55% | 🔴 running now |
| cricket BBL match odds → [`cricket/`](cricket/) (control #2) | no — **no wedge exists**: the close is no better than the open; moves are toss noise | — | — |

A 2026-07-28 methodological audit ([AUDIT.md](AUDIT.md)) found the previously
published live expectations were inflated by measurement artifacts (date-join
contamination, mispaired quotes, envelope CLV); the numbers above and every
live pipeline reflect the post-audit, honest versions.

The two control results are what make the middle rows credible: the same
methodology, honestly applied, returns "no" twice. An exploitable opener
needs both a *lazy open* (low attention per price) **and** an *informative
close* (real information arriving before tip, so there's a correction to
capture). NBA fails the first — attention floods the market. BBL cricket
fails the second — the lines move plenty, but toward toss noise, not
winners. Lower-league soccer and WNBA prop menus satisfy both.

## Anchoring: what it cost

The 2026-07-31 critique, stated precisely, and audited against what each
subproject actually did:

| project | starts from the market's number? | what it would have to be reworked into |
|---|---|---|
| [`nba/`](nba/) | **no** — Elo, RAPM, efficiency ratings, availability, referee crews, travel; the line is only the benchmark | nothing. This already is the from-scratch experiment. |
| [`soccer/`](soccer/) | **yes** — `stack` and `gbmmove` both take Pinnacle's opener logits as their base | a goals model (e.g. bivariate Poisson / Dixon-Coles on team attack-defence) priced independently |
| [`wnba/`](wnba/) | **yes** — `mu_open` is inverted out of the opening price and every prediction is `mu_open + predicted_move` | a minutes × usage × efficiency player model producing a full stat distribution |
| [`props/`](props/) | **yes** — explicitly a port of the WNBA anchored architecture to MLB/NBA/NFL/NHL | same, per sport |
| [`cricket/`](cricket/) | n/a — screening only; the wedge test found no wedge and **no model was ever built** | a from-scratch BBL model is untested territory |

So the critique is right about three of the four modelling projects, and
right about the one that went live. Two things it is not right about, both
of which matter before the weekend:

**1. `nba/` already ran this experiment and it lost.** It builds a win
probability from scratch and benchmarks it against the closing line. Result:
the line wins by 0.0135 log loss, an encompassing regression finds the model
contributes nothing the line lacks (coef −0.08, p=0.43), and a subset search
across 14 regimes finds **0** where the model wins. It is also bounded, not
merely failed — perfect exploitation of all 95 observables reaches 0.5932 and
a look-ahead oracle refitting RAPM *on the held-out seasons* reaches 0.5915,
both short of the line's 0.5818.

**2. The anchored architecture was adopted because the from-scratch one lost
first, twice.** `soccer/` v1 fed the odds to a GBM as plain features and lost
to the opener by 0.022. `wnba/` v1 ([`src/train_eval.py`](wnba/src/train_eval.py))
regressed the outcome residual and lost to the close by 0.028. The repo's own
[`props/PLAN.md`](props/PLAN.md) records the tally as *"the approach already
0-for-2 in this repo."* Anchoring was a retreat from those losses, not a
shortcut taken before trying.

None of this makes the critique wrong. An anchored model genuinely cannot
tell you what a player will do, only where a price will drift — and a
research programme that can only ever measure the market's own second
thoughts is a narrow thing to own. But the from-scratch direction is a
**harder** problem that this repo has already lost three times, and `nba/`
argues the loss is structural: the market's private information (injury
detail, lineup intent, money flow) is ~82% orthogonal to everything public
data measures. The rebuild should start by saying why it beats that bound.

## Live experiment

**Paused 2026-07-31.** No open positions. WNBA props ran on FanDuel with a
$100 bankroll and quarter-Kelly stakes, judged on CLV; it finished at $98.91
over 5 settled bets (2W-3L) — a sample far too small to mean anything either
way, which was expected (one season could not have separated the effect from
zero either; AUDIT.md H7). The `edge-watch` routine is halted by the block at
the top of [wnba/live/PROTOCOL.md](wnba/live/PROTOCOL.md).

- WNBA (paused): [scoreboard](https://soldoutbudokan.github.io/beating-the-opener/#wnba)
  · [record](wnba/RESULTS.md) · [protocol](wnba/live/PROTOCOL.md)
- Soccer (cancelled before launch, 2026-07-28 — no edge in the post-Pinnacle
  regime): [record](soccer/RESULTS.md) · [protocol](soccer/live/PROTOCOL.md)

## Repo layout

Each market is a self-contained subproject (its own `src/`, `data/`, `live/`,
README with the full research writeup, and auto-generated RESULTS.md). Run
scripts from inside the subdirectory. The WNBA line archive under
`wnba/data/raw/bp/` is committed because the upstream source deletes old
seasons — it is irreplaceable.

`nba/` was developed as its own repository (`soldoutbudokan/nba-win-prob`) and
absorbed here with its history intact — it is the first control, and the
methodology it rules out is why the later markets were chosen the way they
were.

`site/build_site.py` renders both markets' live files into `docs/index.html`
(the scoreboard above) — see [`site/README.md`](site/README.md). Each
`settle_bets.py` run regenerates it, so the page never drifts from the CSVs.
