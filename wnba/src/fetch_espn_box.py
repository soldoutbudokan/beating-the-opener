"""ESPN box-score fallback for dates wehoop has not published yet.

wehoop (sportsdataverse/wehoop-wnba-data) publishes in bulk and stalls: in
August 2026 it froze at 2026-08-01 for five days while games kept being
played, which left every bet on those slates `open` with no result - and,
worse, on course to be voided by settle_bets' "no box row after 3d" rule,
which cannot tell an absent player from an absent feed.

This fetcher archives ESPN's own final box scores (the same source wehoop
scrapes) for dates wehoop is missing, so settlement can proceed. It is a
FALLBACK, not a replacement: `load_espn_box` is consulted only for dates
with no wehoop coverage, and wehoop reclaims those dates the moment it
publishes them. Nothing here feeds the model panel - `features.py` and
`grade_props.py` still read wehoop only, so the scoring firewall in
PROGRESS.md is untouched.

Output: data/raw/espn_box/<ET date>.json, one file per slate, COMMITTED
(the parquet mirror is gitignored, so this archive is the only record of a
settlement's inputs while wehoop is behind).

Usage:
  python3 src/fetch_espn_box.py                # dates wehoop lacks -> yesterday
  python3 src/fetch_espn_box.py --since 2026-08-02
  python3 src/fetch_espn_box.py --dates 2026-08-03,2026-08-05
"""
import argparse
import glob
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "espn_box")
ET = "America/New_York"
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
# same honest UA as avail_watch.py - the session proxy 403s spoofed
# browser user-agents
UA = {"User-Agent": "beating-the-opener/1.0 (research archive)",
      "Accept": "*/*"}
TIMEOUT = 30
MAX_BACKFILL_DAYS = 14  # a cold start must not walk the whole season

# ESPN box header -> wehoop player_box column. Only the columns settlement
# reads; the rest of wehoop's 100+ columns are model inputs and are NOT
# reconstructed here (see module docstring).
STATS = {"MIN": "minutes", "PTS": "points", "REB": "rebounds",
         "AST": "assists", "STL": "steals", "BLK": "blocks",
         "TO": "turnovers"}
MADE_OF = {"3PT": "three_point_field_goals_made"}  # "2-5" -> 2
COLS = ["game_id", "game_date", "athlete_display_name", "team_abbreviation",
        "minutes", "points", "rebounds", "assists", "steals", "blocks",
        "turnovers", "three_point_field_goals_made", "did_not_play",
        "reason", "starter", "ejected", "active"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _num(v):
    """ESPN stat cell -> float. '--', '' and junk become NaN, not 0."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return float("nan")


def _made(v):
    """'2-5' -> 2.0 (makes of a makes-attempts cell)."""
    try:
        return float(str(v).split("-")[0])
    except (TypeError, ValueError, IndexError):
        return float("nan")


def wehoop_dates():
    """ET dates wehoop already covers (season 2025+, the settleable era)."""
    dates = set()
    for p in sorted(glob.glob(os.path.join(
            ROOT, "data", "wehoop", "player_box_*.parquet"))):
        if int(p[-12:-8]) < 2025:
            continue
        try:
            df = pd.read_parquet(p, columns=["game_date"])
        except Exception:
            continue
        dates |= set(df["game_date"].astype(str).str[:10])
    return dates


def parse_summary(event_id, date):
    """ESPN summary -> rows in wehoop's player_box shape (settlement subset)."""
    s = _get(f"{API}/summary?event={event_id}")
    rows = []
    for team in s.get("boxscore", {}).get("players", []):
        abbr = (team.get("team") or {}).get("abbreviation")
        for block in team.get("statistics", []):
            names = block.get("names") or []
            where = {n: i for i, n in enumerate(names)}
            for a in block.get("athletes", []):
                stats = a.get("stats") or []
                dnp = bool(a.get("didNotPlay")) or not stats

                def cell(header):
                    i = where.get(header)
                    return stats[i] if i is not None and i < len(stats) else None

                row = {
                    "game_id": int(event_id),
                    "game_date": date,
                    "athlete_display_name": (a.get("athlete") or {})
                    .get("displayName"),
                    "team_abbreviation": abbr,
                    "did_not_play": dnp,
                    # ESPN sets reason to "COACH'S DECISION" even for players
                    # who logged 34 minutes - it is only meaningful on a DNP.
                    "reason": a.get("reason") if dnp else None,
                    "starter": bool(a.get("starter")),
                    "ejected": bool(a.get("ejected")),
                    "active": bool(a.get("active", True)),
                }
                for header, col in STATS.items():
                    row[col] = float("nan") if dnp else _num(cell(header))
                for header, col in MADE_OF.items():
                    row[col] = float("nan") if dnp else _made(cell(header))
                if row["athlete_display_name"]:
                    rows.append(row)
    return rows


def fetch_date(date, force=False):
    """Archive one ET slate. Returns (n_games, n_rows, note)."""
    path = os.path.join(OUT, f"{date}.json")
    ymd = date.replace("-", "")
    sb = _get(f"{API}/scoreboard?dates={ymd}")
    finals = [e for e in sb.get("events", [])
              if ((e.get("status") or {}).get("type") or {})
              .get("name") == "STATUS_FINAL"]
    if not finals:
        return 0, 0, "no final games"
    want = sorted(int(e["id"]) for e in finals)
    if os.path.exists(path) and not force:
        try:
            have = json.load(open(path))
            if sorted(g["event_id"] for g in have.get("games", [])) == want:
                return len(want), sum(len(g["players"]) for g in have["games"]), \
                    "unchanged"
        except Exception:
            pass  # unreadable archive -> refetch
    games = []
    for e in finals:
        rows = parse_summary(e["id"], date)
        games.append({"event_id": int(e["id"]),
                      "short_name": e.get("shortName"),
                      "utc_tip": e.get("date"),
                      "players": rows})
    os.makedirs(OUT, exist_ok=True)
    payload = {
        "date": date,
        "captured_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"{API}/summary (ESPN), fallback while wehoop lags",
        "games": games,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return len(games), sum(len(g["players"]) for g in games), "written"


def load_espn_box(covered=()):
    """Archived ESPN rows for dates NOT in `covered` (wehoop's dates).

    Returns a DataFrame in wehoop's player_box shape plus `nname`/`date`,
    or None when there is nothing to add. Never raises: a settlement run
    must survive a malformed or absent archive.
    """
    covered = set(covered)
    rows = []
    for p in sorted(glob.glob(os.path.join(OUT, "*.json"))):
        date = os.path.basename(p)[:-5]
        if date in covered:
            continue  # wehoop published it - wehoop is the source of record
        try:
            d = json.load(open(p))
        except Exception:
            continue
        for g in d.get("games", []):
            rows.extend(g.get("players", []))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in COLS:
        if c not in df.columns:
            df[c] = float("nan")
    df["nname"] = df["athlete_display_name"].map(norm)
    df["date"] = df["game_date"].astype(str).str[:10]
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="first ET date to fill (YYYY-MM-DD)")
    ap.add_argument("--dates", help="explicit comma-separated ET dates")
    ap.add_argument("--force", action="store_true",
                    help="refetch even if the archive already matches")
    a = ap.parse_args()

    # tz-naive calendar date: every date this module handles is an ET
    # calendar day (wehoop's own convention), never an instant.
    today = pd.Timestamp(pd.Timestamp.now(tz=ET).date())
    if a.dates:
        targets = [d.strip() for d in a.dates.split(",") if d.strip()]
    else:
        have = wehoop_dates()
        if a.since:
            start = pd.Timestamp(a.since)
        elif have:
            start = pd.Timestamp(max(have)) + pd.Timedelta(days=1)
        else:  # cold container, wehoop not fetched yet
            start = today - pd.Timedelta(days=MAX_BACKFILL_DAYS)
        start = max(start, today - pd.Timedelta(days=MAX_BACKFILL_DAYS))
        end = today - pd.Timedelta(days=1)  # yesterday ET; today is unfinished
        targets = [str(d.date()) for d in pd.date_range(start, end)]
        targets = [d for d in targets if d not in have]

    if not targets:
        print("ESPN_BOX_UP_TO_DATE")
        return
    games = rows = 0
    unreachable = 0
    for d in targets:
        try:
            g, r, note = fetch_date(d, force=a.force)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            print(f"{d}: unreachable ({e})")
            unreachable += 1
            continue
        print(f"{d}: {g} game(s), {r} player rows ({note})")
        games += g
        rows += r
    if unreachable and not games:
        print("SOURCES_UNREACHABLE")
    else:
        print(f"ESPN_BOX_COMPLETE {games} game(s), {rows} player rows")


if __name__ == "__main__":
    main()
