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

MARKET_IDS = {'points': 393, 'rebounds': 394, 'assists': 395, 'threes': 398}


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
        return paired_reference(offer, str(int(row['open_book'])), row['open_line'],
                                row['tip_at'], row['forecast_at'])


def score_quote(row):
    """Preserve PR #2's exact quote contract; outcome labels are added later."""
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
    return {'game_id': str(int(row['game_id'])), 'player_id': str(int(row['athlete_id'])),
            'quote_id': quote_id(row), 'book': str(int(row['open_book'])), 'market': row['market'],
            'line': float(row['open_line']), 'over_odds': decimal_odds(row['open_over']),
            'under_odds': decimal_odds(row['open_under']), 'over_at': over_at.isoformat(),
            'under_at': under_at.isoformat(), 'quote_available_at': as_of.isoformat(),
            'as_of': as_of.isoformat(), 'tip_at': tip.isoformat(), 'date': row['date'],
            'p_open_nonpush': float(row['p_open']),
            'baseline_p_over_nonpush': float(row['box_ridge_p_over_nonpush'])}
