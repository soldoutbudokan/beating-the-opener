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
