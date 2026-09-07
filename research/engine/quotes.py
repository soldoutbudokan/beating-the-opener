"""Scoring-only quote adapter. No quote data enters the structural model."""
from __future__ import annotations
from collections import Counter
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path

from research.clocks import parse

MARKET_IDS = {'points': 393, 'rebounds': 397, 'assists': 391, 'threes': 390}


def decimal_odds(american):
    value = float(american)
    if not math.isfinite(value) or abs(value) < 100:
        raise ValueError('Invalid American odds')
    return 1 + (value / 100 if value > 0 else 100 / -value)


def quote_id(row):
    return ':'.join(str(row[key]) for key in ('event_id', 'offer_id', 'market'))


def paired_reference(offer, book, line, tip_at, opening_at):
    """Last archived coherent same-book/line pair strictly before scheduled tip.

    This is a provider-reported historical reference, not proof of a captured,
    tradable final quote. Both side times and age remain visible. No use of
    outcome/cover/EV fields embedded in the provider payload.
    """
    tip = parse('quote.tip_at', tip_at)
    opened = parse('forecast.as_of', opening_at)
    sides = {'over': [], 'under': []}
    rejected = Counter()
    for selection in offer.get('selections', []):
        side = selection.get('selection', '').lower()
        if side not in sides:
            continue
        for source in selection.get('books', []):
            if str(source.get('id')) != str(book):
                continue
            for record in source.get('lines', []):
                try:
                    if float(record['line']) != float(line):
                        continue
                    at = parse('bp.updated', record.get('updated'))
                    odds = decimal_odds(record['cost'])
                    if not opened <= at < tip:
                        rejected['outside_open_to_tip'] += 1
                        continue
                    if record.get('active') is not True or record.get('is_off') is not False:
                        rejected['inactive'] += 1
                        continue
                    sides[side].append((at, odds))
                except (KeyError, TypeError, ValueError, OverflowError):
                    rejected['malformed'] += 1
    pairs = []
    for over_at, over in sides['over']:
        for under_at, under in sides['under']:
            # Original opener accepts <= five minute side skew; preserve that
            # explicit contemporaneity requirement for the closing reference.
            skew = abs((over_at - under_at).total_seconds())
            booksum = 1 / over + 1 / under
            if skew > 300 or not 1.0 <= booksum <= 1.15:
                rejected['incoherent_pair'] += 1
                continue
            pairs.append((max(over_at, under_at), over_at, under_at, over, under))
    if not pairs:
        return {'status': 'missing', 'rejections': dict(rejected), 'p_over_nonpush': None}
    latest_at = max(p[0] for p in pairs)
    candidates = {p for p in pairs if p[0] == latest_at}
    if len(candidates) != 1:
        return {'status': 'ambiguous', 'rejections': dict(rejected), 'p_over_nonpush': None}
    at, over_at, under_at, over, under = candidates.pop()
    return {'status': 'matched', 'p_over_nonpush': (1 / over) / (1 / over + 1 / under),
            'book': str(book), 'line': float(line), 'over_odds': over, 'under_odds': under,
            'over_at': over_at.isoformat(), 'under_at': under_at.isoformat(),
            'available_at': at.isoformat(), 'age_seconds': (tip - at).total_seconds(),
            'rejections': dict(rejected),
            'meaning': 'last archived same-line pre-tip reference; historical capture availability unverified'}


class QuoteArchive:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = {}

    @lru_cache(maxsize=1024)
    def _offers(self, event_id, market):
        relative = Path('offers') / f'{event_id}_{MARKET_IDS[market]}.json.gz'
        path = self.root / relative
        if not path.exists():
            return {}
        raw = path.read_bytes()
        self.manifest[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        payload = json.loads(gzip.decompress(raw))
        result = {}
        for offer in payload.get('offers', []):
            key = str(offer['id'])
            if key in result and result[key] != offer:
                raise ValueError('Conflicting archived offer identity')
            result[key] = offer
        return result

    def reference(self, row):
        event = str(int(row['event_id']))
        offer = self._offers(event, row['market']).get(str(row['offer_id']))
        if offer is None:
            return {'status': 'missing_offer', 'p_over_nonpush': None}
        if str(offer.get('event_id')) != event:
            raise ValueError('Archived event identity differs')
        if str(offer.get('market_id')) != str(MARKET_IDS[row['market']]):
            raise ValueError('Archived market identity differs')
        if str(offer.get('player_id')) != str(int(row['bp_player_id'])):
            raise ValueError('Archived player identity differs')
        return paired_reference(offer, str(int(row['open_book'])), row['open_line'],
                                row['tip_at'], row['forecast_at'])


def score_quote(row):
    """Preserve PR #2's prices and expose both named opener probabilities.

    The shared scorer defines p_open_nonpush using proportional no-vig odds.
    PR #2 saved a power-devig probability; retain that exact value separately
    so it cannot be mistaken for the shared scorer's price-derived field.
    Outcome labels and model forecasts are added later.
    """
    if int(row['season']) != 2025:
        raise ValueError('Only frozen reused-2025 quotes are admitted')
    if row['open_book'] != row['open_book_under']:
        raise ValueError('Opening pair mixes books')
    over_at = parse('quote.open_created_over', row['open_created_over'])
    under_at = parse('quote.open_created_under', row['open_created_under'])
    as_of = parse('forecast.as_of', row['forecast_at'])
    tip = parse('quote.tip_at', row['tip_at'])
    if max(over_at, under_at) != as_of or not as_of < tip:
        raise ValueError('Frozen quote clock contract changed')
    over, under = decimal_odds(row['open_over']), decimal_odds(row['open_under'])
    power_probability = float(row['p_open'])
    if not math.isfinite(power_probability) or not 0 <= power_probability <= 1:
        raise ValueError('Invalid frozen power-devig probability')
    return {'game_id': str(int(row['game_id'])), 'player_id': str(int(row['athlete_id'])),
            'quote_id': quote_id(row), 'book': str(int(row['open_book'])), 'market': row['market'],
            'line': float(row['open_line']), 'over_odds': over,
            'under_odds': under, 'over_at': over_at.isoformat(),
            'under_at': under_at.isoformat(), 'quote_available_at': as_of.isoformat(),
            'as_of': as_of.isoformat(), 'tip_at': tip.isoformat(), 'date': row['date'],
            'p_open_nonpush': (1 / over) / (1 / over + 1 / under),
            'pr2_power_p_open': power_probability,
            'baseline_p_over_nonpush': float(row['box_ridge_p_over_nonpush'])}


def preflight_quotes(rows, archive=None):
    """Validate every quote boundary without fitting or outcome scoring.

    The strict scorer receives synthetic predictions and grades only. Actual
    counts, participation and fitted forecast fields from the supplied rows
    are never read. Optional archive checks use quote identities and prices;
    close-reference coverage is source metadata, not a performance result.
    """
    from .scoring import validate_rows

    sentinels, references = [], Counter()
    for original in rows:
        row = score_quote(original)
        # A zero-count sentinel is consistent across all quoted lines for a
        # player-game. At a zero line its mass is an explicit push.
        push = .25 if row['line'] == 0 else 0.
        row.update(p_over=.5, p_push=push, p_under=.5-push,
                   p_dnp=.1, minutes_values=[1.], minutes_probs=[1.],
                   model_mean=1., model_median=1, actual=0,
                   actual_minutes=1., void=False, count_p_actual=.25)
        sentinels.append(row)
        if archive is not None:
            reference = archive.reference(original)
            references[reference['status']] += 1
            row['close_p_over_nonpush'] = reference['p_over_nonpush']
    checked = validate_rows(sentinels)
    if len(checked) != len(sentinels):
        raise ValueError('Quote preflight found duplicate rows')
    return {'status': 'PASS', 'quotes': len(checked),
            'basis': 'Quote schema and archive checks with synthetic forecasts and outcomes; no scores computed',
            'opener_method': 'proportional no-vig from saved paired prices',
            'retained_pr2_opener_method': 'power devig',
            'close_reference_statuses': dict(references)}
