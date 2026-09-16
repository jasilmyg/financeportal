from clickhouse_client import cache_clear
cache_clear()

from clickhouse_aggregator import get_clickhouse_df, _JUNK_PRODUCTS

df = get_clickhouse_df('2026-08-01', 'monthly')

print("=== MONTHLY AUG 2026 — Post-Cleaning Report ===")
print("Total rows : {:,}".format(len(df)))
print("Total QTY  : {:,}".format(int(df['QTY'].sum())))
print()

print("--- Categories ---")
cat_grp = df.groupby('Category').agg({'QTY': 'sum', 'Sold Price': 'sum'}).sort_values('QTY', ascending=False)
for cat, row in cat_grp.iterrows():
    cr_val = row['Sold Price'] / 1e7
    print("  {:15s}  QTY: {:>8,}  Value: {:>7.2f} Cr".format(cat, int(row['QTY']), cr_val))

print()
print("--- Products in each category ---")
for cat in cat_grp.index:
    prods = sorted(df[df['Category'] == cat]['Product'].unique())
    print("  {}: {}".format(cat, prods))

print()
print("--- Financiers (cleaned+mapped) ---")
fin_sum = int(df['QTY'].sum())
fin_grp = df.groupby('Financier')['QTY'].sum().sort_values(ascending=False)
for fin, qty in fin_grp.items():
    pct = qty / fin_sum * 100
    print("  {:12s}  {:>8,}  ({:.1f}%)".format(fin, int(qty), pct))

print()
print("--- Junk products (defined but NOT in df) ---")
all_prods = set(df['Product'].unique())
print("  Excluded OK:", sorted(_JUNK_PRODUCTS - all_prods)[:10], "...")
print("  Still present (should be empty):", sorted(_JUNK_PRODUCTS & all_prods))
