"""Build docs/index.html - the at-a-glance scoreboard for the live experiments.

Reads each market's live/ files (bankroll, bets, picks) plus soccer's backtest
P&L curve and renders one self-contained page: live bankrolls and CLV, open
positions, the bet log, and the research evidence behind the wedge.

Auto-generated - the last step of each market's settle_bets.py run regenerates
it, so never hand-edit docs/index.html. Stdlib only, no build step.

    python3 site/build_site.py              # -> docs/index.html
    python3 site/build_site.py --fragment X # body-only copy (previewing)

The page is written only when its content changes, and every timestamp on it
comes from the data (not the clock), so no-op runs produce no commit churn.
"""
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

SOCCER = {
    "id": "soccer", "dir": "soccer", "label": "Soccer 1X2",
    "tab": "Soccer 1X2", "sport": "football-data leagues · FanDuel",
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
    "picks_note": "A pick is playable when FanDuel's price is at or above "
                  "<code>min 5% EV</code>.",
    "runs": [("Status", "cancelled 2026-07-28, before the first bet"),
             ("Why", "post-Pinnacle replay: no edge over its own anchor"),
             ("Record", "research result below; no live bets were placed")],
}
WNBA = {
    "id": "wnba", "dir": "wnba", "label": "WNBA props",
    "tab": "WNBA props", "sport": "player props · FanDuel",
    "what": "Points, rebounds, assists, threes and combos, priced from "
            "scratch by the v3 talent model (Kalman player states + news "
            "minutes overrides — no market inputs). Re-opened 2026-07-31; "
            "bet only FanDuel coherent quotes at claimed EV > 10%.",
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
    "runs": [("Routines", "news-watch hourly (overrides+picks+notify); "
                          "edge-watch 7x daily (close archiver)"),
             ("Status", "LIVE — v3 from-scratch talent model, re-opened "
                        "2026-07-31 (PROGRESS.md)"),
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
        b["_ev"] = num(b, "ev_claimed")  # model's claim at the price taken
        b["_status"] = txt(b, "status") or "open"
    settled = [b for b in bets if b["_status"] == "settled"]
    graded = [b for b in bets if b["_status"] in ("settled", "push", "void")]
    open_bets = [b for b in bets if b["_status"] == "open"]
    clvs = [b["_clv"] for b in graded if b["_clv"] is not None]
    evs = [b["_ev"] for b in graded if b["_ev"] is not None]

    staked = sum(b["_stake"] for b in settled)
    pnl = sum(b["_pnl"] or 0.0 for b in settled)
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    t_stat = None
    if len(clvs) >= 2:
        var = sum((c - mean_clv) ** 2 for c in clvs) / (len(clvs) - 1)
        se = math.sqrt(var / len(clvs))
        t_stat = mean_clv / se if se else None

    curve, running = [], bank["start"]
    for b in sorted(graded, key=lambda r: (txt(r, "match_date"), txt(r, "key"))):
        running += b["_pnl"] or 0.0
        curve.append((txt(b, "match_date"), round(running, 2)))

    return {
        "cfg": cfg, "bank": bank, "meta": meta, "bets": bets,
        "settled": settled, "graded": graded, "open": open_bets,
        # Split the sheet: rows whose game has already tipped are the record
        # of what the model priced, not something anyone can still bet.
        "picks": [p for p in picks_all if not pick_expired(p, as_of)],
        "picks_expired": [p for p in picks_all if pick_expired(p, as_of)],
        "as_of": as_of,
        "wins": sum(1 for b in settled if txt(b, "result") == "won"),
        "pushes": sum(1 for b in bets if b["_status"] == "push"),
        "voids": sum(1 for b in bets if b["_status"] == "void"),
        "staked": staked, "pnl": pnl,
        "roi": pnl / staked if staked else None,
        "mean_clv": mean_clv, "n_clv": len(clvs), "t_stat": t_stat,
        "exp_pnl": sum(b["_stake"] * b["_clv"] for b in graded
                       if b["_clv"] is not None),
        # What the model itself claimed the same graded bets were worth, at
        # the prices actually taken. Same population as exp_pnl so the two
        # tiles are read side by side: model's claim vs market's verdict.
        "model_exp_pnl": sum(b["_stake"] * b["_ev"] for b in graded
                             if b["_ev"] is not None),
        "mean_ev": sum(evs) / len(evs) if evs else None,
        "n_ev": len(evs),
        "curve": curve,
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


def table(cols, rows, cls="", aligns=None):
    aligns = aligns or ["l"] * len(cols)
    head = "".join(f'<th class="a-{a}">{c}</th>' for c, a in zip(cols, aligns))
    body = []
    for r in rows:
        cells = "".join(f'<td class="a-{a}">{c}</td>' for c, a in zip(r, aligns))
        body.append(f"<tr>{cells}</tr>")
    return (f'<div class="scroll"><table class="{cls}"><thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(body)}</tbody></table></div>')


# --------------------------------------------------------------- charts ----


def area_chart(pts, cid, ylabel, w=760, h=260, mark_dd=False):
    """Single-series area+line over dates. pts = [(date, value)]."""
    if len(pts) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 46, 58, 18, 26
    xs = [p[0].toordinal() for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08

    def px(x):
        return pad_l + (x - x0) / max(x1 - x0, 1) * (w - pad_l - pad_r)

    def py(y):
        return pad_t + (hi - y) / (hi - lo) * (h - pad_t - pad_b)

    step = 10 ** math.floor(math.log10(max(abs(hi), abs(lo), 1)))
    ticks, t = [], math.ceil(lo / step) * step
    while t <= hi and len(ticks) < 8:
        ticks.append(t)
        t += step
    grid = "".join(
        f'<line class="grid" x1="{pad_l}" x2="{w - pad_r}" '
        f'y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        f'<text class="tick a-r" x="{pad_l - 8}" y="{py(v) + 4:.1f}">'
        f'{v:,.0f}</text>' for v in ticks)
    if lo < 0 < hi:
        grid += (f'<line class="zero" x1="{pad_l}" x2="{w - pad_r}" '
                 f'y1="{py(0):.1f}" y2="{py(0):.1f}"/>')

    years = sorted({p[0].year for p in pts})
    xlab = "".join(
        f'<text class="tick a-m" x="{px(dt.date(y, 1, 1).toordinal()):.1f}" '
        f'y="{h - 6}">{y}</text>'
        for y in years if x0 <= dt.date(y, 1, 1).toordinal() <= x1)

    line = " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}"
                    for i, (x, y) in enumerate(zip(xs, ys)))
    base = py(max(lo, min(hi, 0.0)))
    area = f"{line} L{px(xs[-1]):.1f},{base:.1f} L{px(xs[0]):.1f},{base:.1f} Z"

    ann = ""
    if mark_dd:
        _, i, depth = max_drawdown(pts)
        ann = (f'<circle class="dot-dd" cx="{px(xs[i]):.1f}" '
               f'cy="{py(ys[i]):.1f}" r="4"/>'
               f'<text class="ann" x="{px(xs[i]):.1f}" '
               f'y="{py(ys[i]) + 22:.1f}" text-anchor="middle">'
               f'deepest drawdown {MINUS}{abs(depth):,.0f}u</text>')

    end = (f'<circle class="dot-end" cx="{px(xs[-1]):.1f}" '
           f'cy="{py(ys[-1]):.1f}" r="4.5"/>'
           f'<text class="end-label" x="{px(xs[-1]) + 10:.1f}" '
           f'y="{py(ys[-1]) + 4:.1f}">{ys[-1]:+,.0f}u</text>')

    payload = json.dumps({"x": [px(x) for x in xs], "y": [py(v) for v in ys],
                          "d": [p[0].isoformat() for p in pts],
                          "v": ys, "unit": ylabel,
                          "top": pad_t, "bot": h - pad_b})
    return (
        f'<figure class="chart" data-chart="{cid}">'
        f'<svg viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="none" '
        f'aria-label="{esc(ylabel)} over time, ending {ys[-1]:+,.0f}">'
        f'{grid}{xlab}<path class="area" d="{area}"/>'
        f'<path class="line" d="{line}"/>{ann}{end}'
        f'<g class="cross" hidden><line class="cross-l" y1="{pad_t}" '
        f'y2="{h - pad_b}"/><circle class="cross-d" r="4.5"/></g>'
        f'<rect class="hit" x="{pad_l}" y="{pad_t}" width="{w - pad_l - pad_r}" '
        f'height="{h - pad_t - pad_b}"/></svg>'
        f'<div class="tip" hidden></div>'
        f'<script type="application/json">{payload}</script></figure>')


def spark(pts, start, w=280, h=56):
    """Tiny bankroll trace for a market card."""
    if len(pts) < 2:
        return ""
    ys = [p[1] for p in pts] + [start]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.15, hi + span * 0.15
    n = len(pts)

    def px(i):
        return 2 + i / (n - 1) * (w - 4)

    def py(v):
        return 4 + (hi - v) / (hi - lo) * (h - 8)

    line = " ".join(f"{'M' if i == 0 else 'L'}{px(i):.1f},{py(v):.1f}"
                    for i, (_, v) in enumerate(pts))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
            f'aria-hidden="true"><line class="grid" x1="0" x2="{w}" '
            f'y1="{py(start):.1f}" y2="{py(start):.1f}"/>'
            f'<path class="area" d="{line} L{px(n - 1):.1f},{h} L2,{h} Z"/>'
            f'<path class="line" d="{line}"/>'
            f'<circle class="dot-end" cx="{px(n - 1):.1f}" '
            f'cy="{py(pts[-1][1]):.1f}" r="4"/></svg>')


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


def bet_rows(m):
    """(columns, rows, aligns) for a market's bet log - schemas differ."""
    cfg = m["cfg"]
    rows = sorted(m["bets"], key=lambda b: txt(b, "match_date"), reverse=True)
    style = cfg["odds_style"]
    if cfg["id"] == "soccer":
        cols = ["date", "match", "pick", "odds", "stake", "result", "P&L", "CLV"]
        aligns = ["l", "l", "l", "r", "r", "l", "r", "r"]
        out = []
        for b in rows[:200]:
            side = {"H": "home", "D": "draw", "A": "away"}.get(txt(b, "side"),
                                                               txt(b, "side"))
            out.append([
                date_short(txt(b, "match_date")),
                f'<span class="muted mono">{esc(txt(b, "div"))}</span> '
                f'{esc(txt(b, "home"))} v {esc(txt(b, "away"))}',
                f'<span class="chip">{side}</span>',
                f'<span class="mono">{odds(num(b, "odds_taken"), style)}</span>',
                money(b["_stake"]), result_chip(b),
                pnl_cell(b["_pnl"]), clv_cell(b["_clv"])])
        return cols, out, aligns

    cols = ["date", "player", "market", "pick", "odds", "stake", "actual",
            "result", "P&L", "CLV"]
    aligns = ["l", "l", "l", "l", "r", "r", "r", "l", "r", "r"]
    out = []
    for b in rows[:200]:
        actual = num(b, "actual")
        out.append([
            date_short(txt(b, "match_date")),
            esc(txt(b, "player")),
            f'<span class="muted">{esc(txt(b, "market"))}</span>',
            f'<span class="chip">{esc(txt(b, "side"))} '
            f'{num(b, "line"):g}</span>' if num(b, "line") is not None
            else f'<span class="chip">{esc(txt(b, "side"))}</span>',
            f'<span class="mono">{odds(num(b, "odds_taken"), style)}</span>',
            money(b["_stake"]),
            f"{actual:g}" if actual is not None else "—",
            result_chip(b), pnl_cell(b["_pnl"]), clv_cell(b["_clv"])])
    return cols, out, aligns


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


def picks_block(m):
    cfg, picks = m["cfg"], m["picks"]
    stale = m.get("picks_expired") or []
    if not picks:
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
    if cfg.get("paused"):
        # Kept as the record of what the paused model last produced. Not a
        # sheet anyone should bet: nothing is refreshing these prices.
        head = (f'<p class="note"><strong>Not actionable — the experiment is '
                f'paused.</strong> These {len(picks)} rows are the last thing '
                f'the model priced before it was stopped, left up as the '
                f'record. Nothing is refreshing them and no stake should be '
                f'taken from them.</p>')
    else:
        head = (f'<p class="note">{len(picks)} priced, '
                f'<strong>{len(strong)} strong</strong>. '
                f'{cfg["picks_note"]}'
                + (f' {len(stale)} further row(s) on the sheet are for games '
                   f'already played and are not shown.' if stale else "")
                + '</p>')
    if cfg["id"] == "soccer":
        cols = ["date", "match", "pick", "model p", "best price",
                "min 5% EV", "stake"]
        aligns = ["l", "l", "l", "r", "r", "r", "r"]
        rows = []
        for p in picks[:25]:
            side = {"H": "home", "D": "draw", "A": "away"}.get(
                txt(p, "side"), txt(p, "side"))
            rows.append([
                date_short(txt(p, "date")),
                f'<span class="muted mono">{esc(txt(p, "div"))}</span> '
                f'{esc(txt(p, "home"))} v {esc(txt(p, "away"))}',
                f'<span class="chip">{side}</span>'
                + (' <span class="chip chip-strong">strong</span>'
                   if str(p.get("strong", "")).lower() in ("true", "1") else ""),
                f'{(num(p, "model_p") or 0) * 100:.1f}%',
                f'<span class="mono">{odds(num(p, "max_odds"), "decimal")}</span>',
                f'<span class="mono">{odds(num(p, "min_odds_5pct"), "decimal")}</span>',
                money(num(p, "stake_at_min5") or 0)])
        return head + table(cols, rows, "tbl", aligns)

    cols = ["game", "player", "pick", "price", "model p", "EV", "stake"]
    aligns = ["l", "l", "l", "r", "r", "r", "r"]
    rows = []
    for p in picks[:25]:
        ev = num(p, "ev")
        rows.append([
            f'<span class="muted mono">{esc(txt(p, "game"))}</span> '
            f'{date_short(txt(p, "date"))}',
            esc(txt(p, "player")),
            f'<span class="chip">{esc(txt(p, "market"))} {esc(txt(p, "side"))} '
            f'{num(p, "fd_line"):g}</span>'
            + (' <span class="chip chip-strong">strong</span>'
               if str(p.get("strong", "")).lower() in ("true", "1") else ""),
            f'<span class="mono">{odds(num(p, "fd_cost"), "american")}</span>',
            f'{(num(p, "model_p") or 0) * 100:.1f}%',
            f'<span class="val-good mono">{signed_pct(ev)}</span>'
            if ev is not None else "—",
            money(num(p, "stake") or 0)])
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


def market_card(m):
    cfg, bank = m["cfg"], m["bank"]
    delta = bank["current"] - bank["start"]
    body = spark(m["curve"], bank["start"]) if len(m["curve"]) >= 2 else (
        f'<div class="card-empty">{esc(cfg["idle"]) if not m["open"] else "First settlement pending"}</div>')
    clv = (signed_pct(m["mean_clv"]) if m["mean_clv"] is not None
           else "—")
    rec = (f'{len(m["settled"])} settled · {m["wins"]}W'
           f'{MINUS}{len(m["settled"]) - m["wins"]}L' if m["settled"]
           else f'{len(m["open"])} open · 0 settled')
    dtone = "good" if delta > 0 else ("bad" if delta < 0 else "flat")
    return (
        f'<a class="mcard" href="#{cfg["id"]}" data-goto="{cfg["id"]}">'
        f'<div class="mcard-top"><h3>{esc(cfg["label"])}</h3>{status_pill(m)}</div>'
        f'<div class="mcard-figure">{money(bank["current"])}'
        f'<span class="mcard-delta val-{dtone}">{signed_money(delta)}</span></div>'
        f'<div class="mcard-meta">{rec} · mean CLV {clv}</div>'
        f'{body}<div class="mcard-go">Full record →</div></a>')


def market_panel(m):
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
        "Mean CLV",
        signed_pct(m["mean_clv"]) if m["mean_clv"] is not None else "—",
        f'n = {m["n_clv"]}' + (f' · t = {m["t_stat"]:.2f}'
                               if m["t_stat"] is not None else ""),
        "good" if (m["mean_clv"] or 0) > 0 else
        ("bad" if m["mean_clv"] is not None and m["mean_clv"] < 0 else "")))
    tiles.append(tile(
        "P&L", signed_money(m["pnl"]) if m["settled"] else "—",
        f'{signed_pct(m["roi"])} ROI on {money(m["staked"])} staked'
        if m["roi"] is not None else "nothing staked yet",
        "good" if m["pnl"] > 0 else ("bad" if m["pnl"] < 0 else "")))
    if m["n_ev"]:
        tiles.append(tile(
            "Model-expected P&L", signed_money(m["model_exp_pnl"]),
            f'what the model claimed these bets were worth '
            f'({signed_pct(m["mean_ev"])} per bet)'))
    tiles.append(tile(
        "CLV-expected P&L",
        signed_money(m["exp_pnl"]) if m["n_clv"] else "—",
        "what the closing line says you should have won"))
    if m["pushes"] or m["voids"]:
        tiles.append(tile("Push / void", f'{m["pushes"]} / {m["voids"]}',
                          "stake returned"))

    cols, rows, aligns = bet_rows(m)
    log = (table(cols, rows, "tbl", aligns) if rows else
           empty("No bets logged yet",
                 "Fills are reported conversationally and logged to "
                 "live/bets.csv."))
    runs = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>"
                   for k, v in cfg["runs"])
    if cfg.get("cancelled"):
        clv_block = (f'<div class="block"><h3>Why there is no record</h3>'
                     f'<p class="note">{esc(cfg["cancelled"])} Full numbers '
                     f'in the Evidence tab and the subproject README.</p>'
                     f'</div>')
    else:
        clv_block = f"""
  <div class="block">
    <h3>Is it beating the close?</h3>
    <p class="note">CLV is the scoreboard: one season of profit is noise, one
      season of closing-line value is decisive. The band is what the backtest
      says these bets should average.</p>
    {clv_band(cfg, m['mean_clv'], m['n_clv'])}
  </div>"""

    return f"""
<section class="panel" id="panel-{cfg['id']}" role="tabpanel"
         aria-labelledby="tab-{cfg['id']}" hidden>
  <div class="panel-head">
    <div>
      <h2>{esc(cfg['label'])} <span class="head-pill">{status_pill(m)}</span></h2>
      <p class="lede">{esc(cfg['what'])}</p>
      {f'<p class="note">{esc(cfg["paused"])}</p>' if cfg.get("paused") else ''}
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
{clv_block}
  <div class="block">
    <h3>On the sheet now</h3>
    {picks_block(m)}
  </div>

  <div class="block">
    <h3>Bet log</h3>
    {log}
  </div>

  <p class="foot">Protocol:
    <a href="{REPO}/blob/main/{cfg['protocol']}">{esc(cfg['protocol'])}</a>
    · method and backtest:
    <a href="{REPO}/blob/main/{cfg['readme']}">{esc(cfg['readme'])}</a>
    · raw record:
    <a href="{REPO}/blob/main/{cfg['dir']}/RESULTS.md">RESULTS.md</a></p>
</section>"""


def live_panel(markets):
    start = sum(m["bank"]["start"] for m in markets)
    cur = sum(m["bank"]["current"] for m in markets)
    delta = cur - start
    dtone = "good" if delta > 0 else ("bad" if delta < 0 else "flat")
    n_open = sum(len(m["open"]) for m in markets)
    n_settled = sum(len(m["settled"]) for m in markets)
    staked = sum(m["staked"] for m in markets)
    clvs = [b["_clv"] for m in markets for b in m["graded"]
            if b["_clv"] is not None]
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    live_now = [m["cfg"]["label"] for m in markets if m["open"] or m["picks"]]

    tiles = [
        tile("Open positions", f"{n_open}",
             f'{money(sum(b["_stake"] for m in markets for b in m["open"]))} at risk'
             if n_open else "nothing live"),
        tile("Settled bets", f"{n_settled}",
             f"{money(staked)} staked" if staked else "none yet"),
        tile("Mean CLV", signed_pct(mean_clv) if mean_clv is not None else "—",
             f"n = {len(clvs)}" if clvs else "the scoreboard that matters",
             "good" if (mean_clv or 0) > 0 else ""),
        tile("Markets live", f"{len(live_now)} of {len(markets)}",
             ", ".join(live_now) if live_now else "nothing on the board"),
    ]
    return f"""
<section class="panel" id="panel-live" role="tabpanel"
         aria-labelledby="tab-live">
  <div class="panel-head">
    <div>
      <h2>The live experiment</h2>
      <p class="lede">Real money on FanDuel, quarter-Kelly stakes.
        <strong>WNBA props is running.</strong> The opener-anchored model was
        retired on 2026-07-31 — it inverted its opinion out of the price
        rather than pricing games itself — and live betting re-opened the same
        evening on a from-scratch talent model, at a claimed-EV &gt; 10%
        trigger. The soccer experiment was cancelled before its first bet when
        the post-Pinnacle replay showed no edge; its card stays as the
        record.</p>
    </div>
    <div class="hero">
      <div class="hero-label">Combined bankroll</div>
      <div class="hero-fig">{money(cur)}</div>
      <div class="hero-sub val-{dtone}">{signed_money(delta)} on
        {money(start, 0)} start</div>
    </div>
  </div>
  <div class="tiles">{''.join(tiles)}</div>
  <div class="cards">{''.join(market_card(m) for m in markets)}</div>

  <div class="block explain">
    <h3>How to read this</h3>
    <div class="explain-grid">
      <div><h4>CLV, not profit</h4><p>Closing-line value is
        <code>p_close × odds_taken − 1</code>: did the price beat where the
        market ended up? It converges within a season. ROI needs several.</p></div>
      <div><h4>Bet the opener</h4><p>The edge is the stale opening price, not
        the model knowing more than the market. Once a line moves, the pick is
        dead — no chasing.</p></div>
      <div><h4>Losing can still be a pass</h4><p>Negative P&amp;L with clearly
        positive CLV means the model works and variance didn't cooperate.
        Profit with negative CLV is just luck.</p></div>
    </div>
  </div>
</section>"""


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

    def sim(spec):
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

    curve = backtest_curve()
    chart = area_chart(curve, "backtest", "cumulative units", mark_dd=True)
    chart_block = (f"""
  <div class="block">
    <h3>What the soccer edge looked like out-of-sample</h3>
    <p class="note">Cumulative profit of the backtested strategy — flat 1-unit
      bets at the best early price whenever the model saw &gt;2% EV, over 31,192
      bets and nine walk-forward seasons. Never retrained on the future.</p>
    {chart}
  </div>""" if chart else "")

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
  {chart_block}

  <div class="block two-up">
    <div>
      <h3>Soccer 1X2 betting simulation</h3>
      {sim(SOCCER_SIM)}
    </div>
    <div>
      <h3>WNBA props betting simulation</h3>
      {sim(WNBA_SIM)}
    </div>
  </div>

  <p class="foot">Full write-ups, caveats and reproduction steps:
    <a href="{REPO}/tree/main/soccer">soccer/</a> ·
    <a href="{REPO}/tree/main/wnba">wnba/</a> ·
    <a href="{REPO}/tree/main/cricket">cricket/</a> ·
    <a href="{REPO}/tree/main/nba">nba/</a>.
    Simulations model prices, not frictions — limits, restrictions and
    palpable-error voids are real and unmodelled.</p>
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
.brand h1{font-size:26px;letter-spacing:-.02em}
.brand p{color:var(--ink2);max-width:56ch;margin-top:6px}
.stamp{color:var(--muted);font-size:12.5px;text-align:right;
  display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.stamp .mono{font-size:12px}
.repo-link{text-decoration:none;border:1px solid var(--base);border-radius:999px;
  padding:5px 12px;color:var(--ink2);font-size:12.5px;white-space:nowrap}
.repo-link:hover{border-color:var(--ink2);color:var(--ink)}

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

/* market cards */
.cards{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  margin-top:14px}
.mcard{display:block;text-decoration:none;background:var(--surface);
  border:1px solid var(--rule);border-radius:12px;padding:16px 18px 14px;
  box-shadow:var(--shadow);transition:border-color .15s,transform .15s}
.mcard:hover{border-color:var(--base);transform:translateY(-1px)}
.mcard:focus-visible{outline:2px solid var(--model);outline-offset:2px}
.mcard-top{display:flex;align-items:center;justify-content:space-between;gap:10px}
.mcard-top h3{font-size:15px}
.mcard-figure{font-size:32px;font-weight:600;letter-spacing:-.02em;margin-top:10px;
  display:flex;align-items:baseline;gap:10px}
.mcard-delta{font-size:13px;font-weight:400;color:var(--ink2)}
.mcard-meta{font-size:12.5px;color:var(--ink2)}
.mcard .spark{width:100%;height:56px;margin-top:12px;display:block}
.card-empty{margin-top:12px;height:56px;display:flex;align-items:center;
  justify-content:center;font-size:12.5px;color:var(--muted);
  border:1px dashed var(--grid);border-radius:8px;text-align:center;padding:0 10px}
.mcard-go{font-size:12.5px;color:var(--model);margin-top:12px}

/* blocks */
.block{margin-top:34px}
.block>h3{font-size:16px;letter-spacing:-.01em}
.block>.note,.block>p.note{margin-top:6px}
.note{font-size:13px;color:var(--ink2);max-width:70ch}
.note+.scroll,.bandwrap,.tbl,.empty{margin-top:12px}
.foot{margin-top:32px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted);max-width:78ch}
.foot a{color:var(--ink2)}
.two-up{display:grid;gap:28px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.two-up h3{font-size:15px}
.two-up .tbl{font-size:12.5px}
.two-up .tbl th,.two-up .tbl td{padding:8px 10px}

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
.band-note{font-size:12.5px;color:var(--ink2);margin-top:8px}

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
.chart .area{fill:var(--model);opacity:.1;stroke:none}
.chart .line{fill:none;stroke:var(--model);stroke-width:2;stroke-linejoin:round;
  stroke-linecap:round}
.chart .dot-end{fill:var(--model);stroke:var(--surface);stroke-width:2}
.chart .dot-dd{fill:var(--market);stroke:var(--surface);stroke-width:2}
.chart .ann{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.chart .end-label{fill:var(--ink);font-size:13px;font-weight:600;
  font-family:var(--sans)}
.chart .hit{fill:transparent}
.chart .cross[hidden]{display:none}
.chart .cross-l{stroke:var(--base);stroke-width:1}
.chart .cross-d{fill:var(--model);stroke:var(--surface);stroke-width:2}
.tip{position:absolute;pointer-events:none;background:var(--surface);
  border:1px solid var(--base);border-radius:8px;padding:7px 10px;font-size:12.5px;
  box-shadow:var(--shadow);white-space:nowrap;transform:translate(-50%,-130%);
  font-variant-numeric:tabular-nums}
.tip b{font-size:13.5px}
.spark .grid{stroke:var(--grid);stroke-width:1}
.spark .area{fill:var(--model);opacity:.1}
.spark .line{fill:none;stroke:var(--model);stroke-width:2}
.spark .dot-end{fill:var(--model);stroke:var(--surface);stroke-width:2}

/* explainer */
.explain-grid{display:grid;gap:18px;margin-top:12px;
  grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.explain-grid h4{font-size:13.5px}
.explain-grid p{font-size:13px;color:var(--ink2);margin-top:4px}

@media (max-width:640px){
  .hero{text-align:left}
  .hero-fig{font-size:38px}
  .ledger-row{grid-template-columns:1fr auto}
  .ledger-gates{grid-column:1/-1}
  .tabs{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
  .tabs::-webkit-scrollbar{display:none}
  .tab{white-space:nowrap}
}
@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important}
}
"""

JS = """
(function(){
  var tabs=[].slice.call(document.querySelectorAll('[role="tab"]'));
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
  document.querySelectorAll('[data-goto]').forEach(function(a){
    a.addEventListener('click',function(e){e.preventDefault();
      show(a.dataset.goto,true);window.scrollTo({top:0,behavior:'smooth'});});
  });
  var ids=tabs.map(function(t){return t.dataset.panel;});
  show(ids.indexOf(location.hash.slice(1))>-1?location.hash.slice(1):ids[0],false);
  window.addEventListener('hashchange',function(){
    if(ids.indexOf(location.hash.slice(1))>-1)show(location.hash.slice(1),false);
  });

  document.querySelectorAll('.chart').forEach(function(fig){
    var svg=fig.querySelector('svg'),tip=fig.querySelector('.tip'),
        cross=fig.querySelector('.cross'),hit=fig.querySelector('.hit'),
        d=JSON.parse(fig.querySelector('script[type="application/json"]').textContent),
        vb=svg.viewBox.baseVal;
    function at(ev){
      var r=svg.getBoundingClientRect(),sx=(ev.clientX-r.left)/r.width*vb.width,i=0,
          best=1e9;
      for(var k=0;k<d.x.length;k++){var dx=Math.abs(d.x[k]-sx);
        if(dx<best){best=dx;i=k;}}
      cross.removeAttribute('hidden');   // SVG has no .hidden property
      cross.querySelector('.cross-l').setAttribute('x1',d.x[i]);
      cross.querySelector('.cross-l').setAttribute('x2',d.x[i]);
      cross.querySelector('.cross-d').setAttribute('cx',d.x[i]);
      cross.querySelector('.cross-d').setAttribute('cy',d.y[i]);
      tip.hidden=false;
      tip.innerHTML='<b>'+(d.v[i]>=0?'+':'\\u2212')+
        Math.abs(d.v[i]).toLocaleString(undefined,{maximumFractionDigits:0})+
        'u</b><br>'+d.d[i];
      tip.style.left=(d.x[i]/vb.width*100)+'%';
      tip.style.top=(d.y[i]/vb.height*r.height)+'px';
    }
    hit.addEventListener('pointermove',at);
    hit.addEventListener('pointerdown',at);
    fig.addEventListener('pointerleave',function(){
      cross.setAttribute('hidden','');tip.hidden=true;});
  });
})();
"""


def render(markets, fragment=False):
    updated = stamp(markets)
    tabs = [("live", "Live"), ("soccer", SOCCER["tab"]),
            ("wnba", WNBA["tab"]), ("evidence", "Evidence")]
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
      <p>Soft sportsbooks' opening lines are inefficient. Four markets tested,
        two beaten in research. Soccer's live experiment was cancelled when
        its regime died. WNBA props is betting again since 2026-07-31: the
        model that anchored on the opening price is retired, and a
        from-scratch talent model prices the games instead.</p>
    </div>
    <div class="stamp">
      <a class="repo-link" href="{REPO}">Source on GitHub ↗</a>
      <span>data updated <span class="mono">{esc(updated) or "—"}</span></span>
    </div>
    <nav class="tabs" role="tablist" aria-label="Results views">{tab_html}</nav>
  </div>
</header>
<main class="wrap">
{live_panel(markets)}
{market_panel(by_id["soccer"])}
{market_panel(by_id["wnba"])}
{evidence_panel()}
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
<meta name="description" content="CLV, bankroll and bet log for two
 opening-line betting experiments — both now stopped — plus the out-of-sample
 evidence behind them.">
<meta property="og:title" content="Beating the opener — scoreboard">
<meta property="og:description" content="Two opening-line betting experiments
 scored on closing-line value, both now stopped, and the research behind them.">
<!-- Generated by site/build_site.py - do not edit by hand. -->
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
    if "--fragment" in sys.argv:
        path = sys.argv[sys.argv.index("--fragment") + 1]
        write_if_changed(path, render(markets, fragment=True))
        print(f"fragment -> {path}")
        return
    copy_pages()
    changed = write_if_changed(OUT, render(markets))
    print(f"docs/index.html {'updated' if changed else 'unchanged'}")


if __name__ == "__main__":
    main()
