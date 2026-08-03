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

from odds_utils import (amer_to_prob, amer_to_dec, devig_power, fit_shade,
                        apply_shade)
from dist_utils import implied_mu, p_over
from grade_props import STAT_COLS, norm, BP2WH

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
BETS = os.path.join(LIVE, "bets.csv")
BANKROLL = os.path.join(LIVE, "bankroll.json")
RESULTS_MD = os.path.join(ROOT, "RESULTS.md")
ET = "America/New_York"

COLS = ["key", "placed_at", "match_date", "event_id", "market", "player",
        "side", "line", "odds_taken", "stake", "model_p", "ev_claimed",
        "status", "result", "actual", "clv", "clv_cal", "clv_source", "pnl",
        "notes"]
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


def market_shades():
    """Per-market over-shade from the graded archive (expanding window).

    WNBA prop prices overstate P(over) by ~2pp on average at BOTH open and
    close (AUDIT N1), so raw-close CLV mechanically penalises unders. The
    scoreboard records CLV against the raw close (`clv`, the standard
    yardstick) AND against the shade-corrected close (`clv_cal`).
    """
    path = os.path.join(ROOT, "data", "modelset.pkl")
    if not os.path.exists(path):
        return {}
    ms = pd.read_pickle(path)
    if "open_coherent" not in ms.columns:
        return {}
    d = ms[ms.actual.notna() & ~ms.void & ms.open_coherent.fillna(False)
           & (ms.actual != ms.open_line)]
    if len(d) < 400:
        return {}
    y = (d.actual > d.open_line).astype(float)
    pooled = fit_shade(d.p_open, y)
    out = {}
    for mkt in d.market.unique():
        m = d.market == mkt
        out[mkt] = fit_shade(d.p_open[m], y[m]) if m.sum() >= 200 else pooled
    return out


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
            return po, f"{tag}@{ov['line']}"  # P(over at the bet's line)
    return np.nan, ""


def main():
    os.makedirs(LIVE, exist_ok=True)
    if not os.path.exists(BETS):
        pd.DataFrame(columns=COLS).to_csv(BETS, index=False)
    bets = pd.read_csv(BETS)
    for c in COLS:
        if c not in bets.columns:
            bets[c] = np.nan
    bets = bets[COLS]
    for c in ["result", "clv_source", "notes", "status"]:
        bets[c] = bets[c].astype("object")
    for c in ["line", "odds_taken", "stake", "model_p", "ev_claimed",
              "actual", "clv", "clv_cal", "pnl"]:
        bets[c] = pd.to_numeric(bets[c], errors="coerce")
    # ev_claimed: what the model said the bet was worth AT THE PRICE TAKEN.
    # Every row must carry it - a routine that logs a fill without it gets it
    # filled here from model_p and odds_taken, so the column can never go
    # stale or half-populated (see live/PROTOCOL.md "Reporting fills").
    need_ev = bets.ev_claimed.isna() & bets.model_p.notna() \
        & bets.odds_taken.notna()
    if need_ev.any():
        bets.loc[need_ev, "ev_claimed"] = [
            round(float(mp) * float(amer_to_dec(oc)) - 1, 4)
            for mp, oc in zip(bets.loc[need_ev, "model_p"],
                              bets.loc[need_ev, "odds_taken"])]
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
            if bets.loc[i, "status"] == "void":
                # a CLV stamped while the bet was still open is meaningless
                # once it voids - the close was priced off the same absence
                bets.loc[i, ["clv", "clv_cal"]] = np.nan
                bets.loc[i, "clv_source"] = ""
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

    # a void that carries a CLV stamped while it was still open (early
    # stamping preceded the void) is scrubbed - voids carry no CLV
    stale_void = bets.status.eq("void") & bets.clv.notna()
    if stale_void.any():
        bets.loc[stale_void, ["clv", "clv_cal"]] = np.nan
        bets.loc[stale_void, "clv_source"] = ""

    # CLV: fresh settlements AND any earlier row whose close snapshot was
    # missing when it settled (backfill - AUDIT C3). Voids carry no CLV.
    # Open bets whose game has finished are stamped too: the close exists
    # once the game tips, and a box-score lag upstream must not hide the
    # market's verdict from the scoreboard (owner request 2026-08-03).
    shades = market_shades()
    n_clv = 0
    for i, b in bets.iterrows():
        game_done = (b["status"] == "open"
                     and emeta.get(int(b["event_id"]),
                                   (str(b["match_date"]), None))[0] < today_et)
        if (b["status"] not in ("settled", "push") and not game_done) \
                or (pd.notna(b["clv"]) and pd.notna(b["clv_cal"])):
            continue
        po, src = close_prob(int(b["event_id"]), b["market"], b["player"],
                             b["side"], float(b["line"]))
        if not np.isnan(po):
            dec = float(amer_to_dec(b["odds_taken"]))
            po_cal = float(apply_shade(po, shades.get(b["market"], 0.0)))
            over = b["side"] == "over"
            bets.loc[i, "clv"] = round((po if over else 1 - po) * dec - 1, 4)
            bets.loc[i, "clv_cal"] = round(
                (po_cal if over else 1 - po_cal) * dec - 1, 4)
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
             "decimal_odds - 1`: whether the bets beat the closing price. "
             "`CLV*` re-expresses the close with the measured market "
             "over-shade removed (WNBA prop prices overstate P(over) by ~2pp "
             "on average, so raw CLV mechanically penalises unders - see "
             "AUDIT.md N1). Both converge far faster than ROI.\n",
             "`EV said` is the model's own claim for that bet at the price "
             "actually taken (`model_p x decimal_odds - 1`). Read it against "
             "`CLV`: the model's claim vs the market's verdict on the same "
             "bet. A large positive `EV said` next to a negative `CLV` means "
             "the market never came to us - the claimed edge was not visible "
             "to anyone else. Note CLV's break-even is not zero: paying a "
             "two-way price and seeing no line movement scores about "
             "`1/booksum - 1`, i.e. roughly -5% to -7% at typical prop "
             "prices, so `CLV` near -6% means the line simply did not "
             "move.\n"]
    sett = bets[bets.status == "settled"]
    no = int((bets.status == "open").sum())
    if len(sett):
        staked = sett.stake.sum()
        pnl = sett.pnl.sum()
        wins = int((sett.result == "won").sum())
        # CLV aggregates cover every stamped row, including finished-but-
        # unsettled bets - the market's verdict does not wait for box scores.
        # Voids are excluded: their close was priced off the same absence.
        stamped = bets[bets.status != "void"].dropna(subset=["clv"])
        clv = stamped.clv
        clv_cal = stamped.clv_cal.dropna()
        lines += [
            f"**Bankroll: ${current:.2f}** (start $100)\n",
            "| metric | value |", "|---|---|",
            f"| settled | {len(sett)} ({wins}W-{len(sett) - wins}L), "
            f"{int((bets.status == 'push').sum())} push, "
            f"{int((bets.status == 'void').sum())} void, {no} open |",
            f"| staked | ${staked:.2f} |",
            f"| P&L | ${pnl:+.2f} ({pnl / staked:+.1%} ROI) |",
            f"| mean EV said (model) | {sett.ev_claimed.mean():+.2%} "
            f"(n={int(sett.ev_claimed.notna().sum())}) |"
            if sett.ev_claimed.notna().any() else "| mean EV said (model) | - |",
            f"| mean CLV (vs close) | {clv.mean():+.2%} (n={len(clv)}) |",
            f"| mean CLV* (shade-adj) | "
            f"{clv_cal.mean():+.2%} (n={len(clv_cal)}) |"
            if len(clv_cal) else "| mean CLV* (shade-adj) | - |",
            f"| Model-expected P&L | "
            f"${(done.stake * done.ev_claimed).sum():+.2f} |",
            f"| CLV-expected P&L | "
            f"${(stamped.stake * stamped.clv).sum():+.2f} |", ""]
        if len(clv) >= 2:
            se = clv.std() / np.sqrt(len(clv))
            tline = f"CLV t-stat: {clv.mean() / se:.2f} (iid)"
            byd = stamped.groupby("match_date")["clv"].mean()
            if len(byd) >= 2 and byd.std() > 0:
                tc = byd.mean() / (byd.std() / np.sqrt(len(byd)))
                tline += f"; {tc:.2f} clustered by match date ({len(byd)} dates)"
            lines.append(tline + "\n")
    else:
        lines.append(f"No settled bets yet ({no} open).\n")
    show = bets.sort_values("match_date", ascending=False).head(200)
    if len(show):
        lines += ["| date | player | market | side | line | odds | stake "
                  "| EV said | actual | result | P&L | CLV | CLV* |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, b in show.iterrows():
            f2 = lambda v, fmt: fmt.format(v) if pd.notna(v) else ""
            lines.append(
                f"| {b['match_date']} | {b['player']} | {b['market']} "
                f"| {b['side']} | {b['line']} | {int(b['odds_taken'])} "
                f"| {b['stake']} | {f2(b['ev_claimed'], '{:+.1%}')} "
                f"| {f2(b['actual'], '{:g}')} "
                f"| {b['result'] if pd.notna(b['result']) else ''} "
                f"| {f2(b['pnl'], '{:+.2f}')} | {f2(b['clv'], '{:+.1%}')} "
                f"| {f2(b['clv_cal'], '{:+.1%}')} |")
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    refresh_site()
    print(f"bankroll ${current:.2f}; {no} open bets")
    if n_clv:
        print(f"CLV_STAMPED {n_clv}")
    print(f"SETTLED {n_settled}" if n_settled else "NOTHING_TO_SETTLE")


if __name__ == "__main__":
    main()
