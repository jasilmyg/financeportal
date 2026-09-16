"""
diagnose_ch2.py — Trace exactly where QTY is lost in the pipeline
Run: python diagnose_ch2.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from clickhouse_client import query_df

DATE_START = "2026-07-01"
DATE_END   = "2026-07-20"

# ── Step 1: What does the actual portal SQL return? ──────────────────────────
print("\n=== STEP 1: Portal SQL (what the aggregator actually fetches) ===")
df = query_df(f"""
SELECT
    toDate(s.date)         AS Date,
    s.branch               AS Branch,
    inv.rbm                AS RBM,
    inv.bdm                AS BDM,
    inv.financier_name     AS Financier_Raw,
    i.product              AS Product,
    i.brand                AS Brand,
    i.category             AS Category_Raw,
    sum(toFloat64(s.qty))  AS QTY,
    sum(toFloat64(s.sold_price)) AS SoldPrice
FROM azure_sales_report s
LEFT JOIN item_master i ON s.item_code = i.item_code
LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
WHERE toDate(s.date) BETWEEN '{DATE_START}' AND '{DATE_END}'
  AND (i.category IS NULL OR i.category NOT IN ('VALUE ADDED SERVICE','ENDPOINT PROTECTION','INFRA','PROJECT'))
GROUP BY Date, Branch, RBM, BDM, Financier_Raw, Product, Brand, Category_Raw
HAVING QTY > 0
""", cache=False)
print(f"Rows returned by SQL: {len(df)}")
print(f"Total QTY in SQL result: {df['QTY'].sum():,.0f}")
print(f"\nCategory_Raw breakdown:")
print(df.groupby('Category_Raw')['QTY'].sum().sort_values(ascending=False).to_string())
print(f"\nProduct top-20 by QTY:")
print(df.groupby('Product')['QTY'].sum().sort_values(ascending=False).head(20).to_string())
print(f"\nFinancier_Raw top-20 by QTY:")
print(df.groupby('Financier_Raw')['QTY'].sum().sort_values(ascending=False).head(20).to_string())
