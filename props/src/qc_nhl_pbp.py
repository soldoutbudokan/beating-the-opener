"""Pre-registered QC gate for the registration-N2 pbp attempts data.

Registered in PROGRESS.md (registration N2, 2026-08-24) BEFORE any N2
engine code may tune:
  (a) pbp coverage >= 98% of fetched (boxscore) games per season;
  (b) on seasons 2013-14, 2018-19, 2022-23, 2025-26: pbp-derived SOG
      (goal + shot-on-goal events) equals boxscore sog on >= 97% of
      skater-game rows, and attempts >= boxscore sog on >= 99.9%.
"""
import os
import sys

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
QC_SEASONS = (2013, 2018, 2022, 2025)


def box_path(season):
    sub = "nhl_hist" if season <= 2023 else "nhl"
    return os.path.join(ROOT, "data", sub, f"skater_box_{season}.parquet")


def main():
    ok = True
    print("(a) pbp coverage vs boxscore games:")
    for s in range(2010, 2026):
        box = pd.read_parquet(box_path(s))
        att = pd.read_parquet(os.path.join(ROOT, "data", "nhl_pbp",
                                           f"attempts_{s}.parquet"))
        cov = att.game_id.nunique() / box.game_id.nunique()
        flag = "" if cov >= 0.98 else "  <-- FAIL"
        ok &= cov >= 0.98
        print(f"  {s}: {att.game_id.nunique()}/{box.game_id.nunique()} "
              f"= {cov:.4f}{flag}")
    print("(b) identities on registered seasons:")
    for s in QC_SEASONS:
        box = pd.read_parquet(box_path(s))
        att = pd.read_parquet(os.path.join(ROOT, "data", "nhl_pbp",
                                           f"attempts_{s}.parquet"))
        j = box.merge(att[["game_id", "pid", "attempts", "sog_pbp"]],
                      on=["game_id", "pid"], how="left")
        j[["attempts", "sog_pbp"]] = j[["attempts", "sog_pbp"]].fillna(0)
        ident = float((j.sog == j.sog_pbp).mean())
        geq = float((j.attempts >= j.sog).mean())
        f1 = "" if ident >= 0.97 else "  <-- FAIL sog identity"
        f2 = "" if geq >= 0.999 else "  <-- FAIL attempts>=sog"
        ok &= (ident >= 0.97) and (geq >= 0.999)
        print(f"  {s}: sog identity {ident:.4f}{f1}; "
              f"attempts>=sog {geq:.4f}{f2}; "
              f"att/sog ratio {j.attempts.sum()/j.sog.sum():.2f}")
    print("QC_PASS" if ok else "QC_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
