"""Join graded props + panel features + market-implied means -> modelset.

One row per prop (event x market x player) with:
  open:  line, devigged p_over, implied mean mu_open, book, created ts
  close: consensus (book 0) and FanDuel (book 10) line/p_over/mu
  panel: player EW rates, minutes, team/opp context, rest, absences
  actual + void

Output: data/modelset.pkl
"""
import os
import re
import unicodedata

import numpy as np
import pandas as pd

from odds_utils import amer_to_prob, devig_power
from dist_utils import implied_mu

ROOT = os.path.join(os.path.dirname(__file__), "..")

PANEL_FEATS = [
    "min_ewf", "min_ews", "poi_ewf", "poi_ews", "reb_ewf", "reb_ews",
    "ass_ewf", "ass_ews", "tpm_ewf", "tpm_ews", "tpa_ewf", "fga_ewf",
    "fta_ewf", "ste_ewf", "blo_ewf", "tur_ewf",
    "poi_rate_ewf", "reb_rate_ewf", "ass_rate_ewf", "tpm_rate_ewf",
    "ste_rate_ewf", "blo_rate_ewf", "tur_rate_ewf",
    "gp", "rest", "started_ewf", "home",
    "tm_pace_ew", "tm_pts_for_ew", "tm_pts_against_ew",
    "opp_pace_ew", "opp_pts_for_ew", "opp_pts_against_ew", "opp_tpa_for_ew",
    "absent_ew_min", "absent_prior_ew_min",
]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def side_frame(graded, book, tag):
    g = graded[(graded.book == book) & ~graded.is_off
               & graded.line.notna() & graded.over_cost.notna()
               & graded.under_cost.notna()].copy()
    g = g.drop_duplicates(["event_id", "market", "player"])
    g[f"p_{tag}"] = devig_power(amer_to_prob(g.over_cost),
                                amer_to_prob(g.under_cost))
    # coherent = one real two-way quote: same line both sides, sane vig.
    # A book's over main at one line paired with its under main at another
    # fabricates the implied mean (AUDIT C1/N4).
    bs = amer_to_prob(g.over_cost) + amer_to_prob(g.under_cost)
    g[f"coh_{tag}"] = ((g.line == g.line_under)
                       & (bs >= 1.00) & (bs <= 1.15))
    g = g.rename(columns={"line": f"line_{tag}", "over_cost": f"oc_{tag}",
                          "under_cost": f"uc_{tag}", "updated": f"upd_{tag}"})
    return g[["event_id", "market", "player", f"line_{tag}", f"oc_{tag}",
              f"uc_{tag}", f"p_{tag}", f"upd_{tag}", f"coh_{tag}"]]


def main():
    graded = pd.read_pickle(os.path.join(ROOT, "data", "graded.pkl"))
    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))

    base = graded[graded.open_line.notna() & graded.open_over_cost.notna()
                  & graded.open_under_cost.notna()].drop_duplicates(
        ["event_id", "market", "player"]).copy()
    base["p_open"] = devig_power(amer_to_prob(base.open_over_cost),
                                 amer_to_prob(base.open_under_cost))
    base["open_booksum"] = (amer_to_prob(base.open_over_cost)
                            + amer_to_prob(base.open_under_cost))
    # a trustworthy opener: same book, same line, sane vig (AUDIT C1/H2)
    base["open_coherent"] = ((base.open_line_over == base.open_line_under)
                             & (base.open_book_over == base.open_book_under)
                             & (base.open_booksum >= 1.00)
                             & (base.open_booksum <= 1.15))
    base = base[["event_id", "season", "date", "market", "player", "team",
                 "pos", "open_line", "open_over_cost", "open_under_cost",
                 "p_open", "open_book", "open_created", "open_booksum",
                 "open_coherent", "actual", "void", "matched"]]

    for book, tag in [(0, "close"), (10, "fd")]:
        base = base.merge(side_frame(graded, book, tag),
                          on=["event_id", "market", "player"], how="left")

    # market-implied means, per market family
    for mkt in base.market.unique():
        m = base.market == mkt
        base.loc[m, "mu_open"] = implied_mu(mkt, base.loc[m, "open_line"],
                                            base.loc[m, "p_open"])
        c = m & base.p_close.notna()
        base.loc[c, "mu_close"] = implied_mu(mkt, base.loc[c, "line_close"],
                                             base.loc[c, "p_close"])

    # panel features by (normalized name, date +/- 1)
    panel = panel.copy()
    panel["nname"] = panel.athlete_display_name.map(norm)
    panel["pdate"] = panel.game_date.dt.strftime("%Y-%m-%d")
    pidx = panel.set_index(["nname", "pdate"])
    pidx = pidx[~pidx.index.duplicated()][PANEL_FEATS]
    base["nname"] = base.player.map(norm)

    def lookup(r):
        # r.date is the ET game date (build_props); +/-1 is a rare-skew
        # fallback only, and -1 is probed before +1 so a missed join can
        # never prefer the player's NEXT game (AUDIT H1)
        for delta in (0, -1, 1):
            d = str((pd.Timestamp(r.date) + pd.Timedelta(days=delta)).date())
            try:
                return pidx.loc[(r.nname, d)]
            except KeyError:
                continue
        return pd.Series(np.nan, index=PANEL_FEATS)

    feats = base.apply(lookup, axis=1)
    ms = pd.concat([base.reset_index(drop=True),
                    feats.reset_index(drop=True)], axis=1)
    ms.to_pickle(os.path.join(ROOT, "data", "modelset.pkl"))

    print(f"modelset: {len(ms)} props, "
          f"{ms.p_close.notna().mean():.1%} with consensus close, "
          f"{ms.p_fd.notna().mean():.1%} with FD close, "
          f"{ms.min_ewf.notna().mean():.1%} with panel features")
    print(ms.groupby("market").size().to_string())


if __name__ == "__main__":
    main()
