"""Shared helpers for the live-betting files (bets.csv, the scoreboard).

Kept dependency-free (stdlib only) so fill logging works in a bare checkout,
before the model artifacts are built.
"""
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
BETS = os.path.join(LIVE, "bets.csv")
PICKS = os.path.join(LIVE, "picks.csv")

# bets.csv schema - the single definition; settle_bets.py and log_fill.py
# both bind to this so a column can never be added in one and missed in the
# other.
COLS = ["key", "placed_at", "match_date", "event_id", "market", "player",
        "side", "line", "odds_taken", "stake", "model_p", "ev_claimed",
        "status", "result", "actual", "clv", "clv_cal", "clv_source", "pnl",
        "notes"]


def refresh_site():
    """Rebuild docs/index.html from both markets' live files - never fatal.

    Every writer of bets.csv calls this: the published scoreboard is
    generated, so a fill logged between settlements leaves a stale page
    unless the logger rebuilds it too (owner instruction, 2026-08-08).
    """
    try:
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "..", "site", "build_site.py")],
                           capture_output=True, text=True, timeout=120)
        print((r.stdout or r.stderr).strip().splitlines()[-1])
    except Exception as e:  # a broken page must never block the caller
        print(f"site build skipped: {e}")


def amer_to_dec_scalar(cost):
    """American odds -> decimal odds, plain float (no numpy)."""
    c = float(cost)
    return 1.0 + 100.0 / -c if c < 0 else 1.0 + c / 100.0
