"""Parse Cricsheet ball-by-ball JSON zips into match + delivery tables.

Input: data/raw/cricsheet/{bbl,ipl,psl,cpl,ntb,t20s}_json.zip, downloaded
from https://cricsheet.org/downloads/<comp>_json.zip (stable upstream, so
the zips and parsed parquets are gitignored — re-download any time; this
script records the exact source).

Output (gitignored):
  data/matches_cs.parquet    one row per match: comp, date, teams, venue,
                             city, winner, toss, XIs (as lists)
  data/deliveries_cs.parquet one row per delivery: match_id, innings,
                             batter, bowler, runs_batter, runs_total,
                             wicket (0/1, credited-to-bowler only)

Usage: python3 src/fp_ingest.py
"""
import io
import json
import os
import zipfile

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAWDIR = os.path.join(ROOT, "data", "raw", "cricsheet")
COMPS = ["bbl", "ipl", "psl", "cpl", "ntb", "t20s"]
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
        "comp": comp,
        "date": info["dates"][0],
        "team1": teams[0], "team2": teams[1],
        "venue": info.get("venue"), "city": info.get("city"),
        "winner": outcome.get("winner"),
        "result": outcome.get("result", "normal"),
        "toss_winner": info.get("toss", {}).get("winner"),
        "xi1": info.get("players", {}).get(teams[0], []),
        "xi2": info.get("players", {}).get(teams[1], []),
    }
    dels = []
    for inn_i, inn in enumerate(j.get("innings", [])):
        batting = inn.get("team")
        for over in inn.get("overs", []):
            for d in over.get("deliveries", []):
                wick = 0
                for w in d.get("wickets", []):
                    if w.get("kind") in BOWLER_WICKETS:
                        wick = 1
                dels.append((row["match_id"], inn_i + 1, batting,
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
    d = pd.DataFrame(dels, columns=["match_id", "innings", "batting_team",
                                    "batter", "bowler", "runs_batter",
                                    "runs_total", "wicket"])
    m.to_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    d.to_parquet(os.path.join(ROOT, "data", "deliveries_cs.parquet"))
    print(f"total: {len(m)} matches, {len(d)} deliveries "
          f"({m.date.min()} .. {m.date.max()})")


if __name__ == "__main__":
    main()
