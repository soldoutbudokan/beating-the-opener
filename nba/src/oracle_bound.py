"""Upper bound on what better PLAYER RATINGS could ever buy.

Rather than spend hours reconstructing lineups from play-by-play to improve RAPM,
ask the question that bounds it: if the player ratings were *perfect*, would the
model beat the closing line?

We fit RAPM on the ENTIRE dataset -- including the held-out seasons -- giving each
player a rating no causal system could ever beat, since it is fit on the future.
Availability itself stays honest (who is dressed tonight, expected minutes from
prior games). If this oracle still loses to the line, then no player-rating work,
stint-level RAPM included, can close the gap.
"""
import os
import sys
import time

import numpy as np
import pandas as pd
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
import rapm  # noqa: E402
from market import add_market_probs, load_games_odds, log_loss_vec  # noqa: E402
from run_experiment import PRED_SEASONS, TEST, VAL, logit, wf_margin, wf_proba  # noqa: E402
from final_experiment import BASE_MODELS  # noqa: E402
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROT_MIN = 15.0


def oracle_rapm(games, pb, lam=120.0):
    """One ridge fit over ALL games -> look-ahead player ratings."""
    played = pb[pb.played].copy()
    tot = played.groupby(["game_id", "team"])["min"].transform("sum")
    played["share"] = played["min"] / tot.replace(0, np.nan)
    played = played[played.share.notna()]

    pids = sorted(played.pid.unique())
    pidx = {p: i for i, p in enumerate(pids)}
    n_p = len(pids)

    gm = games.set_index("game_id")[["home_abbr", "away_abbr", "margin"]]
    played = played.join(gm, on="game_id")
    played = played[played.home_abbr.notna()]
    played["sign"] = np.where(played.team == played.home_abbr, 1.0, -1.0)

    gids = played.game_id.unique()
    grow = {g: i for i, g in enumerate(gids)}
    rows = played.game_id.map(grow).values
    cols = played.pid.map(pidx).values
    vals = played.sign.values * played.share.values

    y = gm.reindex(gids).margin.values.astype(float)
    X = np.zeros((len(gids), n_p + 1))
    np.add.at(X, (rows, cols), vals)
    X[:, n_p] = 1.0
    A = X.T @ X
    reg = np.eye(n_p + 1) * lam
    reg[n_p, n_p] = 1e-6
    sol = np.linalg.solve(A + reg, X.T @ y)
    return pids, pidx, sol[:n_p], float(sol[n_p])


def oracle_features(games, pb, pidx, beta, rot_min=ROT_MIN, rot_games=8, window=10):
    """Availability features valued with the oracle ratings."""
    played = pb[pb.played]
    appear = {(g, t): set(x.pid for x in grp.itertuples())
              for (g, t), grp in played.groupby(["game_id", "team"])}
    minutes = {(r.game_id, r.team, r.pid): r.min for r in played.itertuples(index=False)}

    g = games.sort_values("date_utc").reset_index(drop=True)
    ewma_min, n_seen = {}, defaultdict(int)
    recent = defaultdict(lambda: deque(maxlen=window))
    out = []
    for r in g.itertuples(index=False):
        feat = {"game_id": r.game_id}
        for side, team in (("home", r.home_abbr), ("away", r.away_abbr)):
            pool = set()
            for s in recent[team]:
                pool |= s
            rot = [p for p in pool
                   if n_seen[p] >= rot_games and ewma_min.get(p, 0.0) >= rot_min]
            act = appear.get((r.game_id, team), set())
            avail = [p for p in rot if p in act]
            miss = [p for p in rot if p not in act]

            def val(p):
                i = pidx.get(p)
                b = beta[i] if i is not None else 0.0
                return float(b) * ewma_min.get(p, 0.0) / 48.0

            feat[f"{side}_orc_avail"] = sum(val(p) for p in avail)
            feat[f"{side}_orc_missing"] = sum(val(p) for p in miss)
            feat[f"{side}_orc_star_out"] = max([val(p) for p in miss], default=0.0)
            w = np.array([ewma_min.get(p, 0.0) for p in avail], dtype=float)
            bb = np.array([beta[pidx[p]] if p in pidx else 0.0 for p in avail])
            feat[f"{side}_orc_rating"] = float(np.dot(w / w.sum(), bb)) if w.sum() > 0 else 0.0
        feat["orc_avail_diff"] = feat["home_orc_avail"] - feat["away_orc_avail"]
        feat["orc_missing_diff"] = feat["away_orc_missing"] - feat["home_orc_missing"]
        feat["orc_rating_diff"] = feat["home_orc_rating"] - feat["away_orc_rating"]
        feat["orc_star_diff"] = feat["away_orc_star_out"] - feat["home_orc_star_out"]
        out.append(feat)

        for team in (r.home_abbr, r.away_abbr):
            act = appear.get((r.game_id, team), set())
            if act:
                recent[team].append(set(act))
            for p in act:
                mn = minutes.get((r.game_id, team, p))
                if mn is not None and np.isfinite(mn):
                    ewma_min[p] = mn if p not in ewma_min else 0.8 * ewma_min[p] + 0.2 * mn
                n_seen[p] += 1
    return pd.DataFrame(out)


def main():
    t0 = time.time()
    base = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_final_v4.csv"))
    base = base[base.season_type.isin([2, 3, 5])].copy()
    base["game_id"] = base.game_id.astype(str)

    df = load_games_odds()
    d = df[df.season_type.isin([2, 3, 5])].copy()
    d["game_id"] = d.game_id.astype(str)
    pb = rapm.load_player_minutes()

    pids, pidx, beta, hfa = oracle_rapm(d, pb)
    print(f"oracle RAPM fit on ALL {len(d)} games, {len(pids)} players "
          f"(hfa {hfa:.2f})  [{time.time()-t0:.0f}s]")
    nm = pb.drop_duplicates("pid").set_index("pid")["name"]
    s = pd.Series(beta, index=pids).sort_values(ascending=False)
    print("  top 5 oracle:", ", ".join(nm.reindex(s.head(5).index).tolist()))

    of = oracle_features(d, pb, pidx, beta)
    of["game_id"] = of.game_id.astype(str)
    X = base.merge(of, on="game_id").reset_index(drop=True)
    print(f"oracle features merged: {X.shape}  [{time.time()-t0:.0f}s]")

    cols = [c for c in M.blind_features(X) if not c.startswith(("A_", "B_"))]
    names = []
    for name, f in BASE_MODELS.items():
        c = f"O_{name}"
        X[c] = wf_proba(X, cols, f, PRED_SEASONS)
        names.append(c)
    for name, f in (("marg_gbm", M.mk_gbm_reg), ("marg_ridge", M.mk_ridge)):
        c = f"O_{name}"
        X[c] = wf_margin(X, cols, f, PRED_SEASONS)
        names.append(c)

    ok = X[names].notna().all(axis=1)
    val = X[X.season_year.isin(VAL) & ok]
    st = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                       LogisticRegression(C=1.0, max_iter=3000))
    st.fit(logit(val[names].values), val.home_win.values)
    X["O_stack"] = np.nan
    X.loc[ok, "O_stack"] = st.predict_proba(logit(X.loc[ok, names].values))[:, 1]

    t = X[X.season_year.isin(TEST) & X.mkt_mult.notna() & X.O_stack.notna()]
    y = t.home_win.values
    print(f"\n=== ORACLE-RATED MODEL vs CLOSING LINE (n={len(t)}) ===")
    for c, lab in [("mkt_mult", "MARKET closing line"), ("O_stack", "ORACLE model"),
                   ("B_stack", "Honest Tier B model")]:
        e = M.evaluate(y, t[c].values, lab)
        print(f"  {lab:22s} logloss={e['logloss']:.5f} brier={e['brier']:.5f} acc={e['acc']:.4f}")
    s = M.paired_test(y, t.O_stack.values, t.mkt_mult.values)
    flag = "BEATS" if s["ci_hi"] < 0 else ("loses" if s["ci_lo"] > 0 else "tied")
    print(f"\n  oracle vs market: dLL={s['mean_diff']:+.5f} "
          f"CI[{s['ci_lo']:+.5f},{s['ci_hi']:+.5f}] p={s['p_two_sided']:.4f} [{flag}]")
    print("\n  The oracle is fit on the held-out seasons themselves; no causal player")
    print("  rating system can do better. If it loses, player-rating work cannot win.")


if __name__ == "__main__":
    main()
