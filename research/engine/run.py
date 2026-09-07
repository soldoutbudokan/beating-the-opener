"""Structural research CLI: fit, freeze, evaluate, verify, and reproduce.

Evaluation uses only the registered 2025 frozen quote population. Reproduction
from saved distributions needs no source download or model fitting. Every output
directory is new; an interrupted or completed evaluation is never overwritten.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = '69c2a32e13a024f7dcd01541d42b77b784b13810'
PRIOR_USES = ROOT / 'research/audits/2026-09-process-audit/results.json'


def plain(value):
    if isinstance(value, dict): return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [plain(v) for v in value]
    if hasattr(value, 'item'): return plain(value.item())
    if isinstance(value, Path): return str(value)
    return value


def dump(path, value):
    path = Path(path)
    with path.open('x') as f: json.dump(plain(value), f, indent=2, sort_keys=True, allow_nan=False)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def implementation():
    paths = sorted((ROOT / 'research/engine').glob('*.py')) + [ROOT / 'research/clocks.py', ROOT / 'tools/research.py']
    return [{'path': p.relative_to(ROOT).as_posix(), 'sha256': digest(p)} for p in paths
            if p.name != 'cricket_capture.py']


def timing_checks():
    """Run and record the actual pre-score software gate, on these exact bytes."""
    before = implementation()
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q']
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode or implementation() != before:
        raise ValueError('Pre-score timing tests failed or implementation changed: ' + completed.stdout + completed.stderr)
    return {'status': 'PASS', 'command': command, 'returncode': completed.returncode,
            'implementation': before, 'tests': [
                {'path': p.relative_to(ROOT).as_posix(), 'sha256': digest(p)}
                for p in sorted((ROOT/'tests').glob('test*.py'))],
            'output': completed.stdout + completed.stderr}


def frozen_fit(recipe_path, raw):
    """A recipe cannot be evaluated outside its frozen source/code contract."""
    path = Path(recipe_path)
    record = json.loads((path.parent/'fit.json').read_text())
    recipe = json.loads(path.read_text())
    if (record.get('status') != 'frozen_before_2025' or
            record.get('registration_commit') != REGISTRATION or
            recipe.get('through_season') != 2024):
        raise ValueError('Evaluation requires the registered pre-2025 frozen fit')
    if record.get('recipes', {}).get(str(recipe.get('shrinkage'))) != digest(path):
        raise ValueError('Recipe differs from the frozen fit')
    if record.get('implementation') != implementation():
        raise ValueError('Implementation changed since freezing; freeze before evaluation')
    if record.get('source_manifest_sha256') != digest(Path(raw)/'manifest.json'):
        raise ValueError('Inputs changed since freezing')
    return recipe, record


def reserve_evaluation(recipe_path, recipe, output, attempt):
    """An interrupted evaluation also consumes its durable, non-overwrite lock."""
    if attempt not in (1, 2):
        raise ValueError('Only two registered evaluation attempts are allowed')
    root = Path(recipe_path).parent
    path = root/f'evaluation-r{recipe["shrinkage"]}.json'
    record = {'recipe_sha256': digest(recipe_path), 'recipe_hash': recipe['recipe_hash'],
              'attempt': attempt, 'output': str(Path(output).resolve()),
              'registration_commit': REGISTRATION,
              'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'started'}
    # At most one use of either attempt number in the same frozen fitting batch.
    for previous in root.glob('evaluation-r*.json'):
        if json.loads(previous.read_text()).get('attempt') == attempt:
            raise ValueError('This registered attempt is already reserved')
    dump(path, record)
    return record


def forecast_request(player_id, game_id, at, tip, history, event):
    """Resolve a player side from prior evidence and independent schedule IDs."""
    from .store import ForecastRequest
    def identity(value):
        if value is None or str(value).lower() in ('nan', 'none', '<na>', ''):
            raise ValueError('Upcoming schedule is missing an event team identity')
        return str(value).removesuffix('.0')
    home = identity(event.get('home_id', event.get('home_team_id')))
    away = identity(event.get('away_id', event.get('away_team_id')))
    if home == away:
        raise ValueError('Upcoming schedule has duplicate team identities')
    eligible = [o for o in history if o.event_id != game_id and
                o.available_at < at and o.effective_at < at]
    latest = max(eligible, key=lambda o: (o.effective_at, o.available_at, o.record_id)) if eligible else None
    team = str(latest.payload['team_id']) if latest is not None else None
    reason = None
    if team not in (home, away):
        reason = 'no_prior_player_identity' if latest is None else 'prior_team_outside_scheduled_matchup'
        # A player's historical rate can still be used with neutral league
        # context. Do not guess a trade destination from the target final box.
        team, opponent = 'unknown-team', 'unknown-opponent'
    else:
        opponent = away if team == home else home
    return ForecastRequest(player_id, game_id, team, opponent, 2025, tip, at), reason


def load_observations(raw, hours=8):
    from . import sources
    data = sources.load_historical(raw)
    obs = sources.observations(data, availability_hours=hours)
    return data, obs


def validation(observations, recipe, season):
    from .model import StructuralModel, training_examples, count_probability, MARKETS
    from .scoring import discrete_crps
    model = StructuralModel(observations, recipe)
    minute_loss, absolute, count = [], [], {m: [] for m in MARKETS}
    for request, outcome in training_examples(observations, season):
        f = model.predict(request, include_manifest=False)
        actual_minutes = float(outcome.payload['minutes'])
        values = [0.] + f['minutes_values']
        weights = [f['p_dnp']] + [(1-f['p_dnp'])*p for p in f['minutes_probs']]
        minute_loss.append(discrete_crps(values, weights, actual_minutes))
        absolute.append(abs(sum(v*p for v,p in zip(values, weights))-actual_minutes))
        if actual_minutes > 0:
            for market in MARKETS:
                count[market].append(-math.log(max(1e-12, count_probability(f, market, outcome.payload['counts'][market]))))
    return {'season': season, 'n': len(minute_loss), 'minutes_crps': sum(minute_loss)/len(minute_loss),
            'minutes_mae': sum(absolute)/len(absolute),
            'count_nll': {m: sum(v)/len(v) for m,v in count.items()},
            'population': 'observed source box appearances including explicit DNP; incomplete pregame roster coverage'}


def fit_stage(raw, output):
    from .model import fit
    out = new_directory(output)
    data, obs = load_observations(raw)
    reports, recipes = {}, {}
    for shrinkage in (1, 2):
        print(f'Fitting registered shrinkage {shrinkage} through 2022', flush=True)
        first = fit(obs, through_season=2022, shrinkage=shrinkage)
        v2023 = validation(obs, first, 2023)
        print(f'Validating shrinkage {shrinkage} chronologically in 2024', flush=True)
        through2023 = fit(obs, through_season=2023, shrinkage=shrinkage)
        v2024 = validation(obs, through2023, 2024)
        final = fit(obs, through_season=2024, shrinkage=shrinkage)
        reports[str(shrinkage)] = {'validation_2023': v2023, 'validation_2024': v2024,
                                  'initial_recipe': first, 'validation_recipe': through2023}
        recipes[str(shrinkage)] = final
        dump(out/f'recipe_{shrinkage}.json', final)
        dump(out/f'validation_{shrinkage}.json', reports[str(shrinkage)])
    # Minutes CRPS is the primary market-free selection criterion; count NLL
    # breaks exact ties, then stronger shrinkage. No 2025 score selects a recipe.
    selected = min((1,2), key=lambda n: (reports[str(n)]['validation_2023']['minutes_crps'],
        sum(reports[str(n)]['validation_2023']['count_nll'].values()), -n))
    dump(out/'fit.json', {'registration_commit': REGISTRATION, 'selected_shrinkage': selected,
        'selection_rule': '2023 minutes CRPS, total count NLL tie break, stronger shrinkage exact tie',
        'quality': data.quality, 'source_manifest_sha256': digest(Path(raw)/'manifest.json'),
        'recipes': {str(n): digest(out/f'recipe_{n}.json') for n in (1,2)},
        'implementation': implementation(), 'status': 'frozen_before_2025'})
    print(json.dumps({'status':'frozen_before_2025', 'selected_shrinkage':selected, 'output':str(out)}), flush=True)


def make_forecasts(raw, recipe, hours=8):
    from scipy.stats import nbinom, poisson
    from . import sources
    from .model import StructuralModel, price_forecast
    from .quotes import QuoteArchive, score_quote
    from .store import payload_digest
    data, obs = load_observations(raw, hours)
    engine = StructuralModel(obs, recipe)
    # Sensitivity changes source delay only. The original PR2 24h cohort had
    # three fewer rows; using it here would change the registered comparison.
    quotes = sources.load_frozen_quotes(scenario='8h')
    old_fit = json.loads((sources.INPUTS/'fit_report.json').read_text())['distribution']
    archives = QuoteArchive(ROOT/'wnba/data/raw/bp')
    players, outcomes = defaultdict(list), {}
    for o in obs:
        if o.payload.get('record_type') == 'player_box':
            players[o.entity_id].append(o)
            outcomes[(o.event_id,o.entity_id)] = o
    times = {p: [o.available_at for o in values] for p,values in players.items()}
    # Upcoming schedule provides event teams. Target final player/team fields
    # from PR2 are retained only as historical grading data, never model inputs.
    events = {str(r['game_id']): r for r in data.schedule.to_dict('records')}
    forecasts, cached, input_records, input_ids = [], {}, [], {}
    implementation_hash = payload_digest({'implementation': implementation()})
    model_hash = payload_digest({'recipe_hash': recipe['recipe_hash'], 'implementation_hash': implementation_hash})
    for number, q in enumerate(quotes.to_dict('records')):
        row = score_quote(q)
        pid, gid = row['player_id'], row['game_id']
        at, tip = q['forecast_at'].to_pydatetime(), q['tip_at'].to_pydatetime()
        past = players[pid][:bisect_left(times.get(pid, []), at)]
        past = [o for o in past if o.event_id != gid and o.effective_at < at]
        event = events[gid]
        request, identity_reason = forecast_request(pid, gid, at, tip, past, event)
        team = request.team_id
        identity_fallback = identity_reason is not None
        key = (pid,gid,at)
        if key not in cached:
            f = engine.predict(request)
            entries = f['input_manifest'].pop('observations')
            references = []
            for entry in entries:
                identity = (entry['source_id'], entry['record_id'], entry['available_at'], entry['payload_hash'])
                if identity not in input_ids:
                    input_ids[identity] = len(input_records)
                    input_records.append(entry)
                references.append(input_ids[identity])
            f['input_manifest']['observation_ids'] = references
            f['input_manifest_encoding'] = 'shared-record-ids-v1'
            f['implementation_hash'] = implementation_hash
            cached[key] = f
        f = cached[key]
        actual = None if bool(q['void']) else int(q['actual'])
        o = outcomes.get((gid,pid))
        if o is None:
            raise ValueError(f'Missing actual minutes for frozen player {pid}/{gid}; do not impute DNP')
        actual_minutes = float(o.payload['minutes'])
        if bool(q['void']) != (actual_minutes == 0):
            raise ValueError('Frozen void and source participation disagree')
        if actual is not None and actual != o.payload['counts'][q['market']]:
            raise ValueError('Frozen count and pinned source outcome disagree')
        priced = price_forecast(f,q['market'],q['open_line'],actual)
        alpha = old_fit[q['market']]['box_ridge']['alpha']
        mean = float(q['box_ridge_mean'])
        baseline_dist = poisson(mean) if alpha <= 0 else nbinom(1/alpha,1/(1+alpha*mean))
        baseline_mass = None if actual is None else float(baseline_dist.pmf(actual))
        ref = archives.reference(q)
        row.update(priced, void=bool(q['void']), actual=actual, actual_minutes=actual_minutes,
                   p_dnp=f['p_dnp'], minutes_values=f['minutes_values'], minutes_probs=f['minutes_probs'],
                   baseline_count_p_actual=baseline_mass, close_p_over_nonpush=ref['p_over_nonpush'],
                   close_reference=ref, distribution=f, identity_fallback=identity_fallback,
                   identity_fallback_reason=identity_reason,
                   baseline_distribution={'mean': mean, 'alpha': alpha},
                   predicted_team_id=team, model_hash=model_hash, implementation_hash=implementation_hash,
                   run_id=f'structural-{hours}h-r{recipe["shrinkage"]}')
        forecasts.append(row)
        if number and number%1000==0: print(f'Generated {number}/7604 quotes ({hours}h)',flush=True)
    return forecasts, {'quality':data.quality,'quote_sha256':quotes.attrs['source_sha256'],
                       'source_manifest_sha256':digest(Path(raw)/'manifest.json'),
                       'quote_archive':archives.manifest,'timing':'assumed','availability_hours':hours,
                       'baseline_fit_report_sha256': digest(sources.INPUTS/'fit_report.json'),
                       'frozen_comparator': 'PR2 box regression 8h held fixed in 24h sensitivity',
                       'input_records': input_records}


def saved_distributions(rows, recipe=None, input_records=None):
    from .model import price_forecast
    from .store import payload_digest
    from scipy.stats import nbinom, poisson
    def stamp(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Saved source clock lacks timezone')
        return parsed
    implementation_hash = payload_digest({'implementation': implementation()})
    expected_model_hash = payload_digest({'recipe_hash': recipe['recipe_hash'], 'implementation_hash': implementation_hash}) if recipe else None
    for row in rows:
        priced = price_forecast(row['distribution'],row['market'],row['line'],row['actual'])
        for key,value in priced.items():
            if value is None:
                if row[key] is not None: raise ValueError('Void saved distribution differs')
            elif not math.isclose(row[key],value,rel_tol=0,abs_tol=1e-12):
                raise ValueError('Saved distribution differs: '+key)
        f=row['distribution']
        for key in ('p_dnp','minutes_values','minutes_probs'):
            if row[key]!=f[key]: raise ValueError('Saved minute distribution differs')
        if recipe is not None and f['recipe_hash'] != recipe['recipe_hash']:
            raise ValueError('Saved forecast differs from frozen recipe')
        if recipe is not None and (row['model_hash'] != expected_model_hash or
                row['implementation_hash'] != implementation_hash or f['implementation_hash'] != implementation_hash):
            raise ValueError('Saved forecast model identity differs from code and recipe')
        request, manifest = f['request'], f['input_manifest']
        if 'observation_ids' in manifest:
            if input_records is None:
                raise ValueError('Shared saved source records are missing')
            ids = manifest['observation_ids']
            if any(type(i) is not int or not 0 <= i < len(input_records) for i in ids):
                raise ValueError('Saved source record reference is invalid')
            manifest = {'schema': manifest['schema'], 'request': manifest['request'],
                        'observations': [input_records[i] for i in ids]}
        if (request['entity_id'] != row['player_id'] or request['event_id'] != row['game_id'] or
                stamp(request['as_of']) != stamp(row['as_of']) or
                stamp(request['tip_at']) != stamp(row['tip_at']) or request['season'] != 2025):
            raise ValueError('Saved forecast request differs from quote identity/clock')
        if manifest['request'] != request or payload_digest(manifest) != f['input_manifest_hash']:
            raise ValueError('Saved input manifest hash or request differs')
        for source in manifest['observations']:
            if source['season'] >= 2026 or stamp(source['available_at']) >= stamp(request['as_of']):
                raise ValueError('Saved forecast consumes future/protected evidence')
            if source['kind'] in ('historical_outcome', 'final_roster') and (
                    source['event_id'] == request['event_id'] or
                    stamp(source['effective_at']) >= stamp(request['as_of'])):
                raise ValueError('Saved forecast consumes target-game outcome/roster')
        baseline = row['baseline_distribution']
        mu, alpha = baseline['mean'], baseline['alpha']
        dist = poisson(mu) if alpha <= 0 else nbinom(1/alpha,1/(1+alpha*mu))
        mass = None if row['actual'] is None else float(dist.pmf(row['actual']))
        if (mass is None) != (row['baseline_count_p_actual'] is None) or (
                mass is not None and not math.isclose(mass, row['baseline_count_p_actual'], rel_tol=0, abs_tol=1e-12)):
            raise ValueError('Saved baseline count probability differs')
    return {'status':'PASS','distributions':len(rows),'tolerance':1e-12}


def frozen_population(rows):
    """Check identities, prices, comparator and labels against pinned PR2 bytes."""
    from .sources import load_frozen_quotes
    from .quotes import score_quote
    expected = {}
    for original in load_frozen_quotes(scenario='8h').to_dict('records'):
        row = score_quote(original)
        row.update(void=bool(original['void']), actual=None if original['void'] else int(original['actual']))
        expected[row['quote_id']] = row
    if len(rows) != 7604 or len({r['quote_id'] for r in rows}) != 7604 or set(expected) != {r['quote_id'] for r in rows}:
        raise ValueError('Saved rows differ from frozen quote IDs')
    for row in rows:
        for key, value in expected[row['quote_id']].items():
            if row.get(key) != value:
                raise ValueError('Frozen quote field changed: ' + key)
    return {'status': 'PASS', 'quote_ids': len(expected)}


def derived_results(models, attempt, timing, prior_uses):
    """One registered gate derivation shared by evaluation and verification."""
    from .scoring import evaluate_gates
    if set(models) != {'8h', '24h'}:
        raise ValueError('Both fixed timing scenarios are required')
    if attempt not in (1, 2) or not prior_uses:
        raise ValueError('Attempt and prior-development disclosures are required')
    if (timing.get('status') != 'PASS' or timing.get('returncode') != 0 or
            not timing.get('tests') or timing.get('implementation') != implementation()):
        raise ValueError('Missing or stale successful timing test receipt')
    checked=evaluate_gates(models['8h']['scores'],calibration_overall_limit=.015,calibration_bucket_limit=.025,
        log_loss_limit=.687662,expected_settled=7473,expected_markets=('points','rebounds','assists','threes'),
        close_gain_tripwire=.001,close_t_tripwire=3)
    statuses=checked['gates']
    populations = all(model['scores']['quotes'] == 7604 and
                      model['scores']['settled_nonpush'] == 7473 for model in models.values())
    gates=[{'id':'TIMING','status':'PASS','reason':'Recorded source/model tests passed on the frozen implementation; source availability remains assumed.'},
        {'id':'POPULATION','status':'PASS' if populations else 'FAIL','reason':'Both scenarios require 7604 frozen quotes and 7473 played non-push rows; delay changes no quote identities.'},
        {'id':'CALIBRATION','status':'PASS' if statuses['calibration'] else 'FAIL','reason':f'Overall gap {models["8h"]["scores"]["calibration"]["gap"]:.6f}; all model-mean buckets published.'},
        {'id':'COUNT','status':'PASS' if statuses['count_nll_every_market'] else 'FAIL','reason':'Every market must improve count likelihood on the same rows.'},
        {'id':'OPENER','status':'PASS' if statuses['registered_log_loss'] else 'FAIL','reason':f'Log loss {models["8h"]["scores"]["log_loss"]:.6f}, required <=0.687662.'},
        {'id':'CLOSE','status':'PASS' if statuses['close_tripwire_clear'] else 'BLOCKED','reason':'Same-line pre-tip reference coverage and paired interval are saved; a tripwire requires investigation.'},
        {'id':'RECEIPT','status':'PASS','reason':'Saved distributions, rows, scores and selection arithmetic reproduce without refitting.'}]
    passed=all(g['status']=='PASS' for g in gates)
    reason='All registered structural gates pass.' if passed else 'Structural model does not clear all registered gates; retain failed results and honor the two-attempt budget.'
    return {'schema_version':'structural-v1','research_year':2025,'evidence_kind':'reused-development',
        'protected_arms_scored':0,'attempt':attempt,'status':'PASS' if passed else 'FAIL',
        'decision':'prepare independent test' if passed else 'stop','reason':reason,'gates':gates,
        'models':{k:v['scores'] for k,v in models.items()},'prior_2025_uses':prior_uses}


def evaluate_stage(raw, recipe_path, output, attempt=1):
    from .scoring import build_report
    recipe, fit_record = frozen_fit(recipe_path, raw)
    timing = timing_checks()
    out = new_directory(output)
    lock = reserve_evaluation(recipe_path, recipe, out, attempt)
    dump(out/'recipe.json',recipe)
    dump(out/'timing.json', timing)
    dump(out/'evaluation_lock.json', lock)
    dump(out/'fit.json', fit_record)
    all_rows,models,sources={}, {}, {}
    for hours in (8,24):
        rows,manifest=make_forecasts(raw,recipe,hours)
        frozen_population(rows)
        input_records = manifest.pop('input_records')
        input_path = out/f'input_records_{hours}h.json.gz'
        with input_path.open('xb') as f:
            f.write(gzip.compress(json.dumps(input_records, sort_keys=True, allow_nan=False).encode(), mtime=0))
        manifest['input_records_file'] = input_path.name
        manifest['input_records_sha256'] = digest(input_path)
        saved_distributions(rows, recipe, input_records)
        label=f'{hours}h';all_rows[label]=rows;sources[label]=manifest
        models[label]=build_report(rows)
    rawbytes=''.join(json.dumps({'scenario':key,'row':r},sort_keys=True,allow_nan=False)+'\n'
                     for key,rows in all_rows.items() for r in rows).encode()
    with (out/'forecasts.jsonl.gz').open('xb') as f:f.write(gzip.compress(rawbytes,mtime=0))
    dump(out/'scores.json',models);dump(out/'source_manifest.json',sources)
    result=derived_results(models, attempt, timing, json.loads(PRIOR_USES.read_text())['prior_2025_uses'])
    dump(out/'results.json',result)
    # A complete receipt is written only after independent arithmetic and
    # distribution verification succeeds. Interrupted failures keep the lock.
    verification = verify_stage(out, check_receipt=False)
    dump(out/'verification.json', verification)
    receipt={'schema_version':'structural-receipt-v1','status':'complete','evidence_kind':'reused-development',
        'registration_commit':REGISTRATION,'results_sha256':digest(out/'results.json'),
        'implementation':implementation(),'artifacts':[{'path':p.name,'sha256':digest(p)} for p in sorted(out.iterdir()) if p.name!='results.json']}
    dump(out/'receipt.json',receipt)
    from .review import review_structural,render_structural
    with (out/'decision.md').open('x') as f:f.write(render_structural(review_structural(out/'results.json',out/'receipt.json')))
    print(json.dumps({'status':result['status'],'calibration':next(g['status'] for g in result['gates'] if g['id']=='CALIBRATION'),'run':str(out)}),flush=True)


def verify_stage(run,output=None, *, check_receipt=True):
    from .scoring import verify_saved_report
    from .review import review_structural
    root=Path(run)
    if check_receipt:
        review_structural(root/'results.json',root/'receipt.json')
    groups=defaultdict(list)
    for line in gzip.decompress((root/'forecasts.jsonl.gz').read_bytes()).splitlines():
        record=json.loads(line);groups[record['scenario']].append(record['row'])
    reports=json.loads((root/'scores.json').read_text())
    if set(groups) != {'8h', '24h'} or set(reports) != set(groups):
        raise ValueError('Saved scenarios are incomplete')
    if {r['quote_id'] for r in groups['8h']} != {r['quote_id'] for r in groups['24h']}:
        raise ValueError('Sensitivity changed the frozen quote population')
    recipe=json.loads((root/'recipe.json').read_text())
    inputs={k:json.loads(gzip.decompress((root/f'input_records_{k}.json.gz').read_bytes())) for k in groups}
    verification={k:{'scores':verify_saved_report(rows,reports[k]),
                     'distributions':saved_distributions(rows, recipe, inputs[k]),
                     'population': frozen_population(rows)} for k,rows in groups.items()}
    result=json.loads((root/'results.json').read_text())
    timing=json.loads((root/'timing.json').read_text())
    prior_uses=json.loads(PRIOR_USES.read_text())['prior_2025_uses']
    if result != derived_results(reports, result['attempt'], timing, prior_uses):
        raise ValueError('Saved gates, decision or summary differs from score reproduction')
    lock=json.loads((root/'evaluation_lock.json').read_text())
    fit_record=json.loads((root/'fit.json').read_text())
    if (lock['attempt'] != result['attempt'] or lock['recipe_hash'] != recipe['recipe_hash'] or
            lock['recipe_sha256'] != digest(root/'recipe.json') or
            fit_record['recipes'][str(recipe['shrinkage'])] != digest(root/'recipe.json') or
            fit_record['implementation'] != implementation()):
        raise ValueError('Saved evaluation lock or frozen fit differs')
    if output:
        out=new_directory(output);dump(out/'verification.json',verification)
    return verification


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    fit=s.add_parser('fit');fit.add_argument('--raw',type=Path,required=True);fit.add_argument('--output',type=Path,required=True)
    ev=s.add_parser('evaluate');ev.add_argument('--raw',type=Path,required=True);ev.add_argument('--recipe',type=Path,required=True);ev.add_argument('--output',type=Path,required=True);ev.add_argument('--attempt',type=int,choices=(1,2),required=True)
    for name in ('verify','reproduce'):
        q=s.add_parser(name);q.add_argument('--run',type=Path,required=True)
        if name=='reproduce':q.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.command=='fit':fit_stage(a.raw,a.output)
    elif a.command=='evaluate':evaluate_stage(a.raw,a.recipe,a.output,a.attempt)
    else: print(json.dumps(verify_stage(a.run,getattr(a,'output',None)),indent=2))


if __name__=='__main__':main()
