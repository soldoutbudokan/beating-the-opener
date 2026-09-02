# Cricket — from-scratch pricing vs a prediction market

> ## Status for review — 2026-09-02 (session re-opened to keep trying)
>
> **Both cells are now at parity with the exchange's day-before price;
> the registered goal is still not met.** The 2026-08-30 model was
> reproduced exactly on a fresh container, the pipeline made fast enough
> to run fourteen full evaluations, and one change did most of the work:
> **opponent-quality-adjusted per-ball player values** (a run off Bumrah
> is not a run off Nepal's fifth bowler) took the international cell from
> +0.0138 to +0.0008 in a single step. Blast dead rubbers from Cricsheet's
> group labels took franchise from +0.0017 to +0.0002.
>
> | dev cell (n=693) | 2026-08-30 | **2026-09-02** | t |
> |---|---|---|---|
> | franchise leagues (466) | +0.0017 | **+0.0002** | 0.0 |
> | internationals (227) | +0.0138 | **+0.0008** | 0.0 |
> | pooled | +0.0057 | **+0.0004** | 0.0 |
>
> Positive = still behind, by less than 0.001 in each cell. Dev ROI at the
> open with a 1¢ spread: **+13.2% at EV>5% (n=469, t=2.0)**; the placebo
> takes 0 bets. Second half of dev (post-hoc): franchise +0.0001,
> international −0.0187, pooled −0.0055. With one market per match
> (nine matches carry a re-listed duplicate) the pooled gap is −0.0003.
>
> **Why the gate is still unmet:** it asks for both cells below zero AND a
> pooled clustered t ≤ −1.5. At this sample the t condition needs a gap of
> roughly −0.012, an order of magnitude beyond anything measured in either
> direction. The cells can be pushed below zero; the t condition is a
> sample-size question the prospective arm has to answer.
>
> **The 2026-08-31 meta-model was evaluated first, as instructed.** It
> wins on train in every form (shared, per segment, with the component-
> disagreement feature, with a wider penalty) and loses on dev in every
> form. Its largest weight is on prior-match count: it learns that full
> members were coin-flips in 2018-24, and the exchange's era disagrees.
> Recorded as rejected so it is not retried.
>
> **Nothing to decide.** Research only; nothing live; `pm-prospective-1`
> keeps the 2026-08-30 recipe and **`pm-prospective-2`** is registered on
> today's recipe (markets resolving after 2026-09-02). The two owner
> options from 2026-08-30 stand unchanged: point-in-time squad
> announcements (the information layer that would address the remaining
> full-member loss) and The Odds API at ~$30. The archive is frozen at
> 2026-08-29 because Polymarket is unreachable from this container.
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
- **Opponent-adjusted values (2026-09-02)**: every batting delta is
  credited with the strictly-prior quality of the bowling XI it was scored
  against, and every bowling delta with the quality of the batting XI it
  was bowled to (coefficient 1.0 on train; 1.5 and above make the values
  diverge). Internationals mix tiers, so unadjusted values flattered
  players who feast on weak opposition; the player component's train log
  loss went 0.668 → 0.661 (women's internationals 0.601 → 0.581) and the
  blend gave it half the weight in full-member classes.
- **Blast dead rubbers (2026-09-02)**: the T20 Blast plays North/South
  groups of nine with four qualifiers each; Cricsheet labels the group, so
  its standings are reconstructed like every other league's.
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

| cell | 2026-08-30 start | 2026-08-30 | **2026-09-02** | clustered t |
|---|---|---|---|---|
| franchise leagues (466) | +0.0069 | +0.0017 | **+0.0002** | 0.0 |
| internationals (227) | +0.0871 | +0.0138 | **+0.0008** | 0.0 |
| pooled | +0.0332 | +0.0057 | **+0.0004** | 0.0 |

Positive = still behind the exchange's day-before price. **The registered
goal gate — both cells below 0.000 with pooled t ≤ −1.5 — is not met.**
Log loss 0.6891 vs 0.6889 (franchise) and 0.5250 vs 0.5242
(international); calibration +1.2pp / −1.0pp; against the pre-toss close
+0.017 (t=1.8, no tripwire).

Flat-stake ROI at the open (paying a 1¢ spread), dev: **+13.2% at EV > 5%
(n=469, clustered t = 2.0)**, +11.3% at EV > 2%; the zero-skill placebo
that bets the market's own price takes **0 bets** in every cell and every
configuration.

Split by time (post-hoc; neither half is a claim): first half franchise
+0.0002, international +0.0167; second half franchise +0.0001,
**international −0.0187**, pooled −0.0055. With one market per match
(nine matches carry a re-listed duplicate, 18 rows) the pooled gap is
−0.0003.

Where the model beats the open, and where it does not (2026-09-02):

| beats the open | loses to the open |
|---|---|
| men's associate internationals −0.052 (n=59) | women's mismatches +0.107 (n=11) |
| women's associate internationals −0.024 (n=19) | men's full-member +0.026 (n=70) |
| CPL −0.060 (n=13), IPL −0.010 (n=137) | women's full-member +0.024 (n=44) |
| ILT20 −0.013, SA20 −0.009 | LPL +0.099 (n=14), MLC +0.027 (n=23), T20 Blast +0.010 (n=103) |
| men's mismatches −0.013 (n=24) | one row, Switzerland v Croatia at 0.977, lost: 0.010 of the international gap by itself |

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
6. **The meta-scale confidence model** (2026-09-02) — shared, per segment,
   with component disagreement as a feature, with a wider penalty grid:
   every version wins on train (as far as 0.62648 raw against 0.63227) and
   every version loses on dev (best pooled +0.0048 against +0.0004). Its
   largest weight is on prior-match count; it learns the train era's
   reliability structure, which the exchange's era does not share.
7. **Women's-specific membership tiers** — train gain in the women's
   international cell, dev international +0.0138 -> +0.0167.
8. **Letting the online recalibration switch off** where raw beats every
   window on train — costs 0.0004 on train, pays 0.008 on dev franchise:
   the recalibration is tracking a regime change from the train era.
9. **Dedicated probability maps for the full-member-v-associate classes**
   (row floor 120 -> 90) — fits on 94 and 115 rows, dev international
   +0.0008 -> +0.0062.
10. **Wider opponent grid** (shrink 75 lifts the player component, not the
    blend) and **venue par normalisation** (player 0.66062 -> 0.66050, blend
    unchanged) — no gain at the blend level.

One earlier claim was **withdrawn**: an in-sample isotonic "oracle" suggested
the model's ordering was already good enough to beat the open in both cells.
Cross-validated, that vanishes (international as-is 0.563 vs CV-isotonic
0.624), so the international shortfall is a genuine ordering deficit, not a
calibration one. The mistake is recorded rather than quietly deleted.

## 7. Status and what would move it

- **Research only.** No betting, no live arm, nothing under any `live/`
  directory. Any live design would be on an exchange, not FanDuel, and needs
  its own protocol and an explicit owner decision.
- **The claim generators are `pm-prospective-1`** (the 2026-08-30 recipe,
  `pm_model2.py --no-opp --no-blast-groups`, lock 2026-08-30) **and
  `pm-prospective-2`** (the 2026-09-02 recipe, the defaults, lock
  2026-09-02), each scored once at n ≥ 300 or 2027-06-30 by
  `src/pm_prospective.py --lock-date`. Dev has been iterated on many times
  and is development-grade by construction.
- The remaining loss is where it was: full-member internationals (+1.8 LL
  units on 70 rows) and women's fixtures. The market-free levers tried
  this session are exhausted at the blend level; what remains is the
  information layer (point-in-time squads) or more sample.
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
python3 src/pm_model2.py --dev       # tune on train, score dev (model of record)
python3 src/pm_model2.py --cache --dev   # reuse the cached Elo/player stages
python3 src/pm_diag.py               # where the dev loss sits
python3 src/pm_prospective.py --lock-date 2026-09-02   # pm-prospective-2, when it has rows
```
