"""Render the saved results. This script never fits or scores a model."""
from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE = Path(__file__).resolve().parents[1]
r = json.loads((HERE/'results/results.json').read_text())['8h']
keys = ['incumbent','box_ridge','rich_ridge','rich_boost']
labels = ['Corrected old model*','Box-score regression','Richer-data regression','Richer-data trees']
colors = ['#70757d','#23364b','#147d92','#a65b20']
fig, axes = plt.subplots(1,2,figsize=(12.5,6.2))
fig.subplots_adjust(left=.19,right=.97,top=.77,bottom=.24,wspace=.22)
for ax, metric in zip(axes,['forecast','returns']):
    for i,k in enumerate(keys):
        m = r['models'][k]
        v = m['versus_opener'] if metric=='forecast' else m['economics']['0.05']['roi']
        est = v['estimate']; lo,hi = v['ci95']
        ax.errorbar(est, i, xerr=[[est-lo],[hi-est]],fmt='o',color=colors[i],
                    markersize=7,capsize=4,elinewidth=2)
    ax.axvline(0,color='#50555b',linewidth=1,linestyle='--')
    ax.set_yticks(range(4),labels if metric=='forecast' else ['']*4)
    ax.set_ylim(3.6,-.6)
    ax.spines[['top','right','left']].set_visible(False)
    ax.tick_params(axis='y',length=0,labelsize=10)
    ax.grid(axis='x',alpha=.16)
    if metric=='forecast':
        ax.set_title('Forecast error versus opening prices',fontsize=11,pad=16)
        ax.set_xlabel('Log-loss difference · below zero is better',fontsize=9)
    else:
        ax.set_title('Historical return at the 5% EV rule',fontsize=11,pad=16)
        ax.set_xlabel('Return per settled $1 · includes push stakes',fontsize=9)
        ax.xaxis.set_major_formatter(PercentFormatter(1))
fig.text(.04,.94,'Richer data did not establish better forecasts',fontsize=18,weight='bold')
fig.text(.04,.88,'Positive simulated returns need to be read alongside prediction quality.',fontsize=12,color='#444b53')
fig.text(.04,.13,'2025 reused development sample · 7,473 settled quotes · 116 game dates',fontsize=10)
fig.text(.04,.085,'Bars: 95% intervals from whole-date resampling. Historical quote and publication timing remain assumptions.',fontsize=9,color='#444b53')
fig.text(.04,.045,'* All four primary comparisons use the same count-distribution calibration method.',fontsize=9,color='#444b53')
for ext in ['png','svg']:
    fig.savefig(HERE/f'results/comparison.{ext}',dpi=180,facecolor='white')
plt.close(fig)
