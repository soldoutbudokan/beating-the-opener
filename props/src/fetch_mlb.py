"""Fetch MLB schedules + boxscores from the MLB StatsAPI (free, no key).

Outputs (data/mlb/, gitignored, regenerable):
  schedule_<yyyy>.parquet     one row per game (gamePk, UTC time, DH flags, teams)
  pitcher_box_<yyyy>.parquet  one row per pitcher-appearance
  batter_box_<yyyy>.parquet   one row per batter-appearance

Verified 2026-07-28: boxscore pitching carries `outs` directly (no IP-string
parsing) and batting carries `totalBases`; schedule carries doubleHeader
(N/Y/S) + gameNumber with distinct gamePk and gameDate per DH game.

Spring training / exhibitions / All-Star (gameType S, E, A) are excluded;
regular season R and postseason F/D/L/W are kept (season_type flag).

Usage: python3 src/fetch_mlb.py [--seasons 2025 2026]
Idempotent per game: existing gamePks are kept, only missing finals fetched.
"""
import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "mlb")
API = "https://statsapi.mlb.com/api/v1"
KEEP_TYPES = {"R", "F", "D", "L", "W"}  # regular + postseason rounds
PAUSE = 0.15
WORKERS = 8


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retry any transport error
            wait = 2 ** (i + 1)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def team_abbrs(season):
    d = get(f"{API}/teams?sportId=1&season={season}")
    return {t["id"]: t.get("abbreviation") for t in d.get("teams", [])}


def fetch_schedule(season, abbr):
    """Day-by-day: the season-range query serves stale gameDate/status for
    rescheduled games (verified 2026-07-28: makeup-day appearance said
    Postponed + original May time; the single-day query says Final + real
    time). Day queries are authoritative."""
    days = pd.date_range(f"{season}-03-01", f"{season}-11-30").strftime("%Y-%m-%d")

    def one_day(day):
        d = get(f"{API}/schedule?sportId=1&startDate={day}&endDate={day}")
        time.sleep(PAUSE)
        return d.get("dates", []) if d else []

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        day_payloads = list(ex.map(one_day, days))
    for dates in day_payloads:
        for dt in dates:
            for g in dt["games"]:
                if g.get("gameType") not in KEEP_TYPES:
                    continue
                rows.append({
                    "gamePk": g["gamePk"], "gameDate": g["gameDate"],
                    "officialDate": g["officialDate"],
                    "doubleHeader": g.get("doubleHeader"),
                    "gameNumber": g.get("gameNumber"),
                    "gameType": g.get("gameType"),
                    "status": g["status"].get("detailedState"),
                    "home_id": g["teams"]["home"]["team"]["id"],
                    "away_id": g["teams"]["away"]["team"]["id"],
                    "home": abbr.get(g["teams"]["home"]["team"]["id"]),
                    "away": abbr.get(g["teams"]["away"]["team"]["id"]),
                    "venue_id": (g.get("venue") or {}).get("id"),
                })
    # postponed games appear twice under one gamePk; BOTH appearances can
    # carry the makeup officialDate (verified: original-day row keeps the
    # stale gameDate + "Postponed", makeup-day row is Final) - prefer the
    # played row explicitly, then the latest officialDate
    df = pd.DataFrame(rows)
    df["_pri"] = (df.status == "Final").astype(int)
    df = (df.sort_values(["officialDate", "_pri"], kind="mergesort")
          .drop_duplicates("gamePk", keep="last")
          .drop(columns="_pri").reset_index(drop=True))
    print(f"{season}: {len(df)} scheduled games "
          f"({(df.status == 'Final').sum()} final)", flush=True)
    return df


def parse_box(gamePk, d, sched_row):
    prows, brows = [], []
    for side in ("home", "away"):
        team = d["teams"][side]
        t_abbr = team["team"].get("abbreviation")
        o_abbr = d["teams"]["away" if side == "home" else "home"]["team"].get(
            "abbreviation")
        for p in team["players"].values():
            person = p.get("person") or {}
            base = {
                "gamePk": gamePk, "date": sched_row["officialDate"],
                "team": t_abbr, "opp": o_abbr, "home": side == "home",
                "pid": person.get("id"), "name": person.get("fullName"),
            }
            pit = (p.get("stats") or {}).get("pitching") or {}
            if pit.get("battersFaced", 0) > 0:
                prows.append({
                    **base,
                    "started": pit.get("gamesStarted", 0) == 1,
                    "outs": pit.get("outs"), "k": pit.get("strikeOuts"),
                    "h_allowed": pit.get("hits"),
                    "bb_allowed": pit.get("baseOnBalls"),
                    "er": pit.get("earnedRuns"), "bf": pit.get("battersFaced"),
                    "pitches": pit.get("numberOfPitches"),
                })
            bat = (p.get("stats") or {}).get("batting") or {}
            if bat.get("plateAppearances", 0) > 0:
                brows.append({
                    **base, "order": p.get("battingOrder"),
                    "pa": bat.get("plateAppearances"), "ab": bat.get("atBats"),
                    "h": bat.get("hits"), "d2": bat.get("doubles"),
                    "t3": bat.get("triples"), "hr": bat.get("homeRuns"),
                    "r": bat.get("runs"), "rbi": bat.get("rbi"),
                    "bb": bat.get("baseOnBalls"), "so": bat.get("strikeOuts"),
                    "tb": bat.get("totalBases"), "sb": bat.get("stolenBases"),
                })
    return prows, brows


def fetch_season(season):
    os.makedirs(OUT, exist_ok=True)
    abbr = team_abbrs(season)
    sched = fetch_schedule(season, abbr)
    sched.to_parquet(os.path.join(OUT, f"schedule_{season}.parquet"))

    ppath = os.path.join(OUT, f"pitcher_box_{season}.parquet")
    bpath = os.path.join(OUT, f"batter_box_{season}.parquet")
    old_p = pd.read_parquet(ppath) if os.path.exists(ppath) else pd.DataFrame()
    old_b = pd.read_parquet(bpath) if os.path.exists(bpath) else pd.DataFrame()
    have = set(old_p["gamePk"].unique()) if len(old_p) else set()

    finals = sched[(sched.status == "Final") & ~sched.gamePk.isin(have)]
    rows = list(finals.to_dict("records"))
    got_p, got_b, fails = [], [], [0]

    def one(row):
        d = get(f"{API}/game/{row['gamePk']}/boxscore")
        if d is None:
            fails[0] += 1
            print(f"FAIL boxscore {row['gamePk']}", flush=True)
            return
        pr, br = parse_box(row["gamePk"], d, row)
        got_p.extend(pr)
        got_b.extend(br)
        if (len(got_p) // 20) % 25 == 0:
            pass
        time.sleep(PAUSE)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, rows))

    new_p = pd.concat([old_p, pd.DataFrame(got_p)], ignore_index=True)
    new_b = pd.concat([old_b, pd.DataFrame(got_b)], ignore_index=True)
    if len(old_p) and len(new_p) < len(old_p):
        raise RuntimeError(f"pitcher_box_{season} would shrink - refusing")
    new_p.to_parquet(ppath)
    new_b.to_parquet(bpath)
    print(f"{season}: +{len(rows) - fails[0]} games fetched "
          f"({fails[0]} failed) -> {new_p.gamePk.nunique()} games, "
          f"{len(new_p)} pitcher rows, {len(new_b)} batter rows", flush=True)
    return fails[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+", default=[2025, 2026])
    args = ap.parse_args()
    # canary: API reachable before touching anything
    if get(f"{API}/teams?sportId=1", tries=2) is None:
        print("FETCH_ABORTED: statsapi.mlb.com unreachable", flush=True)
        raise SystemExit(1)
    total_fails = 0
    for season in args.seasons:
        total_fails += fetch_season(season)
    print("STATSAPI_COMPLETE" if total_fails == 0
          else f"STATSAPI_PARTIAL ({total_fails} fails)", flush=True)


if __name__ == "__main__":
    main()
