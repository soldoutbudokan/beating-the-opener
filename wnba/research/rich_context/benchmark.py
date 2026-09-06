"""Historical WNBA opener quotes, with source and timing preserved.

This is a research price comparison, not a reconstruction of executable
FanDuel bets. BettingPros' ``opening_line`` is a source-book record shared
across its offer, not the opening quote of every book listed in that offer.
The archived payloads were collected later; their timestamps describe the
reported quote, not independently verified real-time availability.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import numpy as np
import pandas as pd

from .baseline import PARTS, RAW

MARKETS = {393: "points", 397: "rebounds", 391: "assists", 390: "threes",
           396: "pra", 394: "pts_ast", 395: "pts_reb", 398: "reb_ast",
           399: "steals", 392: "blocks", 401: "turnovers", 400: "stl_blk"}
TEAM_ALIASES = {"NY": "NYL", "LV": "LVA", "LA": "LAS", "WSH": "WAS",
                "CONN": "CON", "PHO": "PHX", "GS": "GSV"}


def normalized_name(value):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def normalized_id(value):
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def normalized_team(value):
    text = str(value).upper().strip()
    return TEAM_ALIASES.get(text, text)


def american_decimal(cost):
    c = np.asarray(cost, float)
    if not np.isfinite(c).all() or (np.abs(c) < 100).any():
        raise ValueError("American odds must be finite and at least 100 in absolute value.")
    return np.where(c > 0, 1 + c / 100.0, 1 + 100.0 / -c)


def power_devig(over_cost, under_cost):
    """Two-way P(over | no push), using the incumbent power method."""
    po, pu = 1 / american_decimal(over_cost), 1 / american_decimal(under_cost)
    lo, hi = np.full(po.shape, 0.5), np.full(po.shape, 10.0)
    for _ in range(80):
        exponent = (lo + hi) / 2
        too_high = po ** exponent + pu ** exponent > 1
        lo, hi = np.where(too_high, exponent, lo), np.where(too_high, hi, exponent)
    return po ** ((lo + hi) / 2)


def _utc(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


def _number(value):
    try:
        result = float(value)
        return result if np.isfinite(result) else np.nan
    except (ValueError, TypeError):
        return np.nan


def parse_offer(offer: dict, event: dict, *, max_pair_skew_seconds=0) -> dict:
    """One source opener per offer; retain reasons for rejecting the pair."""
    market = MARKETS.get(int(offer.get("market_id", -1)))
    sides = {s.get("selection"): s.get("opening_line") or {}
             for s in offer.get("selections") or []}
    over, under = sides.get("over", {}), sides.get("under", {})
    participant = ((offer.get("participants") or [{}])[0] or {})
    player = participant.get("player") or {}
    name = (f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            or participant.get("name", ""))
    oline, uline = _number(over.get("line")), _number(under.get("line"))
    ocost, ucost = _number(over.get("cost")), _number(under.get("cost"))
    otime, utime, tip = _utc(over.get("created")), _utc(under.get("created")), _utc(event.get("scheduled"))
    reasons = []
    if market is None:
        reasons.append("unsupported_market")
    if not name:
        reasons.append("missing_player")
    if not np.isfinite(oline) or oline < 0 or oline != uline:
        reasons.append("missing_or_different_lines")
    if over.get("book_id") is None or over.get("book_id") != under.get("book_id"):
        reasons.append("missing_or_different_books")
    odds_ok = np.isfinite(ocost) and np.isfinite(ucost) and abs(ocost) >= 100 and abs(ucost) >= 100
    if not odds_ok:
        reasons.append("invalid_prices")
    booksum = float(1 / american_decimal(ocost) + 1 / american_decimal(ucost)) if odds_ok else np.nan
    if odds_ok and not 1.00 <= booksum <= 1.15:
        reasons.append("margin_outside_0_to_15_percent")
    if pd.isna(otime) or pd.isna(utime) or pd.isna(tip):
        reasons.append("missing_time")
        skew = np.nan
    else:
        skew = abs((otime - utime).total_seconds())
        if skew > max_pair_skew_seconds:
            reasons.append("unsynchronized_openers")
        if max(otime, utime) >= tip:
            reasons.append("opener_not_before_tip")
    quote = max(otime, utime) if pd.notna(otime) and pd.notna(utime) else pd.NaT
    return {
        "event_id": normalized_id(event["id"]), "offer_id": str(offer.get("id", "")),
        "bp_player_id": normalized_id(offer.get("player_id")), "player": name,
        "normalized_player": normalized_name(name), "bp_player_team": player.get("team"),
        "market": market, "open_line": oline, "open_over_cost": ocost,
        "open_under_cost": ucost, "open_book": over.get("book_id"),
        "open_book_under": under.get("book_id"), "open_created_over": otime,
        "open_created_under": utime, "forecast_at": quote, "tip_at": tip,
        "pair_skew_seconds": skew, "date": tip.tz_convert("America/New_York").date().isoformat()
        if pd.notna(tip) else None, "season": event.get("season"),
        "season_type": event.get("season_type"), "event_home": normalized_team(event.get("home")),
        "event_away": normalized_team(event.get("visitor")), "open_booksum": booksum,
        "open_coherent": not reasons, "rejection_reasons": ";".join(reasons),
        "p_open": float(power_devig(ocost, ucost)) if odds_ok else np.nan,
    }


def load_openers(raw_dir, season=2025, *, max_pair_skew_seconds=0):
    """Return accepted opener rows and a coverage report; never read 2026.

    Same-time, same-book, same-threshold pairs are the default. A looser time
    tolerance is an explicitly labeled sensitivity analysis, not evidence
    that the two quoted prices coexisted. Missing files and incomplete
    pagination are counted so the scored sample is not mistaken for all props.
    """
    if int(season) != 2025:
        raise ValueError("This research benchmark is limited to development season 2025.")
    if max_pair_skew_seconds < 0:
        raise ValueError("Pair time tolerance cannot be negative.")
    root = Path(raw_dir)
    events_path = root / "events_2025.json.gz"
    event_bytes = events_path.read_bytes()
    events = json.loads(gzip.decompress(event_bytes))
    # BP uses PST, not POST, for playoff games; CC is the Commissioner's
    # Cup final. These competitive games are retained, preseason is not.
    events = [e for e in events if int(e.get("season", 0)) == 2025
              and e.get("season_type") in {"REG", "PST", "POST", "CC"}]
    records, reasons, missing, incomplete, hashes = [], Counter(), [], [], {}
    hashes[events_path.name] = hashlib.sha256(event_bytes).hexdigest()
    files_read = 0
    for event in events:
        for market_id in MARKETS:
            rel = f"offers/{event['id']}_{market_id}.json.gz"
            path = root / rel
            if not path.exists():
                missing.append(rel)
                continue
            raw = path.read_bytes()
            hashes[rel] = hashlib.sha256(raw).hexdigest()
            data = json.loads(gzip.decompress(raw))
            files_read += 1
            offers = data.get("offers") or []
            total = int((data.get("_pagination") or {}).get("total_items") or 0)
            if total > len(offers):
                incomplete.append(rel)
                continue
            for offer in offers:
                if normalized_id(offer.get("event_id")) != normalized_id(event["id"]):
                    reasons["payload_event_mismatch"] += 1
                    continue
                record = parse_offer(offer, event, max_pair_skew_seconds=max_pair_skew_seconds)
                if not record["open_coherent"]:
                    reasons.update(record["rejection_reasons"].split(";"))
                records.append(record)
    data = pd.DataFrame(records)
    if not len(data):
        raise ValueError("No 2025 offer rows found in the archived inputs.")
    accepted = data[data.open_coherent].copy()
    key = ["event_id", "bp_player_id", "market"]
    duplicated = accepted.duplicated(key, keep=False)
    # Distinct payload records for one prop are ambiguous; do not silently pick
    # the cheapest, most profitable, latest, or outcome-dependent candidate.
    reasons["duplicate_prop_identity"] = int(duplicated.sum())
    accepted = accepted[~duplicated].reset_index(drop=True)
    accepted["open_book"] = accepted.open_book.astype(int)
    accepted["open_book_under"] = accepted.open_book_under.astype(int)
    canonical_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    report = {"season": 2025, "benchmark": "BettingPros reported source-book opener",
              "captured_realtime": False, "pair_skew_limit_seconds": max_pair_skew_seconds,
              "competitive_events": len(events),
              "event_season_type_counts": dict(Counter(e.get("season_type") for e in events)),
              "offer_files_read": files_read,
              "missing_files": missing, "incomplete_files_excluded": incomplete,
              "offer_rows_inspected": len(data), "coherent_rows": len(accepted),
              "rejection_counts_not_mutually_exclusive": dict(reasons),
              "open_source_books": {normalized_id(k): int(v) for k, v in accepted.open_book.value_counts().items()},
              "market_counts": {str(k): int(v) for k, v in accepted.market.value_counts().items()},
              "input_file_count": len(hashes), "input_manifest_sha256": canonical_hash,
              "input_sha256": hashes}
    if len(accepted):
        hours = (accepted.tip_at - accepted.forecast_at).dt.total_seconds() / 3600
        report["hours_before_tip_quantiles"] = {str(q): float(hours.quantile(q)) for q in [0, .25, .5, .75, 1]}
    return accepted, report


def match_openers(openers: pd.DataFrame, player_box: pd.DataFrame):
    """Exact-date, both-team event match, then unique athlete-name match.

    No neighboring-date fallback and no guessed ID aliases. Output includes
    unmatched/void rows, with explicit reasons. Final-game boxes establish the
    outcome identity only; the caller must obtain prediction inputs from the
    independently timed history store, never from these final-game columns.
    """
    box = player_box.copy()
    box["game_date"] = pd.to_datetime(box.game_date)
    if (box.game_date.dt.year != 2025).any():
        box = box[box.game_date.dt.year == 2025].copy()
    for c in ["athlete_id", "game_id", "team_id", "opponent_team_id"]:
        box[c] = box[c].map(normalized_id)
    box["normalized_player"] = box.athlete_display_name.map(normalized_name)
    box["date"] = box.game_date.dt.strftime("%Y-%m-%d")
    box["_team"] = box.team_abbreviation.map(normalized_team)
    box["_opponent"] = box.opponent_team_abbreviation.map(normalized_team)
    box["event_home"] = np.where(box.home_away.eq("home"), box._team, box._opponent)
    box["event_away"] = np.where(box.home_away.eq("away"), box._team, box._opponent)
    event_key = ["date", "event_home", "event_away"]
    games = box[event_key + ["game_id"]].drop_duplicates()
    ambiguous = games.duplicated(event_key, keep=False)
    games = games[~ambiguous]
    rows = openers.merge(games, on=event_key, how="left", validate="many_to_one")
    identity = ["game_id", "normalized_player"]
    duplicates = box.duplicated(identity, keep=False)
    unique = box[~duplicates].copy()
    kept = identity + ["athlete_id", "team_id", "opponent_team_id", "minutes", "home_away"] + list(RAW.values())
    rows = rows.merge(unique[kept], on=identity, how="left", validate="many_to_one")
    rows["home"] = rows.home_away.eq("home").astype(int)
    rows["matched"] = rows.athlete_id.notna()
    rows["void"] = rows.matched & (rows.minutes.isna() | rows.minutes.le(0))
    rows["match_reason"] = np.select(
        [rows.game_id.isna(), ~rows.matched, rows.void],
        ["no_unique_exact_date_both_team_game", "no_unique_player_in_game", "did_not_play"],
        default="matched")
    rows["actual"] = np.nan
    for market, parts in PARTS.items():
        mask = rows.market.eq(market) & rows.matched & ~rows.void
        rows.loc[mask, "actual"] = rows.loc[mask, [RAW[p] for p in parts]].sum(axis=1, min_count=len(parts))
    # Remove every outcome component other than the explicit grading field,
    # so accidental feature selection cannot absorb tonight's box score.
    rows = rows.drop(columns=list(RAW.values()) + ["minutes", "home_away"])
    report = {"quote_rows": len(rows), "matched_rows": int(rows.matched.sum()),
              "void_rows": int(rows.void.sum()), "match_counts": rows.match_reason.value_counts().to_dict(),
              "ambiguous_game_candidates": int(ambiguous.sum()),
              "ambiguous_player_candidates": int(duplicates.sum()),
              "identity_method": "exact ET date + both teams + unique normalized name"}
    return rows, report
