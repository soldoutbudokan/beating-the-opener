"""Point-in-time feature construction.

Every feature here is computed from information available strictly BEFORE tipoff.
The whole exercise is worthless if a single feature leaks the result, so all
rolling state is updated only after a game's row has been emitted.
"""
import os
from collections import defaultdict, deque

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Arena coordinates for travel/timezone features (team abbr -> lat, lon, tz offset).
ARENAS = {
    "ATL": (33.757, -84.396, -5), "BOS": (42.366, -71.062, -5),
    "BKN": (40.683, -73.975, -5), "CHA": (35.225, -80.839, -5),
    "CHI": (41.881, -87.674, -6), "CLE": (41.496, -81.688, -5),
    "DAL": (32.790, -96.810, -6), "DEN": (39.749, -105.008, -7),
    "DET": (42.341, -83.055, -5), "GS": (37.768, -122.388, -8),
    "GSW": (37.768, -122.388, -8), "HOU": (29.751, -95.362, -6),
    "IND": (39.764, -86.156, -5), "LAC": (34.043, -118.267, -8),
    "LAL": (34.043, -118.267, -8), "MEM": (35.138, -90.051, -6),
    "MIA": (25.781, -80.187, -5), "MIL": (43.045, -87.917, -6),
    "MIN": (44.979, -93.276, -6), "NO": (29.949, -90.082, -6),
    "NOP": (29.949, -90.082, -6), "NY": (40.751, -73.994, -5),
    "NYK": (40.751, -73.994, -5), "OKC": (35.463, -97.515, -6),
    "ORL": (28.539, -81.384, -5), "PHI": (39.901, -75.172, -5),
    "PHX": (33.446, -112.071, -7), "POR": (45.532, -122.667, -8),
    "SAC": (38.580, -121.500, -8), "SA": (29.427, -98.437, -6),
    "SAS": (29.427, -98.437, -6), "TOR": (43.643, -79.379, -5),
    "UTA": (40.768, -111.901, -7), "UTAH": (40.768, -111.901, -7),
    "WSH": (38.898, -77.021, -5), "WAS": (38.898, -77.021, -5),
}


def haversine(a, b):
    if a is None or b is None:
        return 0.0
    lat1, lon1 = np.radians(a[0]), np.radians(a[1])
    lat2, lon2 = np.radians(b[0]), np.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * 6371 * np.arcsin(np.sqrt(h)))


class Elo:
    """Margin-aware Elo with season carryover, à la FiveThirtyEight."""

    def __init__(self, k=20.0, hfa=100.0, carry=0.75, base=1500.0):
        self.k, self.hfa, self.carry, self.base = k, hfa, carry, base
        self.r = defaultdict(lambda: base)
        self.last_season = {}

    def new_season(self, team, season):
        if self.last_season.get(team) != season:
            if team in self.r:
                self.r[team] = self.carry * self.r[team] + (1 - self.carry) * self.base
            self.last_season[team] = season

    def expected(self, home, away, hfa_adj=0.0):
        diff = (self.r[home] + self.hfa + hfa_adj) - self.r[away]
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def update(self, home, away, margin, hfa_adj=0.0):
        exp_h = self.expected(home, away, hfa_adj)
        s_h = 1.0 if margin > 0 else 0.0
        # Margin multiplier damps blowouts and corrects autocorrelation.
        elo_diff = (self.r[home] + self.hfa + hfa_adj) - self.r[away]
        winner_diff = elo_diff if margin > 0 else -elo_diff
        mult = ((abs(margin) + 3.0) ** 0.8) / (7.5 + 0.006 * winner_diff)
        delta = self.k * mult * (s_h - exp_h)
        self.r[home] += delta
        self.r[away] -= delta


def build(df, elo_k=20.0, elo_hfa=100.0, ewma_alpha=0.05):
    """Return a feature frame; df must be sorted by tipoff time."""
    df = df.sort_values("date_utc").reset_index(drop=True)

    elo = Elo(k=elo_k, hfa=elo_hfa)
    # Rolling per-team state
    last_game_date = {}
    games_played = defaultdict(int)
    recent_dates = defaultdict(lambda: deque(maxlen=12))
    ewma_margin = {}
    ewma_pts_for = {}
    ewma_pts_against = {}
    last_venue = {}
    win_streak = defaultdict(int)
    last10 = defaultdict(lambda: deque(maxlen=10))
    season_of = {}
    season_wins = defaultdict(int)
    season_games = defaultdict(int)
    road_trip = defaultdict(int)

    rows = []
    for r in df.itertuples(index=False):
        h, a = r.home_abbr, r.away_abbr
        season, gdate = r.season_year, r.game_date

        for t in (h, a):
            elo.new_season(t, season)
            if season_of.get(t) != season:
                season_of[t] = season
                season_wins[t] = 0
                season_games[t] = 0
                road_trip[t] = 0

        def rest_feats(t, is_home):
            lg = last_game_date.get(t)
            days = (gdate - lg).days if lg is not None else 7
            days = min(days, 10)
            recent = recent_dates[t]
            g7 = sum(1 for d in recent if 0 <= (gdate - d).days <= 7)
            g10 = sum(1 for d in recent if 0 <= (gdate - d).days <= 10)
            b2b = 1 if days <= 1 else 0
            three_in_four = 1 if sum(1 for d in recent if 0 <= (gdate - d).days <= 3) >= 2 else 0
            four_in_six = 1 if sum(1 for d in recent if 0 <= (gdate - d).days <= 5) >= 3 else 0
            prev_venue = last_venue.get(t)
            cur_venue = h
            dist = haversine(ARENAS.get(prev_venue), ARENAS.get(cur_venue)) if prev_venue else 0.0
            tz_prev = ARENAS.get(prev_venue, (0, 0, -5))[2] if prev_venue else -5
            tz_cur = ARENAS.get(cur_venue, (0, 0, -5))[2]
            return {
                "rest": days, "b2b": b2b, "g7": g7, "g10": g10,
                "3in4": three_in_four, "4in6": four_in_six,
                "travel_km": dist, "tz_shift": tz_cur - tz_prev,
                "road_trip": road_trip[t] if not is_home else 0,
            }

        hf, af = rest_feats(h, True), rest_feats(a, False)

        row = {
            "game_id": r.game_id,
            "date_utc": r.date_utc,
            "game_date": gdate,
            "season_year": season,
            "season_type": r.season_type,
            "home_abbr": h, "away_abbr": a,
            "home_win": r.home_win,
            "margin": r.margin,
            "elo_home": elo.r[h], "elo_away": elo.r[a],
            "elo_diff": elo.r[h] - elo.r[a],
            "elo_prob": elo.expected(h, a),
            "home_gp": games_played[h], "away_gp": games_played[a],
            "ewma_margin_home": ewma_margin.get(h, 0.0),
            "ewma_margin_away": ewma_margin.get(a, 0.0),
            "ewma_margin_diff": ewma_margin.get(h, 0.0) - ewma_margin.get(a, 0.0),
            "ewma_pf_home": ewma_pts_for.get(h, 110.0),
            "ewma_pa_home": ewma_pts_against.get(h, 110.0),
            "ewma_pf_away": ewma_pts_for.get(a, 110.0),
            "ewma_pa_away": ewma_pts_against.get(a, 110.0),
            "win_streak_home": win_streak[h], "win_streak_away": win_streak[a],
            "last10_home": np.mean(last10[h]) if last10[h] else 0.5,
            "last10_away": np.mean(last10[a]) if last10[a] else 0.5,
            "winpct_home": (season_wins[h] / season_games[h]) if season_games[h] else 0.5,
            "winpct_away": (season_wins[a] / season_games[a]) if season_games[a] else 0.5,
        }
        for k, v in hf.items():
            row[f"home_{k}"] = v
        for k, v in af.items():
            row[f"away_{k}"] = v
        row["rest_diff"] = hf["rest"] - af["rest"]
        row["b2b_diff"] = hf["b2b"] - af["b2b"]
        row["g7_diff"] = hf["g7"] - af["g7"]
        row["travel_diff"] = af["travel_km"] - hf["travel_km"]
        row["fatigue_diff"] = (af["b2b"] + af["3in4"] + af["4in6"]) - (
            hf["b2b"] + hf["3in4"] + hf["4in6"]
        )
        rows.append(row)

        # ---- state updates happen only AFTER the row is emitted ----
        margin = r.margin
        elo.update(h, a, margin)
        for t, pf, pa, won in ((h, r.home_score, r.away_score, margin > 0),
                               (a, r.away_score, r.home_score, margin < 0)):
            m = pf - pa
            ewma_margin[t] = (
                m if t not in ewma_margin
                else (1 - ewma_alpha) * ewma_margin[t] + ewma_alpha * m
            )
            ewma_pts_for[t] = (
                pf if t not in ewma_pts_for
                else (1 - ewma_alpha) * ewma_pts_for[t] + ewma_alpha * pf
            )
            ewma_pts_against[t] = (
                pa if t not in ewma_pts_against
                else (1 - ewma_alpha) * ewma_pts_against[t] + ewma_alpha * pa
            )
            last_game_date[t] = gdate
            recent_dates[t].append(gdate)
            games_played[t] += 1
            last_venue[t] = h
            last10[t].append(1 if won else 0)
            win_streak[t] = (win_streak[t] + 1) if won else 0
            season_games[t] += 1
            season_wins[t] += 1 if won else 0
        road_trip[a] += 1
        road_trip[h] = 0

    return pd.DataFrame(rows)
