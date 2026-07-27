"""Fetch NBA game results from the ESPN scoreboard API.

One request per calendar month keeps this to ~140 calls for a decade of games.
Raw JSON is cached to disk so re-runs are free.
"""
import json
import os
import sys
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "cache", "scoreboard")
OUT = os.path.join(ROOT, "data", "raw", "games.csv")

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# NBA seasons run Oct->Jun; ESPN labels a season by its ending year.
# Cover 2014-15 through 2025-26.
MONTHS = [(y, m) for y in range(2014, 2027) for m in range(1, 13)]
MONTHS = [(y, m) for (y, m) in MONTHS if not (y == 2014 and m < 10)]


def month_range(y, m):
    start = f"{y}{m:02d}01"
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    end_day = (pd.Timestamp(ny, nm, 1) - pd.Timedelta(days=1)).day
    return f"{start}-{y}{m:02d}{end_day:02d}"


def fetch_month(y, m, session):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{y}{m:02d}.json")
    if os.path.exists(path) and os.path.getsize(path) > 200:
        with open(path) as f:
            return json.load(f)
    params = {"dates": month_range(y, m), "limit": 1000}
    for attempt in range(4):
        try:
            r = session.get(BASE, params=params, headers=HEADERS, timeout=45)
            if r.status_code == 200:
                data = r.json()
                with open(path, "w") as f:
                    json.dump(data, f)
                return data
        except Exception as exc:  # noqa: BLE001 - network flakiness
            print(f"  retry {y}-{m:02d}: {exc}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return {"events": []}


def parse(data):
    rows = []
    for e in data.get("events", []):
        comps = e.get("competitions") or []
        if not comps:
            continue
        c = comps[0]
        status = c.get("status", {}).get("type", {}).get("name")
        if status != "STATUS_FINAL":
            continue
        teams = c.get("competitors") or []
        if len(teams) != 2:
            continue
        home = next((t for t in teams if t.get("homeAway") == "home"), None)
        away = next((t for t in teams if t.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        try:
            hs, as_ = int(home["score"]), int(away["score"])
        except (KeyError, TypeError, ValueError):
            continue
        season = e.get("season") or {}
        rows.append(
            {
                "game_id": e["id"],
                "date_utc": e["date"],
                "season_year": season.get("year"),
                "season_type": season.get("type"),
                "neutral": bool(c.get("neutralSite", False)),
                "home_id": home["team"]["id"],
                "home_abbr": home["team"].get("abbreviation"),
                "away_id": away["team"]["id"],
                "away_abbr": away["team"].get("abbreviation"),
                "home_score": hs,
                "away_score": as_,
            }
        )
    return rows


def main():
    session = requests.Session()
    all_rows = []
    for i, (y, m) in enumerate(MONTHS):
        data = fetch_month(y, m, session)
        rows = parse(data)
        all_rows.extend(rows)
        if rows:
            print(f"{y}-{m:02d}: {len(rows)} final games")
        if i % 10 == 0:
            time.sleep(0.3)

    df = pd.DataFrame(all_rows).drop_duplicates(subset="game_id")
    df["date_utc"] = pd.to_datetime(df["date_utc"], utc=True)
    # Game "day" in US Eastern determines rest/schedule logic.
    df["game_date"] = df["date_utc"].dt.tz_convert("US/Eastern").dt.date
    df = df.sort_values("date_utc").reset_index(drop=True)
    df.to_csv(OUT, index=False)
    print(f"\nwrote {len(df)} games -> {OUT}")
    print(df.groupby(["season_year", "season_type"]).size().to_string())


if __name__ == "__main__":
    main()
