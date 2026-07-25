"""Live pipeline: refresh data, retrain on all history, score upcoming fixtures.

Differences from the research scripts (train_eval_v4):
  - The market anchor is Pinnacle early if present, else the market average
    (football-data dropped Pinnacle in Jan 2026); training rows are built the
    same way so live and train distributions match.
  - Momentum/closing anchor: Pinnacle close if present, else average close.
Outputs live/picks.csv and prints a summary ending in NEW_PICKS or NO_CHANGE.
"""
import hashlib
import io
import json
import os
import sys
import urllib.request
from collections import defaultdict, deque

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
import build_dataset
from download_data import BASE as SEASON_URL, DIVS
from features import DIV_BASE, DIV_IDX, EW_ALPHA, HOME_ADV, K, elo_expect
from odds_utils import devig_shin

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw")
LIVE = os.path.join(ROOT, "live")
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

# leagues assumed available on FanDuel (edit freely)
FANDUEL_LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "D1", "D2", "I1", "I2",
                   "SP1", "SP2", "F1", "F2", "N1", "B1", "P1", "T1", "G1"]
CURRENT_SEASONS = ["2526", "2627"]
MOM_ALPHA = 0.15
ENS_W_STACK = 0.4  # weight chosen by the walk-forward's adaptive rule (v4)
EV_STRONG = 0.01   # avg-book EV that makes a pick "strong" (notify)
EV_SHEET = 0.02    # max-book EV to appear on the sheet at all
KELLY_FRACTION = 0.25
MAX_STAKE_FRac = 0.10

FUND = ["elo_diff", "elo_exp_h", "att_edge_h", "att_edge_a",
        "sot_edge_h", "sot_edge_a", "ew_stf_h", "ew_sta_h", "ew_stf_a", "ew_sta_a",
        "form_h", "form_a", "rest_h", "rest_a", "n_played_h", "n_played_a",
        "overround_anchor"]
XCOLS = FUND + ["mom_h", "mom_a", "nm_h", "nm_a",
                "dis_h", "dis_a", "dis_missing", "disavg_h", "disavg_a",
                "p_over", "ou_missing", "p_ah_h", "ah_line", "ah_missing"]
GBM_COLS = XCOLS + ["div_idx"]


def fetch(url, dest=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    if dest and len(body) > 500:
        with open(dest, "wb") as f:
            f.write(body)
    return body


def refresh_data():
    for season in CURRENT_SEASONS:
        for div in DIVS:
            try:
                fetch(SEASON_URL.format(season=season, div=div),
                      os.path.join(RAW, f"{season}_{div}.csv"))
            except Exception:
                pass  # season file may not exist yet
    body = fetch(FIXTURES_URL)
    fx = pd.read_csv(io.BytesIO(body), encoding="utf-8-sig",
                     encoding_errors="replace", on_bad_lines="skip")
    fx.columns = [c.strip().lstrip("﻿") for c in fx.columns]
    return fx


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def anchor_probs(df, ps_cols, avg_cols):
    """Devigged probs from Pinnacle if present, else market average."""
    ps = df[ps_cols].to_numpy(float)
    avg = df[avg_cols].to_numpy(float)
    p = np.full((len(df), 3), np.nan)
    ok_ps = ~np.isnan(ps).any(axis=1)
    ok_avg = ~np.isnan(avg).any(axis=1) & ~ok_ps
    if ok_ps.any():
        p[ok_ps] = devig_shin(ps[ok_ps])
    if ok_avg.any():
        p[ok_avg] = devig_shin(avg[ok_avg])
    over = np.where(ok_ps, (1 / ps).sum(axis=1) - 1,
                    np.where(ok_avg, (1 / avg).sum(axis=1) - 1, np.nan))
    return p, over


def three_way_feats(df, zo, tag, cols):
    o = df[cols].to_numpy(float)
    ok = ~np.isnan(o).any(axis=1)
    z = np.zeros_like(zo)
    if ok.any():
        pz = devig_shin(o[ok])
        z[ok] = np.log(pz[:, [0, 2]] / pz[:, [1]]) - zo[ok]
    return z, ok


def build_rows(df, is_fixture):
    """Chronological state pass; returns feature frame aligned to df."""
    elo, ew_gf, ew_ga, ew_stf, ew_sta = {}, {}, {}, {}, {}
    form = defaultdict(lambda: deque(maxlen=5))
    last_date, n_played = {}, defaultdict(int)
    mom, nmom = {}, {}

    p_anchor, over = anchor_probs(df, ["PSH", "PSD", "PSA"], ["EAvgH", "EAvgD", "EAvgA"])
    ok_a = ~np.isnan(p_anchor).any(axis=1)
    zo = np.full((len(df), 2), np.nan)
    zo[ok_a] = np.log(p_anchor[ok_a][:, [0, 2]] / p_anchor[ok_a][:, [1]])
    p_close, _ = anchor_probs(df, ["PSCH", "PSCD", "PSCA"], ["AvgCH", "AvgCD", "AvgCA"])
    ok_c = ~np.isnan(p_close).any(axis=1)
    zc = np.full((len(df), 2), np.nan)
    zc[ok_c] = np.log(p_close[ok_c][:, [0, 2]] / p_close[ok_c][:, [1]])

    rows = []
    dates = df["Date"].to_numpy()
    for i, r in enumerate(df.itertuples(index=False)):
        h, a, div = r.HomeTeam, r.AwayTeam, r.Div
        base = DIV_BASE.get(div, 1400)
        eh = elo.setdefault(h, float(base)); ea = elo.setdefault(a, float(base))
        gfh = ew_gf.get(h, 1.3); gah = ew_ga.get(h, 1.3)
        gfa = ew_gf.get(a, 1.3); gaa = ew_ga.get(a, 1.3)
        stfh = ew_stf.get(h, 4.4); stah = ew_sta.get(h, 4.4)
        stfa = ew_stf.get(a, 4.4); staa = ew_sta.get(a, 4.4)
        d = pd.Timestamp(dates[i])
        rest_h = min((d - last_date[h]).days, 30) if h in last_date else 30
        rest_a = min((d - last_date[a]).days, 30) if a in last_date else 30
        diff = eh + HOME_ADV - ea
        rows.append((
            diff, elo_expect(diff), gfh - gaa, gfa - gah,
            stfh - staa, stfa - stah, stfh, stah, stfa, staa,
            (sum(form[h]) / len(form[h])) if form[h] else 1.1,
            (sum(form[a]) / len(form[a])) if form[a] else 1.1,
            rest_h, rest_a, min(n_played[h], 100), min(n_played[a], 100),
            over[i],
            mom.get(h, 0.0), mom.get(a, 0.0),
            min(nmom.get(h, 0), 50), min(nmom.get(a, 0), 50),
        ))
        if is_fixture[i]:
            continue  # no result yet: never update state
        hg, ag = r.FTHG, r.FTAG
        res = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        margin = np.log1p(abs(hg - ag)) if hg != ag else 1.0
        delta = K * margin * (res - elo_expect(diff))
        elo[h] = eh + delta; elo[a] = ea - delta
        ew_gf[h] = (1 - EW_ALPHA) * gfh + EW_ALPHA * hg
        ew_ga[h] = (1 - EW_ALPHA) * gah + EW_ALPHA * ag
        ew_gf[a] = (1 - EW_ALPHA) * gfa + EW_ALPHA * ag
        ew_ga[a] = (1 - EW_ALPHA) * gaa + EW_ALPHA * hg
        hst, ast = r.HST, r.AST
        if not (np.isnan(hst) or np.isnan(ast)):
            ew_stf[h] = (1 - EW_ALPHA) * stfh + EW_ALPHA * hst
            ew_sta[h] = (1 - EW_ALPHA) * stah + EW_ALPHA * ast
            ew_stf[a] = (1 - EW_ALPHA) * stfa + EW_ALPHA * ast
            ew_sta[a] = (1 - EW_ALPHA) * staa + EW_ALPHA * hst
        form[h].append(3.0 if res == 1 else (1.0 if res == 0.5 else 0.0))
        form[a].append(3.0 if res == 0 else (1.0 if res == 0.5 else 0.0))
        last_date[h] = d; last_date[a] = d
        n_played[h] += 1; n_played[a] += 1
        if ok_a[i] and ok_c[i]:
            mv = zc[i] - zo[i]
            mom[h] = (1 - MOM_ALPHA) * mom.get(h, 0.0) + MOM_ALPHA * mv[0]
            mom[a] = (1 - MOM_ALPHA) * mom.get(a, 0.0) + MOM_ALPHA * mv[1]
            nmom[h] = nmom.get(h, 0) + 1
            nmom[a] = nmom.get(a, 0) + 1

    cols = ["elo_diff", "elo_exp_h", "att_edge_h", "att_edge_a",
            "sot_edge_h", "sot_edge_a", "ew_stf_h", "ew_sta_h", "ew_stf_a", "ew_sta_a",
            "form_h", "form_a", "rest_h", "rest_a", "n_played_h", "n_played_a",
            "overround_anchor", "mom_h", "mom_a", "nm_h", "nm_a"]
    feat = pd.DataFrame(rows, columns=cols, index=df.index)

    zb, okb = three_way_feats(df, np.nan_to_num(zo), "dis", ["B365H", "B365D", "B365A"])
    feat["dis_h"], feat["dis_a"] = zb[:, 0], zb[:, 1]
    feat["dis_missing"] = (~okb).astype(float)
    za, _ = three_way_feats(df, np.nan_to_num(zo), "disavg", ["EAvgH", "EAvgD", "EAvgA"])
    feat["disavg_h"], feat["disavg_a"] = za[:, 0], za[:, 1]

    inv_ov, inv_un = 1 / df["EOv"], 1 / df["EUn"]
    feat["p_over"] = (inv_ov / (inv_ov + inv_un)).to_numpy()
    feat["ou_missing"] = feat["p_over"].isna().astype(float)
    inv_h, inv_a = 1 / df["EAHH"], 1 / df["EAHA"]
    feat["p_ah_h"] = (inv_h / (inv_h + inv_a)).to_numpy()
    feat["ah_line"] = df["EAHh"].to_numpy()
    feat["ah_missing"] = feat[["p_ah_h", "ah_line"]].isna().any(axis=1).astype(float)
    feat["div_idx"] = df["Div"].map(DIV_IDX).fillna(-1).to_numpy()
    return feat, zo, p_anchor


def quarter_kelly(p, odds, bankroll):
    edge = p * odds - 1
    if edge <= 0 or odds <= 1:
        return 0.0
    f = min(KELLY_FRACTION * edge / (odds - 1), MAX_STAKE_FRac)
    return round(bankroll * f, 2)


def main():
    os.makedirs(LIVE, exist_ok=True)
    print("refreshing data...", flush=True)
    fx_raw = refresh_data()
    build_dataset.main()
    hist = pd.read_pickle(os.path.join(ROOT, "data", "matches.pkl"))

    # fixtures -> same schema as historical rows
    fx = fx_raw[fx_raw["Div"].isin(FANDUEL_LEAGUES)].copy()
    fx["Date"] = pd.to_datetime(fx["Date"], format="mixed", dayfirst=True, errors="coerce")
    today = pd.Timestamp.now().normalize()
    fx = fx.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    if not os.environ.get("LIVE_TEST_ALLOW_PAST"):
        fx = fx[fx["Date"] >= today]
    for c in hist.columns:
        if c not in fx.columns:
            fx[c] = np.nan
    for c in ["EOv", "EUn", "EAHh", "EAHH", "EAHA"]:
        pass  # built below from fixture columns
    fx["EMaxH"], fx["EMaxD"], fx["EMaxA"] = fx["MaxH"], fx["MaxD"], fx["MaxA"]
    fx["EAvgH"], fx["EAvgD"], fx["EAvgA"] = fx["AvgH"], fx["AvgD"], fx["AvgA"]
    fx["EOv"] = fx["P>2.5"].fillna(fx["Avg>2.5"]) if "P>2.5" in fx_raw.columns else fx["Avg>2.5"]
    fx["EUn"] = fx["P<2.5"].fillna(fx["Avg<2.5"]) if "P<2.5" in fx_raw.columns else fx["Avg<2.5"]
    fx["EAHh"] = fx["AHh"]
    fx["EAHH"] = fx["PAHH"].fillna(fx["AvgAHH"]) if "PAHH" in fx_raw.columns else fx["AvgAHH"]
    fx["EAHA"] = fx["PAHA"].fillna(fx["AvgAHA"]) if "PAHA" in fx_raw.columns else fx["AvgAHA"]
    fx = fx[hist.columns.tolist()]

    both = pd.concat([hist, fx], ignore_index=True)
    both = both.sort_values(["Date", "Div", "HomeTeam"]).reset_index(drop=True)
    is_fixture = np.zeros(len(both), bool)
    is_fixture[-len(fx):] = True if len(fx) else False
    # concat then sort loses the split point; recompute: fixtures have no FTR
    is_fixture = both["FTR"].isna().to_numpy()

    feat, zo, p_anchor = build_rows(both, is_fixture)

    # training rows: completed, early+close anchors, 2012+
    p_close, _ = anchor_probs(both, ["PSCH", "PSCD", "PSCA"], ["AvgCH", "AvgCD", "AvgCA"])
    ok_a = ~np.isnan(zo).any(axis=1)
    ok_c = ~np.isnan(p_close).any(axis=1)
    tr = ~is_fixture & ok_a & ok_c & (both["Date"] >= "2012-07-01").to_numpy()
    te = is_fixture & ok_a
    y = both["FTR"].map({"H": 0, "D": 1, "A": 2}).to_numpy(float)

    zc_tr = np.log(p_close[tr][:, [0, 2]] / p_close[tr][:, [1]])
    X = feat[XCOLS].to_numpy(float)
    Xg = np.hstack([feat[GBM_COLS].to_numpy(float), np.nan_to_num(zo)])
    cat_idx = [GBM_COLS.index("div_idx")]

    print(f"training on {tr.sum()} matches; scoring {te.sum()} fixtures", flush=True)
    if te.sum() == 0:
        _write_picks(pd.DataFrame(), "no upcoming fixtures with odds")
        return

    imp = SimpleImputer(strategy="median").fit(X[tr])
    sc = StandardScaler().fit(imp.transform(X[tr]))
    lr = LogisticRegression(max_iter=2000, C=1.0)
    lr.fit(np.hstack([zo[tr], sc.transform(imp.transform(X[tr]))]), y[tr].astype(int))
    p_stack = lr.predict_proba(np.hstack([zo[te], sc.transform(imp.transform(X[te]))]))

    zhat = np.zeros((int(te.sum()), 2))
    for j in range(2):
        g = HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.05, min_samples_leaf=80,
            l2_regularization=1.0, max_leaf_nodes=31, early_stopping=False,
            categorical_features=cat_idx, random_state=7)
        g.fit(Xg[tr], zc_tr[:, j] - zo[tr][:, j])
        zhat[:, j] = zo[te][:, j] + g.predict(Xg[te])
    p_move = softmax(np.hstack([zhat[:, [0]], np.zeros((len(zhat), 1)), zhat[:, [1]]]))
    p_ens = softmax(ENS_W_STACK * np.log(np.clip(p_stack, 1e-9, 1))
                    + (1 - ENS_W_STACK) * np.log(np.clip(p_move, 1e-9, 1)))

    bankroll = 100.0
    bk_path = os.path.join(LIVE, "bankroll.json")
    if os.path.exists(bk_path):
        bankroll = json.load(open(bk_path))["current"]

    fxs = both[te].reset_index(drop=True)
    picks = []
    sides = ["H", "D", "A"]
    avg = fxs[["EAvgH", "EAvgD", "EAvgA"]].to_numpy(float)
    mx = fxs[["EMaxH", "EMaxD", "EMaxA"]].to_numpy(float)
    for i in range(len(fxs)):
        for j in range(3):
            p = p_ens[i, j]
            min2, min5 = 1.02 / p, 1.05 / p
            ev_avg = p * avg[i, j] - 1 if not np.isnan(avg[i, j]) else np.nan
            ev_max = p * mx[i, j] - 1 if not np.isnan(mx[i, j]) else np.nan
            if not (np.nan_to_num(ev_max, nan=-1) > EV_SHEET
                    or np.nan_to_num(ev_avg, nan=-1) > 0):
                continue
            picks.append({
                "key": f"{fxs.at[i,'Div']}|{fxs.at[i,'Date'].date()}|"
                       f"{fxs.at[i,'HomeTeam']}|{fxs.at[i,'AwayTeam']}|{sides[j]}",
                "date": fxs.at[i, "Date"].date(), "div": fxs.at[i, "Div"],
                "home": fxs.at[i, "HomeTeam"], "away": fxs.at[i, "AwayTeam"],
                "side": sides[j], "model_p": round(p, 4),
                "min_odds_2pct": round(min2, 2), "min_odds_5pct": round(min5, 2),
                "avg_odds": avg[i, j], "max_odds": mx[i, j],
                "ev_at_avg": round(ev_avg, 4) if not np.isnan(ev_avg) else np.nan,
                "strong": bool(np.nan_to_num(ev_avg, nan=-1) > EV_STRONG),
                "stake_at_min5": quarter_kelly(p, min5, bankroll),
                "stake_at_avg": quarter_kelly(p, avg[i, j], bankroll)
                if not np.isnan(avg[i, j]) else 0.0,
            })
    picks = pd.DataFrame(picks).sort_values("ev_at_avg", ascending=False) \
        if picks else pd.DataFrame()
    _write_picks(picks, f"{te.sum()} fixtures scored, bankroll=${bankroll:.2f}")


def _write_picks(picks, note):
    path = os.path.join(LIVE, "picks.csv")
    meta_path = os.path.join(LIVE, "picks_meta.json")
    old_hash = ""
    if os.path.exists(meta_path):
        old_hash = json.load(open(meta_path)).get("hash", "")
    if len(picks):
        picks.to_csv(path, index=False)
        content = picks[["key", "min_odds_2pct"]].to_csv(index=False)
    else:
        pd.DataFrame().to_csv(path, index=False)
        content = "empty"
    h = hashlib.md5(content.encode()).hexdigest()
    json.dump({"hash": h, "note": note}, open(meta_path, "w"))

    print(f"\n{note}")
    if len(picks):
        strong = picks[picks["strong"]]
        print(f"picks on sheet: {len(picks)} | strong (avg-book EV>1%): {len(strong)}")
        if len(strong):
            print(strong[["date", "div", "home", "away", "side", "model_p",
                          "min_odds_5pct", "stake_at_min5", "ev_at_avg"]]
                  .head(10).to_string(index=False))
    print("NEW_PICKS" if h != old_hash else "NO_CHANGE")


if __name__ == "__main__":
    main()
