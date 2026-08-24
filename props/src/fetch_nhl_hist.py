"""Training-era NHL skater boxscores from api-web (registration N data).

Writes data/nhl_hist/ (gitignored, regenerable):
  schedule_<season>.parquet    one row per game (types 2+3)
  skater_box_<season>.parquet  same schema as fetch_nhl.py's skater_box

Seasons 2010..2023 (2010 = 2010-11). Kept separate from data/nhl/ per the
registration-N isolation rule: the eval fetch, panel, modelset and benchmark
are untouched by this data. Goalies/rosters not fetched (out of scope —
the talent module keys on pid, no name map needed; saves stays a control).

Usage: python3 src/fetch_nhl_hist.py [--season 2010] (default: all missing)
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import fetch_nhl as fn

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "nhl_hist")

# regular season + playoffs; COVID seasons have shifted calendars
SPANS = {s: (f"{s}-10-01", f"{s + 1}-06-30")
         for s in range(2010, 2024)}
SPANS[2012] = ("2013-01-01", "2013-06-30")   # lockout: games began 2013-01-19
SPANS[2019] = ("2019-10-01", "2020-10-15")   # COVID pause; bubble playoffs
SPANS[2020] = ("2020-12-15", "2021-07-31")   # 56-game season, Jul 7 final


def fetch_season(season):
    fn.SEASON_SPAN[season] = SPANS[season]
    spath = os.path.join(OUT, f"skater_box_{season}.parquet")
    schpath = os.path.join(OUT, f"schedule_{season}.parquet")
    if os.path.exists(schpath):
        sched = pd.read_parquet(schpath)
    else:
        sched = fn.fetch_schedule(season)
        sched.to_parquet(schpath)
    old = pd.read_parquet(spath) if os.path.exists(spath) else pd.DataFrame()
    done = set(old.game_id.unique()) if len(old) else set()
    rows = [r for _, r in sched.iterrows()
            if r["state"] in ("OFF", "FINAL") and r["game_id"] not in done]
    got, fails = [], [0]

    def one(row):
        d = fn.get(f"{fn.API}/gamecenter/{row['game_id']}/boxscore")
        if d is None:
            fails[0] += 1
            print(f"FAIL boxscore {row['game_id']}", flush=True)
            return
        sr, _ = fn.parse_box(row["game_id"], d, row["gameDate"])
        got.extend(sr)
        time.sleep(fn.PAUSE)

    with ThreadPoolExecutor(max_workers=fn.WORKERS) as ex:
        list(ex.map(one, rows))
    new = pd.concat([old, pd.DataFrame(got)], ignore_index=True)
    if len(old) and len(new) < len(old):
        raise RuntimeError(f"skater_box_{season} would shrink - refusing")
    new.to_parquet(spath)
    print(f"{season}: +{len(rows) - fails[0]} games ({fails[0]} failed) -> "
          f"{new.game_id.nunique()}/{len(sched)} games, {len(new)} skater rows",
          flush=True)
    return fails[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    seasons = [args.season] if args.season else sorted(SPANS)
    total_fails = 0
    for s in seasons:
        total_fails += fetch_season(s)
    print("NHL_HIST_COMPLETE" if total_fails == 0
          else f"NHL_HIST_PARTIAL ({total_fails} fails)", flush=True)


if __name__ == "__main__":
    main()
