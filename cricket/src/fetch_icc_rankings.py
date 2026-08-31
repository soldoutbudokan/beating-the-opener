"""Point-in-time ICC T20I team rankings from Wikipedia revision history.

Why Wikipedia and not the ICC: the ICC publishes only *current* ratings and
its pages are JS-rendered (Wayback captures are sparse and mostly empty),
while the Wikipedia rankings articles carry the full table in wikitext and
are edited within a day or two of every ICC update — so the article's own
revision history IS a timestamped ratings archive, fetched from the MediaWiki
API with no scraping (verified 2026-08-30: men's page 33519096 back to 2011,
women's 78031889).

The ratings are a MARKET-FREE public tier signal: they are computed by the
ICC from results only, and every model row uses the newest revision STRICTLY
BEFORE the match date (never the post-match update).

Output (committed - the API's older revisions can be revision-deleted):
  data/raw/icc/rankings_{male,female}.csv
    revid, ts (UTC), team_code, matches, points, rating

Usage: python3 src/fetch_icc_rankings.py [--since 2017-01-01] [--step-days 10]
"""
import argparse
import os
import re
import time

import pandas as pd
import requests

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "icc")
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "beating-the-opener research (github.com/soldoutbudokan)"}
# the articles TRANSCLUDE the ratings table from these templates, which are
# edited after every ICC update - the template history is the real archive
PAGES = {"male": "Template:ICC Men's T20I Team Rankings",
         "female": "Template:ICC Women's T20I Rankings"}
FALLBACK = {"male": "ICC Men's T20I Team Rankings",
            "female": "ICC Women's ODI and T20I Team Rankings"}
ROW = re.compile(r"\{\{\s*cr\|([A-Za-z]{2,4})\s*\}\}[^|]*\|\|\s*([\d,]+)\s*\|\|\s*([\d,]+)\s*\|\|\s*([\d,]+)")
PAUSE = 0.3


def get(sess, params, tries=5):
    for i in range(tries):
        try:
            r = sess.get(API, params={**params, "format": "json"}, timeout=90)
            if r.status_code == 200:
                return r.json()
            print(f"  retry {i+1}: HTTP {r.status_code}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  retry {i+1}: {e}", flush=True)
        time.sleep(3 * (i + 1))
    return None


def list_revisions(sess, title, since):
    revs, cont = [], None
    while True:
        p = {"action": "query", "prop": "revisions", "titles": title,
             "rvlimit": 500, "rvprop": "ids|timestamp", "rvdir": "newer",
             "rvstart": f"{since}T00:00:00Z"}
        if cont:
            p["rvcontinue"] = cont
        d = get(sess, p)
        if not d:
            break
        pages = d.get("query", {}).get("pages", {})
        for _, pg in pages.items():
            if "missing" in pg:
                return []
            revs += [(r["revid"], r["timestamp"]) for r in pg.get("revisions", [])]
        cont = d.get("continue", {}).get("rvcontinue")
        if not cont:
            break
        time.sleep(PAUSE)
    return revs


def thin(revs, step_days):
    """One revision per step_days window (the last in each window)."""
    out, last = [], None
    for revid, ts in revs:
        t = pd.Timestamp(ts)
        if last is None or (t - last).days >= step_days:
            out.append((revid, ts))
            last = t
    if revs and out[-1][0] != revs[-1][0]:
        out.append(revs[-1])
    return out


def fetch_contents(sess, revids):
    rows = []
    for i in range(0, len(revids), 40):
        chunk = revids[i:i + 40]
        d = get(sess, {"action": "query", "prop": "revisions",
                       "revids": "|".join(str(r) for r in chunk),
                       "rvprop": "content|ids|timestamp", "rvslots": "main"})
        if not d:
            continue
        for _, pg in d.get("query", {}).get("pages", {}).items():
            for rev in pg.get("revisions", []):
                txt = rev.get("slots", {}).get("main", {}).get("*", "")
                rows.append((rev["revid"], rev["timestamp"], txt))
        print(f"  fetched {min(i+40, len(revids))}/{len(revids)} revisions", flush=True)
        time.sleep(PAUSE)
    return rows


def parse_table(txt):
    """Rows of the ratings table: {{cr|XXX}} || matches || points || rating."""
    out = []
    for code, mt, pts, rat in ROW.findall(txt):
        try:
            out.append((code.upper(), int(mt.replace(",", "")),
                        int(pts.replace(",", "")), int(rat.replace(",", ""))))
        except ValueError:
            continue
    # a valid capture lists many teams and plausible ratings
    if len(out) < 8 or max(r for _, _, _, r in out) > 400:
        return []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2017-01-01")
    ap.add_argument("--step-days", type=int, default=10)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sess = requests.Session()
    sess.headers.update(UA)
    for gender, title in PAGES.items():
        revs = list_revisions(sess, title, args.since)
        if len(revs) < 20 and gender in FALLBACK:
            revs = list_revisions(sess, FALLBACK[gender], args.since)
            title = FALLBACK[gender]
        print(f"{gender}: {len(revs)} revisions of '{title}' since {args.since}",
              flush=True)
        sel = thin(revs, args.step_days)
        print(f"{gender}: {len(sel)} sampled (>= {args.step_days}d apart)", flush=True)
        contents = fetch_contents(sess, [r for r, _ in sel])
        rows = []
        for revid, ts, txt in contents:
            for code, mt, pts, rat in parse_table(txt):
                rows.append({"revid": revid, "ts": ts, "team_code": code,
                             "matches": mt, "points": pts, "rating": rat})
        df = pd.DataFrame(rows)
        path = os.path.join(OUT, f"rankings_{gender}.csv")
        if os.path.exists(path):
            old = pd.read_csv(path)
            df = (pd.concat([old, df]).drop_duplicates(["revid", "team_code"])
                  .sort_values(["ts", "team_code"]))
            if len(df) < len(old):
                raise RuntimeError(f"{path} would shrink - refusing")
        df.to_csv(path, index=False)
        if len(df):
            print(f"{gender}: {len(df)} rows, {df.revid.nunique()} snapshots, "
                  f"{df.ts.min()[:10]} .. {df.ts.max()[:10]}, "
                  f"{df.team_code.nunique()} teams", flush=True)
        else:
            print(f"{gender}: NO ROWS PARSED", flush=True)
    print("ICC_RANKINGS_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
