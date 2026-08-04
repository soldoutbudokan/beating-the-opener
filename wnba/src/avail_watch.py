"""Point-in-time availability capture for the news-watch routine
(v3 T3, owner-approved 2026-08-04: make the override layer systematic
and its inputs trainable).

Each run snapshots, from free ESPN endpoints:
  - the league injury report (player, status, type, return date, comment
    — includes "Coach's Decision", a pure minutes signal),
  - today's slate with per-event game-specific injury reports,
  - starters/DNP-with-reason from the event boxscore whenever ESPN has
    populated it (near/after tip) — that is the point-in-time lineup
    record that makes this dataset trainable later.

Snapshots are COMMITTED under data/raw/avail/<date>/<hhmm>Z.json (the
feed is ephemeral; the archive is the dataset). A snapshot is written
only when the normalized content changed vs the previous one, and the
script prints a structured diff (JSON lines) for the routine to judge
into live/projections_overrides.json. This script never judges, never
picks, never bets.

No model may train on this archive without a pre-registered QC gate
(PROGRESS.md discipline for created datasets).

Usage: python3 src/avail_watch.py --fetch
Exit lines: NO_CHANGE | SNAPSHOT_WRITTEN <path> | SOURCES_UNREACHABLE
"""
import glob
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "avail")
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
HDRS = {"User-Agent": "beating-the-opener/1.0 (research archive)",
        "Accept": "*/*"}


def fetch_json(url):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def norm_injury(team, it, source):
    det = it.get("details") or {}
    return {
        "team": team,
        "player": (it.get("athlete") or {}).get("displayName"),
        "status": it.get("status"),
        "type": det.get("type"),
        "return_date": det.get("returnDate"),
        "comment": (it.get("shortComment") or it.get("longComment")
                    or "")[:300],
        "source": source,
    }


def capture():
    snap = {"injuries": [], "events": [], "lineups": []}
    ok = False
    try:
        data = fetch_json(f"{BASE}/injuries")
        ok = True
        for t in data.get("injuries", []):
            team = t.get("displayName")
            for it in t.get("injuries", []):
                snap["injuries"].append(norm_injury(team, it, "league"))
    except Exception as e:
        print(f"source error (league injuries): {e}", file=sys.stderr)
    try:
        sb = fetch_json(f"{BASE}/scoreboard")
        ok = True
        for e in sb.get("events", []):
            eid = e.get("id")
            snap["events"].append({
                "event_id": eid, "name": e.get("shortName"),
                "date": e.get("date"),
                "status": (e.get("status", {}).get("type", {})
                           .get("name")),
            })
            try:
                s = fetch_json(f"{BASE}/summary?event={eid}")
            except Exception as ex:
                print(f"source error (summary {eid}): {ex}",
                      file=sys.stderr)
                continue
            for t in s.get("injuries", []):
                team = (t.get("team") or {}).get("displayName")
                for it in t.get("injuries", []):
                    snap["injuries"].append(
                        norm_injury(team, it, f"event:{eid}"))
            for tp in (s.get("boxscore") or {}).get("players", []):
                team = (tp.get("team") or {}).get("displayName")
                stats = tp.get("statistics") or [{}]
                for a in stats[0].get("athletes", []):
                    snap["lineups"].append({
                        "event_id": eid, "team": team,
                        "player": (a.get("athlete") or {})
                        .get("displayName"),
                        "starter": bool(a.get("starter")),
                        "dnp": bool(a.get("didNotPlay")),
                        "reason": a.get("reason"),
                    })
    except Exception as e:
        print(f"source error (scoreboard): {e}", file=sys.stderr)
    return snap if ok else None


def latest_snapshot():
    files = sorted(glob.glob(os.path.join(OUT, "*", "*.json")))
    if not files:
        return None, None
    try:
        return json.load(open(files[-1])), files[-1]
    except Exception:
        return None, files[-1]


def status_map(snap):
    """player -> (status, type) with league entries taking precedence
    over per-event duplicates only when the event entry is missing."""
    m = {}
    for it in snap.get("injuries", []):
        key = it.get("player")
        if not key:
            continue
        if key not in m or it["source"] == "league":
            m[key] = (it.get("status"), it.get("type"))
    return m


def diff(prev, cur):
    p, c = status_map(prev or {}), status_map(cur)
    out = []
    for player, (status, typ) in sorted(c.items()):
        if player not in p:
            out.append({"change": "NEW", "player": player,
                        "status": status, "type": typ})
        elif p[player] != (status, typ):
            out.append({"change": "UPDATED", "player": player,
                        "status": status, "type": typ,
                        "was": p[player][0]})
    for player, (status, typ) in sorted(p.items()):
        if player not in c:
            out.append({"change": "CLEARED", "player": player,
                        "was": status})
    return out


def main():
    snap = capture()
    if snap is None:
        print("SOURCES_UNREACHABLE")
        return
    prev, _ = latest_snapshot()
    core = lambda s: {k: s.get(k) for k in ("injuries", "events",
                                            "lineups")}
    if prev is not None and core(prev) == core(snap):
        print("NO_CHANGE")
        return
    now = datetime.now(timezone.utc)
    snap["captured_utc"] = now.strftime("%Y-%m-%d %H:%M")
    day = os.path.join(OUT, now.strftime("%Y-%m-%d"))
    os.makedirs(day, exist_ok=True)
    path = os.path.join(day, now.strftime("%H%M") + "Z.json")
    json.dump(snap, open(path, "w"), indent=0, sort_keys=True)
    print(f"SNAPSHOT_WRITTEN {os.path.relpath(path, ROOT)}")
    for d in diff(prev, snap):
        print(json.dumps(d))


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        main()
    else:
        print(__doc__)
