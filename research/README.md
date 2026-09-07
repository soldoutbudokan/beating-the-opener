# Research: start with a decision

Use the [workflow](WORKFLOW.md) for a practical explanation, then copy the
[one-page experiment template](EXPERIMENT_TEMPLATE.md). Keep the initial question
small, check the data early, and write down what result would justify more work.

Three offline commands need only Python 3.10 or newer:

- `python tools/research.py source-check --help`: check a small data sample,
  including missing requests and information available too late.
- `python tools/research.py review --help`: turn the completed richer-WNBA result
  format into a concise decision brief; no fitting or prediction.
- `python tools/research.py pack --help`: package named evidence files with
  checksums, without overwriting previous evidence.

See the [brief from the completed WNBA study](examples/rich-context-decision.md)
and its [provenance and reproduction instructions](examples/README.md).

Run the offline regression checks with:

```sh
python -m unittest discover -s tests -p 'test_research_process.py' -v
```

The same checks run on relevant pull requests and changes to `main` through
[the focused workflow](../.github/workflows/research-process.yml). They use
synthetic fixtures and need no model fitting, archive download or credentials
beyond a read-only repository checkout. The
[checkout action](https://github.com/actions/checkout) is pinned to a commit;
the workflow checks out only these tools, tests and research documentation.

The tools help check and communicate evidence. They do not establish test
freshness, prove a data provider's timestamps, authorize a model change or
replace the existing live operating rules.
