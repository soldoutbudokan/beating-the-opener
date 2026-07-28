"""Archive prop + game lines from the BettingPros API (MLB/NBA/NFL/NHL).

Multi-sport port of wnba/src/scrape_bettingpros.py. The archive is ephemeral
upstream (seasons roll off ~13-19 months out), so it is committed to the repo:
events as raw payloads, offers through the src/slim.py whitelist (~70%
smaller, parser-equivalent — PLAN.md D1).

Saves under data/raw/bp/<sport_lower>/:
  events_<season>.json.gz                    all events for a season (raw)
  offers/<season>/<event>_<market>.json.gz   one slimmed offers payload

Usage:
  python3 src/scrape_bp.py --sport MLB [--season 2025]
                           [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                           [--raw-dir DIR]
Idempotent: skips offer files that already exist. Offers are only fetched
once the event's date has passed, so the single snapshot is the close.
--start/--end restrict which event dates are processed (dual-capture runs);
--raw-dir additionally dumps the unslimmed payload there (parity check).
"""
import argparse
import glob
import gzip
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from slim import slim_payload
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")
API = "https://api.bettingpros.com/v3"
# public key embedded in bettingpros.com frontend
KEY = "CHi8Hy5CEE4khd46XNYL23dCFX96oUdw6qOt1Dnh"

PAUSE = 0.35
WORKERS = 8
WINDOW_DAYS = 7  # events endpoint caps at 200/call; MLB peaks ~15 games/day


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "x-api-key": KEY, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        # catch ALL transport errors: RemoteDisconnected escapes the
        # URLError net and killed a 50k-file run 6k files in (2026-07-28)
        except Exception as e:  # noqa: BLE001
            wait = 2 ** (i + 1)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def save_gz(path, obj):
    # tmp + rename: a killed process must never leave a truncated .gz at
    # the final path (the fetch-skip would then preserve it forever)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def load_gz(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def windows(start, end):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else date.today()
    while d0 <= d1:
        w1 = min(d0 + timedelta(days=WINDOW_DAYS - 1), d1)
        yield str(d0), str(w1)
        d0 = w1 + timedelta(days=1)


def fetch_events(sport, season, raw_dir_sport):
    """Fetch season events and MERGE into the committed archive file.

    The archive is irreplaceable (upstream deletes old seasons), so this must
    never shrink it: fetched events are unioned with the existing file, and
    nothing is written when the fetch comes back empty.
    """
    path = os.path.join(raw_dir_sport, f"events_{season}.json.gz")
    existing = load_gz(path) if os.path.exists(path) else []
    events = []
    if sport == "NFL":
        # the NFL events endpoint ignores start/end (week-based league):
        # date windows return only the current week (verified 2026-07-28,
        # 16 events for the whole 2025 season). Iterate season weeks:
        # 1-18 regular, 19-22 playoffs.
        for week in range(1, 23):
            d = get(f"{API}/events?sport=NFL&season={season}&week={week}")
            if d is None:
                print(f"FAIL events week {week}", flush=True)
                continue
            events.extend(d.get("events") or [])
            time.sleep(PAUSE)
    else:
        start, end = SPORTS[sport]["seasons"][season]
        for w0, w1 in windows(start, end):
            d = get(f"{API}/events?sport={sport}&start={w0}&end={w1}")
            if d is None:
                print(f"FAIL events {w0}..{w1}", flush=True)
                continue
            evs = d.get("events") or []
            if len(evs) >= 200:
                print(f"WARNING: {w0}..{w1} hit the 200-event cap - narrow "
                      f"the window", flush=True)
            events.extend(evs)
            time.sleep(PAUSE)
    if not events:
        print(f"{sport} {season}: fetch empty - keeping existing "
              f"{len(existing)} events, writing nothing", flush=True)
        return existing
    merged = {e["id"]: e for e in existing}
    merged.update({e["id"]: e for e in events})
    out = sorted(merged.values(), key=lambda e: e["scheduled"])
    if len(out) < len(existing):
        raise RuntimeError(f"merge shrank events_{season} "
                           f"({len(existing)} -> {len(out)}) - refusing to write")
    if out == existing:  # identical content - don't churn the gzip
        print(f"{sport} {season}: {len(out)} events (unchanged)", flush=True)
        return out
    save_gz(path, out)
    print(f"{sport} {season}: {len(out)} events "
          f"({len(out) - len(existing)} new)", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--start", default=None,
                    help="only process events on/after this date")
    ap.add_argument("--end", default=None,
                    help="only process events on/before this date")
    ap.add_argument("--raw-dir", default=None,
                    help="also dump unslimmed payloads here (parity check)")
    args = ap.parse_args()
    sport = args.sport
    cfg = SPORTS[sport]
    seasons = [args.season] if args.season else sorted(cfg["seasons"])
    markets = {**cfg["prop_markets"], **cfg["game_markets"]}
    raw_dir_sport = os.path.join(ROOT, "data", "raw", "bp", sport.lower())

    # canary: if the API is unreachable (e.g. egress-blocked environment),
    # abort before touching anything - a failed run must not modify the archive
    if get(f"{API}/markets?sport={sport}", tries=2) is None:
        print("SCRAPE_ABORTED: api.bettingpros.com unreachable", flush=True)
        raise SystemExit(1)

    os.makedirs(raw_dir_sport, exist_ok=True)
    if args.raw_dir:
        os.makedirs(args.raw_dir, exist_ok=True)
    today = time.strftime("%Y-%m-%d")

    for season in seasons:
        os.makedirs(os.path.join(raw_dir_sport, "offers", str(season)),
                    exist_ok=True)
        events = fetch_events(sport, season, raw_dir_sport)
        # oldest first: earliest data is closest to rolling off upstream
        events.sort(key=lambda e: e["scheduled"])
        jobs = []
        n_skip = 0
        for e in events:
            eid, sched = e["id"], e["scheduled"][:10]
            if sched >= today:
                continue  # not closed yet
            if args.start and sched < args.start:
                continue
            if args.end and sched > args.end:
                continue
            for mid in markets:
                path = os.path.join(raw_dir_sport, "offers", str(season),
                                    f"{eid}_{mid}.json.gz")
                if os.path.exists(path):
                    n_skip += 1
                else:
                    jobs.append((eid, mid, path, sched))

        done = [0]

        def fetch_one(job):
            eid, mid, path, sched = job
            d = get(f"{API}/offers?sport={sport}&market_id={mid}"
                    f"&event_id={eid}&location=ALL")
            if d is None:
                print(f"FAIL offers event {eid} market {mid}", flush=True)
                return
            if args.raw_dir:
                save_gz(os.path.join(args.raw_dir, f"{eid}_{mid}.json.gz"), d)
            save_gz(path, slim_payload(d))
            done[0] += 1
            if done[0] % 250 == 0:
                print(f"  {sport} {season}: {done[0]}/{len(jobs)} (at {sched})",
                      flush=True)
            time.sleep(PAUSE)

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(fetch_one, jobs))
        print(f"{sport} {season} done: {done[0]} fetched, {n_skip} already had",
              flush=True)

    n = len(glob.glob(os.path.join(raw_dir_sport, "offers", "*", "*.json.gz")))
    print(f"{sport} archive: {n} offer files", flush=True)
    print("SCRAPE_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
