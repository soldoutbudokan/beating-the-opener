# props (subproject of beating-the-opener)

Multi-sport player-prop screen: MLB / NBA / NFL / NHL openers vs closes from
the BettingPros archive, FanDuel as the tradeable book. Research phase —
there is no live/ here yet; nothing in this directory places or logs bets.

- **HALTED 2026-07-31**: PLAN.md's Phase 2/3 port the WNBA anchor-on-open
  architecture, which the user retired repo-wide in favour of
  first-principles pricing (../PLAN.md). Don't run further phases as written.
  The QC/grading work, the archive, and the pre-registration discipline all
  carry over — see the banner at the top of PLAN.md.
- Pre-registered gates and the decision log live in PLAN.md. Gates are
  written down BEFORE the phase that uses them runs. Don't move them after.
- `data/raw/bp/` is an irreplaceable committed archive (upstream deletes old
  seasons) — never delete, shrink, or gitignore it. Offers are stored in a
  slimmed schema (see src/slim.py); events files are raw payloads.
- Run scripts from inside props/ (`python3 src/<script>.py` — sibling
  imports are path-relative).
- Outcome data (data/mlb/, data/nba/, ...) and derived .pkl tables are
  gitignored and regenerable; the odds archive is not.
