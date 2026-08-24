"""Per-player-game shot attempts from api-web pbp (registration N2 data).

Aggregates in-flight (raw pbp payloads are not retained) into
data/nhl_pbp/attempts_<season>.parquet, one row per (game_id, pid):
  goals_pbp   goal events (shooter = scoringPlayerId)
  sog_ev      shot-on-goal events (exclude goals by construction)
  miss        missed-shot events
  blocked_att blocked-shot events as the SHOOTER
  blk_pbp     blocked-shot events as the BLOCKER
  attempts = goals_pbp + sog_ev + miss + blocked_att
  sog_pbp  = goals_pbp + sog_ev   (QC identity vs boxscore sog)

Shootout events are excluded (periodType SO) — the boxscore sog the QC
reconciles against excludes them too. Seasons 2010..2025 (training era
schedules from data/nhl_hist/, eval era from data/nhl/). Resumable.

Usage: python3 src/fetch_nhl_pbp.py [--season 2010]
"""
import argparse
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import fetch_nhl as fn

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "nhl_pbp")
SEASONS = list(range(2010, 2026))


def sched_path(season):
    sub = "nhl_hist" if season <= 2023 else "nhl"
    return os.path.join(ROOT, "data", sub, f"schedule_{season}.parquet")


def parse_pbp(gid, game_date, d):
    counts = Counter()
    for p in d.get("plays", []):
        if (p.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        k = p.get("typeDescKey")
        det = p.get("details") or {}
        if k == "goal" and det.get("scoringPlayerId"):
            counts[(det["scoringPlayerId"], "goals_pbp")] += 1
        elif k == "shot-on-goal" and det.get("shootingPlayerId"):
            counts[(det["shootingPlayerId"], "sog_ev")] += 1
        elif k == "missed-shot" and det.get("shootingPlayerId"):
            counts[(det["shootingPlayerId"], "miss")] += 1
        elif k == "blocked-shot":
            if det.get("shootingPlayerId"):
                counts[(det["shootingPlayerId"], "blocked_att")] += 1
            if det.get("blockingPlayerId"):
                counts[(det["blockingPlayerId"], "blk_pbp")] += 1
    rows = {}
    for (pid, col), v in counts.items():
        rows.setdefault(pid, {"game_id": gid, "date": game_date, "pid": pid,
                              "goals_pbp": 0, "sog_ev": 0, "miss": 0,
                              "blocked_att": 0, "blk_pbp": 0})[col] = v
    out = list(rows.values())
    for r in out:
        r["attempts"] = (r["goals_pbp"] + r["sog_ev"] + r["miss"]
                         + r["blocked_att"])
        r["sog_pbp"] = r["goals_pbp"] + r["sog_ev"]
    return out


def fetch_season(season):
    sched = pd.read_parquet(sched_path(season))
    path = os.path.join(OUT, f"attempts_{season}.parquet")
    old = pd.read_parquet(path) if os.path.exists(path) else pd.DataFrame()
    done = set(old.game_id.unique()) if len(old) else set()
    rows = [r for _, r in sched.iterrows()
            if r["state"] in ("OFF", "FINAL") and r["game_id"] not in done]
    got, fails = [], [0]

    def one(row):
        d = fn.get(f"{fn.API}/gamecenter/{row['game_id']}/play-by-play")
        if d is None:
            fails[0] += 1
            print(f"FAIL pbp {row['game_id']}", flush=True)
            return
        got.extend(parse_pbp(row["game_id"], row["gameDate"], d))
        time.sleep(fn.PAUSE)

    with ThreadPoolExecutor(max_workers=fn.WORKERS) as ex:
        list(ex.map(one, rows))
    new = pd.concat([old, pd.DataFrame(got)], ignore_index=True)
    if len(old) and len(new) < len(old):
        raise RuntimeError(f"attempts_{season} would shrink - refusing")
    new.to_parquet(path)
    n_final = sched.state.isin(["OFF", "FINAL"]).sum()
    print(f"{season}: +{len(rows) - fails[0]} games ({fails[0]} failed) -> "
          f"{new.game_id.nunique()}/{n_final} games, {len(new)} rows",
          flush=True)
    return fails[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    seasons = [args.season] if args.season else SEASONS
    total_fails = 0
    for s in seasons:
        total_fails += fetch_season(s)
    print("NHL_PBP_COMPLETE" if total_fails == 0
          else f"NHL_PBP_PARTIAL ({total_fails} fails)", flush=True)


if __name__ == "__main__":
    main()
