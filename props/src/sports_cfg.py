"""Per-sport BettingPros configuration (market ids verified 2026-07-28).

Single source of truth for scrape_bp.py, build_props.py, grade_props.py.
Market ids come from /v3/markets?sport=<X> probes; the events endpoint caps
at 200 events per call, so season date ranges are walked in 7-day windows.
"""

SPORTS = {
    "MLB": {
        "prop_markets": {
            # pitcher
            285: "strikeouts", 405: "outs_recorded", 404: "hits_allowed",
            408: "walks_allowed", 290: "earned_runs",
            # batter
            287: "hits", 293: "total_bases", 403: "hrr", 299: "homeruns",
            288: "runs", 289: "rbi", 294: "stolen_bases", 295: "singles",
            291: "doubles", 292: "triples",
        },
        "game_markets": {
            122: "moneyline", 175: "total", 176: "run_line",
            279: "f5_moneyline", 281: "f5_total",
        },
        # season -> (start, end); end None = today
        "seasons": {2025: ("2025-03-15", "2025-11-05"),
                    2026: ("2026-03-20", None)},
    },
    "NBA": {
        "prop_markets": {
            156: "points", 157: "rebounds", 151: "assists", 162: "threes",
            338: "pra", 335: "pts_ast", 336: "pts_reb", 337: "reb_ast",
            160: "steals", 152: "blocks",
        },
        "game_markets": {127: "moneyline", 128: "total", 129: "spread"},
        # 2025-26 season (BP labels it season=2025)
        "seasons": {2025: ("2025-10-01", "2026-06-30")},
    },
    "NFL": {
        "prop_markets": {
            103: "passing_yards", 100: "passing_completions",
            333: "passing_attempts", 102: "passing_tds", 101: "interceptions",
            104: "receptions", 105: "receiving_yards",
            107: "rushing_yards", 106: "rushing_attempts",
            406: "rush_rec_yards",
        },
        "game_markets": {1: "moneyline", 2: "total", 3: "spread"},
        # 2025 season incl. preseason + playoffs (season_type separates them)
        "seasons": {2025: ("2025-08-01", "2026-02-15")},
    },
    "NHL": {
        "prop_markets": {
            318: "goals", 319: "points", 320: "assists", 321: "shots",
            322: "saves", 362: "blocked_shots",
        },
        "game_markets": {193: "moneyline", 194: "total", 195: "puck_line"},
        # 2024 has no archived odds - fetched only for panel warmup +
        # play-data calibration of the fp model
        "seasons": {2024: ("2024-10-01", "2025-06-30"),
                    2025: ("2025-10-01", "2026-06-30")},
    },
}
