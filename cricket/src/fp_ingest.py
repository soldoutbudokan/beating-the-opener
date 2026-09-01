"""Parse Cricsheet ball-by-ball JSON zips into match + delivery tables.

Input: data/raw/cricsheet/{bbl,ipl,psl,cpl,ntb,t20s,hnd,sat,ilt,mlc,wpl,lpl,bpl,ssm}_json.zip, downloaded
from https://cricsheet.org/downloads/<comp>_json.zip (stable upstream, so
the zips and parsed parquets are gitignored — re-download any time; this
script records the exact source).

Output (gitignored):
  data/matches_cs.parquet    one row per match: comp, date, teams, venue,
                             city, winner, toss, XIs (as lists)
  data/deliveries_cs.parquet one row per delivery: match_id, innings, over
                             (0-indexed), batter, bowler, runs_batter,
                             runs_total, wicket (0/1, credited-to-bowler only)

Usage: python3 src/fp_ingest.py
"""
import io
import json
import os
import zipfile

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAWDIR = os.path.join(ROOT, "data", "raw", "cricsheet")
COMPS = ["bbl", "ipl", "psl", "cpl", "ntb", "t20s",
         # added 2026-08-29 for the Polymarket benchmark (all T20 leagues it lists)
         "hnd", "sat", "ilt", "mlc", "wpl", "lpl", "bpl", "ssm", "wbb"]
BOWLER_WICKETS = {"bowled", "caught", "lbw", "stumped", "caught and bowled",
                  "hit wicket"}


def parse_match(comp, mid, blob):
    j = json.loads(blob)
    info = j["info"]
    if info.get("match_type") != "T20":
        return None, []
    teams = info["teams"]
    outcome = info.get("outcome", {})
    row = {
        "match_id": f"{comp}_{mid}",
        "comp": comp, "gender": info.get("gender"),
        "date": info["dates"][0],
        "team1": teams[0], "team2": teams[1],
        "venue": info.get("venue"), "city": info.get("city"),
        "winner": outcome.get("winner"),
        "result": outcome.get("result", "normal"),
        "toss_winner": info.get("toss", {}).get("winner"),
        "stage": (info.get("event") or {}).get("stage"),
        "event_name": (info.get("event") or {}).get("name"),
        "match_number": (info.get("event") or {}).get("match_number"),
        # Blast North/South, WC groups (labels are mixed str/int upstream)
        "group": (None if (info.get("event") or {}).get("group") is None
                  else str((info.get("event") or {}).get("group"))),
        "xi1": info.get("players", {}).get(teams[0], []),
        "xi2": info.get("players", {}).get(teams[1], []),
    }
    dels = []
    for inn_i, inn in enumerate(j.get("innings", [])):
        batting = inn.get("team")
        for over in inn.get("overs", []):
            ov = over.get("over", 0)
            for d in over.get("deliveries", []):
                wick = 0
                for w in d.get("wickets", []):
                    if w.get("kind") in BOWLER_WICKETS:
                        wick = 1
                dels.append((row["match_id"], inn_i + 1, ov, batting,
                             d["batter"], d["bowler"],
                             d["runs"]["batter"], d["runs"]["total"], wick))
    return row, dels


def main():
    matches, dels = [], []
    for comp in COMPS:
        path = os.path.join(RAWDIR, f"{comp}_json.zip")
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.endswith(".json")]
        n_ok = 0
        for n in names:
            row, d = parse_match(comp, n[:-5], z.read(n))
            if row is None:
                continue
            matches.append(row)
            dels.extend(d)
            n_ok += 1
        print(f"{comp}: {n_ok} T20 matches parsed")
    m = pd.DataFrame(matches).sort_values("date").reset_index(drop=True)
    d = pd.DataFrame(dels, columns=["match_id", "innings", "over",
                                    "batting_team", "batter", "bowler",
                                    "runs_batter", "runs_total", "wicket"])
    m.to_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    d.to_parquet(os.path.join(ROOT, "data", "deliveries_cs.parquet"))
    print(f"total: {len(m)} matches, {len(d)} deliveries "
          f"({m.date.min()} .. {m.date.max()})")




# ---- extra international results (no deliveries): cross-format Elo food ----
EXTRA = ["odis", "odms", "it20s"]          # ODI / other one-day / unofficial T20I
EXTRA_TYPES = {"ODI", "ODM", "IT20", "T20"}


def parse_extra(comp, mid, blob):
    j = json.loads(blob)
    info = j["info"]
    if info.get("match_type") not in EXTRA_TYPES:
        return None
    if info.get("team_type") != "international":
        return None
    teams = info["teams"]
    outcome = info.get("outcome", {})
    return {"match_id": f"x_{comp}_{mid}", "comp": comp,
            "gender": info.get("gender"), "date": info["dates"][0],
            "team1": teams[0], "team2": teams[1], "city": info.get("city"),
            "winner": outcome.get("winner"),
            "result": outcome.get("result", "normal"),
            "format": info.get("match_type")}


def build_extra():
    rows = []
    for comp in EXTRA:
        path = os.path.join(RAWDIR, f"{comp}_json.zip")
        if not os.path.exists(path):
            continue
        z = zipfile.ZipFile(path)
        n0 = len(rows)
        for n in [x for x in z.namelist() if x.endswith(".json")]:
            r = parse_extra(comp, n[:-5], z.read(n))
            if r is not None:
                rows.append(r)
        print(f"extra {comp}: {len(rows)-n0} international results")
    df = pd.DataFrame(rows)
    df.to_parquet(os.path.join(ROOT, "data", "matches_extra.parquet"))
    print(f"matches_extra.parquet: {len(df)} rows")


if __name__ == "__main__":
    main()
    build_extra()
