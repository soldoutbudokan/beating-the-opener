# Repo audit — will this produce CLV, or profits?

*2026-07-28. Read-only audit of the committed data and code (data vintage
2026-07-26). Every headline number below was independently recomputed from
`modelset.pkl` / `preds_v2.pkl` / `preds_v4.pkl` / `matches.pkl` / the
wehoop parquets / the BP archive; live facts were verified against the
BettingPros API. Verification scripts: session scratchpad `audit/`.*

## Bottom line

The architecture is sound and the negative controls show real discipline, but
**both live experiments, as currently instrumented, should be expected to
show ≈ zero CLV — and the record-keeping will overstate whatever CLV there
is.** Three independent inflation mechanisms account for essentially all of
the published WNBA +5.4% CLV (date-join contamination, mispaired opening
quotes, and non-FanDuel opener books); the honest cell — FanDuel-priced,
sanely-quoted, correctly-dated bets — has **CLV +0.3% to +1.2% with t ≈ 0**.
The soccer +2.2% CLV (t=41.7) is a property of the best-of-30-books envelope,
not the model: a zero-skill control betting the opener's own probabilities
gets **more** CLV (+2.8%) than the model. The soccer live notification rule
corresponds to the one backtest cell with **negative CLV in 9 of 9 seasons**.
Separately, one of the four live WNBA bets was selected off a fabricated
opening price, and its CLV will be fabricated at settlement by the same bug —
the first data points on the scoreboard will falsely confirm the edge.

The model itself is not worthless: on the clean subset the WNBA model still
beats the consensus open on log loss (+0.0017, date-clustered t 3.6), and the
soccer model adds real ROI over the opener-only control (+5.2% vs +1.8%
best-of-book). What is unsupported is that either edge is **tradeable on
FanDuel at the listed prices** — which is exactly what the live experiments
measure.

---

## CRITICAL — live money / record integrity, act before next settlement (~Jul 30)

### C1. The Engstler bet's EV was fabricated, and its CLV will be too

`live_pipeline.py` builds `mu_open` by devigging `opening_line` records taken
independently for over and under (`parse_offer`, wnba/src/live_pipeline.py:96-99).
Verified live from the BP API for event 2682 / threes / Engstler:

- over opening: **line 1.5 @ +194 (FanDuel**, 18:08:38)
- under opening: **line 0.5 @ +150 (Fanatics**, 18:24:46) — different book,
  different line, 16 minutes later

Devigging that pair (booksum **0.74**) yields p_over(1.5)=0.472 → `mu_open`
1.59 — exactly what `picks.csv` shows. A line-consistent read of the market
(consensus over 1.5 +170; the books quoting 0.5 imply p_over(0.5)≈0.68) puts
mu around 1.1–1.2, i.e. the bet's true EV at +194 is ≈ **−12% to −1%**, not
the +31.7% that made it the sheet's top pick (filled $1).

Worse: the consensus **close** mains currently show the same mismatch (over
main at 1.5, under main at 0.5). `close_prob()` (wnba/src/settle_bets.py:87-93)
devigs whatever over/under mains a book shows **without checking the lines
match**, then uses the over's line — so at settlement this bet will be
stamped **CLV ≈ +39%**, fabricated by the same mechanism that fabricated the
EV. The scoreboard's primary metric gets corrupted in the direction of
confirming the edge.

*Remediation (prospective): pick-time and CLV-time guard — require the
over/under pair to be same book (or both consensus), same line, booksum in
[1.00, 1.15]. This single guard would have blocked the Engstler pick and
blocks the fake CLV. For the open bet, hand-annotate `notes` at settlement.*

### C2. All four bets carry the wrong match date; one can settle against the wrong game

BettingPros dates are UTC; wehoop dates are ET. All four bets have
`match_date=2026-07-29` for games played **2026-07-28 ET**. `find_box`
(settle_bets.py:56-58) tries +1 day **before** −1. Verified from the 2026
schedule: none of MIN/IND/POR play 07-29, so on a healthy 07-30 settlement
run the −1 fallback lands correctly. But **MIN plays again 07-30** (MIN@TOR):
if settlement slips ~36h — e.g. a repeat of last week's BP egress outage
(which aborts PART A before settlement) or a delayed wehoop release — the
McBride bet settles against the **wrong game's** box score. Clark and the
two POR bets are structurally safe (their teams next play 07-31, which the
(0,+1,−1) probe never reaches).

*Remediation: store the ET date (or better, resolve the box by event/team
via events.pkl — `bets.csv` already carries `event_id`), or probe (0,−1,+1).*

### C3. CLV is written exactly once and never backfilled

Settlement only touches `status=='open'` rows and stamps CLV in the same pass
(settle_bets.py:113-147). If the archived closing snapshot isn't there in
that hour (the `scrape_bettingpros` step failed, or the API omitted the
event), the bet settles with `clv` blank **permanently** — silent holes in
the declared primary scoreboard. Same single-pass structure in soccer.

*Remediation: let a later run fill blank `clv` on settled rows.*

---

## HIGH — published results are materially overstated; honest live expectation ≈ no CLV

### H1. WNBA: ~24% of the backtest is contaminated by a timezone join bug

BP `date` = UTC date of tip; wehoop `game_date` = ET date. 312 of 641 events
tip 00:00–02:00 UTC, so their BP date is ET+1. The (0,+1,−1) fallback in
`grade_props.py:58-64` / `build_modelset.py:89-98` then probes ET+1, **ET+2**,
ET — and lands on the team's *next game* whenever one exists ≤2 days out.
Recomputed on the committed modelset (28,922 rows):

| join lands on | rows | share |
|---|---|---|
| correct game | 20,715 | 71.6% |
| a later game (+1/+2 days) | 7,346 | **25.4%** |
| no panel row | 853 | 2.9% |

Consequences, both verified: (a) **features leak** — the EW panel state on
contaminated rows already includes the target game's own box score (α=0.18 on
the most recent game); (b) **labels are wrong** — only 76.7% of graded
`actual` values match the true game's stat line. The betting sim splits
accordingly: contaminated bets show CLV +10.1% (EV>2%) / +27.5% (EV>6%) vs
clean +3.4% / +6.8%. The live path computes features correctly (stamps
today's date), so the backtest also mis-measures the live model's true
feature distribution. On the clean subset the log-loss edge vs the open
survives at +0.0017 (date-clustered t 3.6) — smaller but real; note the model
generating it was still *trained* on contaminated data, so a clean retrain is
needed before trusting any number.

### H2. WNBA: mispaired opening quotes manufacture the biggest "edges"

Same mechanism as C1, in the archive: over/under opening costs come from two
independent records. Only 2.4% of props have an opening booksum <1.02
(impossible for a real two-way quote) — but they are **30.8% of EV>2% bets
and 42.1% of EV>6% bets**. The EV filter is a mispair detector. Excluding
them: CLV +5.35% → **+2.77%** (EV>2%), +13.82% → **+7.59%** (EV>6%).

### H3. WNBA: the backtest bets books that don't exist on FanDuel

FanDuel supplies 54.6% of openers in the population but only 39.5% of
selected bets; book 60 (Novig, a no-vig exchange) is 3.9% of the population
and 20.1% of selected bets. Restricting to FanDuel-sourced opens — the only
prices the live experiment can take: **CLV +0.25% (t −0.44)** at EV>2%,
+2.44% (t 1.69) at EV>6%. The honest cell (FanDuel opens ∩ booksum≥1.02 ∩
correctly-dated):

| threshold | n bets | ROI (pg-t) | CLV (pg-t) |
|---|---|---|---|
| EV>2% | 341 | +11.9% (1.4) | **+0.36% (−0.5)** |
| EV>3% (live list) | 226 | +8.8% (0.6) | **+0.29% (−0.7)** |
| EV>6% (live strong) | 63 | +10.8% (0.2) | **+1.22% (0.9)** |

vs the published +5.4% (hardcoded into the scoreboard site as the target
band, site/build_site.py). ROI stays positive but is statistical noise.

### H4. Soccer: the headline CLV is the max-odds envelope, not the model

Best-of-book (`EMax`) has mean booksum 1.0128 and is a literal arb on 15.2%
of matches. Betting EV>2% against that envelope with the **opener's own
devigged probabilities** (zero skill): CLV **+2.80%** (9/9 seasons) — more
than the model's +2.17%. At EV>5%: +5.02% vs +4.24%. Any calibrated
probability harvests this CLV; it measures the envelope, not alpha. The
model's genuine contribution shows in ROI (+5.18% vs +1.84%, t 5.8), the
noisier metric. Also: the t=41.7 is iid; date-clustering alone cuts it to
19.7, with match-level clustering (all three sides betable) untested.

### H5. Soccer: the live "strong pick" rule reproduces the negative-CLV cell

`EV_STRONG = 0.01` **against the average-book price** (soccer/src/live_pipeline.py:314).
Simulating exactly that rule on the backtest: n=2354, **ROI −0.80%, CLV
−2.32%, CLV-positive seasons 0/9** (date-clustered t −11.5). Meanwhile README
and the site quote "+1% to +3% CLV" — the B365-row numbers. FanDuel's early
lower-league price is much closer to an average soft book than to
best-of-30-books. As configured, the notifications the routine sends are the
backtest's money-losing cell.

### H6. Soccer: the live regime has zero backtest evidence

Pinnacle vanished from football-data mid-Jan 2026 (PSH non-null: 2026Q1 162,
2026Q2 **0** — verified). The whole published result (through 2026-01-14)
lives in the Pinnacle-anchor regime; live now anchors on the average book
(overround 7.2% vs 3.3%) and will grade CLV against the average close, a
different yardstick than the backtest's Pinnacle close. Also `EAHH` is NaN in
all recent training rows but populated for live fixtures (build_dataset KEEP
omits AvgAHH) — a straight train/serve skew. The hourly all-data retrain
scheme was never backtested either.

### H7. The experiment can't resolve the honest effect size

wnba PROTOCOL claims "a season of ~150–400 bets resolves a CLV edge of that
size decisively" — true at +5.4%. At the honest cell's +0.3%, with observed
per-player-game CLV sd of 0.095, t=2 needs **~4,000 player-games** — an order
of magnitude beyond a season. The experiment *can* reject the published
+5.4% quickly; it cannot distinguish +0.3% from zero.

---

## MEDIUM — robustness and backtest/live mismatches

- **"Still at the opening line" is really ±15¢** of juice drift vs an opener
  that may be a different book's (wnba/src/live_pipeline.py:251-254); the
  protocol states the rule absolutely. The consensus price is parsed and
  never used as a sanity check.
- **Sim populations condition on unknowable info**: player played (voids
  dropped), a two-sided close exists; `open_book` is fed to the model
  (learns book-specific mispricing untradeable on FanDuel); σ(μ) fit on the
  full panel including the eval window; `absent_prior_ew_min` computed by a
  different rule live vs training.
- **Soccer model selection on the OOS window**: ens vs stack vs gbmmove, and
  four v1→v4 iterations, all scored on the same 63,586 matches for a
  +0.0006-nat effect.
- **Sizing**: bankroll = settled P&L only (open exposure invisible to Kelly);
  soccer can pick H, D and A of one fixture, each quarter-Kelly'd against the
  full bankroll; `min_odds_5pct = 1.05/p` asks FanDuel to beat the European
  best-book-implied fair by 5%.
- **Live CLV t-stat is unclustered** (settle_bets.py) while the backtest's is
  player-game-clustered — live significance will look better than the
  backtest's own standard.

## LOW — hygiene

- Site hardcodes the CLV target bands (+5.4% / +1–3%) and a stale ":51 UTC"
  routine string; constants can drift silently from the code.
- The four fills were logged at $1 flat vs sheet stakes $4/$1.5/$1/$1.5 with
  no `notes` — the audit trail can't tell deliberate flat-staking from a
  mis-log. (Kelly arithmetic itself verified correct on all four.)
- Soccer protocol says round stakes to $0.50 / skip <$0.50; soccer code
  rounds to cents with no floor (wnba code matches the protocol).
- "One bet per player per game" exists only in prose; notification dedupe
  exists only in prose; `--refresh-days` documented but not implemented;
  README reproduce line omits `ABLATE_ABSENT=1` though the headline table
  uses it; current-season CSV re-downloads validated only by `len > 500`.

---

## What's actually good

Anchor-on-the-market + predict-the-move is the right architecture (v1's
failure documented honestly). The shift-then-ewm feature machinery is
leak-free *in isolation*. Walk-forward hygiene (imputer/scaler fit on train
only, expanding windows) is right. Two null results (NBA, cricket BBL) were
accepted and published — including killing a p=0.03 home-bias candidate that
failed OOS. Ops hardening (canary aborts, merge-never-shrink archive, outage
markers, no-churn commits) is genuinely thoughtful. The instinct to grade on
CLV rather than P&L is correct — which is exactly why the CLV measurement
pipeline needs to be trustworthy.

## If you want the experiments to mean something

1. **Now**: add the same-book/same-line/booksum guard at pick time and in
   `close_prob`; fix the UTC→ET date handling (or settle by event_id); allow
   CLV backfill. Annotate the Engstler row so the first scoreboard entries
   aren't poisoned.
2. **WNBA**: rebuild the modelset with correct dates, retrain, and re-run the
   backtest restricted to FanDuel-sourced, sanely-paired opens. That number —
   currently ≈ +0.3%, t≈0 — is the honest prior. If it stays ≈0, the polite
   conclusion is that WNBA joins NBA and cricket as a control.
3. **Soccer, before the August go-live**: re-derive expectations in the
   post-Pinnacle regime (avg-book anchor, avg-book close as CLV yardstick),
   fix the strong-pick threshold or the published expectation to refer to the
   same cell, and decide whether an avg-book-priced FanDuel experiment is
   worth running at all given the backtest's own avg-book row is −2.3% CLV.

---

# Follow-up pass — second audit

*Independent re-audit run after the review above, same data vintage. Method was
deliberately different: measure first, from `preds_v2.pkl` / `preds_v4.pkl` /
`props.pkl` / `matches.pkl` / the wehoop parquets / the BP archive, and treat the
code as the explanation rather than the evidence. Most of the findings above
reproduced; this section records only what is **new or materially sharper**, plus
the corroborations that firm up the earlier calls.*

## New — not in the first pass

### N1. The distribution layer is over-biased, and it inverts the whole portfolio

`dist_utils.py` is treated above as neutral machinery. It is not. Measured over
23,644 graded props at the opening line:

- realised over rate **0.4678**
- model mean P(over) **0.4910** (+2.33pp)
- **the opener's own implied P(over) 0.4889 (+2.11pp)**

The bias is present when the machinery is fed *the market's own prices*, which
rules out the market and the model as the cause — it is the parametric inversion.
~6σ, negative in all 8 markets (`reb_ast` −4.7pp, `threes` −3.8pp, `pra` −0.8pp).
Likely cause: `SIGMA_AB` is fitted as the variance of the actual around the
player's *fast EW mean*, folding EW estimation error into σ.

A 2.3pp bias on a coin-flip prop is ~4.5% of EV, so **the entire EV ≥ 3% listing
tier sits inside the model's own calibration error**. It points at overs, which is
why the sim is 67-79% overs and why all four live bets are overs. Correcting it on
both the model and the CLV benchmark (apples-to-apples — the benchmark shares the
bias, so an overs-heavy book scores inflated CLV automatically):

| | n | % over | ROI | CLV |
|---|---|---|---|---|
| as published (EV>2%) | 1,190 | 67% | +10.6% (t=2.9) | +5.35% (t=2.3) |
| coherent-devig only | 819 | 79% | +11.8% (t=2.7) | +2.71% (t=3.8) |
| **coherent + calibrated** | 1,595 | **8%** | **+13.4% (t=3.8)** | **+2.48% (t=5.5)** |
| coherent + calibrated, EV>6% | 245 | 15% | +26.7% (t=3.6) | +8.05% (t=5.3) |

So a calibrated version of this model bets **the opposite side** of what is
currently on the sheet, and looks better doing it. This is the single largest
correction available, and it is independent of H1/H2/H3 above.

### N2. The staleness gate is a filter *for* props that structurally cannot pay CLV

A prop whose price never moves returns CLV = −(the vig you paid), deterministically
— there is no mechanism by which it can pay. Measured:

```
EV>2% picks, price MOVED open->close   n=1030  ROI +12.31%  CLV +6.84%
EV>2% picks, price NEVER moved         n= 160  ROI  -0.29%  CLV -4.26% (t=-13.4)
   coherent-devig subset:                                   CLV -6.84% (t=-43.5)
```

Replaying the live gate (`live_pipeline.py:252`) against FanDuel's archived close
on the 1,190 EV≥2% bets:

```
FanDuel quotes the prop at all       41.3%
 ...same line at close               29.2%
 ...passes the full stale gate       18.9%

gate PASSES (FD never moved)   n= 225   ROI  -4.63%   CLV -3.66% (t=-7.7)
gate BLOCKS (FD moved)         n= 965   ROI +14.17%   CLV +7.45% (t=+2.6)
```

**Caveat, stated plainly:** this replay uses the *close*, so "passes" means FanDuel
never moved all season, while the live gate at pick time cannot distinguish "hasn't
moved yet" from "will never move". −3.66% is therefore a lower bound, not the
expected live value. The mechanism is the point: the gate is positively correlated
with the never-move population, and that population is a guaranteed CLV loser. This
sharpens the ±15¢ note in MEDIUM above from a precision quibble into a selection
problem.

### N3. Soccer: two trained features go to exactly zero the moment Pinnacle is gone

H6 above identifies the regime change. Two concrete mechanisms, both verified:

- `zo` is `logit(devig_shin(PS if present else EAvg))`, and
  `three_way_feats(..., EAvg)` returns `logit(devig_shin(EAvg)) − zo`. Under the
  fallback those are the same quantity, so **`disavg_h` and `disavg_a` are exactly
  0** for every live fixture (numerically confirmed: max|disavg| = 0.0 over 3,000
  fallback rows) while carrying real variance in training.
- `overround_anchor` has median **0.0305** in training (94.4% of 2012+ rows are
  Pinnacle-anchored); every live row will be avg-anchored at ~**0.0677**, a value
  only **6.19%** of training rows reach.

Separately: switching the CLV yardstick from the Pinnacle close (overround 1.0387)
to the average-book close (1.0772) costs about **−0.5 to −0.6pp** of measured CLV
on identical bets — smaller than the regime change itself, but it moves the live
result against the published expectation.

### N4. On artifact rows the *close* is broken too, so their CLV is unmeasurable

H2 above excludes mispaired opens. Going further: among bets whose **open** is
incoherent, the **close** is also incoherent **39.2%** of the time, versus **0.5%**
for clean rows. Their apparent "+22% CLV" at EV≥6% is therefore not merely
overstated, it is measuring the same defect twice. The honest cell is
bets coherent at *both* ends: n=819, ROI +11.8%, CLV **+2.71%** (t=3.8).

## Corroborated independently

- **The Engstler bet (C1).** Re-verified live against the BP API: over 1.5 @ +194
  (book 10, 18:08:38) vs under 0.5 @ +150 (book 14, 18:24:46), two-way sum
  **0.7401**. Same conclusion, same mechanism.
- **The date-join bug (H1),** reached by a different route: resolving each prop's
  true game via the event schedule converted UTC→ET, then replaying
  `grade_props.find`. Result **26.83% wrong-game (8,071 of 30,083)**, of which
  **7,599 are off by exactly +2 days** — consistent with H1's 25.4%. The date
  convention itself was verified rather than assumed: over 309 evening events the
  home team has a wehoop game on the ET date **78.0%** of the time and on the UTC
  date **1.6%**.
- **The mispaired-quote enrichment (H2).** Base rate of incoherent opening pairs
  2.18% across 27,009 archived pairs; 30.8% of EV≥2% bets, 34.5% at EV≥3%, and
  **42.1% at EV≥6%** — a 19× enrichment at the notification threshold.
- **The soccer placebo (H4).** Substituting the opener's own devigged probabilities
  for the model — zero information — gives CLV **+2.80%** at EV>2% vs the model's
  +2.17%, and **+5.02%** vs +4.24% at EV>5%. Decomposed: the 10,706 bets shopping
  would have found anyway carry +3.94%; the 20,486 the model *adds* carry only
  **+1.25%**. Underlying arithmetic: max-of-25-books early carries 1.0120 overround
  (−1.18% blind EV) against the average book's 1.0718 (−6.69%), while the ens
  deviates from the devigged opener by just **0.76pp** on average.
- **The soccer notifier (H5).** Confirmed, with a note: priced at best-of-book the
  notified subset is not negative, it is merely the *worse* slice — playable and
  notified n=1,893, ROI +5.43%, CLV +3.23%; playable but not notified n=6,376, ROI
  **+8.07%**, CLV **+4.54%**. The notified picks are where the model disagrees most
  with the opener (0.0240 vs 0.0108) — its error tail. The −2.32% CLV figure in H5
  is the avg-book *price*, which the protocol's own playability gate would not let
  you take; the real cost is that ~77% of the playable set is never surfaced,
  cutting realised volume ~4× and with it the power of the season.

## Also confirmed, smaller

- `PANEL_FEATS` is defined twice (`build_modelset.py:23`, `train_eval.py:26`); live
  imports one, the backtest the other. Verified identical today — 36 entries, same
  order — but nothing asserts it, and a divergence would silently feed the live
  model a permuted feature vector.
- `soccer/src/live_pipeline.py:246` `is_fixture = FTR.isna()` would score any
  result-less historical row as a fixture; currently 0 rows are affected, so it is
  latent rather than active. The live exposure is the date filter instead — played
  matches persist in `fixtures.csv` for months.
- `fixture_panel` stamps `game_date = now()` for a game 1-2 days out, so `rest` is
  understated on every live row.
- `hash((r.event_id,))` at `live_pipeline.py:155` is non-deterministic under
  `PYTHONHASHSEED` if `event_id` is ever a string.

## Revised order of work

The first pass's ordering holds, with N1 inserted — it is cheap, it is independent
of the date and devig fixes, and it changes which side of the market the model
bets:

1. **Now** — same-book / same-line / booksum guard at pick time and in
   `close_prob`; reject any pick where `mu_model` contradicts the recommended side
   (the Engstler pick had `mu_model` 1.52 < `mu_open` 1.59 yet was listed as the
   sheet's strongest *over*; that contradiction alone would have caught it); allow
   CLV backfill.
2. **Then** — UTC→ET dates (or settle by `event_id`), probe order `(0, −1, +1)`.
3. **Then, before retraining** — recalibrate `SIGMA_AB` against a conditional
   projection plus a walk-forward per-market offset. Acceptance test: fed the
   *market's own* prices, mean implied P(over) must match the realised over rate
   within 0.5pp overall and 1pp per market. Only then rebuild and retrain, so the
   clean-subset number is measured against a calibrated model rather than a
   +2.3pp-biased one.
4. **Soccer, before August** — add the placebo control column permanently; run a
   full walk-forward on the average-book anchor (data exists back to 2012) and drop
   `disavg_*` when the fallback fires; retarget `strong` at the playable condition.

---

# Remediation log — 2026-07-28

*Every item in both passes above was either fixed, measured-and-documented, or
found to rest on a wrong mechanism (noted below). Commits: `4a5b134` (1/6,
live-money integrity), `67ea267` (2/6, ET dates), `00d307a` (3-4/6, WNBA
calibration + hardening), `c9406a2` (5/6, soccer), plus this one (6/6, docs).*

## Fixed in code

- **C1** — pick time AND CLV time now require a coherent two-way quote (same
  book, same line, booksum 1.00-1.15); the bet side must agree with the
  predicted move. The Engstler mechanism can no longer produce a pick or a
  CLV stamp; the live sheet drops ~25% of BP offers under this guard.
- **C2** — settlement resolves the game by `event_id` (ET date from
  events.pkl + the box row must belong to one of the event's two teams,
  probe (0,−1,+1)). Replayed: the McBride slipped-settlement case can no
  longer grade the next game. `match_date` on the four open bets corrected
  to ET; the Engstler row carries a fabricated-EV note.
- **C3** — blank CLV on settled rows is backfilled by later runs, both
  markets. WNBA also stamps `clv_cal` (see N1 below), backfilled the same way.
- **C4** (found 2026-07-29) — the WNBA archiver gated on `scheduled[:10] >=
  today`, comparing a **UTC** event date against a UTC today while the rest of
  the pipeline keys off the **ET** game date. Wrong in both directions: an
  8pm-ET tip carries tomorrow's UTC date, so its close went unarchived for a
  full extra day (all five settled bets sat with blank CLV, backfill starved
  because no file existed); and a 6-8pm-ET tip shares its UTC date, so its
  offers were archived at the next 00:21Z run — event 2679 was snapshotted
  0.93h after tip, in principle mid-game. Now gated on timestamps:
  `is_final()` requires `now >= tip + FINAL_CUSHION_H` (5h).
  **Only the missing-close half of this bug ever bit.** Audited the whole
  archive by first-commit time vs tip: four events missing (2680-2683) and
  one snapshotted early (2679). Re-fetching 2679 changed nothing but the
  response's own `utc`/`ts` metadata — all 52 main quotes byte-identical —
  because BP freezes a prop's `updated` stamp at tip (measured: tip+7s to
  tip+15s across 2679, tip+20s to tip+30s across 2680-2683). So an early
  snapshot still captures the true close, and no CLV or modelset value was
  ever wrong from it. The timestamp gate is kept as the correct invariant,
  not as a repair: it costs nothing and stops depending on a freeze
  behaviour BP never promised. Repair used a new `--refetch` flag that never
  trades a good file for an empty response. Net effect: the four missing
  closes arrived and CLV stamped on all five settled bets (mean -4.14%,
  `clv_cal` -6.36%).
- **C5** (found 2026-07-29, chasing "the CLV column looks wrong") — the CLV
  column was *right*, but the machinery under it had an off-by-one on
  whole-number lines. `dist_utils` documented "half-lines assumed" and the
  Poisson branch took `k = ceil(line)`, so `over 2.0` was scored as
  `P(X>=2)` when a whole-number line pushes at 2 and means `P(X>=3)` — a
  **27pp** error (0.594 vs 0.323 at mu=2). Whole-number lines are not
  hypothetical: **5.8%** of archived consensus closes and 2.45% of openers
  sit on one, so the next bet whose close landed on an integer would have
  been stamped with a badly wrong CLV. Blast radius already realised: 263
  archived coherent closes carried a wrong `mu_close` (mean 0.99 counts),
  and because the live model's training target is
  `move = (mu_close - mu_open)/scale`, 10 of those were **corrupted training
  labels, each off by ~0.74 sd of a target whose 1st-99th percentile span is
  only +-0.35 sd** - pathological outliers, plus the per-player momentum EW
  built from them. Fixed to `k = floor(line) + 1` in both `p_over` and
  `implied_mu`. Verified **exact half-line parity** (bit-identical on every
  half-line x mu tested), so the model, the picks and all five stamped CLVs
  are untouched by the change itself; only whole-number lines move. Retrained
  on the corrected labels the live sheet went from 3 marginal picks to 0.
  Calibration evidence, 25,553 graded coherent closes re-expressed to a
  shifted line: at +-1.0 (half-line to half-line) bias is +1.5/+2.3pp against
  a +2.2pp over-shade baseline - i.e. the re-expression is sound, which is
  why the two extrapolated rows (Leite 5.5->6.5, Engstler 0.5->1.5) stand.
- **H1** — BP event dates are converted UTC→ET at the source. Rebuilt:
  later-game joins 25.4% → **0.10%**, stored labels match the true game
  76.7% → **100%**.
- **H2/N4** — per-side opening records archived; `open_coherent`/`coh_close`
  flags in the modelset; training and eval restricted to coherent-both-ends.
- **H3** — the live pipeline scores FanDuel-sourced openers only, and the
  published expectation is the FD cell.
- **H5** — `strong` = best-book EV ≥ 5% (the playable condition); the
  avg-book EV>1% rule is gone.
- **H6** — `AvgAHH/AvgAHA` added to the dataset and the `EAHH` fallback
  chain (post-Pinnacle EAHH went 0 → 3652 non-null); `disavg_missing` flag
  added (N3); `train_eval_avg.py` replays the identical live model on the
  avg anchor with a permanent placebo control (H4).
- Medium/Low sweep: Kelly sizes off bankroll minus open stakes; one bet per
  player per game and bets.csv-dedupe enforced in code (`play`/`already_bet`
  columns); live CLV t reported match-date-clustered; soccer stakes round to
  $0.50 with a floor; raw CSV overwrites require a football-data header;
  result-less historical rows can't score as fixtures; fixture stubs get the
  real ET game date (rest fix) and deterministic ids; `PANEL_FEATS`
  single-sourced; dead `--refresh-days` flag removed; site strings and bands
  regenerated from the honest numbers.

## Measured — and where the audit's mechanism was wrong

- **N1**: the round trip market price → `implied_mu` → `p_over` is *exact*
  (bias +0.0000) — `SIGMA_AB` was NOT the cause, so it was not refit. The
  +2pp over-bias lives in the devigged prices themselves, at the open AND
  the close, i.e. the books shade the popular side and no devig removes a
  one-sided skew. It is corrected with an expanding per-market logit shift
  (`fit_shade`) applied to the model and to a second CLV yardstick
  (`clv_cal`).
- **The acceptance test (±0.5pp overall / ±1pp per market) is unattainable
  out-of-sample**: the shade drifts (+1.7pp → +2.6pp → +3.1pp by quarter,
  then **−1.6pp in Jul 2026**, ~4σ swings; open and close drift together).
  Expanding-window calibration achieves 0.55pp / 1.38pp walk-forward vs
  1.79pp raw; trailing windows do worse (they chase the drift). Because the
  shade can invert, EV is never allowed to come from the shade alone — the
  move model must point the same way.
- **The audit's "coherent + calibrated" ROI (+13.4%, t 3.8) does not
  survive walk-forward calibration + the tradeable-cell restriction**: the
  honest FD cell at the live rule is ROI +3.1% (pg-t 0.5), CLV −2.9% vs the
  raw close (mechanical for an under-heavy book), +3.2% (pg-t 7.8) vs the
  shade-adjusted close. H7 stands: one season cannot resolve effects this
  small.

## The re-derived bottom lines

- **WNBA**: the opener inefficiency is real (LL edge, clustered t 4.8; wedge
  59% directional, p 1e-50) but the FanDuel-tradeable edge is statistically
  indistinguishable from zero at one season's volume. The live experiment
  continues as measurement; both CLV yardsticks are on the scoreboard.
- **Soccer**: the 9/9-season result was a Pinnacle-anchor result. In the
  regime live actually runs in (avg anchor since Jan 2026), the identical
  model does **not** beat its own anchor (LL −0.0006, t −0.3), best-book ROI
  is +0.6% (t 1.1), and its +1.1% CLV is less than the zero-skill placebo's
  +3.7%. README/PROTOCOL now say so, and whether the August experiment is
  worth running at all is flagged as an open decision for the owner.

**Decision (2026-07-28, owner):** the soccer live experiment is **cancelled
before launch** on the strength of the re-derivation above. The `edge-watch`
routine is WNBA-only; the soccer pipeline and protocol remain in the repo as
the record.
