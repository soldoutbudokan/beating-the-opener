"""Log owner-reported fills to live/bets.csv and rebuild the scoreboard.

The owner reports fills in plain words ("got Zandalasini, Cloud and Copper
for $1 each"). This turns that into rows on the bet log: it copies the pick's
fields from live/picks.csv, stamps `ev_claimed` at the price ACTUALLY taken,
appends with status=open, and rebuilds docs/index.html - the published page is
generated, so a fill logged between settlements used to leave it stale
(owner instruction, 2026-08-08).

It never invents a fill: every row comes from an argument you passed, and a
key already on the log is refused rather than double-counted.

Usage
  python3 src/log_fill.py --stake 1 Zandalasini Cloud "Kahleah Copper"
  python3 src/log_fill.py --stake 2 --price 128 "2712_assists_courtney williams_over"
  python3 src/log_fill.py --stake 1 --dry-run Malonga     # preview only

Each positional arg is a pick key, or any substring that matches exactly one
`play=True` row in picks.csv (player name, optionally plus market:
"Cloud points"). Ambiguous or unmatched -> it stops and lists the candidates.

Defaults: price = the sheet's FanDuel price, stake = the sheet's Kelly stake.
`--price` applies to one fill only (a single price cannot describe several).
Exit lines: LOGGED <n> | NOTHING_LOGGED
"""
import argparse
import csv
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_utils import BETS, COLS, PICKS, amer_to_dec_scalar, refresh_site

ET = ZoneInfo("America/New_York")


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def resolve(arg, picks):
    """A pick key, or a substring matching exactly one play=True row."""
    exact = [p for p in picks if p["key"] == arg]
    if exact:
        return exact[0]
    terms = arg.lower().split()
    hay = [(p, f"{p['player']} {p['market']} {p['side']} {p['key']}".lower())
           for p in picks if p.get("play") == "True"]
    hits = [p for p, h in hay if all(t in h for t in terms)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(
            f"no play=True pick matches {arg!r}. Pass the exact key if the "
            f"pick is off the sheet - fills are never invented here.")
    raise SystemExit(
        f"{arg!r} matches {len(hits)} picks - be more specific:\n" +
        "\n".join(f"  {p['key']}  ({p['player']} {p['market']} {p['side']} "
                  f"{p['fd_line']})" for p in hits))


def build_row(pick, price, stake, placed_at, today, prior, extra_note):
    """One bets.csv row. ev_claimed is stamped at the price actually taken -
    the sheet's `ev` is quoted at the sheet price and drifts once the fill
    comes in at something else (PROTOCOL 'Reporting fills')."""
    model_p = float(pick["model_p"])
    notes = [f"owner fill {today}"]
    sheet_stake = float(pick["stake"])
    if abs(stake - sheet_stake) > 1e-9:
        notes.append(f"deliberate flat ${stake:g} staking "
                     f"(sheet Kelly stake was ${sheet_stake:.2f})")
    sheet_price = int(float(pick["fd_cost"]))
    if price != sheet_price:
        notes.append(f"filled at {price:+d}, sheet quoted {sheet_price:+d}")
    if prior:
        other = "; ".join(f"{o['market']} {o['side']} {o['line']}"
                          for o in prior)
        notes.append(f"second bet on {pick['player']} in event "
                     f"{pick['event_id']} ({other} already open) - PROTOCOL "
                     f"one-bet-per-player-per-game not satisfied, user's call")
    if extra_note:
        notes.append(extra_note)
    return {
        "key": pick["key"], "placed_at": placed_at, "match_date": pick["date"],
        "event_id": pick["event_id"], "market": pick["market"],
        "player": pick["player"], "side": pick["side"], "line": pick["fd_line"],
        "odds_taken": price, "stake": stake, "model_p": model_p,
        "ev_claimed": round(model_p * amer_to_dec_scalar(price) - 1, 4),
        "status": "open", "result": "", "actual": "", "clv": "", "clv_cal": "",
        "clv_source": "", "pnl": "", "notes": "; ".join(notes),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fills", nargs="+", help="pick key or unique substring")
    ap.add_argument("--stake", type=float,
                    help="stake per fill (default: the sheet's Kelly stake)")
    ap.add_argument("--price", type=int,
                    help="American price taken, one fill only "
                         "(default: the sheet's FanDuel price)")
    ap.add_argument("--note", default="", help="appended to every row's notes")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rows, write nothing")
    a = ap.parse_args()
    if a.price is not None and len(a.fills) > 1:
        raise SystemExit("--price applies to a single fill; log the others "
                         "separately so each price is the one actually taken.")

    picks, bets = load(PICKS), load(BETS)
    now = datetime.now(ET)
    placed_at, today = now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d")

    open_by_pg = {}
    for b in bets:
        if b["status"] == "open":
            open_by_pg.setdefault((b["event_id"], b["player"]), []).append(b)

    logged, rows = {b["key"] for b in bets}, []
    for arg in a.fills:
        pick = resolve(arg, picks)
        if pick["key"] in logged:
            raise SystemExit(f"{pick['key']} is already on bets.csv - refusing "
                             f"to double-log. Nothing was written.")
        price = a.price if a.price is not None else int(float(pick["fd_cost"]))
        stake = a.stake if a.stake is not None else float(pick["stake"])
        row = build_row(pick, price, stake, placed_at, today,
                        open_by_pg.get((pick["event_id"], pick["player"]), []),
                        a.note)
        rows.append(row)
        logged.add(row["key"])
        # a second fill on the same player-game must warn the same way
        open_by_pg.setdefault((pick["event_id"], pick["player"]), []).append(
            {"market": row["market"], "side": row["side"], "line": row["line"]})
        floor = pick.get("min_odds_3pct")
        if floor and price < int(float(floor)):
            print(f"WARNING {row['key']}: {price:+d} is worse than the "
                  f"EV>=3% floor of {int(float(floor)):+d} - logged anyway "
                  f"(user's call), but the claimed edge is thin.")

    for r in rows:
        print(f"  {r['player']:<22} {r['market']:<9} {r['side']:<5} "
              f"{float(r['line']):>5} {int(r['odds_taken']):>+5} "
              f"${r['stake']:>5.2f}  ev_claimed {r['ev_claimed']:+.1%}")
    if a.dry_run:
        print("NOTHING_LOGGED (dry run)")
        return
    with open(BETS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        for r in rows:
            w.writerow(r)
    refresh_site()
    print(f"LOGGED {len(rows)}")


if __name__ == "__main__":
    main()
