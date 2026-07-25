"""Fetch per-game betting markets from the ESPN core odds API.

For each event we scan every provider and keep both a preferred-book record and a
cross-book consensus (median). Results stream to JSONL so runs resume cheaply.
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
OUT = os.path.join(ROOT, "data", "raw", "odds.jsonl")

URL = ("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/"
       "events/{eid}/competitions/{eid}/odds")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Sharp / widely-followed books first; ESPN's own feed dominates recent seasons.
PREFERRED = [
    "ESPN BET", "DraftKings", "Caesars Sportsbook", "Caesars", "BetMGM",
    "William Hill (New Jersey)", "Westgate", "Bovada", "BOVADA.lv",
    "5Dimes.eu", "BETONLINE.ag", "unibet", "Tipico",
]

_lock = threading.Lock()


def _num(x):
    """American odds arrive as '+225', '-110', or a float. Normalise to float."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("+", "")
    if s in ("", "EVEN", "even", "OFF", "off", "-", "PK", "pk"):
        return 100.0 if s.lower() == "even" else None
    try:
        return float(s)
    except ValueError:
        return None


def _leaf(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def parse_item(it):
    """Pull open/close moneyline, spread and total out of one provider block."""
    rec = {"provider": _leaf(it, "provider", "name")}
    for side, key in (("home", "homeTeamOdds"), ("away", "awayTeamOdds")):
        blk = it.get(key) or {}
        # Closing: prefer the explicit close block, else the flat moneyLine field.
        rec[f"{side}_ml_close"] = (
            _num(_leaf(blk, "close", "moneyLine", "american"))
            or _num(blk.get("moneyLine"))
        )
        rec[f"{side}_ml_open"] = _num(_leaf(blk, "open", "moneyLine", "american"))
        rec[f"{side}_spread_close"] = _num(
            _leaf(blk, "close", "pointSpread", "american")
        )
        rec[f"{side}_spread_open"] = _num(
            _leaf(blk, "open", "pointSpread", "american")
        )
        rec[f"{side}_spread_price_close"] = _num(
            _leaf(blk, "close", "spread", "american")
        )
    rec["total_close"] = _num(it.get("overUnder"))
    rec["total_open"] = _num(_leaf(it, "open", "total", "american"))
    rec["over_price"] = _num(it.get("overOdds"))
    rec["under_price"] = _num(it.get("underOdds"))
    # ESPN's flat `spread` is signed from the home side.
    rec["spread_flat"] = _num(it.get("spread"))
    return rec


def consensus(items):
    """Median across books for the fields that matter, plus a preferred-book pick."""
    recs = [parse_item(it) for it in items]
    recs = [r for r in recs if r.get("provider")]
    # Live-odds feeds reflect in-game state -> must never be used as a pre-game line.
    recs = [r for r in recs if "live" not in (r["provider"] or "").lower()]
    if not recs:
        return {}

    out = {"n_books": len(recs)}
    fields = [
        "home_ml_close", "away_ml_close", "home_ml_open", "away_ml_open",
        "home_spread_close", "away_spread_close", "home_spread_open",
        "total_close", "total_open", "over_price", "under_price", "spread_flat",
    ]
    for f in fields:
        vals = [r[f] for r in recs if r.get(f) is not None]
        out[f"cons_{f}"] = float(pd.Series(vals).median()) if vals else None

    pick = None
    for name in PREFERRED:
        for r in recs:
            if (r["provider"] or "").lower() == name.lower():
                pick = r
                break
        if pick:
            break
    if pick is None:
        # Fall back to whichever book actually carries a closing moneyline.
        withml = [r for r in recs if r.get("home_ml_close") is not None]
        pick = withml[0] if withml else recs[0]
    for k, v in pick.items():
        out[f"book_{k}"] = v
    return out


def fetch_one(eid, session):
    for attempt in range(3):
        try:
            r = session.get(URL.format(eid=eid), headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return {"game_id": eid, "n_books": 0}
            if r.status_code == 200:
                d = r.json()
                rec = consensus(d.get("items", []))
                rec["game_id"] = eid
                return rec
        except Exception:  # noqa: BLE001 - transient network errors
            pass
    return None


def main():
    games = pd.read_csv(GAMES)
    # Preseason lines are thin and irrelevant to the benchmark.
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
    adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)

    n = [0]
    with open(OUT, "a") as fh, ThreadPoolExecutor(max_workers=12) as ex:
        for rec in ex.map(lambda e: fetch_one(e, session), todo):
            if rec is None:
                continue
            with _lock:
                fh.write(json.dumps(rec) + "\n")
                n[0] += 1
                if n[0] % 500 == 0:
                    fh.flush()
                    print(f"  {n[0]}/{len(todo)}", file=sys.stderr)
    print(f"done, wrote {n[0]} records -> {OUT}")


if __name__ == "__main__":
    main()
