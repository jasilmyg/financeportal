"""
ai_semantic_layer.py
--------------------
Domain knowledge, ClickHouse schema documentation, reusable SQL macros,
and dynamic system prompt construction for the myG Finance Portal AI Agent.

All SQL macros mirror `clickhouse_aggregator.py` verbatim so ad-hoc SQL queries
written by the AI Agent achieve 100% numeric parity with the live dashboard.
"""

from __future__ import annotations

import json
import re
from typing import Any

import clickhouse_aggregator as ca
import ai_memory


# ---------------------------------------------------------------------------
# Canonical ClickHouse SQL Macros (safe substitution in run_readonly_sql)
# ---------------------------------------------------------------------------
def get_sql_macros() -> dict[str, str]:
    """Build reusable SQL fragments directly from clickhouse_aggregator constants."""
    do_clean = ca._RAW_DO_CLEAN
    prod_expr = f"({ca._RAW_PROD_EXPR})"
    state_expr = (
        "CASE "
        "WHEN upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('RENJITH','ALFAS','SAMEER','VAISAKH','MAHESH','SAMEEM') "
        " AND upper(trim(s.branch)) NOT IN ('CSB','GPF','3GH','KSO','SMC','FLF','FALNIR FUTURE') THEN 'KERALA' "
        "WHEN upper(trim(coalesce(bm.district, ''))) = 'DAKSHINA KANNADA' "
        " OR upper(trim(s.branch)) IN ('FLF','FALNIR FUTURE') THEN 'KARNATAKA' "
        "ELSE 'OTHERS' END"
    )
    paper_fin_expr = f"""CASE
        WHEN s.invoice_no IN ('26-I-CHSY-13671', '26-I-RAM-5618') THEN 'BAJAJ'
        WHEN match({do_clean}, '^B[0-9]') THEN 'BAJAJ'
        WHEN position({do_clean}, 'DMI') > 0 THEN 'DMI'
        WHEN startsWith({do_clean}, '10108') OR startsWith({do_clean}, '10109') THEN 'IDFC'
        WHEN startsWith({do_clean}, 'CDSK') THEN 'PFL'
        WHEN position({do_clean}, '3022CD') > 0 OR position({do_clean}, '3023CD') > 0 THEN 'TVS'
        WHEN match({do_clean}, '^[1-9][0-9]{{10,11}}$')
          AND NOT startsWith({do_clean}, '1018') AND NOT startsWith({do_clean}, '1019')
          AND NOT startsWith({do_clean}, '10208') AND NOT startsWith({do_clean}, '11090')
          AND NOT startsWith({do_clean}, '3022') AND NOT startsWith({do_clean}, '3023')
          AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%BENOW%' THEN 'HDB'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%' OR upper(coalesce(inv.financier_code, '')) LIKE 'BAJAJ%' THEN 'BAJAJ'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%HDB%' OR upper(coalesce(inv.financier_code, '')) LIKE 'HDB%' THEN 'HDB'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%DMI%' OR upper(coalesce(inv.financier_code, '')) LIKE 'SAMSUNG%' THEN 'DMI'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%' OR upper(coalesce(inv.financier_code, '')) LIKE 'IDFC%' THEN 'IDFC'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%TVS%' OR upper(coalesce(inv.financier_code, '')) LIKE 'TVSC%' THEN 'TVS'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%' OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%' OR upper(coalesce(inv.financier_code, '')) LIKE 'POONA%' THEN 'PFL'
        ELSE 'aCASH'
    END"""

    cat_expr = f"""CASE
        WHEN {prod_expr} IN ('MOBILE','TABLET','TAB') THEN 'MOBILE'
        WHEN {prod_expr} IN ('LAPTOP','DESKTOP') THEN 'LAPTOP'
        WHEN {prod_expr} LIKE '%SMALL APPLIANCE%' THEN 'SA'
        WHEN {prod_expr} IN ('AIR CONDITIONER','DISH WASHER','DRYER','FREEZER','HOME APPLIANCES','MICROWAVE OVEN','REFRIGERATORS','TV','WASHING MACHINES') THEN 'CD PRODUCTS'
        WHEN {prod_expr} IN ('AUDIO','CEGI','EAR WEARABLES','GAMING','HOME THEATRE','SMART WATCH') THEN 'HVA'
        ELSE 'OTHERS'
    END"""

    qty_expr = "toInt64(if(toFloat64(s.sold_price) < 0, -abs(toInt64(coalesce(s.qty, 0))), abs(toInt64(coalesce(s.qty, 0)))))"
    val_expr = "round(toFloat64(coalesce(s.sold_price, 0)), 2)"

    return {
        "DO_CLEAN": do_clean,
        "PROD_EXPR": prod_expr,
        "CAT_EXPR": cat_expr,
        "STATE_EXPR": state_expr,
        "PAPER_FIN_EXPR": paper_fin_expr,
        "QTY_EXPR": qty_expr,
        "VAL_EXPR": val_expr,
        "ZONE_EXPR": "if(upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('ALFAS','SAMEER','SAMEEM'), 'NORTH', if(upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('RENJITH','MAHESH','VAISAKH'), 'SOUTH', 'OTHERS'))",
        "BASE_FILTER": (
            "abs(toFloat64(s.sold_price)) > 3000 "
            "AND upper(coalesce(inv.customer_type, '')) != 'CORPORATE' "
            "AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%' "
            "AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%' "
            "AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%MYG ONLINE%'"
        ),
    }


def expand_sql_macros(sql: str) -> str:
    """Replace {{MACRO_NAME}} tokens in SQL with canonical portal expressions."""
    macros = get_sql_macros()
    out = sql
    for name, expr in macros.items():
        out = out.replace("{{" + name + "}}", f"({expr})")
    return out


# ---------------------------------------------------------------------------
# Schema & Domain Knowledge Reference
# ---------------------------------------------------------------------------
SCHEMA_GUIDE = """
=== myG FINANCE PORTAL — LIVE CLICKHOUSE SCHEMA & BUSINESS RULES ===

1. PRIMARY TABLES:
   - `azure_sales_report` (alias `s`): line-item retail sales (~36M+ rows)
     Columns: `date` (Date/DateTime), `invoice_no` (String), `branch` (String branch code),
              `item_code` (String), `imei_batch` (String), `qty` (Int/Float), `sold_price` (Float64).
   - `azure_invoice_report` (alias `inv`, join `s.invoice_no = inv.invoice_no`):
     Columns: `invoice_no`, `financier_name`, `financier_code`, `customer_type`, `rbm`, `bdm`.
   - `item_master` (alias `i`, join `s.item_code = i.item_code`):
     Columns: `item_code`, `product`, `brand`, `category`, `item_name`.
   - `branch_master` (alias `bm`, join `upper(trim(s.branch)) = upper(trim(bm.code))`):
     Columns: `code`, `branch_name`, `rbm`, `bdm`, `district`, `store_type`.
   - `sales_data` (alias `sd`, join `s.invoice_no = sd.invoice_number`, aggregate with `any()` by `invoice_number` first to avoid fanout!):
     Columns: `invoice_number`, `delivery_order_no`, `staff`, `customer_name`, `customer_mobile`.
   - Card EMI settlement tables (store-level / invoice-level uploaded files):
     * `pine_lab_transactions` (`date`, `store`, `amount`, `qty`, `product`, `category`, `brand`, `issuer_bank`, `data_month`)
     * `paytm_transactions` (`date`, `store`, `amount`, `qty`, `product`, `category`, `brand`, `data_month`)
     * `benow_transactions` (`date`, `store`, `amount`, `qty`, `product`, `category`, `brand`, `data_month`)
     * `benow_pine_financier` (`invoice_no`, `imei`, `financier`, `data_month`)
   - `tm_fin_targets`: monthly TM (Fin BDM) targets (`month` 'YYYY-MM', `tm`, `value_target`, `card_slab_1`, `card_slab_2`).

2. CORE BUSINESS RULES:
   - Retail filter: `abs(sold_price) > 3000`, `customer_type != 'CORPORATE'`, exclude E-commerce (`ECOMMERCE`, `E-COMMERCE`, `MYG ONLINE`).
   - Returns: when `sold_price < 0`, quantity is negative (`-abs(qty)`).
   - Paper Financiers (6): `BAJAJ`, `HDB`, `IDFC`, `PFL` (Poonawalla), `TVS`, `DMI`.
   - Card EMI Financiers (3): `PineLab`, `PAYTM`, `Benow`.
   - Total Finance (`FIN`) = Paper Finance + Card EMI; Non-financed = `aCASH`.
   - `Fin Share %` = `FIN / TOTAL * 100` (computed separately for Value in ₹ / Cr and Volume in Qty).
   - Zones (Kerala retail):
     * NORTH RBMs: `ALFAS`, `SAMEER`, `SAMEEM`
     * SOUTH RBMs: `RENJITH`, `MAHESH`, `VAISAKH`
   - Categories: `MOBILE`, `LAPTOP`, `CD PRODUCTS`, `SA`, `HVA`, `OTHERS`.
   - Price Slabs: Below 10K, 10K-15K, 15K-20K, 20K-30K, 30K-40K, 40K-50K, 50K-75K, 75K-100K, 100K-150K, 150K-200K, Above 200K.
   - Currency: Indian Rupees (₹). Express large totals in Crores (1 Cr = ₹1,00,00,000 = 1e7) or Lakhs (1 L = ₹1,00,000 = 1e5).

3. REUSABLE SQL MACROS (use inside `run_readonly_sql` with table aliases `s`, `inv`, `i`, `bm`, `sd`):
   - `{{PAPER_FIN_EXPR}}` -> exact Paper Financier ('BAJAJ','HDB','IDFC','PFL','TVS','DMI','aCASH') with DO overrides
   - `{{CAT_EXPR}}`       -> exact Category ('MOBILE','LAPTOP','CD PRODUCTS','SA','HVA','OTHERS')
   - `{{PROD_EXPR}}`      -> exact Product name with item-code fallback
   - `{{STATE_EXPR}}`     -> 'KERALA' | 'KARNATAKA' | 'OTHERS'
   - `{{ZONE_EXPR}}`      -> 'NORTH' | 'SOUTH' | 'OTHERS'
   - `{{QTY_EXPR}}`       -> return-signed Int64 qty
   - `{{VAL_EXPR}}`       -> rounded Float64 sold_price
   - `{{DO_CLEAN}}`       -> normalized uppercase delivery_order_no
   - `{{BASE_FILTER}}`    -> standard >3000 & non-corporate & non-ecommerce WHERE condition
   IMPORTANT: Always filter `toDate(s.date)` FIRST for fast partition pruning!
"""


def build_system_prompt(context: dict[str, Any] | None = None) -> str:
    """Construct the dynamic system prompt with active portal context, learned memories, and golden SQL."""
    ctx = context or {}
    memories = ai_memory.list_memories(limit=30)
    golden = ai_memory.list_golden_queries(limit=10)
    dash_cfg = ai_memory.get_dashboard_config()

    mem_block = ""
    if memories:
        lines = [f"- [{m['category']}] {m['key_topic']}: {m['content']}" for m in memories]
        mem_block = "\n=== LEARNED BUSINESS MEMORY (ALWAYS RESPECT THESE) ===\n" + "\n".join(lines) + "\n"

    gq_block = ""
    if golden:
        lines = [f"- Q: {g['question_pattern']} | SQL: {g['sql_query'][:260]}" for g in golden[:6]]
        gq_block = "\n=== VERIFIED GOLDEN SQL EXAMPLES ===\n" + "\n".join(lines) + "\n"

    widgets = (dash_cfg.get("config") or {}).get("widgets") or []
    widget_summary = (
        f"Current custom widgets ({len(widgets)}): "
        + ", ".join(f"{w.get('id')} ({w.get('title')} on {w.get('tab')})" for w in widgets[:12])
        if widgets
        else "No custom widgets pinned yet."
    )

    active_ctx = json.dumps({
        "active_tab": ctx.get("active_tab", "dashboard"),
        "date": ctx.get("date", ""),
        "end_date": ctx.get("end_date", ""),
        "timeframe": ctx.get("timeframe", "monthly"),
        "state": ctx.get("state", "KERALA"),
        "source": ctx.get("source", "clickhouse"),
        "oct2_view_by": ctx.get("oct2_view_by", "store"),
        "oct2_compare": ctx.get("oct2_compare", "none"),
        "oct2_fin_split": ctx.get("oct2_fin_split", "total"),
    }, ensure_ascii=False)

    return f"""You are **myG Finance AI**, the inbuilt Principal Finance Analyst, Data Scientist, and Dashboard Engineer inside the **myG Finance Automation Portal** (Kerala & Karnataka retail consumer electronics finance analytics).

=== YOUR CAPABILITIES & OPERATING RULES ===
1. **ZERO HALLUCINATION:** NEVER invent or guess numbers. Always call live tools first (`get_dashboard_summary`, `get_store_fin_share`, `get_brand_and_price_analysis`, `get_targets_and_pacing`, `run_deep_analysis`, `generate_morning_brief`, or `run_readonly_sql`).
2. **TOOL SELECTION PRIORITY:**
   - For overall KPIs, Category, Financier, or Zone summaries matching portal tabs → call `get_dashboard_summary` (100% exact portal parity including uploaded Card EMI files!).
   - For Store / RBM / BDM / Fin BDM (TM) / Staff Fin Share, rankings, and period-over-period comparisons → call `get_store_fin_share`.
   - For Brand or Price Slab breakdowns → call `get_brand_and_price_analysis`.
   - For TM targets, Card Slabs, or Global Targets → call `get_targets_and_pacing`.
   - For "deep analysis", "why did it drop", "root cause", "audit", "anomalies", or "insights" → call `run_deep_analysis` (and supplement with `run_readonly_sql` if needed).
   - For any custom ad-hoc question (specific invoices, DO mismatches, store+brand+lender cross-cuts, day-of-week trends, staff-on-iPhone conversion, etc.) → call `run_readonly_sql` using the schema & macros below.
3. **DASHBOARD CONTROL & EDITING:**
   - **Live UI Control (Tier 1):** Whenever the user asks to open a tab, switch period/timeframe/state, change Duplicate Store Fin Share view/comparison/split, or highlight rows → call `control_dashboard_ui`.
   - **Persistent Dashboard Studio & Target Edits (Tier 2):** Whenever the user asks to add/edit/remove a custom KPI card, chart, or live table widget, change Fin Share color thresholds, hide/rename a section, or update Global/TM Targets → call `propose_dashboard_edit`. This creates a safe Preview & Confirm Diff Card with 1-click Undo.
   - **Code Edit Proposals (Tier 3):** If the user explicitly asks to modify portal source code (`index.html`, `main.js`, `style.css`, `app.py`, etc.) → call `propose_code_patch` to generate a syntax-checked unified diff for explicit approval.
4. **CONTINUOUS LEARNING:** Whenever the user teaches you a business rule, store closure/exception, nickname, or custom definition (e.g., "remember that...", "note that...", "from now on..."), immediately call `save_business_memory` so you remember it permanently!
5. **EXECUTIVE COMMUNICATION STYLE:**
   - Be sharp, structured, and quantified: lead with the bottom-line headline, back it with exact ₹ Cr / Lakhs / Units / % figures, show tables/charts where helpful, and end with concrete prioritized actions when giving insights.
   - Use Indian Rupee formatting (₹, Cr, L) and exact percentages (e.g., `61.2%`, `+2.4 pp`).
   - Respect the user's current active portal context unless they ask for a different date/period/state.

=== CURRENT PORTAL CONTEXT ===
{active_ctx}
{widget_summary}
{mem_block}{gq_block}
{SCHEMA_GUIDE}
"""
