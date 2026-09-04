# Beating the Opener, explained in plain language

*Written 2026-09-04. This is a reader's guide to the whole project: what the
model does, how the betting and research processes work, where the results
stand, what the September audit ([issue #1](https://github.com/soldoutbudokan/beating-the-opener/issues/1))
says, and what I make of it. It is written for someone who does not code and
does not bet. Every number in it was recomputed from the files in this
repository on the date above, unless it says otherwise.*

*It describes; it does not decide. The rules that govern the project live in
`wnba/live/PROTOCOL.md` and `PROGRESS.md`. If this document and those
disagree, those win.*

---

## Contents

1. [The idea, in one page](#1-the-idea-in-one-page)
2. [What the model does](#2-what-the-model-does)
3. [The live betting process, hour by hour](#3-the-live-betting-process-hour-by-hour)
4. [The research process, and the other sports](#4-the-research-process-and-the-other-sports)
5. [Where the results stand today](#5-where-the-results-stand-today)
6. [What the audit says](#6-what-the-audit-says)
7. [My judgement on the audit](#7-my-judgement-on-the-audit)
8. [Glossary](#8-glossary)

---

## 1. The idea, in one page

A sportsbook lets you bet on a single player's statistics in a single game.
For example: "Will Caitlin Clark make more than 2.5 three-pointers tonight?"
That is a **player prop**. The number 2.5 is the **line**. You bet the
**over** or the **under**, and the book quotes a price for each side.

The first price a book posts is called the **opener**. The price just before
the game starts is the **close**. Between those two moments, the book
adjusts the number as news arrives and as bettors push money one way or the
other.

The project's bet, in the broadest sense, is this: **the opener on WNBA
player props is a lazy price.** It is set by a small team, for hundreds of
players, on a sport that gets little attention. If a model built from
public data can guess the player's true chances better than that lazy
number, then betting the opener should make money over time.

The project tests that claim in two separate ways, and it is important to
keep them apart:

- **The research question.** Does the model forecast better than the
  opener? This is judged on thousands of historical props with a scoring
  rule, not with money.
- **The live experiment.** Can a person, sitting at FanDuel with a $100
  bankroll, turn those forecasts into profit? This is judged with real $1
  bets.

The project has run through several versions. The current one, called
**v3**, started on 2026-07-31. Earlier versions borrowed the book's own
price as a starting point and tried to predict how it would move. The
owner retired that approach because a model that starts from the market's
number never forms its own opinion. v3 builds the forecast from scratch:
from box scores, minutes, and injury news, with the book's price used only
to score and to bet against.

The short version of where things stand: the live experiment made **$8.93
on $236 of $1 bets**. That is a profit, but it is a small one on a small
sample, and the model's own claims were far larger than what it delivered.
The audit found several real defects in how the model is run live. Section
5 and Section 7 go into what that means.

---

## 2. What the model does

### 2.1 The ingredients

The model uses three kinds of data. None of them is the book's price.

**Box scores.** Every WNBA game since 2003, player by player: minutes,
points, rebounds, assists, three-pointers, steals, blocks, turnovers, shot
attempts, whether she started. The main source is a public dataset called
wehoop. When wehoop falls behind, which it did for about a week in August,
the project fetches the same numbers from ESPN's own game pages as a
fallback.

**The odds archive.** Every WNBA prop that a site called BettingPros has
listed since 2025, with the opening line, the opening price, which book
posted it, and the prices at many books as the game approached. This
archive is saved into the repository on every run because BettingPros
deletes old seasons. It is the single most valuable thing in the project,
and it is the yardstick everything is measured against.

**Availability news.** ESPN's injury report, per-game injury notes, and
game lineups, captured every hour. Plus a news feed for things injury
reports miss, like a coach saying a returning player is on a minutes limit.

### 2.2 The forecast, step by step

For each prop on tonight's slate, the model asks: how many of this stat
will this player record tonight, and how sure are we? It builds that answer
in layers.

**Step 1. How many minutes will she play?**

Props are mostly a minutes question. A player who averages 12 points in 30
minutes will not score 12 in 18. The model keeps two running averages of
each player's minutes, one that reacts fast to recent games and one that
reacts slowly, and blends them. If there is a news override for her (say,
"minutes limit, about 14 tonight"), the override replaces the estimate.

**Step 2. How productive is she per minute?**

This is the part called the **talent engine**, and it is the core of v3.
For each player and each statistic, the model keeps a running estimate of
her per-minute rate. After each game it nudges the estimate toward what
she just did, but only partway, because one game is noisy.

Two things make it more than a simple average:

- It knows what a player at her position and career stage usually does. A
  rookie guard starts from "typical rookie guard," not from zero. As her
  career goes on, the estimate is allowed to drift along the typical career
  curve. So a hot streak pulls the estimate up a little, and then it drifts
  back toward what players like her usually do. The owner's argument for
  this was that recent-form averages are too jumpy, and the historical
  test agreed: the talent engine beat the simple average on all seven
  stats it was tested on.
- It trusts a game more when she played more minutes in it. Three points
  in four minutes tells you less than fifteen points in thirty.

The technical name for this kind of running estimate is a Kalman filter.
You do not need the name. The picture to hold is a dial that turns a
little after each game, with a spring pulling it back toward "normal for
her type."

**Step 3. Adjust for tonight's opponent and venue.**

Two teams that play fast produce more of everything. A team that gives up
a lot of points inflates opposing scorers. The model multiplies the
per-minute rate by a pace factor and a defence factor, both built from the
teams' recent box scores, and applies a small home-court factor.

**Step 4. Turn the average into a spread of outcomes.**

"She will score about 14" is not enough. You need "there is a 41% chance
she scores more than 14.5." So the model attaches a probability curve to
the average. For low-count stats like three-pointers and assists it uses a
curve for counts (a Poisson distribution). For points and rebounds it uses
a wider curve that was tuned on past seasons. From that curve it reads the
probability of the over and the probability of the under at tonight's
line.

**Step 5. Compare with the price.**

FanDuel's price implies a probability too. If the model says the under has
a 62% chance and FanDuel's price implies 52%, the model believes the under
is worth more than it costs. The size of that gap, expressed as expected
return per dollar, is the **claimed EV** (expected value). A claimed EV of
+15% means "the model thinks that for every $1 bet here you would make 15
cents on average."

### 2.3 What the model deliberately ignores

The model never sees the line or the price when it forms its forecast. The
line is used once, at the end, to read the over/under probability off the
curve. This was a design rule of v3, written down before the model was
built, and it is what makes the forecast an independent opinion rather
than a tweak to the book's number.

The model also does not read injury news itself. A person (in practice,
the assistant running the hourly routine) reads the reports and writes
minute estimates into a file of overrides. The model then prices with
those minutes.

### 2.4 What the model was measured to do, before any money was involved

The 2025 season was used as the **development season**: the model was
tuned on games before 2025, then scored on the 2025 props it had never
seen.

The scoring rule is **log loss**. It rewards a forecast for putting high
probability on what actually happened, and punishes confident mistakes
harshly. Lower is better. The opener has its own log loss, since its price
implies a probability. The question is whether the model's score beats the
opener's.

On the full 2025 season, the model beat the opener by a hair: a gap of
about 0.0006 in the opener's disfavour. That is a small number, but it was
the first version in this project to beat the opener at all across a whole
season. In the August to October window, the gap widened to about 0.008,
which is meaningful for this kind of model. The model also stayed well
calibrated: when it said 60%, things happened about 60% of the time.

Two attempts to improve the minutes estimate with more statistics both
failed their tests and were not adopted. A diagnostic explained why: if the
model were given the true minutes in advance, its edge over the opener
would grow about ten-fold. So minutes are where the value is, but the
book already knows the minutes news. Better statistics on past minutes do
not help. Only genuinely new information about tonight's lineup does.
That is why the live version leans on the hourly injury-news layer.

---

## 3. The live betting process, hour by hour

### 3.1 The cast

- **The routine.** A scheduled cloud job called `news-watch`, fired once
  an hour at 31 minutes past. Each firing is a fresh assistant session
  that follows a written checklist. It does everything except place bets.
- **The owner.** The only person who bets. Reads the routine's
  notifications, checks FanDuel, decides, and reports what was taken.
- **The ledger.** A spreadsheet-style file, `wnba/live/bets.csv`, with one
  row per bet. Everything on the public scoreboard is generated from it.

### 3.2 What the routine does each hour

1. **Archive prices.** Save the current prices for every WNBA prop to the
   odds archive. This runs every hour regardless of anything else, because
   the closing prices are what bets are graded against later.
2. **Settle finished games.** If any bet is still open for a game that has
   been played, fetch the box scores, mark each bet won, lost, or void,
   compute the profit or loss, and compute the bet's closing-line value
   (explained in 3.5). Update the bankroll file.
3. **Refresh the model's memory.** If new box scores arrived, rebuild the
   player histories so tonight's prices reflect the latest games. (The
   audit found a defect here. See Section 6.)
4. **Take an injury snapshot.** Fetch ESPN's injury report and lineups.
   Compare with the previous snapshot. For each change, judge what it
   means for minutes and write an override entry: "player X, out," or
   "player Y, about 14 minutes, minutes limit per coach." Overrides are
   never edited or deleted, only superseded by a newer entry.
5. **Read the news feed** for anything the injury report misses.
6. **Price tonight's props.** Run the model on every prop listed for today
   and tomorrow, once with base minutes and once with the news-adjusted
   minutes. Save every projection to an append-only archive. Write the
   candidate bets to a sheet called `picks.csv`.
7. **Apply the gates.** Each candidate must pass every test in 3.3. A
   candidate that fails stays on the sheet with a label saying why, so the
   owner can see what was skipped. It is not playable.
8. **Notify.** If there are new playable picks the owner has not seen, send
   one push notification listing them. Never repeat a notification for the
   same pick.
9. **Commit everything** to the repository.

### 3.3 The gates: when a candidate becomes a pick

These rules were written down before the first v3 bet, and tightened on
2026-08-08 after the first week's audit. A prop is playable only if all of
these hold:

| Rule | Plain meaning |
|---|---|
| Claimed EV above 10% at FanDuel | The model has to see a clear gap, at the book the owner actually uses. Gaps at other books do not count. |
| Coherent quote | FanDuel's over and under must be at the same line and their prices must add up sensibly. A lopsided pair is a data error, not an edge. |
| Still at the FanDuel opener | FanDuel's current line must equal its opening line, and the price must not have drifted more than 15 cents. Once the line has moved, the "edge" is mostly the model disagreeing with news it has not seen. |
| Fresh model memory | No completed slate can be missing from the model's histories. |
| Fresh player | The player's last game in the model's memory must be within 14 days. (A 42-day-stale player on the wrong team once produced a "+29%" pick.) |
| Right team | The player must be on one of the two teams playing, and on the team the model has her on. Traded players are unpriceable until their first game with the new team. |
| Sane claim | Claimed EV of more than 25% is quarantined rather than bet. Every audited claim that large was a defect. |
| One bet per player per game | The highest-EV market only. Points and rebounds for the same player ride the same minutes, so two bets is one bet with double exposure. |

Two advisory flags do not block a pick but are shown loudly: the player's
last game was far off her usual minutes, or she just switched between
starting and coming off the bench. The talent engine is slow on role
changes by design, and the owner wanted to be warned rather than have the
model changed silently.

### 3.4 What the owner does

The owner opens FanDuel, checks that the price is still there, checks that
the player is actually in the lineup (the routine runs hourly, so late
scratches can be missed), and bets or does not bet.

The sheet suggests a stake using a formula (a quarter of the Kelly
criterion on half the claimed edge, capped at 5% of bankroll per bet and
30% per sheet). In practice, the owner has bet **exactly $1 on every one of
the 242 bets**. This was a deliberate choice, noted on each row. It means
the money results test a flat-stake process, not the suggested sizing.

The owner then tells any assistant session what was taken, and the session
logs it with a small script that copies the pick's details into the ledger
and stamps the model's claimed EV at the price actually taken. Fills are
never invented and never hand-typed into the ledger.

Since 2026-08-17 the owner also notes, when convenient, the maximum stake
FanDuel's betslip allows on each pick. That is a rough measure of how
confident the book is in its own price. It is observation only and does
not affect any decision.

### 3.5 How a bet is judged after the game

Each settled bet gets three verdicts:

- **Won, lost, or void.** Void means the player did not play, in which
  case FanDuel refunds the stake. Profit or loss is computed from the
  price taken.
- **Closing-line value (CLV).** Take the closing price, remove the book's
  margin to get a fair probability, and ask: at that fair probability,
  was the price we paid a good deal? If the line moved our way after we
  bet, CLV is positive. Bettors use CLV because it settles far faster than
  profit. Over a few hundred bets, profit is mostly luck. CLV is not.
- **Shade-adjusted CLV (CLV\*).** WNBA prop prices systematically lean
  toward the over by about two percentage points. Raw CLV therefore
  punishes every under bet a little, mechanically. CLV\* corrects for
  that measured lean.

One thing to know about CLV on this strategy: because the picks are bets
on lines that have not moved yet, and most lines never move, the typical
pick closes at the same line it was bet at. In that case CLV is roughly
"minus the book's margin," about −5% to −7%. So an opener-only strategy
expects negative raw CLV. The project wrote this expectation down before
going live (−4.6% was the pre-registered figure). What it would want to
see is CLV\* near or above zero.

### 3.6 The timeline

| Date | What happened |
|---|---|
| 2026-07-27 to 07-31 | The earlier, retired model made 5 bets. It lost $1.09. |
| 2026-07-31 | v3 goes live. Owner takes 8 of 17 picks, $1 each. |
| 2026-08-01 to 08-07 | First week. A box-score source stalls for a week; the model keeps pricing off stale histories and claimed EVs climb. |
| 2026-08-08 | First-week audit. Routine paused, then restarted the same day with the gates in 3.3, a fix to the CLV\* column (it had been silently recording zero adjustment), and the ESPN fallback for box scores. |
| 2026-08-10 | One bet per player per game enforced across sheets, after two bets on the same player slipped through. |
| 2026-08-17 | Limit capture added (observation only). |
| 2026-08-29 | Last fill. |
| 2026-08-30 | Last games settled. Last projections generated at 23:38 UTC. |
| 2026-08-31 | Bankroll file last updated. **The `news-watch` routine was disabled at 21:57 UTC.** Nothing in the repository records this; I read it from the routine's own settings. |
| 2026-09-01 to 09-02 | Cricket research only. |
| 2026-09-04 | The audit is posted as issue #1. |

So as of this writing the live experiment has been off for four days, the
sheet still shows seven "playable" picks from August 30, and the
documentation still describes the routine as running. That is a
housekeeping gap, not a money problem, but it is worth closing.

---

## 4. The research process, and the other sports

### 4.1 Rules the whole project follows

The project's research culture is its strongest feature, and it is worth
spelling out because the audit leans on it.

**Write the test before running it.** Before a new model idea is coded,
the pass/fail rule is written into `PROGRESS.md` with the exact number it
must beat, the data it will be judged on, and how many attempts are
allowed (usually two). If it fails, it is recorded as failed and not
retried. Several promising ideas died this way: two minutes engines, a
cricket confidence model, a soccer live launch.

**Three tiers of data.** Old seasons are for building and tuning.
One season (2025 for WNBA) is the **development** season, where a model is
scored and iterated on. A final slice is the **holdout**, touched once or
never. WNBA's holdout was spent on 2026-07-31; from then on, 2026 numbers
are labelled as diagnostics, not results.

**Prospective arms.** For a model that is already live, the fair test is
on games that had not happened when it was locked. Two WNBA arms
(`fp-prospective-1` and `-2`) have been accruing since 2026-08-01 and are
meant to be scored once, at season end or 3,000 props, whichever comes
first. They score the base model's forecasts, not the bets, so that
betting decisions cannot contaminate the forecasting verdict.

**Placebos and tripwires.** Every simulated betting result is paired with a
"zero-skill" strategy that bets the book's own probabilities. If the
placebo also makes money, the yardstick is broken, not the model. Every
model is also checked against the closing line: a model that beats the
close by a lot is assumed to be leaking future information until proven
otherwise, because the close is very hard to beat.

**Negative controls stay published.** Two sports were tested with the same
methods and returned "no." Those results are kept prominent because they
are what make the "yes" results believable.

### 4.2 The other sports, briefly

| Sport | What was tried | Result | Status |
|---|---|---|---|
| **NBA moneyline** | A from-scratch win-probability model from player ratings, availability, referees, travel. The only model here never anchored on the market. | The closing line wins decisively. Even a cheating version that refits on the test seasons falls short. | Control. Closed. |
| **Soccer 1X2** (lower leagues, home/draw/away) | A model anchored on Pinnacle's opener, then a goals model. | Beat the opener 9 of 9 seasons while Pinnacle was in the data. Pinnacle left the feed in January 2026; replayed against the average book, no edge. Live launch cancelled two days before the first bet. | Parked. |
| **Cricket T20** (BBL, then Polymarket) | First a check whether the close beats the open (it does not: moves are toss noise). Then, from August 29, a from-scratch Elo-plus-player model against the Polymarket prediction market's day-before price. | Now at parity with the exchange's price on both development cells, within 0.001. The stricter registered goal is not met. | Research only, prospective arms accruing. |
| **NHL player props** (shots, blocks) | A talent engine like WNBA's, then a shot-attempts model. | Closed 83% of the gap to the opener; now statistically indistinguishable from it. Holdout unspent. | Research only. |
| **NBA player props** | Same architecture. | Loses to the opener clearly. The NBA market prices minutes and matchups far better than box-score averages can. | Control. Holdout unspent. |
| **MLB pitcher props** | Registered 2026-08-29. | Early. | Research only. |

The pattern across sports is consistent with the thesis: the model's gap to
the opener tracks how much attention the market gets. WNBA is close to
parity or slightly ahead. NBA is far behind.

---

## 5. Where the results stand today

### 5.1 The money

Everything below is recomputed from the ledger. The ledger reconciles: the
stakes, prices, outcomes, and profit on every row add up, and the audit
independently matched all 236 settled outcomes to ESPN's box scores.

| | |
|---|---|
| Bets logged | 242 |
| Settled | 236 (124 won, 112 lost) |
| Void (player did not play) | 6 |
| Stake on every bet | $1 |
| Total staked on settled bets | $236 |
| Profit | **+$8.93** |
| Return per dollar | **+3.8%** |
| Bankroll | $108.93, from $100 |
| Distinct game dates | 32 |
| Distinct games | 83 |

### 5.2 What the model claimed versus what happened

| | |
|---|---|
| Average claimed EV per bet | +19.1% |
| Actual return per bet | +3.8% |
| Wins the model expected | 142.6 |
| Wins that happened | 124 |
| Profit if the claims had been right | +$45.13 |

The model said each bet was worth about 19 cents on the dollar. The bets
returned about 4 cents. The model expected to win 143 of 236 and won 124.
That shortfall of 19 wins is unlikely to be chance (the site's own test
puts it at roughly 2.5 standard deviations), though it is not a precise
test because bets on the same night are not independent.

So the clearest thing the data says is: **the model's confidence is
inflated.** That was already visible after one week (37 expected wins, 28
actual) and it has persisted. Section 6 lists mechanical reasons for it.

### 5.3 What the closing line says

| | |
|---|---|
| Average raw CLV | −3.67% |
| Average shade-adjusted CLV\* | −2.73% |
| Bets that closed at the same line they were bet at | 185 of 236 |

Read against the pre-registered expectation (raw CLV around −4.6% for an
opener-only strategy), raw CLV is about where it was expected to be. That
is neither good news nor bad. The number that was meant to show the edge,
CLV\*, is negative. If the book's closing prices are a fair guide, the bets
were slightly worse than fair on average.

The audit's bootstrap intervals (resampling whole game dates 100,000
times) put the 95% range for CLV\* at about −4.2% to −1.2%. Both ends are
below zero. The same method puts the return per dollar at −9.9% to +18.2%.
That range includes zero comfortably, and it includes both "modest
losing strategy" and "strong winning strategy." The sample cannot tell
them apart.

Two cautions on CLV. First, 234 of the 236 closing prices come from a
consensus across books, not from FanDuel itself, so this is a proxy for
the close, not FanDuel's close. Second, when the line has moved by the
close, the closing probability has to be translated back to the line we
bet at using the model's own distribution, which adds a layer of
assumption. The audit is right to call this a "closing-price proxy."

### 5.4 Slices worth knowing, and not over-reading

| Slice | Bets | Return | Claimed |
|---|---:|---:|---:|
| Overs | 83 | +11.4% | +17.8% |
| Unders | 153 | −0.4% | +19.8% |
| Assists | 66 | +16.5% | +19.2% |
| Points | 63 | −10.3% | +20.3% |
| Rebounds | 54 | +6.4% | +18.2% |
| Threes | 53 | +2.1% | +18.6% |
| Claimed EV 10 to 15% | 69 | +10.7% | +12.1% |
| Claimed EV 15 to 20% | 71 | +10.7% | +17.5% |
| Claimed EV 20 to 25% | 63 | −7.5% | +22.2% |
| Claimed EV above 25% | 29 | −4.2% | +34.5% |

Three things stand out. The sheet is two-thirds unders, and the unders are
where the calibration problem lives: the model expected 95 wins on them
and got 79. Points props lost money while assists made it. And the bets
the model was most sure about did worst. Bigger claimed edges did not mean
bigger returns; they meant the opposite.

None of these slices is large enough to act on by itself. Sixty bets can
swing ten percentage points on luck alone. But the pattern of "the more
the model disagrees with the market, the worse it does" is exactly what you
would expect if the model's biggest disagreements come from information
the market has and the model does not.

### 5.5 The cohort problem

The public scoreboard shows a "since the gates" record of 172 bets and
+7.0%, splitting on the game date of 2026-08-08. But eleven of those bets
were placed on August 7, before the gates existed, and seven of them
carried claimed EVs above 25% that the gates would have quarantined. If
you split on when the bet was placed instead, the post-gates record is 161
bets and +6.8%. If you start on August 11, after the one-bet-per-player
fix, it is 146 bets and +3.6%. The differences are small in dollars, but
the point is that there is no clean "current process" cohort. Each bet
should carry a record of which version of the rules produced it.

### 5.6 My best guess, stated as a review

If I had to grade the project today, on what it set out to prove:

**Does the model forecast better than the opener?** *Probably a little,
on paper. Not established.* The development-season result is real but
small, and the prospective test has not been scored. The audit found a
defect (Section 6, "one game behind") that makes the live model worse than
the one that was tested, so the live forecasts are not the ones the
research validated.

**Is there a tradeable edge at FanDuel?** *Not shown.* The profit is
consistent with luck. The closing line says the bets were slightly worse
than fair. The model's own claims were far too high, and the failure is
concentrated exactly where the model is most confident.

**Is the bookkeeping trustworthy?** *Yes.* This is the part that
survived every check, including the audit's independent grading of all 236
outcomes.

**Is the research culture sound?** *Yes, and unusually so.* Failed ideas
are recorded, controls are kept, tests are written before they are run.
The gap the audit found is that the culture is written in prose rather
than enforced by code.

The most useful next step is not a bigger model. It is to fix the four or
five mechanical defects the audit found, replay the corrected model on the
2025 development season, and see whether the small edge survives. If it
does, the prospective arms and a clean post-fix live cohort can test it.
If it does not, the project has its answer cheaply.

---

## 6. What the audit says

### 6.1 What it is

Issue #1 was opened by the owner on 2026-09-04. It is an audit of the
repository as of commit `9164a03`, about 155,000 characters across the
issue body and six long comments. It covers the model, the live process,
the other sports' research, engineering hygiene, and the website. It
recomputed the ledger, parsed all 117 Python files, ran small synthetic
tests against the repository's own functions, and checked the published
site by hand. It changed nothing.

Its one-paragraph verdict: the project has a useful research foundation, a
ledger that reconciles, and a capable website, but it has **not
established a dependable, executable betting edge**, and several defects
should be fixed before trusting the forecasts, running the risky tools, or
building a bigger model.

### 6.2 The main findings, in plain language

The audit labels twelve findings as top priority. Here they are grouped by
what they affect.

**Defects in the forecast itself.**

- *The live model is one game behind.* The player histories are built so
  that each game's row holds the player's state *before* that game. The
  live pricing takes the most recent row and uses it as tonight's state.
  So tonight's price does not include what she did in her last game. The
  research backtest does not have this problem, which means the live model
  is not the one that was tested. The market, of course, has seen her last
  game. This is a strong candidate for why the model's disagreements with
  the market are too confident.
- *A player's forecast can depend on other players' results.* The model
  computes "league average pace" from whatever props are in the batch it
  is given. In the backtest, props that ended in a push (the player landed
  exactly on the line) are removed before the batch is priced. So one
  player landing on her line changes the league average used to price
  another player. The audit shows the mechanism with a synthetic example.
  It also means adding or removing an unrelated prop changes the forecast.
- *Whole-number lines are priced wrong.* When the line is a whole number,
  landing exactly on it is a push (stake refunded). The model computes the
  chance of going over, and calls everything else the chance of the under.
  That counts pushes as under wins. No bet in the ledger had a whole-number
  line, so no money was affected, but the backtest simulation was.
- *The opening benchmark is mislabelled.* The research calls its yardstick
  a "consensus opener," but the code uses whichever single book posted
  first. The measurements are fine; the name is wrong.
- *A statistical fit in the game-lines model passes an argument in a way
  the library treats as a starting guess rather than a fixed value.* A
  small, real bug in a research-only model.

**Defects in the live process.**

- *Picks can be offered after the game has started.* The routine drops
  games marked "closed" but does not compare the current time with the
  scheduled tip. The August 30 sheet contains picks generated 38 minutes
  after their game tipped.
- *A run that finds no offers leaves the old sheet in place.* The picks
  file is not cleared or marked expired. Old "playable" rows persist.
- *The live price fetch reads only the first page.* The odds site paginates
  at ten offers per page. The archiver was fixed to fetch every page in
  August; the live path was not. Players on page two are never priced
  live.
- *Settlement can grade against the wrong game.* If a box score is missing
  for the exact date, the code looks one day earlier, then one day later,
  checking only that the team matches. All 236 settled bets matched their
  exact date, so this has not happened, but the guard is weaker than the
  code's own comment claims.
- *An injury feed failure can look like "everyone cleared."* If the league
  injury endpoint fails but the scoreboard endpoint succeeds with no
  injury rows, the diff reports every previously injured player as
  cleared. The result is reviewed by a person, which limits the damage.
- *Kelly stakes can exceed the cap by rounding.* A $5.45 cap becomes a
  $5.50 suggestion. Irrelevant while the owner bets $1, but the sheet shows
  it.

**Defects in bookkeeping and tools.**

- *Two processes writing the ledger at once could lose a fill.* Settlement
  reads the whole file and writes it back. If the owner logs a fill in
  between, the fill is gone. No fill has been lost; the audit checked.
- *The fill logger accepts nonsense inputs.* A negative stake or a
  probability above one would be written without complaint.
- *The FanDuel limit probe does not clean up on failure.* This is an
  owner-run browser tool that types $1,000,000 into a betslip to read the
  maximum, then clears it. If it fails between typing and clearing, the
  oversized stake stays in the slip. It has never placed a bet, and it is
  not used by any routine, but the audit is right that its safety claim is
  stronger than its code.
- *No pinned dependencies, no automated tests, no continuous integration.*
  A clean checkout cannot be guaranteed to reproduce a result.

**Defects in the cricket research** (research only, no money).

- Series standings can read results from a *later* series with the same
  name, because series are grouped by name without a season.
- The open and close timestamps are inferred from the later price path,
  which can place a "pre-match" price inside the match.
- The archiver's checkpoint can drop a market's history before the final
  safety check runs.
- The Elo rating's "regress to the league mean" computes the mean wrong
  (halving it), which would make regression look useless during tuning.
- The prospective scorer will happily score any file, any time, without
  checking it is the registered model.

**Defects in reporting and the website.**

- The "since the gates" cohort mixes in pre-gate bets (Section 5.5).
- Expected profit includes void bets; actual profit does not. $46.21 versus
  the matched $45.13.
- The clustered statistic on the site averages days equally, which is a
  different number from the bet-weighted average it sits next to.
- The site says a season of CLV is "decisive" and that profit with negative
  CLV is "just luck." The audit calls this stronger than the evidence.
- The research README still shows a chart from the retired model claiming
  +123% return. The text next to it explains that number was inflated, but
  the image travels without the text.
- Light-theme small text is below the accessibility contrast standard.
- The "Evidence" tab counts the retired WNBA model and the cancelled soccer
  launch as two of four successes, which can be read as support for the
  current model.
- The Players tab mixes stats from different games into one row for 58 of
  142 players, and 21 cells are older than the 14-day limit while labelled
  fresh.

### 6.3 What the audit praises

It is specific about this, and it matters: the ledger reconciles to the
cent; every settled outcome matched ESPN independently; the site rebuilds
byte-for-byte from its inputs; the model is interpretable and separates
minutes from rates from opponent effects; failed models and negative
controls are kept visible; the odds archive is irreplaceable and well kept;
the protocols already contain most of the right rules. The audit does not
say the research is worthless, the ledger is fabricated, or the profit
proves skill.

### 6.4 What the audit recommends

Five work packages, in order, with rough effort:

1. Make execution and accounting trustworthy (3 to 5 days): time checks,
   pagination, fail-closed runs, input validation, atomic ledger writes,
   probe cleanup.
2. Repair the prediction engine (3 to 7 days plus a replay): fix the
   one-game lag, the batch dependence, the push handling, the fit bug;
   build fixtures that prove the live and research paths match.
3. Make experiments reproducible (2 to 4 days): pinned environment, run
   IDs, model hashes, tests, continuous integration.
4. Rebuild the site's evidence hierarchy (2 to 4 days): status from a
   registry, uncertainty on every headline, replace the stale chart.
5. Only then, improve the model through bounded experiments, starting
   with news-informed minutes.

It also proposes a shared record format across sports (source
observation, quote, forecast, decision, fill, settlement, evaluation) and
seven acceptance criteria for closing the issue.

---

## 7. My judgement on the audit

### 7.1 Is it right?

Yes, wherever I could check. I recomputed every headline number from the
ledger and matched all of them, including the ones that looked most like
they might be off (the eleven pre-gate bets, the $5.50 stake, the 38-minute
late picks, the seven cross-player links in the overrides file, the
contrast ratios). I traced every top-priority code defect to the line in
source and confirmed the mechanism. I did not re-run the bootstrap
intervals or the two counts that need the full projection archive; the
numbers they rest on are correct, so I would treat them as reliable.

### 7.2 Where it gets the proportions wrong

The audit's weakness is not accuracy. It is scale. It reads as an audit of
a trading operation, and this is one person betting $1 a pick to measure
whether a model works. Several consequences follow.

**Twelve "P1" findings is too many, and they are not alike.** The
one-game lag changes every live price. The Kelly rounding changes a
suggestion nobody follows by five cents. Both are P1. A reader who takes
the list at face value will spend time on the wrong things.

**The recommended sequence puts infrastructure before the fix that
matters.** Packages 1 and 3 together are five to nine days of plumbing,
scheduled ahead of the two-line fixes that would change what the model
says tonight. That is backwards for a project whose open question is
"does the edge exist."

**The proposed architecture is heavy.** Immutable event ledgers, run
manifests with model hashes, sealed one-shot evaluators, an experiment
registry that generates the website, seven shared record types. Each is
defensible in the abstract. Together they are weeks of work that make the
project harder to change without making the edge more likely to exist. A
pinned requirements file and one small test file covering the boundaries
the audit lists would capture most of the value.

**Some findings are policy choices, not defects.** Whether an injured
player's "OUT" status should block a pick is a decision the owner made
deliberately (display only, owner checks the lineup). The audit says as
much, then lists it as a P1 anyway.

**Some synthetic examples exaggerate.** The "one player's push changes
another's forecast from 20.0 to 17.7" example uses a batch of two rows. A
real slate is hundreds of rows, so a single push moves the league average
by a fraction of a percent. The defect is real and should be fixed because
it is cheap, but the number is not a measured effect.

**The concurrency finding assumes a threat that is not present.** One
hourly routine and one owner, both committing through git, do not write
the ledger at the same second. A lock is fine. An event-sourced ledger is
not warranted.

**The cricket findings are correct but carry no money.** They matter for
whether the cricket research is right, and one of them (the Elo mean bug)
may have caused the tuner to reject regression when it should not have.
They do not bear on the WNBA experiment at all, and should not share a
priority list with it.

### 7.3 What I would do, in order

1. **Fix the one-game lag.** Build a placeholder "next game" row for each
   player before the histories are computed, so the state after her last
   game is what gets priced. Advance the talent engine one step the same
   way. About half a day. This is the one change most likely to move the
   calibration problem.
2. **Fix the three live-process holes together:** refuse picks once the
   tip has passed, write an empty sheet when nothing qualifies, and reuse
   the archiver's page-by-page fetch in the live path. A few lines each.
3. **Fix the two forecast defects:** compute league pace and defence from
   a stable per-date team table instead of the batch, and price whole-number
   lines with explicit over, push, and under probabilities. Cheap.
4. **Fix the reporting defects in one pass:** split cohorts by fill time
   and rule version, match expected profit to the settled population, fix
   the clustered statistic, replace the retired model's chart, darken three
   colours, and soften the "decisive" and "just luck" language.
5. **Replay the corrected model on the 2025 development season** and
   record the before-and-after gap to the opener. This is the moment the
   project learns whether the edge was real.
6. **Then** decide about restarting the live experiment, with a clean
   cohort boundary, and only then think about the heavier items.

### 7.4 What I would skip, or defer until there is a reason

The event ledger. The run-ID and hash machinery. The experiment registry
generating the site. The five-graphic redesign. The shared cross-sport
record schema. The atomic-write and lock work beyond a simple lock. The
full continuous-integration suite. All of these are good practice for a
larger operation. None changes whether a WNBA prop model beats the opener.

### 7.5 What the audit could not see

The audit says, correctly, that it did not verify the scheduler's state.
I did. The `news-watch` routine has been disabled since 2026-08-31 at
21:57 UTC, after its last firing at 21:31 that day. That is why every live
file stops at August 30 or 31. `CLAUDE.md` and `PROTOCOL.md` both still
describe the routine as re-enabled on 2026-08-08 and record no later stop.
I did not touch the routine. Whatever the owner decides about it, both
documents should say what happened on August 31.

### 7.6 What not to do

Do not rewrite the 236-row ledger; it is correct. Do not restart betting
before items 1 to 3 and the replay in item 5. Do not treat the +$8.93 as
evidence of anything. Do not treat the audit's negative CLV\* interval as
proof of a losing strategy either; it is a proxy with its own assumptions.
And do not spend two weeks on infrastructure before the half-day fix that
changes what the model says.

---

## 8. Glossary

**Bankroll.** The pot of money set aside for the experiment. Started at
$100, currently $108.93.

**Calibration.** Whether a model's stated probabilities match reality.
A calibrated model that says 60% is right about 60% of the time.

**Claimed EV.** The model's own estimate of a bet's expected return per
dollar, at the price actually taken. "+15%" means the model expects 15
cents profit per dollar on average.

**Close, closing line.** The last price before the game starts.

**CLV, closing-line value.** How much better or worse the price you took
was than the closing price, after removing the book's margin. Positive
means the market moved your way.

**CLV\*.** CLV corrected for the WNBA prop market's systematic lean toward
the over.

**Coherent quote.** An over and under at the same line whose prices add up
to a normal book margin. The opposite is a mispaired quote, which is a data
error that once produced a fake "+32%" pick.

**Development season.** The season a model is tuned and scored on
repeatedly during research. 2025 for WNBA.

**Gate.** A written pass/fail rule for a model, set before the model is
built or run.

**Holdout.** Data reserved for a one-time final test. WNBA's holdout was
spent on 2026-07-31.

**Kelly criterion.** A formula for bet size proportional to edge. The sheet
suggests a quarter of it, on half the claimed edge. The owner bets $1.

**Line.** The number a prop is bet over or under. 2.5 threes, 14.5 points.

**Log loss.** A scoring rule for probability forecasts. Lower is better.
Rewards confidence when right, punishes it when wrong.

**Opener, opening line.** The first price a book posts for a prop.

**Override.** A hand-written entry setting a player's expected minutes or
availability for a specific game, based on injury news. Overrides replace
the model's minutes estimate.

**Panel.** The internal table of every player's game-by-game history and
running averages, rebuilt whenever new box scores arrive.

**Placebo.** A zero-skill strategy run alongside the model's strategy. If
the placebo also wins, the yardstick is broken.

**Prospective arm.** A locked model scored only on games that had not
happened when it was locked. The fairest test there is.

**Push.** The player lands exactly on a whole-number line. Stake refunded.

**Shade.** The WNBA prop market's tendency to price overs about two
percentage points too high.

**Talent engine.** The v3 model's per-minute rate estimator: a running
estimate per player per stat that updates a little after each game and
drifts back toward what players of her type usually do.

**Void.** The bet is cancelled and refunded, usually because the player did
not play.

**Wedge.** The gap between the opener and the close. If the close is
better informed than the opener, a model can try to capture part of that
gap. The project measured the WNBA wedge as real and the NBA wedge as
absent.
