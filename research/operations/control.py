"""Deterministic research operating status. No order placement or live adoption.

The registry records an externally reviewed qualification decision. Validating
its structure does not independently establish its truth. An empty registry is
normal: healthy collection does not establish a market-beating model.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse

POLICY_SCHEMA = 'operations-policy-v1'
STATUS_SCHEMA = 'operations-status-v1'
HEALTH_SCHEMA = 'collection-health-v1'
MODEL_STATES = {'registered', 'collecting', 'evaluation_due', 'stopped', 'validated'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def clock(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('an explicit timestamp is required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp timezone is required')
    return parsed.astimezone(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def number(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('finite numeric value required')
    if not math.isfinite(value) or value < minimum:
        raise ValueError('numeric value outside allowed range')
    return value


def count(value):
    number(value)
    if not isinstance(value, int):
        raise ValueError('integer count required')
    return value


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('SHA-256 identity required')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise ValueError('stable identifier required')
    return value


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.get('schema') != POLICY_SCHEMA:
        raise ValueError('unsupported policy schema')
    identifier(policy['policy_id'])
    required = policy['required_sources']
    if not isinstance(required, dict) or not required:
        raise ValueError('required sources must be explicit')
    for name, settings in required.items():
        identifier(name)
        if not isinstance(settings, dict) or number(settings['max_age_seconds']) <= 0:
            raise ValueError('positive source age limit required')
        markets = settings.get('required_markets', [])
        if not isinstance(markets, list) or len(markets) != len(set(markets)):
            raise ValueError('required markets must be a unique list')
        for market in markets:
            identifier(market)
    number(policy.get('max_clock_skew_seconds', 0))
    models = policy['models']
    if not isinstance(models, list):
        raise ValueError('model registry must be a list')
    ids = []
    for model in models:
        ids.append(identifier(model['model_id']))
        sha256(model['recipe_sha256'])
        if model['state'] not in MODEL_STATES:
            raise ValueError('unknown model lifecycle state')
        sources = model['required_sources']
        if not isinstance(sources, list) or not sources or len(sources) != len(set(sources)):
            raise ValueError('model sources must be explicit and unique')
        if any(source not in required for source in sources):
            raise ValueError('model uses an unmonitored source')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate model identity')


def qualification_errors(model, now):
    """Require a recorded prospective decision, never infer one from test success."""
    if model['state'] != 'validated':
        return [f"Lifecycle state is {model['state']}."]
    try:
        evidence = model['qualification']
        if not isinstance(evidence, dict):
            raise ValueError('qualification must be an object')
        if evidence['schema'] != 'operations-qualification-v1':
            raise ValueError('unsupported qualification schema')
        if evidence['model_id'] != model['model_id'] or evidence['recipe_sha256'] != model['recipe_sha256']:
            raise ValueError('qualification belongs to a different model recipe')
        sha256(evidence['receipt_sha256'])
        sha256(evidence['registration_sha256'])
        # A mutable branch URL is not a pinned evidence record.
        if not isinstance(evidence['record_url'], str):
            raise ValueError('qualification record URL must be a string')
        url = urlparse(evidence['record_url'])
        if url.scheme != 'https' or not url.netloc or url.username or url.password:
            raise ValueError('publicly addressable HTTPS evidence record required')
        if url.hostname != 'github.com' or not re.fullmatch(
                r'/[^/]+/[^/]+/blob/[0-9a-f]{40}/.+', url.path):
            raise ValueError('qualification must link to a receipt at a full GitHub commit')
        if evidence['evidence_kind'] != 'independent-prospective':
            raise ValueError('only independent prospective evidence qualifies')
        dates = [clock(evidence[key]) for key in (
            'registered_at', 'recipe_frozen_at', 'evaluation_started_at',
            'evaluation_completed_at', 'recorded_at')]
        if not (dates[0] <= dates[1] < dates[2] < dates[3] <= dates[4] <= now):
            raise ValueError('registration, freeze, evaluation and record clocks are out of order')
        if evidence['independent_reproduction'] != 'PASS' or evidence['decision'] != 'VALIDATE':
            raise ValueError('independent reproduction and validation decision are required')
        checks = evidence['checks']
        if not isinstance(checks, dict):
            raise ValueError('qualification checks must be an object')
        for key in ('all_registered_gates_passed', 'timing_verified', 'costs_accounted',
                    'evaluation_was_unused', 'forecast_and_execution_rules_frozen'):
            if checks.get(key) is not True:
                raise ValueError(f'qualification check missing or failed: {key}')
        # These bounds must come from the registered study, including its
        # dependence/multiplicity treatment. The controller does not estimate them.
        lower, upper = evidence['net_return_interval']
        number(lower, minimum=-1)
        number(upper, minimum=-1)
        if not 0 < lower <= upper:
            raise ValueError('net return interval must establish a positive result after costs')
        if not isinstance(evidence['uncertainty_method'], str) or not evidence['uncertainty_method'].strip():
            raise ValueError('registered uncertainty method required')
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return [f'Qualification unavailable or invalid: {error}']
    return []


def incident(code, subject, summary, severity='error'):
    return {'incident_id': digest([code, subject])[:24], 'code': code,
            'subject': subject, 'severity': severity, 'summary': summary}


def source_issues(name, source, settings, now, skew, started=None, completed=None):
    issues = []
    add = lambda code, summary: issues.append(incident(code, name, summary))
    if not isinstance(source, dict):
        add('SOURCE_MISSING', f'{name}: the required collection report is missing.')
        return issues, {}
    state = source.get('status')
    if not isinstance(state, str) or state not in {'OK', 'NO_EVENTS', 'DEGRADED', 'FAILED'}:
        add('SOURCE_STATUS_INVALID', f'{name}: the source reported an unknown state.')
    elif state in {'DEGRADED', 'FAILED'}:
        add('SOURCE_FAILED', f'{name}: collection reported {state.lower()}; inspect its run report.')
    age = None
    try:
        checked, received = clock(source['checked_at']), clock(source['received_at'])
        if (checked - now).total_seconds() > skew or received > checked:
            raise ValueError('future or reversed source clock')
        if started is not None and completed is not None and not started <= received <= checked <= completed:
            raise ValueError('source clocks fall outside the collection run')
        age = max(0, (now - received).total_seconds())
        if age > settings['max_age_seconds'] or (now - checked).total_seconds() > settings['max_age_seconds']:
            add('SOURCE_STALE', f'{name}: its last response is older than the allowed collection interval.')
    except (KeyError, TypeError, ValueError, OverflowError):
        add('SOURCE_CLOCK_INVALID', f'{name}: a required response or check time is missing or invalid.')
    coverage = {}
    try:
        markets = source.get('markets', {})
        if not isinstance(markets, dict):
            raise ValueError('market coverage must be an object')
        if any(market not in markets for market in settings.get('required_markets', [])):
            raise ValueError('a required market coverage record is missing')
        for market, values in sorted(markets.items()):
            identifier(market)
            offers = count(values['offers'])
            if state == 'NO_EVENTS' and offers:
                raise ValueError('an empty event report cannot contain offers')
            pairs = count(values['any_book_coherent_pairs'])
            if pairs > offers:
                raise ValueError('more coherent offers than offers')
            books = values['books']
            if not isinstance(books, dict):
                raise ValueError('book coverage must be an object')
            eligible_books = []
            for book, detail in sorted(books.items()):
                paired = count(detail['coherent_pairs'])
                fresh = count(detail['fresh_pairs'])
                stale = count(detail['stale_pairs'])
                missing = count(detail['missing_quote_clock_pairs'])
                if fresh + stale + missing != paired or paired > offers:
                    raise ValueError('quote coverage counts do not reconcile')
                if fresh:
                    eligible_books.append(str(book))
            coverage[market] = {'offers': offers, 'coherent_offers': pairs,
                                'books_with_fresh_pairs': eligible_books}
            if offers and not pairs:
                issues.append(incident('QUOTE_PAIRS_MISSING', f'{name}.{market}',
                    f'{name} {market}: offers exist but no coherent two-sided price is available.'))
            elif pairs and not eligible_books:
                issues.append(incident('QUOTE_CLOCKS_UNUSABLE', f'{name}.{market}',
                    f'{name} {market}: paired prices exist but none have usable fresh clocks.'))
    except (KeyError, TypeError, ValueError, OverflowError):
        add('COVERAGE_INVALID', f'{name}: quote coverage is missing, malformed or inconsistent.')
    return issues, {'collection_state': state, 'age_seconds': age, 'coverage': coverage,
                    'status': 'DATA_DEGRADED' if issues else 'DATA_HEALTHY'}


def evaluate(health, policy, previous=None, now=None):
    """Return a fresh status and new transition events; inputs are never mutated."""
    now = clock(now) if isinstance(now, str) else now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('evaluation clock requires an explicit timezone')
    now = now.astimezone(timezone.utc)
    at = timestamp(now)
    issues, sources, models = [], {}, []
    policy_ok = True
    try:
        validate_policy(policy)
    except (KeyError, TypeError, ValueError, OverflowError):
        policy_ok = False
        issues.append(incident('POLICY_INVALID', 'controller',
            'The operating policy is missing or invalid. Model eligibility is blocked.'))
    health_ok = isinstance(health, dict) and health.get('schema') == HEALTH_SCHEMA
    if not health_ok:
        issues.append(incident('HEALTH_SCHEMA_INVALID', 'collector',
            'The collection health document is missing or uses an unsupported schema.'))
    elif policy_ok:
        skew = policy.get('max_clock_skew_seconds', 0)
        started = completed = None
        try:
            if not isinstance(health['run_id'], str) or not health['run_id'].strip():
                raise ValueError('run identity is required')
            started = clock(health['started_at'])
            completed = clock(health['completed_at'])
            if started > completed or (completed - now).total_seconds() > skew:
                raise ValueError('reversed or future run clock')
            # The run itself must be fresh enough for every required source.
            # Fresh-looking nested clocks cannot revive an old completed run.
            if any((now - completed).total_seconds() > settings['max_age_seconds']
                   for settings in policy['required_sources'].values()):
                issues.append(incident('RUN_STALE', 'collector',
                    'The latest completed collection run is older than the allowed interval.'))
        except (KeyError, TypeError, ValueError, OverflowError):
            issues.append(incident('RUN_CLOCK_INVALID', 'collector',
                'The collection run has missing, reversed or future timestamps.'))
        if not isinstance(health.get('status'), str) or health.get('status') not in {'OK', 'DEGRADED', 'FAILED'}:
            issues.append(incident('RUN_STATUS_INVALID', 'collector', 'The collection run state is unknown.'))
        elif health.get('status') in {'DEGRADED', 'FAILED'}:
            issues.append(incident('RUN_FAILED', 'collector', 'The latest collection run did not complete successfully.'))
        raw_sources = health.get('sources')
        raw_sources = raw_sources if isinstance(raw_sources, dict) else {}
        for name, settings in sorted(policy['required_sources'].items()):
            found, detail = source_issues(name, raw_sources.get(name), settings, now, skew, started, completed)
            issues.extend(found)
            sources[name] = detail or {'status': 'DATA_DEGRADED', 'coverage': {}}
        for model in sorted(policy['models'], key=lambda item: item['model_id']):
            errors = qualification_errors(model, now)
            unhealthy = [name for name in model['required_sources'] if sources[name]['status'] != 'DATA_HEALTHY']
            models.append({'model_id': model['model_id'], 'recipe_sha256': model['recipe_sha256'],
                           'state': model['state'], 'qualification_valid': not errors,
                           'ready': not errors and not unhealthy and not issues,
                           'blockers': errors + [f'Required source is unhealthy: {name}' for name in unhealthy]
                                       + (['Operating status is degraded.'] if issues else [])})
    previous_ok = previous is None or (isinstance(previous, dict) and previous.get('schema') == STATUS_SCHEMA)
    try:
        if previous is not None and previous_ok:
            if clock(previous['generated_at']) > now:
                previous_ok = False
            if not isinstance(previous['active_incidents'], list) or not isinstance(previous['recent_events'], list):
                previous_ok = False
            for row in previous['active_incidents']:
                if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{24}', row['incident_id']):
                    previous_ok = False
                if clock(row['first_seen_at']) > now or not isinstance(row['summary'], str):
                    previous_ok = False
            if not isinstance(previous['ready_models'], list):
                previous_ok = False
            for model_id in previous['ready_models']:
                identifier(model_id)
            if not isinstance(previous['models'], list):
                previous_ok = False
            for model in previous['models']:
                identifier(model['model_id'])
                sha256(model['recipe_sha256'])
            for row in previous['recent_events']:
                if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{24}', row['event_id']):
                    previous_ok = False
                if clock(row['at']) > now or not isinstance(row['summary'], str):
                    previous_ok = False
    except (KeyError, TypeError, ValueError, OverflowError):
        previous_ok = False
    if not previous_ok:
        issues.append(incident('PREVIOUS_STATUS_INVALID', 'controller',
            'The prior operating status is invalid. Alert history needs review.'))
        for model in models:
            model['ready'] = False
            model['blockers'].append('Prior status is invalid.')
    prior = previous if previous_ok and previous is not None else {}
    old = {row['incident_id']: row for row in prior.get('active_incidents', [])
           if isinstance(row, dict) and isinstance(row.get('incident_id'), str)}
    active, events = [], []
    for row in sorted(issues, key=lambda item: item['incident_id']):
        row['first_seen_at'] = old.get(row['incident_id'], {}).get('first_seen_at', at)
        active.append(row)
        if row['incident_id'] not in old:
            events.append({'event_id': digest(['OPENED', row['incident_id'], at])[:24],
                           'kind': 'INCIDENT_OPENED', 'at': at, **row})
    current = {row['incident_id'] for row in active}
    for key, row in sorted(old.items()):
        if key not in current:
            events.append({'event_id': digest(['RECOVERED', key, at])[:24],
                           'kind': 'INCIDENT_RECOVERED', 'at': at, 'incident_id': key,
                           'severity': 'info', 'summary': f"Recovered: {row.get('summary', key)}"})
    ready = [row['model_id'] for row in models if row['ready']]
    old_ready = prior.get('ready_models', [])
    old_models = {row['model_id']: row for row in prior.get('models', [])
                  if isinstance(row, dict) and isinstance(row.get('model_id'), str)}
    for model in models:
        before = old_models.get(model['model_id'], {})
        if model['state'] == 'evaluation_due' and (
                before.get('state') != 'evaluation_due' or before.get('recipe_sha256') != model['recipe_sha256']):
            events.append({'event_id': digest(['MODEL_EVALUATION_DUE', model['model_id'], model['recipe_sha256'], at])[:24],
                           'kind': 'MODEL_EVALUATION_DUE', 'at': at, 'severity': 'info',
                           'model_id': model['model_id'], 'recipe_sha256': model['recipe_sha256'],
                           'summary': f"{model['model_id']}: its registered evaluation is due; freeze the endpoint and run its registered scorer."})
    ready_identities = {(row['model_id'], row['recipe_sha256']) for row in models if row['ready']}
    old_ready_identities = {(name, old_models.get(name, {}).get('recipe_sha256')) for name in old_ready}
    for model_id, recipe in sorted(ready_identities - old_ready_identities):
        events.append({'event_id': digest(['MODEL_READY', model_id, recipe, at])[:24],
                       'kind': 'MODEL_READY', 'at': at, 'severity': 'info',
                       'model_id': model_id, 'recipe_sha256': recipe,
                       'summary': f'{model_id}: recorded qualification and required collection checks pass; shadow review is available.'})
    for model_id in sorted(set(old_ready) - set(ready)):
        events.append({'event_id': digest(['MODEL_NOT_READY', model_id, at])[:24],
                       'kind': 'MODEL_NOT_READY', 'at': at, 'severity': 'warning',
                       'summary': f'{model_id}: model eligibility is now blocked.'})
    data_status = 'DATA_DEGRADED' if active else 'DATA_HEALTHY'
    model_status = 'MODEL_READY' if ready else 'MODEL_NOT_READY'
    summary = 'Collection checks pass.' if not active else f'{len(active)} operating issue(s) need attention.'
    summary += (' A prospectively qualified model is available for shadow review.' if ready else
                ' No model is eligible for validated shadow signals.' if models else
                ' No prospectively validated model is registered.')
    for event in events:
        event['run_id'] = health.get('run_id') if health_ok else None
    return {'schema': STATUS_SCHEMA, 'generated_at': at,
            'policy_id': policy.get('policy_id') if policy_ok else None,
            'run_id': health.get('run_id') if health_ok else None,
            'data_status': data_status, 'model_status': model_status,
            'mode': 'research-shadow', 'actionable_bet_notifications': False,
            'summary': summary, 'sources': sources, 'models': models, 'ready_models': ready,
            'active_incidents': active, 'new_events': events,
            'recent_events': [event for event in prior.get('recent_events', []) + events
                              if clock(event['at']) >= now - timedelta(hours=48)]}


def write_result(status, output, decisions):
    """Both destinations must be new; callers publish a latest pointer separately."""
    output, decisions = Path(output), Path(decisions)
    if output.resolve() == decisions.resolve() or output.exists() or decisions.exists():
        raise FileExistsError('status and decision destinations must both be new and distinct')
    for path in (output, decisions):
        path.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(status, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    with decisions.open('x') as stream:
        for event in status['new_events']:
            stream.write(canonical(event).decode() + '\n')


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {'schema': 'unreadable-input'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--previous')
    parser.add_argument('--now', help='explicit UTC/offset clock, primarily for reproducible tests')
    parser.add_argument('--output', required=True)
    parser.add_argument('--decisions', required=True, help='new append-only transition JSONL')
    args = parser.parse_args()
    status = evaluate(load(args.health), load(args.policy),
                      load(args.previous) if args.previous else None, now=args.now)
    write_result(status, args.output, args.decisions)
    print(status['summary'])
    return 0 if status['data_status'] == 'DATA_HEALTHY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
