# A durable runtime for the frozen WNBA shadow trial

The executable adapter is `research.wnba_v2.runtime`. Its workflow remains an
**inactive template** under `templates/wnba-shadow.yml`; adding these source
files does not start collection. No model is deployed, no forecast is admitted,
and no notification delivery is established by a passing local test alone.

The intended destination is a private Git repository authorized to hold the
frozen parameters, source observations, forecasts and results. The original
public repository contains the reviewed implementation. Existing public data
collection and the existing operations notification watch remain separate.

## Required deployment inputs

The authorized evidence repository must have a `main` branch and these files:

| Path | Required content |
| --- | --- |
| `data/wnba-shadow/runtime.json` | Runtime configuration shown below. |
| `data/wnba-shadow/frozen/candidate.zip` | Actual frozen bundle bytes. |
| `data/wnba-shadow/ledger/study.json` | Valid `wnba-shadow-study-v1` study with actual freeze and durable storage receipts. |

The deployment configuration has this shape. Replace the example identities
with the actual repository and reviewed 40-character commit; they are not
defaults or permission grants.

```json
{
  "schema": "wnba-shadow-runtime-v1",
  "repository_full_name": "OWNER/PRIVATE_EVIDENCE_REPOSITORY",
  "code_commit": "ACTUAL_40_CHARACTER_REVIEWED_COMMIT",
  "bundle_path": "data/wnba-shadow/frozen/candidate.zip",
  "ledger_path": "data/wnba-shadow/ledger"
}
```

The bundle ZIP contains `MANIFEST.json`, the selected recipe JSON, the frozen
seed observations as `observations.jsonl.gz`, and any explicitly listed source
receipts. `recipe_file` and `seed_file` identify their exact paths. The manifest
schema is `wnba-shadow-bundle-v1`; it binds `candidate_id`, `frozen_at`,
`model_recipe_hash`, and `implementation_sha256` to the study. Its `files` map
contains every member except `MANIFEST.json`, each with `sha256` and `bytes`.
Extra files, duplicated members, path traversal and symlinks are rejected.

`implementation_files` uses the same path-to-hash-and-size map for all imported
local model, source and runtime code and the pinned requirements file.
`implementation_sha256` is SHA-256 of `shadow.encode(implementation_files)`.
The runtime requires its complete named core dependency set and verifies each
file in the actual pinned checkout. The ZIP hash is `study.bundle_sha256`;
the recipe file hash is `study.recipe_sha256`. `model_recipe_hash` is the
configuration's own canonical identity and is distinct from file formatting.

The study's bundle receipt must identify the actual previously saved artifact
and its actual durability clock. A receipt copied from another candidate, a
local creation time presented as a completed upload, or a made-up version is
invalid. Library receipts can be retained from the original freeze; this Git
runtime does not claim to independently query Library. It verifies the exact
frozen ZIP already present in the authorized repository on every run.
Previously sealed forecast batches require verifiable Git receipts in this
runtime. Keep earlier Library batch archives separately; do not copy their
receipts into the active Git ledger and claim their clocks were independently
verified here.

Seed history is restricted to data through 2025, prepared after candidate
selection and available before the freeze. It does not include the earlier
protected 2026 window. The only later state updates are genuinely observed
completed games after the freeze, accepted by `FutureOnlyIndex`.

## What one run does

1. Confirm the Git remote matches the configured repository. Read authenticated
   GitHub metadata and require a private repository on `main` by default.
2. Fetch current evidence into a new scratch directory. Verify the study, ZIP,
   recipe, seed observations, implementation files and exact code commit.
   Verify every restored sealed batch against its reachable Git commit and all
   exact manifest bytes before using its forecasts or settlements.
3. Refresh completed post-freeze games, keeping raw ESPN bytes, receipt clocks,
   normalized observations and append-only settlement revisions. Reconcile
   pending dates and unresolved forecasts. A degraded history refresh blocks
   new forecasts while its captured evidence is preserved.
   Consume prior observations only when their completed history receipt and
   underlying raw source hashes and clocks have been verified. Interrupted
   observation files without a receipt remain evidence, not model inputs.
4. Capture current prices and identity evidence; call the frozen model once per
   eligible request. There is no fitting entry point. Preserve raw responses,
   failures and exclusions, including a healthy request that found no events.
5. Publish the immutable staged batch, captured sources, refreshed history and
   settlement additions. The Git writer starts from the latest remote tree,
   preserves unrelated files and permits only append-only evidence paths.
6. Fetch the remote again and verify the published commit is reachable and all
   staged bytes match. **Only then** observe `durable_at`. Bind that receipt to
   the exact batch manifest path and commit, seal the batch, and publish the
   seal and terminal run receipt in a second append.

A local forecast is ineligible until its batch has a durable receipt. The
receipt must establish that its bytes were durably stored at least 15 minutes
before tip. A slow push can therefore leave correctly generated forecasts
excluded. The receipt records the runner's observed completion of remote
verification; it is not a provider-signed timestamp or a guarantee about clock
synchronization. Git commits preserve the exact published data and causal
sequence for independent review.

On a concurrent fast-forward, the writer fetches the latest tree and retries up
to three times. Different run directories survive together. An existing file
must match exactly; conflicting bytes fail. No force-push, deletion, full-tree
replacement or mutable aggregate ledger is used. Changes to the frozen bundle,
study or runtime configuration during a run stop publication.
Deleted or rewritten prior evidence also stops publication. Each proposed merge
revalidates forecast identities and complete settlement revision chains. If a
concurrent settlement produces a conflicting child, the rejected additions are
preserved under the run's diagnostic directory and the active ledger keeps the
already published revision.

An interrupted run with a published batch can resume without recapturing. If
its original seal was lost, the recovery uses its **current** verified durability
time; it never backdates the receipt. A completed run ID is not run again.
Authentication or policy failures with unchanged remote state are not retried
as though they were merge conflicts.

The runtime keeps refreshing settlements after the registered endpoint but
stops capturing new forecasts. It does not evaluate early or automatically
promote a candidate. An `EVALUATION_DUE` record calls for the registered final
evaluation and independent reproduction; `EVALUATED` remains terminal.
An interrupted evaluation also blocks further forecast capture.

## Activation and verification

After the private evidence destination and frozen artifacts exist, copy the
template into that repository's `.github/workflows/wnba-shadow.yml`. Set its
`WNBA_CODE_COMMIT` repository variable to the reviewed source commit, matching
`runtime.json`. Add a `BETTINGPROS_API_KEY` secret if needed; the existing
source adapter can otherwise use the repository's already committed key.
The workflow's GitHub token needs content write permission in the evidence
repository. The code checkout keeps no publishing credentials.

The template runs hourly with one writer at a time and supports a manual first
run. Verify that this first hosted run produced a reachable batch commit,
its seal, and `data/wnba-shadow/runs/RUN_ID/runtime.json`. A `NO_EVENTS` result
confirms a successful source check, **not** that any trial forecasts were
recorded. A degraded or failed run preserves available evidence and returns a
nonzero process status. No assertion about alert delivery follows from that
exit status; configure and test any separate notification path explicitly.

Dependency installation, malformed deployment configuration or Git permission
failure can stop execution before a durable run receipt can be saved. GitHub's
run result then provides the failure evidence; the CLI reports
`receipt_saved: false`. The template contains no expiring artifact upload as a
substitute for the primary Git archive.

The CLI can also run from its pinned code checkout:

```sh
python -m research.wnba_v2.runtime \
  --code-repo /ABSOLUTE/PINNED_CODE_CHECKOUT \
  --data-repo /ABSOLUTE/AUTHORIZED_EVIDENCE_CHECKOUT \
  --repository OWNER/PRIVATE_EVIDENCE_REPOSITORY \
  --run-id UNIQUE_RUN_ID
```

`--allow-public-evidence` exists only for an explicitly authorized public
deployment. It does not grant disclosure permission, and this template never
sets it. Do not use it to work around an approval rejection. Public deployment
would expose parameters, forecasts, source evidence and results and therefore
requires that concrete disclosure decision first.

## Verification scope

The runtime tests use actual temporary bare Git repositories to check normal
pushes, simultaneous writers, immutable conflicts, preserved unrelated files,
remote byte verification and recovery. Synthetic bundles test exact restoration
and tamper rejection. Integration tests exercise the frozen model constructor,
no-event capture, failure receipts and ledger sealing. These tests do not claim
that a hosted private deployment has been activated or that the model has an
advantage over market prices.
