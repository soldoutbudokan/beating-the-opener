"""Download historical odds/results CSVs from football-data.co.uk.

Seasons 2008-09 through 2025-26 (early seasons only used to warm up ratings;
Pinnacle closing odds exist from 2012-13 onward).
"""
import os
import time
import urllib.request

BASE = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
DIVS = [
    "E0", "E1", "E2", "E3", "EC",          # England: Prem, Champ, L1, L2, National
    "SC0", "SC1", "SC2", "SC3",            # Scotland
    "D1", "D2",                            # Germany
    "I1", "I2",                            # Italy
    "SP1", "SP2",                          # Spain
    "F1", "F2",                            # France
    "N1", "B1", "P1", "T1", "G1",          # Netherlands, Belgium, Portugal, Turkey, Greece
]
SEASONS = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2008, 2026)]

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def main():
    os.makedirs(OUT, exist_ok=True)
    n_ok, n_fail = 0, 0
    for season in SEASONS:
        for div in DIVS:
            dest = os.path.join(OUT, f"{season}_{div}.csv")
            if os.path.exists(dest) and os.path.getsize(dest) > 1000:
                n_ok += 1
                continue
            url = BASE.format(season=season, div=div)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                if len(body) < 500:
                    n_fail += 1
                    continue
                with open(dest, "wb") as f:
                    f.write(body)
                n_ok += 1
            except Exception as e:
                print(f"FAIL {season} {div}: {e}")
                n_fail += 1
            time.sleep(0.15)
        print(f"season {season} done ({n_ok} ok, {n_fail} missing)")
    print(f"downloaded/present: {n_ok}, missing: {n_fail}")


if __name__ == "__main__":
    main()
