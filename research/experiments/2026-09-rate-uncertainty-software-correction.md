# Pre-result software correction: registered clock-parser field names

Status: registered when published, before the corrected execution. This amends
only execution accounting and a clock-parser interface mistake in the
[rate uncertainty pilot](2026-09-rate-uncertainty-pilot.md), published at
`afb56707c0cda9cbec41efcd072bfb04268b1da0`.

The original primary invocation stopped while independently checking source
clocks. The helper passed display labels `completion` and `last play` to the
existing parser, which accepts registered field keys. The observed exception
was `KeyError: 'last play'`. The corrected arguments are
`wehoop.completed_at` and `wehoop.wallclock`. The existing parser and clock
formula are unchanged. A synthetic integration regression checks both fields
and the maximum of the tip delay, completion delay and last-play delay.

The invocation had verified and loaded only the 81 source assets through 2024,
saved its fixed source sample, matched the original 2022 fit input fingerprint,
reproduced its pre-2015 priors and preserved the requested-appearance inventory.
It produced no source-check result, calibration checkpoints, noise scalars,
2023/2024 variance aggregates or new model fit. Preserve the entire original
`research/work/rate-uncertainty-pilot/` directory, its interruption record,
original implementation/test snapshots and budget-accounting evidence.

Exact elapsed time was not emitted when the exception escaped. Charge a
conservative **106 seconds** to the original 600-second primary allowance.
The underlying filesystem bound is 105.451024979 seconds, from output directory
creation to the interruption record written after the process exit was
observed; it includes diagnostic delay. This deliberately overcharges execution
time and does not create additional computation budget.

Authorize exactly one corrected append-only primary invocation at
`research/work/rate-uncertainty-pilot-corrected/`, with **494 seconds** remaining,
and the already registered one exact reproduction at
`research/work/rate-uncertainty-reproduction/`, with **600 seconds**. The CLI
must enforce those limits and emit elapsed time even on failure. Neither output
directory may replace an existing one. If the remaining budget is insufficient,
preserve the partial run and stop.

The corrected code uses the same frozen recipe and prior source manifest,
checks the same deterministic source sample, and retains every denominator,
moment estimator, variance calculation, screening threshold and decision rule.
Bind this amendment in the corrected result and receipt. Reproduction must
match all substantive artifacts; elapsed-time metadata and the different
registered execution allowances are explicitly metadata differences.

No 2025/2026 raw source asset or market comparison is added. There is no new
statistical candidate, tuning attempt or permission to reopen the original
structural experiments. Private evidence remains private under the existing
disclosure decision.
