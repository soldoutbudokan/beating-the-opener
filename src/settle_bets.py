"""Settle logged prop bets: actuals, P&L, CLV vs the close, RESULTS.md.

Run after the data refresh (needs fresh wehoop box + BP archive for closes).
Voids on DNP (stake returned), pushes on exact line, CLV from the archived
consensus (else FanDuel) closing snapshot re-expressed at the bet's own line.
"""
import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

from odds_utils import amer_to_prob, amer_to_dec, devig_power
from dist_utils import implied_mu, p_over
from grade_props import STAT_COLS, norm

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
BETS = os.path.join(LIVE, "bets.csv")
BANKROLL = os.path.join(LIVE, "bankroll.json")
RESULTS_MD = os.path.join(ROOT, "RESULTS.md")

COLS = ["key", "placed_at", "match_date", "event_id", "market", "player",
        "side", "line", "odds_taken", "stake", "model_p", "status", "result",
        "actual", "clv", "clv_source", "pnl", "notes"]
MARKET_IDS = {"points": 393, "rebounds": 397, "assists": 391, "threes": 390,
              "pra": 396, "pts_ast": 394, "pts_reb": 395, "reb_ast": 398}


def load_box_index():
    parts = [pd.read_parquet(p) for p in sorted(glob.glob(
        os.path.join(ROOT, "data", "wehoop", "player_box_*.parquet")))
        if int(p[-12:-8]) >= 2025]
    box = pd.concat(parts, ignore_index=True)
    box["nname"] = box["athlete_display_name"].map(norm)
    box["date"] = box["game_date"].astype(str).str[:10]
    return {(r.nname, r.date): r for r in box.itertuples()}


def find_box(idx, nname, date):
    for delta in (0, 1, -1):
        d = str((pd.Timestamp(date) + pd.Timedelta(days=delta)).date())
        if (nname, d) in idx:
            return idx[(nname, d)]
    return None


def close_prob(event_id, market, player, side, line):
    """P(side wins at `line`) from the archived closing snapshot."""
    path = os.path.join(ROOT, "data", "raw", "bp", "offers",
                        f"{event_id}_{MARKET_IDS[market]}.json.gz")
    if not os.path.exists(path):
        return np.nan, ""
    with gzip.open(path, "rt") as f:
        d = json.load(f)
    for o in d.get("offers", []):
        pl = (o.get("participants") or [{}])[0].get("player") or {}
        name = f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip()
        if norm(name) != norm(player):
            continue
        for book, tag in [(0, "cons"), (10, "FD")]:
            lines = {}
            for sel in o.get("selections", []):
                s = sel.get("selection")
                for b in sel.get("books", []):
                    if b["id"] != book:
                        continue
                    for ln in b.get("lines", []):
                        if ln.get("main") and ln.get("line") is not None:
                            lines[s] = ln
            if "over" in lines and "under" in lines:
                p_close = float(devig_power(
                    amer_to_prob(lines["over"]["cost"]),
                    amer_to_prob(lines["under"]["cost"])))
                mu_c = float(implied_mu(market, np.array([lines["over"]["line"]]),
                                        np.array([p_close]))[0])
                po = float(p_over(market, np.array([mu_c]), np.array([line]))[0])
                return (po if side == "over" else 1 - po), \
                    f"{tag}@{lines['over']['line']}"
    return np.nan, ""


def main():
    os.makedirs(LIVE, exist_ok=True)
    if not os.path.exists(BETS):
        pd.DataFrame(columns=COLS).to_csv(BETS, index=False)
    bets = pd.read_csv(BETS)
    for c in ["result", "clv_source", "notes", "status"]:
        bets[c] = bets[c].astype("object")
    for c in ["line", "odds_taken", "stake", "model_p", "actual", "clv", "pnl"]:
        bets[c] = pd.to_numeric(bets[c], errors="coerce")
    if not os.path.exists(BANKROLL):
        json.dump({"start": 100.0, "current": 100.0}, open(BANKROLL, "w"))

    idx = load_box_index()
    n_settled = 0
    for i, b in bets.iterrows():
        if b["status"] != "open":
            continue
        if str(b["match_date"]) >= str(pd.Timestamp.now().date()):
            continue
        r = find_box(idx, norm(b["player"]), b["match_date"])
        if r is None or pd.isna(getattr(r, "minutes", np.nan)):
            days = (pd.Timestamp.now() - pd.Timestamp(b["match_date"])).days
            if r is not None and (r.did_not_play or pd.isna(r.minutes)):
                bets.loc[i, ["status", "result", "pnl"]] = "void", "void (DNP)", 0.0
                n_settled += 1
            elif days > 3:
                bets.loc[i, ["status", "result", "pnl"]] = \
                    "void", "void (no box score)", 0.0
                bets.loc[i, "notes"] = "no box row after 3d - inactive or postponed"
                n_settled += 1
            continue
        actual = float(sum(getattr(r, c) for c in STAT_COLS[b["market"]]))
        bets.loc[i, "actual"] = actual
        if actual == b["line"]:
            bets.loc[i, ["status", "result", "pnl"]] = "push", "push", 0.0
        else:
            won = (actual > b["line"]) == (b["side"] == "over")
            dec = float(amer_to_dec(b["odds_taken"]))
            bets.loc[i, "status"] = "settled"
            bets.loc[i, "result"] = "won" if won else "lost"
            bets.loc[i, "pnl"] = round(b["stake"] * (dec - 1) if won
                                       else -b["stake"], 2)
        pc, src = close_prob(int(b["event_id"]), b["market"], b["player"],
                             b["side"], float(b["line"]))
        if not np.isnan(pc):
            dec = float(amer_to_dec(b["odds_taken"]))
            bets.loc[i, "clv"] = round(pc * dec - 1, 4)
            bets.loc[i, "clv_source"] = src
        n_settled += 1

    bets.to_csv(BETS, index=False)
    done = bets[bets.status.isin(["settled", "push", "void"])]
    bk = json.load(open(BANKROLL))
    start = bk["start"]
    current = round(start + done["pnl"].sum(), 2) if len(done) else start
    if current != bk.get("current"):  # write only on change - no commit churn
        json.dump({"start": start, "current": current,
                   "updated": str(pd.Timestamp.now())}, open(BANKROLL, "w"))

    # ---- RESULTS.md ----
    lines = ["# Live FanDuel WNBA props - results\n",
             "Quarter-Kelly, $100 starting bankroll, picks from the "
             "[wnba-props model](README.md). CLV = `p_close(at bet line) x "
             "decimal_odds - 1`: whether the bets beat the closing price. CLV "
             "converges in one season; ROI does not - CLV is the scoreboard.\n"]
    sett = bets[bets.status == "settled"]
    no = int((bets.status == "open").sum())
    if len(sett):
        staked = sett.stake.sum()
        pnl = sett.pnl.sum()
        wins = int((sett.result == "won").sum())
        clv = done.clv.dropna()
        lines += [
            f"**Bankroll: ${current:.2f}** (start $100)\n",
            "| metric | value |", "|---|---|",
            f"| settled | {len(sett)} ({wins}W-{len(sett) - wins}L), "
            f"{int((bets.status == 'push').sum())} push, "
            f"{int((bets.status == 'void').sum())} void, {no} open |",
            f"| staked | ${staked:.2f} |",
            f"| P&L | ${pnl:+.2f} ({pnl / staked:+.1%} ROI) |",
            f"| mean CLV | {clv.mean():+.2%} (n={len(clv)}) |",
            f"| CLV-expected P&L | ${(done.stake * done.clv).sum():+.2f} |", ""]
        if len(clv) >= 2:
            se = clv.std() / np.sqrt(len(clv))
            lines.append(f"CLV t-stat: {clv.mean() / se:.2f}\n")
    else:
        lines.append(f"No settled bets yet ({no} open).\n")
    show = bets.sort_values("match_date", ascending=False).head(200)
    if len(show):
        lines += ["| date | player | market | side | line | odds | stake "
                  "| actual | result | P&L | CLV |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, b in show.iterrows():
            f2 = lambda v, fmt: fmt.format(v) if pd.notna(v) else ""
            lines.append(
                f"| {b['match_date']} | {b['player']} | {b['market']} "
                f"| {b['side']} | {b['line']} | {int(b['odds_taken'])} "
                f"| {b['stake']} | {f2(b['actual'], '{:g}')} "
                f"| {b['result'] if pd.notna(b['result']) else ''} "
                f"| {f2(b['pnl'], '{:+.2f}')} | {f2(b['clv'], '{:+.1%}')} |")
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"bankroll ${current:.2f}; {no} open bets")
    print(f"SETTLED {n_settled}" if n_settled else "NOTHING_TO_SETTLE")


if __name__ == "__main__":
    main()
