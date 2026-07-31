"""Stage A benchmark for the from-scratch programme (PROGRESS.md Market 2).

Scores the market's own devigged open/close against results — the yardstick
the fp model must beat. Eval convention (registered before Stage B):

- population: H/A winner + full open/close odds (excludes NR/ties), from the
  committed aussportsbetting xlsx (OddsPortal multi-book averages)
- outcome: home win; forecast: devigged two-way P(home)
- split by season (Oct-start): dev = 2018-2020 seasons, holdout = 2021-2022
- the OPENER PREDATES THE TOSS: toss / batted-first are unknowable at open
  time and are banned as model inputs

Usage: python3 src/fp_benchmark.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "asb"
EPS = 1e-9
DEV_SEASONS = (2018, 2019, 2020)
HOLDOUT_SEASONS = (2021, 2022)


def ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def devig(oh, oa):
    ph, pa = 1.0 / oh, 1.0 / oa
    return ph / (ph + pa)


def load_all():
    """Full match table (549 rows, 2011-2023), season-stamped."""
    f = sorted(RAW.glob("big_bash_league*.xlsx"))[-1]
    df = pd.read_excel(f, sheet_name="Data", header=1)
    df.columns = [c.replace("\n", " ").strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df["season"] = np.where(df.Date.dt.month >= 10,
                            df.Date.dt.year, df.Date.dt.year - 1)
    return df.sort_values("Date").reset_index(drop=True)


def eval_pop(df):
    """The 297-match open/close benchmark population."""
    keep = df["Winner"].isin(["H", "A"])
    for c in ["Home Odds Open", "Away Odds Open",
              "Home Odds Close", "Away Odds Close"]:
        keep &= pd.to_numeric(df[c], errors="coerce").gt(1.0)
    ev = df[keep].copy()
    ev["home_win"] = (ev.Winner == "H").astype(int)
    ev["p_open"] = devig(ev["Home Odds Open"].astype(float),
                         ev["Away Odds Open"].astype(float))
    ev["p_close"] = devig(ev["Home Odds Close"].astype(float),
                          ev["Away Odds Close"].astype(float))
    return ev


def main():
    df = load_all()
    ev = eval_pop(df)
    print(f"benchmark population: {len(ev)} matches, "
          f"seasons {sorted(ev.season.unique())}")
    for name, seasons in [("dev", DEV_SEASONS), ("holdout", HOLDOUT_SEASONS)]:
        sub = ev[ev.season.isin(seasons)]
        lo = ll(sub.p_open, sub.home_win)
        lc = ll(sub.p_close, sub.home_win)
        print(f"{name} ({seasons}): n={len(sub)}  "
              f"LL(open)={lo.mean():.5f}  LL(close)={lc.mean():.5f}  "
              f"home rate={sub.home_win.mean():.3f}  "
              f"implied={sub.p_open.mean():.3f}")
    coin = ll(np.full(len(ev), 0.5), ev.home_win).mean()
    print(f"coin flip LL = {coin:.5f} (the market's edge over coin is small "
          f"in this league — T20 is high-variance)")


if __name__ == "__main__":
    main()
