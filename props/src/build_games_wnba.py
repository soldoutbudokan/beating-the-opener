"""Build the WNBA game-odds table into props/data (Phase 1G input).

Read-only over the wnba/ archive (flat offers dir, game mids 371 moneyline /
372 total / 373 spread) using this project's parse_game_file, joined to
wnba events metadata. Nothing under wnba/ is modified.

Output: data/games_wnba.pkl

Usage: python3 src/build_games_wnba.py
"""
import glob
import os

import pandas as pd

from build_props import parse_game_file

ROOT = os.path.join(os.path.dirname(__file__), "..")
WNBA_RAW = os.path.join(ROOT, "..", "wnba", "data", "raw", "bp", "offers")
GAME_MARKETS = {371: "moneyline", 372: "total", 373: "spread"}


def main():
    rows = []
    files = []
    for mid, name in GAME_MARKETS.items():
        fs = sorted(glob.glob(os.path.join(WNBA_RAW, f"*_{mid}.json.gz")))
        files.extend(fs)
        for path in fs:
            parse_game_file(path, name, rows)
    games = pd.DataFrame(rows)

    ev = pd.read_pickle(os.path.join(ROOT, "..", "wnba", "data", "events.pkl"))
    meta = ev.set_index("event_id")[["season", "date", "home", "visitor"]]
    games = games.join(meta, on="event_id")

    out = os.path.join(ROOT, "data", "games_wnba.pkl")
    games.to_pickle(out)
    print(f"game odds rows: {len(games)} from {len(files)} files "
          f"({games.date.min()} .. {games.date.max()})")
    print(games.market.value_counts().to_string())
    print(f"with opener: {games.open_cost.notna().mean():.1%}, "
          f"is_off at close: {games.is_off.mean():.1%}")


if __name__ == "__main__":
    main()
