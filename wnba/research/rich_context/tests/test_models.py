import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import probabilities, economic_rows, clustered_ratio, validate_feature_names


class ProbabilityContractTests(unittest.TestCase):
    def test_poisson_push_and_economic_value(self):
        p = probabilities([3.], [3.], {'scale': 1., 'alpha': 0.})
        self.assertAlmostEqual(p.p_push.iloc[0], .224041807655, places=10)
        self.assertAlmostEqual(p.p_over.iloc[0], .352768111218, places=10)
        self.assertAlmostEqual(p.p_under.iloc[0], .423190081127, places=10)
        self.assertAlmostEqual(p.p_under.iloc[0] * (100/110) - p.p_over.iloc[0], .03195014435, places=8)

    def test_half_lines_and_negative_binomial(self):
        p = probabilities([.3, 3., 20.], [.5, 3.5, 20.], {'scale': 1., 'alpha': .2})
        np.testing.assert_allclose(p.p_over + p.p_under + p.p_push, 1.)
        np.testing.assert_array_equal(p.p_push.iloc[:2], 0.)

    def test_market_information_rejected(self):
        for col in ['open_line', 'home_favorite', 'actual_points', 'game_spread']:
            with self.assertRaises(ValueError):
                validate_feature_names([col])

    def test_push_retains_stake_and_later_signal_cannot_replace_first(self):
        q = pd.DataFrame(dict(game_id=['a','a','b'], athlete_id=['x','x','y'],
                              forecast_at=['2025-01-01T10:00Z','2025-01-01T11:00Z','2025-01-02T10:00Z'],
                              market=['points','assists','points'], open_over=[110]*3,
                              open_under=[110]*3, open_line=[3.]*3, actual=[3.,8.,0.]))
        p = pd.DataFrame(dict(p_over=[.7,.9,.7],p_under=[.1,.05,.1]))
        r = economic_rows(q,p,.05)
        self.assertEqual(len(r),2)
        self.assertEqual(r.iloc[0]['market'],'points')
        self.assertEqual(r.iloc[0].profit,0.)
        self.assertEqual(r.settled_stake.sum(),2.)

    def test_cluster_ratio_keeps_weighted_estimand(self):
        r = clustered_ratio(['a','a','b'],[1.,1.,-1.],repeats=100)
        self.assertAlmostEqual(r['estimate'],1/3)


if __name__ == '__main__':
    unittest.main()
