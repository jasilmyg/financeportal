"""
clickhouse_aggregator.py
------------------------
Fetches live sales data from ClickHouse and returns a pandas DataFrame
whose column names are **identical** to those expected by dashboard_aggregator.py.

The data lives across three tables that must be JOINed:

  azure_sales_report   — line-item sales (date, branch, item_code, qty, sold_price)
         ↓ item_code
  item_master          — product catalogue (product, brand, category)
         ↓ invoice_no
  azure_invoice_report — invoice-level info (financier_name, rbm, bdm)

Public API
----------
get_clickhouse_df(date_str, timeframe, rbm, bdm, store, staff)
    -> pandas.DataFrame  (or raises on error)
"""

import logging
from datetime import datetime

import pandas as pd

from clickhouse_client import query_df

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Financier name mapping  (ClickHouse raw → portal standard)
# Mirrors sales_cleaner.py step 4 exactly:
#   BAJAJ/HDB/DMI/IDFC/TVS/PFL → respective finance names
#   BENOW/PINELAB/PAYTM/CO-OP/SOCIETY/CASH variants → aCASH  (same as Excel)
#   Empty / unknown → aCASH
# ---------------------------------------------------------------------------
# RBMs excluded in sales_cleaner.py step 1
_REMOVE_RBM: frozenset[str] = frozenset({"GENERAL", "RAJESH"})

# Ecommerce financier pattern (step 3)
_ECOMMERCE_PATTERN = "ECOMMERCE|E-COMMERCE"

# Minimum sold price (step 2) — mirrors sales_cleaner.py
_MIN_PRICE = 3000.0

# Category mapping from item_master categories → portal standard categories
# item_master uses broader categories; we remap to portal's PRODUCT_CATEGORY_MAP groups
_CAT_MAP: dict[str, str] = {
    "TELECOM":              "MOBILE",
    "CONSUMER ELECTRONICS": "CD PRODUCTS",  # will be refined by product below
    "IT":                   "LAPTOP",
    "HOME SOLUTIONS":       "CD PRODUCTS",
    "ACCESSORIES":          "OTHERS",
    "INFRA":                "OTHERS",
    "ENDPOINT PROTECTION":  "OTHERS",
    "PROJECT":              "OTHERS",
    "VALUE ADDED SERVICE":  "OTHERS",
    "OTHERS":               "OTHERS",
}

# Fine-grained product → portal category (mirrors PRODUCT_CATEGORY_MAP in sales_cleaner)
_PRODUCT_CAT_MAP: dict[str, str] = {
    # CD PRODUCTS
    "AIR CONDITIONER":  "CD PRODUCTS",
    "AC OUTDOOR":       "CD PRODUCTS",   # outdoor unit is part of AC sale
    "DISH WASHER":      "CD PRODUCTS",
    "DRYER":            "CD PRODUCTS",
    "FREEZER":          "CD PRODUCTS",
    "HOME APPLIANCES":  "CD PRODUCTS",
    "HOUSE HOLD":       "CD PRODUCTS",
    "MICROWAVE OVEN":   "CD PRODUCTS",
    "REFRIGERATORS":    "CD PRODUCTS",
    "TV":               "CD PRODUCTS",
    "WASHING MACHINES": "CD PRODUCTS",
    # HVA
    "AUDIO":            "HVA",
    "EAR WEARABLES":    "HVA",
    "GAMING":           "HVA",
    "HOME THEATRE":     "HVA",
    "SMART WATCH":      "HVA",
    # LAPTOP
    "LAPTOP":           "LAPTOP",
    "DESKTOP":          "LAPTOP",
    # MOBILE
    "MOBILE":           "MOBILE",
    "TABLET":           "MOBILE",
    # SA
    "SMALL APPLIANCES": "SA",
    # OTHERS (valid accessories/niche products)
    "CAMERA":           "OTHERS",
    "CCTV":             "OTHERS",
    "DIY":              "OTHERS",
    "IT ACCESSORIES":   "OTHERS",
    "PRINTER":          "OTHERS",
    "PROJECTOR":        "OTHERS",
    "STABILIZER":       "OTHERS",
    "STORAGE DEVICES":  "OTHERS",
    "MYG VERSE":        "OTHERS",
    "MYG DOMO":         "OTHERS",
    "RIG":              "OTHERS",
}

# Products to strip entirely — non-sellable / service / internal / junk items
_JUNK_PRODUCTS: frozenset[str] = frozenset({
    # Spares & internal
    "D SPARE", "SPARE", "GDP-SPARE",
    # Accessories that aren't retail items
    "ACCESSORIES", "DEMO ACCESSORIES", "PRINTER ACCESSORIES",
    "LAPTOP BAG", "LENS & BODY",
    # Non-consumer items
    "FIXED ASSETS", "INFRA", "IT PRODUCT",
    # Services / warranties
    "LG AMC", "CARE PLUS", "PROTECT MAX", "OSG WARRANTY",
    "VALUE ADDED SERVICE",
    "SERVICE",           # generic service SKU (repairs, installation etc.)
    "SERVICE CHARGES",   # billing line item, not a product
    "GDOT CARE",         # extended warranty / care plan
    # Scheme / offer SKUs (non-product billing lines)
    "OFFER KIT", "SCHEME", "SMART CHOICE EXT",
    # Demo / promo items
    "DEMO", "DEMO LAPTOP", "DUMMY",
    # Miscellaneous junk
    "CROCKERY", "DETERGENT", "CEGI",
    "GLAMSHIELD", "FRAGRANCE", "WATCH",
    "PERSONAL CARE", "STATIONERY ITEMS", "GIFT ITEMS",
    "SMART CHOICE",
    "PROTECTION", "TOTAL SECURITY", "MOBILE ANTIVIRUS",
    "TOTAL LOSS", "FOC", "CONTRACT WORK",
    "TOYS", "MONITOR",
    "HA ACCESSORIES",  # accessory SKUs, not main products
    "ITEMS",
    "RECHARGE",          # mobile recharge, not a product
})

# ClickHouse item_master categories to EXCLUDE at SQL level
# (speeds up query by dropping entire segments before GROUP BY)
# NOTE: "OTHERS" is intentionally NOT excluded here — it contains real sellable products
# (cameras, projectors, accessories) that ARE counted in the Excel report.
# Junk items within OTHERS are cleaned in Python using _JUNK_PRODUCTS blacklist.
_EXCLUDED_CH_CATEGORIES: tuple[str, ...] = (
    "VALUE ADDED SERVICE",  # warranties/AMC
    "ENDPOINT PROTECTION",  # antivirus
    "INFRA",             # internal IT
    "PROJECT",           # contract work
)


def _map_financier(raw: str) -> str:
    """Map a raw ClickHouse financier_name to the portal standard name.

    Mirrors sales_cleaner.py step 4 exactly.
    Order of precedence (same as sales_cleaner):
      1. BAJAJ (contains 'BAJAJ')
      2. HDB   (contains 'HDB')
      3. DMI   (contains 'DMI')
      4. IDFC  (contains 'IDFC')
      5. TVS   (contains 'TVS')
      6. PFL   (contains 'POONAWALLA' or 'PFL')
      7. aCASH — BENOW, PINELAB, PAYTM, CO-OP, SOCIETY, CASH variants, DELHIVERY, OFFICIUM, etc.
      8. aCASH — empty / unrecognised
    """
    cleaned = (raw or "").strip().upper()
    if not cleaned or cleaned in ('', 'NAN', 'NONE'):
        return "aCASH"
    if 'BAJAJ' in cleaned:
        return "BAJAJ"
    if 'HDB' in cleaned:
        return "HDB"
    if 'DMI' in cleaned:
        return "DMI"
    if 'IDFC' in cleaned:
        return "IDFC"
    if 'TVS' in cleaned:
        return "TVS"
    if 'POONAWALLA' in cleaned or 'PFL' in cleaned:
        return "PFL"
    # Everything below is treated as cash — mirrors sales_cleaner cash_pattern:
    # 'BENOW|PINELAB|PAYTM|DELHIVERY|OFFICIUM|COCHIN|CASH|ACASH|CO-OP|COOP|SOCIETY|THAYANOOR'
    _CASH_KEYWORDS = (
        'BENOW', 'PINELAB', 'PINE LAB', 'PAYTM',
        'DELHIVERY', 'OFFICIUM', 'COCHIN',
        'CASH', 'ACASH',
        'CO-OP', 'COOP', 'SOCIETY', 'THAYANOOR',
    )
    for kw in _CASH_KEYWORDS:
        if kw in cleaned:
            return "aCASH"
    # Unrecognised → cash
    return "aCASH"


def _map_category(product: str, ch_category: str) -> str:
    """Map item_master product/category to a portal standard category."""
    prod_upper = (product or "").strip().upper()
    cat_upper = (ch_category or "").strip().upper()
    # Product-level override wins
    if prod_upper in _PRODUCT_CAT_MAP:
        return _PRODUCT_CAT_MAP[prod_upper]
    return _CAT_MAP.get(cat_upper, "OTHERS")


def _build_date_filter(date_str: str, timeframe: str, end_date_str: str = None) -> tuple[str, dict]:
    """
    Return (WHERE_fragment, params) for the requested timeframe.

    Uses ClickHouse {name:Type} parameterised query syntax.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")

    if timeframe == "daily":
        sql = "toDate(s.date) = {target_date:Date}"
        params = {"target_date": dt.date()}
    elif timeframe == "monthly":
        sql = "toYear(s.date) = {yr:UInt16} AND toMonth(s.date) = {mo:UInt8}"
        params = {"yr": dt.year, "mo": dt.month}
    elif timeframe == "quarterly":
        quarter_start_month = ((dt.month - 1) // 3) * 3 + 1
        quarter_end_month = quarter_start_month + 2
        sql = (
            "toYear(s.date) = {yr:UInt16} "
            "AND toMonth(s.date) BETWEEN {mo_start:UInt8} AND {mo_end:UInt8}"
        )
        params = {
            "yr": dt.year,
            "mo_start": quarter_start_month,
            "mo_end": quarter_end_month,
        }
    elif timeframe == "custom":
        dt_end = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else dt
        sql = "toDate(s.date) BETWEEN {start_date:Date} AND {end_date:Date}"
        params = {
            "start_date": dt.date(),
            "end_date": dt_end.date()
        }
    else:
        # All data — no date filter
        sql = "1 = 1"
        params = {}

    return sql, params


def get_clickhouse_df(
    date_str: str,
    timeframe: str = "monthly",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    staff: str = "",
    end_date_str: str = None,
) -> pd.DataFrame:
    """
    Fetch sales data from ClickHouse and return a DataFrame with columns:

        Date, Branch, RBM, BDM, Financier, Category, Product, Brand, QTY, Sold Price

    These column names exactly match what dashboard_aggregator.py expects,
    so the existing aggregation logic can be reused without modification.

    Parameters
    ----------
    date_str  : ISO date string, e.g. "2026-08-01"
    timeframe : "daily" | "monthly" | "quarterly"
    rbm       : Optional RBM filter (staff code string as stored in ClickHouse)
    bdm       : Optional BDM filter
    store     : Optional branch name filter
    staff     : Ignored for ClickHouse (not in the 3-table join; kept for API parity)
    """
    date_filter, params = _build_date_filter(date_str, timeframe, end_date_str)

    # Additional WHERE clauses for hierarchy filters
    extra_filters = []
    if rbm:
        extra_filters.append("inv.rbm = {rbm:String}")
        params["rbm"] = rbm
    if bdm:
        extra_filters.append("inv.bdm = {bdm:String}")
        params["bdm"] = bdm
    if store:
        extra_filters.append("s.branch = {store:String}")
        params["store"] = store

    where_clause = " AND ".join([date_filter] + extra_filters) if extra_filters else date_filter

    # Use coarser date granularity in GROUP BY to maximise pre-aggregation:
    #   daily     → keep exact date   (11K rows)
    #   monthly   → truncate to month start  (248K → ~5K)
    #   quarterly → truncate to quarter start (589K → ~5K)
    # dashboard_aggregator's year/month/quarter filters still match these dates.
    date_expr = {
        'daily':     'toDate(s.date)',
        'monthly':   'toStartOfMonth(s.date)',
        'quarterly': 'toStartOfQuarter(s.date)',
    }.get(timeframe, 'toDate(s.date)')

    # Build SQL-level exclusion list for known-junk item_master categories
    excluded_cats_sql = ", ".join(f"'{c}'" for c in _EXCLUDED_CH_CATEGORIES)

    # RBM exclusion list for SQL (mirrors sales_cleaner step 1)
    remove_rbm_sql = ", ".join(f"'{r}'" for r in _REMOVE_RBM)

    sql = f"""
SELECT
    {date_expr}                             AS Date,
    s.branch                                AS Branch,
    coalesce(bm.rbm, inv.rbm, '')           AS RBM,
    coalesce(bm.bdm, inv.bdm, '')           AS BDM,
    inv.financier_name                      AS Financier_Raw,
    inv.financier_code                      AS FC_Raw,
    i.product                               AS Product,
    i.brand                                 AS Brand,
    i.category                              AS Category_Raw,
    sum(toFloat64(s.qty))                   AS QTY,
    sum(toFloat64(s.sold_price))            AS `Sold Price`
FROM azure_sales_report s
LEFT JOIN item_master i
       ON s.item_code = i.item_code
LEFT JOIN azure_invoice_report inv
       ON s.invoice_no = inv.invoice_no
LEFT JOIN branch_master bm
       ON upper(trim(s.branch)) = upper(trim(bm.code))
WHERE {where_clause}
  AND (i.category IS NULL OR i.category NOT IN ({excluded_cats_sql}))
  AND toFloat64OrZero(toString(s.sold_price)) > {_MIN_PRICE}
  AND upper(trim(coalesce(bm.rbm, inv.rbm, ''))) NOT IN ({remove_rbm_sql})
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%'
GROUP BY Date, Branch, RBM, BDM, Financier_Raw, FC_Raw, Product, Brand, Category_Raw
HAVING QTY > 0
"""

    logger.info(
        "ClickHouse query: timeframe=%s date=%s rbm=%s bdm=%s store=%s",
        timeframe, date_str, rbm or "*", bdm or "*", store or "*",
    )

    df = query_df(sql, params)

    if df.empty:
        logger.info("ClickHouse returned 0 rows for %s / %s", date_str, timeframe)
        return df

    # ------------------------------------------------------------------
    # Post-process: map raw ClickHouse values to portal standard names
    # ------------------------------------------------------------------

    # 1. Financier mapping — two-pass, mirrors sales_cleaner.py:
    #    Pass A: name-based mapping (financier_name → portal name)
    #    Pass B: financier_code override (ClickHouse equivalent of DO-number override)
    #    The financier_code is a short code that definitively identifies the lender,
    #    same way DO patterns work in the Excel pipeline.
    df["Financier"] = df["Financier_Raw"].apply(_map_financier)

    # Pass B — financier_code overrides (mirrors sales_cleaner DO-number logic)
    # Mapping of financier_code patterns → portal financier name
    _FC_MAP = {
        # Exact code → name
        "BAJAJ_FIN":    "BAJAJ",
        "BAJAJCD_FIN":  "BAJAJ",    # CD = Consumer Durable Bajaj
        "HDB_FIN":      "HDB",
        "IDFC_FIN":     "IDFC",
        "TVSC_FIN":     "TVS",
        "POONA_FIN":    "PFL",
        "SAMSUNG_FIN":  "DMI",      # Samsung Finance = DMI Finance
        "BENOW_FIN":    "aCASH",    # Benow = card/cash
        "ECOMMERCE_FIN": None,      # E-commerce rows already filtered in SQL
    }
    fc = df["FC_Raw"].fillna("").astype(str).str.strip().str.upper()
    for code, fin in _FC_MAP.items():
        if fin is None:
            continue
        mask = fc == code
        if mask.any():
            df.loc[mask, "Financier"] = fin
            logger.info("FC override [%s] → %s: %d rows", code, fin, mask.sum())

    # Also handle prefix-based overrides (mirrors DO prefix patterns):
    # BAJAJ* → BAJAJ, HDB* → HDB, etc.
    for prefix, fin in [("BAJAJ", "BAJAJ"), ("HDB", "HDB"), ("IDFC", "IDFC"),
                        ("TVSC", "TVS"), ("POONA", "PFL"), ("SAMSUNG", "DMI")]:
        mask = fc.str.startswith(prefix, na=False) & (df["Financier"] == "aCASH")
        if mask.any():
            df.loc[mask, "Financier"] = fin
            logger.info("FC prefix override [%s*] → %s: %d rows", prefix, fin, mask.sum())

    df = df.drop(columns=["Financier_Raw", "FC_Raw"])

    # 2. Category mapping (product-aware, falls back to raw category)
    df["Category"] = df.apply(
        lambda r: _map_category(r["Product"], r["Category_Raw"]), axis=1
    )
    df = df.drop(columns=["Category_Raw"])

    # 3. Normalise text columns to UPPER STRIP (matches dashboard_aggregator conventions)
    for col in ["Branch", "RBM", "BDM", "Product", "Brand", "Category"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip().str.upper()

    # 4. Fix empty brand names
    df["Brand"] = df["Brand"].replace("", "UNKNOWN")
    df.loc[df["Brand"].str.strip() == "", "Brand"] = "UNKNOWN"

    # 5. Python-level junk product filter (Layer 2 cleaning)
    #    Uses a BLACKLIST (same approach as sales_cleaner.py) instead of a whitelist,
    #    so product name variations in item_master don't silently drop valid rows.
    before = len(df)
    df = df[~df["Product"].isin(_JUNK_PRODUCTS)]
    logger.info(
        "Junk-product blacklist removed %d rows (%d → %d)",
        before - len(df), before, len(df),
    )

    # 6. Ensure Date is datetime
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # 7. Numeric safety
    df["QTY"] = pd.to_numeric(df["QTY"], errors="coerce").fillna(0)
    df["Sold Price"] = pd.to_numeric(df["Sold Price"], errors="coerce").fillna(0.0)

    logger.info("ClickHouse DataFrame (cleaned): %d rows, columns: %s", len(df), list(df.columns))
    return df
