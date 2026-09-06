# Richer WNBA information: research experiment

This experiment tests whether the details of previous games improve player
forecasts: shot locations and types, assisted baskets, late-game involvement,
past teammates, and the opportunities an opponent allows.

It compares four models: the corrected incumbent, a box-score regression,
the same regression with richer information, and a small tree model using
that richer information. Every comparison is reported, including failures.

**The richer data did not demonstrate an improvement over box-only
regression.** Read the [plain-language report](REPORT.md), see the
[comparison chart](results/comparison.png), or inspect the
[full numerical results](results/results.json).

The final historical comparison uses 2025, a development season already used
by this project. It does not use 2026 or change the live system.

- [Research plan, fixed before evaluation](PROTOCOL.md)
- [What data we obtained and its limits](SOURCES.md)
- [Exact input files and hashes](results/source_manifest.json)
- [Data-quality inventory](results/source_quality.json)

## Reproduce

From the repository root, in a Python 3.11 or newer environment:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r wnba/research/rich_context/requirements.txt
python -m wnba.research.rich_context.sources --qc
python wnba/research/rich_context/tools/reproduce.py prepare --output-dir wnba/data/rich_context/reproductions/replay-1 --check
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python wnba/research/rich_context/tools/reproduce.py prepare --output-dir wnba/data/rich_context/reproductions/replay-1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python wnba/research/rich_context/tools/reproduce.py fit --output-dir wnba/data/rich_context/reproductions/replay-1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python wnba/research/rich_context/tools/reproduce.py evaluate --output-dir wnba/data/rich_context/reproductions/replay-1
```

The download is about 48 MB. Preparation constructs past-game feature states
and freezes the talent engine's fitting before 2015. Fitting uses 2015–2022,
selects settings on 2023, validates on 2024, and uses those saved 2024
predictions to calibrate the probability distributions for 2025.

The source command downloads and checks the frozen inputs; there is no
`--download` flag. Run it once before preparation. It rewrites the ignored raw
download manifest with a new retrieval timestamp, even when the underlying
files match. Do not rerun that download between preparation, fitting, and
evaluation: each phase checks the same manifest fingerprint. The committed
source manifest under `results/` is the published provenance; do not replace
it with the newly dated download manifest.

The wrapper verifies the model-code fingerprint against the published
evaluation receipt and checks every downloaded file against the published
source fingerprints. It writes only to the requested reproduction directory,
with separate `results/` and `cache/` children. The example directory is
ignored by Git. Use a new directory name for another reproduction. The
`--check` command verifies paths and inputs without writing files or fitting
models.

The original `results/evaluation_receipt.json` is intentionally committed.
Calling the frozen runner's `evaluate` command directly would refuse to
overwrite it. Keep that receipt and use the wrapper; it redirects output
without changing the registered model code. Its own receipt also prevents
silently repeating an evaluation in the same reproduction directory.

Compare the new `results/results.json` and forecast files with the published
ones. This repeats the same development exercise, not an independent test.
Receipt timestamps and manifest fingerprints can differ because of the new
download date; input-file fingerprints must match. Use the published study
commit and pinned dependencies if later model-code changes prevent a replay.

The historical odds archive is already tracked in this repository. A sparse
checkout must materialize `wnba/data/raw/bp/events_2025.json.gz` and the 2025
events' files under `wnba/data/raw/bp/offers/`. Do not fetch current odds or
delete existing archives to run this experiment.

## Checks

```sh
python -m unittest discover -s wnba/research/rich_context/tests -v
python -m unittest wnba.research.rich_context.test_baseline_benchmark -v
python wnba/research/rich_context/tools/verify_saved_results.py
```

These checks cover historical information timing, latest-game updates,
unchanged predictions when unrelated offers change, delayed data, source
quality, probability accounting, integer-line pushes, and chronological bet
selection.

The saved-results verifier checks the published predictions, chronological
selections and all 16 return intervals without fitting a model or downloading
data. It reports the optional source-history check as skipped when the local
preparation cache is absent. To redraw the published chart from saved results,
run `python wnba/research/rich_context/tools/plot_results.py`.

Public play-by-play is not player tracking. Assisted makes are not potential
assists, and recorded late-game actions are not measured playing time. The
files were downloaded retrospectively; an explicit eight-hour publication
delay, with a 24-hour sensitivity, approximates when prior games could enter
the model. Historical quotes are price comparisons, not proof of executable
fills.
