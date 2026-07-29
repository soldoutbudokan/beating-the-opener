"""Join graded props + panel features + market-implied means -> modelset.

One row per consensus prop (event x market x player, book 0) with a COHERENT
open (same book + same line both sides, booksum 1.00-1.15 - a mispaired open
fabricates the implied mean, AUDIT C1/H2/N4), non-void, actual present:
  open:   line/costs/book/created, devigged p_open, mu_open
  close:  book-0 close (+ coh_close flag), FanDuel book-10 close (trade cell)
  target: y = (mu_close - mu_open) / scale, coherent closes only, clipped +/-4
  panel:  role features joined by (native_id, nname, role) - NEVER name+date
          (AUDIT H1/C2)
  gap_ew: (mu_open - box-score EW projection) / scale

Distribution params (sigma / NegBin r) are fitted here on panel rows strictly
before EVAL_END (env; default = earliest odds date, fully out-of-sample to
all odds rows) and saved to data/dist_params_<sport>.json for train_eval.

Output: data/modelset_<sport>.pkl
Usage:  python3 src/build_modelset.py --sport MLB
"""
import argparse
import os

import numpy as np
import pandas as pd

import dist_utils
from dist_utils import EW_PROJ, implied_mu
from grade_props import STAT_COLS, norm
from odds_utils import amer_to_prob, devig_power

ROOT = os.path.join(os.path.dirname(__file__), "..")

MARKETS = {  # v1 model markets; order fixes mkt_i
    "MLB": ["strikeouts", "outs_recorded", "hits_allowed", "walks_allowed",
            "earned_runs", "hits", "total_bases", "hrr"],
    "NBA": ["points", "rebounds", "assists", "threes", "steals", "blocks",
            "pra", "pts_ast", "pts_reb", "reb_ast"],
}

PANEL_FEATS = {  # single source - train_eval imports it (wnba double-def lesson)
    "MLB": {
        "pitcher": ["k_ewf", "k_ews", "outs_ewf", "outs_ews", "bf_ewf",
                    "bf_ews", "pit_ewf", "pit_ews", "bb_ewf", "bb_ews",
                    "ha_ewf", "ha_ews", "er_ewf", "er_ews", "k_bf_ewf",
                    "bb_bf_ewf", "rest", "gp", "home", "post",
                    "tm_runs_ew", "tm_so_pa_ew", "opp_runs_ew", "opp_so_pa_ew"],
        "batter": ["h_ewf", "h_ews", "tb_ewf", "tb_ews", "hr_ewf", "hr_ews",
                   "r_ewf", "r_ews", "rbi_ewf", "rbi_ews", "pa_ewf", "pa_ews",
                   "h_pa_ewf", "tb_pa_ewf", "hr_pa_ewf", "so_pa_ewf",
                   "ord_ewf", "rest", "gp", "home", "post",
                   "tm_runs_ew", "tm_so_pa_ew", "opp_runs_ew", "opp_so_pa_ew"],
    },
    "NBA": {
        "skater": ["min_ewf", "min_ews", "pts_ewf", "pts_ews", "reb_ewf",
                   "reb_ews", "ast_ewf", "ast_ews", "tpm_ewf", "tpm_ews",
                   "stl_ewf", "stl_ews", "blk_ewf", "blk_ews", "rest", "gp",
                   "home", "post", "started_ewf",
                   "tm_pace_ew", "tm_pts_for_ew", "tm_pts_against_ew",
                   "opp_pace_ew", "opp_pts_for_ew", "opp_pts_against_ew"],
    },
}

KEEP = ["event_id", "season", "season_type", "date", "market", "player",
        "player_id", "team", "pos", "native_id", "role", "nname", "mkt_i",
        "open_line", "open_over_cost", "open_under_cost", "open_book",
        "open_created", "open_booksum", "open_coherent", "p_open",
        "line_close", "oc_close", "uc_close", "upd_close", "p_close",
        "coh_close", "line_fd", "oc_fd", "uc_fd", "p_fd", "upd_fd", "coh_fd",
        "actual", "void", "matched", "mu_open", "mu_close", "scale", "y"]


def side_frame(graded, book, tag):
    """A book's close as one coherent two-way quote (wnba side_frame)."""
    g = graded[(graded.book == book) & ~graded.is_off.fillna(False)
               & graded.line.notna() & graded.over_cost.notna()
               & graded.under_cost.notna()].copy()
    cols = ["event_id", "market", "player", f"line_{tag}", f"oc_{tag}",
            f"uc_{tag}", f"p_{tag}", f"upd_{tag}", f"coh_{tag}"]
    if not len(g):
        return pd.DataFrame(columns=cols)
    g = g.drop_duplicates(["event_id", "market", "player"])
    g[f"p_{tag}"] = devig_power(amer_to_prob(g.over_cost),
                                amer_to_prob(g.under_cost))
    bs = amer_to_prob(g.over_cost) + amer_to_prob(g.under_cost)
    g[f"coh_{tag}"] = (g.line == g.line_under) & (bs >= 1.00) & (bs <= 1.15)
    g = g.rename(columns={"line": f"line_{tag}", "over_cost": f"oc_{tag}",
                          "under_cost": f"uc_{tag}", "updated": f"upd_{tag}"})
    return g[cols]


def leakage_guard(ms, sport, thresh=0.12):
    """|corr(feature, outcome residual)| tripwire (nba build_dataset_v4
    pattern). Legit EW features sit ~0.02-0.06; > 0.12 means a feature read
    the current game -> halt (PLAN Phase 2 tripwire)."""
    resid = ((ms.actual - ms.mu_open) / ms.scale).to_numpy(float)
    bad = []
    for role, cols in PANEL_FEATS[sport].items():
        rm = (ms.role == role).to_numpy()
        if not rm.any():
            continue
        for c in cols + ["gap_ew"]:
            v = ms.loc[rm, c].astype(float).to_numpy()
            if not np.isfinite(v).any() or np.nanstd(v) == 0:
                continue
            r = np.corrcoef(np.nan_to_num(v), resid[rm])[0, 1]
            if abs(r) > thresh:
                bad.append((role, c, r))
    if bad:
        for role, c, r in sorted(bad, key=lambda x: -abs(x[2])):
            print(f"  LEAK? {role}/{c}: corr {r:+.4f}")
        raise RuntimeError(f"leakage guard tripped on {len(bad)} feature(s)")
    print(f"leakage guard: clean (|corr| <= {thresh} for all features)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(STAT_COLS))
    args = ap.parse_args()
    sport = args.sport
    if sport not in MARKETS:
        raise NotImplementedError(
            f"{sport}: model markets not defined - add {sport} to MARKETS/"
            f"PANEL_FEATS here and to dist_utils.FAMILY (MLB/NBA only for now)")
    sl = sport.lower()
    mkts = MARKETS[sport]

    # graded_<sport>.pkl = props + event_map (native_id) + actual/void/matched
    graded = pd.read_pickle(os.path.join(ROOT, "data", f"graded_{sl}.pkl"))
    panel = pd.read_pickle(os.path.join(ROOT, "data", f"panel_{sl}.pkl"))
    g = graded[graded.market.isin(mkts)]

    base = g[(g.book == 0) & g.open_line.notna() & g.open_over_cost.notna()
             & g.open_under_cost.notna()].drop_duplicates(
        ["event_id", "market", "player"]).copy()
    bs = amer_to_prob(base.open_over_cost) + amer_to_prob(base.open_under_cost)
    base["open_booksum"] = bs
    # a trustworthy opener: same book, same line, sane vig (AUDIT C1/H2)
    base["open_coherent"] = ((base.open_line_over == base.open_line_under)
                             & (base.open_book_over == base.open_book_under)
                             & (bs >= 1.00) & (bs <= 1.15))
    n_all = len(base)
    base = base[base.open_coherent & ~base.void.fillna(True)
                & base.actual.notna() & base.native_id.notna()].copy()
    print(f"base: {len(base)}/{n_all} consensus props "
          f"(coherent open, graded, non-void)")
    if not len(base):
        raise RuntimeError(f"{sport}: no coherent graded consensus props - "
                           f"archive mid-backfill? (rerun after grade_props)")
    base["p_open"] = devig_power(amer_to_prob(base.open_over_cost),
                                 amer_to_prob(base.open_under_cost))

    # book-0 close lives on the base row itself
    co = (base.line.notna() & base.over_cost.notna() & base.under_cost.notna()
          & ~base.is_off.fillna(False)).to_numpy()
    cbs = amer_to_prob(base.over_cost) + amer_to_prob(base.under_cost)
    base["coh_close"] = (co & (base.line == base.line_under).to_numpy()
                         & (cbs >= 1.00) & (cbs <= 1.15))
    base["p_close"] = np.nan
    if co.any():
        base.loc[co, "p_close"] = devig_power(
            amer_to_prob(base.loc[co, "over_cost"]),
            amer_to_prob(base.loc[co, "under_cost"]))
    base = base.rename(columns={"line": "line_close", "over_cost": "oc_close",
                                "under_cost": "uc_close", "updated": "upd_close"})

    # FanDuel close (book 10) - the tradeable cell (AUDIT H3)
    base = base.merge(side_frame(g, 10, "fd"),
                      on=["event_id", "market", "player"], how="left")

    # fit sigma / dispersion STRICTLY before the odds era (see docstring)
    cutoff = os.environ.get("EVAL_END") or str(base.date.min())
    sab = dist_utils.fit_sigma(panel, sport, cutoff)
    nbr = dist_utils.fit_dispersion(panel, sport, cutoff)
    print(f"dist params (panel rows < {cutoff}):")
    print(f"  sigma a,b: {sab}")
    print(f"  negbin r:  {nbr}  (None = Poisson fallback, r > 200)")
    dist_utils.dispersion_audit(panel, sport, cutoff)
    print(f"  saved -> {dist_utils.save_params(sport, cutoff)}")

    # market-implied means, per-family; scale = SD at mu_open
    for mkt in mkts:
        m = (base.market == mkt).to_numpy()
        if not m.any():
            continue
        base.loc[m, "mu_open"] = implied_mu(sport, mkt, base.loc[m, "open_line"],
                                            base.loc[m, "p_open"])
        base.loc[m, "scale"] = dist_utils.scale(sport, mkt,
                                                base.loc[m, "mu_open"])
        c = m & base.coh_close.to_numpy() & base.p_close.notna().to_numpy()
        if c.any():
            base.loc[c, "mu_close"] = implied_mu(sport, mkt,
                                                 base.loc[c, "line_close"],
                                                 base.loc[c, "p_close"])
    if "mu_close" not in base.columns:
        base["mu_close"] = np.nan
    # standardized move target; +/-4 SD cap so one mispriced close can't own
    # the L2 loss (wnba v2 left it unclipped)
    base["y"] = ((base.mu_close - base.mu_open) / base.scale).clip(-4, 4)

    base["mkt_i"] = base.market.map({m: i for i, m in enumerate(mkts)})
    base["role"] = base.market.map({m: STAT_COLS[sport][m][0] for m in mkts})
    base["nname"] = base.player.map(norm)  # same norm as grading
    base["native_id"] = base.native_id.astype("int64")

    # panel join by (native_id, nname, role) - no date probing (AUDIT H1/C2)
    feat_cols = sorted({c for cols in PANEL_FEATS[sport].values() for c in cols})
    pidx = panel.copy()
    pidx["native_id"] = pidx.native_id.astype("int64")
    pidx = pidx.drop_duplicates(["native_id", "nname", "role"])[
        ["native_id", "nname", "role"] + feat_cols]
    ms = base[KEEP].merge(pidx, on=["native_id", "nname", "role"], how="left")

    # gap_ew: where the opener disagrees with box-score fundamentals
    proj = np.full(len(ms), np.nan)
    for mkt in mkts:
        cols = EW_PROJ[sport][mkt]
        m = (ms.market == mkt).to_numpy()
        if m.any():
            proj[m] = ms.loc[m, cols].sum(axis=1, min_count=len(cols))
    ms["gap_ew"] = (ms.mu_open - proj) / ms.scale

    leakage_guard(ms, sport)

    ms.to_pickle(os.path.join(ROOT, "data", f"modelset_{sl}.pkl"))
    print(f"\nmodelset: {len(ms)} props "
          f"({ms.date.min()} .. {ms.date.max()})")
    print(f"  coherent consensus close: {ms.coh_close.mean():.1%}, "
          f"FD close: {ms.p_fd.notna().mean():.1%}, "
          f"FD-sourced open: {(ms.open_book == 10).mean():.1%}")
    for role in sorted(PANEL_FEATS[sport]):
        rm = ms.role == role
        if rm.any():
            sent = PANEL_FEATS[sport][role][0]
            print(f"  {role}: {rm.sum()} rows, panel join "
                  f"{ms.loc[rm, sent].notna().mean():.1%}")
    print(ms.groupby("market").size().to_string())


if __name__ == "__main__":
    main()
