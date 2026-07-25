"""Settle logged bets: results, P&L, CLV vs the closing line, RESULTS.md.

Run after live_pipeline.py (needs a fresh data/matches.pkl).
"""
import json
import os

import numpy as np
import pandas as pd

from odds_utils import devig_shin

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
BETS = os.path.join(LIVE, "bets.csv")
BANKROLL = os.path.join(LIVE, "bankroll.json")
RESULTS_MD = os.path.join(ROOT, "RESULTS.md")

COLS = ["key", "placed_at", "match_date", "div", "home", "away", "side",
        "odds_taken", "stake", "model_p", "status", "result", "clv",
        "clv_source", "pnl", "notes"]
SIDE_IDX = {"H": 0, "D": 1, "A": 2}


def close_prob(row, side):
    ps = row[["PSCH", "PSCD", "PSCA"]].to_numpy(float).reshape(1, 3)
    avg = row[["AvgCH", "AvgCD", "AvgCA"]].to_numpy(float).reshape(1, 3)
    if not np.isnan(ps).any():
        return devig_shin(ps)[0, SIDE_IDX[side]], "PS"
    if not np.isnan(avg).any():
        return devig_shin(avg)[0, SIDE_IDX[side]], "Avg"
    return np.nan, ""


def main():
    os.makedirs(LIVE, exist_ok=True)
    if not os.path.exists(BETS):
        pd.DataFrame(columns=COLS).to_csv(BETS, index=False)
    bets = pd.read_csv(BETS)
    for c in ["result", "clv_source", "notes"]:
        if c in bets.columns:
            bets[c] = bets[c].astype("object")
    for c in ["odds_taken", "stake", "model_p", "clv", "pnl"]:
        if c in bets.columns:
            bets[c] = pd.to_numeric(bets[c], errors="coerce")
    if not os.path.exists(BANKROLL):
        json.dump({"start": 100.0, "current": 100.0}, open(BANKROLL, "w"))

    matches = pd.read_pickle(os.path.join(ROOT, "data", "matches.pkl"))
    matches["_date"] = matches["Date"].dt.date.astype(str)

    n_settled = 0
    for idx, b in bets.iterrows():
        if b["status"] != "open":
            continue
        m = matches[(matches["Div"] == b["div"]) & (matches["_date"] == str(b["match_date"]))
                    & (matches["HomeTeam"] == b["home"]) & (matches["AwayTeam"] == b["away"])]
        if len(m) == 0:
            days = (pd.Timestamp.now() - pd.Timestamp(b["match_date"])).days
            if days > 7:
                bets.loc[idx, "notes"] = "no result after 7d - postponed? check"
            continue
        m = m.iloc[0]
        won = m["FTR"] == b["side"]
        bets.loc[idx, "status"] = "settled"
        bets.loc[idx, "result"] = "won" if won else "lost"
        bets.loc[idx, "pnl"] = round(
            b["stake"] * (b["odds_taken"] - 1) if won else -b["stake"], 2)
        pc, src = close_prob(m, b["side"])
        if not np.isnan(pc):
            bets.loc[idx, "clv"] = round(pc * b["odds_taken"] - 1, 4)
            bets.loc[idx, "clv_source"] = src
        n_settled += 1

    bets.to_csv(BETS, index=False)
    settled = bets[bets["status"] == "settled"]
    start = json.load(open(BANKROLL))["start"]
    current = round(start + settled["pnl"].sum(), 2) if len(settled) else start
    json.dump({"start": start, "current": current,
               "updated": str(pd.Timestamp.now())}, open(BANKROLL, "w"))

    # ---- RESULTS.md ----
    lines = ["# Live FanDuel experiment - results\n",
             "Quarter-Kelly, $100 starting bankroll, picks from the "
             "[beating-the-opener model](README.md). CLV = closing-line value: "
             "`p_close x odds_taken - 1`. Positive mean CLV means the bets "
             "systematically beat the closing price - the fast-converging measure "
             "of whether the edge is real (ROI needs several seasons to separate "
             "from luck; CLV needs one).\n"]
    n, no = len(settled), int((bets["status"] == "open").sum())
    if n:
        staked = settled["stake"].sum()
        pnl = settled["pnl"].sum()
        wins = int((settled["result"] == "won").sum())
        clv = settled["clv"].dropna()
        exp_pnl = (settled["stake"] * settled["clv"]).sum()
        lines += [
            f"**Bankroll: ${current:.2f}** (start $100)\n",
            f"| metric | value |", "|---|---|",
            f"| settled bets | {n} ({wins}W-{n - wins}L), {no} open |",
            f"| total staked | ${staked:.2f} |",
            f"| actual P&L | ${pnl:+.2f} ({pnl / staked:+.1%} ROI) |",
            f"| mean CLV | {clv.mean():+.2%} (n={len(clv)}) |",
            f"| CLV-expected P&L | ${exp_pnl:+.2f} |", ""]
        if len(clv) >= 2:
            se = clv.std() / np.sqrt(len(clv))
            lines.append(f"CLV t-stat: {clv.mean() / se:.2f}\n")
    else:
        lines.append(f"No settled bets yet ({no} open).\n")

    show = bets.sort_values("match_date", ascending=False).head(200)
    if len(show):
        lines.append("| date | match | side | odds | stake | result | P&L | CLV |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for _, b in show.iterrows():
            pnl_s = f"{b['pnl']:+.2f}" if pd.notna(b["pnl"]) else ""
            clv_s = f"{float(b['clv']):+.1%}" if pd.notna(b["clv"]) else ""
            res_s = b["result"] if pd.notna(b["result"]) else ""
            lines.append(f"| {b['match_date']} | {b['div']} {b['home']} v {b['away']} "
                         f"| {b['side']} | {b['odds_taken']} | {b['stake']} "
                         f"| {res_s} | {pnl_s} | {clv_s} |")
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"bankroll ${current:.2f}; {no} open bets")
    print(f"SETTLED {n_settled}" if n_settled else "NOTHING_TO_SETTLE")


if __name__ == "__main__":
    main()
