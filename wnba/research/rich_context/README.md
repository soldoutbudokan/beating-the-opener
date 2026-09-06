# Richer WNBA information: research experiment

This experiment tests whether the details of previous games improve player
forecasts: shot locations and types, assisted baskets, late-game involvement,
past teammates, and the opportunities an opponent allows.

It compares four models: the corrected incumbent, a box-score regression,
the same regression with richer information, and a small tree model using
that richer information. Every comparison is reported, including failures.

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
python -m wnba.research.rich_context.sources --download --qc
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python -m wnba.research.rich_context.run prepare
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python -m wnba.research.rich_context.run fit
```

The download is about 48 MB. Preparation constructs past-game feature states
and freezes the talent engine's fitting before 2015. Fitting uses 2015–2022,
selects settings on 2023, validates on 2024, and uses those saved 2024
predictions to calibrate the probability distributions for 2025.

The `evaluate` stage produces the one registered 2025 report, then writes a
receipt that prevents silently repeating it. Reproducing a published report
should use a separate checkout/output directory and retain the original
receipt; it is a reproduction of the same development exercise, not another
independent test. Detailed evaluation commands and results will be recorded
with the completed run.

The historical odds archive is already tracked in this repository. A sparse
checkout must materialize `wnba/data/raw/bp/events_2025.json.gz` and the 2025
events' files under `wnba/data/raw/bp/offers/`. Do not fetch current odds or
delete existing archives to run this experiment.

## Checks

```sh
python -m unittest discover -s wnba/research/rich_context/tests -v
python -m unittest wnba.research.rich_context.test_baseline_benchmark -v
```

These checks cover historical information timing, latest-game updates,
unchanged predictions when unrelated offers change, delayed data, source
quality, probability accounting, integer-line pushes, and chronological bet
selection.

Public play-by-play is not player tracking. Assisted makes are not potential
assists, and recorded late-game actions are not measured playing time. The
files were downloaded retrospectively; an explicit eight-hour publication
delay, with a 24-hour sensitivity, approximates when prior games could enter
the model. Historical quotes are price comparisons, not proof of executable
fills.
