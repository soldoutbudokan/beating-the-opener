"""Score tonight's WNBA props against the model -> live/picks.csv.

Run AFTER the data refresh chain (see live/PROTOCOL.md):
  fetch_wehoop -> scrape_bettingpros -> build_props -> grade_props
  -> features -> build_modelset -> THIS -> settle_bets

Anchors on each prop's OPENING line (as in the backtest), trains the v2 move
model on the full graded archive (open-safe feature set), and computes EV at
FanDuel's CURRENT price. Lists EV >= 3%; 'strong' (notify) at EV >= 6%.
Integer lines are skipped (push handling not worth it; FD props are half-lines).

Prints NEW_PICKS if the strong-pick set changed since last run, else NO_CHANGE.
"""
import glob
import gzip
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from odds_utils import amer_to_prob, amer_to_dec, devig_power, apply_shade
from dist_utils import implied_mu, p_over, sigma, POISSON
from features import load_player_box, load_team_box, build_panel
from build_modelset import PANEL_FEATS, norm
from grade_props import BP2WH
from train_eval_v2 import add_v2_features, EW_PROJ, fit_shades
from train_eval import MARKETS, prepare

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIVE = os.path.join(ROOT, "live")
API = "https://api.bettingpros.com/v3"
KEY = "CHi8Hy5CEE4khd46XNYL23dCFX96oUdw6qOt1Dnh"

MARKET_IDS = {"points": 393, "rebounds": 397, "assists": 391, "threes": 390,
              "pra": 396, "pts_ast": 394, "pts_reb": 395, "reb_ast": 398}
EV_LIST, EV_STRONG = 0.03, 0.06
BOOKSUM_LO, BOOKSUM_HI = 1.00, 1.15  # sane two-way vig band (AUDIT C1)
KELLY_FRACTION, MAX_STAKE_FRAC = 0.25, 0.10
# open-safe: absent_ew_min needs tonight's roster, unknowable pre-tip
LIVE_PANEL_FEATS = [c for c in PANEL_FEATS if c != "absent_ew_min"]
V2_EXTRA = ["move_mom", "move_mom_all", "gap_ew"]


def get(url, tries=6):
    """Fetch with the same backoff scrape_bettingpros.py uses.

    The BettingPros API 504s intermittently on /offers (~1 call in 5 on
    2026-08-18), and a single miss used to kill the whole run. The failures
    look like upstream cache misses: a URL that 504s twice then serves 200
    and keeps serving it, so tries/backoff are sized to ride that out.
    Unlike the archiver this RAISES when every try fails instead of
    returning None: a silently-missing market would shrink the offer set
    fp_live prices from, and "highest-EV market only" would then pick from
    an incomplete sheet.
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "x-api-key": KEY, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError) as e:
            last = e
            if i == tries - 1:
                break
            wait = min(2 ** (i + 1), 8)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    raise last


def fetch_upcoming():
    """Events today/tomorrow (scheduled is UTC) not yet closed, + prop offers.

    LIVE_TEST_DATE=YYYY-MM-DD replays a past date's (closed) events through the
    identical path - closing snapshots stand in for live prices. Test only.
    """
    test_date = os.environ.get("LIVE_TEST_DATE")
    if test_date:
        start = end = pd.Timestamp(test_date).date()
    else:
        today = pd.Timestamp.now().normalize()
        start, end = today.date(), (today + pd.Timedelta(days=1)).date()
    evs = get(f"{API}/events?sport=WNBA&start={start}&end={end}").get("events", [])
    if not test_date:
        evs = [e for e in evs if e.get("status") != "closed"]
    offers = []
    for e in evs:
        for mkt, mid in MARKET_IDS.items():
            d = get(f"{API}/offers?sport=WNBA&market_id={mid}"
                    f"&event_id={e['id']}&location=ALL")
            for o in d.get("offers", []):
                o["_market"], o["_event"] = mkt, e
                offers.append(o)
    return evs, offers


def parse_offer(o):
    """-> dict with open line/costs + FD and consensus current prices."""
    pl = (o.get("participants") or [{}])[0].get("player") or {}
    e = o["_event"]
    # BP `scheduled` is UTC; the game date everywhere else in this repo
    # (wehoop, bets.csv match_date) is the ET date (AUDIT C2/H1)
    et_date = str(pd.Timestamp(e["scheduled"], tz="UTC")
                  .tz_convert("America/New_York").date())
    row = {"event_id": o["event_id"], "market": o["_market"],
           "player": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(),
           "bp_team": pl.get("team"), "pos": pl.get("position"),
           "home": e["home"], "visitor": e["visitor"],
           "date": et_date, "tip": e["scheduled"],
           "open_line_over": np.nan, "open_line_under": np.nan,
           "open_book_over": np.nan, "open_book_under": np.nan}
    for sel in o.get("selections", []):
        side = sel.get("selection")
        if side not in ("over", "under"):
            continue
        op = sel.get("opening_line") or {}
        row.setdefault("open_line", op.get("line"))
        row[f"open_{side}_cost"] = op.get("cost")
        row[f"open_line_{side}"] = op.get("line")
        row[f"open_book_{side}"] = op.get("book_id")
        row.setdefault("open_book", op.get("book_id"))
        for b in sel.get("books", []):
            if b["id"] not in (0, 10):
                continue
            tag = "fd" if b["id"] == 10 else "cons"
            for ln in b.get("lines", []):
                if ln.get("main") and not ln.get("is_off") and ln.get("active"):
                    row[f"{tag}_line_{side}"] = ln.get("line")
                    row[f"{tag}_{side}_cost"] = ln.get("cost")
    return row


def train_model():
    """v2 move model on the full graded archive, open-safe features."""
    ms = prepare()
    ms = add_v2_features(ms)
    tr = ms[ms.move.notna()]
    cols = LIVE_PANEL_FEATS + V2_EXTRA + ["mkt_i", "mu_open", "open_line",
                                          "open_juice", "open_book"]
    X = tr.reindex(columns=cols).to_numpy(float)
    model = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
        min_samples_leaf=60, l2_regularization=1.0, random_state=0)
    model.fit(X, tr.move.to_numpy())
    # per-market over-shade from the graded archive (all strictly past at
    # pick time): WNBA prop prices overstate P(over) by ~2pp and the model
    # anchors on them, so every quoted P(over) gets the correction (AUDIT N1)
    shades = fit_shades(ms, np.ones(len(ms), bool))
    # momentum state for tonight: EW over each player's full history (no shift)
    mom = (ms.sort_values("date").groupby(["player", "market"])["move"]
           .apply(lambda s: s.ewm(alpha=0.25, min_periods=1).mean().iloc[-1]))
    mom_all = (ms.sort_values("date").groupby("player")["move"]
               .apply(lambda s: s.ewm(alpha=0.15, min_periods=1).mean().iloc[-1]))
    return model, cols, mom, mom_all, shades


def fixture_panel(props):
    """Stub rows for tonight's player-games -> features as of today."""
    box = load_player_box()
    tb = load_team_box()
    latest = box.sort_values("game_date").groupby("athlete_id").tail(1)
    by_name = {n: r for n, r in zip(latest.athlete_display_name.map(norm),
                                    latest.itertuples())}
    tid_by_abbr = (box.sort_values("game_date").groupby("team_abbreviation")
                   .tail(1).set_index("team_abbreviation")["team_id"].to_dict())
    stubs, meta = [], []
    for i, r in enumerate(props.itertuples()):
        pr = by_name.get(norm(r.player))
        wh_home = BP2WH.get(r.home, r.home)
        wh_vis = BP2WH.get(r.visitor, r.visitor)
        wh_team = BP2WH.get(r.bp_team, r.bp_team)
        if pr is None or wh_team not in (wh_home, wh_vis):
            meta.append(None)
            continue
        is_home = wh_team == wh_home
        opp = wh_vis if is_home else wh_home
        if opp not in tid_by_abbr or wh_team not in tid_by_abbr:
            meta.append(None)
            continue
        # deterministic stub id (hash() is salted per process for str ids);
        # game_date is the actual ET game date, not now() - a game 1-2 days
        # out was getting its `rest` understated (AUDIT follow-up)
        gid = -(1000 + int(r.event_id) % 100000)
        stubs.append({
            "game_id": gid, "game_date": pd.Timestamp(r.date),
            "athlete_id": pr.athlete_id,
            "athlete_display_name": pr.athlete_display_name,
            "team_id": tid_by_abbr[wh_team], "team_abbreviation": wh_team,
            "opponent_team_id": tid_by_abbr[opp],
            "opponent_team_abbreviation": opp,
            "home_away": "home" if is_home else "away",
            "did_not_play": False, "is_fixture": True, "starter": np.nan})
        meta.append((gid, pr.athlete_id))
    if not stubs:
        return pd.DataFrame(), meta
    sdf = pd.DataFrame(stubs).drop_duplicates(["game_id", "athlete_id"])
    box["is_fixture"] = False
    box2 = pd.concat([box, sdf], ignore_index=True)
    # team-box stubs so tm_/opp_ EWs exist for tonight
    tstubs = []
    for gid, sub in sdf.groupby("game_id"):
        for tid, oid in {(t, o) for t, o in zip(sub.team_id, sub.opponent_team_id)}:
            tstubs.append({"game_id": gid, "team_id": tid,
                           "opponent_team_id": oid,
                           "game_date": sub.game_date.iloc[0]})
    tb2 = pd.concat([tb, pd.DataFrame(tstubs)], ignore_index=True)
    fixture_gids = set(sdf.game_id)
    panel = build_panel(box2, tb2, fixture_gids)
    fix = panel[panel.is_fixture].set_index(["game_id", "athlete_id"])
    return fix, meta


def kelly(bankroll, p, dec):
    edge = p * dec - 1
    if edge <= 0:
        return 0.0
    stake = bankroll * KELLY_FRACTION * edge / (dec - 1)
    return round(min(stake, bankroll * MAX_STAKE_FRAC) * 2) / 2


def min_amer(p, thresh):
    """Cheapest American price giving EV >= thresh at model prob p."""
    dec = (1 + thresh) / p
    return int(np.ceil((dec - 1) * 100)) if dec >= 2 else -int(np.floor(100 / (dec - 1)))


def main():
    os.makedirs(LIVE, exist_ok=True)
    evs, offers = fetch_upcoming()
    print(f"upcoming events: {len(evs)}, offers: {len(offers)}")
    if not offers:
        print("NO_UPCOMING")
        return
    props = pd.DataFrame([parse_offer(o) for o in offers])
    props = props[props.open_line.notna() & props.open_over_cost.notna()
                  & props.open_under_cost.notna()].reset_index(drop=True)
    # C1 guard: BP stores over/under openers as independent records. Only a
    # SAME-book SAME-line pair with sane total vig is a real two-way quote;
    # anything else (e.g. FD o1.5 +194 x Fanatics u0.5 +150, booksum 0.74)
    # fabricates mu_open and manufactures phantom EV.
    booksum = (amer_to_prob(props.open_over_cost)
               + amer_to_prob(props.open_under_cost))
    coherent = ((props.open_line_over == props.open_line_under)
                & (props.open_book_over == props.open_book_under)
                & (booksum >= BOOKSUM_LO) & (booksum <= BOOKSUM_HI))
    if (~coherent).any():
        print(f"dropped {int((~coherent).sum())} props with mispaired/"
              f"insane opening quotes (C1 guard)")
    props = props[coherent].reset_index(drop=True)

    # only FanDuel-sourced openers: EV computed off another book's open
    # (Novig etc.) is untradeable on FanDuel - the honest backtest cell is
    # FD-opens-only (AUDIT H3) - and this makes the stale-price gate below
    # a same-book comparison instead of FD-vs-someone-else's-open.
    n0 = len(props)
    props = props[props.open_book == 10].reset_index(drop=True)
    print(f"FanDuel-sourced openers: {len(props)}/{n0}")

    model, cols, mom, mom_all, shades = train_model()
    fix, meta = fixture_panel(props)

    bankroll = 100.0
    bk_path = os.path.join(LIVE, "bankroll.json")
    if os.path.exists(bk_path):
        bankroll = json.load(open(bk_path))["current"]
    # size off capital not already at risk (open stakes were invisible to
    # Kelly before), and know which keys are already played for dedupe
    logged_keys = set()
    bets_path = os.path.join(LIVE, "bets.csv")
    if os.path.exists(bets_path):
        prior = pd.read_csv(bets_path)
        logged_keys = set(prior.key)
        open_stake = pd.to_numeric(
            prior.loc[prior.status == "open", "stake"], errors="coerce").sum()
        bankroll = max(bankroll - float(open_stake), 0.0)

    rows = []
    mkt_i = {m: i for i, m in enumerate(MARKETS)}
    for r, m in zip(props.itertuples(), meta):
        if m is None or r.market not in mkt_i:
            continue
        try:
            f = fix.loc[m]
        except KeyError:
            continue
        p_open = float(devig_power(amer_to_prob(r.open_over_cost),
                                   amer_to_prob(r.open_under_cost)))
        mu_open = float(implied_mu(r.market, np.array([r.open_line]),
                                   np.array([p_open]))[0])
        scale = (np.sqrt(max(mu_open, 0.3)) if r.market in POISSON
                 else float(sigma(r.market, np.array([mu_open]))[0]))
        proj_cols = EW_PROJ[r.market]
        proj = float(f[proj_cols].sum()) if f[proj_cols].notna().all() else np.nan
        feat = {c: f.get(c, np.nan) for c in LIVE_PANEL_FEATS}
        feat.update({
            "move_mom": mom.get((r.player, r.market), np.nan),
            "move_mom_all": mom_all.get(r.player, np.nan),
            "gap_ew": (mu_open - proj) / scale if not np.isnan(proj) else np.nan,
            "mkt_i": mkt_i[r.market], "mu_open": mu_open,
            "open_line": r.open_line, "open_juice": p_open - 0.5,
            "open_book": r.open_book})
        X = np.array([[feat[c] for c in cols]], float)
        mu_model = mu_open + float(model.predict(X)[0]) * scale
        for side in ("over", "under"):
            fd_line = getattr(r, f"fd_line_{side}", np.nan)
            fd_cost = getattr(r, f"fd_{side}_cost", np.nan)
            if pd.isna(fd_line) or pd.isna(fd_cost) or fd_line == int(fd_line):
                continue
            # only bet STALE prices: the backtest edge is the open->close move,
            # so once FanDuel moves the line/juice the opportunity is gone
            # (the model does NOT beat moved/closing prices)
            open_cost = getattr(r, f"open_{side}_cost", np.nan)
            if fd_line != r.open_line or pd.isna(open_cost) \
                    or abs(fd_cost - open_cost) > 15:
                continue
            # the model must predict a move TOWARD the bet side: EV may not
            # come from the shade correction alone (the shade drifts
            # quarter to quarter), and with a sane quote an "edge" against
            # the predicted move can only be a broken open (AUDIT C1/N1)
            if (side == "over") != (mu_model > mu_open):
                continue
            p_over_m = float(p_over(r.market, np.array([mu_model]),
                                    np.array([fd_line]))[0])
            # market over-shade correction (AUDIT N1), same expanding
            # per-market fit the backtest uses
            p_over_m = float(apply_shade(p_over_m, shades.get(r.market, 0.0)))
            p_side = p_over_m if side == "over" else 1 - p_over_m
            dec = float(amer_to_dec(fd_cost))
            ev = p_side * dec - 1
            if ev < EV_LIST:
                continue
            rows.append({
                "key": f"{r.event_id}_{r.market}_{norm(r.player)}_{side}",
                "date": r.date, "tip": r.tip, "event_id": r.event_id,
                "market": r.market, "player": r.player, "team": r.bp_team,
                "game": f"{r.visitor}@{r.home}", "side": side,
                "fd_line": fd_line, "fd_cost": int(fd_cost),
                "model_p": round(p_side, 4), "ev": round(ev, 4),
                "strong": ev >= EV_STRONG,
                "min_odds_3pct": min_amer(p_side, 0.03),
                "min_odds_6pct": min_amer(p_side, 0.06),
                "stake": kelly(bankroll, p_side, dec),
                "mu_model": round(mu_model, 2), "mu_open": round(mu_open, 2),
                "open_line": r.open_line})
    picks = pd.DataFrame(rows).sort_values("ev", ascending=False) if rows \
        else pd.DataFrame(columns=["key", "strong"])
    if len(picks):
        # one bet per player per game (combo markets on one player are
        # heavily correlated): only the top-EV row per (event, player) is
        # playable - the rest stay on the sheet for context
        picks["play"] = ~picks.duplicated(["event_id", "player"])
        # protocol's no-duplicate-notification rule, enforced in code
        picks["already_bet"] = picks.key.isin(logged_keys)
    picks.to_csv(os.path.join(LIVE, "picks.csv"), index=False)
    print(f"picks: {len(picks)} (strong: {int(picks.strong.sum()) if len(picks) else 0})")

    strong_keys = sorted(picks[picks.strong & picks.play
                               & ~picks.already_bet].key) if len(picks) else []
    meta_path = os.path.join(LIVE, "picks_meta.json")
    prev = json.load(open(meta_path))["strong"] if os.path.exists(meta_path) else []
    json.dump({"strong": strong_keys,
               "updated": str(pd.Timestamp.now())}, open(meta_path, "w"))
    print("NEW_PICKS" if strong_keys and strong_keys != prev else "NO_CHANGE")


if __name__ == "__main__":
    main()
