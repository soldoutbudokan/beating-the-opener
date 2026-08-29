"""Pre-registered QC gate for the registration-K MLB pitcher training-data
fetch (2015-2022 backfill via fetch_mlb.py, + the 2026 refresh).

Registered in PROGRESS.md ("Registration K", gates registered 2026-08-29,
before any engine code exists) BEFORE the pitcher-talent engine sees any
of this data:

  (a) per-season boxscore coverage >= 98% of that season's scheduled
      finals (schedule rows with status Final/Completed Early and gameType in
      {R, F, D, L, W}) -- coverage = share of those gamePks present in
      pitcher_box_<season>. Every season 2015..2026 found on disk is
      reported.
  (b) independent-aggregation agreement: for a random sample (seed 0) of
      60 pitcher-seasons with >= 5 regular-season appearances in each of
      2016, 2019, 2022, the boxscore-summed (regular season only, via the
      schedule's gameType == "R") season totals of {k, bf, outs} must
      exactly match statsapi's own `stats=season` totals
      (strikeOuts/battersFaced/outs) on >= 98% of cells.

Exit code 0 + QC_PASS only if every gate with data to check passes.
Seasons/years not yet fetched (the 2015-2022 backfill runs in the
background) are reported NOT_ON_DISK and do not fail the gate.
"""
import json
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
MLB = os.path.join(ROOT, "data", "mlb")
API = "https://statsapi.mlb.com/api/v1"
KEEP_TYPES = {"R", "F", "D", "L", "W"}   # regular + postseason rounds
SEASONS = range(2015, 2027)
QC_SEASONS = (2016, 2019, 2022)
SAMPLE = 60
MIN_APP = 5
PAUSE = 0.15
RNG = np.random.default_rng(0)


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retry any transport error
            wait = 2 ** (i + 1)
            print(f"    retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def season_paths(season):
    return (os.path.join(MLB, f"schedule_{season}.parquet"),
            os.path.join(MLB, f"pitcher_box_{season}.parquet"))


def fetch_person_season(pid, season):
    """statsapi `stats=season` totals for one pitcher-season.

    Verified 2026-08-29: a pitcher who stayed with one team all season
    (Gerrit Cole, 2022) returns exactly one split, and that split DOES
    carry a "team" key. A pitcher traded mid-season (Luis Castillo,
    CIN->SEA, 2022) returns THREE splits: splits[0] has no "team" key and
    is the combined-team aggregate (battersFaced 615 = 349 + 266 from the
    two per-team splits that follow). So: prefer the split with no "team"
    key (the aggregate); if every split carries a team (single-team
    season), there is exactly one split and we use it.
    """
    d = get(f"{API}/people/{pid}/stats?stats=season&group=pitching"
            f"&season={season}")
    time.sleep(PAUSE)
    if d is None:
        return None
    stats = d.get("stats") or []
    if not stats:
        return None
    splits = stats[0].get("splits") or []
    if not splits:
        return None
    agg = next((sp for sp in splits if "team" not in sp), None)
    split = agg if agg is not None else splits[0]
    stat = split.get("stat") or {}
    name = (split.get("player") or {}).get("fullName")
    return {"k": stat.get("strikeOuts"), "bf": stat.get("battersFaced"),
            "outs": stat.get("outs"), "name": name}


def qc_coverage():
    print("(a) boxscore coverage vs scheduled finals:")
    ok = True
    for s in SEASONS:
        sched_path, box_path = season_paths(s)
        if not os.path.exists(sched_path):
            print(f"  {s}: NOT_ON_DISK (no schedule file)")
            continue
        sched = pd.read_parquet(sched_path)
        final = sched[sched.status.astype(str).str.startswith(("Final", "Completed Early"))
                      & sched.gameType.isin(KEEP_TYPES)]
        if not os.path.exists(box_path):
            print(f"  {s}: NOT_ON_DISK (no pitcher_box file yet; "
                  f"{len(final)} scheduled finals waiting)")
            continue
        box = pd.read_parquet(box_path)
        n_final = len(final)
        cov = box.gamePk.nunique() / n_final if n_final else float("nan")
        flag = "" if cov >= 0.98 else "  <-- FAIL"
        ok &= bool(n_final == 0 or cov >= 0.98)
        print(f"  {s}: {box.gamePk.nunique()}/{n_final} = {cov:.4f}{flag}")
    return ok


def qc_agreement():
    print("(b) independent-aggregation agreement vs statsapi stats=season:")
    ok = True
    for s in QC_SEASONS:
        sched_path, box_path = season_paths(s)
        if not (os.path.exists(sched_path) and os.path.exists(box_path)):
            print(f"  {s}: NOT_ON_DISK (backfill hasn't reached this "
                  f"season yet) - skipped, not counted against the gate")
            continue

        sched = pd.read_parquet(sched_path)
        box = pd.read_parquet(box_path)
        reg_games = set(sched.loc[sched.gameType == "R", "gamePk"])
        reg = box[box.gamePk.isin(reg_games)].copy()

        counts = reg.groupby("pid").size()
        eligible = counts[counts >= MIN_APP].index.to_numpy()
        if len(eligible) == 0:
            print(f"  {s}: no eligible pitcher-seasons (>= {MIN_APP} "
                  f"regular-season appearances) - skipped")
            continue

        n = min(SAMPLE, len(eligible))
        sample_pids = RNG.choice(eligible, size=n, replace=False)

        totals = (reg[reg.pid.isin(sample_pids)]
                  .groupby("pid")[["k", "bf", "outs"]].sum())
        names = reg.drop_duplicates("pid").set_index("pid")["name"]

        cells = agree = fetch_fails = 0
        mismatches = []
        for pid in sample_pids:
            pid = int(pid)
            theirs = fetch_person_season(pid, s)
            if theirs is None:
                fetch_fails += 1
                continue
            ours_row = totals.loc[pid]
            name = names.get(pid, theirs.get("name"))
            for stat in ("k", "bf", "outs"):
                ov = ours_row[stat]
                tv = theirs[stat]
                cells += 1
                match = (pd.notna(ov) and tv is not None
                          and int(ov) == int(tv))
                if match:
                    agree += 1
                else:
                    ov_disp = None if pd.isna(ov) else int(ov)
                    mismatches.append((pid, name, stat, ov_disp, tv))

        rate = agree / cells if cells else 0.0
        flag = "" if rate >= 0.98 else "  <-- FAIL"
        season_ok = cells > 0 and rate >= 0.98
        ok &= season_ok
        print(f"  {s}: sampled {n}/{len(eligible)} eligible "
              f"pitcher-seasons ({fetch_fails} fetch failures); "
              f"cell match {agree}/{cells} = {rate:.4f}{flag}")
        for pid, name, stat, ov, tv in mismatches:
            print(f"    MISMATCH pid={pid} name={name!r} stat={stat} "
                  f"ours={ov} theirs={tv}")
    return ok


def main():
    ok_a = qc_coverage()
    ok_b = qc_agreement()
    ok = ok_a and ok_b
    print("QC_PASS" if ok else "QC_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
