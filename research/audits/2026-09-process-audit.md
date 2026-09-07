# September process audit

**Decision: recover the frozen evaluation inputs and repair the research
boundaries before fitting the structural model.** Phase 0 is complete as a
process audit; its protected scoring and human-baseline tasks are blocked.
No protected arm was scored, no model was fitted and no live code was changed.

The archive is useful, but two assumptions in the programme brief need correction.
The WNBA props count proxy already exceeds the 3,000-row endpoint, while the
files needed to reproduce the registered forecasts are absent. Also, the news
log is not uniformly pregame: 26 dated entries were added at or after their
matched game's scheduled tip. Treating all 554 entries as pregame predictions
would bias the news experiment.

**2025 has repeatedly informed development and selection. It is not an
independent test.** The experiment-level inventory below applies to every later
report; new claims need a genuinely prospective population.

The [plan](../experiments/2026-09-process-audit.md) and review adapter were
[published before measurements](https://github.com/soldoutbudokan/beating-the-opener/commit/5bfd24d0f669eba88b793d4153ea4a3ebd4556be).
Read the [generated decision](2026-09-process-audit/decision.md),
[plain-language note](2026-09-process-audit/note.md),
[metrics](2026-09-process-audit/results.json) and
[reproduction instructions](2026-09-process-audit/README.md).

## 1. Forecast-validity triage

Main is pinned to `d2b5e685ce10d9670fc93bf8c22810bc8cc5df83`;
PR #2 to `64acad8248ef6536ef7c655d733293f6763263cc`. The WNBA and cricket source
files and live records are byte-unchanged from issue #1's audited commit
`9164a0355950a590dcdd99b93b17f04997819382`. This is a source-level status check,
not a new estimate of each defect's historical impact. Issue #1's unchecked
boxes are not themselves evidence that a defect remains; the code is.

| Requirement | WNBA live/current research code | PR #2 research adapter | Cricket | Research harness / owner-only live proposal |
| --- | --- | --- | --- | --- |
| M1: batch normalization | **Unfixed.** `fp_model.mu_stats` and `_pace_def` average league context across supplied offers. `main` removes realized pushes/voids first; `fp_live` supplies the current offer batch. | **Repaired for prediction.** `baseline.predict_means` consumes explicit as-of league denominators; means do not depend on offer count or outcomes. | No equivalent offer-batch normalization in the player model. **Other chronology defects remain**, particularly series state; this is not a general causality pass. | Build league state from complete prior games and predict before grading. Test append/remove/duplicate/reorder invariance. Replacing the live normalization requires owner approval. |
| M2: latest completed game | **Unfixed.** `fp_live.latest_states` copies the final pregame EW/talent row; the latest observation has not entered that state. Game rotation code shares the extra lag. | **Repaired.** Feature EWs are postgame, `_next_talent` updates before emitting the next state, and queries apply an availability cutoff. | Rating passes update after predictions, but `add_series_state` sorts by match number before date and groups recurring series names without season IDs. Date-level states do not establish quote-time availability. | A common update/forecast interface; globally chronological, season-specific series keys; latest-game and target-outcome invariance. Live changes require approval. |
| M3: pushes | **Unfixed.** `p_over` is unconditional; live and ROI code treat its complement as an under win. Binary scoring drops pushes without conditioning probabilities. | **Repaired.** Explicit over/push/under, conditional non-push probabilities and push-aware economics. | Two-outcome match-winner prices have no integer threshold; ties/no-results are excluded. WNBA push fix is not directly applicable. | Preserve three masses; EV = win probability × net payout − loss probability. Live pricing change requires approval. |
| M4/R5: frozen recipes and protected windows | **Unfixed.** No committed frozen WNBA calibration/data bundle or dedicated one-time scorer. Ordinary `--holdout` can include protected August data. ASG segmentation is prose. | **Partly repaired.** 2026 rejected; pinned source manifest, implementation hash, one-run receipt and no-overwrite reproduction protect this 2025 experiment. They do not recover WNBA prospective artifacts. | **Unfixed.** `pm_prospective.py` prints the endpoint but does not enforce it, reads a mutable predictions file, uses match date instead of resolution time and can label arm two as arm one. Arm-one recipe history conflicts. | Separate count-only accrual from scoring; require arm/recipe/input hashes and an endpoint before a one-time receipt. Do not silently refit old arms. Phase 0 only adds isolated audit/review tools. |
| M5: opener provenance | **Partly repaired.** Props check same source book/line and margin, but “consensus opener” is actually a mixed-source BP opening pair. Game code copies global opening records into current book-0 rows without equivalent checks. | **Repaired, with source limitation.** `benchmark.parse_offer` retains both books/times/lines, rejects skew and after-tip pairs, labels BP source-book openers. Later archive capture does not independently prove historical executability. | Token/market IDs retained in raw prices. **Unfixed timing:** `onset_time` derives the benchmark clock from the future price path; closure metadata is not validated resolution time. | Keep every quote's provenance. For cricket use independent event/toss times; do not rename the old benchmark. Live opener changes require approval. |
| X4: event and athlete identity | **Unfixed.** `grade_props` and `build_modelset` use normalized name/date and ±1-day fallbacks; talent joins collapse name/date with `max`. | **Partly repaired.** Event/team/date and athlete crosswalks reject ambiguity. However, eligibility uses `same_team_as_last_game` from the target game's final team, a hindsight population filter. | Gender/team/date checks reject ambiguous matches, but neighboring-day matching and multiple markets for one match remain; series identity is not season-specific. | Resolve IDs from pregame sources, retain missing requests and quarantine ambiguity. Use BP pregame team for new eligibility. Preserve PR #2's frozen population as a reused, selected comparison and disclose its limitation. Live join changes require approval. |

Source functions: `wnba/src/{fp_model,fp_live,features,talent,grade_props,
build_modelset,build_props,fp_games2}.py`; PR #2's
`wnba/research/rich_context/{baseline,benchmark,features,run}.py`;
`cricket/src/{pm_model2,pm_benchmark,pm_prospective,fetch_polymarket}.py`.
Their exact hashes are in the count evidence. The underlying detailed
reproductions remain in [issue #1](https://github.com/soldoutbudokan/beating-the-opener/issues/1)
and the [PR #2 review](https://github.com/soldoutbudokan/beating-the-opener/pull/2#pullrequestreview-5133651371).

## 2. Protected-window calendar

These are archive counts as of the pinned source, not a claim of complete live
accrual through September 7. The current WNBA event file contains 85 closed
August games and 30 scheduled later games, through September 24. The official
[2026 key dates](https://www.wnba.com/keydates), checked September 7, put the
regular-season end at September 24, the Finals start at October 17 and the last
possible Finals game at October 31. Those dates are subject to change. The
game arm explicitly includes playoffs; September 24 does not end that arm.

| Arm | Registered endpoint and recipe | Current n / archive evidence | Can score now? | When 2026 becomes development data |
| --- | --- | --- | --- | --- |
| fp-prospective-1 | Props dated Aug 1 onward; 3,000 standard eligible non-push rows or season end, whichever first. v1 lock `5f6e021f9`; pre-2026 calibration. | Exact registered n **unknown**. 6,711 coherent unique opening pairs; ESPN exact-game count proxy 6,324, crossing 3,000 on Aug 15. | **BLOCKED, potentially overdue.** Missing registered wehoop rebuild inputs, frozen calibration/source hashes and original Aug 1–3 forecasts. | No release date yet. Only after a valid one-time result is recorded; overlapping arms also remain protected. |
| fp-prospective-2 | Same population/endpoint; talent lock `3d2251ed2`, pre-2025 talent fitting and v1 calibration. | Same count bounds; these are not a scored result or a validated replacement for wehoop-qualified n. | **BLOCKED, potentially overdue.** Same artifacts plus frozen talent parameters. | Same rule; no automatic Aug 16 or Sep 25 release. |
| fp-games-prospective-1 | Games Aug 1 onward, **season end including playoffs**; spread head at `e9b8d6bb5`, no refits. | Exact eligible spread n **unknown**; at most 85 closed archived events so far. | **WAIT.** Endpoint has not arrived. Frozen parameter/source recovery is also unresolved. | After the actual postseason endpoint and published score; currently no fixed release date. Latest possible Finals date is Oct 31, not a substitute for the completion check. |
| pm-prospective-1 | 300 markets or June 30, 2027. P originally locked the July player model at `348e16dda`; Q/later text instead calls it an Aug 30 v2 recipe with `--no-opp --no-blast-groups`. | **0** archived closed markets after either lock; latest closure Aug 29 18:53:16Z, earlier than P's Aug 29 21:09:42Z registration. | **WAIT**, with recipe and resolution-time reconciliation required. No scores were read to choose a recipe. | Only after its registered score; no automatic calendar release while recipe/population is unresolved. |
| pm-prospective-2 | Markets resolving after Sep 2, 2026; 300 or June 30, 2027. it-23 lock `76f3c27a5`, default consolidation `9164a0355`. | **0** eligible archived closed markets. | **WAIT.** No reached endpoint or new closed-market capture. | Only after scoring that arm, respecting overlapping arm one. |

The WNBA proxy uses the original coherence rule and only exact-date,
team-pair/player matches against committed ESPN boxes. Its 6,711 requests
partition into 6,324 played non-push rows, 203 voids, 1 push, 89 missing or
ambiguous players and 94 missing or ambiguous games. It is deliberately **not**
the original wehoop/name-fallback population. No model probability, log loss,
profit or minutes error was computed. The archive has 1,020 offer files and
360 missing expected files for the 30 scheduled future games; those missing
files do not establish an August capture failure.

The August 3 ASG amendment at `0e420df01` requires August 1–3 predictions to
stand as produced and August 4 onward to use the clean panel. The live CSV does
contain `mu_base` and `p_over_base`, alongside the news-adjusted fields. These
are repeated current-line projections from the live state path; they omit
event IDs, paired price/book timestamps and frozen recipe metadata. They are
not an identifiable complete set of either registered opener arm's forecasts.
Replaying today's corrected model, retraining on today's pre-cutoff history,
or substituting those live rows would change the registered experiment.

No frozen WNBA calibration/talent/modelset bundle or prospective evaluation
receipt is tracked. The historical paths checked also have no commits for
those artifacts. The existing live base fields do not resolve this gap. The
2026 wehoop player-box download did not complete: the
environment reported that network approval was cancelled. No downloaded file
was used. A data cutoff alone does not prove unchanged fitted parameters.
**Do not call a reconstructed/amended result the original registered result.**

## 3. Human news baseline and the archive

**Human-baseline accuracy is not yet scoreable.** There are zero unlocked
post-endpoint entries under this audit's rules, so out-playing frequency,
minutes MAE/bias and questionable-range coverage are **not estimated**.
The one dated July 31 entry predates the prospective window, but is not a
post-endpoint baseline entry either. The news accuracy task waits; zeros are
not substituted for missing metrics or missing player records.

| Asset | Verified extent | Limitation for research |
| --- | --- | --- |
| Overrides | 554 entries, Jul 31 18:05Z–Aug 31 13:45Z: 231 out, 201 in, 122 questionable | 343 target a date; 211 mean “until cleared.” Only 216 have a minutes estimate and 27 any explicit minutes range. Entries are not independent player-games. 374 have a supersession pointer. |
| Availability | 242 snapshots across all 28 dates Aug 4–31; first Aug 4 15:09Z, last Aug 31 15:33Z | Normalized, change-deduplicated snapshots, not every firing and not raw API responses. 12,892 injury rows and 7,624 lineup rows include repeated observations; near/after-tip lineups cannot be pregame inputs. |
| Narrative news | 413 IDs in `live/news_seen.json`; **0 raw payload files** | Text, publication times and revisions are not retained by `news_watch.py`; an ID is not a recoverable article archive. |
| ESPN outcome fallback | 35 schema-2 date files, Jul 25–Aug 30; capture stamps Aug 8 22:35:54Z–Aug 31 04:33:56Z | Regenerable wehoop originals are absent. Outcome availability is capture time, not the date of the game. These files were used only for isolated count masks and identity checks. |
| Polymarket prices | 2,158 market records, 1,163,261 price rows, 2,055 priced markets; median spacing 600 seconds; no duplicate market/timestamp pairs | Intermediate prices **do exist**. Price times span Jun 7, 2024 15:40:03Z–Aug 29, 2026 18:50:14Z. They do not prove bettable spreads, independent match start or resolution time. |

Timestamp checks resolve one exact event for 221 overrides: **195 before tip,
26 at/after tip**. Another 333 are unresolved, including all 211 until-cleared
entries. That is incomplete identity coverage, not evidence that those 333
are late. For example, zero-based entry 338 (Jonquel Jones) was added at
2026-08-19 02:47Z for an event tipped at 01:00Z; its quotation says she would
not return to the game. Entry 269 (Cameron Brink) was added 64 minutes after
tip. These are not pregame availability forecasts for the target game.

Every entry's timing classification and matched tip is retained in the
compressed count evidence, without actual minutes. Before a human comparison,
resolve event-specific validity intervals for until-cleared entries, keep all
requested rows, exclude after-tip observations, distinguish missing roster
records from DNP, and report superseded forecasts separately from a final
pre-tip sensitivity. Do not repair original log entries in place.

**Owner proposal, not implemented:** archive the complete unmodified ESPN news
response on every successful `news_watch.py --fetch`, before ID deduplication,
at `wnba/data/raw/news/<UTC date>/<hhmm>Z.json`. Include UTC observed-at,
request URL, schema version and payload SHA-256; preserve the item's published
time inside the payload. Record unchanged-content firings too. On a same-minute
collision retain both responses with a timestamp/hash suffix; never overwrite.
Retain failed-fetch status separately, never treating it as an empty response.
Commit the ephemeral observations. This proposal changes only archival
persistence, but it edits a routine script and therefore needs owner approval
under the brief. No restart or routine-state change is included.

For Phase 2's two-hours-before-tip scenario, the BP archive currently stores
per-event offer responses/line histories, not a demonstrated complete series of
historically available two-sided snapshots. Its feasibility remains a source
pilot question. Do not promise the scenario from file counts alone.

## 4. Timestamp contracts

The full field-by-field [timestamp table](2026-09-process-audit/timestamps.md)
is generated from `research/clocks.py`. Every named field has an assertion test.
The parser rejects date-only publication times, wrong source offsets, UTC/ET
disagreement, ambiguous/nonexistent Eastern DST hours and epoch milliseconds
passed as seconds. Calendar fields remain dates. Inputs must strictly precede
the forecast; equality fails.

The archive scan found no parse errors in the checked values: 14,273 BP opener
timestamps, 103,637 update timestamps, 1,020 response clocks/epochs, 242 ESPN
capture clocks, 683 event clocks, 554 override clocks and the nonmissing
Polymarket metadata clocks. Epoch endpoints were checked; the complete price
sequence was used only for timing/count summaries.

**The real wehoop dual-column assertion is still blocked by missing parquet
inputs.** Synthetic assertions passing does not establish that PR #2's stored
timestamps were correct. PR #2's 8/24-hour source availability delays are
assumptions; neither a tip timestamp nor final-play wallclock proves when the
provider published the complete file. New work must record this distinction.

## 5. Recorded prior uses of 2025

This inventories documented experiment families and their logged iterations.
It cannot count unlogged runs. Exact registered and subsequent diagnostics are
preserved in `PROGRESS.md`, `AUDIT.md`, the sport READMEs and PR #2.

| Experiment | Use of 2025 for selection, fitting or interpretation |
| --- | --- |
| WNBA legacy fundamentals v1 | Training/evaluation and rejection in favor of the anchored model; `wnba/src/train_eval.py`, WNBA README. |
| WNBA anchored v2 | Weekly fitting, feature/distribution choices, move-model and economic-rule assessment across 2025–26; `train_eval_v2.py`, WNBA README. |
| July WNBA audits and remediation | Repeated join/coherence/source-book/shade/threshold and conditional-ROI analyses; initial shade-cause interpretation corrected; `AUDIT.md` H1–H3, N1–N4 and remediation. |
| WNBA fp Stage A/B/C | 2025 benchmark, two development gates, distribution/calibration assessment before the original 2026 holdout; root PROGRESS Market 1. |
| WNBA fp revisit | Expanding recalibration, threes defense, presumed absences, expansion/season-phase slices and late-season selection rationale; root PROGRESS revisit. |
| WNBA T1 | Talent-vs-incumbent prop evaluation and late-season breakdown; chose the prospective-2 base; training cutoffs do not make the 2025 model selection fresh. |
| WNBA T2 | Both minutes-distribution/grid-calibration iterations evaluated on 2025 and rejected. |
| WNBA oracle minutes diagnostic | 2025 oracle minutes/rate substitutions and error-tail decomposition; explicitly post-hoc. |
| WNBA M | Share-of-minutes engine, frozen and recalibrated delivery, both M-G2 iterations and season-phase diagnosis; 2025 reused. |
| WNBA game models GG/GV2 | v1 rotation variants and v2 possession model, moneyline/spread development on 2025 plus spent early 2026; spread arm chosen after these comparisons. |
| WNBA live-rule design | 2025 expected-value buckets, FanDuel threshold/flat-stake simulations and claimed-versus-realized calibration used to motivate v3 policy; root PROGRESS live reopening. |
| PR #2 rich context | Four models, 8/24-hour scenarios, frozen 7,604-quote comparison, betting rules, later blanket/matched-under controls and dispersion review; openly reused development. |
| Cricket Polymarket P | 2025 within all-history development to Aug 23, 2026: onset revision, wedge, two player-model iterations and franchise split. BBL-only 2018–22 tests did not use 2025 odds. |
| Cricket Polymarket Q | 2025 inside the repeatedly reused 693-market development set; logged iterations 1–25 plus rejected variants, XI choice, maps, opponent adjustment, Blast groups and diagnostic slices. Training-era-only fit does not undo development selection. |
| Other markets: MLB K; NHL N/N2; NBA props; props screens | MLB calendar-2025 development; NHL/NBA 2025–26 development splits, distributions and talent/attempts candidates; original multi-sport price screens also inspect 2025. Separate sports, not additional WNBA observations. |
| Other markets: soccer and NBA game models | Soccer model/anchor variants and regime replays span 2025; NBA reported holdout includes 2024–25/2025–26 with later upper-bound diagnostics. Root PROGRESS and sport READMEs retain their own freshness limits. |

## 6. What changes next

1. Recover the registered wehoop and calibration/talent artifacts and original
   August 1–3 base forecasts, or have the owner explicitly decide how an
   unrecoverable registration is recorded. No substitute was selected here.
2. Apply the dual-clock check to PR #2's pinned schedule bytes before Phase 1.
   The required source is `sportsdataverse/wehoop-wnba-data` at
   `db0d4921daa58aec06da2931eec713d9e9a52c6e`; its manifest/loader remain on PR #2.
3. Phase 1 gets a separate preregistered structural experiment, frozen quote IDs,
   input/time/roster invariance, quoted-mean-bucket calibration and at most two
   calibration attempts. No fitting was spent on it while Phase 0 inputs remain
   unresolved. Phase 2 cannot start before that calibration gate passes.
4. Keep the raw-news archival proposal separate for owner approval. The 500-pair
   historical source pilot and family floors must precede any extractor code.
   The existing log needs timing/identity QC, not just text extraction.
5. Phases 3 and 4 wait for Phase 2's decision and their registered endpoints.
   The current audit does not nominate a market for a 2027 trial.

Two bounded audit passes were used. The second added row-level timestamp
evidence; aggregate counts did not change. The adapter was hardened to reject
tampered implementation evidence and unsupported release bookkeeping without
changing its preregistered result schema or gates. All 26 offline checks pass.
Independent recount/receipt verification is recorded with the deliverables.
