"""Reproduce the registered research experiment in separate, visible stages.

From repository root: python -m wnba.research.rich_context.run prepare|fit|evaluate
Download first with: python -m wnba.research.rich_context.sources --download
The fit stage never scores a 2025 outcome. Evaluation has a one-run receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from . import baseline, benchmark, features, models, sources

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CACHE = HERE.parents[1] / 'data' / 'rich_context' / 'cache'
RESULTS = HERE / 'results'
STAT_KEY = dict(points='poi', rebounds='reb', assists='ass', threes='tpm')


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(plain(data), indent=2, sort_keys=True, allow_nan=False) + '\n')
    tmp.replace(path)


def code_hash():
    h = hashlib.sha256()
    for p in sorted(HERE.glob('*.py')) + [HERE / 'PROTOCOL.md', HERE / 'requirements.txt',
                                         HERE.parents[1] / 'src' / 'talent.py']:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def normalise_ids(frame):
    out = frame.copy()
    for c in ['game_id', 'athlete_id', 'team_id', 'opponent_team_id']:
        if c in out:
            out[c] = out[c].astype('string').str.replace(r'\.0$', '', regex=True)
    return out


def add_interactions(f):
    """Named basketball relationships, all made from prior-state columns."""
    f = f.copy()
    pairs = {
        'context_rim_match': ('pbp_rim_share_ewf', 'opp_pbp_allowed_rim_share_ew'),
        'context_three_match': ('pbp_three_share_ewf', 'opp_pbp_allowed_three_share_ew'),
        'context_rim_accuracy_match': ('pbp_rim_share_ewf', 'opp_pbp_allowed_rim_accuracy_ew'),
        'context_three_rebounds': ('reb_rate_ewf', 'opp_pbp_three_misses_ew'),
        'context_miss_rebounds': ('reb_rate_ewf', 'opp_pbp_fg_misses_ew'),
        'context_assisted_finishing': ('pbp_assisted_make_share_ewf', 'tm_ast_for_ew'),
    }
    for name, (a, b) in pairs.items():
        if a in f and b in f:
            f[name] = f[a] * f[b]
    return f


def feature_lists(frame):
    named = {'gp', 'rest', 'started_ewf', 'minutes_sd_10', 'minutes_change_last',
             'player_state_age_days', 'same_team_as_last_game', 'home',
             'lg_pace_asof', 'lg_pts_against_asof', 'pbp_games', 'pbp_state_age_days'}
    columns = [c for c in frame if
               (c.endswith(('_ewf', '_ews', '_ew')) or c.startswith(('talent_', 'context_')) or c in named
                or (c.startswith('pbp_') and c.endswith(('_age_days','_valid_games'))))
               and not c.endswith('_source_at')]
    rich = [c for c in columns if 'pbp' in c or c.startswith('context_')]
    box = [c for c in columns if c not in rich]
    models.validate_feature_names(columns)
    return sorted(box), sorted(box + rich)


def eligible(frame):
    """Eligibility depends on known history, never a target game's result."""
    required = ['min_ewf', 'min_ews', 'tm_pace_ew', 'opp_pace_ew',
                'lg_pace_asof', 'lg_pts_against_asof']
    valid = frame[required].notna().all(axis=1) & frame.gp.ge(4)
    valid &= frame.same_team_as_last_game.eq(1)
    for short in STAT_KEY.values():
        valid &= frame[f'{short}_rate_ewf'].notna()
    return valid


def training_queries(box, states):
    b = normalise_ids(box)
    b['tip_at'] = pd.to_datetime(b.game_date_time, utc=True, errors='coerce')
    b['game_date'] = pd.to_datetime(b.game_date).dt.tz_localize(None)
    b = b[b.game_id.isin(states.player.game_id) & b.minutes.gt(0)].copy()
    b = b[(b.game_date >= '2010-01-01') & (b.game_date < '2026-01-01')]
    b['forecast_at'] = b.tip_at - pd.Timedelta(hours=24)
    b['home'] = b.home_away.eq('home').astype(float)
    qcols = ['game_id', 'athlete_id', 'team_id', 'opponent_team_id', 'tip_at',
             'forecast_at', 'home', 'game_date', 'athlete_display_name']
    out = add_interactions(features.query_features(states, b[qcols]))
    # Outcomes are joined only after state retrieval and are outside the
    # explicit model feature list. Counts are conditional on participation.
    outcomes = b[['game_id', 'athlete_id', 'minutes'] + list(baseline.RAW.values())]
    out = out.merge(outcomes, on=['game_id', 'athlete_id'], how='left', validate='one_to_one')
    out['eligible'] = eligible(out)
    return out


def prepare(cache_dir=CACHE):
    started_hash = code_hash()
    cache_dir.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    box, team, pbp, schedule = sources.load_sources()
    print(f'Loaded {len(box):,} player rows and {len(pbp):,} events.', flush=True)
    states = features.build_state_tables(box, team, pbp, schedule)
    talent, talent_meta = baseline.build_talent_snapshots(
        box, cutoff='2015-01-01', eligible_game_ids=states.player.game_id)
    states.player = states.player.merge(talent, on=['athlete_id', 'game_id'],
                                        how='left', validate='one_to_one')
    frame = training_queries(box, states)
    box_cols, rich_cols = feature_lists(frame)
    save_json(RESULTS / 'feature_list.json', {'box_features': box_cols,
                                            'rich_features': rich_cols,
                                            'extra_information_columns': sorted(set(rich_cols)-set(box_cols))})
    save_json(RESULTS / 'feature_quality.json', states.coverage)
    save_json(RESULTS / 'talent_settings.json', talent_meta)
    summary = frame.groupby(frame.game_date.dt.year).agg(
        player_games=('game_id', 'size'), eligible=('eligible', 'sum'))
    save_json(RESULTS / 'training_coverage.json', summary.reset_index().to_dict(orient='records'))
    payload = {'frame': frame, 'states': states, 'talent': talent,
               'box_columns': box_cols, 'rich_columns': rich_cols,
               'source_manifest_sha256': sources.file_sha256(sources.DEFAULT_RAW/'manifest.json'),
               'implementation_sha256': started_hash}
    if started_hash != code_hash():
        raise RuntimeError('Implementation changed while features were being prepared')
    with (cache_dir/'prepared.pkl').open('wb') as f:
        pickle.dump(payload, f, protocol=5)
    print(summary.to_string(), flush=True)
    print(f'Prepared {len(box_cols)} box predictors; {len(rich_cols)-len(box_cols)} richer predictors.', flush=True)


def fit(cache_dir=CACHE):
    with (cache_dir/'prepared.pkl').open('rb') as f:
        prepared = pickle.load(f)
    if prepared.get('implementation_sha256') != code_hash():
        raise RuntimeError('Prepared features do not match the current implementation; prepare again before fitting')
    if prepared['source_manifest_sha256'] != sources.file_sha256(sources.DEFAULT_RAW/'manifest.json'):
        raise RuntimeError('Source manifest changed after feature preparation')
    data = prepared['frame']
    data = data[data.eligible & (data.game_date < '2025-01-01')].reset_index(drop=True)
    if data.game_date.dt.year.max() > 2024:
        raise AssertionError('2025 outcomes entered fitting')
    artifact = {'models': {}, 'calibration': {}, 'baseline': {},
                'box_columns': prepared['box_columns'], 'rich_columns': prepared['rich_columns']}
    report = {'selection_year': 2023, 'validation_and_distribution_year': 2024,
              'tuning': {}, 'validation_2024': {}, 'distribution': {}, 'ridge_effects': {}}
    bases = {}
    for year in [2023, 2024, 2025]:
        cal = baseline.fit_baseline(data, cutoff=f'{year}-01-01')
        artifact['baseline'][year] = cal
        bases[year] = baseline.predict_markets(data, cal)
    period = data.game_date.dt.year
    train = (period >= 2015) & (period <= 2022)
    select = period == 2023
    validation = period == 2024
    final_train = period >= 2015
    for market in models.MARKETS:
        target = data[models.TARGETS[market]].to_numpy(float)
        artifact['models'][market], artifact['calibration'][market] = {}, {}
        report['tuning'][market], report['validation_2024'][market] = {}, {}
        report['distribution'][market], report['ridge_effects'][market] = {}, {}
        for family in models.MODEL_NAMES:
            columns = prepared['box_columns'] if family in ('incumbent', 'box_ridge') else prepared['rich_columns']
            base = bases[2023][market].to_numpy(float)
            chosen, tuning = models.tune_mean_model(family, columns, data.loc[train], target[train], base[train],
                                                    data.loc[select], target[select], base[select])
            report['tuning'][market][family] = {'chosen': chosen, 'grid': tuning}
            base = bases[2024][market].to_numpy(float)
            train_to_2023 = final_train & (period < 2024)
            model = models.fit_mean_model(family, columns, data.loc[train_to_2023], target[train_to_2023],
                                           base[train_to_2023], chosen)
            pred = model.predict(data.loc[validation], base[validation])
            report['validation_2024'][market][family] = models.count_metrics(target[validation], pred)
            dist = models.fit_distribution(target[validation], pred)
            report['distribution'][market][family] = dist
            artifact['calibration'][market][family] = dist
            base = bases[2025][market].to_numpy(float)
            final = models.fit_mean_model(family, columns, data.loc[final_train], target[final_train],
                                           base[final_train], chosen)
            artifact['models'][market][family] = final
            if family.endswith('ridge'):
                coefficients = final.estimator[-1].coef_
                names = final.estimator[0].get_feature_names_out(columns + ['incumbent_mean'])
                ordered = sorted(zip(names, coefficients), key=lambda p: abs(p[1]), reverse=True)
                report['ridge_effects'][market][family] = [dict(feature=n, effect_per_training_sd=float(c)) for n,c in ordered[:20]]
            print(f'{market:9} {family:12} chosen={chosen} 2024deviance={report["validation_2024"][market][family]["poisson_deviance"]:.5f}', flush=True)
    artifact['implementation_sha256'] = code_hash()
    artifact['source_manifest_sha256'] = prepared['source_manifest_sha256']
    with (cache_dir/'fitted.pkl').open('wb') as f:
        pickle.dump(artifact, f, protocol=5)
    save_json(RESULTS/'fit_report.json', report)
    print('Fit and pre-2025 calibration complete. No 2025 model score read.', flush=True)


def _predict_quote_rows(frame, quotes, artifact):
    base = baseline.predict_markets(frame, artifact['baseline'][2025])
    all_predictions = {}
    for family in models.MODEL_NAMES:
        pieces = []
        for market in models.MARKETS:
            mask = quotes.market.eq(market)
            if not mask.any():
                continue
            mean = artifact['models'][market][family].predict(frame.loc[mask], base.loc[mask, market])
            p = models.probabilities(mean, quotes.loc[mask, 'open_line'], artifact['calibration'][market][family])
            p.index = quotes.index[mask]
            p['uncalibrated_mean'] = mean
            p['count_nll'] = models.count_nll(quotes.loc[mask,'actual'], mean,
                                             artifact['calibration'][market][family])
            pieces.append(p)
        all_predictions[family] = pd.concat(pieces).sort_index()
    return all_predictions


def score_quotes(quotes, predictions, label):
    valid = ~quotes.void & quotes.actual.notna()
    dates = quotes.date
    market_ll, market_bs = models.proper_scores(quotes.actual, quotes.open_line, quotes.p_open)
    result = {'label': label, 'quotes': len(quotes), 'settled_quotes': int(valid.sum()),
              'void_quotes': int(quotes.void.sum()), 'markets': quotes.market.value_counts().to_dict(),
              'games': int(quotes.game_id.nunique()), 'dates': int(dates.nunique()),
              'market': {'log_loss': float(np.nanmean(market_ll[valid])),
                         'brier': float(np.nanmean(market_bs[valid]))}, 'models': {}, 'comparisons': {}}
    per_rows = quotes.copy()
    losses = {}
    for family, p in predictions.items():
        ll, bs = models.proper_scores(quotes.actual, quotes.open_line, p.p_over_nonpush)
        ll[~valid], bs[~valid] = np.nan, np.nan
        losses[family] = ll
        item = {'log_loss': float(np.nanmean(ll)), 'brier': float(np.nanmean(bs)),
                'count_nll': float(p.loc[valid,'count_nll'].mean()),
                'versus_opener': models.clustered_ratio(dates, ll-market_ll),
                'by_market': {}, 'economics': {}, 'calibration': []}
        for market in models.MARKETS:
            m = quotes.market.eq(market).to_numpy() & valid.to_numpy()
            item['by_market'][market] = {'n': int(m.sum()), 'log_loss': float(np.nanmean(ll[m])),
                                        'brier': float(np.nanmean(bs[m])),
                                        'versus_opener': models.clustered_ratio(dates[m], (ll-market_ll)[m])}
        observed_over = quotes.actual > quotes.open_line
        nonpush = valid & quotes.actual.ne(quotes.open_line)
        for lo,hi in zip(np.arange(0,1,.1), np.arange(.1,1.1,.1)):
            m = nonpush & p.p_over_nonpush.ge(lo) & p.p_over_nonpush.lt(hi)
            if m.any():
                item['calibration'].append({'lo': float(lo), 'hi': float(hi), 'n': int(m.sum()),
                                            'predicted': float(p.loc[m,'p_over_nonpush'].mean()),
                                            'observed': float(observed_over[m].mean())})
        for threshold in [.05,.10]:
            bets = models.economic_rows(quotes, p, threshold)
            if len(bets):
                settled = ~bets.is_void & ~bets.is_unresolved
                economics = {'bets': len(bets), 'wins': int(bets.profit.gt(0).sum()),
                             'losses': int(bets.profit.lt(0).sum()), 'pushes': int(bets.is_push.sum()),
                             'voids': int(bets.is_void.sum()), 'unresolved': int(bets.is_unresolved.sum()),
                             'stake': float(bets.settled_stake.sum()),
                             'profit': float(bets.profit.sum()),
                             'matched_claimed_profit': float(bets.loc[settled,'claimed_ev'].sum()),
                             'over_bets': int(bets.side.eq('over').sum()),
                             'roi': models.clustered_ratio(bets.date, bets.profit, bets.settled_stake)}
            else:
                economics = {'bets': 0, 'profit': 0., 'stake': 0., 'roi': {'estimate': None, 'ci95':[None,None]}}
            item['economics'][str(threshold)] = economics
            bets.to_csv(RESULTS/f'bets_{label}_{family}_{int(threshold*100)}.csv',index=False)
        result['models'][family] = item
        for col in ['mean','median','p10','p90','p_over','p_under','p_push','p_over_nonpush']:
            per_rows[f'{family}_{col}'] = p[col].to_numpy()
    for a,b in [('rich_ridge','box_ridge'),('rich_boost','rich_ridge'),('box_ridge','incumbent'),('rich_boost','incumbent')]:
        result['comparisons'][f'{a}_minus_{b}'] = models.clustered_ratio(dates,losses[a]-losses[b])
    per_rows.to_csv(RESULTS/f'forecasts_{label}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    per_rows.head(80).to_csv(RESULTS/f'forecast_examples_{label}.csv',index=False)
    return result


def common_coverage_scores(quotes, predictions, mask):
    """Paired information comparisons where all three PBP families have history."""
    valid = mask & ~quotes.void & quotes.actual.notna()
    losses, scores = {}, {}
    for family,p in predictions.items():
        ll,_=models.proper_scores(quotes.actual,quotes.open_line,p.p_over_nonpush)
        ll[~valid]=np.nan
        losses[family]=ll
        scores[family]=float(np.nanmean(ll)) if valid.any() else None
    return {'quotes':int(mask.sum()), 'settled_quotes':int(valid.sum()),
            'log_loss':scores,
            'information_test':models.clustered_ratio(quotes.date,losses['rich_ridge']-losses['box_ridge']),
            'flexibility_test':models.clustered_ratio(quotes.date,losses['rich_boost']-losses['rich_ridge'])}


def evaluate(cache_dir=CACHE):
    receipt = RESULTS/'evaluation_receipt.json'
    if receipt.exists():
        raise RuntimeError('2025 already evaluated: preserve this receipt; any new evaluation is a separately documented experiment.')
    with (cache_dir/'fitted.pkl').open('rb') as f:
        artifact = pickle.load(f)
    if artifact['implementation_sha256'] != code_hash():
        raise RuntimeError('Implementation changed since fitting; review and record changes before evaluation.')
    with (cache_dir/'prepared.pkl').open('rb') as f:
        prepared = pickle.load(f)
    if prepared['source_manifest_sha256'] != artifact['source_manifest_sha256']:
        raise RuntimeError('Prepared and fitted source manifests disagree')
    box, team, pbp, schedule = sources.load_sources()
    openers, coverage = benchmark.load_openers(HERE.parents[1]/'data'/'raw'/'bp')
    quoted, matching = benchmark.match_openers(openers, box)
    quoted = quoted[quoted.market.isin(models.MARKETS)].copy()
    quoted = quoted[quoted.matched].reset_index(drop=True)
    quoted = quoted.rename(columns={'open_over_cost':'open_over','open_under_cost':'open_under'})
    quoted['game_date'] = pd.to_datetime(quoted.date)
    qcols = ['game_id','athlete_id','team_id','opponent_team_id','tip_at','forecast_at','home','game_date']
    save_json(RESULTS/'benchmark_coverage.json', coverage)
    save_json(RESULTS/'benchmark_matching.json', matching)
    run = {'status': 'running', 'started_at': pd.Timestamp.now(tz='UTC'),
           'implementation_sha256': code_hash(), 'research_year':2025,
           'source_manifest_sha256': artifact['source_manifest_sha256']}
    save_json(receipt, run)
    results, sensitivity_rows = {}, {}
    for lag in [8,24]:
        if lag == 8:
            states = prepared['states']
        else:
            states = features.build_state_tables(box, team, pbp, schedule, availability_hours=lag)
            states.player = states.player.merge(prepared['talent'],on=['athlete_id','game_id'],how='left',validate='one_to_one')
        frame = add_interactions(features.query_features(states, quoted[qcols]))
        ok = eligible(frame)
        q = quoted.loc[ok].reset_index(drop=True)
        f = frame.loc[ok].reset_index(drop=True)
        predictions = _predict_quote_rows(f,q,artifact)
        has_rich = f[[f'pbp_{family}_valid_games' for family in ('shot','assist','rebound')]].gt(0).all(axis=1)
        common = common_coverage_scores(q,predictions,has_rich)
        # Missing whole PBP families use the already-defined box-only model.
        # This decision uses historical source coverage, never a target result.
        for family in ('rich_ridge','rich_boost'):
            predictions[family].loc[~has_rich] = predictions['box_ridge'].loc[~has_rich]
        result = score_quotes(q,predictions,f'{lag}h')
        result['common_pbp_coverage'] = common
        result['box_fallback_quotes'] = int((~has_rich).sum())
        result['input_eligible'] = int(ok.sum())
        result['input_ineligible'] = int((~ok).sum())
        result['with_prior_pbp'] = int(f.pbp_games.gt(0).sum())
        result['max_source_not_before_forecast'] = int(f.max_source_at.ge(f.forecast_at).sum())
        # Native incumbent distribution is a separate comparator; it must not
        # be confused with the common-distribution information comparison.
        native_loss = np.full(len(q),np.nan)
        means = baseline.predict_markets(f,artifact['baseline'][2025])
        for market in models.MARKETS:
            m=q.market.eq(market)
            native=baseline.outcome_probabilities(market,means.loc[m,market],q.loc[m,'open_line'],artifact['baseline'][2025])
            conditional=native['p_over']/np.maximum(1-native['p_push'],1e-12)
            ll,_=models.proper_scores(q.loc[m,'actual'],q.loc[m,'open_line'],conditional)
            native_loss[m]=ll
        native_loss[q.void]=np.nan
        result['native_incumbent_secondary_log_loss']=float(np.nanmean(native_loss))
        results[f'{lag}h']=result
        sensitivity_rows[lag]=(q,predictions)
        save_json(RESULTS/'results.json',results)
        print(f'{lag}h: {len(q):,} eligible quotes; opener LL {result["market"]["log_loss"]:.6f}',flush=True)
        for family,item in result['models'].items():
            print(f'  {family}: LL {item["log_loss"]:.6f}; ROI5 {item["economics"]["0.05"]["roi"]["estimate"]}',flush=True)
    q8,p8=sensitivity_rows[8]
    q24,p24=sensitivity_rows[24]
    key=['event_id','bp_player_id','market']
    i8=q8[key].apply(tuple,axis=1)
    i24=q24[key].apply(tuple,axis=1)
    common_ids=set(i8)&set(i24)
    timing={'quotes':len(common_ids),'log_loss':{}}
    for lag,(q,ps) in sensitivity_rows.items():
        ids=q[key].apply(tuple,axis=1)
        keep=ids.isin(common_ids)&~q.void&q.actual.notna()
        timing['log_loss'][str(lag)]={}
        for family,p in ps.items():
            ll,_=models.proper_scores(q.actual,q.open_line,p.p_over_nonpush)
            timing['log_loss'][str(lag)][family]=float(np.nanmean(ll[keep]))
    results['timing_common_cohort']=timing
    save_json(RESULTS/'results.json',results)
    # Count validation at the fixed training horizon is descriptive secondary
    # evidence; primary market scoring uses each actual opener timestamp.
    dev = prepared['frame']
    dev = dev[dev.eligible & dev.game_date.dt.year.eq(2025)].reset_index(drop=True)
    base=baseline.predict_markets(dev,artifact['baseline'][2025])
    count_results={}
    for market in models.MARKETS:
        count_results[market]={}
        for family in models.MODEL_NAMES:
            pred=artifact['models'][market][family].predict(dev,base[market])
            count_results[market][family]=models.count_metrics(dev[models.TARGETS[market]],pred)
    save_json(RESULTS/'count_results_2025.json',count_results)
    run.update(status='complete', completed_at=pd.Timestamp.now(tz='UTC'), results_sha256=sources.file_sha256(RESULTS/'results.json'))
    save_json(receipt,run)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','fit','evaluate'])
    parser.add_argument('--cache-dir',type=Path,default=CACHE)
    args=parser.parse_args()
    globals()[args.phase](args.cache_dir)


if __name__ == '__main__':
    main()
