"""Fetch NHL schedules + boxscores from api-web.nhle.com (free, no key).

Outputs (data/nhl/, gitignored):
  schedule_<season>.parquet   one row per game (id, UTC start, teams, state)
  skater_box_<season>.parquet goals/assists/points/sog/blocked per skater-game
  goalie_box_<season>.parquet saves per goalie-game
  players_<season>.parquet    playerId -> full name (rosters; boxscore names
                              are abbreviated like "S. Bennett")

Season label is the start year (2025 = 2025-26).

Usage: python3 src/fetch_nhl.py [--season 2025]
Idempotent per game: existing game ids are kept, only missing finals fetched.
"""
import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "nhl")
API = "https://api-web.nhle.com/v1"
PAUSE = 0.15
WORKERS = 8
# regular season + playoffs; gameType 1 = preseason (excluded)
KEEP_TYPES = {2, 3}
SEASON_SPAN = {2025: ("2025-10-01", "2026-06-30")}
TEAMS = ["ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET",
         "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT",
         "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
         "WPG", "WSH"]


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            wait = 2 ** (i + 1)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def fetch_schedule(season):
    """Walk the season by week (the /schedule/<date> endpoint returns a week)."""
    start, end = SEASON_SPAN[season]
    rows, seen = [], set()
    d = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    while d <= d1:
        payload = get(f"{API}/schedule/{d}")
        if payload is None:
            print(f"FAIL schedule week {d}", flush=True)
            d += timedelta(days=7)
            continue
        for day in payload.get("gameWeek", []):
            for g in day.get("games", []):
                if g["id"] in seen or g.get("gameType") not in KEEP_TYPES:
                    continue
                seen.add(g["id"])
                rows.append({
                    "game_id": g["id"], "gameType": g["gameType"],
                    "startTimeUTC": g.get("startTimeUTC"),
                    "gameDate": day.get("date"),
                    "home": g["homeTeam"].get("abbrev"),
                    "away": g["awayTeam"].get("abbrev"),
                    "state": g.get("gameState"),
                })
        d += timedelta(days=7)
        time.sleep(PAUSE)
    df = pd.DataFrame(rows).drop_duplicates("game_id")
    print(f"{season}: {len(df)} scheduled games "
          f"({(df.state.isin(['OFF', 'FINAL'])).sum()} final)", flush=True)
    return df


def fetch_rosters(season):
    rows = []
    sid = f"{season}{season + 1}"
    for t in TEAMS:
        d = get(f"{API}/roster/{t}/{sid}", tries=2)
        if d is None:
            continue
        for group in ("forwards", "defensemen", "goalies"):
            for p in d.get(group, []):
                rows.append({
                    "pid": p.get("id"),
                    "first": (p.get("firstName") or {}).get("default"),
                    "last": (p.get("lastName") or {}).get("default"),
                })
        time.sleep(PAUSE)
    return pd.DataFrame(rows).drop_duplicates("pid")


def parse_box(gid, d, game_date):
    srows, grows = [], []
    pbs = d.get("playerByGameStats") or {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        team = (d.get(key) or {}).get("abbrev")
        opp = (d.get("awayTeam" if side == "home" else "homeTeam") or {}).get("abbrev")
        tstats = pbs.get(key) or {}
        for group in ("forwards", "defense"):
            for p in tstats.get(group) or []:
                srows.append({
                    "game_id": gid, "date": game_date, "team": team,
                    "opp": opp, "home": side == "home",
                    "pid": p.get("playerId"),
                    "name_abbr": (p.get("name") or {}).get("default"),
                    "pos": p.get("position"),
                    "goals": p.get("goals"), "assists": p.get("assists"),
                    "points": p.get("points"), "sog": p.get("sog"),
                    "blocked": p.get("blockedShots"), "toi": p.get("toi"),
                })
        for p in tstats.get("goalies") or []:
            grows.append({
                "game_id": gid, "date": game_date, "team": team, "opp": opp,
                "home": side == "home", "pid": p.get("playerId"),
                "name_abbr": (p.get("name") or {}).get("default"),
                "saves": p.get("saves"), "shots_against": p.get("shotsAgainst"),
                "starter": p.get("starter"), "toi": p.get("toi"),
            })
    return srows, grows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    args = ap.parse_args()
    season = args.season
    if get(f"{API}/schedule/{SEASON_SPAN[season][0]}", tries=2) is None:
        print("FETCH_ABORTED: api-web.nhle.com unreachable", flush=True)
        raise SystemExit(1)
    os.makedirs(OUT, exist_ok=True)

    sched = fetch_schedule(season)
    sched.to_parquet(os.path.join(OUT, f"schedule_{season}.parquet"))

    roster_path = os.path.join(OUT, f"players_{season}.parquet")
    if not os.path.exists(roster_path):
        players = fetch_rosters(season)
        players.to_parquet(roster_path)
        print(f"rosters: {len(players)} players", flush=True)

    spath = os.path.join(OUT, f"skater_box_{season}.parquet")
    gpath = os.path.join(OUT, f"goalie_box_{season}.parquet")
    old_s = pd.read_parquet(spath) if os.path.exists(spath) else pd.DataFrame()
    old_g = pd.read_parquet(gpath) if os.path.exists(gpath) else pd.DataFrame()
    have = set(old_s["game_id"].unique()) if len(old_s) else set()

    finals = sched[sched.state.isin(["OFF", "FINAL"]) & ~sched.game_id.isin(have)]
    rows = list(finals.to_dict("records"))
    got_s, got_g, fails = [], [], [0]

    def one(row):
        d = get(f"{API}/gamecenter/{row['game_id']}/boxscore")
        if d is None:
            fails[0] += 1
            print(f"FAIL boxscore {row['game_id']}", flush=True)
            return
        sr, gr = parse_box(row["game_id"], d, row["gameDate"])
        got_s.extend(sr)
        got_g.extend(gr)
        time.sleep(PAUSE)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, rows))

    new_s = pd.concat([old_s, pd.DataFrame(got_s)], ignore_index=True)
    new_g = pd.concat([old_g, pd.DataFrame(got_g)], ignore_index=True)
    if len(old_s) and len(new_s) < len(old_s):
        raise RuntimeError(f"skater_box_{season} would shrink - refusing")
    new_s.to_parquet(spath)
    new_g.to_parquet(gpath)
    print(f"{season}: +{len(rows) - fails[0]} games ({fails[0]} failed) -> "
          f"{new_s.game_id.nunique()} games, {len(new_s)} skater rows, "
          f"{len(new_g)} goalie rows", flush=True)
    print("NHL_FETCH_COMPLETE" if fails[0] == 0
          else f"NHL_FETCH_PARTIAL ({fails[0]} fails)", flush=True)


if __name__ == "__main__":
    main()
