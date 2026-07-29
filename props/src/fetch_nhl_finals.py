"""Official NHL final scores (incl. SO winner's +1 goal) for game grading.

api-web /v1/score/{date} per calendar day — the official final is the
settlement convention for ML/PL/totals (OT counts; a shootout adds exactly
one goal to the winner). lastPeriodType is kept so SO games can be checked
explicitly against BP's scores in the dual-source validation.

Output: data/nhl/finals_2025.parquet

Usage: python3 src/fetch_nhl_finals.py
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
START, END = "2025-09-15", "2026-06-30"


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def day(date):
    d = get(f"https://api-web.nhle.com/v1/score/{date}")
    rows = []
    for g in d.get("games", []):
        if g.get("gameState") not in ("OFF", "FINAL"):
            continue
        rows.append({
            "game_id": g["id"],
            "date": g.get("gameDate", date),
            "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"],
            "home_score": g["homeTeam"].get("score"),
            "away_score": g["awayTeam"].get("score"),
            "last_period": (g.get("gameOutcome") or {}).get("lastPeriodType"),
        })
    return rows


def main():
    dates = [str(d.date()) for d in pd.date_range(START, END)]
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for chunk in ex.map(day, dates):
            rows.extend(chunk)
    df = pd.DataFrame(rows).drop_duplicates("game_id", keep="last")
    out = os.path.join(ROOT, "data", "nhl", "finals_2025.parquet")
    df.to_parquet(out)
    print(f"{len(df)} finals ({df.date.min()} .. {df.date.max()}), "
          f"by last period:\n{df.last_period.value_counts().to_string()}")


if __name__ == "__main__":
    main()
