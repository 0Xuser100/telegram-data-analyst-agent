import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

pd.set_option('display.width', 220)
pd.set_option('display.max_columns', 20)
path = r'C:\Users\ThinkTech\AppData\Local\Temp\baseline-gpt-5_6-luna-u9rf0blt\data\deaths.csv'
out = Path(r'C:\Users\ThinkTech\AppData\Local\Temp\baseline-gpt-5_6-luna-u9rf0blt\output')
df = pd.read_csv(path)

us = df[df['State'].eq('United States')].copy()
causes = us['Cause Name'].unique().tolist()
print('CAUSES:', causes)
print('YEARS:', int(df['Year'].min()), int(df['Year'].max()), 'NATIONAL ROWS:', len(us))

cause_totals = us.groupby('Cause Name', as_index=False).agg(
    total_deaths=('Deaths', 'sum'),
    avg_annual_deaths=('Deaths', 'mean'),
    avg_rate=('Age-adjusted Death Rate', 'mean')
)
cause_totals['share_of_national_cause_total_pct'] = 100 * cause_totals['total_deaths'] / cause_totals['total_deaths'].sum()
cause_totals = cause_totals.sort_values('total_deaths', ascending=False)
specific = cause_totals[~cause_totals['Cause Name'].str.lower().eq('all causes')].copy()
specific['share_of_specific_cause_total_pct'] = 100 * specific['total_deaths'] / specific['total_deaths'].sum()
print('\nCAUSE TOTALS AND SHARES (US rows, all years; share denominator excludes All causes):')
print(specific.to_string(index=False, formatters={'avg_annual_deaths':'{:.1f}'.format, 'avg_rate':'{:.1f}'.format, 'share_of_specific_cause_total_pct':'{:.1f}'.format}))

annual = us.groupby('Year', as_index=False).agg(total_deaths=('Deaths','sum'), avg_age_adjusted_rate=('Age-adjusted Death Rate','mean'))
print('\nANNUAL NATIONAL TOTALS:')
print(annual.to_string(index=False, formatters={'avg_age_adjusted_rate':'{:.1f}'.format}))
print('\nALL-CAUSE TOTAL / AVG ANNUAL:', int(cause_totals.iloc[0]['total_deaths']), round(cause_totals.iloc[0]['avg_annual_deaths'], 1))
print('SPECIFIC-CAUSE TOTAL / AVG ANNUAL:', int(specific['total_deaths'].sum()), round(specific['total_deaths'].sum() / len(all_causes) if 'all_causes' in locals() else specific['total_deaths'].sum() / 19, 1))
print('NATIONAL TOTAL SUM / MEAN / MIN / MAX:', int(us['Deaths'].sum()), round(us['Deaths'].mean(), 1), int(us['Deaths'].min()), int(us['Deaths'].max()))

max_record = us.loc[us['Deaths'].idxmax()]
min_record = us.loc[us['Deaths'].idxmin()]
print('\nBIGGEST NATIONAL CAUSE-YEAR:', int(max_record['Year']), max_record['Cause Name'], int(max_record['Deaths']))
print('SMALLEST NATIONAL CAUSE-YEAR:', int(min_record['Year']), min_record['Cause Name'], int(min_record['Deaths']))

# IQR outliers among national cause-year death counts.
q1, q3 = us['Deaths'].quantile([.25, .75]); iqr = q3 - q1
outliers = us[us['Deaths'] > q3 + 1.5 * iqr].sort_values('Deaths', ascending=False)
all_causes = us[us['Cause Name'].str.lower().eq('all causes')].sort_values('Year')
print('ALL-CAUSE 1999 TO 2017:', int(all_causes.iloc[0]['Deaths']), int(all_causes.iloc[-1]['Deaths']), round(100 * (all_causes.iloc[-1]['Deaths'] / all_causes.iloc[0]['Deaths'] - 1), 1), 'pct change')
print('NATIONAL DEATH COUNT IQR OUTLIERS:', len(outliers), 'threshold:', round(q3 + 1.5 * iqr, 1))
print(outliers[['Year','Cause Name','Deaths']].head(5).to_string(index=False))

# Plot national trends for causes excluding the aggregate All causes line, which dwarfs other causes.
plot_df = us[~us['Cause Name'].str.lower().eq('all causes')].pivot(index='Year', columns='Cause Name', values='Deaths')
plot_df = plot_df.loc[:, plot_df.sum().sort_values(ascending=False).index]
fig, ax = plt.subplots(figsize=(12, 7))
for col in plot_df.columns:
    ax.plot(plot_df.index, plot_df[col], marker='o', linewidth=2, markersize=3, label=col)
ax.set_title('United States deaths by leading cause, 1999–2017')
ax.set_xlabel('Year')
ax.set_ylabel('Deaths')
ax.grid(axis='y', alpha=0.25)
ax.legend(title='Cause', bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=8)
fig.tight_layout()
fig.savefig(out / 'deaths_by_cause_trend.png', dpi=150, facecolor='white')
plt.close(fig)
print('\nCHART WRITTEN')
