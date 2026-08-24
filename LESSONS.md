# Lessons learned

*Written 2026-08-24, one month into the WNBA v3 live experiment and four
markets into the programme. This distils; it does not supersede. The
measurements live where they were made — [AUDIT.md](AUDIT.md),
[PROGRESS.md](PROGRESS.md), the subproject READMEs, and the live record in
[`wnba/RESULTS.md`](wnba/RESULTS.md) — and every number quoted below is
quoted from there, not remeasured here.*

The one-line version: **every dollar this project lost, it lost to its own
measurement errors, not to the market.** The market said "small edge, maybe"
from the start and has never contradicted itself. What changed between the
inflated July numbers and the clean August ones was never the model — it was
the honesty of the instrumentation around it. Most of what follows is
variations on that theme.

---

## 1. Measurement

**Recompute headline numbers from raw data before believing them — three
independent artifacts can stack in the same direction.** The published WNBA
+10.6% ROI / +5.4% CLV was inflated by a UTC/ET date-join bug, mispaired
opening quotes, and non-FanDuel opener books (AUDIT H1–H3). Each alone was
subtle; together they accounted for essentially the entire published edge.
The honest cell — FanDuel-priced, coherently-quoted, correctly-dated — was
CLV +0.3% to +1.2% at t ≈ 0. Nothing about the pipeline *looked* broken.

**Run the zero-skill placebo, always.** Soccer's +2.2% CLV came with t=41.7
and 9/9 positive seasons — and a control that bet the opener's own devigged
probabilities (zero skill by construction) harvested *more* CLV (+2.8%) from
the same best-of-30-books envelope (AUDIT H4). A benchmark you can beat with
no model is measuring the benchmark, not you. The strongest-looking t-stat
in the repo's history was an artifact of the yardstick.

**Know each metric's actual break-even before staring at it.** CLV's
break-even is not zero: a prop whose line never moves scores CLV ≈ −vig
deterministically (AUDIT N2: the never-moved population runs −4.26% CLV at
t=−13.4 no matter what you know). An opener-only strategy therefore
*expects* negative raw CLV — the v3 dev analogue registered −4.6%, "negative
by construction" — and its edge, if real, realizes at settlement, invisible
to the close. Corollary learned twice: write the registered expectation next
to the metric wherever it is displayed, because anyone reading "CLV −3%"
cold — including a careful reader, including the assistant running the
analysis — will score it against zero and misread expected behavior as
failure.

**Calibration against the model's own claims is the fastest tripwire.** The
2026-08-08 audit's decisive number was not ROI or CLV: on 59 settled bets the
model's own `model_p` predicted 37.1 wins and 28 happened (z=−2.48, unders
alone z=−3.03) while the market's vig-inclusive probabilities passed the
same test (z=−0.49). That was detectable in one week. ROI at these effect
sizes needs thousands of bets (AUDIT H7: t=2 on the real CLV effect needs
~4,000 player-games); calibration z catches a model lying to itself in ~60.

**State the power calculation before launch, and let it set the rhetoric.**
"A season of bets resolves this edge" was claimed and was wrong by an order
of magnitude (AUDIT H7). The honest framing that replaced it — *this
experiment can reject a large edge and measure a small one; it cannot prove
one* — changed staking, expectations, and how results get described. An
experiment that can't statistically deliver a verdict is still worth running
as measurement, but only if nobody is allowed to pretend otherwise later.

## 2. The markets

**The thesis needed two gates, and the negative controls are what make the
positives believable.** An exploitable opener needs a *lazy open* (low
attention per price) and an *informative close* (real information arriving
before tip). NBA fails the first — the closing line wins every market
tested, an encompassing regression finds the model adds nothing (coef −0.08,
p=0.43), and even a look-ahead oracle refitting on held-out seasons (0.5915)
falls short of the line (0.5818): ~82% of the market's private information
is orthogonal to all 95 observables (`nba/`). BBL cricket fails the second —
lines move plenty (290/297 matches, mean 3.6pp) but point at the winner
46.2% of the time: the moves are toss noise, nothing informative arrives
(`cricket/`). Two honest "no"s from the same methodology are the best
evidence the "yes"es aren't procedural.

**Statistically beaten ≠ tradeable.** The WNBA anchored model beat the
consensus open on log loss at clustered t=4.8 — and the FanDuel-tradeable
cell of that same edge was ROI ≈ +3% at t ≈ 0.5 (`wnba/README.md`). The cell
you can measure is always larger and softer than the cell you can bet:
coherent quote, right book, still-at-the-open, one side, after vig. Every
market here shrank dramatically on the way from eval table to betslip.

**An edge is a property of a regime, and regimes end without telling you.**
Soccer beat the Pinnacle-anchored opener in 9/9 out-of-sample seasons
(p=0.0039); Pinnacle vanished from the data feed in Jan 2026, and the
identical model replayed on the average-book anchor it would actually trade
against showed no edge at all (−0.0006 LL, t=−0.3). The live experiment was
cancelled two days before its first bet because someone thought to replay
the live regime rather than trust the historical one (`soccer/README.md`).
Cheapest save in the repo.

**Implausibly large claimed edges are defects, not gifts.** Every audited
claim above ~25% EV was mechanical: Engstler +31.7% (mispaired opening quote,
booksum 0.74), Plum +29% (42-day-stale player state priced onto the wrong
team), Leite +51% (frozen panel while FanDuel walked the line 5.5→8.5). The
protocol now quarantines big claims (`SUSPECT_EV`) instead of celebrating
them, and the quarantine catches new ones weekly. The market is allowed to
be a little wrong; when it looks *very* wrong, it's your pipeline.

## 3. Modelling

**Anchoring on the market's price means you never had an opinion.** The
retired architecture inverted `mu_open` out of the opening price and
predicted `open + move` — so it could never disagree with the market about a
player, only about the market's next twitch, and it inherited every defect
of the quote it anchored on (Engstler's fabricated EV *was* a fabricated
`mu_open`). The rework prices from first principles and uses the market only
to score and to bet against. But record the full sequence honestly: the
from-scratch approach lost *first* — soccer v1 fed odds to a GBM and lost to
the opener by 0.022, `nba/` lost to the close outright — and anchoring was
adopted as the fix. Both failure modes are real; the second was just
discovered later.

**Leakage scales with surface area.** The `nba/` leakage finding
manufactured +12.3% simulated ROI out of nothing, and a from-scratch model
touches raw play data everywhere — far more places for exactly that bug than
an anchored model has. The rework's insistence on pre-registered gates,
one-shot holdouts, and prospective registrations (`fp-prospective-1/2`,
scored on data that didn't exist at registration time) is the countermeasure,
not ceremony.

**Pre-registration is only worth anything when it kills something you
like.** The record has real examples: MLB killed on a 16-bet trade cell
despite passing its quality gates (`props/PLAN.md`); Market 1 Stage C
held-out FAILED with ROI +11–14% at t<1 recorded as noise *per the
pre-registered rule* and parked (PROGRESS push log, 2026-07-31); cricket's
player model passed dev and failed holdout, verdict honoured. The gates that
only ever pass are decoration.

## 4. Live operation

**The experiment you run drifts from the experiment you registered unless
every rule is enforced in code.** The first-week audit found the harness was
not running the backtested experiment: the v1 opener-only rule had been
silently dropped, a week-stale panel kept pricing, a 42-day-stale player got
bet, and the shade-adjusted CLV column had been silently stamping zero shade
(PROGRESS, 2026-08-08). None of this was visible in the protocol prose,
which still said all the right things. The fix was structural: every rule
became a named gate in `fp_live.py` that stamps a flag on the sheet
(`PANEL_STALE`, `STALE_PLAYER`, `MOVED_OFF_OPEN`, `SUSPECT_EV`, …), so a
skipped pick is a visible row with `play=False`, not an absence. Prose
protocols decay; flags don't.

**Stale data doesn't look stale — it looks like edge.** The panel froze for
a week and claimed EV per slate rose 20%→30% *mechanically*, because the
market kept pricing games the model hadn't seen and the model read the gap
as opportunity. Information deficit is indistinguishable from alpha from the
inside. Freshness is now a hard gate checked against the event calendar,
with a loud flag when both box sources fail, and the sweep runs on every
firing whether or not bets are open — an archive that depends on there
happening to be stale open bets (as `edge-watch`'s did) will have holes
exactly when you need it.

**Metrics must never silently default.** `market_shades()` returned `{}` in
fresh containers, so `clv_cal` was stamped equal to raw CLV for five days —
the one pre-registered falsifier wasn't being computed, and nothing errored.
The rule now: blank-and-backfill, never zero-fill. Same family: partial
scrape data must raise, not skip (the 2026-08-19 fix), because a partial
offer set silently changes which rows survive the caps.

**Enforce invariants at the data layer, against full history.** "One bet per
player per game" was enforced within a sheet; the moment the owner's fill
made one market `already_bet`, the dedupe logic promoted a *different*
market on the same player-game and offered it as fresh (the 2026-08-10 Hamby
case — it cost a real losing bet). Any cap enforced per-batch instead of
against the persistent record will leak through exactly this seam.

**The record-keeping rules earn their strictness.** Append-only `bets.csv`,
owner-reported fills only, never invent a fill, `ev_claimed` stamped at the
price actually taken, generated files never hand-edited, every session's
work fast-forwarded to `main` before it ends (work was stranded on session
branches twice before that became a standing rule). Each of these exists
because its absence produced a specific loss or a specific lie in the data.

**Tiny stakes are cheap tuition.** The entire live programme — both eras —
has risked a two-digit dollar amount, and surfaced a dozen defects that no
backtest showed: fabricated quotes, void handling, trade-day scratches
(Plum, void), line moves between sheet and fill, book behavior at the slip.
The expensive version of this education is available at any bankroll; the
information content is the same at $1 a bet.

## 5. Where that leaves it (as of 2026-08-24)

Since the 2026-08-08 gates: 112 settled bets, 66W–46L, +14.9% ROI on flat
stakes; calibration z −0.23 (66 observed wins vs 67.2 claimed — the week-one
failure mode is gone); raw CLV −2.98% against a registered expectation of
≈ −3%; the unders bleed (17W–24L before the gates) closed to 44W–28L. Every
registered axis is on or above its pre-launch expectation, and none of it is
proof — by the repo's own power math it can't be at this n. The honest
statement is the one the protocol already makes: the process is now the one
that was backtested, the tripwires are armed, and the verdict is a
season-scale question. The discipline that produced that sentence — not any
model in the repo — is the asset this project built.
