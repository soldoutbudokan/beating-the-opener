"""Hyperparameter search + extra model families, scored on validation seasons only.

Held-out seasons are never consulted here. The point is to establish whether the
line-blind model is near its ceiling, so that "the market wins" is a conclusion
about the market rather than about lazy tuning.
"""
import os
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from market import log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL = [2019, 2020, 2021, 2022, 2023]


def wf_val(df, cols, factory, seasons=VAL):
    out = np.full(len(df), np.nan)
    for s in seasons:
        tr = df[df.season_year < s]
        te = (df.season_year == s).values
        m = factory()
        m.fit(tr[cols].astype(float).values, tr.home_win.values)
        out[te] = m.predict_proba(df[te][cols].astype(float).values)[:, 1]
    return out


def score(df, p, seasons=VAL):
    m = df.season_year.isin(seasons).values & np.isfinite(p)
    return float(log_loss_vec(df.home_win.values[m], p[m]).mean())


class MLP(nn.Module):
    def __init__(self, d, h=64, p=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d, h), nn.ReLU(), nn.Dropout(p),
            nn.Linear(h, h // 2), nn.ReLU(), nn.Dropout(p),
            nn.Linear(h // 2, 1))

    def forward(self, x):
        return self.net(x).squeeze(-1)


class TorchClf:
    """Small MLP with a sklearn-ish interface so it drops into the same harness."""

    def __init__(self, epochs=60, lr=1e-3, h=64, wd=1e-4, seed=0):
        self.epochs, self.lr, self.h, self.wd, self.seed = epochs, lr, h, wd, seed

    def fit(self, X, y):
        torch.manual_seed(self.seed)
        self.imp = SimpleImputer(strategy="median").fit(X)
        self.sc = StandardScaler().fit(self.imp.transform(X))
        Xt = torch.tensor(self.sc.transform(self.imp.transform(X)), dtype=torch.float32)
        yt = torch.tensor(y, dtype=torch.float32)
        self.m = MLP(Xt.shape[1], self.h)
        opt = torch.optim.AdamW(self.m.parameters(), lr=self.lr, weight_decay=self.wd)
        lossf = nn.BCEWithLogitsLoss()
        n = len(Xt)
        for _ in range(self.epochs):
            self.m.train()
            perm = torch.randperm(n)
            for i in range(0, n, 256):
                idx = perm[i:i + 256]
                opt.zero_grad()
                loss = lossf(self.m(Xt[idx]), yt[idx])
                loss.backward()
                opt.step()
        return self

    def predict_proba(self, X):
        self.m.eval()
        Xt = torch.tensor(self.sc.transform(self.imp.transform(X)), dtype=torch.float32)
        with torch.no_grad():
            p = torch.sigmoid(self.m(Xt)).numpy()
        return np.column_stack([1 - p, p])


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "dataset_v4.csv"))
    df = df[df.season_type.isin([2, 3, 5])].copy().reset_index(drop=True)
    cols = [c for c in M.blind_features(df) if not c.startswith(("A_", "B_"))]
    print(f"{len(df)} games, {len(cols)} features. Tuning on {VAL} (held-out untouched).\n")

    results = []

    print("--- logistic C sweep ---")
    for C in [0.003, 0.01, 0.03, 0.1, 0.3, 1.0]:
        p = wf_val(df, cols, lambda C=C: M.mk_logit(C=C))
        s = score(df, p)
        results.append((f"logit C={C}", s))
        print(f"  C={C:<6} val_logloss={s:.5f}")

    print("--- GBM sweep ---")
    grid = [
        dict(max_leaf_nodes=7, learning_rate=0.04, min_samples_leaf=150),
        dict(max_leaf_nodes=15, learning_rate=0.025, min_samples_leaf=100),
        dict(max_leaf_nodes=31, learning_rate=0.02, min_samples_leaf=60),
        dict(max_leaf_nodes=15, learning_rate=0.05, min_samples_leaf=200,
             l2_regularization=5.0),
        dict(max_leaf_nodes=7, learning_rate=0.02, min_samples_leaf=300,
             l2_regularization=10.0),
    ]
    for gkw in grid:
        p = wf_val(df, cols, lambda g=gkw: M.mk_gbm(**g))
        s = score(df, p)
        results.append((f"gbm {gkw}", s))
        print(f"  {str(gkw)[:78]:78s} val={s:.5f}")

    print("--- MLP ---")
    for h, wd in [(32, 1e-3), (64, 1e-4), (128, 1e-3)]:
        p = wf_val(df, cols, lambda h=h, wd=wd: TorchClf(h=h, wd=wd))
        s = score(df, p)
        results.append((f"mlp h={h} wd={wd}", s))
        print(f"  h={h:<4} wd={wd:<7} val={s:.5f}")

    print("\n--- best configs ---")
    for name, s in sorted(results, key=lambda x: x[1])[:6]:
        print(f"  {s:.5f}  {name[:80]}")

    # Market reference on the same validation seasons.
    v = df[df.season_year.isin(VAL) & df.mkt_mult.notna()]
    print(f"\n  MARKET on validation seasons: "
          f"{float(log_loss_vec(v.home_win.values, v.mkt_mult.values).mean()):.5f}")


if __name__ == "__main__":
    main()
