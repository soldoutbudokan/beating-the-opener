"""Download NBA player box scores + schedules from sportsdataverse/hoopR-nba-data.

Same pattern as wnba/src/fetch_wehoop.py. Year labels are season-END years
(2026 = the 2025-26 season). Not committed (gitignored) - refetchable.

Usage: python3 src/fetch_nba.py
"""
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "nba")
BASE = ("https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data"
        "/main/nba")
YEARS = range(2024, 2027)  # 2023-24 .. 2025-26 (panel warmup + graded season)

FILES = (
    [f"player_box/parquet/player_box_{y}.parquet" for y in YEARS]
    + [f"team_box/parquet/team_box_{y}.parquet" for y in YEARS]
    + [f"schedules/parquet/nba_schedule_{y}.parquet" for y in YEARS]
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
        print(f"NBA_FETCH_FAILED: {n_fail}/{len(FILES)} downloads failed",
              flush=True)
        raise SystemExit(1)
    print("NBA_FETCH_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
