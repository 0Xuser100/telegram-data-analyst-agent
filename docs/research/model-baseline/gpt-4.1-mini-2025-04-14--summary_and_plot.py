import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

file_path = "./data/deaths.csv"
df = pd.read_csv(file_path)

# Analysis by main categories (Cause Name)
summary = df.groupby('Cause Name')['Deaths'].sum().sort_values(ascending=False)
total_deaths = summary.sum()

# Calculate shares of total deaths
shares = (summary / total_deaths * 100).round(1)

# Largest and smallest contributors
largest_cause = summary.idxmax()
largest_deaths = summary.max()
smallest_cause = summary.idxmin()
smallest_deaths = summary.min()

# If there is a Year column, summarize trend by year for total deaths
deaths_by_year = df.groupby('Year')['Deaths'].sum()

# Plot: line plot for trend over years with total deaths if 'Year' exists
plt.figure(figsize=(10, 6))
sns.lineplot(data=deaths_by_year)
plt.title('Total Deaths Trend Over Years')
plt.ylabel('Total Deaths')
plt.xlabel('Year')
plt.grid(True)
plt.tight_layout()
plt.savefig('./output/total_deaths_trend.png', dpi=150, facecolor='white')

# Also plot the share of deaths by cause as a descending horizontal bar chart
plt.figure(figsize=(12, 8))
share_df = shares.reset_index().rename(columns={0:'Percentage', 'Cause Name':'Cause Name'})
share_df = share_df.sort_values(by='Deaths', ascending=True) if 'Deaths' in share_df.columns else share_df.sort_values(by=share_df.columns[1], ascending=True)

# fix share_df columns
share_df.columns = ['Cause Name', 'Percentage']

sns.barplot(y='Cause Name', x='Percentage', data=share_df, palette='tab10', dodge=False)
plt.title('Share of Total Deaths by Cause')
plt.xlabel('Percentage of Total Deaths (%)')
plt.ylabel('Cause Name')

# Annotate the bars
for i, v in enumerate(share_df['Percentage']):
    plt.text(v + 0.5, i, f'{v}%', color='black', va='center')

plt.tight_layout()
plt.savefig('./output/deaths_cause_share.png', dpi=150, facecolor='white')

# Save summary info for reporting
summary_info = {
    'total_deaths': total_deaths,
    'largest_cause': largest_cause,
    'largest_deaths': largest_deaths,
    'smallest_cause': smallest_cause,
    'smallest_deaths': smallest_deaths,
    'shares': shares,
    'deaths_by_year': deaths_by_year
}

with open('./output/summary_info.txt', 'w') as f:
    f.write(str(summary_info))
