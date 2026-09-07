# Phase 0 decision

**Decision: collect missing input.** Recover the registered WNBA replay artifacts and pinned wehoop schedule inputs; the count proxy is already beyond the props endpoint. Preserve the protected windows and exclude after-tip overrides from future pregame tests. Phase 1 fitting has not started.

2025 has repeatedly informed development and is not an independent test.

| Gate | Status | Finding |
| --- | --- | --- |
| AUDIT | PASS | All six validity requirements checked across live WNBA, PR #2 and cricket; repairs and owner-only live proposals recorded. |
| CLOCKS | BLOCKED | Explicit source contracts and synthetic assertions pass; checked archive clocks parse, but real pinned wehoop dual-column comparison is unavailable. |
| WINDOWS | BLOCKED | Five endpoints and release rules recorded. Props proxy crosses 3000 on Aug 15; exact frozen scoring artifacts missing. Other arms are not due. |
| NEWS | BLOCKED | 554 entries and 242 snapshots inventoried; zero post-endpoint entries unlocked. 26 matched entries are at/after tip and 333 lack a unique event. |
| RECEIPT | PASS | Checksummed count evidence and implementation; source recount and no-overwrite verification provided. |

| Protected arm | Qualified n | Archive upper bound | Status |
| --- | ---: | ---: | --- |
| fp-prospective-1 | unknown | 6711 | BLOCKED |
| fp-prospective-2 | unknown | 6711 | BLOCKED |
| fp-games-prospective-1 | unknown | 85 | WAIT |
| pm-prospective-1 | 0 | 0 | WAIT |
| pm-prospective-2 | 0 | 0 | WAIT |

Human baseline: 554 logged entries, 0 unlocked, 0 matched for scoring. No complete protected-arm result releases the post-endpoint window; accuracy metrics remain unestimated.

## Prior uses of 2025

- WNBA fundamentals v1: 2025 fitting/evaluation and rejection (WNBA README; train_eval.py).
- WNBA anchored v2: weekly fitting, feature/distribution/selection and economic-rule analysis across 2025-26 (WNBA README; train_eval_v2.py).
- July WNBA audits/remediation: repeated 2025 join, source, quote, shade, threshold and conditional-return diagnostics (AUDIT.md).
- WNBA fp A/B/C: 2025 benchmark, development gates and distribution/calibration assessment (PROGRESS Market 1).
- WNBA fp revisit: 2025 expanding recalibration, threes defense, absence and season-phase/expansion diagnostics.
- WNBA T1: 2025 talent-model selection and late-season breakdown before prospective-2.
- WNBA T2: both 2025 minutes-distribution/grid-calibration iterations rejected.
- WNBA oracle minutes: 2025 oracle minutes/rate and error-tail diagnostics, post-hoc.
- WNBA M: 2025 share-of-minutes engine, both delivery/calibration iterations and phase diagnostics.
- WNBA GG/GV2: rotation and possession game models, moneyline/spread selection on 2025 and spent early 2026.
- WNBA v3 live-rule design: 2025 expected-value buckets, FanDuel thresholds and claim/return calibration.
- PR #2: four 2025 models, 8/24-hour scenarios, fixed quote comparison, economic rules, later under controls and dispersion review.
- Cricket P: 2025 within all-history development; onset revision, wedge, two model iterations and franchise split.
- Cricket Q: 2025 within reused 693-market development; logged iterations 1-25 plus variants, maps, XI choices, opponent adjustment and diagnostics.
- Other sports: MLB K calendar-2025 dev; NHL N/N2 and NBA props 2025-26 dev splits; multi-sport price screens.
- Other sports: soccer variants/regime replays spanning 2025; NBA game holdout seasons containing 2025 and later upper-bound diagnostics.

## Verification limits

This adapter checks the audit schema, gate coverage, protected-release bookkeeping, count arithmetic and saved evidence checksums. It does not independently establish recipe fidelity, timestamp truth, archive completeness or historical model validity.

Results SHA-256: `a53ea212d82d810b132db67c496f493cb19951887faecbd656de08133565aaf6`

Receipt SHA-256: `b2fa0c46200a28642f8bff59e23223dfabb52bf29c74677a13e7a26fffc71a50`
