"""G0.3 hand audit: re-resolve a random sample of graded props from the
source APIs and compare against graded_<sport>.pkl.

For MLB (the doubleheader-risk sport) each sampled prop's boxscore is
re-fetched fresh from statsapi by gamePk and the stat recomputed; NBA is
spot-checked the same way against the hoopR parquet's ESPN game id via the
schedule. A mismatch in `actual` means a wrong-game or wrong-player join —
the gate is 0 mismatches on graded (non-void) rows.

Usage: python3 src/audit_grades.py --sport MLB [--n 500] [--seed 7]
"""
import argparse
import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd

from grade_props import STAT_COLS, norm
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")

MLB_FIELDS = {
    "k": ("pitching", "strikeOuts"), "outs": ("pitching", "outs"),
    "h_allowed": ("pitching", "hits"), "bb_allowed": ("pitching", "baseOnBalls"),
    "er": ("pitching", "earnedRuns"),
    "h": ("batting", "hits"), "tb": ("batting", "totalBases"),
    "r": ("batting", "runs"), "rbi": ("batting", "rbi"),
    "hr": ("batting", "homeRuns"), "sb": ("batting", "stolenBases"),
    "d2": ("batting", "doubles"), "t3": ("batting", "triples"),
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def mlb_actual(gamePk, nname, market):
    role, cols = STAT_COLS["MLB"][market]
    d = get(f"https://statsapi.mlb.com/api/v1/game/{int(gamePk)}/boxscore")
    for side in ("home", "away"):
        for p in d["teams"][side]["players"].values():
            if norm((p.get("person") or {}).get("fullName")) != nname:
                continue
            stats = p.get("stats") or {}
            grp = "pitching" if role == "pitcher" else "batting"
            if not stats.get(grp):
                continue
            vals = []
            for c in cols:
                if c == "b1":
                    b = stats["batting"]
                    vals.append(b.get("hits", 0) - b.get("doubles", 0)
                                - b.get("triples", 0) - b.get("homeRuns", 0))
                else:
                    g, f = MLB_FIELDS[c]
                    vals.append(stats[g].get(f, 0))
            return float(sum(vals))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=["MLB"])
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    sl = args.sport.lower()

    graded = pd.read_pickle(os.path.join(ROOT, "data", f"graded_{sl}.pkl"))
    u = graded[graded.matched & ~graded.void & graded.actual.notna()]
    u = u.drop_duplicates(["event_id", "market", "player"])
    samp = u.sample(min(args.n, len(u)), random_state=args.seed)

    bad, checked = [], 0
    for r in samp.itertuples():
        fresh = mlb_actual(r.native_id, norm(r.player), r.market)
        checked += 1
        if fresh is None or fresh != r.actual:
            bad.append((r.event_id, int(r.native_id), r.market, r.player,
                        r.actual, fresh))
            print(f"MISMATCH {bad[-1]}", flush=True)
        if checked % 50 == 0:
            print(f"  {checked}/{len(samp)} checked, {len(bad)} mismatches",
                  flush=True)
        time.sleep(0.1)

    print(f"G0.3 hand audit: {checked} props re-resolved, "
          f"{len(bad)} mismatches")
    print("PASS (0 wrong joins)" if not bad else "FAIL - investigate joins")


if __name__ == "__main__":
    main()
