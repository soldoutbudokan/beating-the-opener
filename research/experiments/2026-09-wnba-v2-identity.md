# Prospective identity and completed-source rule

Fixed before candidate selection and real prospective capture. Supplements the
participation-v2 registration and prospective details without changing either
the comparison or its outcome-selection gates.

Map a provider event only to a unique current ESPN scoreboard event whose
opposing teams match the fixed BettingPros-to-ESPN team code table and whose UTC
scheduled tip is identical. Preserve both source identities and raw responses.
Unknown team codes, ambiguous matches and tip differences are exclusions.

For players, prefer the provider's explicit ESPN identifier, verified against the
current ESPN team roster. If no explicit crosswalk exists, permit a unique exact
full-name match on that current team roster. The only normalization is Unicode
NFKC, case folding and collapsed whitespace. Do not remove punctuation, match
initials, guess nicknames or use fuzzy matching. Preserve both raw names, team
membership, candidate matches and the mapping rule in the source evidence.
Multiple matches or roster uncertainty remain unresolved and retain their
denominator; they cannot silently borrow another player's history.

After the candidate freeze, refresh completed ESPN boxes only for games whose
tip follows that freeze. Preserve exact response bytes and actual receipt time;
do not read protected pre-freeze 2026 games into the new state. Only explicit
completed-game evidence can supply outcomes. In-progress, contradictory or
unidentified records stay unresolved. New source observations and settlements
append with source hashes, real observation clocks and explicit revision chains;
they never replace a saved prediction. Played with displayed zero minutes is
still played; uncertain exposure omits numeric rate measurement. DNP must be
explicitly supported. Current roster identity is not proof of participation.

These are software/source rules, not model tuning. Do not use later outcomes to
repair the identity or information set of an already recorded forecast.
