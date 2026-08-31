# Cricket — from-scratch pricing vs a prediction market

> ## Status for review — 2026-08-30 (stopped here at owner instruction)
>
> **The goal was not met.** The registered target was to beat the exchange's
> day-before price in *both* cells; on the full dev population both remain
> marginally behind. What the session did achieve is large: the international
> cell improved **84%** and the franchise cell reached statistical parity, and
> in the more recent half of dev the model beats the open on internationals.
>
> | dev cell (n=693) | session start | final | t |
> |---|---|---|---|
> | franchise leagues (466) | +0.0069 | **+0.0017** | 0.3 |
> | internationals (227) | +0.0871 | **+0.0138** | 0.7 |
> | pooled | +0.0332 | **+0.0057** | 0.7 |
>
> Positive = still behind. Second half of dev (post-hoc): franchise +0.0009,
> **international −0.0113**, pooled −0.0027. Dev ROI at the open with a 1¢
> spread: **+9.6% at EV>5% (t=1.5)**; the zero-skill placebo took **0 bets**
> in every cell, in every configuration, all session.
>
> **The one-line diagnosis:** the model is at or ahead of the market wherever
> the market is thin or the teams are obscure (associate internationals
> −0.039, IPL −0.009, ILT20, SA20, the Hundred) and behind exactly where
> squads rotate and the market is liquid and news-driven (full-member
> internationals, T20 Blast, MLC). This is the WNBA "information, not
> estimation" wall in cricket form.
>
> **The decisive evidence that it is an information wall, not a modelling
> one:** a player plus-minus model *handed the actual XIs* — deliberately
> cheating in the model's favour — scores 0.682 against team Elo's 0.608.
> Knowing exactly who plays does not convert into edge at this data volume.
>
> **Three decisions waiting for you** (none taken; all research-only so far):
> 1. **Point-in-time squad announcements** — the real information layer, and
>    the only candidate that addresses the losing cells. A collection job,
>    needs its own registration.
> 2. **The Odds API, ~$30** — a bookmaker open/close on the same IPL matches
>    from 2020, i.e. a second, independent benchmark. Needs your email, a
>    card, and a reCAPTCHA sign-up.
> 3. **`pm-prospective-1`** — the only claim generator, accruing now, scored
>    once at n ≥ 300 or 2027-06-30. Nothing to decide unless you want the
>    lock date moved.
>
> **Not started, deliberately:** a continuous "meta-model" for confidence
> (predicting the model's own reliability per row from coverage, layoff,
> tournament status and prior-match counts, rather than the hand-cut
> per-class buckets used now). It generalises what is already there and is
> the natural next modelling step if the market-beat is worth more effort.
>
> Everything below is the full write-up. No betting, no live arm, nothing
> under any `live/` directory.

Standalone progress document for the cricket revisit, written 2026-08-30 at
the owner's instruction to stop and record the state. The registrations that
govern this work (population, timestamps, gates, iteration rules) live in the
repo-root [`PROGRESS.md`](../PROGRESS.md) under **registration P** (benchmark)
and **registration Q** (the v2 programme and its goal gates). Nothing here
supersedes those; this file is the readable summary.

The earlier BBL study — the one that concluded cricket had no exploitable
wedge — is [`README.md`](README.md). Its verdict stands *for its own data*
and is revised only where this document says so explicitly.

---

## 1. The question, and why cricket was reopened

The owner asked (2026-08-29) to try cricket "in stages", data first, and then
(2026-08-30) to keep iterating "until you come up with a model that beats the
league AND international markets", allowing creative data and techniques and
a market-structure ("meta") view.

The 2026-07-31 cricket work had died on data: BBL had 297 odds-matched
matches, no informative close, and every route to IPL odds was blocked. Stage
0 of this revisit re-opened that question and found one usable source.

## 2. Stage 0 — the data (solved)

Seven routes probed from the owner's Mac; full table in the root
`PROGRESS.md`. Outcome:

| route | verdict |
|---|---|
| **Polymarket** (Gamma + CLOB APIs) | **usable now, free** — 2,158 T20 match-winner markets since 2024-06, 10-minute price paths from market creation to resolution, IPL trading ~$1–2M a match, and *still accruing* |
| The Odds API | usable with payment ($30 buys IPL 2020-26 at two snapshots a match; bookmaker prices, unverified FanDuel cricket coverage) |
| Betfair historic data | true first-traded open and pre-off close since 2015, but every host Cloudflare-403s from this machine (egress IP is a Toronto hosting ASN; Betfair blocks CA/US) — needs a KYC'd account and a residential connection in a permitted country |
| OddsPortal / SofaScore / BetExplorer / aussportsbetting / public datasets | closing-only, IP-blocked, no cricket at all, or non-existent |

Committed archive: `data/raw/polymarket/{markets,prices}.parquet`
(`src/fetch_polymarket.py`, idempotent, refuses to shrink). Play data:
Cricsheet ball-by-ball for **15 T20 competitions, 12,348 matches, 2.82M
deliveries** (2005 → 2026-08-23), plus **3,810 cross-format international
results** (ODI / other one-day / unofficial T20I) used as extra ratings
evidence. Point-in-time ICC T20I ratings are archived from Wikipedia
revision history (`src/fetch_icc_rankings.py`, 18 snapshots 2026-02 → 08);
older history is unrecoverable because the article transcluded a template
that has since been deleted.

## 3. Stage A — what the market itself looks like

`src/pm_benchmark.py` matches markets to Cricsheet fixtures (tiered date
windows, gender-aware, alias-mapped; ambiguous cases dropped, never guessed)
and reads two pre-registered prices off each price path:

- **open** = last price 24 h before the match,
- **close** = last price 45 min before the in-play onset (pre-toss).

The onset is derived from the price path itself, because Polymarket's
`gameStartTime` is a per-league default slot that is wrong by hours on ~30%
of markets. *The first onset rule was defective* (it fired on thin-book jumps
a median 33 h early); the replacement — the end of the last calm two-hour
window before the price settles at 0/1, bounded to ±12 h of the label —
validates at a median **−9 min** against the labels. That correction was made
and disclosed before any verdict was recorded.

Benchmark population: **n = 693** (franchise 466 / international 227),
2024-06-12 → 2026-08-22, volume ≥ $5k.

**Finding that revises the old BBL verdict:** on this exchange the pre-toss
close beats the day-before price by **+0.0168 LL (clustered t = 3.3)**, and
moved prices point at the winner **56.7%** of the time. The BBL study's "no
wedge — nothing informative arrives" was a fact about a bookmaker average on
297 matches, not about T20 cricket. Information does arrive here.

A second market fact shapes everything downstream: **the day-before price is
immature.** Its own favourites at implied 0.595 win 67%, at 0.912 win 92.5%,
at 0.965 win 100%. A model calibrated to *results* (never to prices) is
therefore paid simply for being correctly confident.

## 4. The model

`src/pm_model2.py`. Everything is fit or tuned on matches **before
2024-06-01**; dev is scored only after a change improves the train-era
walk-forward log loss, and every dev touch is logged in the root
`PROGRESS.md`. No market-derived quantity is an input anywhere — prices are
the benchmark and the settlement, never a feature.

Components:

- **Team Elo**, separately for men's internationals, women's internationals
  and franchise leagues: margin-of-victory weighting on a chase-aware runs-
  equivalent margin (wickets in hand and balls to spare count), cross-format
  international results as extra observations, **membership-tier priors** on
  initial ratings, and opponent-based seeding for debutants (a team's first
  fixture reveals its class).
- **Player composition**: phase-aware (powerplay / middle / death) batting and
  bowling values from ball-by-ball, wicket-valued, coverage-shrunk, aggregated
  over an **expected XI**.
- **Expected XI by series continuity** — measured overlap with the actual XI:
  smoothed appearance roster 0.61 international / 0.70 franchise; last XI
  anywhere 0.69 / 0.74; **last XI in the same series 0.84 / 0.86**. The model
  uses series continuity where available.
- **Context terms**, all strictly prior and public: franchise dead rubbers
  from reconstructed standings, bilateral-series state, knockout and
  major-tournament confidence multipliers, venue-country home detection
  (inferred from which domestic league uses a venue — "India" never
  token-matches "Mumbai").
- **Conditions familiarity**: a side from the venue's *region* (subcontinent,
  Gulf, Oceania, Europe, Africa, Caribbean, Americas, East Asia) wins 58.3%
  against 41.5% when the opponent is the regional side, over 20% of
  international matches - a far larger effect than country-exact home
  advantage, and the reason the tuner kept valuing "home" at nearly nothing.
- **A piecewise-monotone capped probability map fitted per segment x fixture
  class** (mismatches need a sharp map - the model ranks them perfectly and
  the market correctly prices 0.97 - while full-member T20Is are near
  coin-flips and need a flat one), plus **walk-forward online recalibration**
  keyed the same way, learning from the model's own past predictions only.

## 5. Result (dev, n = 693)

| cell | session start | final | clustered t |
|---|---|---|---|
| franchise leagues (466) | +0.0069 | **+0.0017** | 0.3 |
| internationals (227) | +0.0871 | **+0.0138** | 0.7 |
| pooled | +0.0332 | **+0.0057** | 0.7 |

Positive = still behind the exchange's day-before price. **The registered
goal gate — both cells below 0.000 with pooled t ≤ −1.5 — was not met.**

Flat-stake ROI at the open (paying a 1¢ spread), dev: **+9.6% at EV > 5%
(clustered t = 1.5)**, +6.6% at EV > 2%; the zero-skill placebo that bets the
market's own price takes **0 bets** in every cell and every configuration.

Split by time (post-hoc; neither half is a claim), the second half of dev
sits at or past parity: franchise +0.0009, **international -0.0113**,
pooled -0.0027. The residual full-dev deficit is concentrated in the first
half, which is dominated by World Cup qualifying fixtures.

Where the model already beats the open, and where it does not:

| beats the open | loses to the open |
|---|---|
| men's associate internationals −0.039 (n=59) | women's mismatches +0.177 (n=11, t=2.3) |
| women's associate internationals −0.015 (n=19) | men's mismatches +0.043 (n=24, t=2.4) |
| IPL −0.009 (n=137) | women's full-member +0.038 (n=44) |
| ILT20 −0.008, SA20 −0.004, Hundred −0.001 | men's full-member +0.032 (n=70) |
| | T20 Blast +0.012 (n=103), MLC +0.025 (n=23) |

## 6. What this says

The model is at or ahead of the exchange wherever the market is thin or the
teams are obscure, and behind exactly where squads rotate and the market is
liquid and news-driven. That is the WNBA lesson in cricket form: what remains
is **information, not estimation**.

Three attempts to break that wall failed, and are recorded so they are not
retried:

1. **Ridge Bradley-Terry ratings** (joint MLE, time-decayed, refit every 30
   days, tier-centred priors) — train 0.610 men's / 0.557 women's vs Elo's
   0.608 / 0.529. A joint fit does not beat Elo on this data.
2. **Player plus-minus from match results**, given the *actual* XIs (a
   deliberate cheat in the model's favour) — 0.682 vs Elo's 0.608. With 1,591
   players over 3,413 matches the system is hopelessly underdetermined, so
   "know the squad" does not mechanically convert into edge at this volume.
3. **International call-ups** as a franchise availability signal — 0.08% of
   expected-XI weight same-day, 0.5% within ±4 days; leagues genuinely
   schedule around international windows.
4. **Rest and travel** in franchise T20 — no signal at all (win rate 0.493
   when a side is more rested, 0.496 when less, 0.496 when equal).
5. **Per-competition adaptive recalibration** — chose a 100-match window and
   pushed franchise dev +0.0017 -> +0.0049. Reverted to per-fixture-class.

One earlier claim was **withdrawn**: an in-sample isotonic "oracle" suggested
the model's ordering was already good enough to beat the open in both cells.
Cross-validated, that vanishes (international as-is 0.563 vs CV-isotonic
0.624), so the international shortfall is a genuine ordering deficit, not a
calibration one. The mistake is recorded rather than quietly deleted.

## 7. Status and what would move it

- **Research only.** No betting, no live arm, nothing under any `live/`
  directory. Any live design would be on an exchange, not FanDuel, and needs
  its own protocol and an explicit owner decision.
- **The only claim generator is `pm-prospective-1`** (`src/pm_prospective.py`):
  markets resolving after the lock, scored once at n ≥ 300 or 2027-06-30. Dev
  has been iterated on many times and is development-grade by construction.
- Candidates that would plausibly close the international gap, each needing a
  fresh registration: point-in-time squad announcements (the real information
  layer), The Odds API's bookmaker prices as a second benchmark from 2020,
  and a women's-specific tier structure (the men's membership list is being
  reused, and women's mismatches remain the single worst cell).

## Reproduce

```
python3 src/fetch_polymarket.py      # market + price archive (committed)
python3 src/fp_ingest.py             # Cricsheet -> matches/deliveries
python3 src/fetch_icc_rankings.py    # ICC ratings snapshots (committed)
python3 src/pm_benchmark.py          # crosswalk + Stage A benchmark
python3 src/pm_model2.py --dev       # tune on train, score dev
python3 src/pm_prospective.py        # the claim arm, when it has rows
```
