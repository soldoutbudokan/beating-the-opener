# WNBA participation-aware research candidate

This separate implementation prepares and measures a points model without
changing the earlier experiments. It is a research system. Running successfully
does not mean that its forecasts beat market prices.

The source adapter distinguishes playing from measured playing time. A player
can play while the box score displays zero minutes. Such a row informs
participation and retains its points, but supplies no production-rate measurement
without known positive exposure. Missing evidence stays unknown.

## Fixed research sequence

1. Validate the raw source and the histories the model consumes. Keep unavailable
   and conflicting records in the audit; never turn missing records into DNPs.
2. Run the registered variance/count comparison once within its remaining budget,
   then reproduce the saved-input result. Historical means and state coefficients
   are inherited; the comparison only estimates the two registered noise scalars.
3. Prepare the selected candidate only if its correctness gates pass. The fixed
   fallback is an unqualified constant-dispersion research benchmark. Reading a
   through-2025 inference seed is permitted only after candidate selection.
4. Seal the frozen bundle, then capture genuinely future prices and predictions
   with exact raw sources and clocks. The new future boundary excludes protected
   pre-freeze 2026 history. Outcomes from later completed games update the state
   without refitting parameters and settle saved forecasts separately.
5. Evaluate at the registered endpoint and settlement wait, including price
   comparison, costs and uncertainty. Do not promote an unfinished study.

The governing documents are the [participation-v2 registration](../experiments/2026-09-wnba-participation-v2.md),
[prospective details](../experiments/2026-09-wnba-v2-prospective-details.md),
[identity rule](../experiments/2026-09-wnba-v2-identity.md), and the explicit
[invocation correction](../experiments/2026-09-wnba-v2-invocation-correction.md)
and [source quarantine clarification](../experiments/2026-09-wnba-v2-source-quarantine.md).
Read all of them before running an empirical comparison; its total budget does
not reset when a command is restarted.

## Components

| Module | Responsibility |
| --- | --- |
| `sources.py` | Participation, exposure and complete historical source audit |
| `model.py` | Fixed state coefficients and constant/rate count distributions |
| `comparison.py` | Bounded source, variance and count-distribution checks |
| `future.py` | Frozen seed restoration and separate post-freeze state boundary |
| `prepare_candidate.py` | Verify reproduction and package the selected private candidate |
| `capture.py` | Current paired prices, exact identities and full forecast staging |
| `outcomes.py` | Completed post-freeze sources, state updates and settlement revisions |
| `shadow.py` | Immutable admissions, settlements and fixed endpoint evaluation |
| `runtime.py` | Durable Git restoration, publication, verification and recovery |

The [deployment guide](DEPLOYMENT.md) and inactive workflow template describe the
required runtime. They do not activate it. Deployment must have authorized access
to the frozen data repository and must verify a real durable run. A scratch file,
passing synthetic test, empty scan or receipt supplied by a caller is insufficient
to demonstrate unattended operation.

Keep fitted artifacts and private research evidence out of the public repository
unless their publication has been explicitly authorized. The existing operations
watch and Claude routine are separate and are not modified by this module.


## Recovering unavailable fitted artifacts

`comparison reconstruct` is a separately registered recovery path. It recreates
only the original through-2022 box recipe and repeats the unchanged comparison;
it never invents a seal for the unavailable original bundle. The reconstruction
amendment fixes two executions with a shared 600-second limit per execution.
The original `comparison` invocation still requires its actual old fit and seal.

Supply a private expectation JSON before either execution with schema
`wnba-v2-reconstruction-expectation-v1`, source `prior_conversation_record`, and
the previously recorded `model_recipe_hash` and `variance_mode`. These are
verification anchors, not newly selected values. Use distinct exclusive output
directories for primary and reproduction:

```sh
python -m research.wnba_v2.comparison reconstruct \
  --raw /ABSOLUTE/VERIFIED_RAW \
  --output /ABSOLUTE/NEW_PRIMARY \
  --expectation /ABSOLUTE/PRIVATE_EXPECTATION.json \
  --registration research/experiments/2026-09-wnba-v2-reconstruction.md \
  --registration-commit 1ac79e3d856580869ccf4051ec68ca395499b8b8
```

Repeat once into a different output directory for the registered reproduction.
A stopped run is evidence, not permission to retry. Candidate preparation checks
both completed executions, their input/implementation/dependency provenance,
actual clocks, inherited recipe and every substantive artifact. A recorded
candidate mismatch blocks freezing. Only then may preparation read the pinned
2025 seed data and create a new freeze using the current clock.

Keep the entire reconstruction directories, expectation, exact raw manifest,
raw assets and actual retrieval receipts with the new bundle in the authorized
evidence destination. Private artifacts must not be added to the public code
branch. A local restoration test or successful storage write alone does not
establish a running durable prospective service.
