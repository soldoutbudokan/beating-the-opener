"""Stage A for the cricket revisit (PROGRESS.md "Cricket revisit"): match
Polymarket match-winner markets to Cricsheet matches and score the market's
own prices at PRE-REGISTERED timestamps against results.

Matching (never by name alone): a market's candidate matches are Cricsheet
matches within +/-2 days of the market's labelled game date (the *date* of the
label is reliable; its hour is not); both outcome names must map to the two
teams by token containment with a small alias table; ambiguous or unmatched
markets are dropped, never guessed (AUDIT H1/C2 discipline).

Timestamps (registered before any model): the in-play onset is the first
10-minute point at which the price has moved > 0.08 from 60 minutes earlier
or hit an extreme (>0.97 / <0.03), after >= 6 pre-points; the toss sits ~30
min before the start and the price starts moving at the toss, so
  close = last price <= onset - 45 min   (pre-toss)
  open  = last price <= onset - 24 h     (the day-before price)
Markets whose onset date disagrees with the Cricsheet date by > 1 day (UTC vs
local) are dropped. Outcome: Cricsheet winner; no-result/tie rows dropped.

Usage: python3 src/pm_benchmark.py --match-only     # build the crosswalk
       python3 src/pm_benchmark.py                  # + benchmark table
"""
import argparse
import json
import os
import re

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
PM = os.path.join(ROOT, "data", "raw", "polymarket")
EPS = 1e-9
ALIASES = {  # polymarket token -> cricsheet substring (lowercase)
    "bangalore": "bengaluru", "bengaluru": "bengaluru",
    "pindiz": "rawalpindi", "rawalpindi": "rawalpindi",
    "usa": "united states of america", "uae": "united arab emirates",
    "png": "papua new guinea", "west indies": "west indies",
    "mi cape town": "mi cape town", "mi new york": "mi new york",
    "la knight riders": "los angeles knight riders",
    "sf unicorns": "san francisco unicorns",
    "london spirit": "london spirit", "welsh fire": "welsh fire",
    "southern brave": "southern brave", "birmingham phoenix": "birmingham phoenix",
    "oval invincibles": "oval invincibles", "trent rockets": "trent rockets",
    "hobart hurricanes": "hobart hurricanes",
}


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).lower()).strip()


def team_hit(outcome, team):
    o, t = norm(outcome), norm(team)
    for k, v in ALIASES.items():          # token-wise aliases (bangalore -> bengaluru)
        if k in o:
            o = o.replace(k, v)
    if o in t:
        return True
    # all outcome tokens (len>=3) appear in the team name
    toks = [w for w in o.split() if len(w) >= 3]
    return bool(toks) and all(w in t for w in toks)


def _date(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    try:
        ts = pd.Timestamp(str(v)[:10])
        return None if pd.isna(ts) else ts.date()
    except Exception:  # noqa: BLE001
        return None


def game_date(row):
    return _date(row.get("game_start_label"))


def date_span(row):
    """Label-less markets (2024 vintage): search from market creation to its
    resolution deadline / close for a UNIQUE match between the two teams."""
    lo = _date(row.get("accepting_orders_ts")) or _date(row.get("start_date"))
    hi = _date(row.get("closed_time")) or _date(row.get("end_date"))
    if lo is None or hi is None:
        return None
    return lo, hi + pd.Timedelta(days=1)


def match_markets(mk, cs):
    cs = cs.copy()
    cs["d"] = pd.to_datetime(cs.date).dt.date
    cs["female"] = cs.get("gender", pd.Series("male", index=cs.index)).eq("female")
    by_date = {d: g for d, g in cs.groupby("d")}
    rows = []
    for r in mk.to_dict("records"):
        try:
            outs = json.loads(r["outcomes"])
        except Exception:  # noqa: BLE001
            continue
        gd = game_date(r)
        if gd is not None:
            # tiered window: +/-1 day first; widen to +/-2 only if that found nothing
            tiers = [[gd + pd.Timedelta(days=off) for off in (-1, 0, 1)],
                     [gd + pd.Timedelta(days=off) for off in (-2, 2)]]
        else:
            span = date_span(r)
            if span is None or (span[1] - span[0]).days > 45:
                continue
            tiers = [list(pd.date_range(span[0], span[1]).date)]
        cands = []
        want_female = "women" in (r.get("question") or "").lower()
        for days in tiers:
            for dday in days:
                g = by_date.get(dday)
                if g is None:
                    continue
                g = g[g.female == want_female]
                for c in g.itertuples():
                    a = team_hit(outs[0], c.team1) and team_hit(outs[1], c.team2)
                    b = team_hit(outs[0], c.team2) and team_hit(outs[1], c.team1)
                    if a or b:
                        cands.append((c.match_id, c.comp, c.date, c.team1, c.team2,
                                      c.winner, c.result, "12" if a else "21"))
            if cands:
                break
        uniq = {c[0]: c for c in cands}
        if len(uniq) != 1:
            rows.append({"market_id": r["market_id"], "status": "unmatched" if not uniq else "ambiguous",
                         "n_cands": len(uniq)})
            continue
        m = list(uniq.values())[0]
        rows.append({"market_id": r["market_id"], "status": "ok", "n_cands": 1,
                     "match_id": m[0], "comp": m[1], "cs_date": m[2], "team1": m[3],
                     "team2": m[4], "winner": m[5], "result": m[6],
                     "outcome0_is_team1": m[7] == "12"})
    return pd.DataFrame(rows)


def onset_time(t, p):
    """First 10-min point moving > 0.08 vs 60 min earlier or hitting an
    extreme, after >= 6 pre-points. Returns the timestamp 60 min earlier
    (the last quiet point) or None."""
    for i in range(6, len(t)):
        j = max(0, i - 6)
        if abs(p[i] - p[j]) > 0.08 or p[i] > 0.97 or p[i] < 0.03:
            return t[j]
    return None


def benchmark(mk, xw, prices):
    prices = prices.sort_values(["market_id", "t"])
    out = []
    for r in xw[xw.status == "ok"].merge(mk, on="market_id").to_dict("records"):
        g = prices[prices.market_id == r["market_id"]]
        if len(g) < 30:
            continue
        t, p = g.t.to_numpy(), g.p.to_numpy(float)
        on = onset_time(t, p)
        if on is None:
            continue
        on_dt = pd.Timestamp(on, unit="s", tz="UTC")
        if abs((on_dt.date() - pd.Timestamp(r["cs_date"]).date()).days) > 1:
            continue
        pre_close = p[t <= on - 45 * 60]
        pre_open = p[t <= on - 24 * 3600]
        if not len(pre_close) or not len(pre_open):
            continue
        if r["result"] != "normal" or not r["winner"]:
            continue
        y = 1 if (r["winner"] == (r["team1"] if r["outcome0_is_team1"] else r["team2"])) else 0
        out.append({"market_id": r["market_id"], "comp": r["comp"], "date": r["cs_date"],
                    "p_open": float(pre_open[-1]), "p_close": float(pre_close[-1]),
                    "y": y, "volume": r["volume"], "n_pre": int((t <= on).sum()),
                    "onset_utc": on_dt, "lead_h": float((on - t[0]) / 3600)})
    return pd.DataFrame(out)


def ll(p, y):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def clustered_t(diff, dates):
    g = pd.DataFrame({"d": diff, "date": dates}).groupby("date")["d"]
    means, sizes = g.mean(), g.size()
    w = sizes / sizes.sum()
    mu = float((means * w).sum())
    var = float(((means - mu) ** 2 * w ** 2).sum())
    return mu, mu / np.sqrt(var) if var > 0 else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-only", action="store_true")
    ap.add_argument("--min-volume", type=float, default=5000.0)
    args = ap.parse_args()
    mk = pd.read_parquet(os.path.join(PM, "markets.parquet"))
    cs = pd.read_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    xw = match_markets(mk, cs)
    xw.to_parquet(os.path.join(ROOT, "data", "pm_crosswalk.parquet"), index=False)
    ok = xw[xw.status == "ok"]
    print(f"markets {len(mk)} -> matched {len(ok)} ({len(ok)/len(mk):.1%}), "
          f"ambiguous {(xw.status=='ambiguous').sum()}, unmatched {(xw.status=='unmatched').sum()}")
    print("matched by comp:", ok.comp.value_counts().to_dict())
    un = xw[xw.status != "ok"].merge(mk[["market_id", "question", "outcomes", "game_start_label", "closed"]], on="market_id")
    print("closed-but-unmatched sample:")
    for r in un[un.closed].head(25).itertuples():
        print(f"  {r.status:9s} {str(r.game_start_label)[:10]} {r.question[:70]} {r.outcomes}")
    if args.match_only:
        return
    prices = pd.read_parquet(os.path.join(PM, "prices.parquet"))
    prices["market_id"] = prices.market_id.astype(str)
    b = benchmark(mk, xw, prices)
    b = b[b.volume >= args.min_volume]
    b.to_parquet(os.path.join(ROOT, "data", "pm_benchmark.parquet"), index=False)
    b["ll_open"], b["ll_close"] = ll(b.p_open, b.y), ll(b.p_close, b.y)
    print(f"\nbenchmark rows: {len(b)} (volume >= {args.min_volume:,.0f}); "
          f"median first-quote lead {b.lead_h.median():.0f}h")
    d, t = clustered_t((b.ll_open - b.ll_close).values, b.date)
    moved = (b.p_close - b.p_open).abs() > 0.01
    right = np.sign(b.p_close - b.p_open) == np.sign(b.y - 0.5)
    print(f"LL(open T-24h)={b.ll_open.mean():.5f}  LL(close pre-toss)={b.ll_close.mean():.5f}  "
          f"open-close={d:+.5f} (clustered t={t:.1f})  cal open {100*(b.p_open.mean()-b.y.mean()):+.1f}pp")
    print(f"moved (>1pp) {moved.mean():.1%}; of moved, pointing at the winner {right[moved].mean():.1%} (n={int(moved.sum())})")
    for c, g in b.groupby("comp"):
        d2, t2 = clustered_t((g.ll_open - g.ll_close).values, g.date)
        print(f"  {c:5s} n={len(g):4d} LL(open)={g.ll_open.mean():.4f} open-close={d2:+.4f} (t={t2:.1f}) "
              f"vol med ${g.volume.median():,.0f}")


if __name__ == "__main__":
    main()
