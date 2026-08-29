"""Archive Polymarket cricket MATCH-WINNER markets + their price histories.

Source (verified 2026-08-29 from the owner's Mac; egress-blocked in the cloud
container): Gamma API for market metadata, CLOB API for timestamped prices.
  events:  https://gamma-api.polymarket.com/events?tag_slug=cricket&closed=...
           &limit=100&offset=N&start_date_min=..&start_date_max=..
           (offset caps at ~2,000 -> walk monthly start-date windows)
  prices:  https://clob.polymarket.com/prices-history?market=<clobTokenId>
           &startTs=..&endTs=..&fidelity=10   (window <= 14 days; interval=max
           returns nothing for resolved markets)
Facts that shape the archive: the first quote lands a median ~3 days before
the match (93% >= 24h); `gameStartTime` is a per-league default slot and is
wrong by hours on ~30% of markets (doubleheaders, qualifiers) — the true
start / toss is recovered downstream from the price path (in-play onset),
never trusted from the label. Match-winner markets = exactly two NAMED
outcomes (not Yes/No) and a non-prop question. Props (most sixes, top batter,
toss double, 200+ runs, champion futures) are enumerated but not archived.

Output (committed — the feed is the benchmark; ~2k markets, ~2M price rows
at 10-min fidelity fit in a few MB of parquet):
  data/raw/polymarket/markets.parquet   one row per match-winner market
  data/raw/polymarket/prices.parquet    (market_id, t, p) for outcome[0]
Idempotent: markets are re-listed every run (metadata refreshes: closedTime,
outcomePrices); price rows are fetched only for markets with no rows yet or
still open at the last fetch. Refuses to shrink either file.

Usage: python3 src/fetch_polymarket.py [--since 2024-05-01] [--no-prices]
"""
import argparse
import datetime as dt
import json
import os
import time

import pandas as pd
import requests

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "polymarket")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
PAUSE = 0.5
FIDELITY = 10
WINDOW = dt.timedelta(days=13)
PROP_WORDS = ("sixes", "top batter", "top bowler", "toss", "200+", "runs",
              "wickets", "double", "champion", "winner of", "to win the",
              "most ", "total ", "over ", "under ", "century", "fifty",
              "player of", "will ", "advance", "qualify", "finalist")


def get(url, params=None, tries=6):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                print(f"  400 {r.text[:120]}", flush=True)
                return None
            print(f"  retry {i+1}: HTTP {r.status_code} {r.text[:80]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  retry {i+1}: {e}", flush=True)
        time.sleep(4 * (i + 1))
    return None


def month_windows(since, until):
    d = dt.date(since.year, since.month, 1)
    while d <= until:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d.isoformat(), nxt.isoformat()
        d = nxt


def is_match_winner(m):
    try:
        outs = json.loads(m.get("outcomes") or "[]")
    except Exception:  # noqa: BLE001
        return False
    if len(outs) != 2 or {o.strip().lower() for o in outs} <= {"yes", "no"}:
        return False
    q = (m.get("question") or "").lower()
    return not any(w in q for w in PROP_WORDS)


def list_markets(since):
    rows = []
    seen = set()
    until = dt.date.today() + dt.timedelta(days=90)
    for closed in ("true", "false"):
        for lo, hi in month_windows(since, until):
            offset = 0
            while True:
                d = get(f"{GAMMA}/events", {
                    "tag_slug": "cricket", "closed": closed, "limit": 100,
                    "offset": offset, "start_date_min": f"{lo}T00:00:00Z",
                    "start_date_max": f"{hi}T00:00:00Z"})
                if not d:
                    break
                for e in d:
                    for m in e.get("markets") or []:
                        if m.get("id") in seen or not is_match_winner(m):
                            continue
                        seen.add(m.get("id"))
                        rows.append({
                            "market_id": str(m.get("id")),
                            "event_id": str(e.get("id")),
                            "event_title": e.get("title"),
                            "event_slug": e.get("slug"),
                            "question": m.get("question"),
                            "outcomes": m.get("outcomes"),
                            "clob_token_ids": m.get("clobTokenIds"),
                            "outcome_prices": m.get("outcomePrices"),
                            "game_start_label": m.get("gameStartTime"),
                            "start_date": m.get("startDate"),
                            "accepting_orders_ts": m.get("acceptingOrdersTimestamp"),
                            "end_date": m.get("endDate"),
                            "closed_time": m.get("closedTime"),
                            "closed": bool(m.get("closed")),
                            "volume": m.get("volumeNum"),
                            "liquidity": m.get("liquidityNum"),
                            "uma_status": m.get("umaResolutionStatus"),
                        })
                offset += 100
                time.sleep(PAUSE)
                if len(d) < 100:
                    break
            print(f"{closed} {lo}: {len(rows)} markets", flush=True)
    return pd.DataFrame(rows)


def price_history(token, t0, t1):
    out = []
    a = t0
    while a < t1:
        b = min(a + WINDOW, t1)
        d = get(f"{CLOB}/prices-history", {
            "market": token, "startTs": int(a.timestamp()),
            "endTs": int(b.timestamp()), "fidelity": FIDELITY})
        for x in (d or {}).get("history") or []:
            out.append((x["t"], x["p"]))
        a = b
        time.sleep(PAUSE)
    return out


def parse_ts(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    if s.endswith("+00"):
        s += ":00"
    try:
        return pd.Timestamp(s).tz_convert("UTC")
    except Exception:  # noqa: BLE001
        return pd.Timestamp(s).tz_localize("UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-05-01")
    ap.add_argument("--no-prices", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    mpath = os.path.join(OUT, "markets.parquet")
    ppath = os.path.join(OUT, "prices.parquet")
    old_m = pd.read_parquet(mpath) if os.path.exists(mpath) else pd.DataFrame()
    old_p = pd.read_parquet(ppath) if os.path.exists(ppath) else pd.DataFrame(
        columns=["market_id", "t", "p"])

    mk = list_markets(dt.date.fromisoformat(args.since))
    if len(old_m) and len(mk) < len(old_m):
        raise RuntimeError("markets.parquet would shrink - refusing")
    mk.to_parquet(mpath, index=False)
    print(f"markets.parquet: {len(mk)} match-winner markets "
          f"({mk.closed.sum()} closed)", flush=True)
    if args.no_prices:
        return

    have = set(old_p.market_id.astype(str)) if len(old_p) else set()
    still_open = set(old_m[~old_m.closed].market_id.astype(str)) if len(old_m) else set()
    todo = mk[~mk.market_id.isin(have) | mk.market_id.isin(still_open)]
    print(f"prices: {len(todo)} markets to fetch ({len(have)} already have rows)",
          flush=True)
    now = pd.Timestamp.now(tz="UTC")
    new = []
    for i, r in enumerate(todo.itertuples(), 1):
        try:
            toks = json.loads(r.clob_token_ids or "[]")
        except Exception:  # noqa: BLE001
            toks = []
        if not toks:
            continue
        t0 = parse_ts(r.accepting_orders_ts) or parse_ts(r.start_date)
        t1 = parse_ts(r.closed_time) or now
        if t0 is None:
            continue
        t1 = min(t1 + pd.Timedelta(hours=1), now)
        hist = price_history(toks[0], t0.to_pydatetime(), t1.to_pydatetime())
        new += [(r.market_id, t, p) for t, p in hist]
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} markets, {len(new)} new rows", flush=True)
            pd.concat([old_p[~old_p.market_id.isin(still_open)],
                       pd.DataFrame(new, columns=["market_id", "t", "p"])]
                      ).to_parquet(ppath, index=False)
    allp = pd.concat([old_p[~old_p.market_id.isin(still_open)],
                      pd.DataFrame(new, columns=["market_id", "t", "p"])])
    allp = allp.drop_duplicates(["market_id", "t"]).sort_values(["market_id", "t"])
    if len(old_p) and len(allp) < len(old_p):
        raise RuntimeError("prices.parquet would shrink - refusing")
    allp.to_parquet(ppath, index=False)
    print(f"prices.parquet: {len(allp)} rows, {allp.market_id.nunique()} markets",
          flush=True)
    print("POLYMARKET_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
