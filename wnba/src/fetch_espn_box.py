"""ESPN box-score fallback for dates wehoop has not published yet.

wehoop (sportsdataverse/wehoop-wnba-data) publishes in bulk and stalls: in
August 2026 it froze at 2026-08-01 for five days while games kept being
played, which left every bet on those slates `open` with no result - and,
worse, on course to be voided by settle_bets' "no box row after 3d" rule,
which cannot tell an absent player from an absent feed.

This fetcher archives ESPN's own final box scores (the same source wehoop
scrapes) for dates wehoop is missing, so settlement can proceed. It is a
FALLBACK, not a replacement: `load_espn_box` (settlement) and
`load_espn_panel` (model panel) are consulted only for dates with no
wehoop coverage, and wehoop reclaims those dates the moment it publishes
them.

Schema v2 (owner-directed 2026-08-08, after the August stall left the
model panel frozen for a week while picks kept flowing): each player row
now carries the FULL stat line (made-attempted splits, orebs, fouls),
athlete/team ids and position, and each game carries a `teams` block with
team totals and scores - everything `features.py`/`talent.py` consume, in
wehoop's own id space (wehoop scrapes ESPN, so the ids are identical).
`load_espn_panel` feeds these rows into the panel build for uncovered
dates only; v1 files (settlement subset, no ids) are automatically
ignored by the panel path and still settle fine. `grade_props.py` stays
wehoop-only, and the fp-prospective registrations are untouched: their
season-end evaluation rebuilds everything from wehoop once it has
published, so the fallback changes only what the LIVE sheet can see.

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

SCHEMA = 2  # v2 adds ids/positions/attempts/team totals (panel-grade rows)

# ESPN box header -> wehoop player_box column (single-number cells)
STATS = {"MIN": "minutes", "PTS": "points", "REB": "rebounds",
         "AST": "assists", "STL": "steals", "BLK": "blocks",
         "TO": "turnovers", "OREB": "offensive_rebounds",
         "DREB": "defensive_rebounds", "PF": "fouls"}
# "2-5" made-attempted cells -> (made column, attempted column)
SPLITS = {"FG": ("field_goals_made", "field_goals_attempted"),
          "3PT": ("three_point_field_goals_made",
                  "three_point_field_goals_attempted"),
          "FT": ("free_throws_made", "free_throws_attempted")}
COLS = ["game_id", "game_date", "athlete_display_name", "team_abbreviation",
        "minutes", "points", "rebounds", "assists", "steals", "blocks",
        "turnovers", "three_point_field_goals_made", "did_not_play",
        "reason", "starter", "ejected", "active"]
# ESPN team-statistics `name` -> wehoop team_box column (features.py inputs)
TEAM_STATS = {"totalRebounds": "total_rebounds",
              "offensiveRebounds": "offensive_rebounds",
              "defensiveRebounds": "defensive_rebounds",
              "assists": "assists", "steals": "steals", "blocks": "blocks",
              "turnovers": "turnovers", "teamTurnovers": "team_turnovers",
              "totalTurnovers": "total_turnovers", "fouls": "fouls"}
TEAM_SPLITS = {"fieldGoalsMade-fieldGoalsAttempted":
               ("field_goals_made", "field_goals_attempted"),
               "threePointFieldGoalsMade-threePointFieldGoalsAttempted":
               ("three_point_field_goals_made",
                "three_point_field_goals_attempted"),
               "freeThrowsMade-freeThrowsAttempted":
               ("free_throws_made", "free_throws_attempted")}
# player_box columns the model panel consumes (features.py STATS + ids);
# a fallback row must carry every one of these to enter the panel
PANEL_PLAYER_COLS = [
    "game_id", "game_date", "athlete_id", "athlete_display_name",
    "athlete_position_abbreviation", "team_id", "team_abbreviation",
    "team_name", "opponent_team_id", "opponent_team_abbreviation",
    "opponent_team_name", "home_away", "starter", "did_not_play", "reason",
    "ejected", "active", "minutes", "points", "rebounds", "assists",
    "steals", "blocks", "turnovers", "three_point_field_goals_made",
    "three_point_field_goals_attempted", "field_goals_made",
    "field_goals_attempted", "free_throws_made", "free_throws_attempted",
    "offensive_rebounds", "defensive_rebounds", "fouls"]
PANEL_TEAM_COLS = [
    "game_id", "game_date", "team_id", "team_abbreviation", "team_name",
    "opponent_team_id", "opponent_team_abbreviation", "opponent_team_name",
    "team_score", "opponent_team_score", "field_goals_made",
    "field_goals_attempted", "free_throws_made", "free_throws_attempted",
    "three_point_field_goals_made", "three_point_field_goals_attempted",
    "total_turnovers", "offensive_rebounds", "defensive_rebounds",
    "total_rebounds", "assists"]


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


def _split(v):
    """'2-5' -> (2.0, 5.0)."""
    try:
        m, a = str(v).split("-")[:2]
        return float(m), float(a)
    except (TypeError, ValueError, IndexError):
        return float("nan"), float("nan")


def parse_summary(event_id, date):
    """ESPN summary -> (player rows, team rows) in wehoop's shape.

    Player rows carry the full stat line plus athlete/team ids and
    position; team rows carry the totals features.py's team_features
    reads. Both use ESPN ids, which ARE wehoop's ids.
    """
    s = _get(f"{API}/summary?event={event_id}")
    # home/away + finals from the header (boxscore.teams lacks the score)
    sides, scores = {}, {}
    for c in (s.get("header", {}).get("competitions") or [{}])[0] \
            .get("competitors", []):
        tid = str((c.get("team") or {}).get("id"))
        sides[tid] = c.get("homeAway")
        scores[tid] = _num(c.get("score"))

    # team meta + totals, then cross-fill opponent columns
    teams = []
    for t in s.get("boxscore", {}).get("teams", []):
        tm = t.get("team") or {}
        row = {"game_id": int(event_id), "game_date": date,
               "team_id": int(tm.get("id")),
               "team_abbreviation": tm.get("abbreviation"),
               "team_name": tm.get("name"),
               "home_away": sides.get(str(tm.get("id"))),
               "team_score": scores.get(str(tm.get("id")))}
        stats = {st.get("name"): st.get("displayValue")
                 for st in t.get("statistics", [])}
        for name, col in TEAM_STATS.items():
            row[col] = _num(stats.get(name))
        for name, (mcol, acol) in TEAM_SPLITS.items():
            row[mcol], row[acol] = _split(stats.get(name))
        teams.append(row)
    for i, t in enumerate(teams):
        opp = teams[1 - i] if len(teams) == 2 else {}
        t["opponent_team_id"] = opp.get("team_id")
        t["opponent_team_abbreviation"] = opp.get("team_abbreviation")
        t["opponent_team_name"] = opp.get("team_name")
        t["opponent_team_score"] = opp.get("team_score")
    tmeta = {t["team_abbreviation"]: t for t in teams}

    rows = []
    for team in s.get("boxscore", {}).get("players", []):
        abbr = (team.get("team") or {}).get("abbreviation")
        tm = tmeta.get(abbr, {})
        for block in team.get("statistics", []):
            names = block.get("names") or []
            where = {n: i for i, n in enumerate(names)}
            for a in block.get("athletes", []):
                stats = a.get("stats") or []
                dnp = bool(a.get("didNotPlay")) or not stats
                ath = a.get("athlete") or {}

                def cell(header):
                    i = where.get(header)
                    return stats[i] if i is not None and i < len(stats) else None

                row = {
                    "game_id": int(event_id),
                    "game_date": date,
                    "athlete_id": int(ath["id"]) if ath.get("id") else None,
                    "athlete_display_name": ath.get("displayName"),
                    "athlete_position_abbreviation":
                        (ath.get("position") or {}).get("abbreviation"),
                    "team_id": tm.get("team_id"),
                    "team_abbreviation": abbr,
                    "team_name": tm.get("team_name"),
                    "opponent_team_id": tm.get("opponent_team_id"),
                    "opponent_team_abbreviation":
                        tm.get("opponent_team_abbreviation"),
                    "opponent_team_name": tm.get("opponent_team_name"),
                    "home_away": tm.get("home_away"),
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
                for header, (mcol, acol) in SPLITS.items():
                    row[mcol], row[acol] = (float("nan"), float("nan")) \
                        if dnp else _split(cell(header))
                if row["athlete_display_name"]:
                    rows.append(row)
    return rows, teams


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
            if (have.get("schema", 1) >= SCHEMA and sorted(
                    g["event_id"] for g in have.get("games", [])) == want):
                return len(want), sum(len(g["players"]) for g in have["games"]), \
                    "unchanged"
        except Exception:
            pass  # unreadable / pre-v2 archive -> refetch
    games = []
    for e in finals:
        rows, teams = parse_summary(e["id"], date)
        games.append({"event_id": int(e["id"]),
                      "short_name": e.get("shortName"),
                      "utc_tip": e.get("date"),
                      "teams": teams,
                      "players": rows})
    os.makedirs(OUT, exist_ok=True)
    payload = {
        "date": date,
        "schema": SCHEMA,
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


def load_espn_panel(covered=()):
    """Panel-grade fallback rows for dates NOT in `covered` (wehoop's dates).

    Returns (player_box, team_box) DataFrames in wehoop's shape, restricted
    to schema>=2 archives (v1 files lack ids/attempts and cannot enter the
    panel - they remain settlement-only via load_espn_box). Returns
    (None, None) when there is nothing to add. The caller decides whether
    to append; wehoop reclaims a date the moment it publishes it.
    """
    covered = set(covered)
    prows, trows, skipped = [], [], []
    for p in sorted(glob.glob(os.path.join(OUT, "*.json"))):
        date = os.path.basename(p)[:-5]
        if date in covered:
            continue  # wehoop published it - wehoop is the source of record
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if d.get("schema", 1) < 2:
            skipped.append(date)  # settlement-only archive; refetch upgrades it
            continue
        for g in d.get("games", []):
            prows.extend(r for r in g.get("players", [])
                         if r.get("athlete_id") is not None)
            trows.extend(g.get("teams", []))
    if skipped:
        print(f"PANEL_FALLBACK_SKIPPED v1 archive(s) without ids: "
              f"{','.join(skipped)} (run fetch_espn_box.py --force to upgrade)")
    if not prows:
        return None, None
    pbox = pd.DataFrame(prows).reindex(columns=PANEL_PLAYER_COLS)
    tbox = pd.DataFrame(trows).reindex(columns=PANEL_TEAM_COLS)
    for df in (pbox, tbox):
        for c in df.columns:
            if c not in ("game_date", "athlete_display_name",
                         "athlete_position_abbreviation", "team_abbreviation",
                         "team_name", "opponent_team_abbreviation",
                         "opponent_team_name", "home_away", "reason",
                         "starter", "did_not_play", "ejected", "active"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
    return pbox, tbox


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
