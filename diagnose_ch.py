"""
diagnose_ch.py — Raw ClickHouse row count diagnostic
Run: python diagnose_ch.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from clickhouse_client import query_df

# ── 1. Raw row count in azure_sales_report for the exact date range ──────────
print("\n=== RAW ROW COUNT (azure_sales_report, 01-Jul to 20-Jul 2026) ===")
df = query_df("""
    SELECT count() AS total_rows,
           sum(toFloat64(qty)) AS total_qty,
           sum(toFloat64(sold_price)) AS total_value
    FROM azure_sales_report
    WHERE toDate(date) BETWEEN '2026-07-01' AND '2026-07-20'
""", cache=False)
print(df.to_string(index=False))

# ── 2. How many rows survive after joining item_master ───────────────────────
print("\n=== ROWS SURVIVING LEFT JOIN WITH item_master ===")
df2 = query_df("""
    SELECT count() AS rows_after_join,
           countIf(i.item_code IS NULL) AS rows_with_no_item_master_match,
           countIf(i.item_code IS NOT NULL) AS rows_with_item_master_match,
           sum(toFloat64(s.qty)) AS total_qty
    FROM azure_sales_report s
    LEFT JOIN item_master i ON s.item_code = i.item_code
    WHERE toDate(s.date) BETWEEN '2026-07-01' AND '2026-07-20'
""", cache=False)
print(df2.to_string(index=False))

# ── 3. How many rows survive after joining azure_invoice_report ──────────────
print("\n=== ROWS SURVIVING LEFT JOIN WITH azure_invoice_report ===")
df3 = query_df("""
    SELECT count() AS rows_after_join,
           countIf(inv.invoice_no IS NULL) AS rows_with_no_invoice_match,
           countIf(inv.invoice_no IS NOT NULL) AS rows_with_invoice_match
    FROM azure_sales_report s
    LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
    WHERE toDate(s.date) BETWEEN '2026-07-01' AND '2026-07-20'
""", cache=False)
print(df3.to_string(index=False))

# ── 4. Distinct branches in ClickHouse for this date range ──────────────────
print("\n=== DISTINCT BRANCHES IN CLICKHOUSE (Jul 1-20 2026) ===")
df4 = query_df("""
    SELECT branch, sum(toFloat64(qty)) AS qty
    FROM azure_sales_report
    WHERE toDate(date) BETWEEN '2026-07-01' AND '2026-07-20'
    GROUP BY branch
    ORDER BY qty DESC
    LIMIT 30
""", cache=False)
print(df4.to_string(index=False))

# ── 5. Category breakdown in item_master ────────────────────────────────────
print("\n=== ITEM_MASTER CATEGORY BREAKDOWN ===")
df5 = query_df("""
    SELECT category, count() AS item_count
    FROM item_master
    GROUP BY category
    ORDER BY item_count DESC
""", cache=False)
print(df5.to_string(index=False))
