# Worked examples and the decision from the last study

**Recorded decision:** the richer WNBA data did not demonstrate better forecasts
than the same regression using box scores. Keep that result, preserve the
existing models, and investigate a small sample of genuinely new information
before building another candidate. Do not promote the tree model just because
it had the highest simulated return at one rule.

The [generated decision brief](rich-context-decision.md) makes the supporting
comparison visible in about 5 KB. It shows both information delays, all four
models, every fixed betting rule, the claimed-versus-actual profit gap, and the
qualification about the incumbent's different probability distributions.

This is a **reporting example produced after the original study**. No model was
trained, no prediction was regenerated, and no new test season was opened. The
full original evidence remains in [PR #2](https://github.com/soldoutbudokan/beating-the-opener/pull/2),
at commit `64acad8248ef6536ef7c655d733293f6763263cc`:

- [Original results](https://github.com/soldoutbudokan/beating-the-opener/blob/64acad8248ef6536ef7c655d733293f6763263cc/wnba/research/rich_context/results/results.json)
- [Completed evaluation receipt](https://github.com/soldoutbudokan/beating-the-opener/blob/64acad8248ef6536ef7c655d733293f6763263cc/wnba/research/rich_context/results/evaluation_receipt.json)
- [Original individual-prediction verifier](https://github.com/soldoutbudokan/beating-the-opener/blob/64acad8248ef6536ef7c655d733293f6763263cc/wnba/research/rich_context/tools/verify_saved_results.py)

## Reproduce the brief independently of PR #2 being merged

From the repository root, choose an unused work directory. Fetching this known
commit retrieves repository history; the commands below read only its two
completed 2025 summary files.

```sh
mkdir -p research/work/brief-example
git fetch origin 64acad8248ef6536ef7c655d733293f6763263cc
git show 64acad8248ef6536ef7c655d733293f6763263cc:wnba/research/rich_context/results/results.json > research/work/brief-example/results.json
git show 64acad8248ef6536ef7c655d733293f6763263cc:wnba/research/rich_context/results/evaluation_receipt.json > research/work/brief-example/evaluation_receipt.json
python tools/research.py review --results research/work/brief-example/results.json --receipt research/work/brief-example/evaluation_receipt.json --comparison rich_ridge_minus_box_ridge --evidence-kind reused-development --output research/work/brief-example/decision.md
cmp research/examples/rich-context-decision.md research/work/brief-example/decision.md
```

The brief deliberately has no newly invented minimum-gain threshold. A threshold
chosen after these results were seen cannot become a preregistered success rule.

## A source pilot that should stop

`source-pilot.csv` contains **six synthetic requests**, not real player news.
It deliberately retains one failed match. Shot information has full coverage;
role information has only two usable records out of three.

```sh
python tools/research.py source-check research/examples/source-pilot.csv --expected-rows 6 --min-coverage 0.90 --time-basis observed
```

Expected result: `STOP` with exit code 2, because role coverage is below 90%.
This demonstrates a process decision before fitting anything. It does not
establish the quality or existence of a real historical injury dataset.

## What was measured about efficiency

The [local demonstration record](efficiency-check.json) reports:

- The brief read **43,938 bytes** of completed summaries and receipt, took about
  **one millisecond inside Python**, and needed no model dependencies or raw data.
  Starting a separate Python process adds overhead.
- The packaging helper retained all **36 published result files**, reducing
  **17.26 MB to 6.06 MB**, with every file named and checksummed inside the bundle.
  That is about 65% smaller than the source files combined, with no evidence removed.
- All original input checksums were unchanged afterward. Zero models ran.

These are one-machine measurements of these helpers, not a controlled claim
about how much faster future research will be. The large demonstration bundle
is a disposable local copy of already-published evidence and is not committed
again in this PR. The helper's tests verify deterministic packaging and recovery
of every input byte; publication of future unique evidence remains part of the
workflow.
