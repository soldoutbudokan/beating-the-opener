"""Dual-source game-score validation (Phase 1G QC gate).

BP event results vs a native source; only agreeing events survive into
data/game_scores_<sport>.pkl. Disagreements are dropped, never adjudicated.

  NHL:  api-web official finals (data/nhl/finals_2025.parquet, SO-inclusive)
        joined via the existing crosswalk data/event_map_nhl.pkl.
  WNBA: wehoop schedule scores (wnba/data/wehoop/), joined by ET date +
        a learned BP->wehoop abbr map (map_events.learn_code_map,
        bijection-checked; count >= 5 to shed one-off All-Star codes).

Gate: >= 99% agreement on completed events.

Usage: python3 src/qc_game_scores.py --sport NHL|WNBA
"""
import argparse
import glob
import os

import pandas as pd

from map_events import learn_code_map

ROOT = os.path.join(os.path.dirname(__file__), "..")
WNBA_ROOT = os.path.join(ROOT, "..", "wnba")


def nhl():
    ev = pd.read_pickle(os.path.join(ROOT, "data", "events_nhl.pkl"))
    emap = pd.read_pickle(os.path.join(ROOT, "data", "event_map_nhl.pkl"))
    fin = pd.read_parquet(os.path.join(ROOT, "data", "nhl", "finals_2025.parquet"))
    ev = ev.merge(emap[["event_id", "native_id"]], on="event_id")
    j = ev.merge(fin, left_on="native_id", right_on="game_id",
                 suffixes=("_bp", "_nat"))
    return j, j.home_score_bp, j.visitor_score, j.home_score_nat, j.away_score

def wnba():
    ev = pd.read_pickle(os.path.join(WNBA_ROOT, "data", "events.pkl"))
    sch = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(
        os.path.join(WNBA_ROOT, "data", "wehoop", "wnba_schedule_202[56].parquet")))],
        ignore_index=True)
    sch = sch[sch.status_type_completed == True].copy()
    sch["et_date"] = sch.game_date.astype(str).str[:10]
    nat = sch.rename(columns={"home_abbreviation": "home",
                              "away_abbreviation": "away"})
    cmap = learn_code_map(ev[ev.home_score.notna()], nat)
    # learn_code_map has no frequency floor; re-count and keep robust entries
    # so one-off exhibition codes (All-Star teams) can't smuggle in a bad map
    counts = {}
    nat_by_date = {}
    for r in nat.itertuples():
        nat_by_date.setdefault(r.et_date, []).append(r)
    for r in ev.itertuples():
        for n in nat_by_date.get(r.date, []):
            for b, c in ((r.home, n.home), (r.visitor, n.away)):
                counts[(b, c)] = counts.get((b, c), 0) + 1
    cmap = {b: n for b, n in cmap.items() if counts.get((b, n), 0) >= 5}
    print(f"abbr map ({len(cmap)} teams): {cmap}")
    ev = ev[ev.home.isin(cmap) & ev.visitor.isin(cmap)].copy()
    ev["nat_home"], ev["nat_away"] = ev.home.map(cmap), ev.visitor.map(cmap)
    j = ev.merge(nat[["et_date", "home", "away", "home_score", "away_score"]],
                 left_on=["date", "nat_home", "nat_away"],
                 right_on=["et_date", "home", "away"],
                 suffixes=("_bp", "_nat"))
    assert not j.duplicated("event_id").any()
    return j, j.home_score_bp, j.visitor_score, j.home_score_nat, j.away_score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=["NHL", "WNBA"])
    args = ap.parse_args()
    j, bp_h, bp_v, nat_h, nat_v = {"NHL": nhl, "WNBA": wnba}[args.sport]()

    both = bp_h.notna() & nat_h.notna()
    agree = both & (bp_h.astype(float) == nat_h.astype(float)) \
                 & (bp_v.astype(float) == nat_v.astype(float))
    n_completed = int(both.sum())
    print(f"{args.sport}: {len(j)} events joined to a native final, "
          f"{n_completed} with scores at both sources")
    print(f"  agreement: {agree.sum()}/{n_completed} "
          f"({agree.sum() / n_completed:.2%})  [gate >= 99%]")
    if "last_period" in j:
        so = j.last_period == "SO"
        so_agree = (agree & so).sum()
        print(f"  SO games: {so.sum()}, agreeing: {so_agree} "
              f"(checks BP uses the SO-inclusive official final)")
    bad = j[both & ~agree]
    if len(bad):
        cols = [c for c in ("date", "home_bp", "home", "visitor",
                            "home_score_bp", "home_score_nat",
                            "visitor_score", "away_score") if c in bad]
        print("  disagreements (dropped):")
        print(bad[cols].head(10).to_string())

    out = j[agree][["event_id"]].copy()
    out["home_score"] = nat_h[agree].astype(float)
    out["away_score"] = nat_v[agree].astype(float)
    path = os.path.join(ROOT, "data", f"game_scores_{args.sport.lower()}.pkl")
    out.to_pickle(path)
    print(f"wrote {path} ({len(out)} validated events)")


if __name__ == "__main__":
    main()
