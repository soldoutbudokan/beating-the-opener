"""News fetch/diff utility for the news-watch routine (v3 T3).

Pulls free WNBA news sources and prints items NOT seen before as JSON
lines; the routine (a Claude session) reads them, decides whether any
imply an availability/minutes change, appends entries to
live/projections_overrides.json, and commits. This script does no
judging — it only fetches and de-duplicates.

Sources (free tier, per owner decision 2026-07-31):
  - ESPN WNBA news API  (site.api.espn.com — needs network allowlisting)
  - ESPN WNBA injuries page (www.espn.com)

State: live/news_seen.json (committed — the routine's memory).

Usage: python3 src/news_watch.py --fetch
Exit: prints one JSON object per new item; "NO_NEW_ITEMS" if none;
      "SOURCES_UNREACHABLE" if every source failed (network not yet
      allowlisted — the routine should end quietly in that case).
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
SEEN = os.path.join(ROOT, "live", "news_seen.json")
NEWS_URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
            "wnba/news?limit=40")


def fetch_json(url):
    # ESPN's edge 403s a bare "Mozilla/5.0" UA without an Accept header
    # (found 2026-08-04 — every routine firing had been getting
    # SOURCES_UNREACHABLE from this); an honest UA + Accept passes.
    req = urllib.request.Request(url, headers={
        "User-Agent": "beating-the-opener/1.0 (research archive)",
        "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    try:
        seen = set(json.load(open(SEEN))["ids"])
    except Exception:
        seen = set()
    new_items, ok = [], False
    try:
        data = fetch_json(NEWS_URL)
        ok = True
        for art in data.get("articles", []):
            key = str(art.get("dataSourceIdentifier")
                      or art.get("links", {}).get("web", {}).get("href")
                      or art.get("headline"))
            if key in seen:
                continue
            seen.add(key)
            new_items.append({
                "id": key,
                "published": art.get("published"),
                "headline": art.get("headline"),
                "description": art.get("description"),
            })
    except Exception as e:
        print(f"source error (espn news): {e}", file=sys.stderr)

    if not ok:
        print("SOURCES_UNREACHABLE")
        return
    if not new_items:
        print("NO_NEW_ITEMS")
        return
    json.dump({"ids": sorted(seen)[-2000:]}, open(SEEN, "w"), indent=0)
    for it in new_items:
        print(json.dumps(it))


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        main()
    else:
        print(__doc__)
