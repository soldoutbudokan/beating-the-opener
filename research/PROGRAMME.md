# Structural and news research programme

PR #4 contains the implementation and tests for the owner-directed programme.
It remains unmerged. The dated registrations in `experiments/` define the
statistical gates; implementation does not waive them.

## Components

| Component | Entry point | What it checks |
|---|---|---|
| Historical sources and point-in-time store | `engine/sources.py`, `engine/store.py` | Exact source bytes, event identity, source availability and protected seasons |
| Minutes and production model | `engine/model.py` | Participation, conditional minutes and count distributions fitted before evaluation |
| Structural evaluation | `python -m research.engine.run` | Frozen quote population, calibration, count likelihood, opener/close comparisons and saved-row reproduction |
| Registered prospective props | `python -m research.prospective_props.runner` | Pinned native recipes, endpoint population, immutable freeze, single primary evaluation and explicit reproduction |
| Human availability baseline | `prospective_props/human_baseline.py` | Released dates, pre-tip entries, exact identities, unresolved records and minimum evidence floors |
| Cricket capture | `python -m research.engine.cricket_capture` | Isolated fetch, recorded API responses, resolved-market probe and preservation of old observations |
| FanDuel coverage diagnosis | `diagnostics/2026-09-fanduel/source.py` | Book identity, source shares and observed live-sheet gate restrictions from pinned Git inputs |
| Decision review | `python tools/research.py review` | Adapter-specific receipts, gates and evidence labels |

## Run the software checks

Use Python 3.12 from the repository root:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r research/engine/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The GitHub Actions workflow runs the same offline suite. Tests cover timing and
future-data invariance, target-outcome exclusion, distribution mathematics,
pushes and refunds, paired comparisons, source failures, tampering and overwrite
refusal. Passing fixtures does not establish an empirical model advantage.

## Reproduce saved evidence

The private evidence package supplies frozen inputs, predictions, receipts,
source manifests and a reproduction guide. Public code is available for review;
new model artifacts and their checksums await the owner's approval for public
disclosure. This restriction is recorded in
`experiments/2026-09-props-private-evidence.md`.

Structural reports can be checked without refitting:

```sh
python -m research.engine.run verify --run research/work/structural-evaluation-1-final
python -m research.engine.run reproduce --run research/work/structural-evaluation-1-final --output research/work/structural-reproduction-new
```

Use the implementation commit recorded with each run. Output directories must
be new. The historical Phase 0 audit uses its original helper commit, recorded
in its receipt; later code does not rewrite that audit.

## Programme gates

The owner's overdue-props amendment permits reconstruction from freshly
downloaded source data and requires that caveat in the scores. Completed props
receipts release the post-endpoint human baseline. They do not release the
WNBA game or cricket evaluation windows.

Phase 1 allows at most two frozen structural recipes. Calibration must pass
before the registered play-by-play comparison and Phase 2 news pilot. A failed
news source pilot calls for prospective collection. The extractor follows a
successful pilot. Later market phases require the Phase 2 decision and their
original prospective endpoints. Every executed attempt and failed source family
belongs in the final evidence; a gate can stop work without making the remaining
research completed.

The complete inventory of earlier 2025 development appears in
`audits/2026-09-process-audit.md`. Reusing that season cannot create a fresh
holdout. This PR does not activate a live model, alter a routine or place bets.
