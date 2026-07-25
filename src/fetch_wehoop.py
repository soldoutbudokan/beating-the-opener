"""Download WNBA box scores + schedules from sportsdataverse/wehoop-wnba-data.

Player box scores 2003-present: minutes, pts/reb/ast/stl/blk/TO/3PM per game.
Not committed (gitignored) - refetchable any time.

Usage: python3 src/fetch_wehoop.py
"""
import os
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "wehoop")
BASE = ("https://github.com/sportsdataverse/wehoop-wnba-data/raw/main/wnba")

FILES = (
    [f"player_box/parquet/player_box_{y}.parquet" for y in range(2003, 2027)]
    + [f"team_box/parquet/team_box_{y}.parquet" for y in range(2003, 2027)]
    + [f"schedules/parquet/wnba_schedule_{y}.parquet" for y in range(2003, 2027)]
)


def main():
    os.makedirs(OUT, exist_ok=True)
    for rel in FILES:
        dest = os.path.join(OUT, os.path.basename(rel))
        # current season file changes daily - always refetch it
        if os.path.exists(dest) and "2026" not in rel:
            continue
        url = f"{BASE}/{rel}"
        try:
            urllib.request.urlretrieve(url, dest)
            print(f"ok  {os.path.basename(rel)} "
                  f"({os.path.getsize(dest)//1024}KB)", flush=True)
        except Exception as e:
            print(f"FAIL {rel}: {e}", flush=True)
            if os.path.exists(dest):
                os.remove(dest)
        time.sleep(0.2)
    print("WEHOOP_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
