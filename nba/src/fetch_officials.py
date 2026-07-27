"""Fetch referee crews (and attendance) from the ESPN summary endpoint.

Crew assignments are published the morning of the game, so they are legitimate
pre-game information for every information tier -- and they are priced into the
closing line. Referees have persistent effects on foul rates and pace, which makes
them the most plausible remaining signal for the totals market.
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
OUT = os.path.join(ROOT, "data", "raw", "officials.jsonl")

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_lock = threading.Lock()


def fetch_one(eid, session):
    for _ in range(3):
        try:
            r = session.get(URL.format(eid=eid), headers=HEADERS, timeout=40)
            if r.status_code == 404:
                return {"game_id": eid, "officials": []}
            if r.status_code == 200:
                gi = (r.json() or {}).get("gameInfo") or {}
                offs = [o.get("fullName") for o in (gi.get("officials") or [])
                        if o.get("fullName")]
                return {"game_id": eid, "officials": offs,
                        "attendance": gi.get("attendance")}
        except Exception:  # noqa: BLE001
            pass
    return None


def main():
    games = pd.read_csv(GAMES)
    games = games[games.season_type.isin([2, 3, 5])]
    want = [str(x) for x in games.game_id.tolist()]

    done = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    done.add(str(json.loads(line)["game_id"]))
                except Exception:  # noqa: BLE001
                    continue
    todo = [e for e in want if e not in done]
    print(f"{len(want)} games, {len(done)} cached, {len(todo)} to fetch")
    if not todo:
        return

    session = requests.Session()
    session.mount("https://", requests.adapters.HTTPAdapter(
        pool_connections=32, pool_maxsize=32))
    n = 0
    with open(OUT, "a") as fh, ThreadPoolExecutor(max_workers=12) as ex:
        for rec in ex.map(lambda e: fetch_one(e, session), todo):
            if rec is None:
                continue
            with _lock:
                fh.write(json.dumps(rec) + "\n")
                n += 1
                if n % 1000 == 0:
                    fh.flush()
                    print(f"  {n}/{len(todo)}", file=sys.stderr)
    print(f"done: {n} records -> {OUT}")


if __name__ == "__main__":
    main()
