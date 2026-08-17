"""Build docs/index.html - the at-a-glance scoreboard for the live experiment.

Reads the WNBA market's live/ files (bankroll, bets, picks, the projection
archive) plus the archived soccer experiment's records and renders one
self-contained page: bankroll, CLV and P&L over time (with process-change
markers), open positions, a filterable bet log, a per-player view of what
the model currently thinks (Players tab, from live/projections.csv), and
the research evidence behind the wedge.

Auto-generated - the last step of each market's settle_bets.py run regenerates
it, so never hand-edit docs/index.html. Stdlib only, no build step.

    python3 site/build_site.py              # -> docs/index.html
    python3 site/build_site.py --fragment X # body-only copy (previewing)

The page is written only when its content changes, and every timestamp on it
comes from the data (not the clock), so no-op runs produce no commit churn.
"""
import bisect
import csv
import datetime as dt
import html
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "index.html")
REPO = "https://github.com/soldoutbudokan/beating-the-opener"
PAGES = "https://soldoutbudokan.github.io/beating-the-opener/"

# Self-contained write-ups republished under docs/ so Pages can serve them.
EXTRA_PAGES = [(os.path.join("nba", "reports", "report.html"),
                "nba-report.html")]

# ---------------------------------------------------------------- config ----
# Research numbers are the published results of each subproject - sources in
# soccer/README.md, wnba/README.md, cricket/README.md, nba/README.md.

# Process changes, drawn as vertical rules on the time charts. The gates date
# also splits the bet log's "era" filter and the before/after comparison.
V3_LIVE = "2026-07-31"
GATES = "2026-08-08"
EVENTS = [(V3_LIVE, "v3 model live", False),
          (GATES, "pick gates", True)]

SOCCER = {
    "id": "soccer", "dir": "soccer", "label": "Soccer 1X2",
    "sport": "football-data leagues · FanDuel",
    "cancelled": "Cancelled 2026-07-28 before the first bet — the "
                 "post-Pinnacle replay (avg-book anchor, the regime live "
                 "would have run in) shows no edge over its own anchor.",
    "what": "Home/draw/away openers across ~20 European leagues, priced days "
            "before kickoff. The live experiment was cancelled before launch; "
            "the research record stays below.",
    "idle": "Experiment cancelled before launch — no bets were ever placed",
    # post-Pinnacle replay (train_eval_avg.py): model EV>2% .. EV>=5% cells
    # vs the avg close. Placebo-caveated - see soccer/README.md.
    "clv_band": (0.011, 0.020), "clv_band_note": "post-Pinnacle replay",
    "capture": 0.18, "odds_style": "decimal",
    "protocol": "soccer/live/PROTOCOL.md", "readme": "soccer/README.md",
    "runs": [("Status", "cancelled 2026-07-28, before the first bet"),
             ("Why", "post-Pinnacle replay: no edge over its own anchor"),
             ("Record", "research result below; no live bets were placed")],
}
WNBA = {
    "id": "wnba", "dir": "wnba", "label": "WNBA player props",
    "sport": "player props · FanDuel",
    "what": "Points, rebounds, assists and threes, priced from scratch by "
            "the v3 talent model (Kalman player states + news minutes "
            "overrides — the market's number is never an input). Betting "
            "FanDuel coherent quotes at claimed EV > 10% since 2026-07-31, "
            "through the 2026-08-08 pick gates.",
    "idle": "No props currently clear the EV>10% FanDuel trigger",
    # v3 dev (2025) at the live rule (EV>10%): realized ROI ~+10%, CLV vs
    # the RAW close -4.6% - negative is EXPECTED for an under-heavy sheet
    # fading an over-shaded close; clv_cal is the fair yardstick (AUDIT N1)
    "clv_band": (-0.065, -0.030), "clv_band_note": "v3 dev, raw close",
    "capture": None, "odds_style": "american",
    "protocol": "wnba/live/PROTOCOL.md", "readme": "wnba/README.md",
    "picks_note": "One bet per player per game (highest EV). Confirm the "
                  "player is in the lineup near tip — the sheet refreshes "
                  "hourly and can miss late scratches.",
    "runs": [("Routine", "news-watch hourly at :31 — archive, panel "
                         "refresh, overrides, picks, notify"),
             ("Status", "LIVE — v3 from-scratch talent model since "
                        "2026-07-31; pick gates since 2026-08-08"),
             ("Scoring", "CLV primary (raw + shade-adjusted), P&L "
                         "secondary; fp-prospective-1/2 LL tests "
                         "firewalled from betting")],
}
MARKETS = [SOCCER, WNBA]

LEDGER = [
    {"market": "NBA moneyline", "where": "3 held-out seasons (control)",
     "lazy_open": False, "live_close": True, "verdict": "no",
     "why": "Attention floods the market — even the opening moneyline is "
            "already sharp, so there is no stale price to take.",
     "capture": None, "link": "nba/",
     "report": ("nba-report.html", "Read the full write-up")},
    {"market": "Soccer 1X2", "where": "9 seasons, ~20 leagues",
     "lazy_open": True, "live_close": True, "verdict": "yes",
     "why": "Beaten out-of-sample in 9/9 Pinnacle-anchored seasons (sign test "
            "p = 0.0039) — but the post-Pinnacle replay (avg-book anchor, the "
            "regime live would have run in) shows no edge over its own "
            "anchor, so the live experiment was cancelled before its first "
            "bet.",
     "capture": 0.18, "link": "soccer/"},
    {"market": "WNBA props", "where": "2 seasons, 8 prop markets",
     "lazy_open": True, "live_close": True, "verdict": "yes",
     "why": "Opener beaten on log loss (date-clustered t = 4.8), but the "
            "FanDuel-tradeable cell is ~+3% ROI at t ≈ 0.5 — an edge too "
            "small for one season to confirm.",
     "capture": 0.55, "link": "wnba/"},
    {"market": "Cricket BBL", "where": "297 matches (control)",
     "lazy_open": None, "live_close": False, "verdict": "no",
     "why": "The close is no better than the open — lines move plenty, but "
            "toward toss noise, not winners.",
     "capture": None, "link": "cricket/"},
]

SOCCER_SIM = {
    "caption": "Post-Pinnacle replay (avg-book anchor + avg close — the live "
               "regime), flat 1u, 6 walk-forward seasons. The placebo bets "
               "the anchor's own probabilities: CLV it also collects is "
               "best-of-book envelope shopping, not model skill.",
    "cols": ["price source", "cell", "bets", "ROI", "CLV", "CLV+ seasons"],
    "rows": [["best-of-book early", "model EV>2%", "21,767", "+0.6%", "+1.1%", "6/6"],
             ["best-of-book early", "placebo EV>2%", "5,395", "−0.4%", "+3.7%", "6/6"],
             ["best-of-book early", "model EV>5%", "7,836", "+0.5%", "+2.0%", "6/6"],
             ["average-book early", "model EV>1%", "3,701", "−5.2%", "−5.1%", "0/6"]],
    "best": -1,
}
WNBA_SIM = {
    "caption": "Flat 1u at FanDuel opening prices — coherent quotes, ET "
               "dates, calibrated model, side must agree with the predicted "
               "move. Jun 2025 – Jul 2026, t clustered by player-game. CLV* "
               "is vs the shade-corrected close (AUDIT.md N1).",
    "cols": ["filter", "bets", "ROI (t)", "CLV vs close", "CLV*"],
    "rows": [["EV≥2%", "1,379", "+1.7% (0.0)", "−3.1%", "+2.7%"],
             ["EV≥3% (list)", "935", "+3.1% (0.5)", "−2.9%", "+3.2%"],
             ["EV≥6% (strong)", "281", "+3.6% (0.6)", "−0.8%", "+6.3%"]],
    "best": -1,
}

# ------------------------------------------------------------- data load ----


def read_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh) if any(v for v in r.values())]


def num(row, key):
    v = (row.get(key) or "").strip()
    if v in ("", "nan", "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def txt(row, key):
    v = (row.get(key) or "").strip()
    return "" if v in ("nan", "None") else v


def sheet_asof(bank, meta, bets):
    """Newest timestamp in this market's own data - never the wall clock.

    Settlement stamps bankroll.json and the routines stamp picks_meta.json,
    both UTC, as does every placed_at: together they are the last moment the
    data knows about. Used to tell a live pick from one whose game has been
    played, without reading the clock (which would rewrite the page hourly).
    """
    seen = [str(v) for v in (bank.get("updated"), meta.get("updated")) if v]
    seen += [txt(b, "placed_at") for b in bets if txt(b, "placed_at")]
    return max(seen) if seen else ""


def pick_expired(p, as_of):
    """True when the pick's game has already tipped, per the data's own clock.

    `tip` is UTC, as is `as_of`, so the comparison never straddles the ET/UTC
    boundary that bit settlement (AUDIT C2). Rows without a tip fall back to
    the ET game date, which can only err towards keeping a pick on the sheet.
    """
    if not as_of:
        return False
    tip = txt(p, "tip")
    if tip:
        return tip < as_of
    d = txt(p, "date")[:10]
    return bool(d) and d < as_of[:10]


def agg_record(sel):
    """One slice of the bet log, aggregated the way the era cards read:
    W-L/P&L over settled rows, CLV/CLV* over stamped non-void rows."""
    settled = [b for b in sel if b["_status"] == "settled"]
    wins = sum(1 for b in settled if txt(b, "result") == "won")
    staked = sum(b["_stake"] for b in settled)
    pnl = sum(b["_pnl"] or 0.0 for b in settled)
    clvs = [b["_clv"] for b in sel
            if b["_clv"] is not None and b["_status"] != "void"]
    cals = [b["_cal"] for b in sel
            if b["_cal"] is not None and b["_status"] != "void"]
    dates = sorted(txt(b, "match_date") for b in sel)
    return {"n": len(sel), "n_settled": len(settled), "wins": wins,
            "losses": len(settled) - wins, "staked": staked, "pnl": pnl,
            "roi": pnl / staked if staked else None,
            "clv": sum(clvs) / len(clvs) if clvs else None,
            "n_clv": len(clvs),
            "cal": sum(cals) / len(cals) if cals else None,
            "first": dates[0] if dates else "", "last": dates[-1] if dates else ""}


def era_stats(bets, era):
    """Aggregate record for the bets before ("pre") / since ("post") GATES."""
    return agg_record([b for b in bets if txt(b, "match_date")
                       and ((txt(b, "match_date") < GATES) == (era == "pre"))])


def load_market(cfg):
    live = os.path.join(ROOT, cfg["dir"], "live")
    bank = {"start": 100.0, "current": 100.0, "updated": ""}
    bpath = os.path.join(live, "bankroll.json")
    if os.path.exists(bpath):
        bank.update(json.load(open(bpath)))
    meta = {}
    mpath = os.path.join(live, "picks_meta.json")
    if os.path.exists(mpath):
        meta = json.load(open(mpath))

    bets = read_rows(os.path.join(live, "bets.csv"))
    picks_all = read_rows(os.path.join(live, "picks.csv"))
    as_of = sheet_asof(bank, meta, bets)
    for b in bets:
        b["_stake"] = num(b, "stake") or 0.0
        b["_pnl"] = num(b, "pnl")
        b["_clv"] = num(b, "clv")
        b["_cal"] = num(b, "clv_cal")  # vs the shade-adjusted close (AUDIT N1)
        b["_ev"] = num(b, "ev_claimed")  # model's claim at the price taken
        b["_status"] = txt(b, "status") or "open"
    settled = [b for b in bets if b["_status"] == "settled"]
    graded = [b for b in bets if b["_status"] in ("settled", "push", "void")]
    open_bets = [b for b in bets if b["_status"] == "open"]
    # every stamped row counts - settlement stamps CLV as soon as the game
    # is over, so open bets awaiting box scores still carry a verdict.
    # Voids are excluded: their close was priced off the same absence.
    clvs = [b["_clv"] for b in bets
            if b["_clv"] is not None and b["_status"] != "void"]
    cals = [b["_cal"] for b in bets
            if b["_cal"] is not None and b["_status"] != "void"]
    evs = [b["_ev"] for b in graded if b["_ev"] is not None]

    staked = sum(b["_stake"] for b in settled)
    pnl = sum(b["_pnl"] or 0.0 for b in settled)
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    mean_cal = sum(cals) / len(cals) if cals else None
    t_stat = None
    if len(clvs) >= 2:
        var = sum((c - mean_clv) ** 2 for c in clvs) / (len(clvs) - 1)
        se = math.sqrt(var / len(clvs))
        t_stat = mean_clv / se if se else None

    # model calibration: expected wins under the model's own model_p vs
    # observed - the highest-power live diagnostic at small n (2026-08-08
    # audit), and the check that first caught the under-side failure
    cal = [(num(b, "model_p"), txt(b, "result")) for b in settled
           if num(b, "model_p") is not None]
    calib = None
    if len(cal) >= 10:
        exp_w = sum(p for p, _ in cal)
        var_w = sum(p * (1 - p) for p, _ in cal)
        obs_w = sum(1 for _, res in cal if res == "won")
        if var_w > 0:
            calib = {"n": len(cal), "exp": exp_w, "obs": obs_w,
                     "z": (obs_w - exp_w) / math.sqrt(var_w)}
    # closing-line movement: at an ~80% no-move rate, raw CLV mostly
    # measures vig - the page must say how much of the sample can speak
    n_stamped = n_moved = 0
    for b in bets:
        if b["_clv"] is None or b["_status"] == "void":
            continue
        src, ln = txt(b, "clv_source"), num(b, "line")
        if "@" not in src or ln is None:
            continue
        n_stamped += 1
        try:
            if float(src.split("@", 1)[1]) != ln:
                n_moved += 1
        except ValueError:
            n_stamped -= 1

    # Daily time series for the record charts, keyed by ET match date. Days
    # with nothing settled and nothing CLV-stamped (open bets only) are
    # skipped, so the curves end at the last day the data can speak about.
    by_day = {}
    for b in bets:
        d = txt(b, "match_date")[:10]
        if not d:
            continue
        rec = by_day.setdefault(d, {"pnl": 0.0, "exp": 0.0, "n_graded": 0,
                                    "clv": [], "cal": []})
        if b["_status"] in ("settled", "push", "void"):
            rec["n_graded"] += 1
            rec["pnl"] += b["_pnl"] or 0.0
        if b["_clv"] is not None and b["_status"] != "void":
            rec["exp"] += b["_stake"] * b["_clv"]
            rec["clv"].append(b["_clv"])
            if b["_cal"] is not None:
                rec["cal"].append(b["_cal"])
    pnl_pts, exp_pts, clvm_pts, calm_pts = [], [], [], []
    cum_pnl = cum_exp = 0.0
    seen_clv, seen_cal = [], []
    for d in sorted(by_day):
        rec = by_day[d]
        if not rec["n_graded"] and not rec["clv"]:
            continue
        try:
            day = dt.date.fromisoformat(d)
        except ValueError:
            continue
        cum_pnl += rec["pnl"]
        cum_exp += rec["exp"]
        seen_clv += rec["clv"]
        seen_cal += rec["cal"]
        pnl_pts.append((day, round(cum_pnl, 2)))
        exp_pts.append((day, round(cum_exp, 2)))
        if seen_clv:
            clvm_pts.append((day, round(sum(seen_clv) / len(seen_clv) * 100, 2)))
        if seen_cal:
            calm_pts.append((day, round(sum(seen_cal) / len(seen_cal) * 100, 2)))

    bet_keys = {txt(b, "key") for b in bets}
    live_picks = [p for p in picks_all if not pick_expired(p, as_of)]
    return {
        "cfg": cfg, "bank": bank, "meta": meta, "bets": bets,
        # betslip max-wager observations (PROTOCOL "Limit capture",
        # 2026-08-17) - the block renders only once the log has rows
        "limits": read_rows(os.path.join(live, "limits.csv")),
        "settled": settled, "graded": graded, "open": open_bets,
        # Split the sheet three ways: rows whose game has already tipped are
        # the record of what the model priced, and rows already in the bet
        # log belong there, not on the sheet - what remains is what the
        # owner can still act on (owner request 2026-08-03).
        "picks": [p for p in live_picks if txt(p, "key") not in bet_keys],
        "picks_logged": [p for p in live_picks if txt(p, "key") in bet_keys],
        "picks_expired": [p for p in picks_all if pick_expired(p, as_of)],
        "as_of": as_of,
        "wins": sum(1 for b in settled if txt(b, "result") == "won"),
        "pushes": sum(1 for b in bets if b["_status"] == "push"),
        "voids": sum(1 for b in bets if b["_status"] == "void"),
        "staked": staked, "pnl": pnl,
        "roi": pnl / staked if staked else None,
        "mean_clv": mean_clv, "n_clv": len(clvs), "t_stat": t_stat,
        "mean_cal": mean_cal, "n_cal": len(cals),
        "calib": calib, "n_moved": n_moved, "n_stamped": n_stamped,
        "exp_pnl": sum(b["_stake"] * b["_clv"] for b in bets
                       if b["_clv"] is not None and b["_status"] != "void"),
        # What the model itself claimed the same graded bets were worth, at
        # the prices actually taken. Same population as exp_pnl so the two
        # tiles are read side by side: model's claim vs market's verdict.
        "model_exp_pnl": sum(b["_stake"] * b["_ev"] for b in graded
                             if b["_ev"] is not None),
        "mean_ev": sum(evs) / len(evs) if evs else None,
        "n_ev": len(evs),
        "pnl_pts": pnl_pts, "exp_pts": exp_pts,
        "clvm_pts": clvm_pts, "calm_pts": calm_pts,
        "era_pre": era_stats(bets, "pre"), "era_post": era_stats(bets, "post"),
    }


def backtest_curve():
    """Soccer betting-sim cumulative P&L, thinned for plotting."""
    path = os.path.join(ROOT, "soccer", "results", "cum_pnl.csv")
    if not os.path.exists(path):
        return []
    pts = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                pts.append((dt.date.fromisoformat(r["date"]),
                            float(r["cum_units"])))
            except (ValueError, KeyError):
                continue
    if len(pts) <= 400:
        return pts
    step = math.ceil(len(pts) / 400)
    keep = set(range(0, len(pts), step)) | {0, len(pts) - 1}
    keep.add(max(range(len(pts)), key=lambda i: pts[i][1]))
    keep.add(min(range(len(pts)), key=lambda i: pts[i][1]))
    keep |= set(max_drawdown(pts)[:2])  # keep the true peak/trough, not a proxy
    return [pts[i] for i in sorted(keep)]


def max_drawdown(pts):
    """(peak index, trough index, depth) of the deepest peak-to-trough fall."""
    peak_i, worst = 0, (0, 0, 0.0)
    for i, (_, v) in enumerate(pts):
        if v > pts[peak_i][1]:
            peak_i = i
        if v - pts[peak_i][1] < worst[2]:
            worst = (peak_i, i, v - pts[peak_i][1])
    return worst


def stamp(markets):
    """Newest date present in the data - never the wall clock.

    Day granularity on purpose: picks_meta is rewritten hourly by the routines,
    and a minute-precision stamp would rewrite this whole page every hour for
    no change in what it says.
    """
    seen = []
    for m in markets:
        for v in (m["bank"].get("updated"), m["meta"].get("updated")):
            if v:
                seen.append(str(v)[:10])
        for b in m["bets"]:
            if txt(b, "placed_at"):
                seen.append(txt(b, "placed_at")[:10])
    return max(seen) if seen else ""


# -------------------------------------------------------------- helpers ----

MINUS = "−"


def esc(v):
    return html.escape(str(v), quote=True)


def money(v, dp=2):
    return f"${v:,.{dp}f}"


def signed_money(v, dp=2):
    return f"{'+' if v >= 0 else MINUS}${abs(v):,.{dp}f}"


def signed_pct(v, dp=1):
    return f"{'+' if v >= 0 else MINUS}{abs(v) * 100:.{dp}f}%"


def odds(v, style):
    if v is None:
        return "—"
    if style == "american":
        return f"{'+' if v > 0 else MINUS}{abs(int(v))}"
    return f"{v:.2f}"


def date_short(s):
    try:
        d = dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return esc(s)
    return f"{d.day} {d.strftime('%b')}"


def et_time(s):
    """'7:00pm ET' from a UTC tip stamp; falls back to UTC if the tz
    database is unavailable, and to nothing if the stamp doesn't parse."""
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", ""))
    except ValueError:
        return ""
    try:
        from zoneinfo import ZoneInfo
        loc = t.replace(tzinfo=dt.timezone.utc).astimezone(
            ZoneInfo("America/New_York"))
        return (loc.strftime("%I:%M%p").lstrip("0").lower() + " ET")
    except Exception:
        return t.strftime("%H:%M UTC")


def tile(label, value, sub="", tone=""):
    tone = f" val-{tone}" if tone else ""
    sub = f'<div class="tile-sub">{sub}</div>' if sub else ""
    return (f'<div class="tile"><div class="tile-label">{label}</div>'
            f'<div class="tile-val{tone}">{value}</div>{sub}</div>')


def pill(text, tone="idle"):
    return f'<span class="pill pill-{tone}">{text}</span>'


def empty(title, note=""):
    note = f"<span>{note}</span>" if note else ""
    return f'<div class="empty"><strong>{title}</strong>{note}</div>'


def table(cols, rows, cls="", aligns=None, wrap_attrs="", extra_row=""):
    """rows entries are either a list of cells or (cells, tr-attr-string)."""
    aligns = aligns or ["l"] * len(cols)
    head = "".join(f'<th class="a-{a}">{c}</th>' for c, a in zip(cols, aligns))
    body = []
    for r in rows:
        attrs = ""
        if isinstance(r, tuple):
            r, attrs = r
        cells = "".join(f'<td class="a-{a}">{c}</td>' for c, a in zip(r, aligns))
        body.append(f"<tr{attrs}>{cells}</tr>")
    return (f'<div class="scroll"{wrap_attrs}><table class="{cls}">'
            f'<thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(body)}{extra_row}</tbody></table></div>')


# --------------------------------------------------------------- charts ----


def nice_step(span):
    """A pleasant tick step giving ~4-6 gridlines over `span`."""
    raw = span / 5 or 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for mlt in (1, 2, 2.5, 5, 10):
        if raw <= mlt * mag * (1 + 1e-9):
            return mlt * mag
    return 10 * mag


def x_ticks(x0, x1):
    """[(ordinal, label)] — day, month or year ticks depending on the span."""
    d0, d1 = dt.date.fromordinal(x0), dt.date.fromordinal(x1)
    span = x1 - x0
    out = []
    if span <= 92:
        step = 14 if span > 60 else 7 if span > 42 else 3 if span > 12 else 1
        o = x0
        while o <= x1:
            out.append((o, date_short(dt.date.fromordinal(o).isoformat())))
            o += step
    elif span <= 760:
        d = dt.date(d0.year, d0.month, 1)
        while d.toordinal() <= x1:
            if d.toordinal() >= x0:
                lab = d.strftime("%b")
                if d.month == 1 or not out:
                    lab += f" ’{d.year % 100:02d}"
                out.append((d.toordinal(), lab))
            d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    else:
        for y in range(d0.year, d1.year + 1):
            o = dt.date(y, 1, 1).toordinal()
            if x0 <= o <= x1:
                out.append((o, str(y)))
    return out


def end_fmt(v, unit):
    sign = "+" if v >= 0 else MINUS
    if unit == "money":
        return f"{sign}${abs(v):,.2f}"
    if unit == "pct":
        return f"{sign}{abs(v):.1f}%"
    return f"{sign}{abs(v):,.0f}u"


def line_chart(series, cid, unit, events=(), area=False, mark_dd=False,
               w=860, h=300, aria=""):
    """1..n same-unit series over dates, with optional process-change rules.

    series: [{"label": str, "pts": [(date, value)]}]; unit: money|pct|units.
    Values are plotted as given (pct series pass percentage points).
    """
    series = [s for s in series if len(s["pts"]) >= 2]
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 78, 34, 30
    xs = sorted({p[0].toordinal() for s in series for p in s["pts"]})
    x0, x1 = xs[0], xs[-1]
    ys = [p[1] for s in series for p in s["pts"]]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08

    def px(x):
        return pad_l + (x - x0) / max(x1 - x0, 1) * (w - pad_l - pad_r)

    def py(y):
        return pad_t + (hi - y) / (hi - lo) * (h - pad_t - pad_b)

    def tick_label(v):
        if abs(v) < 1e-9:
            return {"money": "$0", "pct": "0%"}.get(unit, "0")
        sign = "+" if v > 0 else MINUS
        if unit == "money":
            return f"{sign}${abs(v):,.0f}"
        if unit == "pct":
            return f"{sign}{abs(v):g}%"
        return f"{sign}{abs(v):,.0f}"

    step = nice_step(hi - lo)
    ticks, t = [], math.ceil(lo / step - 1e-9) * step
    while t <= hi + 1e-9 and len(ticks) < 9:
        ticks.append(round(t, 6))
        t += step
    grid = "".join(
        f'<line class="grid" x1="{pad_l}" x2="{w - pad_r}" '
        f'y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        f'<text class="tick a-r" x="{pad_l - 8}" y="{py(v) + 4:.1f}">'
        f'{tick_label(v)}</text>' for v in ticks)
    if lo < 0 < hi:
        grid += (f'<line class="zero" x1="{pad_l}" x2="{w - pad_r}" '
                 f'y1="{py(0):.1f}" y2="{py(0):.1f}"/>')
    xlab = "".join(
        f'<text class="tick a-m" x="{px(o):.1f}" y="{h - 8}">{esc(lab)}</text>'
        for o, lab in x_ticks(x0, x1))

    # process-change rules — the vertical breaks in the record
    ev_svg = ""
    for edate, elabel, major in events:
        try:
            o = dt.date.fromisoformat(edate).toordinal()
        except ValueError:
            continue
        if not (x0 <= o <= x1):
            continue
        ex = px(o)
        anchor = "middle"
        if ex > w - pad_r - 46:
            anchor = "end"
        elif ex < pad_l + 46:
            anchor = "start"
        ev_svg += (
            f'<line class="evt{" evt-major" if major else ""}" '
            f'x1="{ex:.1f}" x2="{ex:.1f}" y1="{pad_t}" y2="{h - pad_b}"/>'
            f'<text class="evt-label{" major" if major else ""}" '
            f'x="{ex:.1f}" y="{pad_t - 10}" text-anchor="{anchor}">'
            f'{esc(elabel)}</text>')

    body = ""
    for k, s in enumerate(series):
        pts = s["pts"]
        line = " ".join(
            f"{'M' if i == 0 else 'L'}{px(p[0].toordinal()):.1f},{py(p[1]):.1f}"
            for i, p in enumerate(pts))
        if k == 0 and area:
            base = py(max(lo, min(hi, 0.0)))
            body += (f'<path class="area" d="{line} '
                     f'L{px(pts[-1][0].toordinal()):.1f},{base:.1f} '
                     f'L{px(pts[0][0].toordinal()):.1f},{base:.1f} Z"/>')
        body += f'<path class="line s{k + 1}" d="{line}"/>'

    ann = ""
    if mark_dd:
        pts = series[0]["pts"]
        _, i, depth = max_drawdown(pts)
        ann = (f'<circle class="dot-dd" cx="{px(pts[i][0].toordinal()):.1f}" '
               f'cy="{py(pts[i][1]):.1f}" r="4"/>'
               f'<text class="ann" x="{px(pts[i][0].toordinal()):.1f}" '
               f'y="{py(pts[i][1]) + 22:.1f}" text-anchor="middle">'
               f'deepest drawdown {MINUS}{abs(depth):,.0f}u</text>')

    # endpoint dot + value per series, labels nudged apart when they collide
    ends = [(k, px(s["pts"][-1][0].toordinal()), py(s["pts"][-1][1]),
             s["pts"][-1][1]) for k, s in enumerate(series)]
    lab_y = {k: ey for k, _, ey, _ in ends}
    if len(ends) == 2 and abs(ends[0][2] - ends[1][2]) < 16:
        top, bot = sorted(ends, key=lambda e: e[2])
        mid = (top[2] + bot[2]) / 2
        lab_y[top[0]], lab_y[bot[0]] = mid - 8, mid + 8
    for k, ex, ey, ev_ in ends:
        body += (f'<circle class="dot-end s{k + 1}" cx="{ex:.1f}" '
                 f'cy="{ey:.1f}" r="4.5"/>'
                 f'<text class="end-label" x="{ex + 9:.1f}" '
                 f'y="{lab_y[k] + 4:.1f}">{end_fmt(ev_, unit)}</text>')

    # hover payload: every series is sampled onto the union date grid
    pay_series = []
    for s in series:
        mp = {p[0].toordinal(): p[1] for p in s["pts"]}
        last, vals = s["pts"][0][1], []
        for o in xs:
            last = mp.get(o, last)
            vals.append(last)
        pay_series.append({"label": s["label"],
                           "y": [round(py(v), 1) for v in vals],
                           "v": vals})
    payload = json.dumps({
        "x": [round(px(o), 1) for o in xs],
        "d": [dt.date.fromordinal(o).isoformat() for o in xs],
        "unit": unit, "series": pay_series})

    legend = ""
    if len(series) > 1:
        legend = '<div class="legend">' + "".join(
            f'<span class="lg"><i class="sw sw-{k + 1}"></i>'
            f'{esc(s["label"])}</span>' for k, s in enumerate(series)) + "</div>"

    cross = (f'<g class="cross" hidden><line class="cross-l" y1="{pad_t}" '
             f'y2="{h - pad_b}"/>'
             + "".join(f'<circle class="cross-d s{k + 1}" r="4.5"/>'
                       for k in range(len(series))) + "</g>")

    return (
        f'<figure class="chart" data-chart="{cid}">{legend}'
        f'<svg viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="none" '
        f'aria-label="{esc(aria)}">'
        f'{grid}{xlab}{ev_svg}{body}{ann}{cross}'
        f'<rect class="hit" x="{pad_l}" y="{pad_t}" width="{w - pad_l - pad_r}" '
        f'height="{h - pad_t - pad_b}"/></svg>'
        f'<div class="tip" hidden></div>'
        f'<script type="application/json">{payload}</script></figure>')


def meter(frac, label, tone="model"):
    pct_w = max(2.0, min(100.0, frac * 100))
    return (f'<div class="meter"><div class="meter-track">'
            f'<div class="meter-fill meter-{tone}" style="width:{pct_w:.1f}%">'
            f'</div></div><span class="meter-label">{label}</span></div>')


def clv_band(cfg, live_clv, n):
    """Where live CLV sits against what the backtest predicts."""
    lo, hi = cfg["clv_band"]
    scale_hi = math.ceil(max(hi * 1.7, 0.06, (live_clv or 0) * 1.3) * 50) / 50
    scale_lo = math.floor(min(-0.02, lo * 1.5, (live_clv or 0) * 1.3) * 50) / 50

    def pos(v):
        return max(0.0, min(100.0, (v - scale_lo) / (scale_hi - scale_lo) * 100))

    ticks = []
    t = scale_lo
    while t <= scale_hi + 1e-9:
        ticks.append(f'<span style="left:{pos(t):.1f}%">'
                     f'{"0" if abs(t) < 1e-9 else signed_pct(t, 0)}</span>')
        t = round(t + 0.02, 4)
    def tight(v):  # +1% not +1.0%, but +5.4% keeps its decimal
        return signed_pct(v, 0 if abs(v * 1000) % 10 < 1e-9 else 1)

    label = tight(lo) if lo == hi else f"{tight(lo)} to {tight(hi)}"
    width = max(pos(hi) - pos(lo), 0.0)
    point = " band-point" if width < 1.5 else ""
    target = (f'<div class="band-target{point}" style="left:{pos(lo):.1f}%;'
              f'width:{width:.1f}%"><span class="band-tlabel">'
              f'{esc(cfg["clv_band_note"])} {label}</span></div>')
    if live_clv is None:
        mark = ""
        note = (f'<p class="band-note">The marker lands here on the first '
                f'settled bet. Right now: nothing settled.</p>')
    else:
        mark = (f'<div class="band-mark" style="left:{pos(live_clv):.1f}%">'
                f'<span>{signed_pct(live_clv)}</span></div>')
        note = (f'<p class="band-note">Live mean CLV across {n} '
                f'{"bet" if n == 1 else "bets"} with a recorded closing price'
                f'{" — still early" if n < 30 else ""}.</p>')
    return (f'<div class="bandwrap"><div class="band">'
            f'<div class="band-zero" style="left:{pos(0):.1f}%"></div>'
            f'{target}{mark}</div>'
            f'<div class="band-axis">{"".join(ticks)}</div>{note}</div>')


# ---------------------------------------------------------------- panels ----


def result_chip(b):
    r = txt(b, "result")
    if not r:
        return '<span class="chip chip-open">open</span>'
    tone = {"won": "good", "lost": "bad"}.get(r, "neutral")
    return f'<span class="chip chip-{tone}">{esc(r)}</span>'


def pnl_cell(v):
    if v is None:
        return '<span class="muted">—</span>'
    tone = "good" if v > 0 else ("bad" if v < 0 else "flat")
    return f'<span class="val-{tone} mono">{signed_money(v)}</span>'


def clv_cell(v):
    if v is None:
        return '<span class="muted">—</span>'
    tone = "good" if v > 0 else ("bad" if v < 0 else "flat")
    return f'<span class="val-{tone} mono">{signed_pct(v)}</span>'


def bet_rows(m):
    """(columns, [(cells, tr-attrs)], aligns) for the WNBA bet log.

    Every row carries data-* attributes so the client-side filter bar can
    slice the log and recompute the summary without a rebuild.
    """
    cfg = m["cfg"]
    rows = sorted(m["bets"], key=lambda b: (txt(b, "match_date"),
                                            txt(b, "key")), reverse=True)
    style = cfg["odds_style"]
    # "model EV" = ev_claimed: the model's expected ROI for the bet at the
    # moment it was taken, at the price actually taken (owner request
    # 2026-08-03). Read against CLV: the claim vs the market's verdict.
    cols = ["date", "player", "market", "pick", "odds", "stake", "model EV",
            "actual", "result", "P&L", "CLV", "CLV*"]
    aligns = ["l", "l", "l", "l", "r", "r", "r", "r", "l", "r", "r", "r"]
    out = []
    def g(v):
        return "" if v is None else f"{v:g}"

    for b in rows[:500]:
        actual = num(b, "actual")
        mdate = txt(b, "match_date")[:10]
        res = txt(b, "result") or b["_status"]
        attrs = (
            f' data-status="{esc(b["_status"])}"'
            f' data-result="{esc(res)}"'
            f' data-market="{esc(txt(b, "market"))}"'
            f' data-side="{esc(txt(b, "side"))}"'
            f' data-era="{"post" if mdate >= GATES else "pre"}"'
            f' data-date="{esc(mdate)}"'
            f' data-player="{esc(txt(b, "player").lower())}"'
            f' data-stake="{b["_stake"]:g}"'
            f' data-pnl="{g(b["_pnl"])}"'
            f' data-clv="{g(b["_clv"])}"'
            f' data-cal="{g(b["_cal"])}"')
        cells = [
            date_short(mdate),
            esc(txt(b, "player")),
            f'<span class="muted">{esc(txt(b, "market"))}</span>',
            f'<span class="chip">{esc(txt(b, "side"))} '
            f'{num(b, "line"):g}</span>' if num(b, "line") is not None
            else f'<span class="chip">{esc(txt(b, "side"))}</span>',
            f'<span class="mono">{odds(num(b, "odds_taken"), style)}</span>',
            money(b["_stake"]),
            f'<span class="muted mono">{signed_pct(b["_ev"])}</span>'
            if b["_ev"] is not None else "—",
            f"{actual:g}" if actual is not None else "—",
            result_chip(b), pnl_cell(b["_pnl"]), clv_cell(b["_clv"]),
            clv_cell(b["_cal"])]
        out.append((cells, attrs))
    return cols, out, aligns


def filter_block(m):
    """The filter row + live summary that scope the bet log below them."""
    cfg = m["cfg"]
    markets = sorted({txt(b, "market") for b in m["bets"] if txt(b, "market")})
    sides = sorted({txt(b, "side") for b in m["bets"] if txt(b, "side")})
    results = [r for r in ("won", "lost", "open", "push", "void")
               if any((txt(b, "result") or b["_status"]) == r
                      for b in m["bets"])]

    def sel(name, blank, vals, labels=None):
        labels = labels or {v: v for v in vals}
        opts = "".join(f'<option value="{esc(v)}">{esc(labels[v])}</option>'
                       for v in vals)
        return (f'<select data-f="{name}" aria-label="{esc(name)}">'
                f'<option value="">{esc(blank)}</option>{opts}</select>')

    gates_lab = date_short(GATES)
    era = (f'<select data-f="era" aria-label="period">'
           f'<option value="">whole run</option>'
           f'<option value="post">since the gates ({esc(gates_lab)})</option>'
           f'<option value="pre">before the gates</option></select>')
    # Game-date range. Bounded by the log's own span so the picker cannot
    # wander off into empty months; both ends are inclusive and compared as
    # ISO strings, which sort correctly without parsing.
    days = sorted({txt(b, "match_date")[:10] for b in m["bets"]
                   if txt(b, "match_date")})
    span = (f' min="{esc(days[0])}" max="{esc(days[-1])}"') if days else ""
    dates = (f'<span class="frange">'
             f'<span class="flabel">games</span>'
             f'<input data-f="from" type="date"{span} '
             f'aria-label="games on or after" title="games on or after">'
             f'<span class="fdash" aria-hidden="true">\u2013</span>'
             f'<input data-f="to" type="date"{span} '
             f'aria-label="games on or before" title="games on or before">'
             f'</span>')
    # Plus/minus EV as the market scored it: CLV is the primary metric, and
    # unlike `ev_claimed` (positive on every logged bet, by the >10% trigger)
    # its sign actually splits the log. Rows awaiting a close are their own
    # bucket rather than silently counting as negative.
    clvsign = (f'<select data-f="clvsign" aria-label="CLV sign">'
               f'<option value="">+ and {MINUS} CLV</option>'
               f'<option value="pos">beat the close (+CLV)</option>'
               f'<option value="neg">lost to the close ({MINUS}CLV)</option>'
               f'<option value="none">no close yet</option></select>')
    # server-side prerender of the all-bets summary line (no-JS fallback)
    parts = [f'{len(m["bets"])} of {len(m["bets"])} bets']
    if m["settled"]:
        parts.append(f'{m["wins"]}W{MINUS}{len(m["settled"]) - m["wins"]}L')
        parts.append(f'P&L {signed_money(m["pnl"])}'
                     + (f' ({signed_pct(m["roi"])} ROI)'
                        if m["roi"] is not None else ""))
    if m["mean_clv"] is not None:
        parts.append(f'mean CLV {signed_pct(m["mean_clv"])}')
    if m["mean_cal"] is not None:
        parts.append(f'CLV* {signed_pct(m["mean_cal"])}')
    return (
        f'<div class="filterbar" role="group" aria-label="Filter the bet log" '
        f'data-scope="log-{cfg["id"]}" data-summary="fsum-{cfg["id"]}">'
        + sel("market", "all markets", markets)
        + sel("side", "over + under", sides)
        + sel("result", "any result", results)
        + era
        + clvsign
        + dates
        + f'<input data-f="q" type="search" placeholder="player…" '
          f'aria-label="filter by player">'
        + '</div>'
        + f'<p class="fsummary" id="fsum-{cfg["id"]}">{" · ".join(parts)}</p>')


def picks_block(m):
    cfg, picks = m["cfg"], m["picks"]
    stale = m.get("picks_expired") or []
    logged = m.get("picks_logged") or []
    if not picks:
        if logged and not (cfg.get("cancelled") or cfg.get("paused")):
            return empty(
                "Nothing left to act on",
                f'All {len(logged)} sheet row(s) for upcoming games are '
                f'already in the bet log below.')
        if stale and not (cfg.get("cancelled") or cfg.get("paused")):
            # Every row is for a game that has tipped. Saying "no picks" would
            # be true but unhelpful; showing them as live would be a lie.
            last = max(txt(p, "date") for p in stale)
            return empty(
                "Nothing on the sheet for an upcoming game",
                f'The sheet\'s {len(stale)} rows are all for games on or '
                f'before {esc(date_short(last))}, already played. They stay '
                f'in <span class="mono">picks.csv</span> as the record of '
                f'what the model priced and are not actionable; the next '
                f'refresh replaces them.')
        note = cfg["idle"] if (cfg.get("cancelled") or cfg.get("paused")) \
            else (m["meta"].get("note") or cfg["idle"])
        return empty("No picks on the sheet", esc(note))
    strong = [p for p in picks
              if str(p.get("strong", "")).lower() in ("true", "1")]
    head = (f'<p class="note">{len(picks)} priced, '
            f'<strong>{len(strong)} strong</strong>. '
            f'{cfg["picks_note"]}'
            + (f' {len(logged)} row(s) already in the bet log are shown '
               f'there, not here.' if logged else "")
            + (f' {len(stale)} further row(s) on the sheet are for games '
               f'already played and are not shown.' if stale else "")
            + '</p>')
    cols = ["game", "player", "pick", "price", "model p", "EV", "stake"]
    aligns = ["l", "l", "l", "r", "r", "r", "r"]
    rows = []
    for p in picks[:25]:
        ev = num(p, "ev")
        # gates (2026-08-08): blocked rows carry their reasons in `flags`
        # and stake 0 - shown struck through so what was skipped, and why,
        # stays legible on the published page
        flags = txt(p, "flags")
        blocked = any(f and not f.endswith("?")
                      for f in flags.split(";")) if flags else False
        tip = et_time(txt(p, "tip"))
        rows.append([
            f'<span class="muted mono">{esc(txt(p, "game"))}</span> '
            f'{date_short(txt(p, "date"))}'
            + (f' <span class="muted">{esc(tip)}</span>' if tip else ""),
            esc(txt(p, "player")),
            f'<span class="chip">{esc(txt(p, "market"))} {esc(txt(p, "side"))} '
            f'{num(p, "fd_line"):g}</span>'
            + (' <span class="chip chip-strong">strong</span>'
               if str(p.get("strong", "")).lower() in ("true", "1")
               and not blocked else "")
            + (f' <span class="chip">⛔ {esc(flags)}</span>' if blocked
               else (f' <span class="chip">⚠ {esc(flags)}</span>'
                     if flags else "")),
            f'<span class="mono">{odds(num(p, "fd_cost"), "american")}</span>',
            f'{(num(p, "model_p") or 0) * 100:.1f}%',
            f'<span class="val-good mono">{signed_pct(ev)}</span>'
            if ev is not None else "—",
            "—" if blocked else money(num(p, "stake") or 0)])
    return head + table(cols, rows, "tbl", aligns)


def status_pill(m):
    if m["cfg"].get("cancelled"):
        return pill("cancelled", "idle")
    if m["cfg"].get("paused"):
        return pill("paused", "idle")
    if m["open"]:
        return pill(f"{len(m['open'])} live", "live")
    if m["picks"]:
        return pill(f"{len(m['picks'])} picks", "warm")
    return pill("dormant", "idle")


def era_block(m):
    """Before/since the 2026-08-08 pick gates — the process-change split."""
    pre, post = m["era_pre"], m["era_post"]
    if not pre["n"] or not post["n"]:
        return ""

    def stat(label, value, tone=""):
        tone = f" val-{tone}" if tone else ""
        return (f'<div class="es"><div class="es-label">{label}</div>'
                f'<div class="es-val{tone}">{value}</div></div>')

    def card(title, dates, st, now=False):
        ptone = ("good" if st["pnl"] > 0 else "bad" if st["pnl"] < 0 else "")
        ctone = ("good" if (st["cal"] or 0) > 0 else
                 "bad" if st["cal"] is not None and st["cal"] < 0 else "")
        stats = [
            stat("Record", f'{st["wins"]}W{MINUS}{st["losses"]}L'
                 + (f' <span class="muted">of {st["n"]}</span>'
                    if st["n"] != st["n_settled"] else "")),
            stat("P&L", signed_money(st["pnl"]), ptone),
            stat("ROI", signed_pct(st["roi"]) if st["roi"] is not None
                 else "—", ptone),
            stat("Mean CLV", signed_pct(st["clv"]) if st["clv"] is not None
                 else "—"),
            stat("CLV*", signed_pct(st["cal"]) if st["cal"] is not None
                 else "—", ctone),
        ]
        chip = (' <span class="chip chip-strong">current process</span>'
                if now else "")
        return (f'<article class="era-card{" era-now" if now else ""}">'
                f'<h4>{title}{chip}</h4>'
                f'<div class="era-dates">{dates}</div>'
                f'<div class="era-stats">{"".join(stats)}</div></article>')

    return f"""
  <div class="block">
    <h3>Before and since the {date_short(GATES)} pick gates</h3>
    <p class="note">The first-week audit found the harness wasn't running the
      experiment that was backtested, so the protocol was amended on
      {date_short(GATES)}: panel/player staleness gates, team consistency,
      FanDuel-opener-only, and an EV&gt;25% quarantine. The dashed rule on the
      charts above is this split.</p>
    <div class="era">
      {card("Before the gates",
            f'{date_short(pre["first"])} – {date_short(pre["last"])}', pre)}
      {card("Since the gates",
            f'{date_short(post["first"])} onward', post, now=True)}
    </div>
    <p class="band-note">{post["n_settled"]} settled bets since the gates is
      an early read, not a verdict — the split is shown so the process change
      stays visible in the record, win or lose.</p>
  </div>"""


def slices_block(m):
    """The record cut three ways: by market, by side, by claimed EV.

    The side split is the page-level version of the check that caught the
    under-side calibration failure; the claimed-EV split audits the model's
    own claims (dev: claims realize ~half). Same aggregation as the era
    cards, so every number here reconciles with the bet log's filters.
    """
    bets = [b for b in m["bets"] if b["_status"] != "open" or
            b["_clv"] is not None]
    if len(m["settled"]) < 10:
        return ""

    def mini(title, pairs):
        rows = []
        for label, st in pairs:
            if not st["n"]:
                continue
            rows.append([
                esc(label),
                f'{st["n"]}',
                f'{st["wins"]}W{MINUS}{st["losses"]}L'
                if st["n_settled"] else "—",
                pnl_cell(st["pnl"]) if st["n_settled"] else "—",
                clv_cell(st["cal"]) if st["cal"] is not None else "—"])
        if not rows:
            return ""
        cols = ["", "bets", "W–L", "P&L", "CLV*"]
        return (f'<div class="slice"><h4>{esc(title)}</h4>'
                + table(cols, rows, "tbl", ["l", "r", "r", "r", "r"])
                + '</div>')

    by_market = {}
    for b in bets:
        by_market.setdefault(txt(b, "market") or "?", []).append(b)
    markets = mini("By market", sorted(
        ((k, agg_record(v)) for k, v in by_market.items()),
        key=lambda kv: -kv[1]["n"]))

    sides = mini("Overs vs unders", [
        (s, agg_record([b for b in bets if txt(b, "side") == s]))
        for s in ("over", "under")])

    buckets = [(0.00, 0.10, "under 10%"), (0.10, 0.15, "10–15%"),
               (0.15, 0.20, "15–20%"), (0.20, 0.25, "20–25%"),
               (0.25, 9.99, "over 25%")]
    claims = mini("By the model's claimed EV", [
        (lab, agg_record([b for b in bets if b["_ev"] is not None
                          and lo < b["_ev"] <= hi]))
        for lo, hi, lab in buckets])

    cuts = markets + sides + claims
    if not cuts:
        return ""
    return f"""
  <div class="block">
    <h3>The record, sliced</h3>
    <p class="note">Three cuts of the same bet log. CLV* (vs the
      shade-adjusted close) is the fair per-slice yardstick — P&amp;L at
      these sample sizes is mostly noise, shown because it's the truth.
      The claimed-EV cut audits the model against its own claims: on dev,
      claims realized at roughly half their stated size.</p>
    <div class="slices">{cuts}</div>
  </div>"""


def limits_block(m):
    """Observed FanDuel betslip max-wagers (PROTOCOL "Limit capture").

    Renders nothing until live/limits.csv has rows, so the page carries no
    empty scaffolding while the log accrues.
    """
    rows_in = m.get("limits") or []
    if not rows_in:
        return ""
    rows_in = sorted(rows_in, key=lambda r: txt(r, "recorded_at_utc"),
                     reverse=True)
    cols = ["seen", "player", "pick", "FD price", "max wager", "bet?"]
    aligns = ["l", "l", "l", "r", "r", "l"]
    rows = []
    for r in rows_in[:100]:
        mx = num(r, "max_wager")
        line = num(r, "fd_line")
        pick = (f'{esc(txt(r, "market"))} {esc(txt(r, "side"))}'
                + (f' {line:g}' if line is not None else ""))
        filled = str(r.get("filled", "")).strip().lower() in ("true", "1", "yes")
        rows.append([
            date_short(txt(r, "recorded_at_utc")),
            esc(txt(r, "player")),
            f'<span class="chip">{pick}</span>',
            f'<span class="mono">{odds(num(r, "fd_cost"), "american")}</span>',
            f'<span class="mono">{money(mx, 0)}</span>' if mx is not None
            else "—",
            "yes" if filled else '<span class="muted">no</span>'])
    return f"""
  <div class="block">
    <h3>Observed FanDuel limits</h3>
    <p class="note">The betslip's maximum wager, captured at the slip for
      playable picks (protocol's Limit capture rule, 2026-08-17). Limits are
      the book's own confidence in its price, so they proxy market
      efficiency — but the number is min(house market cap, account cap),
      and it is observation only: it never gates a pick or enters the
      model.</p>
    {table(cols, rows, "tbl", aligns)}
  </div>"""


# -------------------------------------------------------------- players ----
# "What the model thinks" - the per-player view of the live projection
# sheet. Source: wnba/live/projections.csv, the append-only archive
# fp_live.py writes on every news-watch firing (one row per game date x
# player x market it priced, chronological in generated_utc). Everything
# here derives from that one committed file, so the tab rebuilds
# deterministically and refreshes whenever the sheet does.

PLAYER_STATS = [("points", "PTS"), ("rebounds", "REB"), ("assists", "AST"),
                ("threes", "3PM"), ("pra", "PRA")]
STAT_KEY = {"points": "pts", "rebounds": "reb", "assists": "ast",
            "threes": "tpm", "pra": "pra"}
PLAYER_STALE_D = 14   # mirrors fp_live.MAX_PLAYER_STALE_D (pick gate)
SUSPECT_EV = 0.25     # mirrors fp_live.MAX_SANE_EV (EV quarantine gate)
MIN_RANK_N = 8        # fewer fresh players than this -> no percentile shade


def _date_or_none(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def ordn(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def pct_rank(sorted_vals, v):
    """Mid-rank percentile of v within sorted_vals, 0..1."""
    if len(sorted_vals) < 2:
        return None
    i = bisect.bisect_left(sorted_vals, v)
    j = bisect.bisect_right(sorted_vals, v)
    return ((i + max(j - 1, i)) / 2) / (len(sorted_vals) - 1)


def heat_bin(p):
    """Percentile -> shade bin. 0 = no fill (bottom decile recedes)."""
    for b, hi in ((0, .10), (1, .30), (2, .50), (3, .70), (4, .90)):
        if p < hi:
            return b
    return 5


def load_players():
    """Aggregate the projection archive into one row per player."""
    path = os.path.join(ROOT, "wnba", "live", "projections.csv")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    tracked = {mk for mk, _ in PLAYER_STATS}
    latest = {}       # (player, market) -> newest entry by (gen, date)
    spark = {}        # player -> {game date: points mu_news}
    pdate = {}        # player -> latest game date priced (any market)
    first_d, last_gen, latest_rows = "9999", "", []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            gen, d = txt(r, "generated_utc"), txt(r, "date")[:10]
            p, mk = txt(r, "player"), txt(r, "market")
            mu = num(r, "mu_news")
            base = num(r, "mu_base")
            if not (gen and d and p and mk) or mu is None \
                    or _date_or_none(d) is None:
                continue
            # an OUT override zeroes mu_news for that game - that is
            # availability news, not the model's read on the player, so
            # the player view falls back to the talent number (the OUT
            # chip carries the news)
            if base is not None and (txt(r, "override") == "OUT"
                                     or (mu == 0 and base > 0)):
                mu = base
            if gen > last_gen:
                last_gen, latest_rows = gen, []
            if gen == last_gen:
                latest_rows.append(r)
            if mk not in tracked:
                continue
            if d > pdate.get(p, ""):
                pdate[p] = d
            first_d = min(first_d, d)
            cur = latest.get((p, mk))
            if cur is None or (gen, d) >= cur["sort"]:
                latest[(p, mk)] = {"sort": (gen, d), "mu": mu,
                                   "line": num(r, "line"), "date": d,
                                   "ovr": txt(r, "override")}
            if mk == "points":
                spark.setdefault(p, {})[d] = mu
    if not latest:
        return None

    asof = max(pdate.values())
    asof_d = _date_or_none(asof)

    def is_fresh(p):
        d = _date_or_none(pdate[p])
        return (d is not None and asof_d is not None
                and (asof_d - d).days <= PLAYER_STALE_D)

    # percentile distributions among fresh players only - a shade should
    # mean "vs the league as priced now", never vs a stale ghost
    dist = {mk: sorted(e["mu"] for (p, m), e in latest.items()
                       if m == mk and is_fresh(p))
            for mk, _ in PLAYER_STATS}

    players = []
    for p in sorted(pdate):
        fresh = is_fresh(p)
        stats = {}
        for mk, _ in PLAYER_STATS:
            e = latest.get((p, mk))
            if e is None:
                continue
            e = dict(e)
            if fresh and len(dist[mk]) >= MIN_RANK_N:
                e["pct"] = pct_rank(dist[mk], e["mu"])
            stats[mk] = e
        if not stats:
            continue
        # override chip only when the newest-priced row carries one and
        # its game is at the sheet's frontier (override news is per-game)
        newest = max(stats.values(), key=lambda e: e["sort"])
        ovr, nd = "", _date_or_none(newest["sort"][1])
        if (newest["ovr"] and nd is not None and asof_d is not None
                and (asof_d - nd).days <= 2):
            ovr = newest["ovr"]
        ser = sorted(spark.get(p, {}).items())
        players.append({
            "name": p, "last": pdate[p], "fresh": fresh, "ovr": ovr,
            "stats": stats, "spark": ser,
            "delta": ser[-1][1] - ser[0][1] if len(ser) >= 2 else None,
        })
    players.sort(key=lambda r: -(r["stats"].get("points", {}).get("mu")
                                 if "points" in r["stats"] else -1e9))

    # steepest model-vs-market claims on the newest firing, one per
    # player (the sheet's own one-bet-per-player-game discipline)
    dis, seen_p = [], set()
    rows_ev = []
    for r in latest_rows:
        ev = num(r, "ev_best")
        if ev is not None and ev > 0:
            rows_ev.append((ev, r))
    for ev, r in sorted(rows_ev, key=lambda x: -x[0]):
        p = txt(r, "player")
        if p in seen_p:
            continue
        seen_p.add(p)
        fd = num(r, "ev_fd")
        at_fd = fd is not None and abs(fd - ev) < 1e-9
        dis.append({"date": txt(r, "date")[:10], "player": p,
                    "market": txt(r, "market"), "mu": num(r, "mu_news"),
                    "line": num(r, "line"), "ev": ev,
                    "side": txt(r, "side_fd" if at_fd else "side_cons"),
                    "src": "FanDuel" if at_fd else "consensus",
                    "ovr": txt(r, "override")})

    ds = [d for p in players for d, _ in p["spark"]]
    d0 = _date_or_none(min(ds)) if ds else None
    d1 = _date_or_none(max(ds)) if ds else None
    return {"players": players, "dis": dis[:8], "asof": asof,
            "gen": last_gen, "first": first_d,
            "n_fresh": sum(1 for p in players if p["fresh"]),
            "dist": dist,
            "x0": d0.toordinal() if d0 else 0,
            "x1": d1.toordinal() if d1 else 0}


def spark_svg(ser, x0, x1, label):
    """Tiny season-arc line: points mu_news by game date, drawn on the
    sheet's global date window so position always means time."""
    if len(ser) < 2 or x1 <= x0:
        return ""
    w, h, pad = 120, 30, 4
    os_ = [_date_or_none(d).toordinal() for d, _ in ser]
    vs = [v for _, v in ser]
    lo, hi = min(vs), max(vs)
    if hi - lo < 3.0:   # keep flat arcs flat - never zoom noise into drama
        mid = (hi + lo) / 2
        lo, hi = mid - 1.5, mid + 1.5
    span = hi - lo
    lo -= span * .08
    hi += span * .08

    def px(o):
        return pad + (o - x0) / (x1 - x0) * (w - 2 * pad)

    def py(v):
        return pad + (hi - v) / (hi - lo) * (h - 2 * pad)

    path = " ".join(f"{'M' if i == 0 else 'L'}{px(o):.1f},{py(v):.1f}"
                    for i, (o, v) in enumerate(zip(os_, vs)))
    t = (f"{label} projection by game, "
         f"{date_short(ser[0][0])} – {date_short(ser[-1][0])}: "
         f"{vs[0]:.1f} → {vs[-1]:.1f}")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{esc(t)}"><title>{esc(t)}</title>'
            f'<path class="sl" d="{path}"/>'
            f'<circle class="sd" cx="{px(os_[-1]):.1f}" '
            f'cy="{py(vs[-1]):.1f}" r="2.5"/></svg>')


def player_chips(p):
    chips = ""
    if p["ovr"] == "OUT":
        chips += (' <span class="chip chip-bad" title="News override on the '
                  'latest sheet: ruled out">OUT</span>')
    elif p["ovr"].startswith("min="):
        chips += (f' <span class="chip" title="News override on the latest '
                  f'sheet: minutes capped">≈{esc(p["ovr"][4:])} min</span>')
    if not p["fresh"]:
        chips += (f' <span class="chip" title="Not priced in over '
                  f'{PLAYER_STALE_D} days — outside the pick gate, shown '
                  f'unshaded">stale</span>')
    return chips


def heat_cell(e, label, n_fresh):
    if e is None:
        return '<td class="a-r"><span class="muted">—</span></td>'
    v = f"{e['mu']:.1f}"
    cls, title = "", f"{label} {v}"
    if e.get("pct") is not None:
        b = heat_bin(e["pct"])
        title += f" — {ordn(round(e['pct'] * 100))} percentile of {n_fresh}"
        if b:
            cls = f" h{b}"
    if e.get("line") is not None:
        title += f" · latest line {e['line']:g} ({date_short(e['date'])})"
    return (f'<td class="a-r mono heat{cls}" title="{esc(title)}">{v}</td>')


def leader_cards(pl):
    cards = ""
    for mk, label in PLAYER_STATS:
        if mk == "pra":
            continue
        top = sorted((p for p in pl["players"]
                      if p["fresh"] and mk in p["stats"]),
                     key=lambda p: -p["stats"][mk]["mu"])[:5]
        if not top:
            continue
        lead = top[0]["stats"][mk]["mu"]
        rows = ""
        for i, p in enumerate(top):
            v = p["stats"][mk]["mu"]
            wpct = max(4.0, v / lead * 100 if lead else 0)
            rows += (
                f'<div class="lb"><span class="lb-rank mono">{i + 1}</span>'
                f'<div class="lb-mid"><span class="lb-name">'
                f'{esc(p["name"])}</span>'
                f'<div class="lb-track"><div class="lb-fill" '
                f'style="width:{wpct:.1f}%"></div></div></div>'
                f'<span class="lb-val mono">{v:.1f}</span></div>')
        sub = {"points": "projected points per game",
               "rebounds": "projected rebounds per game",
               "assists": "projected assists per game",
               "threes": "projected threes per game"}[mk]
        name = {"points": "Scoring", "rebounds": "Rebounding",
                "assists": "Playmaking", "threes": "Shooting"}[mk]
        cards += (f'<article class="leader"><h4>{name}</h4>'
                  f'<div class="leader-sub">{sub}</div>{rows}</article>')
    return f'<div class="leaders">{cards}</div>'


def players_table(pl):
    heads = [("name", "Player", "l"), ("asof", "Last priced", "l"),
             ("pts", "PTS", "r"), ("reb", "REB", "r"), ("ast", "AST", "r"),
             ("tpm", "3PM", "r"), ("pra", "PRA", "r"),
             ("trend", "Season arc", "l")]
    head = "".join(
        f'<th class="a-{a}" data-key="{k}"'
        + (' aria-sort="descending"' if k == "pts" else "")
        + f'><button class="thsort" type="button">{esc(lab)}</button></th>'
        for k, lab, a in heads)
    body = []
    for p in pl["players"]:
        cells = [f'<td class="a-l pname">{esc(p["name"])}{player_chips(p)}'
                 '</td>',
                 f'<td class="a-l muted" title="{esc(p["last"])}">'
                 f'{date_short(p["last"])}</td>']
        attrs = (f' data-name="{esc(p["name"].lower())}"'
                 f' data-asof="{esc(p["last"])}"')
        for mk, lab in PLAYER_STATS:
            e = p["stats"].get(mk)
            cells.append(heat_cell(e, lab, len(pl["dist"][mk])))
            attrs += (f' data-{STAT_KEY[mk]}="{e["mu"]:g}"' if e else
                      f' data-{STAT_KEY[mk]}=""')
        svg = spark_svg(p["spark"], pl["x0"], pl["x1"], "PTS")
        if svg and p["delta"] is not None:
            d1 = round(p["delta"], 1)
            tone = "good" if d1 > 0 else ("bad" if d1 < 0 else "flat")
            dtxt = "0.0" if d1 == 0 else \
                f'{"+" if d1 > 0 else MINUS}{abs(d1):.1f}'
            svg += (f' <span class="trend-d mono val-{tone}" title="Change '
                    f'in the projected points since first priced">'
                    f'{dtxt}</span>')
            attrs += f' data-trend="{p["delta"]:g}"'
        else:
            svg = '<span class="muted">—</span>'
            attrs += ' data-trend=""'
        cells.append(f'<td class="a-l trend">{svg}</td>')
        if not p["fresh"]:
            attrs += ' class="row-stale"'
        body.append(f'<tr{attrs}>{"".join(cells)}</tr>')
    no_match = (f'<tr class="no-match" hidden><td colspan="{len(heads)}">'
                f'No players match.</td></tr>')
    return (f'<div class="scroll players-scroll"><table class="tbl psort" '
            f'id="ptable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}{no_match}</tbody></table></div>')


def disagree_block(pl):
    if not pl["dis"]:
        return empty("No live disagreements on the newest sheet",
                     "The next hourly firing repopulates this list.")
    rows = []
    for d in pl["dis"]:
        flag = ""
        if d["ev"] > SUSPECT_EV:
            flag = (' <span class="chip chip-bad" title="Claimed EV above '
                    '25% is quarantined by the pick gates as a suspected '
                    'defect, never bet">⛔ quarantined</span>')
        if d["ovr"]:
            flag += f' <span class="chip">{esc(d["ovr"])}</span>'
        rows.append([
            date_short(d["date"]),
            esc(d["player"]),
            f'<span class="chip">{esc(d["market"])} {esc(d["side"])} '
            f'{d["line"]:g}</span>{flag}' if d["line"] is not None
            else f'<span class="chip">{esc(d["market"])} '
                 f'{esc(d["side"])}</span>{flag}',
            f'<span class="mono">{d["mu"]:.1f}</span>'
            if d["mu"] is not None else "—",
            f'{d["line"]:g}' if d["line"] is not None else "—",
            f'<span class="mono">{signed_pct(d["ev"])}</span> '
            f'<span class="muted">{esc(d["src"])}</span>',
        ])
    return table(["game", "player", "claim", "model", "line",
                  "claimed EV"], rows, "tbl",
                 ["l", "l", "l", "r", "r", "r"])


def players_panel(pl):
    cfg = WNBA
    n_total = len(pl["players"])
    heat_key = "".join(f'<i class="hk{" h" + str(b) if b else ""}"></i>'
                       for b in range(6))
    return f"""
<section class="panel" id="panel-players" role="tabpanel"
         aria-labelledby="tab-players" hidden>
  <div class="panel-head">
    <div>
      <h2>What the model thinks</h2>
      <p class="lede">Per-game projections for every player the model
        prices, straight from the live sheet. The talent engine keeps a
        Kalman state per stat per player — per-minute talent, nudged by
        every game, regressed toward a career-curve prior — and prices a
        slate as talent × minutes with opponent pace and defense applied
        at game level, news-desk minutes overrides on top. The market's
        number is never an input; the lines below are only what the model
        is compared against.</p>
    </div>
    <div class="hero">
      <div class="hero-label">Players priced</div>
      <div class="hero-fig">{pl["n_fresh"]}</div>
      <div class="hero-sub">of {n_total} since {date_short(pl["first"])} ·
        sheet through <span class="mono">{esc(pl["gen"])} UTC</span></div>
    </div>
  </div>

  <div class="block">
    <h3>The model's board</h3>
    <p class="note">The league by the model's current news-adjusted
      projection, among players priced in the last {PLAYER_STALE_D} days.</p>
    {leader_cards(pl)}
  </div>

  <div class="block">
    <h3>Every player on the sheet</h3>
    <p class="note">One row per player: the latest projection for each
      market the books hang a line on. Cell shade is the league percentile
      <span class="heat-key" title="bottom decile unshaded, then deciles
      10–30–50–70–90+">{heat_key}</span> among fresh players; the season
      arc is the points projection by game since {date_short(pl["first"])}.
      Click a column to sort. A player the sheet hasn't priced in
      {PLAYER_STALE_D} days shows unshaded and stale — the same staleness
      that blocks picks.</p>
    <div class="filterbar" role="group" aria-label="Filter players">
      <input id="pfind" data-f="q" type="search" placeholder="player…"
             aria-label="filter by player">
    </div>
    <p class="fsummary" id="fsum-players">{n_total} of {n_total} players</p>
    {players_table(pl)}
  </div>

  <div class="block">
    <h3>Where the model argues with the market</h3>
    <p class="note">The steepest claims on the newest sheet
      (<span class="mono">{esc(pl["gen"])} UTC</span>): the model's number
      against the current two-way quote, sized as claimed EV at the better
      of FanDuel and consensus, one claim per player. Descriptive, not
      picks — what is actually playable passes the Live tab's gates first,
      and a claim above {SUSPECT_EV:.0%} is treated as a defect alarm, not
      an edge.</p>
    {disagree_block(pl)}
  </div>

  <div class="block explain">
    <h3>Where these numbers come from</h3>
    <div class="explain-grid">
      <div><h4>A talent state, not an average</h4><p>Every game updates a
        per-minute Kalman state; hot streaks shrink back toward an
        informed career-curve prior, and the offseason widens uncertainty.
        Parameters were fit strictly pre-2025 and pinned — this is the
        same model whose prospective test is running.</p></div>
      <div><h4>Minutes are the hard part</h4><p>Rates ride on a minutes
        estimate from recent usage. The hourly news desk turns injury
        reports into overrides — OUT, or a minutes cap — and the
        projection scales with them. Chips on a row mark an active
        override.</p></div>
      <div><h4>The market never leaks in</h4><p>Projections are priced
        blind. Lines appear here only as the after-the-fact comparison —
        the same discipline the backtests were scored under.</p></div>
      <div><h4>Disagreement is not a bet</h4><p>A gap becomes a pick only
        through the gates: FanDuel still at its opener, fresh panel, fresh
        player, claimed EV under {SUSPECT_EV:.0%}. Bigger claims are
        quarantined as suspected defects.</p></div>
    </div>
  </div>

  <p class="foot">Projection sheet:
    <a href="{REPO}/blob/main/wnba/src/fp_live.py">wnba/src/fp_live.py</a>
    · talent engine:
    <a href="{REPO}/blob/main/wnba/src/talent.py">wnba/src/talent.py</a>
    · raw archive:
    <a href="{REPO}/blob/main/wnba/live/projections.csv">wnba/live/projections.csv</a>
    · registrations and method:
    <a href="{REPO}/blob/main/PROGRESS.md">PROGRESS.md</a> ·
    <a href="{REPO}/blob/main/{cfg['readme']}">{esc(cfg['readme'])}</a></p>
</section>"""


def live_panel(m):
    cfg, bank = m["cfg"], m["bank"]
    delta = bank["current"] - bank["start"]
    dtone = "good" if delta > 0 else ("bad" if delta < 0 else "flat")

    tiles = [tile("Settled bets",
                  f'{len(m["settled"])}',
                  f'{m["wins"]}W{MINUS}{len(m["settled"]) - m["wins"]}L'
                  if m["settled"] else "none yet")]
    tiles.append(tile("Open positions", f'{len(m["open"])}',
                      f'{money(sum(b["_stake"] for b in m["open"]))} at risk'
                      if m["open"] else "—"))
    tiles.append(tile(
        "P&L", signed_money(m["pnl"]) if m["settled"] else "—",
        f'{signed_pct(m["roi"])} ROI on {money(m["staked"])} staked'
        if m["roi"] is not None else "nothing staked yet",
        "good" if m["pnl"] > 0 else ("bad" if m["pnl"] < 0 else "")))
    tiles.append(tile(
        "Mean CLV",
        signed_pct(m["mean_clv"]) if m["mean_clv"] is not None else "—",
        f'raw close · n = {m["n_clv"]}'
        + (f' · t = {m["t_stat"]:.2f}'.replace("-", MINUS)
           if m["t_stat"] is not None else "")))
    tiles.append(tile(
        "Mean CLV*",
        signed_pct(m["mean_cal"]) if m["mean_cal"] is not None else "—",
        "shade-adjusted close — the fair yardstick (AUDIT N1)",
        "good" if (m["mean_cal"] or 0) > 0 else
        ("bad" if m["mean_cal"] is not None and m["mean_cal"] < 0 else "")))
    if m["n_ev"]:
        tiles.append(tile(
            "Model-expected P&L", signed_money(m["model_exp_pnl"]),
            f'what the model claimed these bets were worth '
            f'({signed_pct(m["mean_ev"])} per bet)'))
    tiles.append(tile(
        "CLV-expected P&L",
        signed_money(m["exp_pnl"]) if m["n_clv"] else "—",
        "what the closing line says you should have won"))
    if m["n_stamped"]:
        tiles.append(tile(
            "Line moved after bet",
            f'{m["n_moved"]} of {m["n_stamped"]}',
            "rest closed at the bet line, where CLV can only measure vig"))
    if m["calib"]:
        c = m["calib"]
        tiles.append(tile(
            "Model calibration",
            f'{c["obs"]}W vs {c["exp"]:.1f} expected',
            f'binomial z = {c["z"]:+.2f}'.replace("-", MINUS)
            + f' on the model\'s own win claims (n = {c["n"]})',
            "bad" if c["z"] <= -2 else ("good" if abs(c["z"]) < 1 else "")))
    if m["pushes"] or m["voids"]:
        tiles.append(tile("Push / void", f'{m["pushes"]} / {m["voids"]}',
                          "stake returned"))

    pnl_chart = line_chart(
        [{"label": "actual P&L", "pts": m["pnl_pts"]},
         {"label": "CLV-expected P&L", "pts": m["exp_pts"]}],
        "pnl", "money", events=EVENTS, area=True,
        aria=f'Cumulative profit and loss over time, currently '
             f'{signed_money(m["pnl"])}')
    clv_chart = line_chart(
        [{"label": "vs raw close", "pts": m["clvm_pts"]},
         {"label": "vs shade-adjusted close (CLV*)", "pts": m["calm_pts"]}],
        "clv", "pct", events=EVENTS,
        aria="Running mean closing-line value per bet over time")

    charts = ""
    if pnl_chart:
        charts += f"""
  <div class="block">
    <h3>Profit &amp; loss over time</h3>
    <p class="note">Cumulative settled P&amp;L, next to what the closing line
      implied the same stakes were worth. Dashed rules mark process changes:
      the from-scratch v3 model going live ({date_short(V3_LIVE)} — bets left
      of it belong to the retired opener-anchored model) and the pick gates
      ({date_short(GATES)}).</p>
    {pnl_chart}
  </div>"""
    if clv_chart:
        charts += f"""
  <div class="block">
    <h3>Closing-line value over time</h3>
    <p class="note">Running mean CLV per bet. Raw CLV (vs the close as quoted)
      skews negative by construction: an under-heavy sheet fades a close the
      books shade toward overs. CLV* re-scores the same bets against the
      shade-adjusted close (AUDIT N1) and is the fair yardstick.</p>
    {clv_chart}
  </div>"""

    cols, rows, aligns = bet_rows(m)
    no_match = (f'<tr class="no-match" hidden><td colspan="{len(cols)}">'
                f'No bets match the current filters.</td></tr>')
    log = (filter_block(m)
           + table(cols, rows, "tbl", aligns,
                   wrap_attrs=f' id="log-{cfg["id"]}"', extra_row=no_match)
           if rows else
           empty("No bets logged yet",
                 "Fills are reported conversationally and logged to "
                 "live/bets.csv."))
    runs = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>"
                   for k, v in cfg["runs"])

    return f"""
<section class="panel" id="panel-live" role="tabpanel"
         aria-labelledby="tab-live">
  <div class="panel-head">
    <div>
      <h2>{esc(cfg['label'])} <span class="head-pill">{status_pill(m)}</span></h2>
      <p class="lede">{esc(cfg['what'])}</p>
      <dl class="meta">{runs}</dl>
    </div>
    <div class="hero">
      <div class="hero-label">Bankroll</div>
      <div class="hero-fig">{money(bank['current'])}</div>
      <div class="hero-sub val-{dtone}">{signed_money(delta)} on
        {money(bank['start'], 0)} start</div>
    </div>
  </div>
  <div class="tiles">{''.join(tiles)}</div>
{charts}
{era_block(m)}
{slices_block(m)}
  <div class="block">
    <h3>Is it beating the close?</h3>
    <p class="note">CLV is the scoreboard: one season of profit is noise, one
      season of closing-line value is decisive. The band is what the backtest
      says these bets should average against the raw close.</p>
    {clv_band(cfg, m['mean_clv'], m['n_clv'])}
  </div>
  <div class="block">
    <h3>On the sheet now</h3>
    {picks_block(m)}
  </div>

  <div class="block">
    <h3>Bet log</h3>
    {log}
  </div>
{limits_block(m)}
  <div class="block explain">
    <h3>How to read this</h3>
    <div class="explain-grid">
      <div><h4>CLV, not profit</h4><p>Closing-line value is
        <code>p_close × odds_taken − 1</code>: did the price beat where the
        market ended up? It converges within a season. ROI needs several.</p></div>
      <div><h4>Priced from scratch</h4><p>The v3 talent model never sees the
        market's number. It prices the slate itself and bets FanDuel two-way
        quotes where its claimed EV &gt; 10%, quarter-Kelly on half the
        claimed edge.</p></div>
      <div><h4>Two yardsticks</h4><p>Raw CLV compares to the close as quoted;
        CLV* to the shade-adjusted close. On an under-heavy sheet the raw
        number is structurally negative — CLV* is the one that has to reach
        zero and beyond.</p></div>
      <div><h4>Losing can still be a pass</h4><p>Negative P&amp;L with clearly
        positive CLV means the model works and variance didn't cooperate.
        Profit with negative CLV is just luck.</p></div>
    </div>
  </div>

  <p class="foot">Protocol:
    <a href="{REPO}/blob/main/{cfg['protocol']}">{esc(cfg['protocol'])}</a>
    · method and backtest:
    <a href="{REPO}/blob/main/{cfg['readme']}">{esc(cfg['readme'])}</a>
    · raw record:
    <a href="{REPO}/blob/main/{cfg['dir']}/RESULTS.md">RESULTS.md</a></p>
</section>"""


def sim_table(spec):
    body = []
    for i, row in enumerate(spec["rows"]):
        cells = []
        for j, c in enumerate(row):
            val = c if j < 2 else f'<span class="mono">{c}</span>'
            cells.append(f'<td class="a-{"l" if j < 2 else "r"}">{val}</td>')
        cls = "row-best" if i == spec["best"] else ""
        body.append(f'<tr class="{cls}">{"".join(cells)}</tr>')
    head = "".join(f'<th class="a-{"l" if j < 2 else "r"}">{c}</th>'
                   for j, c in enumerate(spec["cols"]))
    return (f'<div class="scroll"><table class="tbl"><thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(body)}</tbody></table></div>'
            f'<p class="note">{spec["caption"]}</p>')


def evidence_panel():
    beaten = sum(1 for r in LEDGER if r["verdict"] == "yes")
    def gate(state, text):
        cls = {True: "on", False: "off", None: "unknown"}[state]
        return f'<span class="gate gate-{cls}">{text}</span>'

    rows = []
    for r in LEDGER:
        gates = (gate(r["lazy_open"], "lazy open")
                 + gate(r["live_close"], "informative close"))
        verdict = pill("beaten", "good") if r["verdict"] == "yes" \
            else pill("not beaten", "idle")
        cap = (meter(r["capture"], f'{r["capture"] * 100:.0f}% of the wedge captured')
               if r["capture"] else '<span class="muted">no wedge to capture</span>')
        link = r["link"] if r["link"].startswith("http") \
            else f'{REPO}/tree/main/{r["link"]}'
        report = ""
        if r.get("report"):
            href, label = r["report"]
            report = (f'<a class="ledger-report" href="{href}">'
                      f'{esc(label)} →</a>')
        rows.append(
            f'<article class="ledger-row">'
            f'<div class="ledger-name"><a href="{link}">{esc(r["market"])}</a>'
            f'<span class="muted">{esc(r["where"])}</span></div>'
            f'<div class="ledger-gates">{gates}</div>'
            f'<div class="ledger-verdict">{verdict}</div>'
            f'<div class="ledger-why">{esc(r["why"])} {report}'
            f'<div class="ledger-cap">{cap}</div></div></article>')

    return f"""
<section class="panel" id="panel-evidence" role="tabpanel"
         aria-labelledby="tab-evidence" hidden>
  <div class="panel-head">
    <div>
      <h2>Why bet this at all</h2>
      <p class="lede">The same method was pointed at four markets and honestly
        reported "no" twice. An opener is only exploitable when the book is
        lazy about it <em>and</em> real information arrives before the close —
        two gates, not one.</p>
    </div>
    <div class="hero">
      <div class="hero-label">Openers beaten</div>
      <div class="hero-fig">{beaten} <span class="hero-of">of
        {len(LEDGER)}</span></div>
      <div class="hero-sub">markets tested, out-of-sample</div>
    </div>
  </div>

  <div class="ledger">{''.join(rows)}</div>

  <div class="block narrow">
    <h3>WNBA props betting simulation</h3>
    {sim_table(WNBA_SIM)}
  </div>

  <p class="foot">Full write-ups, caveats and reproduction steps:
    <a href="{REPO}/tree/main/soccer">soccer/</a> ·
    <a href="{REPO}/tree/main/wnba">wnba/</a> ·
    <a href="{REPO}/tree/main/cricket">cricket/</a> ·
    <a href="{REPO}/tree/main/nba">nba/</a>.
    Simulations model prices, not frictions — limits, restrictions and
    palpable-error voids are real and unmodelled. The soccer research record
    lives in the Archive tab.</p>
</section>"""


def archive_panel(m):
    cfg = m["cfg"]
    curve = backtest_curve()
    chart = line_chart(
        [{"label": "cumulative units", "pts": curve}], "backtest", "units",
        area=True, mark_dd=True,
        aria=f'Soccer backtest cumulative units over time, ending '
             f'{curve[-1][1]:+,.0f}' if curve else "")
    chart_block = (f"""
    <h4>What the edge looked like out-of-sample</h4>
    <p class="note">Cumulative profit of the backtested strategy — flat 1-unit
      bets at the best early price whenever the model saw &gt;2% EV, over 31,192
      bets and nine walk-forward seasons. Never retrained on the future.</p>
    {chart}""" if chart else "")
    runs = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>"
                   for k, v in cfg["runs"])

    return f"""
<section class="panel" id="panel-archive" role="tabpanel"
         aria-labelledby="tab-archive" hidden>
  <div class="panel-head">
    <div>
      <h2>The archive</h2>
      <p class="lede">Experiments that ended stay published — a cancelled
        experiment is a result, not an embarrassment. Nothing here is live
        and nothing here should be bet.</p>
    </div>
    <div class="hero">
      <div class="hero-label">Live bets placed</div>
      <div class="hero-fig">0</div>
      <div class="hero-sub">across everything on this page</div>
    </div>
  </div>

  <div class="block">
    <h3>{esc(cfg['label'])} <span class="head-pill">{pill("cancelled", "idle")}</span></h3>
    <p class="note">{esc(cfg['what'])}</p>
    <p class="note"><strong>Why there is no record:</strong>
      {esc(cfg['cancelled'])} Full numbers below and in the subproject
      README.</p>
    <dl class="meta">{runs}</dl>
    {chart_block}
    <h4>Betting simulation — the cancellation evidence</h4>
    {sim_table(SOCCER_SIM)}
    <p class="foot">Protocol:
      <a href="{REPO}/blob/main/{cfg['protocol']}">{esc(cfg['protocol'])}</a>
      · method and backtest:
      <a href="{REPO}/blob/main/{cfg['readme']}">{esc(cfg['readme'])}</a></p>
  </div>

  <div class="block">
    <h3>WNBA props — the anchored era <span class="head-pill">{pill("retired", "idle")}</span></h3>
    <p class="note">The model that opened the WNBA live experiment anchored on
      the market's opening price and bet the residual. It was retired on
      {date_short(V3_LIVE)} — it inverted its opinion out of the price rather
      than pricing games itself — and the from-scratch v3 talent model took
      over the same evening. Its handful of bets stay in the live bet log,
      left of the "v3 model live" rule on the charts, because the bankroll is
      continuous. The full post-mortem is in
      <a href="{REPO}/blob/main/PROGRESS.md">PROGRESS.md</a> and
      <a href="{REPO}/blob/main/AUDIT.md">AUDIT.md</a>.</p>
  </div>
</section>"""


# ------------------------------------------------------------------ page ----

CSS = """
:root{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --sunk:#f3f2ee;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --rule:#e6e5df; --base:#c3c2b7;
  --model:#2a78d6; --model-soft:rgba(42,120,214,.12);
  --market:#eb6834;
  /* players heat ramp: sequential blue, bottom decile recedes to surface;
     ink per step flips to white where the fill goes dark (all pairs
     computed >= 4.4:1) */
  --h1:#cde2fb; --h2:#9ec5f4; --h3:#6da7ec; --h4:#2a78d6; --h5:#184f95;
  --h1i:#0b0b0b; --h2i:#0b0b0b; --h3i:#0b0b0b; --h4i:#fff; --h5i:#fff;
  /* status as text: the light-surface success/critical steps; warning is
     darkened from #fab219, which is illegible as small text on cream. */
  --good:#006300; --bad:#d03b3b; --warn:#b07100;
  --shadow:0 1px 2px rgba(11,11,11,.05), 0 8px 24px rgba(11,11,11,.05);
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Roboto Mono",monospace;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --plane:#0d0d0d; --surface:#1a1a19; --sunk:#141413;
    --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --rule:#2c2c2a; --base:#383835;
    --model:#3987e5; --model-soft:rgba(57,135,229,.16);
    --market:#d95926;
    /* heat ramp flips anchor: near-zero recedes to the dark surface,
       magnitude brightens */
    --h1:#104281; --h2:#1c5cab; --h3:#2a78d6; --h4:#5598e7; --h5:#86b6ef;
    --h1i:#fff; --h2i:#fff; --h3i:#fff; --h4i:#0b0b0b; --h5i:#0b0b0b;
    --good:#0ca30c; --bad:#e66767; --warn:#fab219;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --sunk:#141413;
  --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --rule:#2c2c2a; --base:#383835;
  --model:#3987e5; --model-soft:rgba(57,135,229,.16);
  --market:#d95926;
  --h1:#104281; --h2:#1c5cab; --h3:#2a78d6; --h4:#5598e7; --h5:#86b6ef;
  --h1i:#fff; --h2i:#fff; --h3i:#fff; --h4i:#0b0b0b; --h5i:#0b0b0b;
  --good:#0ca30c; --bad:#e66767; --warn:#fab219;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
}

*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:inherit}
h1,h2,h3,h4{margin:0;text-wrap:balance;line-height:1.2}
p{margin:0}
code{font-family:var(--mono);font-size:.88em;background:var(--sunk);
  padding:.1em .35em;border-radius:4px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.val-good{color:var(--good)} .val-bad{color:var(--bad)} .val-flat{color:var(--ink2)}
.a-l{text-align:left} .a-r{text-align:right} .a-m{text-anchor:middle}

.wrap{max-width:1080px;margin:0 auto;padding:0 20px 72px}

/* masthead */
.masthead{border-bottom:1px solid var(--rule);background:var(--surface)}
.masthead .wrap{padding-top:26px;padding-bottom:0;display:flex;
  flex-wrap:wrap;gap:16px 28px;align-items:flex-start;
  justify-content:space-between}
.brand h1{font-size:27px;letter-spacing:-.02em}
.brand p{color:var(--ink2);max-width:60ch;margin-top:6px}
.stamp{color:var(--muted);font-size:12.5px;text-align:right;
  display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.stamp .mono{font-size:12px}
.stamp-row{display:flex;gap:8px;align-items:stretch}
.repo-link{text-decoration:none;border:1px solid var(--base);border-radius:999px;
  padding:5px 12px;color:var(--ink2);font-size:12.5px;white-space:nowrap}
.repo-link:hover{border-color:var(--ink2);color:var(--ink)}

/* theme toggle: shows the mode a click switches to, driven by the same
   three scopes as the colour tokens (system dark / explicit dark / light) */
.theme-btn{appearance:none;cursor:pointer;background:none;font:inherit;
  border:1px solid var(--base);border-radius:999px;padding:5px 9px;
  color:var(--ink2);display:inline-flex;align-items:center}
.theme-btn:hover{border-color:var(--ink2);color:var(--ink)}
.theme-btn:focus-visible{outline:2px solid var(--model);outline-offset:1px}
.theme-btn svg{width:15px;height:15px;fill:none;stroke:currentColor;
  stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.theme-btn .icon-sun{display:none}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])) .theme-btn .icon-moon{display:none}
  :root:where(:not([data-theme="light"])) .theme-btn .icon-sun{display:block}
}
:root[data-theme="dark"] .theme-btn .icon-moon{display:none}
:root[data-theme="dark"] .theme-btn .icon-sun{display:block}
:root[data-theme="light"] .theme-btn .icon-moon{display:block}
:root[data-theme="light"] .theme-btn .icon-sun{display:none}

/* tabs */
.tabs{display:flex;gap:2px;margin:22px 0 0;flex-wrap:wrap}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  padding:10px 14px;font:inherit;font-size:14px;color:var(--ink2);cursor:pointer;
  border-radius:6px 6px 0 0}
.tab:hover{color:var(--ink);background:var(--sunk)}
.tab[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--model);
  font-weight:600}
.tab:focus-visible{outline:2px solid var(--model);outline-offset:-2px}

/* panels */
.panel{padding-top:28px}
.panel-head{display:flex;gap:28px;justify-content:space-between;
  align-items:flex-start;flex-wrap:wrap;margin-bottom:22px}
.panel-head h2{font-size:21px;letter-spacing:-.01em}
.head-pill{font-weight:400;vertical-align:2px}
.lede{color:var(--ink2);max-width:62ch;margin-top:8px}
.hero{text-align:right;min-width:190px}
.hero-label{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted)}
.hero-fig{font-size:48px;font-weight:600;letter-spacing:-.03em;line-height:1.05;
  margin-top:2px}
.hero-of{font-size:22px;font-weight:400;color:var(--muted)}
.hero-sub{font-size:13px;color:var(--ink2);margin-top:2px}

/* tiles */
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(156px,1fr))}
.tile{background:var(--surface);border:1px solid var(--rule);border-radius:10px;
  padding:14px 16px;box-shadow:var(--shadow)}
.tile-label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted)}
.tile-val{font-size:24px;font-weight:600;letter-spacing:-.02em;margin-top:4px}
.tile-sub{font-size:12.5px;color:var(--ink2);margin-top:2px}

/* blocks */
.block{margin-top:34px}
.block>h3{font-size:16px;letter-spacing:-.01em}
.block h4{font-size:14px;margin-top:22px}
.block>.note,.block>p.note,.block h4+.note{margin-top:6px}
.note{font-size:13px;color:var(--ink2);max-width:70ch}
.note+.scroll,.bandwrap,.tbl,.empty,h4+.scroll{margin-top:12px}
.narrow{max-width:760px}
.foot{margin-top:32px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted);max-width:78ch}
.foot a{color:var(--ink2)}

/* how a market runs */
.meta{display:flex;flex-wrap:wrap;gap:6px 26px;margin:14px 0 0}
.meta div{display:flex;flex-direction:column;gap:1px}
.meta dt{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted)}
.meta dd{margin:0;font-size:13px;color:var(--ink2)}

/* tables */
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;
  background:var(--surface)}
table.tbl{border-collapse:collapse;width:100%;font-size:13.5px}
.tbl th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);font-weight:500;padding:10px 14px;white-space:nowrap;
  border-bottom:1px solid var(--rule);background:var(--surface);
  position:sticky;top:0}
.tbl td{padding:9px 14px;border-bottom:1px solid var(--rule);white-space:nowrap;
  font-variant-numeric:tabular-nums}
.tbl tbody tr:last-child td{border-bottom:0}
.tbl tbody tr:hover{background:var(--sunk)}
.tbl .row-best td{background:var(--model-soft)}
.tbl .row-best td:first-child{box-shadow:inset 2px 0 0 var(--model)}
.tbl .no-match td{text-align:center;color:var(--muted);padding:20px}

/* players: heat cells, leaderboard cards, season-arc sparklines */
.tbl td.heat{min-width:56px}
.tbl td.h1{background:var(--h1);color:var(--h1i)}
.tbl td.h2{background:var(--h2);color:var(--h2i)}
.tbl td.h3{background:var(--h3);color:var(--h3i)}
.tbl td.h4{background:var(--h4);color:var(--h4i)}
.tbl td.h5{background:var(--h5);color:var(--h5i)}
.row-stale td{color:var(--muted)}
.pname{font-weight:600}
.heat-key{white-space:nowrap}
.hk{display:inline-block;width:13px;height:9px;border-radius:2px;
  background:var(--sunk);margin:0 1px;vertical-align:-1px}
.hk.h1{background:var(--h1)} .hk.h2{background:var(--h2)}
.hk.h3{background:var(--h3)} .hk.h4{background:var(--h4)}
.hk.h5{background:var(--h5)}
.leaders{display:grid;gap:12px;margin-top:14px;
  grid-template-columns:repeat(auto-fit,minmax(225px,1fr))}
.leader{background:var(--surface);border:1px solid var(--rule);
  border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.leader h4{font-size:13.5px;margin:0}
.leader-sub{font-size:11px;color:var(--muted);margin:1px 0 8px}
.lb{display:flex;gap:9px;align-items:center;padding:4px 0}
.lb-rank{color:var(--muted);font-size:11px;width:12px;text-align:right;
  flex:none}
.lb-mid{flex:1;min-width:0}
.lb-name{display:block;font-size:13px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.lb-track{height:3px;border-radius:2px;background:var(--model-soft);
  margin-top:3px}
.lb-fill{height:100%;border-radius:2px;background:var(--model)}
.lb-val{font-size:13px;flex:none}
.players-scroll{max-height:640px;overflow-y:auto}
.spark{width:120px;height:30px;vertical-align:middle}
.spark .sl{fill:none;stroke:var(--model);stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.spark .sd{fill:var(--model);stroke:var(--surface);stroke-width:2}
.trend{white-space:nowrap}
.trend-d{font-size:12px;margin-left:6px}
.thsort{appearance:none;background:none;border:0;padding:0;font:inherit;
  color:inherit;cursor:pointer;letter-spacing:inherit;
  text-transform:inherit}
.thsort:hover{color:var(--ink)}
.thsort:focus-visible{outline:2px solid var(--model);outline-offset:1px}
th[aria-sort] .thsort{color:var(--ink);font-weight:600}
th[aria-sort="descending"] .thsort::after{content:" ↓"}
th[aria-sort="ascending"] .thsort::after{content:" ↑"}

/* filters */
.filterbar{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.filterbar select,.filterbar input{font:inherit;font-size:13px;
  color:var(--ink);background:var(--surface);border:1px solid var(--rule);
  border-radius:8px;padding:6px 10px}
.filterbar input{flex:1;min-width:140px;max-width:240px}
.filterbar .frange{display:flex;align-items:center;gap:6px}
.filterbar input[type=date]{flex:0 0 auto;min-width:0;max-width:none;
  padding:5px 8px}
.filterbar .fdash{color:var(--muted);font-size:13px}
.filterbar .flabel{color:var(--ink2);font-size:13px;padding-right:2px}
.filterbar select:hover,.filterbar input:hover{border-color:var(--base)}
.filterbar select:focus-visible,.filterbar input:focus-visible{
  outline:2px solid var(--model);outline-offset:1px}
.fsummary{margin-top:10px;font-size:12.5px;color:var(--ink2);
  font-variant-numeric:tabular-nums}
.fsummary+.scroll{margin-top:10px}

/* record slices */
.slices{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
  margin-top:12px;align-items:start}
.slice h4{font-size:13px;margin:0 0 8px;color:var(--ink2)}
.slice .scroll{margin-top:0}
.slice .tbl th,.slice .tbl td{padding:8px 11px}

/* era comparison */
.era{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  margin-top:14px}
.era-card{background:var(--surface);border:1px solid var(--rule);
  border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}
.era-card h4{font-size:14px;margin:0}
.era-now{border-color:color-mix(in srgb,var(--model) 45%,var(--rule))}
.era-dates{font-size:12px;color:var(--muted);margin-top:2px}
.era-stats{display:flex;flex-wrap:wrap;gap:12px 26px;margin-top:14px}
.es-label{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted)}
.es-val{font-size:17px;font-weight:600;letter-spacing:-.01em;margin-top:2px;
  font-variant-numeric:tabular-nums}
.band-note{font-size:12.5px;color:var(--ink2);margin-top:8px;max-width:70ch}

/* chips + pills */
.chip{display:inline-block;font-size:11.5px;padding:2px 7px;border-radius:5px;
  background:var(--sunk);color:var(--ink2);border:1px solid var(--rule)}
.chip-good{color:var(--good);border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.chip-bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 35%,transparent)}
.chip-open{color:var(--muted)}
.chip-strong{color:var(--market);
  border-color:color-mix(in srgb,var(--market) 40%,transparent)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;
  padding:3px 9px;border-radius:999px;border:1px solid var(--rule);
  background:var(--sunk);color:var(--ink2);white-space:nowrap}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;
  background:currentColor}
.pill-live{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 35%,transparent)}
.pill-good{color:var(--good);border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.pill-warm{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,transparent)}
.pill-idle{color:var(--muted)}

/* empty state */
.empty{display:flex;flex-direction:column;gap:4px;padding:22px;text-align:center;
  border:1px dashed var(--grid);border-radius:10px;color:var(--muted);font-size:13px}
.empty strong{color:var(--ink2);font-weight:600}

/* CLV band */
.bandwrap{max-width:600px;margin-top:18px}
.band{position:relative;height:56px;background:var(--sunk);
  border:1px solid var(--rule);border-radius:8px}
.band-target{position:absolute;top:0;bottom:0;background:var(--model-soft);
  border-left:2px solid var(--model);border-right:2px solid var(--model)}
.band-tlabel{position:absolute;bottom:6px;left:50%;transform:translateX(-50%);
  font-size:11px;color:var(--ink2);white-space:nowrap}
.band-point{width:0;bottom:24px;background:none;border-right:0}
.band-point .band-tlabel{bottom:-22px}
.band-zero{position:absolute;top:0;bottom:0;width:1px;background:var(--base)}
.band-mark{position:absolute;top:0;bottom:0;width:2px;background:var(--ink)}
.band-mark span{position:absolute;top:5px;left:50%;transform:translateX(-50%);
  font-size:12.5px;font-family:var(--mono);white-space:nowrap;
  background:var(--surface);border:1px solid var(--base);border-radius:5px;
  padding:1px 6px}
.band-axis{position:relative;height:16px;margin-top:5px}
.band-axis span{position:absolute;transform:translateX(-50%);font-size:11px;
  color:var(--muted);font-variant-numeric:tabular-nums}

/* ledger */
.ledger{display:flex;flex-direction:column;gap:1px;background:var(--rule);
  border:1px solid var(--rule);border-radius:12px;overflow:hidden}
.ledger-row{display:grid;gap:6px 18px;padding:16px 18px;background:var(--surface);
  grid-template-columns:minmax(150px,1.1fr) auto auto;align-items:center}
.ledger-name{display:flex;flex-direction:column;gap:2px;font-weight:600}
.ledger-name a{text-decoration:none}
.ledger-name a:hover{text-decoration:underline}
.ledger-name span{font-size:12px;font-weight:400}
.ledger-gates{display:flex;gap:6px;flex-wrap:wrap}
.gate{font-size:11.5px;padding:2px 8px;border-radius:5px;border:1px solid var(--rule);
  color:var(--muted);background:var(--sunk);white-space:nowrap}
.gate::before{content:"\\2715  ";font-size:10px}
.gate-on{color:var(--good);border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.gate-on::before{content:"\\2713  "}
.gate-unknown::before{content:"?  "}
.ledger-verdict{justify-self:end}
.ledger-why{grid-column:1/-1;font-size:13px;color:var(--ink2);max-width:74ch}
.ledger-report{color:var(--model);text-decoration:none;white-space:nowrap}
.ledger-report:hover{text-decoration:underline}
.ledger-cap{margin-top:8px}

/* meter */
.meter{display:flex;align-items:center;gap:10px;max-width:420px}
.meter-track{position:relative;flex:1;height:8px;border-radius:999px;
  background:var(--model-soft)}
.meter-fill{position:absolute;left:0;top:0;bottom:0;border-radius:0 4px 4px 0;
  background:var(--model)}
.meter-label{font-size:12px;color:var(--ink2);white-space:nowrap}

/* charts */
.chart{margin:14px 0 0;position:relative}
.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .zero{stroke:var(--base);stroke-width:1}
.chart .tick{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.chart .area{fill:var(--model);opacity:.08;stroke:none}
.chart .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .line.s1{stroke:var(--model)}
.chart .line.s2{stroke:var(--market)}
.chart .dot-end{stroke:var(--surface);stroke-width:2}
.chart .dot-end.s1{fill:var(--model)}
.chart .dot-end.s2{fill:var(--market)}
.chart .dot-dd{fill:var(--market);stroke:var(--surface);stroke-width:2}
.chart .ann{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.chart .end-label{fill:var(--ink);font-size:12.5px;font-weight:600;
  font-family:var(--sans)}
.chart .evt{stroke:var(--base);stroke-width:1;stroke-dasharray:3 4}
.chart .evt-major{stroke:var(--ink2)}
.chart .evt-label{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.chart .evt-label.major{fill:var(--ink2);font-weight:600}
.chart .hit{fill:transparent}
.chart .cross[hidden]{display:none}
.chart .cross-l{stroke:var(--base);stroke-width:1}
.chart .cross-d{stroke:var(--surface);stroke-width:2}
.chart .cross-d.s1{fill:var(--model)}
.chart .cross-d.s2{fill:var(--market)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:12px 0 0;
  font-size:12.5px;color:var(--ink2)}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;
  margin-right:7px;vertical-align:-1px}
.sw-1{background:var(--model)} .sw-2{background:var(--market)}
.tip{position:absolute;pointer-events:none;background:var(--surface);
  border:1px solid var(--base);border-radius:8px;padding:7px 10px;font-size:12.5px;
  box-shadow:var(--shadow);white-space:nowrap;transform:translate(-50%,-118%);
  font-variant-numeric:tabular-nums;display:flex;flex-direction:column;gap:2px}
.tip[hidden]{display:none}
.tip .td{color:var(--muted);font-size:11.5px}
.tip b{font-size:13px}

/* explainer */
.explain-grid{display:grid;gap:18px;margin-top:12px;
  grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.explain-grid h4{font-size:13.5px;margin-top:0}
.explain-grid p{font-size:13px;color:var(--ink2);margin-top:4px}

@media (max-width:640px){
  .hero{text-align:left}
  .hero-fig{font-size:38px}
  .ledger-row{grid-template-columns:1fr auto}
  .ledger-gates{grid-column:1/-1}
  .tabs{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
  .tabs::-webkit-scrollbar{display:none}
  .tab{white-space:nowrap}
  /* keep chart text legible: scroll sideways instead of squashing the svg */
  .chart{overflow-x:auto}
  .chart svg{min-width:640px}
}
@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important}
}
"""

JS = """
(function(){
  // theme toggle: flips the effective mode, remembers the choice. The head
  // script applies a stored choice before first paint; this re-applies it
  // too so the --fragment embedding (no head) still honours it.
  var root=document.documentElement;
  try{
    var stored=localStorage.getItem('theme');
    if(stored==='light'||stored==='dark')root.dataset.theme=stored;
  }catch(e){}
  function effective(){
    if(root.dataset.theme)return root.dataset.theme;
    return matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  }
  document.querySelectorAll('.theme-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      var next=effective()==='dark'?'light':'dark';
      root.dataset.theme=next;
      try{localStorage.setItem('theme',next);}catch(e){}
    });
  });

  var tabs=[].slice.call(document.querySelectorAll('[role="tab"]'));
  var ids=tabs.map(function(t){return t.dataset.panel;});
  var alias={wnba:'live',soccer:'archive',evidence:'evidence'};
  function canon(id){id=alias[id]||id;return ids.indexOf(id)>-1?id:ids[0];}
  function show(id,push){
    tabs.forEach(function(t){
      var on=t.dataset.panel===id;
      t.setAttribute('aria-selected',on?'true':'false');
      t.tabIndex=on?0:-1;
      document.getElementById('panel-'+t.dataset.panel).hidden=!on;
    });
    if(push&&location.hash.slice(1)!==id)history.replaceState(null,'','#'+id);
  }
  tabs.forEach(function(t,i){
    t.addEventListener('click',function(){show(t.dataset.panel,true);});
    t.addEventListener('keydown',function(e){
      var d=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;
      if(!d)return;
      e.preventDefault();
      var n=tabs[(i+d+tabs.length)%tabs.length];
      n.focus();show(n.dataset.panel,true);
    });
  });
  show(canon(location.hash.slice(1)),false);
  window.addEventListener('hashchange',function(){
    show(canon(location.hash.slice(1)),false);
  });

  function fmtv(v,unit){
    var s=v<0?'\\u2212':'+',a=Math.abs(v);
    if(unit==='money')return s+'$'+a.toFixed(2);
    if(unit==='pct')return s+a.toFixed(1)+'%';
    return s+a.toLocaleString(undefined,{maximumFractionDigits:0})+'u';
  }
  document.querySelectorAll('.chart').forEach(function(fig){
    var svg=fig.querySelector('svg'),tip=fig.querySelector('.tip'),
        cross=fig.querySelector('.cross'),hit=fig.querySelector('.hit'),
        d=JSON.parse(fig.querySelector('script[type="application/json"]').textContent),
        dots=cross.querySelectorAll('.cross-d'),
        vb=svg.viewBox.baseVal;
    function at(ev){
      var r=svg.getBoundingClientRect(),sx=(ev.clientX-r.left)/r.width*vb.width,
          i=0,best=1e9;
      for(var k=0;k<d.x.length;k++){var dx=Math.abs(d.x[k]-sx);
        if(dx<best){best=dx;i=k;}}
      cross.removeAttribute('hidden');   // SVG has no .hidden property
      cross.querySelector('.cross-l').setAttribute('x1',d.x[i]);
      cross.querySelector('.cross-l').setAttribute('x2',d.x[i]);
      var rows='',topY=1e9;
      d.series.forEach(function(s,k){
        if(dots[k]){dots[k].setAttribute('cx',d.x[i]);
          dots[k].setAttribute('cy',s.y[i]);}
        if(s.y[i]<topY)topY=s.y[i];
        rows+='<span><i class="sw sw-'+(k+1)+'"></i>'+
          (d.series.length>1?s.label+' ':'')+
          '<b>'+fmtv(s.v[i],d.unit)+'</b></span>';
      });
      tip.hidden=false;
      tip.innerHTML='<span class="td">'+d.d[i]+'</span>'+rows;
      // px against the figure origin, so it stays put when the chart
      // scrolls sideways on small screens
      var fr=fig.getBoundingClientRect();
      var ox=r.left-fr.left+fig.scrollLeft,oy=r.top-fr.top;
      var lx=ox+d.x[i]/vb.width*r.width;
      tip.style.left=Math.max(ox+56,Math.min(ox+r.width-56,lx))+'px';
      tip.style.top=(oy+topY/vb.height*r.height)+'px';
    }
    hit.addEventListener('pointermove',at);
    hit.addEventListener('pointerdown',at);
    fig.addEventListener('pointerleave',function(){
      cross.setAttribute('hidden','');tip.hidden=true;});
  });

  // players table: column sort + name filter
  var pt=document.getElementById('ptable');
  if(pt){
    var ptb=pt.tBodies[0];
    var prows=[].slice.call(ptb.querySelectorAll('tr[data-name]'));
    var pnone=ptb.querySelector('.no-match');
    var psum=document.getElementById('fsum-players');
    pt.querySelectorAll('th[data-key]').forEach(function(th){
      th.querySelector('.thsort').addEventListener('click',function(){
        var k=th.dataset.key,alpha=k==='name'||k==='asof';
        var dir=pt.dataset.sk===k?(pt.dataset.sd==='d'?'a':'d')
                                 :(alpha?'a':'d');
        pt.dataset.sk=k;pt.dataset.sd=dir;
        pt.querySelectorAll('th[data-key]').forEach(function(t){
          t.removeAttribute('aria-sort');});
        th.setAttribute('aria-sort',dir==='d'?'descending':'ascending');
        prows.sort(function(a,b){
          var av=a.dataset[k],bv=b.dataset[k];
          var am=av===''||av==null,bm=bv===''||bv==null;
          if(am&&bm)return 0;if(am)return 1;if(bm)return -1;
          var c=alpha?(av<bv?-1:av>bv?1:0):(+av)-(+bv);
          return dir==='d'?-c:c;
        });
        prows.forEach(function(r){ptb.insertBefore(r,pnone);});
      });
    });
    var pq=document.getElementById('pfind');
    if(pq)pq.addEventListener('input',function(){
      var q=pq.value.trim().toLowerCase(),shown=0;
      prows.forEach(function(r){
        var ok=!q||r.dataset.name.indexOf(q)>-1;
        r.hidden=!ok;if(ok)shown++;
      });
      if(pnone)pnone.hidden=shown>0;
      if(psum)psum.textContent=shown+' of '+prows.length+' players';
    });
  }

  document.querySelectorAll('.filterbar').forEach(function(bar){
    var scope=document.getElementById(bar.dataset.scope);
    var sum=document.getElementById(bar.dataset.summary);
    if(!scope)return;
    var rows=[].slice.call(scope.querySelectorAll('tbody tr[data-status]'));
    var none=scope.querySelector('.no-match');
    var ctrls=[].slice.call(bar.querySelectorAll('[data-f]'));
    function mean(a){var s=0;a.forEach(function(v){s+=v;});return s/a.length;}
    function pct(v){return (v<0?'\\u2212':'+')+Math.abs(v*100).toFixed(1)+'%';}
    function mny(v){return (v<0?'\\u2212':'+')+'$'+Math.abs(v).toFixed(2);}
    function apply(){
      var f={};
      ctrls.forEach(function(c){f[c.dataset.f]=c.value.trim().toLowerCase();});
      var shown=0,w=0,l=0,settled=0,pnl=0,stake=0,clv=[],cal=[];
      rows.forEach(function(r){
        var c=r.dataset.clv;
        var ok=(!f.market||r.dataset.market===f.market)&&
               (!f.side||r.dataset.side===f.side)&&
               (!f.result||r.dataset.result===f.result)&&
               (!f.era||r.dataset.era===f.era)&&
               (!f.from||r.dataset.date>=f.from)&&
               (!f.to||r.dataset.date<=f.to)&&
               (!f.clvsign||(f.clvsign==='none'?c==='':
                 c!==''&&(f.clvsign==='pos'?+c>0:+c<0)))&&
               (!f.q||r.dataset.player.indexOf(f.q)>-1);
        r.hidden=!ok;
        if(!ok)return;
        shown++;
        if(r.dataset.status==='settled'){
          settled++;pnl+=+r.dataset.pnl||0;stake+=+r.dataset.stake||0;
          if(r.dataset.result==='won')w++;
          else if(r.dataset.result==='lost')l++;
        }
        if(r.dataset.status!=='void'){
          if(r.dataset.clv!=='')clv.push(+r.dataset.clv);
          if(r.dataset.cal!=='')cal.push(+r.dataset.cal);
        }
      });
      if(none)none.hidden=shown>0;
      var bits=[shown+' of '+rows.length+' bets'];
      if(settled){
        bits.push(w+'W\\u2212'+l+'L');
        bits.push('P&L '+mny(pnl)+(stake?' ('+pct(pnl/stake)+' ROI)':''));
      }
      if(clv.length)bits.push('mean CLV '+pct(mean(clv)));
      if(cal.length)bits.push('CLV* '+pct(mean(cal)));
      if(sum)sum.textContent=bits.join(' \\u00b7 ');
    }
    ctrls.forEach(function(c){
      c.addEventListener(c.tagName==='INPUT'?'input':'change',apply);});
    apply();
  });
})();
"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
           "viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E"
           "%F0%9F%93%88%3C/text%3E%3C/svg%3E")


def render(markets, players=None, fragment=False):
    updated = stamp(markets)
    tabs = [("live", "Live"), ("evidence", "Evidence"), ("archive", "Archive")]
    if players:
        tabs.insert(1, ("players", "Players"))
    players_blurb = (" The Players tab is the model's current read on every"
                     " player it prices." if players else "")
    tab_html = "".join(
        f'<button class="tab" role="tab" id="tab-{i}" data-panel="{i}" '
        f'aria-controls="panel-{i}" aria-selected="false" tabindex="-1">'
        f'{esc(lab)}</button>' for i, lab in tabs)
    by_id = {m["cfg"]["id"]: m for m in markets}

    body = f"""
<header class="masthead">
  <div class="wrap">
    <div class="brand">
      <h1>Beating the opener</h1>
      <p>One live experiment: WNBA player props, priced from scratch by a
        talent model that never sees the market's number, bet on FanDuel and
        scored on closing-line value first, profit second.{players_blurb}
        Four markets were researched, and the two honest "no"s and the
        cancelled soccer experiment are kept in Evidence and the
        Archive.</p>
    </div>
    <div class="stamp">
      <div class="stamp-row">
        <button class="theme-btn" type="button" title="Toggle light/dark"
                aria-label="Toggle light or dark mode">
          <svg class="icon-sun" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="3.1"/>
            <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4
                     M11.55 11.55l1.4 1.4M12.95 3.05l-1.4 1.4
                     M4.45 11.55l-1.4 1.4"/>
          </svg>
          <svg class="icon-moon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M13.4 10.1A5.9 5.9 0 1 1 5.9 2.6a4.7 4.7 0 0 0 7.5 7.5z"/>
          </svg>
        </button>
        <a class="repo-link" href="{REPO}">Source on GitHub ↗</a>
      </div>
      <span>data updated <span class="mono">{esc(updated) or "—"}</span></span>
    </div>
    <nav class="tabs" role="tablist" aria-label="Results views">{tab_html}</nav>
  </div>
</header>
<main class="wrap">
{live_panel(by_id["wnba"])}
{players_panel(players) if players else ""}
{evidence_panel()}
{archive_panel(by_id["soccer"])}
</main>"""

    if fragment:
        # Hosted off-site, so sibling docs/ pages need their absolute home.
        for _, dst in EXTRA_PAGES:
            body = body.replace(f'href="{dst}"', f'href="{PAGES}{dst}"')
        return f"<style>{CSS}</style>\n{body}\n<script>{JS}</script>\n"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Beating the opener — live scoreboard</title>
<meta name="description" content="Live CLV, P&L and the full bet log for a
 from-scratch WNBA props model betting FanDuel openers, plus the research
 archive behind it.">
<meta property="og:title" content="Beating the opener — scoreboard">
<meta property="og:description" content="A from-scratch WNBA props model
 betting real money, scored on closing-line value, with the research record
 behind it.">
<link rel="icon" href="{FAVICON}">
<!-- Generated by site/build_site.py - do not edit by hand. -->
<script>try{{var t=localStorage.getItem('theme');
if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;
}}catch(e){{}}</script>
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>
"""


def write_if_changed(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and open(path).read() == content:
        return False
    with open(path, "w") as fh:
        fh.write(content)
    return True


def copy_pages():
    """Republish standalone write-ups that live with their subproject.

    Pages only serves docs/, so the page is copied rather than linked; the
    copy is refreshed here so it cannot drift from the original.
    """
    for src, dst in EXTRA_PAGES:
        path = os.path.join(ROOT, src)
        if not os.path.exists(path):
            print(f"missing {src} - skipped")
            continue
        with open(path) as fh:
            if write_if_changed(os.path.join(ROOT, "docs", dst), fh.read()):
                print(f"docs/{dst} updated")


def main():
    markets = [load_market(cfg) for cfg in MARKETS]
    players = load_players()
    if "--fragment" in sys.argv:
        path = sys.argv[sys.argv.index("--fragment") + 1]
        write_if_changed(path, render(markets, players, fragment=True))
        print(f"fragment -> {path}")
        return
    copy_pages()
    changed = write_if_changed(OUT, render(markets, players))
    print(f"docs/index.html {'updated' if changed else 'unchanged'}")


if __name__ == "__main__":
    main()
