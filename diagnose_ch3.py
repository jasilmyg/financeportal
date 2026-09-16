"""
diagnose_ch3.py — Check QTY after applying the same filters as the portal
Run: python diagnose_ch3.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from clickhouse_client import query_df
from clickhouse_aggregator import get_clickhouse_df, _JUNK_PRODUCTS, _PRODUCT_CAT_MAP, _CAT_MAP

DATE_START = "2026-07-01"
DATE_END   = "2026-07-20"

# ── Get the full SQL result first ────────────────────────────────────────────
print("\n=== Raw SQL QTY by product (consumer electronics only) ===")
df = query_df(f"""
SELECT
    i.product              AS Product,
    i.category             AS Category_Raw,
    sum(toFloat64(s.qty))  AS QTY
FROM azure_sales_report s
LEFT JOIN item_master i ON s.item_code = i.item_code
LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
WHERE toDate(s.date) BETWEEN '{DATE_START}' AND '{DATE_END}'
  AND (i.category IS NULL OR i.category NOT IN ('VALUE ADDED SERVICE','ENDPOINT PROTECTION','INFRA','PROJECT'))
GROUP BY Product, Category_Raw
HAVING QTY > 0
ORDER BY QTY DESC
""", cache=False)

print(f"Total QTY (all products): {df['QTY'].sum():,.0f}")

# Apply junk blacklist
df_clean = df[~df['Product'].isin(_JUNK_PRODUCTS)]
print(f"After _JUNK_PRODUCTS blacklist: {df_clean['QTY'].sum():,.0f}")

# Show what's STILL present after blacklist
print("\nTop products REMAINING after blacklist:")
print(df_clean.groupby('Product')['QTY'].sum().sort_values(ascending=False).head(30).to_string())

# Show what's dropped
dropped = df[df['Product'].isin(_JUNK_PRODUCTS)]
print(f"\nDropped by blacklist ({dropped['QTY'].sum():,.0f} QTY):")
print(dropped.groupby('Product')['QTY'].sum().sort_values(ascending=False).to_string())

# Show products NOT in blacklist and NOT in product category map
known = frozenset(_PRODUCT_CAT_MAP.keys())
df_unmapped = df_clean[~df_clean['Product'].isin(known)]
print(f"\nProducts remaining but NOT in _PRODUCT_CAT_MAP ({df_unmapped['QTY'].sum():,.0f} QTY):")
print(df_unmapped.groupby(['Product','Category_Raw'])['QTY'].sum().sort_values(ascending=False).head(30).to_string())
