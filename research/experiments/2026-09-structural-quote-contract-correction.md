# Quote-adapter correction before structural results — September 7, 2026

The first execution under the optimized verifier stopped at the scorer's input
validation before producing any aggregate score, result or completed receipt.
Its lock, input archive and code identity remain preserved. The failure exposed
two adapter errors, which are corrected before another execution.

PR #2's saved `p_open` uses **power devig**. The shared scorer's already-published
contract derives **proportional devig** from the two quoted prices. These are
different ways to remove the bookmaker's margin, not a floating-point rounding
discrepancy. The adapter now supplies the price-derived proportional probability
and retains the exact saved PR #2 probability as `pr2_power_p_open`. The primary
opener comparison follows the existing scorer contract; a separately named
paired power-devig sensitivity preserves the earlier benchmark convention.
Neither method is selected after seeing results.

The quote adapter also used incorrect source-market IDs for rebounds, assists
and threes. The pinned feed maps points to 393, rebounds to 397, assists to 391
and threes to 390. Correcting the lookup restores access to the matching archived
offers for close-reference scoring. Both sides still require the same book and
line, coherent timestamps and a time strictly before tip. Provider-reported
close history remains an assumed historical reference.

The preflight checks all 7,604 frozen quote identities, clocks and paired prices,
using synthetic forecast and outcome sentinels to exercise the complete strict
scoring schema without producing outcome scores. It also checks exact archived
offer, player, event and market identity. The source population, actual quote
prices and frozen box-model probabilities are unchanged.

Model fitting, forecast mathematics, the absolute log-loss threshold, count and
calibration gates, bet selection rules and iteration budget are unchanged.
`research/engine/scoring.py` stays byte-identical, preserving the completed
prospective-props evidence contract. The repaired adapter and additional named
benchmark report receive a separate private software certificate; the learned
recipes remain byte-identical to the original private freeze. Original locks
are neither removed nor reset. No completed statistical attempt is discarded.
