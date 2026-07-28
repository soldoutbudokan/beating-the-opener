"""Download NFL weekly player stats (nflverse) + game schedule (nfldata).

stats_player_week_<yyyy>.parquet: per player-game passing/rushing/receiving
lines. games.csv (Lee Sharpe's nfldata): season/week/gameday/teams - the
week-to-date crosswalk grade_props needs. Not committed - refetchable.

Usage: python3 src/fetch_nfl.py
"""
import os
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "nfl")
YEARS = range(2023, 2026)  # 2025 = the season the odds archive covers

URLS = [
    (f"https://github.com/nflverse/nflverse-data/releases/download/"
     f"stats_player/stats_player_week_{y}.parquet",
     f"stats_player_week_{y}.parquet")
    for y in YEARS
] + [
    ("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
     "games.csv"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    fails = 0
    for url, name in URLS:
        dest = os.path.join(OUT, name)
        # 2025 file and games.csv can change (late corrections) - refetch
        if os.path.exists(dest) and "2025" not in name and name != "games.csv":
            continue
        try:
            urllib.request.urlretrieve(url, dest)
            print(f"ok  {name} ({os.path.getsize(dest)//1024}KB)", flush=True)
        except Exception as e:
            print(f"FAIL {name}: {e}", flush=True)
            fails += 1
            if os.path.exists(dest):
                os.remove(dest)
    if fails:
        print(f"NFL_FETCH_FAILED: {fails} downloads failed", flush=True)
        raise SystemExit(1)
    print("NFL_FETCH_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
