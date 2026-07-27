"""Fetch team + player box scores from the ESPN summary endpoint.

Payloads are ~350KB each, so we parse in flight and persist only compact records.
Two resumable JSONL outputs: team-level stats (for possession-based efficiency)
and player-level lines (for availability / talent-on-floor features).
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES = os.path.join(ROOT, "data", "raw", "games.csv")
TEAM_OUT = os.path.join(ROOT, "data", "raw", "team_box.jsonl")
PLAYER_OUT = os.path.join(ROOT, "data", "raw", "player_box.jsonl")

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_lock = threading.Lock()


def _split(val):
    """'6-16' -> (6, 16)."""
    try:
        a, b = str(val).split("-")
        return float(a), float(b)
    except Exception:  # noqa: BLE001
        return None, None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(eid, d):
    bs = d.get("boxscore") or {}
    teams, players = [], []

    for t in bs.get("teams", []) or []:
        abbr = (t.get("team") or {}).get("abbreviation")
        stats = {s.get("name"): s.get("displayValue") for s in (t.get("statistics") or [])}
        fgm, fga = _split(stats.get("fieldGoalsMade-fieldGoalsAttempted"))
        ftm, fta = _split(stats.get("freeThrowsMade-freeThrowsAttempted"))
        tpm, tpa = _split(stats.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        teams.append({
            "game_id": eid, "team": abbr, "home_away": t.get("homeAway"),
            "fgm": fgm, "fga": fga, "ftm": ftm, "fta": fta, "tpm": tpm, "tpa": tpa,
            "oreb": _f(stats.get("offensiveRebounds")),
            "dreb": _f(stats.get("defensiveRebounds")),
            "reb": _f(stats.get("totalRebounds")),
            "ast": _f(stats.get("assists")), "stl": _f(stats.get("steals")),
            "blk": _f(stats.get("blocks")),
            "tov": _f(stats.get("totalTurnovers")) or _f(stats.get("turnovers")),
            "pf": _f(stats.get("fouls")),
        })

    for grp in bs.get("players", []) or []:
        abbr = (grp.get("team") or {}).get("abbreviation")
        for blk in grp.get("statistics", []) or []:
            labels = blk.get("labels") or []
            idx = {lab: i for i, lab in enumerate(labels)}
            for ath in blk.get("athletes", []) or []:
                a = ath.get("athlete") or {}
                st = ath.get("stats") or []

                def g(lab):
                    i = idx.get(lab)
                    if i is None or i >= len(st):
                        return None
                    return _f(st[i])

                players.append({
                    "game_id": eid, "team": abbr,
                    "pid": a.get("id"), "name": a.get("displayName"),
                    "starter": bool(ath.get("starter")),
                    "dnp": bool(ath.get("didNotPlay")),
                    "min": g("MIN"), "pts": g("PTS"), "reb": g("REB"),
                    "ast": g("AST"), "tov": g("TO"), "stl": g("STL"),
                    "blk": g("BLK"), "pm": g("+/-"),
                })
    return teams, players


def fetch_one(eid, session):
    for _ in range(3):
        try:
            r = session.get(URL.format(eid=eid), headers=HEADERS, timeout=40)
            if r.status_code == 200:
                return parse(eid, r.json())
            if r.status_code == 404:
                return [], []
        except Exception:  # noqa: BLE001
            pass
    return None


def _done_ids(path):
    ids = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    ids.add(str(json.loads(line)["game_id"]))
                except Exception:  # noqa: BLE001
                    continue
    return ids


def main():
    games = pd.read_csv(GAMES)
    games = games[games.season_type.isin([2, 3, 5])]
    want = [str(x) for x in games.game_id.tolist()]
    done = _done_ids(TEAM_OUT)
    todo = [e for e in want if e not in done]
    print(f"{len(want)} games, {len(done)} cached, {len(todo)} to fetch")
    if not todo:
        return

    session = requests.Session()
    session.mount("https://", requests.adapters.HTTPAdapter(
        pool_connections=32, pool_maxsize=32))

    n = 0
    with open(TEAM_OUT, "a") as tf, open(PLAYER_OUT, "a") as pf, \
            ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(lambda e: fetch_one(e, session), todo):
            if res is None:
                continue
            teams, players = res
            with _lock:
                for t in teams:
                    tf.write(json.dumps(t) + "\n")
                for p in players:
                    pf.write(json.dumps(p) + "\n")
                n += 1
                if n % 500 == 0:
                    tf.flush()
                    pf.flush()
                    print(f"  {n}/{len(todo)}", file=sys.stderr)
    print(f"done: {n} games parsed")


if __name__ == "__main__":
    main()
