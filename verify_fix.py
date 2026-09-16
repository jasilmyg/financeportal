import sys; sys.path.insert(0, '.')
from clickhouse_aggregator import get_clickhouse_df
df = get_clickhouse_df('2026-07-01', 'custom', end_date_str='2026-07-20')
print(f"Total rows after all cleaning: {len(df)}")
print(f"Total QTY: {df['QTY'].sum():,.0f}")
print("\nQTY by Category:")
print(df.groupby("Category")["QTY"].sum().sort_values(ascending=False).to_string())
print("\nQTY by Financier:")
print(df.groupby("Financier")["QTY"].sum().sort_values(ascending=False).to_string())
