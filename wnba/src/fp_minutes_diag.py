"""POST-HOC DIAGNOSTIC (never claimable): how much of the dev-2025 gap
to the opener is minutes error vs per-minute-rate error?

Reprices dev 2025 with the pinned fp-prospective-2 configuration
(v1-frozen calibration + talent rates) three ways:
  A. as-is — reproduces the registered dev number.
  B. oracle MINUTES — the minutes estimate replaced by ACTUAL minutes,
     per-game EW components scaled by the same ratio (exactly the
     fp_live.py override mechanism, so this measures what a perfect
     minutes feed through the existing machinery would buy).
  C. oracle RATES — talent/per-game components replaced by the actual
     per-minute production that game (the opposite leak). This is close
     to knowing the outcome, so it bounds total variance, not an
     addressable edge — pre-game information about realized shooting
     variance does not exist, whereas pre-game information about
     minutes (availability, starters, rotations) does.

Both oracles read tonight's box score and are leakage by construction.
Numbers from this script are diagnostics for build prioritisation only.

Usage: python3 src/fp_minutes_diag.py   (after the pipeline + talent.pkl)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import fp_model as fp
from build_modelset import norm
from fp_benchmark import ll, clustered_t

ROOT = os.path.join(os.path.dirname(__file__), "..")

panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
tal = pd.read_pickle(os.path.join(ROOT, "data", "talent.pkl"))
panel = panel.merge(tal, on=["athlete_id", "game_id"], how="left")

ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
ms = ms[ms.matched & ~ms.void & ms.open_coherent
        & (ms.actual != ms.open_line)].copy()
ms["over"] = (ms.actual > ms.open_line).astype(int)

pm = panel.assign(nname=panel.athlete_display_name.map(norm),
                  dstr=pd.to_datetime(panel.game_date).dt.strftime("%Y-%m-%d"))
tcols = [c for c in tal.columns if c.startswith("talent_")]
tmap = pm.groupby(["nname", "dstr"])[tcols].max().reset_index()
ms["dstr"] = pd.to_datetime(ms.date).dt.strftime("%Y-%m-%d")
ms = ms.merge(tmap, on=["nname", "dstr"], how="left")

acols = ["minutes"] + list(fp.RAW.values())
amap = pm.groupby(["nname", "dstr"])[acols].max().reset_index()
amap.columns = ["nname", "dstr"] + ["act_" + c for c in acols]
ms = ms.merge(amap, on=["nname", "dstr"], how="left")

sub = ms[ms.season == 2025].copy()
cal = fp.fit_play_cal(panel, "2025-01-01")


def score(df, label):
    d = df.copy()
    d["mu_model"] = fp.predict(d, cal)
    d = d[d.mu_model.notna()].copy()
    d["p_model"] = [fp.p_over(m, mu, li, cal) for m, mu, li in
                    zip(d.market, d.mu_model, d.open_line)]
    d["ll_model"] = ll(d.p_model, d.over)
    d["ll_open"] = ll(d.p_open, d.over)
    g, t = clustered_t((d.ll_model - d.ll_open).values, d.date)
    calib = d.p_model.mean() - d.over.mean()
    print(f"{label:28s} n={len(d):5d}  model-open={g:+.5f} (t={t:.1f})  "
          f"cal={calib*100:+.2f}pp")
    aug = d[pd.to_datetime(d.date) >= "2025-08-01"]
    ga, ta = clustered_t((aug.ll_model - aug.ll_open).values, aug.date)
    print(f"{'':28s} Aug-Oct n={len(aug):5d}  gap={ga:+.5f} (t={ta:.1f})")
    return d


base = score(sub, "A. as-is (pinned model)")

ok = sub.act_minutes.notna() & (sub.act_minutes > 0)
usual = (fp.W_FAST * sub.min_ewf + (1 - fp.W_FAST) * sub.min_ews)\
    .fillna(sub.min_ewf)

orc = sub.copy()
ratio = (orc.act_minutes / usual.clip(lower=1.0)).where(ok, 1.0)
for st in fp.RAW:
    for tag in ("_ewf", "_ews"):
        c = f"{st}{tag}"
        if c in orc.columns:
            orc[c] = orc[c] * ratio
orc.loc[ok, "min_ewf"] = orc.act_minutes[ok]
orc.loc[ok, "min_ews"] = orc.act_minutes[ok]
score(orc[ok], "B. oracle MINUTES")
score(sub[ok], "A' as-is, same rows")

orc2 = sub.copy()
for st, col in fp.RAW.items():
    act_rate = (orc2["act_" + col] / orc2.act_minutes.clip(lower=1.0))\
        .where(ok)
    orc2[f"talent_{st}"] = act_rate.fillna(orc2[f"talent_{st}"])
    for tag in ("_ewf", "_ews"):
        c = f"{st}{tag}"
        if c in orc2.columns:
            orc2[c] = orc2["act_" + col].where(ok, orc2[c])
score(orc2[ok], "C. oracle RATES (min as-is)")

mhat = usual[ok]
mact = sub.act_minutes[ok]
err = mact - mhat
print(f"\nminutes blend on dev prop rows: MAE={err.abs().mean():.2f} min, "
      f"bias={err.mean():+.2f}, sd={err.std():.2f}")
d = base[base.index.isin(sub[ok].index)].copy()
d["min_err"] = (sub.act_minutes - usual).abs()
d["bucket"] = pd.cut(d.min_err, [0, 2, 4, 7, 60],
                     labels=["0-2", "2-4", "4-7", "7+"])
print("\nLL(model)-LL(open) by |minutes error| (as-is model):")
for b, g in d.groupby("bucket", observed=True):
    print(f"  {b:4s} n={len(g):5d}  "
          f"gap={g.ll_model.mean()-g.ll_open.mean():+.5f}")
