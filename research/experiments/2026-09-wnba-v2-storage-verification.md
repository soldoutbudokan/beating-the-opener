# Verify persisted evidence before hosted activation

During deployment preparation, a previous local restoration contained a
truncated derived audit file although its original ZIP member was complete and
matched the frozen manifest. The archive, model recipe and inference seed remain
intact. The old restoration receipt checked the archive before writing but did
not establish that every resulting file was complete. Preserve that receipt and
record this limitation; do not continue to describe it as full persisted-byte
verification. The underlying filesystem truncation mechanism is not established.

This is an operational correctness repair, not a new model experiment:

1. Keep evidence writes exclusive. Require the returned byte count to match the
   requested write, flush and synchronize, then read back the exact file bytes.
   Fail on any difference and retain incomplete files as failure evidence.
   Do not silently delete, overwrite or retry an interrupted evidence path.
2. Test short reported writes, silent truncation, same-length corruption,
   complete writes and refusal to overwrite existing evidence.
3. Preserve the previous bundle. A separate runtime-only repack verifies every
   old archive member, its exact published implementation and recorded candidate
   identity before constructing a replacement. The only implementation changes
   allowed are this storage verification, the repack adapter and this amendment.
   The model, source adapters, inherited estimator, comparison and decision rules
   must match the old pinned implementation exactly.
4. Copy the model recipe, frozen seed, source audit and source manifest without
   changing their bytes. Preserve the recorded selection and reconstruction
   provenance. Give the replacement its actual current freeze clock and a new
   candidate identity; bind the old archive and code identities in its receipt.
   Do not refit, rescore, select a different candidate, read new historical inputs
   or extend any completed research budget.
5. Verify the replacement ZIP and every persisted file after creation. Publish
   it to the owner-authorized private repository, independently retrieve its
   exact bytes, verify that its commit is reachable, and only then record the
   observed durability clock and initialize the prospective study.
6. Activate the existing private-only collector using the reviewed replacement
   implementation commit. Verify an actual hosted run, its immutable evidence
   commits and seal, and a subsequent restoration before considering deployment
   complete. A successful no-event request records zero forecasts; it does not
   prove live-offer schema coverage, notification delivery or a market advantage.

The prior candidate choice and all prospective endpoint, settlement, control,
cost and qualification rules remain unchanged. This repair does not reopen old
studies, alter the existing public operations watch or a Claude routine, or
authorize wagers. The owner's instruction to complete deployment before a PR
remains in force.
