"""Combine raw football-data.co.uk CSVs into one clean match table.

Output: data/matches.pkl with results + early odds + closing odds.
"""
import glob
import os

import numpy as np
import pandas as pd

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "matches.pkl")

KEEP = [
    "Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    # early (collected Fri/Tue) odds
    "B365H", "B365D", "B365A", "PSH", "PSD", "PSA",
    "MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA",
    "BbMxH", "BbMxD", "BbMxA", "BbAvH", "BbAvD", "BbAvA",
    # closing odds
    "PSCH", "PSCD", "PSCA", "B365CH", "B365CD", "B365CA",
    "MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA",
]


def load_one(path):
    fname = os.path.basename(path)
    season = fname.split("_")[0]
    try:
        df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
    except Exception as e:
        print(f"read fail {fname}: {e}")
        return None
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed")]]
    for col in KEEP:
        if col not in df.columns:
            df[col] = np.nan
    df = df[KEEP]
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTR"])
    df = df[df["FTR"].isin(["H", "D", "A"])]
    df["season"] = "20" + season[:2] + "-" + season[2:]
    return df


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.csv")))
    print(f"{len(files)} raw files")
    parts = [load_one(p) for p in files]
    df = pd.concat([p for p in parts if p is not None and len(p)], ignore_index=True)

    df["Date"] = pd.to_datetime(df["Date"], format="mixed", dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values(["Date", "Div", "HomeTeam"]).reset_index(drop=True)

    for c in KEEP[5:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df.loc[df[c] <= 1.0, c] = np.nan  # decimal odds must exceed 1

    # unified early max/avg: Betbrain pre-2019, Market Max/Avg after
    for side in "HDA":
        df[f"EMax{side}"] = df[f"Max{side}"].fillna(df[f"BbMx{side}"])
        df[f"EAvg{side}"] = df[f"Avg{side}"].fillna(df[f"BbAv{side}"])

    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    df = df.dropna(subset=["FTHG", "FTAG"])

    df["has_ps_early"] = df[["PSH", "PSD", "PSA"]].notna().all(axis=1)
    df["has_ps_close"] = df[["PSCH", "PSCD", "PSCA"]].notna().all(axis=1)

    df.to_pickle(OUT)
    print(f"total matches: {len(df)}")
    print("\ncoverage by season:")
    cov = df.groupby("season").agg(
        n=("Div", "size"),
        ps_early=("has_ps_early", "sum"),
        ps_close=("has_ps_close", "sum"),
    )
    print(cov.to_string())
    print("\nmatches with BOTH early+close PS:",
          int((df["has_ps_early"] & df["has_ps_close"]).sum()))


if __name__ == "__main__":
    main()
