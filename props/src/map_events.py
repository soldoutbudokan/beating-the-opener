"""Crosswalk BP event_id -> native game id, per sport.

Grading joins by native game id (never player-name + date): BP `scheduled`
is UTC, boxscores key by local date, and MLB doubleheaders make date-probe
joins actively dangerous (AUDIT H1/C2 - 25% feature leakage in wnba v1).

Method: convert both sides' start times to an ET calendar date at the
source, learn the BP->native team-code map by co-occurrence frequency
(then assert it's a bijection), join on (et_date, home, away), and break
doubleheader ties by minimum |start-time difference| with a <90 min
assertion on the runner-up gap.

Outputs data/event_map_<sport>.pkl:
  event_id, native_id, et_date, home (native code), away, game_number

Usage: python3 src/map_events.py --sport MLB
"""
import argparse
import os

import pandas as pd

from build_props import parse_events
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")


def et_date(utc_series):
    return (pd.to_datetime(utc_series, utc=True)
            .dt.tz_convert("America/New_York").dt.date.astype(str))


def load_native(sport):
    """Return DataFrame: native_id, sched_utc, et_date, home, away, game_number."""
    ddir = os.path.join(ROOT, "data", sport.lower())
    if sport == "MLB":
        frames = []
        for f in sorted(os.listdir(ddir)):
            if f.startswith("schedule_"):
                frames.append(pd.read_parquet(os.path.join(ddir, f)))
        s = pd.concat(frames, ignore_index=True)
        return pd.DataFrame({
            "native_id": s.gamePk, "sched_utc": s.gameDate,
            "et_date": et_date(s.gameDate),
            "home": s.home, "away": s.away,
            "game_number": s.gameNumber, "status": s.status,
        })
    if sport == "NBA":
        s = pd.concat([pd.read_parquet(os.path.join(ddir, f))
                       for f in sorted(os.listdir(ddir))
                       if f.startswith("nba_schedule_")], ignore_index=True)
        return pd.DataFrame({
            "native_id": s.game_id,
            "sched_utc": s.game_date_time,          # tz-aware
            "et_date": s.game_date.astype(str).str[:10],
            "home": s.home_abbreviation, "away": s.away_abbreviation,
            "game_number": 1, "status": s.status_type_completed,
        })
    if sport == "NFL":
        g = pd.read_csv(os.path.join(ddir, "games.csv"))
        g = g[g.season >= 2025]
        return pd.DataFrame({
            "native_id": g.game_id, "sched_utc": None,
            "et_date": g.gameday,                    # already a local date
            "home": g.home_team, "away": g.away_team,
            "game_number": 1, "status": g.result.notna(),
        })
    if sport == "NHL":
        s = pd.concat([pd.read_parquet(os.path.join(ddir, f))
                       for f in sorted(os.listdir(ddir))
                       if f.startswith("schedule_")], ignore_index=True)
        return pd.DataFrame({
            "native_id": s.game_id, "sched_utc": s.startTimeUTC,
            "et_date": et_date(s.startTimeUTC),
            "home": s.home, "away": s.away,
            "game_number": 1, "status": s.state,
        })
    raise ValueError(sport)


def learn_code_map(bp, nat):
    """BP team code -> native code by same-ET-date co-occurrence frequency."""
    counts = {}
    nat_by_date = {}
    for r in nat.itertuples():
        nat_by_date.setdefault(r.et_date, []).append(r)
    for r in bp.itertuples():
        for n in nat_by_date.get(r.date, []):
            for b_code, n_code in ((r.home, n.home), (r.visitor, n.away)):
                counts[(b_code, n_code)] = counts.get((b_code, n_code), 0) + 1
    best = {}
    for (b, n), c in counts.items():
        if c > best.get(b, (None, 0))[1]:
            best[b] = (n, c)
    cmap = {b: n for b, (n, _) in best.items()}
    # bijection check: two BP codes must not claim one native code
    rev = {}
    for b, n in cmap.items():
        if n in rev:
            raise RuntimeError(f"code map collision: {b} and {rev[n]} -> {n}")
        rev[n] = b
    return cmap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    sport = args.sport

    bp = parse_events(sport)
    bp = bp[bp.status == "closed"]
    nat = load_native(sport)
    cmap = learn_code_map(bp, nat)
    missing = [c for c in set(bp.home) | set(bp.visitor) if c not in cmap]
    if missing:
        raise RuntimeError(f"unmapped BP team codes: {sorted(missing)}")

    nat_idx = {}
    for r in nat.itertuples():
        nat_idx.setdefault((r.et_date, r.home, r.away), []).append(r)

    rows, dh_gaps, unmatched = [], [], []
    for r in bp.itertuples():
        key = (r.date, cmap[r.home], cmap[r.visitor])
        cands = nat_idx.get(key, [])
        if not cands:
            unmatched.append((r.event_id, r.date, r.home, r.visitor))
            continue
        if len(cands) == 1:
            pick = cands[0]
        else:
            # doubleheader: closest scheduled start wins - but only when the
            # pick is close (<90m) AND clearly separated from the runner-up
            # (>=60m margin). BP sometimes lists both DH games at the same
            # time; a wrong pick grades game 1's props on game 2's boxscore,
            # so ambiguous ties are dropped (props void), never guessed.
            bp_t = pd.Timestamp(r.scheduled, tz="UTC")
            gaps = sorted(
                ((abs((pd.Timestamp(c.sched_utc) - bp_t).total_seconds()) / 60,
                  i, c) for i, c in enumerate(cands)))
            pick_gap, _, pick = gaps[0]
            runner_gap = gaps[1][0]
            if pick_gap > 90 or runner_gap - pick_gap < 60:
                unmatched.append((r.event_id, r.date, r.home, r.visitor))
                continue
            dh_gaps.append((pick_gap, runner_gap))
        rows.append({
            "event_id": r.event_id, "native_id": pick.native_id,
            "et_date": r.date, "home": cmap[r.home], "away": cmap[r.visitor],
            "game_number": pick.game_number,
        })

    out = pd.DataFrame(rows)
    dupes = out[out.duplicated("native_id", keep=False)]
    if len(dupes):
        raise RuntimeError(
            f"{dupes.native_id.nunique()} native games claimed by multiple "
            f"BP events:\n{dupes.sort_values('native_id').head(10)}")

    out.to_pickle(os.path.join(ROOT, "data", f"event_map_{sport.lower()}.pkl"))
    rate = len(out) / max(len(bp), 1)
    print(f"{sport}: {len(out)}/{len(bp)} closed BP events mapped ({rate:.2%})")
    if dh_gaps:
        worst = max(g[0] for g in dh_gaps)
        closest_runner = min(g[1] for g in dh_gaps)
        print(f"  doubleheader ties broken: {len(dh_gaps)} "
              f"(worst pick-gap {worst:.0f}m, closest runner-up {closest_runner:.0f}m)")
    if unmatched:
        print(f"  UNMATCHED ({len(unmatched)}):")
        for u in unmatched[:15]:
            print("   ", u)


if __name__ == "__main__":
    main()
