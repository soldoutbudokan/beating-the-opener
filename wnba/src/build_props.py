"""Flatten the BettingPros archive into analysis tables.

Outputs:
  data/props.pkl  one row per (event, market, player, book): main O/U line + prices
                  at last update (close) plus the prop's opening line
  data/games.pkl  game-level odds (moneyline/total/spread) in the same long format
  data/events.pkl event metadata (date, teams, scores)

BP book ids: 0=consensus, 10=FanDuel, 12=DraftKings, 13=Caesars, 14=Fanatics,
19=BetMGM, 27=PartyCasino, 36=?, 37=?, 49=? (kept as-is; consensus+FD are what we use)
"""
import glob
import gzip
import json
import os

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw", "bp")

PROP_MARKETS = {
    393: "points", 397: "rebounds", 391: "assists", 390: "threes",
    396: "pra", 394: "pts_ast", 395: "pts_reb", 398: "reb_ast",
    399: "steals", 392: "blocks", 401: "turnovers", 400: "stl_blk",
}
GAME_MARKETS = {371: "moneyline", 372: "total", 373: "spread"}


def load_gz(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def parse_events():
    rows = []
    for path in sorted(glob.glob(os.path.join(RAW, "events_*.json.gz"))):
        for e in load_gz(path):
            row = {
                "event_id": e["id"], "season": e["season"],
                "season_type": e.get("season_type"),
                "scheduled": e["scheduled"], "date": e["scheduled"][:10],
                "home": e["home"], "visitor": e["visitor"],
                "status": e.get("status"),
            }
            res = (e.get("results") or {}).get("outcomes") or []
            for out in res:
                if out.get("label") == "event":
                    for s in out.get("scores", []):
                        side = "home" if s["participant"] == e["home"] else "visitor"
                        row[f"{side}_score"] = s["score"]
            rows.append(row)
    return pd.DataFrame(rows).drop_duplicates("event_id")


def main_line(book):
    """The book's main (not alt) active-most-recent line."""
    cands = [ln for ln in book.get("lines", []) if ln.get("main")]
    if not cands:
        return None
    return cands[0]


def parse_offer_file(path, market_name, rows):
    d = load_gz(path)
    for o in d.get("offers", []):
        parts = o.get("participants") or [{}]
        pl = (parts[0] or {}).get("player") or {}
        base = {
            "event_id": o["event_id"], "market": market_name,
            "player_id": o.get("player_id"),
            "player": f"{pl.get('first_name','')} {pl.get('last_name','')}".strip(),
            "team": pl.get("team"), "pos": pl.get("position"),
        }
        sides = {}
        opens = {}
        for sel in o.get("selections", []):
            side = sel.get("selection")
            if side not in ("over", "under"):
                continue
            op = sel.get("opening_line") or {}
            opens[side] = op
            for b in sel.get("books", []):
                ln = main_line(b)
                if ln is None or ln.get("line") is None:
                    continue
                sides.setdefault(b["id"], {})[side] = ln
        op_o, op_u = opens.get("over", {}), opens.get("under", {})
        for book_id, s in sides.items():
            ov, un = s.get("over"), s.get("under")
            rows.append({
                **base, "book": book_id,
                "line": (ov or un).get("line"),
                "over_cost": ov.get("cost") if ov else None,
                "under_cost": un.get("cost") if un else None,
                "line_under": un.get("line") if un else None,
                "updated": (ov or un).get("updated"),
                "is_off": bool((ov or {}).get("is_off")) or bool((un or {}).get("is_off")),
                "open_line": op_o.get("line", op_u.get("line")),
                "open_over_cost": op_o.get("cost"),
                "open_under_cost": op_u.get("cost"),
                "open_book": op_o.get("book_id", op_u.get("book_id")),
                "open_created": op_o.get("created", op_u.get("created")),
            })


def parse_game_file(path, market_name, rows):
    d = load_gz(path)
    for o in d.get("offers", []):
        for sel in o.get("selections", []):
            op = sel.get("opening_line") or {}
            label = sel.get("label") or sel.get("selection")
            participant = sel.get("participant")
            for b in sel.get("books", []):
                ln = main_line(b)
                if ln is None:
                    continue
                rows.append({
                    "event_id": o["event_id"], "market": market_name,
                    "selection": label, "participant": participant,
                    "book": b["id"], "line": ln.get("line"),
                    "cost": ln.get("cost"), "updated": ln.get("updated"),
                    "open_line": op.get("line"), "open_cost": op.get("cost"),
                    "open_book": op.get("book_id"), "open_created": op.get("created"),
                })


def main():
    events = parse_events()
    events.to_pickle(os.path.join(ROOT, "data", "events.pkl"))
    print(f"events: {len(events)} "
          f"({events['date'].min()} .. {events['date'].max()})")

    prop_rows, game_rows = [], []
    files = sorted(glob.glob(os.path.join(RAW, "offers", "*.json.gz")))
    for path in files:
        name = os.path.basename(path).replace(".json.gz", "")
        eid, mid = (int(x) for x in name.split("_"))
        if mid in PROP_MARKETS:
            parse_offer_file(path, PROP_MARKETS[mid], prop_rows)
        elif mid in GAME_MARKETS:
            parse_game_file(path, GAME_MARKETS[mid], game_rows)

    props = pd.DataFrame(prop_rows)
    games = pd.DataFrame(game_rows)
    ev_meta = events.set_index("event_id")[["season", "date", "home", "visitor"]]
    if len(props):
        props = props.join(ev_meta, on="event_id")
    if len(games):
        games = games.join(ev_meta, on="event_id")
    props.to_pickle(os.path.join(ROOT, "data", "props.pkl"))
    games.to_pickle(os.path.join(ROOT, "data", "games.pkl"))

    print(f"prop rows: {len(props)} from {len(files)} files")
    if len(props):
        print(f"  unique props: {props.groupby(['event_id','market','player_id']).ngroups}")
        print(f"  by market:\n{props[props.book==0].market.value_counts().to_string()}")
        print(f"  books: {sorted(props.book.unique())}")
        print(f"  with opening line: {props.open_line.notna().mean():.1%}")
    print(f"game odds rows: {len(games)}")


if __name__ == "__main__":
    main()
