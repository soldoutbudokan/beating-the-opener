"""Live projection sheet (v3 T3b): the PINNED fp-prospective-2 talent
model priced on tonight's props, plus a news-adjusted variant driven by
live/projections_overrides.json.

RESEARCH SHEET ONLY — the betting pause (live/PROTOCOL.md) is absolute.
This prints projections and where they disagree with current prices; it
does not pick, notify, or bet.

Base column = exactly the registered model: fp_model.predict() with
fit_play_cal(panel, '2026-01-01') and the talent columns — nothing else.
News column = same model with the minutes estimate replaced by the
override (per-game components scaled by the minutes ratio, so a returnee
capped at 14 minutes isn't projected off her healthy 28-minute averages).

Usage: python3 src/fp_live.py
Writes live/projections.csv (append-logged, timestamped).
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import fp_model as fp
from build_modelset import norm
from live_pipeline import fetch_upcoming, parse_offer

ROOT = os.path.join(os.path.dirname(__file__), "..")
OVR = os.path.join(ROOT, "live", "projections_overrides.json")


def latest_states():
    """One row per player: latest panel row + talent, plus per-team
    context (most recent pre-game EWs, one game stale at worst)."""
    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    tal = pd.read_pickle(os.path.join(ROOT, "data", "talent.pkl"))
    panel = panel.merge(tal, on=["athlete_id", "game_id"], how="left")
    panel["nname"] = panel.athlete_display_name.map(norm)
    panel = panel.sort_values("game_date")
    last = panel.groupby("nname").tail(1).set_index("nname")
    team_ctx = (panel.groupby("team_name")
                .tail(1).set_index("team_name")
                [["tm_pace_ew", "tm_pts_for_ew", "tm_pts_against_ew"]])
    return panel, last, team_ctx


def build_fixture(props, last, team_ctx, abbr2mascot, minutes_override=None):
    """Rows shaped for fp_model.mu_stats: one per (prop row)."""
    rows = []
    for r in props.itertuples():
        nm = norm(r.player)
        if nm not in last.index:
            continue
        st = last.loc[nm]
        home_team = abbr2mascot.get(r.home)
        away_team = abbr2mascot.get(r.visitor)
        my_team = abbr2mascot.get(r.bp_team)
        opp = away_team if my_team == home_team else home_team
        row = {"idx": r.Index, "market": r.market, "date": r.date,
               "home": 1.0 if my_team == home_team else 0.0,
               "gp": st.gp}
        for c in last.columns:
            if (c.endswith(("_ewf", "_ews")) or c.startswith("talent_")):
                row[c] = st[c]
        tc = team_ctx.loc[my_team] if my_team in team_ctx.index else None
        oc = team_ctx.loc[opp] if opp in team_ctx.index else None
        row["tm_pace_ew"] = tc.tm_pace_ew if tc is not None else np.nan
        row["opp_pace_ew"] = oc.tm_pace_ew if oc is not None else np.nan
        row["opp_pts_against_ew"] = (oc.tm_pts_against_ew
                                     if oc is not None else np.nan)
        if minutes_override and nm in minutes_override:
            est = minutes_override[nm]
            usual = (fp.W_FAST * st.min_ewf
                     + (1 - fp.W_FAST) * st.min_ews) or 1.0
            ratio = est / max(usual, 1.0)
            for stat in fp.RAW:
                for tag in ("_ewf", "_ews"):
                    c = f"{stat}{tag}"
                    if c in row and pd.notna(row[c]):
                        row[c] = row[c] * ratio
            row["min_ewf"] = est
            row["min_ews"] = est
        rows.append(row)
    return pd.DataFrame(rows).set_index("idx")


def active_overrides(dates):
    try:
        entries = json.load(open(OVR))["entries"]
    except Exception:
        return {}, {}
    minutes, status = {}, {}
    for i, e in enumerate(entries):
        if e.get("superseded_by") is not None:
            continue
        if e.get("game_date") and e["game_date"] not in dates:
            continue
        nm = norm(e["player"])
        if e.get("status") == "out":
            status[nm] = "out"
        est = e.get("minutes_est")
        if est is None and e.get("minutes_range"):
            est = float(np.mean(e["minutes_range"]))
        if est is not None:
            minutes[nm] = float(est)
    return minutes, status


def ev_cols(p, line_o, cost_o, line_u, cost_u, market, mu, cal):
    """EV for a source's current two-way quote (same-line, sane vig)."""
    if any(pd.isna(x) for x in (line_o, cost_o, line_u, cost_u)):
        return np.nan, ""
    if line_o != line_u:
        return np.nan, ""
    bs = (fp.american_dec(cost_o) ** -1 + fp.american_dec(cost_u) ** -1)
    if not (1.00 <= bs <= 1.15):
        return np.nan, ""
    p_here = fp.p_over(market, mu, line_o, cal)
    ev_o = p_here * fp.american_dec(cost_o) - 1
    ev_u = (1 - p_here) * fp.american_dec(cost_u) - 1
    return (ev_o, "over") if ev_o >= ev_u else (ev_u, "under")


def main():
    evs, offers = fetch_upcoming()
    if not offers:
        print("no upcoming offers")
        return
    props = pd.DataFrame([parse_offer(o) for o in offers])
    props = props[props.market.isin(fp.PARTS)].reset_index(drop=True)
    abbr2mascot = {}
    for e in evs:
        for pt in e.get("participants", []):
            abbr2mascot[pt["id"]] = pt["name"]

    panel, last, team_ctx = latest_states()
    cal = fp.fit_play_cal(panel, "2026-01-01")   # pinned calibration
    mins_ovr, status_ovr = active_overrides(set(props.date))

    fx = build_fixture(props, last, team_ctx, abbr2mascot)
    mus = fp.predict(fx, cal)
    fx_news = build_fixture(props, last, team_ctx, abbr2mascot, mins_ovr)
    mus_news = fp.predict(fx_news, cal)

    out = []
    for r in props.itertuples():
        if r.Index not in mus.index or pd.isna(mus[r.Index]):
            continue
        nm = norm(r.player)
        mu = float(mus[r.Index])
        mu_n = float(mus_news[r.Index]) if r.Index in mus_news.index else mu
        line = r.cons_line_over if pd.notna(
            getattr(r, "cons_line_over", np.nan)) else getattr(
            r, "fd_line_over", np.nan)
        if pd.isna(line):
            continue
        p = fp.p_over(r.market, mu, line, cal)
        p_n = fp.p_over(r.market, mu_n, line, cal)
        ev_c, side_c = ev_cols(p_n, getattr(r, "cons_line_over", np.nan),
                               getattr(r, "cons_over_cost", np.nan),
                               getattr(r, "cons_line_under", np.nan),
                               getattr(r, "cons_under_cost", np.nan),
                               r.market, mu_n, cal)
        ev_f, side_f = ev_cols(p_n, getattr(r, "fd_line_over", np.nan),
                               getattr(r, "fd_over_cost", np.nan),
                               getattr(r, "fd_line_under", np.nan),
                               getattr(r, "fd_under_cost", np.nan),
                               r.market, mu_n, cal)
        out.append({
            "date": r.date, "player": r.player, "market": r.market,
            "line": line, "mu_base": round(mu, 2), "mu_news": round(mu_n, 2),
            "p_over_base": round(p, 3), "p_over_news": round(p_n, 3),
            "override": ("OUT" if status_ovr.get(nm) == "out"
                         else f"min={mins_ovr[nm]:.0f}" if nm in mins_ovr
                         else ""),
            "ev_cons": round(ev_c, 4) if pd.notna(ev_c) else np.nan,
            "side_cons": side_c,
            "ev_fd": round(ev_f, 4) if pd.notna(ev_f) else np.nan,
            "side_fd": side_f,
        })
    sheet = pd.DataFrame(out)
    sheet["ev_best"] = sheet[["ev_cons", "ev_fd"]].max(axis=1)
    sheet = sheet.sort_values("ev_best", ascending=False)
    sheet.insert(0, "generated_utc",
                 datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    path = os.path.join(ROOT, "live", "projections.csv")
    header = not os.path.exists(path)
    sheet.to_csv(path, mode="a", header=header, index=False)
    print(f"{len(sheet)} props projected "
          f"({sheet.date.min()} .. {sheet.date.max()}); appended -> "
          f"live/projections.csv")
    show = sheet[sheet.ev_best.notna()].head(20)
    cols = ["date", "player", "market", "line", "mu_news", "p_over_news",
            "override", "ev_cons", "side_cons", "ev_fd", "side_fd"]
    print(show[cols].to_string(index=False))
    write_picks(props, sheet, cal)


def write_picks(props, sheet, cal):
    """live/picks.csv per the v3 protocol (re-opened 2026-07-31): FanDuel
    coherent quote, news-adjusted claimed EV > 10%. Stake = quarter-Kelly
    on HALF the claimed edge (dev: claimed EV realizes ~half), $0.50
    rounding/minimum, cap 5% of bankroll, dedupe vs bets.csv."""
    from live_pipeline import min_amer
    bank = json.load(open(os.path.join(ROOT, "live", "bankroll.json")))
    bankroll = float(bank["current"])
    try:
        logged = set(pd.read_csv(
            os.path.join(ROOT, "live", "bets.csv")).key)
    except Exception:
        logged = set()
    pmap = props.set_index(props.index)
    rows = []
    for r in sheet.itertuples():
        if pd.isna(r.ev_fd) or r.ev_fd <= 0.10:
            continue
        src = pmap[pmap.player.eq(r.player) & pmap.market.eq(r.market)
                   & pmap.date.eq(r.date)]
        if not len(src):
            continue
        s = src.iloc[0]
        side = r.side_fd
        cost = s.fd_over_cost if side == "over" else s.fd_under_cost
        line = s.fd_line_over
        dec = float(fp.american_dec(cost))
        p_side = (r.p_over_news if side == "over" else 1 - r.p_over_news)
        # half the claimed edge -> implied shrunk probability -> 1/4 Kelly
        e_half = r.ev_fd / 2.0
        p_shrunk = (1 + e_half) / dec
        f_k = max((dec * p_shrunk - 1) / (dec - 1), 0.0)
        stake = min(0.25 * f_k * bankroll, 0.05 * bankroll)
        stake = max(round(stake * 2) / 2, 0.5)
        key = f"{s.event_id}_{s.market}_{norm(s.player)}_{side}"
        rows.append({
            "key": key, "date": r.date, "tip": s.tip,
            "event_id": s.event_id, "market": s.market, "player": s.player,
            "team": s.bp_team, "game": f"{s.visitor}@{s.home}",
            "side": side, "fd_line": line, "fd_cost": cost,
            "model_p": round(p_side, 4), "ev": round(r.ev_fd, 4),
            "strong": True, "min_odds_3pct": min_amer(p_side, 0.03),
            "min_odds_6pct": min_amer(p_side, 0.06),
            "stake": stake, "mu_model": r.mu_news, "mu_open": "",
            "open_line": s.open_line, "play": key not in logged,
            "already_bet": key in logged,
        })
    picks = pd.DataFrame(rows)
    if len(picks):
        # one bet per player per game: keep the highest-EV market
        picks = (picks.sort_values("ev", ascending=False)
                 .drop_duplicates(["player", "date"]).reset_index(drop=True))
        # total-exposure cap: playable stakes <= 30% of bankroll
        play_stake = picks.loc[picks.play, "stake"].sum()
        cap = 0.30 * bankroll
        if play_stake > cap:
            scale = cap / play_stake
            picks.loc[picks.play, "stake"] = (
                (picks.loc[picks.play, "stake"] * scale * 2)
                .round() / 2).clip(lower=0.5)
    picks.to_csv(os.path.join(ROOT, "live", "picks.csv"), index=False)
    n_play = int(picks.play.sum()) if len(picks) else 0
    print(f"\npicks.csv: {len(picks)} qualifying (EV>10% at FanDuel), "
          f"{n_play} new playable")
    if len(picks):
        print(picks[["date", "player", "market", "side", "fd_line",
                     "fd_cost", "ev", "stake", "play"]].to_string(index=False))


if __name__ == "__main__":
    main()
