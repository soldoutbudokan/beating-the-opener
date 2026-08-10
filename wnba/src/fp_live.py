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
ET = "America/New_York"

# Pick gates (owner-directed 2026-08-08, after the first-week audit): the
# August wehoop stall showed the sheet happily pricing a week-stale panel,
# 42-day-stale players, and lines that had moved points off the open - and
# presenting the resulting model-vs-market distance as "EV". Every gate
# below turns one of those defects into a blocked, labelled row instead of
# a playable pick. Blocked rows stay on the sheet (play=False, flags set)
# so what was skipped stays legible.
MAX_PLAYER_STALE_D = 14   # last panel game older than this -> no pick
MAX_SANE_EV = 0.25        # a >25% two-way claim is a defect alarm, not a bet
OPEN_JUICE_TOL = 15       # cents of drift still counting as "at the opener"
ROLE_MIN_JUMP = 8.0       # last-game minutes this far off the EW blend -> flag


def panel_gap(panel):
    """(panel max game date, ET slate dates the panel is missing).

    A missing slate = a date with completed games (per events.pkl) after
    the panel's last game and before today: the market has seen those
    games and the model has not, so claimed EV is contaminated. When
    events.pkl is absent the calendar stands in: a panel more than 2 days
    old in-season counts as behind.
    """
    pmax = pd.Timestamp(panel.game_date.max()).date()
    today = pd.Timestamp.now(tz=ET).date()
    try:
        ev = pd.read_pickle(os.path.join(ROOT, "data", "events.pkl"))
        et_dates = (pd.to_datetime(ev["scheduled"], utc=True)
                    .dt.tz_convert(ET).dt.date)
        missed = sorted({str(d) for d in et_dates if pmax < d < today})
    except Exception:
        missed = ([f"calendar>{(today - pmax).days}d"]
                  if (today - pmax).days > 2 else [])
    return pmax, missed


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
               "gp": st.gp,
               # gate metadata (never model inputs): where/when the panel
               # last saw this player, vs where the feed says she is now
               "last_gd": st.game_date, "panel_team": st.team_name,
               "last_min": st.minutes,
               "last_start": bool(st.starter) if pd.notna(st.starter) else None,
               "evteam_ok": (my_team is not None
                             and my_team in (home_team, away_team)),
               "team_changed": (my_team is not None
                                and st.team_name != my_team)}
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
    """EV for a source's current two-way quote (same-line, sane vig).

    Third return value is the model probability of the chosen side **at
    this source's line**, which is not always the consensus line the
    sheet's `p_over_news` is quoted at (Caitlin Clark assists 2026-08-07:
    consensus 9.5, FanDuel 8.5). Callers that report a probability
    alongside this EV must use it, not `p_over_news`.
    """
    if any(pd.isna(x) for x in (line_o, cost_o, line_u, cost_u)):
        return np.nan, "", np.nan
    if line_o != line_u:
        return np.nan, "", np.nan
    bs = (fp.american_dec(cost_o) ** -1 + fp.american_dec(cost_u) ** -1)
    if not (1.00 <= bs <= 1.15):
        return np.nan, "", np.nan
    p_here = fp.p_over(market, mu, line_o, cal)
    ev_o = p_here * fp.american_dec(cost_o) - 1
    ev_u = (1 - p_here) * fp.american_dec(cost_u) - 1
    return ((ev_o, "over", p_here) if ev_o >= ev_u
            else (ev_u, "under", 1 - p_here))


def at_opener(s, side):
    """Is FanDuel's current quote still the (FanDuel-sourced) opener?

    The v1 discipline, reinstated for v3 (owner decision 2026-08-08): the
    backtested edge is priced at the open - once the line or juice moves,
    the remaining "edge" is the model disagreeing with fresh information.
    Requires a coherent FANDUEL opening pair (AUDIT C1/H3), the current FD
    line equal to the opening line, and juice drift <= OPEN_JUICE_TOL cents
    on the bet side. Returns (ok, reason).
    """
    ol_o = getattr(s, "open_line_over", np.nan)
    ol_u = getattr(s, "open_line_under", np.nan)
    ob_o = getattr(s, "open_book_over", np.nan)
    ob_u = getattr(s, "open_book_under", np.nan)
    oc = getattr(s, f"open_{side}_cost", np.nan)
    oc_other = getattr(s, "open_under_cost" if side == "over"
                       else "open_over_cost", np.nan)
    if any(pd.isna(x) for x in (ol_o, ol_u, oc, oc_other)):
        return False, "NO_OPEN_QUOTE"
    if ol_o != ol_u or ob_o != ob_u:
        return False, "OPEN_INCOHERENT"
    bs = (fp.american_dec(oc) ** -1 + fp.american_dec(oc_other) ** -1)
    if not (1.00 <= bs <= 1.15):
        return False, "OPEN_INCOHERENT"
    if ob_o != 10:
        return False, "NON_FD_OPEN"
    fd_line = getattr(s, "fd_line_over", np.nan)
    fd_cost = getattr(s, f"fd_{side}_cost", np.nan)
    if pd.isna(fd_line) or pd.isna(fd_cost):
        return False, "NO_FD_QUOTE"
    if fd_line != ol_o or abs(float(fd_cost) - float(oc)) > OPEN_JUICE_TOL:
        return False, "MOVED_OFF_OPEN"
    return True, ""


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
    pmax, missed_slates = panel_gap(panel)
    print(f"panel through {pmax}"
          + (f"; MISSING SLATES {','.join(missed_slates)} - picks blocked"
             if missed_slates else ""))
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
        ev_c, side_c, _ = ev_cols(p_n, getattr(r, "cons_line_over", np.nan),
                                  getattr(r, "cons_over_cost", np.nan),
                                  getattr(r, "cons_line_under", np.nan),
                                  getattr(r, "cons_under_cost", np.nan),
                                  r.market, mu_n, cal)
        ev_f, side_f, p_f = ev_cols(p_n, getattr(r, "fd_line_over", np.nan),
                                    getattr(r, "fd_over_cost", np.nan),
                                    getattr(r, "fd_line_under", np.nan),
                                    getattr(r, "fd_under_cost", np.nan),
                                    r.market, mu_n, cal)
        fxr = fx.loc[r.Index]
        blend = (fp.W_FAST * fxr.min_ewf + (1 - fp.W_FAST) * fxr.min_ews) \
            if pd.notna(fxr.min_ewf) and pd.notna(fxr.min_ews) else fxr.min_ewf
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
            # gate metadata + P(side_fd) at the FANDUEL line - carried in
            # memory for write_picks, dropped before the append below so
            # the projections.csv archive keeps its fixed column set
            "p_side_fd": round(p_f, 4) if pd.notna(p_f) else np.nan,
            "days_stale": (pd.Timestamp(r.date)
                           - pd.Timestamp(fxr.last_gd)).days,
            "evteam_ok": bool(fxr.evteam_ok),
            "team_changed": bool(fxr.team_changed),
            "role_min": (pd.notna(fxr.last_min) and pd.notna(blend)
                         and abs(float(fxr.last_min) - float(blend))
                         >= ROLE_MIN_JUMP),
            "role_start": (fxr.last_start is not None
                           and pd.notna(fxr.started_ewf)
                           and bool(fxr.last_start)
                           != bool(fxr.started_ewf > 0.5)),
        })
    sheet = pd.DataFrame(out)
    sheet["ev_best"] = sheet[["ev_cons", "ev_fd"]].max(axis=1)
    sheet = sheet.sort_values("ev_best", ascending=False)
    sheet.insert(0, "generated_utc",
                 datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    path = os.path.join(ROOT, "live", "projections.csv")
    header = not os.path.exists(path)
    META = ["p_side_fd", "days_stale", "evteam_ok", "team_changed",
            "role_min", "role_start"]
    sheet.drop(columns=META).to_csv(path, mode="a", header=header,
                                    index=False)
    print(f"{len(sheet)} props projected "
          f"({sheet.date.min()} .. {sheet.date.max()}); appended -> "
          f"live/projections.csv")
    show = sheet[sheet.ev_best.notna()].head(20)
    cols = ["date", "player", "market", "line", "mu_news", "p_over_news",
            "override", "ev_cons", "side_cons", "ev_fd", "side_fd"]
    print(show[cols].to_string(index=False))
    write_picks(props, sheet, cal, missed_slates)


def write_picks(props, sheet, cal, missed_slates=()):
    """live/picks.csv per the v3 protocol (gates added 2026-08-08): FanDuel
    coherent quote STILL AT THE FANDUEL OPENER, news-adjusted claimed
    EV in (10%, 25%], fresh panel, fresh player state, feed team matching
    the panel team. Stake = quarter-Kelly on HALF the claimed edge (dev:
    claimed EV realizes ~half), $0.50 rounding/minimum, cap 5% of
    bankroll, dedupe vs bets.csv by pick key AND by player-game (a player
    already carrying an open bet in the event cannot resurface under a
    second market - added 2026-08-10). Gated rows stay on the sheet with
    play=False and their reasons in `flags`; `ROLE_*?` flags are advisory
    (owner review - the talent engine is slow on role changes by design)
    and never block on their own."""
    from live_pipeline import min_amer
    bank = json.load(open(os.path.join(ROOT, "live", "bankroll.json")))
    bankroll = float(bank["current"])
    try:
        bets = pd.read_csv(os.path.join(ROOT, "live", "bets.csv"))
    except Exception:
        bets = pd.DataFrame(columns=["key", "event_id", "player", "status",
                                     "market", "side", "line"])
    logged = set(bets.key)
    # One bet per player per game, enforced ACROSS SHEETS (2026-08-10).
    # `logged` matches on the exact pick key, so it only ever caught the same
    # market twice. The drop_duplicates below caps a player at one row per
    # game WITHIN a sheet, but it prefers a playable row - so once the market
    # the owner actually bet became already_bet=True, a DIFFERENT market on
    # the same player-game sorted above it and was offered as playable by the
    # next firing. (Dearica Hamby, 2026-08-11 PHO@LAS: points over 13.5
    # filled, rebounds over 6.5 offered and filled the same evening - two
    # correlated positions on one player's minutes, which the pre-registered
    # cap exists to prevent.) Maps (event_id, normalised player) -> the open
    # bets already on the log, so a later sheet can block against them.
    open_pg = {}
    for b in bets[bets.status.eq("open")].itertuples():
        try:
            ln = f"{float(b.line):g}"
        except (TypeError, ValueError):
            ln = str(b.line)
        open_pg.setdefault((str(b.event_id), norm(b.player)), []).append(
            (b.key, f"{b.market} {b.side} {ln}"))
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
        key = f"{s.event_id}_{s.market}_{norm(s.player)}_{side}"

        blocking, advisory = [], []
        if missed_slates:
            blocking.append(f"PANEL_STALE({len(missed_slates)} slate(s))")
        if r.days_stale > MAX_PLAYER_STALE_D:
            blocking.append(f"STALE_PLAYER({int(r.days_stale)}d)")
        if not r.evteam_ok:
            blocking.append("TEAM_MISMATCH")
        elif r.team_changed:
            blocking.append("TEAM_CHANGED")
        ok, why = at_opener(s, side)
        if not ok:
            blocking.append(why)
        if r.ev_fd > MAX_SANE_EV:
            blocking.append(f"SUSPECT_EV({r.ev_fd:.0%})")
        prior = [d for k, d in open_pg.get((str(s.event_id), norm(s.player)),
                                           []) if k != key]
        if prior:
            blocking.append(f"PLAYER_ALREADY_BET({'; '.join(prior)})")
        if r.role_min:
            advisory.append("ROLE_MIN?")
        if r.role_start:
            advisory.append("ROLE_START?")

        cost = s.fd_over_cost if side == "over" else s.fd_under_cost
        line = s.fd_line_over
        dec = float(fp.american_dec(cost))
        # at the FanDuel line, the line this bet is actually struck at -
        # p_over_news is quoted at the consensus line and disagrees with
        # ev_fd whenever the two books hang different numbers
        p_side = r.p_side_fd
        # half the claimed edge -> implied shrunk probability -> 1/4 Kelly
        e_half = r.ev_fd / 2.0
        p_shrunk = (1 + e_half) / dec
        f_k = max((dec * p_shrunk - 1) / (dec - 1), 0.0)
        stake = min(0.25 * f_k * bankroll, 0.05 * bankroll)
        stake = max(round(stake * 2) / 2, 0.5)
        rows.append({
            "key": key, "date": r.date, "tip": s.tip,
            "event_id": s.event_id, "market": s.market, "player": s.player,
            "team": s.bp_team, "game": f"{s.visitor}@{s.home}",
            "side": side, "fd_line": line, "fd_cost": cost,
            "model_p": round(p_side, 4), "ev": round(r.ev_fd, 4),
            "strong": True, "min_odds_3pct": min_amer(p_side, 0.03),
            "min_odds_6pct": min_amer(p_side, 0.06),
            "stake": 0.0 if blocking else stake,
            "mu_model": r.mu_news, "mu_open": "",
            "open_line": s.open_line,
            "flags": ";".join(blocking + advisory),
            "play": (not blocking) and key not in logged,
            "already_bet": key in logged,
        })
    picks = pd.DataFrame(rows)
    if len(picks):
        # one bet per player per game: keep the highest-EV PLAYABLE market
        # (a blocked row must not shadow a clean one for the same player)
        picks = (picks.sort_values(["play", "ev"], ascending=False)
                 .drop_duplicates(["player", "date"]).reset_index(drop=True)
                 .sort_values("ev", ascending=False))
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
    n_gated = int((picks.stake == 0.0).sum()) if len(picks) else 0
    print(f"\npicks.csv: {len(picks)} qualifying (EV>10% at FanDuel), "
          f"{n_play} new playable, {n_gated} gated")
    if len(picks):
        print(picks[["date", "player", "market", "side", "fd_line",
                     "fd_cost", "ev", "stake", "play", "flags"]]
              .to_string(index=False))


if __name__ == "__main__":
    main()
