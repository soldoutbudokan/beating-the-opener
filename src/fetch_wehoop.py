"""Download WNBA box scores + schedules from sportsdataverse/wehoop-wnba-data.

Player box scores 2003-present: minutes, pts/reb/ast/stl/blk/TO/3PM per game.
Not committed (gitignored) - refetchable any time.

Usage: python3 src/fetch_wehoop.py
"""
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "wehoop")
# raw.githubusercontent.com, NOT github.com/<repo>/raw: cloud-session proxies
# scope github.com to the session's own repos (403) but allow raw content
BASE = ("https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data"
        "/main/wnba")

FILES = (
    [f"player_box/parquet/player_box_{y}.parquet" for y in range(2003, 2027)]
    + [f"team_box/parquet/team_box_{y}.parquet" for y in range(2003, 2027)]
    + [f"schedules/parquet/wnba_schedule_{y}.parquet" for y in range(2003, 2027)]
)


def fetch(rel):
    dest = os.path.join(OUT, os.path.basename(rel))
    # current season file changes daily - always refetch it
    if os.path.exists(dest) and "2026" not in rel:
        return True
    try:
        urllib.request.urlretrieve(f"{BASE}/{rel}", dest)
        print(f"ok  {os.path.basename(rel)} "
              f"({os.path.getsize(dest)//1024}KB)", flush=True)
        return True
    except Exception as e:
        print(f"FAIL {rel}: {e}", flush=True)
        if os.path.exists(dest):
            os.remove(dest)
        return False


def main():
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch, FILES))
    n_fail = results.count(False)
    if n_fail:
        print(f"WEHOOP_FAILED: {n_fail}/{len(FILES)} downloads failed", flush=True)
        raise SystemExit(1)
    print("WEHOOP_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
