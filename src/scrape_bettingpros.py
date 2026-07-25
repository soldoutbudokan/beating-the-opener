"""Archive WNBA prop lines from the BettingPros API.

The archive is ephemeral upstream (2024 offers are already gone; 2025 presumably
rolls off ~13 months out), so raw gzipped JSON is committed to the repo.

Saves:
  data/raw/bp/events_<season>.json.gz          all events for a season
  data/raw/bp/offers/<event>_<market>.json.gz  one offers payload per event x market

Usage: python3 src/scrape_bettingpros.py [--season 2025] [--refresh-days N]
Idempotent: skips files that already exist (unless the event was recent).
"""
import argparse
import glob
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw", "bp")
API = "https://api.bettingpros.com/v3"
# public key embedded in bettingpros.com frontend
KEY = "CHi8Hy5CEE4khd46XNYL23dCFX96oUdw6qOt1Dnh"

# player prop O/U markets + game odds (features/context)
MARKETS = {
    393: "points", 397: "rebounds", 391: "assists", 390: "threes",
    396: "pra", 394: "pts_ast", 395: "pts_reb", 398: "reb_ast",
    399: "steals", 392: "blocks", 401: "turnovers", 400: "stl_blk",
    371: "moneyline", 372: "total", 373: "spread",
}
# season -> (start, end) month windows; events endpoint caps at 200/call
SEASONS = {
    2025: [("2025-05-01", "2025-06-30"), ("2025-07-01", "2025-08-31"),
           ("2025-09-01", "2025-11-01")],
    2026: [("2026-05-01", "2026-06-30"), ("2026-07-01", "2026-08-31"),
           ("2026-09-01", "2026-11-01")],
}
PAUSE = 0.35


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "x-api-key": KEY, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            wait = 2 ** (i + 1)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def save_gz(path, obj):
    with gzip.open(path, "wt") as f:
        json.dump(obj, f)


def load_gz(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def fetch_events(season):
    events = []
    for start, end in SEASONS[season]:
        d = get(f"{API}/events?sport=WNBA&start={start}&end={end}")
        if d is None:
            print(f"FAIL events {start}..{end}", flush=True)
            continue
        evs = d.get("events", [])
        if len(evs) >= 200:
            print(f"WARNING: {start}..{end} hit the 200-event cap - narrow the window",
                  flush=True)
        events.extend(evs)
        time.sleep(PAUSE)
    seen, out = set(), []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    save_gz(os.path.join(RAW, f"events_{season}.json.gz"), out)
    print(f"season {season}: {len(out)} events", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--refresh-days", type=int, default=3,
                    help="re-fetch offers for events within N days (lines still moving)")
    args = ap.parse_args()
    seasons = [args.season] if args.season else sorted(SEASONS)

    os.makedirs(os.path.join(RAW, "offers"), exist_ok=True)
    today = time.strftime("%Y-%m-%d")

    for season in seasons:
        events = fetch_events(season)
        # oldest first: earliest data is closest to rolling off upstream
        events.sort(key=lambda e: e["scheduled"])
        n_done = n_skip = 0
        for e in events:
            eid, sched = e["id"], e["scheduled"][:10]
            if sched >= today:
                continue  # not closed yet; live pipeline's job
            for mid in MARKETS:
                path = os.path.join(RAW, "offers", f"{eid}_{mid}.json.gz")
                if os.path.exists(path):
                    n_skip += 1
                    continue
                d = get(f"{API}/offers?sport=WNBA&market_id={mid}&event_id={eid}&location=ALL")
                if d is None:
                    print(f"FAIL offers event {eid} market {mid}", flush=True)
                    continue
                save_gz(path, d)
                n_done += 1
                if n_done % 100 == 0:
                    print(f"  {season}: {n_done} fetched, {n_skip} skipped "
                          f"(at {sched})", flush=True)
                time.sleep(PAUSE)
        print(f"season {season} done: {n_done} fetched, {n_skip} already had", flush=True)

    n = len(glob.glob(os.path.join(RAW, "offers", "*.json.gz")))
    print(f"archive: {n} offer files", flush=True)
    print("SCRAPE_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
