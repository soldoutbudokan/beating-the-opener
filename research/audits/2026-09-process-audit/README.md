# Reproduce the Phase 0 audit

Decision: collect the missing frozen artifacts. No prospective scores or news
accuracy estimates exist in this evidence package. 2025 is reused development;
the complete prior-use list is in `results.json` and the main audit report.

The preregistration and adapter were published in
`5bfd24d0f669eba88b793d4153ea4a3ebd4556be`, before measurements. Main source
snapshot: `d2b5e685ce10d9670fc93bf8c22810bc8cc5df83`; closed PR #2 source:
`64acad8248ef6536ef7c655d733293f6763263cc`. Both must be retrievable in Git.
No Git history or source archive was removed. Counts cover the committed
archive, not records that an inaccessible external source might hold.

From a clean checkout of this PR's final commit, install `requirements.txt`
in a virtual environment. The focused tests and review adapter use only the
standard library; source recount additionally uses pandas and pyarrow.

```sh
python -m unittest discover -s tests -v
python tools/verify_process_audit.py
python tools/verify_process_audit.py --reproduce research/work/phase0-independent
python tools/research.py review --adapter process-audit-v1 --results research/audits/2026-09-process-audit/results.json --receipt research/audits/2026-09-process-audit/receipt.json --evidence-kind reused-development --output research/work/phase0-decision.md
```

The reproduction directory and generated brief must not exist. Commands refuse
overwrites. The independent recount must match the complete saved count object,
including all 1,302 source-file hashes, availability rows and override timing
classifications. It reads existing raw inputs; it does not fetch or modify them.
No model or prospective scorer is imported or executed. The ESPN count proxy
reads outcome fields only to construct eligibility masks, then emits counts;
it cannot release a protected arm.

`counts.json.gz` is deterministic gzip containing the full count evidence and
per-input hashes. `results.json` is the compact decision schema. `receipt.json`
checksums the results, evidence and implementation. `verification.json` records
the independent source recount. `decision.md` is produced by the registered
review adapter. `timestamps.md` is generated from the clock contract registry.
Tests sit outside the implementation hash set so adding checks does not change
the frozen counting recipe.

Two audit passes were used: the second added matched tip/identity evidence to
each override timing row. Counts and classifications did not change. Subsequent
verification is reproduction, not a new outcome analysis or gate iteration.
The adapter's later hardening validates implementation hashes and receipt
bookkeeping; no result fields, gate thresholds or protected endpoints changed.

The source hashes and tests prove reproducible counts and parser behavior.
They do not establish provider timestamp truth, complete event identity,
historically executable prices, original prospective calibration, or a real
wehoop dual-column check. That check requires the missing pinned parquets.
The report distinguishes unknown qualified counts from archive upper bounds.
