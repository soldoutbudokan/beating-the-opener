# Software correction before structural results — September 7, 2026

The first execution of the selected structural recipe was interrupted during
source-manifest verification, before any scores, results or completed evaluation
receipt were written. The original output directory, evaluation lock, frozen
fit and executed source files are retained in the private evidence.

The verifier repeatedly serialized identical historical records for each quote
and each verification pass. A read-only timing check showed that this redundant
work could dominate the registered compute budget. The correction caches the
canonical bytes of each shared source record and checks each distinct manifest
once per verification call. Its key uses the actual request, schema and ordered
source references. Every row still compares its claimed checksum against the
independently computed checksum. New tests cover canonical-byte equivalence,
reordered or changed references, changed requests and source records, forged
checksums, future clocks and unexpected fields.

This correction changes verification in `research/engine/run.py` and its tests.
Model fitting, forecasting, probability scoring, selection rules and statistical
gates are unchanged. It neither supplies another candidate nor selects a recipe
using 2025 results. The registration already permits separately logged software
repairs and counts forecast-changing repairs after opened results against its
attempt budget. No result was opened in this interrupted execution.

A new software batch contains byte-identical copies of both frozen recipes and
the pre-2025 validation files. Its separate private certificate binds the
original fit and interrupted lock to the reviewed verifier, records unchanged
model/scoring/source bytes, and retains the original recipe selection. The
original fitting batch and lock are not edited or reused. The selected recipe
continues as the first statistical comparison in a new output directory; the
second registered recipe remains the only permitted alternative. There is no
general lock-reset or override option.

The original private snapshot remains the immutable record of learned
parameters. This correction and its reviewed implementation are published
before the new execution. Private model artifacts and their checksums remain
subject to the publication restriction recorded in
`2026-09-props-private-evidence.md`. The final evidence retains interrupted work,
the correction certificate, all completed attempts and independent reproduction.
