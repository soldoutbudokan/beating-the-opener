"""Score `pm-prospective-1` (registration P/Q): the cricket model's claim
generator.

Dev (the registration-P population, matches resolved <= 2026-08-23) is a
development set that has been touched many times and can never carry a
claim. This script scores ONLY markets that resolved after the model was
locked, rebuilding the benchmark rows the same way pm_benchmark.py does and
pricing them with pm_model2's frozen recipe.

Two arms are registered, one per locked recipe (root PROGRESS.md, Q):
  pm-prospective-1  lock 2026-08-30, recipe `pm_model2.py --no-opp --no-blast-groups`
  pm-prospective-2  lock 2026-09-02, recipe `pm_model2.py` (the defaults)

Run it after refreshing the archive:
    python3 src/fetch_polymarket.py          # new markets + prices
    python3 src/fp_ingest.py                 # new Cricsheet results
    python3 src/pm_model2.py --dev           # writes data/pm_preds_v2.parquet
    python3 src/pm_prospective.py --lock-date 2026-09-02

Reports, per cell and pooled: LL(model) - LL(open T-24h) with date-clustered
t, calibration, LL vs the pre-toss close, flat-stake ROI at the open with the
market's own price as the placebo, and n against the registered evaluation
trigger (n >= 300 or 2027-06-30).
"""
import argparse
import os

import numpy as np
import pandas as pd

from pm_benchmark import clustered_t, ll

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock-date", default="2026-08-30",
                    help="markets resolving after this date count")
    ap.add_argument("--preds", default="data/pm_preds_v2.parquet")
    args = ap.parse_args()
    b = pd.read_parquet(os.path.join(ROOT, args.preds))
    post = b[b.date > args.lock_date].copy()
    print(f"pm-prospective-1: {len(post)} scored markets after {args.lock_date} "
          f"(registered trigger: n >= 300 or 2027-06-30)")
    if not len(post):
        print("nothing to score yet - the arm is accruing")
        return
    post["seg"] = np.where(post.comp == "t20s", "international", "franchise")
    for name, g in list(post.groupby("seg")) + [("POOLED", post)]:
        d, t = clustered_t((g.ll_model - g.ll_open).values, g.date)
        d2, t2 = clustered_t((g.ll_model - g.ll_close).values, g.date)
        print(f"  {name:14s} n={len(g):4d}  model-open={d:+.5f} (t={t:.1f})  "
              f"vs close={d2:+.5f} (t={t2:.1f})  "
              f"cal={100*(g.p_model.mean()-g.y.mean()):+.1f}pp")
    print("\nclaim thresholds (registration Q): both cells < 0.000 and pooled "
          "clustered t <= -1.5")


if __name__ == "__main__":
    main()
