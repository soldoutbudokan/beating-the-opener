"""Settle logged prop bets: actuals, P&L, CLV vs the close, RESULTS.md.

Run after the data refresh (needs fresh wehoop box + BP archive for closes).
Voids on DNP (stake returned), pushes on exact line, CLV from the archived
consensus (else FanDuel) closing snapshot re-expressed at the bet's own line.
Also refreshes the shared HTML scoreboard at docs/index.html.

Settlement resolves the game via the bet's event_id (events.pkl carries the
UTC tip, converted to the ET game date, plus the two teams), so a bet can
never grade against a different game of the same player (AUDIT C2). CLV is
only computed from a coherent closing quote - same book, same line for over
and under, booksum within [1.00, 1.15] (AUDIT C1) - and rows that settle
before a usable close arrives get their blank CLV backfilled by later runs
(AUDIT C3).
"""
import glob
import gzip
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

from odds_utils import amer_to_prob, amer_to_dec, devig_power
from dist_utils import implied_mu, p_over
from grade_props import STAT_COLS, norm, BP2WH

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
BETS = os.path.join(LIVE, "bets.csv")
BANKROLL = os.path.join(LIVE, "bankroll.json")
RESULTS_MD = os.path.join(ROOT, "RESULTS.md")
ET = "America/New_York"

COLS = ["key", "placed_at", "match_date", "event_id", "market", "player",
        "side", "line", "odds_taken", "stake", "model_p", "status", "result",
        "actual", "clv", "clv_source", "pnl", "notes"]
MARKET_IDS = {"points": 393, "rebounds": 397, "assists": 391, "threes": 390,
              "pra": 396, "pts_ast": 394, "pts_reb": 395, "reb_ast": 398}
BOOKSUM_LO, BOOKSUM_HI = 1.00, 1.15  # sane two-way vig band


def refresh_site():
    """Rebuild docs/index.html from both markets' live files - never fatal."""
    try:
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "..", "site", "build_site.py")],
                           capture_output=True, text=True, timeout=120)
        print((r.stdout or r.stderr).strip().splitlines()[-1])
    except Exception as e:  # a broken page must never block settlement
        print(f"site build skipped: {e}")


def event_meta():
    """event_id -> (ET game date, {wehoop team abbrs}) from events.pkl."""
    path = os.path.join(ROOT, "data", "events.pkl")
    if not os.path.exists(path):
        return {}
    ev = pd.read_pickle(path)
    et = (pd.to_datetime(ev["scheduled"], utc=True)
          .dt.tz_convert(ET).dt.date.astype(str))
    return {int(e): (d, {BP2WH.get(h, h), BP2WH.get(v, v)})
            for e, d, h, v in zip(ev.event_id, et, ev.home, ev.visitor)}


def load_box_index():
    parts = [pd.read_parquet(p) for p in sorted(glob.glob(
        os.path.join(ROOT, "data", "wehoop", "player_box_*.parquet")))
        if int(p[-12:-8]) >= 2025]
    box = pd.concat(parts, ignore_index=True)
    box["nname"] = box["athlete_display_name"].map(norm)
    box["date"] = box["game_date"].astype(str).str[:10]
    return {(r.nname, r.date): r for r in box.itertuples()}


def find_box(idx, nname, date, teams=None):
    """Box row for (player, ET date); a row only counts if its team matches
    the event's two teams, so a slipped settlement can't grade the player's
    NEXT game (AUDIT C2). Probe order (0, -1, +1): prefer the true date, then
    the earlier day (timestamp skew), and only then the later one."""
    for delta in (0, -1, 1):
        d = str((pd.Timestamp(date) + pd.Timedelta(days=delta)).date())
        r = idx.get((nname, d))
        if r is None:
            continue
        if teams and getattr(r, "team_abbreviation", None) not in teams:
            continue
        return r
    return None


def close_prob(event_id, market, player, side, line):
    """P(side wins at `line`) from the archived closing snapshot.

    Only a COHERENT closing quote is usable: over and under from the same
    book at the same line with booksum in [1.00, 1.15]. Independent
    over/under records (different line, or booksum < 1) fabricate the
    implied mean - returning nan (blank CLV, retried by backfill) is the
    honest outcome (AUDIT C1).
    """
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
            if "over" not in lines or "under" not in lines:
                continue
            ov, un = lines["over"], lines["under"]
            booksum = float(amer_to_prob(ov["cost"]) + amer_to_prob(un["cost"]))
            if ov["line"] != un["line"] or not (BOOKSUM_LO <= booksum <= BOOKSUM_HI):
                continue  # mispaired mains - try the next book, never devig
            p_close = float(devig_power(amer_to_prob(ov["cost"]),
                                        amer_to_prob(un["cost"])))
            mu_c = float(implied_mu(market, np.array([ov["line"]]),
                                    np.array([p_close]))[0])
            po = float(p_over(market, np.array([mu_c]), np.array([line]))[0])
            return (po if side == "over" else 1 - po), f"{tag}@{ov['line']}"
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
    emeta = event_meta()
    today_et = str(pd.Timestamp.now(tz=ET).date())
    n_settled = 0
    for i, b in bets.iterrows():
        if b["status"] != "open":
            continue
        et_date, teams = emeta.get(int(b["event_id"]),
                                   (str(b["match_date"]), None))
        if et_date >= today_et:
            continue  # game not finished
        r = find_box(idx, norm(b["player"]), et_date, teams)
        played = (r is not None and pd.notna(r.minutes) and r.minutes > 0
                  and not (pd.notna(r.did_not_play) and bool(r.did_not_play)))
        if not played:
            days = (pd.Timestamp(today_et) - pd.Timestamp(et_date)).days
            if r is not None:  # box row exists but the player did not play
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
        n_settled += 1

    # CLV: fresh settlements AND any earlier row whose close snapshot was
    # missing when it settled (backfill - AUDIT C3). Voids carry no CLV.
    n_clv = 0
    for i, b in bets.iterrows():
        if b["status"] not in ("settled", "push") or pd.notna(b["clv"]):
            continue
        pc, src = close_prob(int(b["event_id"]), b["market"], b["player"],
                             b["side"], float(b["line"]))
        if not np.isnan(pc):
            dec = float(amer_to_dec(b["odds_taken"]))
            bets.loc[i, "clv"] = round(pc * dec - 1, 4)
            bets.loc[i, "clv_source"] = src
            n_clv += 1

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
             "> At a glance: **[live scoreboard]"
             "(https://soldoutbudokan.github.io/beating-the-opener/#wnba)**"
             " - same numbers, plus the open picks and the backtest evidence.\n",
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
            tline = f"CLV t-stat: {clv.mean() / se:.2f} (iid)"
            byd = (done.dropna(subset=["clv"])
                   .groupby("match_date")["clv"].mean())
            if len(byd) >= 2 and byd.std() > 0:
                tc = byd.mean() / (byd.std() / np.sqrt(len(byd)))
                tline += f"; {tc:.2f} clustered by match date ({len(byd)} dates)"
            lines.append(tline + "\n")
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
    refresh_site()
    print(f"bankroll ${current:.2f}; {no} open bets")
    if n_clv:
        print(f"CLV_STAMPED {n_clv}")
    print(f"SETTLED {n_settled}" if n_settled else "NOTHING_TO_SETTLE")


if __name__ == "__main__":
    main()
