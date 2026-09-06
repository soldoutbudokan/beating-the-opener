# Research decision brief

Question: does **Regression with richer data** improve forecasts over **Box-score regression**?

Evidence: **2025 / previously used development data** (declared by the caller). This brief reads a completed result; it is not a new backtest.

These development results cannot establish an independent betting edge.

No minimum useful improvement was supplied. Do not invent one after seeing the result.

## 8h information delay: No clear improvement; the interval includes no difference

7,473 played outcomes; 131 void quotes; 0 unresolved quotes; 116 dates.

Candidate minus baseline: **+0.000250**, with a 95% interval of **-0.000555 to +0.001071**. Negative favors the candidate. Lower log loss means better probabilities, not higher returns.

| Forecast | Log loss |
| --- | ---: |
| Opening market | 0.685983 |
| Box-score regression | 0.687662 |
| Existing formula, common distribution | 0.690533 |
| Tree model with richer data | 0.688050 |
| Regression with richer data | 0.687912 |

The corrected incumbent's original distribution scores 0.688298; its common-distribution baseline is a different comparison, not an exact deployed-model replay.

Every stored betting rule is shown; the most profitable rule does not choose the model. The percentage beside each model is the minimum profit per dollar it claimed before selecting a bet. Returns are simulated.

| Model / selection rule | Settled stake | Actual profit | Claimed profit on those bets | Return [95% interval] | Unders | Pending |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| Box-score regression / 5% | $2,125 | $+101.02 | $+371.48 | +4.75% [+0.73%, +8.77%] | 91.0% | 0 |
| Box-score regression / 10% | $1,635 | $+138.71 | $+346.36 | +8.48% [+3.89%, +13.11%] | 92.4% | 0 |
| Existing formula, common distribution / 5% | $2,153 | $+79.02 | $+385.30 | +3.67% [-0.16%, +7.42%] | 89.8% | 0 |
| Existing formula, common distribution / 10% | $1,658 | $+93.10 | $+358.58 | +5.62% [+1.10%, +10.00%] | 91.2% | 0 |
| Tree model with richer data / 5% | $2,120 | $+117.45 | $+372.74 | +5.54% [+1.50%, +9.52%] | 87.2% | 0 |
| Tree model with richer data / 10% | $1,639 | $+134.45 | $+348.73 | +8.20% [+3.79%, +12.52%] | 88.9% | 0 |
| Regression with richer data / 5% | $2,159 | $+94.19 | $+379.58 | +4.36% [+0.37%, +8.28%] | 89.9% | 0 |
| Regression with richer data / 10% | $1,669 | $+150.90 | $+354.39 | +9.04% [+4.59%, +13.44%] | 91.9% | 0 |

## 24h information delay: No clear improvement; the interval includes no difference

7,470 played outcomes; 131 void quotes; 0 unresolved quotes; 116 dates.

Candidate minus baseline: **+0.000207**, with a 95% interval of **-0.000554 to +0.000986**. Negative favors the candidate. Lower log loss means better probabilities, not higher returns.

| Forecast | Log loss |
| --- | ---: |
| Opening market | 0.686001 |
| Box-score regression | 0.688121 |
| Existing formula, common distribution | 0.691114 |
| Tree model with richer data | 0.688415 |
| Regression with richer data | 0.688328 |

The corrected incumbent's original distribution scores 0.688925; its common-distribution baseline is a different comparison, not an exact deployed-model replay.

Every stored betting rule is shown; the most profitable rule does not choose the model. The percentage beside each model is the minimum profit per dollar it claimed before selecting a bet. Returns are simulated.

| Model / selection rule | Settled stake | Actual profit | Claimed profit on those bets | Return [95% interval] | Unders | Pending |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| Box-score regression / 5% | $2,140 | $+113.35 | $+378.92 | +5.30% [+0.95%, +9.62%] | 91.0% | 0 |
| Box-score regression / 10% | $1,653 | $+113.09 | $+354.10 | +6.84% [+2.13%, +11.57%] | 92.4% | 0 |
| Existing formula, common distribution / 5% | $2,166 | $+64.62 | $+391.97 | +2.98% [-0.93%, +6.83%] | 89.6% | 0 |
| Existing formula, common distribution / 10% | $1,675 | $+88.40 | $+365.18 | +5.28% [+0.68%, +9.70%] | 91.1% | 0 |
| Tree model with richer data / 5% | $2,126 | $+114.59 | $+376.88 | +5.39% [+1.32%, +9.45%] | 87.7% | 0 |
| Tree model with richer data / 10% | $1,655 | $+132.97 | $+353.63 | +8.03% [+3.31%, +12.74%] | 89.1% | 0 |
| Regression with richer data / 5% | $2,161 | $+98.57 | $+386.49 | +4.56% [+0.42%, +8.62%] | 90.0% | 0 |
| Regression with richer data / 10% | $1,679 | $+132.31 | $+360.42 | +7.88% [+3.39%, +12.29%] | 91.7% | 0 |

## Decision to record

Record **stop**, **collect missing information**, or **prepare a separately specified independent test**, with a reason. A completed run or positive simulated return does not itself justify a model change.

Before choosing the next experiment, inspect the largest probability errors, the gap between claimed and actual profit, and whether the needed information was available before the prediction. These summaries identify questions; they do not establish the causes of errors.

## What was checked

Receipt checksum and summary arithmetic only; individual predictions and uncertainty intervals are not recomputed.

Results SHA-256: `8509b7ffcdbaa5b7cb348a432e18e1de531b655601109369c52dbad43688e942`

Receipt SHA-256: `e75111f1fbd3c5ee723abf32e9e3591f271679951b9e13a2b187fe133605c1c3`

This report cannot authorize model adoption or live betting. Test freshness and any useful-gain threshold must be established in the original plan, not declared after seeing results.
