"""Pre-registered QC gate for the registration-N training-era NHL fetch.

Registered in PROGRESS.md (Market 4 revisit, 2026-08-24) BEFORE the fetch
was used: the talent engine may not tune until this passes.

  (a) coverage: every season 2010..2023, final games in skater_box >= 98%
      of that season's api-web schedule (gameType 2+3, state OFF/FINAL);
  (b) dual-source agreement vs the independent fastRhockey-data mirror
      parquet (data/nhl_hist/player_box_<season+1>.parquet) on a random
      300-skater-game sample in each of 2013-14, 2018-19, 2022-23:
      exact match >= 98% of {goals, assists, sog, blocked} cells,
      toi within 0.5 min on >= 95% of rows.

Exit code 0 + QC_PASS only if every gate passes.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
HIST = os.path.join(ROOT, "data", "nhl_hist")
SEASONS = range(2010, 2024)
QC_SEASONS = (2013, 2018, 2022)     # 2013-14, 2018-19, 2022-23
SAMPLE = 300
RNG = np.random.default_rng(20260824)


def toi_min(s):
    t = s.fillna("00:00").astype(str).str.split(":", expand=True)
    return (pd.to_numeric(t[0], errors="coerce").fillna(0)
            + pd.to_numeric(t[1], errors="coerce").fillna(0) / 60)


def main():
    ok = True

    print("(a) coverage vs api-web schedule:")
    for s in SEASONS:
        sched = pd.read_parquet(os.path.join(HIST, f"schedule_{s}.parquet"))
        box = pd.read_parquet(os.path.join(HIST, f"skater_box_{s}.parquet"))
        final = sched[sched.state.isin(["OFF", "FINAL"])]
        cov = box.game_id.nunique() / len(final)
        flag = "" if cov >= 0.98 else "  <-- FAIL"
        ok &= cov >= 0.98
        print(f"  {s}: {box.game_id.nunique()}/{len(final)} = {cov:.4f}{flag}")

    print("(b) dual-source agreement vs fastRhockey mirror:")
    mirror_cols = {"skater_stats_goals": "goals",
                   "skater_stats_assists": "assists",
                   "skater_stats_shots": "sog",
                   "skater_stats_blocked": "blocked"}
    for s in QC_SEASONS:
        box = pd.read_parquet(os.path.join(HIST, f"skater_box_{s}.parquet"))
        mir = pd.read_parquet(os.path.join(HIST,
                                           f"player_box_{s + 1}.parquet"))
        mir = mir[mir.skater_stats_time_on_ice.notna()].copy()
        mir = mir.rename(columns=mirror_cols)
        mir["toi_min_mir"] = toi_min(mir.skater_stats_time_on_ice)
        mir = mir[["game_id", "player_id"] + list(mirror_cols.values())
                  + ["toi_min_mir"]]
        box = box[box.toi.notna()].copy()
        box["toi_min"] = toi_min(box.toi)
        idx = RNG.choice(len(box), size=min(SAMPLE, len(box)), replace=False)
        sample = box.iloc[idx]
        j = sample.merge(mir, left_on=["game_id", "pid"],
                         right_on=["game_id", "player_id"],
                         suffixes=("", "_mir"))
        matched = len(j) / len(sample)
        cells = agree = 0
        for c in mirror_cols.values():
            a = pd.to_numeric(j[c], errors="coerce")
            b = pd.to_numeric(j[f"{c}_mir"], errors="coerce")
            cells += len(j)
            agree += int((a == b).sum())
        cell_rate = agree / cells if cells else 0.0
        toi_ok = float((np.abs(j.toi_min - j.toi_min_mir) <= 0.5).mean())
        f1 = "" if cell_rate >= 0.98 else "  <-- FAIL cells"
        f2 = "" if toi_ok >= 0.95 else "  <-- FAIL toi"
        ok &= (cell_rate >= 0.98) and (toi_ok >= 0.95)
        print(f"  {s}: joined {len(j)}/{len(sample)} ({matched:.2%}); "
              f"stat cells {cell_rate:.4f}{f1}; toi<=0.5min {toi_ok:.4f}{f2}")

    print("QC_PASS" if ok else "QC_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
