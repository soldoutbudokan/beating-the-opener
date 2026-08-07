"""Archive WNBA prop lines from the BettingPros API.

The archive is ephemeral upstream (2024 offers are already gone; 2025 presumably
rolls off ~13 months out), so raw gzipped JSON is committed to the repo.

Saves:
  data/raw/bp/events_<season>.json.gz          all events for a season
  data/raw/bp/offers/<event>_<market>.json.gz  one offers payload per event x market

Usage: python3 src/scrape_bettingpros.py [--season 2025]
       python3 src/scrape_bettingpros.py --refetch 2679,2680   # re-archive events
       python3 src/scrape_bettingpros.py --fill-pages          # repair truncation
Idempotent: skips files that already exist. Offers are only fetched once the
event has actually finished (tip + FINAL_CUSHION_H), so the first snapshot is
already the close. `offers` is paginated at 10/page upstream and every page is
followed - see fetch_offers() for what keeping page 1 alone used to cost.
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

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
WORKERS = 8
# An event is archivable only once it is over: a WNBA game runs ~2h15m, so
# tip + 5h clears regulation, OT and a broadcast delay. Compare TIMESTAMPS,
# never dates - `scheduled` is UTC while the rest of the pipeline keys off the
# ET game date, and a date-vs-date test gets that boundary wrong in both
# directions: it skipped 8pm-ET tips for a full extra day (their UTC date is
# tomorrow's) and it archived 6-8pm-ET tips ~1h in, mid-game, as the "close".
FINAL_CUSHION_H = 5.0


def is_final(event, now=None):
    """True once `event` is over and its offers are safe to archive as a close."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        tip = dt.datetime.strptime(event["scheduled"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return False
    return now >= tip.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=FINAL_CUSHION_H)


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


def fetch_offers(eid, mid):
    """Every page of an event x market offers payload, or None on failure.

    The API paginates `offers` at 10 per page. The first version of this
    scraper kept page 1 only, so any event x market with more than 10 players
    was silently truncated - 476 of 8145 archived files, 914 offers lost,
    concentrated in points (the market with the most quoted players). The cost
    is invisible until settlement: `close_prob` cannot find the player in the
    snapshot, so the bet's CLV stays blank forever (NaLyssa Smith 8/6, Nyara
    Sabally 8/6, Rae Burrell 8/5, Maria Conde 8/4).

    A partial fetch is worse than none - it would archive a payload that looks
    complete - so any page failing aborts the whole payload and leaves the
    existing file untouched for the next run to retry.
    """
    d = get(f"{API}/offers?sport=WNBA&market_id={mid}&event_id={eid}&location=ALL")
    if d is None:
        return None
    offers = list(d.get("offers") or [])
    pages = int((d.get("_pagination") or {}).get("total_pages") or 1)
    for page in range(2, pages + 1):
        p = get(f"{API}/offers?sport=WNBA&market_id={mid}&event_id={eid}"
                f"&location=ALL&page={page}")
        if p is None:
            print(f"FAIL offers event {eid} market {mid} page {page}", flush=True)
            return None
        offers.extend(p.get("offers") or [])
        time.sleep(PAUSE)
    d["offers"] = offers
    d["_pages_fetched"] = pages
    return d


def offer_ids(payload):
    """Stable per-offer identity, for merges that must not drop anything."""
    out = []
    for o in payload.get("offers") or []:
        oid = o.get("id")
        if oid is None:
            pl = (o.get("participants") or [{}])[0].get("player") or {}
            oid = (pl.get("first_name", ""), pl.get("last_name", ""))
        out.append(oid)
    return out


def fill_pages(only=None):
    """Backfill pages 2..N into archived files truncated by the page-1 bug.

    Non-destructive by construction: the original payload (and its `utc`, the
    time the close was actually captured) is kept as-is and only unseen offers
    are appended, so a re-fetch that comes back short of what upstream served
    at close time can never shrink the archive.
    """
    paths = sorted(glob.glob(os.path.join(RAW, "offers", "*.json.gz")))
    fixed = grew = failed = 0
    for path in paths:
        eid, mid = os.path.basename(path).split(".")[0].split("_")
        if only and int(eid) not in only:
            continue
        d = load_gz(path)
        pag = d.get("_pagination") or {}
        pages = int(pag.get("total_pages") or 1)
        have = len(d.get("offers") or [])
        if pages <= 1 or have >= int(pag.get("total_items") or have):
            continue
        seen = set(map(str, offer_ids(d)))
        added = []
        ok = True
        for page in range(2, pages + 1):
            p = get(f"{API}/offers?sport=WNBA&market_id={mid}&event_id={eid}"
                    f"&location=ALL&page={page}")
            if p is None:
                ok = False
                break
            for o in p.get("offers") or []:
                one = {"offers": [o]}
                if str(offer_ids(one)[0]) not in seen:
                    seen.add(str(offer_ids(one)[0]))
                    added.append(o)
            time.sleep(PAUSE)
        if not ok:
            print(f"FAIL fill {eid}_{mid}: page fetch failed, file untouched",
                  flush=True)
            failed += 1
            continue
        fixed += 1
        if not added:
            continue
        d["offers"] = list(d.get("offers") or []) + added
        d["_pages_fetched"] = pages
        d["_pages_backfilled_utc"] = dt.datetime.now(
            dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        save_gz(path, d)
        grew += 1
        if grew % 50 == 0:
            print(f"  filled {grew} files", flush=True)
    print(f"fill-pages: {fixed} checked, {grew} grew, {failed} failed",
          flush=True)


def fetch_events(season):
    """Fetch season events and MERGE into the committed archive file.

    The archive is irreplaceable (upstream deletes old seasons), so this must
    never shrink it: fetched events are unioned with the existing file, and
    nothing is written when the fetch comes back empty.
    """
    path = os.path.join(RAW, f"events_{season}.json.gz")
    existing = load_gz(path) if os.path.exists(path) else []
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
    if not events:
        print(f"season {season}: fetch empty - keeping existing "
              f"{len(existing)} events, writing nothing", flush=True)
        return existing
    merged = {e["id"]: e for e in existing}
    merged.update({e["id"]: e for e in events})
    out = sorted(merged.values(), key=lambda e: e["scheduled"])
    if len(out) < len(existing):
        raise RuntimeError(f"merge shrank events_{season} "
                           f"({len(existing)} -> {len(out)}) - refusing to write")
    if out == existing:  # identical content - don't churn the gzip
        print(f"season {season}: {len(out)} events (unchanged)", flush=True)
        return out
    save_gz(path, out)
    print(f"season {season}: {len(out)} events "
          f"({len(out) - len(existing)} new)", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--refetch", default="",
                    help="comma-separated event ids to re-archive, overwriting "
                         "existing files (repairs snapshots taken mid-game)")
    ap.add_argument("--fill-pages", default=None, const="all", nargs="?",
                    help="backfill pages 2..N into files truncated by the "
                         "page-1 bug: 'all' or comma-separated event ids. "
                         "Appends only; never rewrites page 1 or its `utc`.")
    args = ap.parse_args()
    seasons = [args.season] if args.season else sorted(SEASONS)
    refetch = {int(x) for x in args.refetch.split(",") if x.strip()}

    # canary: if the API is unreachable (e.g. egress-blocked environment),
    # abort before touching anything - a failed run must not modify the archive
    if get(f"{API}/markets?sport=WNBA", tries=2) is None:
        print("SCRAPE_ABORTED: api.bettingpros.com unreachable", flush=True)
        raise SystemExit(1)

    os.makedirs(os.path.join(RAW, "offers"), exist_ok=True)

    if args.fill_pages:
        only = (None if args.fill_pages == "all"
                else {int(x) for x in args.fill_pages.split(",") if x.strip()})
        fill_pages(only)
        print("SCRAPE_COMPLETE", flush=True)
        return

    for season in seasons:
        events = fetch_events(season)
        # oldest first: earliest data is closest to rolling off upstream
        events.sort(key=lambda e: e["scheduled"])
        jobs = []
        n_skip = 0
        n_pending = 0
        for e in events:
            eid, sched = e["id"], e["scheduled"][:10]
            if not is_final(e):
                n_pending += 1
                continue  # still to come or in progress; live pipeline's job
            for mid in MARKETS:
                path = os.path.join(RAW, "offers", f"{eid}_{mid}.json.gz")
                if os.path.exists(path) and eid not in refetch:
                    n_skip += 1
                else:
                    jobs.append((eid, mid, path, sched))

        done = [0]
        repaired = [0]

        def fetch_one(job):
            eid, mid, path, sched = job
            d = fetch_offers(eid, mid)
            if d is None:
                print(f"FAIL offers event {eid} market {mid}", flush=True)
                return
            # the archive is irreplaceable: never trade a good file for a
            # thinner one (rolled-off event, market never offered, or a
            # refetch that came back short of what upstream served at close)
            if os.path.exists(path):
                old = len(load_gz(path).get("offers") or [])
                if len(d.get("offers") or []) < max(old, 1):
                    print(f"KEEP existing {eid}_{mid}: refetch returned "
                          f"{len(d.get('offers') or [])} offers vs {old}",
                          flush=True)
                    return
            existed = os.path.exists(path)
            save_gz(path, d)
            done[0] += 1
            repaired[0] += existed
            if done[0] % 250 == 0:
                print(f"  {season}: {done[0]}/{len(jobs)} (at {sched})", flush=True)
            time.sleep(PAUSE)

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(fetch_one, jobs))
        print(f"season {season} done: {done[0]} fetched "
              f"({repaired[0]} overwritten), {n_skip} already had, "
              f"{n_pending} not final yet", flush=True)

    n = len(glob.glob(os.path.join(RAW, "offers", "*.json.gz")))
    print(f"archive: {n} offer files", flush=True)
    print("SCRAPE_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
