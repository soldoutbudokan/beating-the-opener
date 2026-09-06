"""Small, fixed outcome models for the rich-context research experiment.

No quoted line, odds, book, game result, or target-game participation column
is admitted to the predictors. Feature names are explicitly supplied by the
historical feature store, and the model contract rejects suspicious names.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy import optimize, stats
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_poisson_deviance
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MARKETS = ('points', 'rebounds', 'assists', 'threes')
TARGETS = dict(points='points', rebounds='rebounds', assists='assists',
               threes='three_point_field_goals_made')
MODEL_NAMES = ('incumbent', 'box_ridge', 'rich_ridge', 'rich_boost')
RIDGE_GRID = (10.0, 100.0, 1000.0, 10000.0)
TREE_GRID = (7, 15)
MIN_MEAN = 0.02


def validate_feature_names(columns):
    forbidden = ('odds', 'cost', 'line', 'spread', 'favorite', 'actual',
                 'outcome', 'score_value', 'lead_', 'did_not_play', 'target')
    bad = [c for c in columns if any(x in c.lower() for x in forbidden)]
    if bad:
        raise ValueError(f'Forbidden predictors: {bad}')
    if len(set(columns)) != len(columns):
        raise ValueError('Duplicate predictor names')


@dataclass
class MeanModel:
    family: str
    columns: tuple[str, ...]
    estimator: object | None = None

    def _matrix(self, frame, base):
        x = frame.reindex(columns=self.columns).apply(pd.to_numeric,
                                                      errors='coerce').copy()
        x = x.replace([np.inf, -np.inf], np.nan)
        x['incumbent_mean'] = np.asarray(base, dtype=float)
        return x

    def predict(self, frame, base):
        base = np.asarray(base, dtype=float)
        if self.family == 'incumbent':
            pred = base
        else:
            pred = self.estimator.predict(self._matrix(frame, base))
            if self.family.endswith('ridge'):
                pred = pred + base
        return np.maximum(np.asarray(pred, dtype=float), MIN_MEAN)


def fit_mean_model(family, columns, frame, target, base, complexity=None):
    """Fit only on the supplied training rows; caller owns chronological split."""
    validate_feature_names(columns)
    target, base = np.asarray(target, float), np.asarray(base, float)
    ok = np.isfinite(target) & (target >= 0) & np.isfinite(base) & (base > 0)
    if ok.sum() < 100:
        raise ValueError('Too few valid training observations')
    model = MeanModel(family, tuple(columns))
    if family == 'incumbent':
        return model
    x = model._matrix(frame, base).loc[ok]
    if family.endswith('ridge'):
        model.estimator = make_pipeline(
            SimpleImputer(strategy='median', keep_empty_features=True, add_indicator=True),
            StandardScaler(), Ridge(alpha=float(complexity)))
        model.estimator.fit(x, target[ok] - base[ok])
    elif family == 'rich_boost':
        model.estimator = HistGradientBoostingRegressor(
            loss='poisson', learning_rate=0.05, max_iter=150,
            max_leaf_nodes=int(complexity), min_samples_leaf=100,
            l2_regularization=10.0, early_stopping=False, random_state=20260906)
        model.estimator.fit(x, target[ok])
    else:
        raise ValueError(f'Unknown family: {family}')
    return model


def tune_mean_model(family, columns, train, train_y, train_base,
                    validation, validation_y, validation_base):
    """Choose from the registered small grid using 2023 count scores only."""
    grid = RIDGE_GRID if family.endswith('ridge') else TREE_GRID
    if family == 'incumbent':
        grid = (None,)
    scores = []
    for value in grid:
        model = fit_mean_model(family, columns, train, train_y, train_base, value)
        pred = model.predict(validation, validation_base)
        ok = np.isfinite(validation_y) & np.isfinite(pred)
        score = float(mean_poisson_deviance(np.asarray(validation_y)[ok], pred[ok]))
        scores.append({'complexity': value, 'poisson_deviance': score})
    best = min(scores, key=lambda x: (x['poisson_deviance'],
               -x['complexity'] if family.endswith('ridge') else (x['complexity'] or 0)))
    return best['complexity'], scores


def count_metrics(actual, mean):
    y, mu = np.asarray(actual, float), np.asarray(mean, float)
    ok = np.isfinite(y) & np.isfinite(mu) & (y >= 0) & (mu > 0)
    return {'n': int(ok.sum()), 'mae': float(np.abs(y[ok] - mu[ok]).mean()),
            'mean_error': float((mu[ok] - y[ok]).mean()),
            'poisson_deviance': float(mean_poisson_deviance(y[ok], mu[ok]))}


def fit_distribution(actual, mean):
    """Calibrate mean/dispersion on 2024 held-forward forecasts, never 2025.

    Negative-binomial variance is mu + alpha*mu**2. A tiny alpha uses the
    Poisson limit. This is a deliberately common distribution layer for all
    four mean models, so a new probability family cannot masquerade as a
    benefit from the new information.
    """
    y, mu = np.asarray(actual, float), np.asarray(mean, float)
    ok = np.isfinite(y) & np.isfinite(mu) & (y >= 0) & (mu > 0)
    y, mu = y[ok], mu[ok]
    if len(y) < 100:
        raise ValueError('Insufficient out-of-time distribution calibration')
    scale = float(y.sum() / mu.sum())
    mu = np.maximum(mu * scale, MIN_MEAN)

    def objective(log_alpha):
        alpha = np.exp(log_alpha)
        size = 1.0 / alpha
        return -float(stats.nbinom.logpmf(y, size, size / (size + mu)).mean())

    result = optimize.minimize_scalar(objective, bounds=(-10, 2), method='bounded')
    if not result.success:
        raise RuntimeError('Count distribution fit failed')
    alpha = float(np.exp(result.x))
    nb_nll = objective(result.x)
    poisson_nll = -float(stats.poisson.logpmf(y, mu).mean())
    if poisson_nll <= nb_nll + 1e-7:
        alpha = 0.0
    return {'scale': scale, 'alpha': alpha, 'calibration_n': len(y),
            'calibration_year': 2024,
            'calibration_nll': min(poisson_nll, nb_nll)}


def distribution(mean, params):
    mu = np.maximum(np.asarray(mean, float) * params['scale'], MIN_MEAN)
    alpha = params['alpha']
    if alpha <= 0:
        return stats.poisson(mu), mu
    size = 1.0 / alpha
    return stats.nbinom(size, size / (size + mu)), mu


def probabilities(mean, line, params):
    line = np.asarray(line, float)
    if not np.isfinite(line).all() or (line < 0).any():
        raise ValueError('Threshold must be finite and nonnegative')
    dist, mu = distribution(mean, params)
    over = dist.sf(np.floor(line))
    under = dist.cdf(np.ceil(line) - 1)
    push = np.where(line == np.floor(line), dist.pmf(line), 0.0)
    if not np.allclose(over + under + push, 1.0, atol=1e-10):
        raise AssertionError('Win/push/loss probabilities must sum to one')
    return pd.DataFrame({'mean': mu, 'p_over': over, 'p_under': under,
                         'p_push': push,
                         'p_over_nonpush': over / np.maximum(1 - push, 1e-12),
                         'median': dist.ppf(.5), 'p10': dist.ppf(.1),
                         'p90': dist.ppf(.9)})


def count_nll(actual, mean, params):
    dist, _ = distribution(mean, params)
    return -dist.logpmf(np.asarray(actual, float))


def proper_scores(actual, line, p_over_nonpush):
    y, line, p = np.asarray(actual, float), np.asarray(line, float), np.asarray(p_over_nonpush, float)
    nonpush = y != line
    over = (y > line).astype(float)
    p = np.clip(p, 1e-8, 1 - 1e-8)
    logloss = -(over * np.log(p) + (1 - over) * np.log1p(-p))
    brier = (p - over)**2
    return np.where(nonpush, logloss, np.nan), np.where(nonpush, brier, np.nan)


def american_decimal(odds):
    odds = np.asarray(odds, float)
    if not np.isfinite(odds).all() or (np.abs(odds) < 100).any():
        raise ValueError('Invalid American price')
    return np.where(odds > 0, 1 + odds / 100, 1 + 100 / np.maximum(-odds, 1))


def economic_rows(quotes, prediction, threshold):
    """First qualifying signal per player/game; future offers cannot win ties.

    Pushes return zero profit and remain in the stake denominator. Known
    voids are refunded and shown separately. The simulation is conditional
    on archived prices; it does not assert those prices could be filled.
    """
    d = quotes.reset_index(drop=True).copy()
    p = prediction.reset_index(drop=True)
    over_d, under_d = american_decimal(d.open_over), american_decimal(d.open_under)
    ev_o = p.p_over.to_numpy() * (over_d - 1) - p.p_under.to_numpy()
    ev_u = p.p_under.to_numpy() * (under_d - 1) - p.p_over.to_numpy()
    d['side'] = np.where(ev_o >= ev_u, 'over', 'under')
    d['claimed_ev'] = np.maximum(ev_o, ev_u)
    d['decimal_odds'] = np.where(d.side.eq('over'), over_d, under_d)
    d = d[d.claimed_ev > threshold].copy()
    if d.empty:
        return d.assign(profit=pd.Series(dtype=float), settled_stake=pd.Series(dtype=float))
    d = d.sort_values(['forecast_at', 'claimed_ev', 'market'],
                      ascending=[True, False, True], kind='stable')
    d = d.drop_duplicates(['game_id', 'athlete_id'], keep='first')
    push = d.actual.eq(d.open_line)
    void = d.get('void', pd.Series(False, index=d.index)).fillna(False).astype(bool)
    unresolved = d.actual.isna() & ~void
    win = np.where(d.side.eq('over'), d.actual > d.open_line, d.actual < d.open_line)
    d['profit'] = np.where(void | push, 0., np.where(win, d.decimal_odds - 1, -1.))
    d.loc[unresolved, 'profit'] = np.nan
    d['settled_stake'] = (~void & ~unresolved).astype(float)
    d['is_push'] = push & ~void
    d['is_void'] = void
    d['is_unresolved'] = unresolved
    return d


def clustered_ratio(dates, numerator, denominator=None, *, repeats=10000, seed=20260906):
    """Percentile bootstrap of a ratio, resampling complete game dates."""
    num = np.asarray(numerator, float)
    den = np.ones(len(num)) if denominator is None else np.asarray(denominator, float)
    valid = np.isfinite(num) & np.isfinite(den)
    d = pd.DataFrame({'date': np.asarray(dates)[valid], 'n': num[valid], 'd': den[valid]})
    g = d.groupby('date')[['n', 'd']].sum()
    if len(g) == 0 or g.d.sum() <= 0:
        return {'estimate': None, 'ci95': [None, None], 'dates': len(g), 'n': int(valid.sum())}
    estimate = float(g.n.sum() / g.d.sum())
    if len(g) < 2:
        return {'estimate': estimate, 'ci95': [None, None], 'dates': len(g), 'n': int(valid.sum())}
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(g), size=(repeats, len(g)))
    sums_n, sums_d = g.n.to_numpy()[indices].sum(axis=1), g.d.to_numpy()[indices].sum(axis=1)
    ratios = sums_n[sums_d > 0] / sums_d[sums_d > 0]
    interval = np.quantile(ratios, [.025, .975]).tolist()
    return {'estimate': estimate, 'ci95': interval, 'dates': len(g), 'n': int(valid.sum())}
