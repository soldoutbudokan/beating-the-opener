"""Research-only timestamp contracts. Never infer publication from a game date."""
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo

UTC = timezone.utc
ET = ZoneInfo('America/New_York')

# field -> (kind, zone, meaning). These contracts do not change live parsers.
FIELDS = {
    'bp.created': ('instant', 'UTC', 'provider-reported opener creation'),
    'bp.updated': ('instant', 'UTC', 'provider-reported quote update'),
    'bp.scheduled': ('instant', 'UTC', 'scheduled tip'),
    'bp.utc': ('instant', 'UTC', 'response timestamp, not historical availability'),
    'bp.ts': ('epoch', 'UTC', 'response epoch seconds'),
    'wehoop.date': ('instant', 'UTC', 'schedule tip'),
    'wehoop.game_date_time': ('instant', 'America/New_York', 'Eastern schedule tip'),
    'wehoop.game_date': ('date', 'America/New_York', 'game calendar date only'),
    'wehoop.wallclock': ('instant', None, 'event wall clock, not publication time'),
    'wehoop.completed_at': ('instant', None, 'completion instant, if provided'),
    'source.retrieved_at': ('instant', None, 'local retrieval, not historic publication'),
    'source.available_at': ('instant', None, 'observed or explicitly assumed availability'),
    'espn.captured_utc': ('instant', 'UTC', 'normalized snapshot capture'),
    'espn.event.date': ('instant', None, 'event scheduled tip'),
    'espn.utc_tip': ('instant', None, 'fallback box scheduled tip'),
    'espn.box.date': ('date', 'America/New_York', 'fallback slate calendar date'),
    'espn.return_date': ('date', None, 'return timeline; not observation time'),
    'news.published': ('instant', None, 'provider-reported news publication'),
    'override.added': ('instant', 'UTC', 'human entry timestamp'),
    'override.game_date': ('date', 'America/New_York', 'target date; null means until cleared'),
    'pm.start_date': ('instant', None, 'market start, not match start'),
    'pm.accepting_orders_ts': ('instant', None, 'market opens for orders'),
    'pm.end_date': ('instant', None, 'scheduled market end; not verified resolution'),
    'pm.closed_time': ('instant', None, 'provider-reported closure; resolution semantics unverified'),
    'pm.game_start_label': ('instant', None, 'nominal match start; known hour errors'),
    'pm.t': ('epoch', 'UTC', 'price observation epoch seconds'),
    'pm.onset_utc': ('instant', None, 'inferred from future price path; not observed start'),
    'cricsheet.date': ('date', None, 'venue-local match date; hour/zone unavailable'),
    'forecast.as_of': ('instant', None, 'strict forecast information cutoff'),
    'quote.open_created_over': ('instant', None, 'normalized over opener timestamp'),
    'quote.open_created_under': ('instant', None, 'normalized under opener timestamp'),
    'quote.tip_at': ('instant', None, 'normalized scheduled tip'),
}


def parse(field, value):
    """Reject missing/ambiguous values; naive time is allowed only by contract."""
    kind, zone, _ = FIELDS[field]
    if value is None or isinstance(value, bool):
        raise ValueError(f'{field}: missing/invalid timestamp')
    if kind == 'epoch':
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{field}: finite epoch seconds required')
        result = datetime.fromtimestamp(value, UTC)
        if not 2000 <= result.year <= 2100:
            raise ValueError(f'{field}: epoch outside source range; check seconds vs milliseconds')
        return result
    if kind == 'date':
        # Intentionally returns date, never a midnight timestamp.
        if isinstance(value, datetime):
            raise ValueError(f'{field}: calendar date required')
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    text = str(value).strip().replace('Z', '+00:00')
    if 'T' not in text and ' ' not in text:
        raise ValueError(f'{field}: date alone does not establish an instant')
    result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        if zone is None:
            raise ValueError(f'{field}: explicit timezone required')
        tz = ZoneInfo(zone)
        a, b = result.replace(tzinfo=tz, fold=0), result.replace(tzinfo=tz, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise ValueError(f'{field}: ambiguous or nonexistent local time')
        result = a
    elif zone is not None:
        expected = result.astimezone(ZoneInfo(zone))
        if result.utcoffset() != expected.utcoffset():
            raise ValueError(f'{field}: offset disagrees with source timezone')
    return result.astimezone(UTC)


def schedule_tip(utc_date, eastern_date_time):
    utc = parse('wehoop.date', utc_date)
    eastern = parse('wehoop.game_date_time', eastern_date_time)
    if utc != eastern:
        raise ValueError('wehoop UTC and Eastern schedule columns disagree')
    return utc


def before(available_at, as_of):
    if not isinstance(available_at, datetime) or not isinstance(as_of, datetime):
        raise ValueError('Calendar dates cannot prove pre-forecast availability')
    if available_at.tzinfo is None or as_of.tzinfo is None or available_at >= as_of:
        raise ValueError('Every consumed observation must strictly precede the forecast')
