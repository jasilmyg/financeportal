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

import emi_state
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
    "AC OUTDOOR":       "CD PRODUCTS",
    "DISH WASHER":      "CD PRODUCTS",
    "DRYER":            "CD PRODUCTS",
    "FREEZER":          "CD PRODUCTS",
    "HOME APPLIANCES":  "CD PRODUCTS",
    "MICROWAVE OVEN":   "CD PRODUCTS",
    "REFRIGERATORS":    "CD PRODUCTS",
    "TV":               "CD PRODUCTS",
    "WASHING MACHINES": "CD PRODUCTS",
    # HVA
    "AUDIO":            "HVA",
    "CEGI":             "HVA",
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
    "TAB":              "MOBILE",
    # SA
    "SMALL APPLIANCES": "SA",
    # OTHERS
    "CAMERA":           "OTHERS",
    "CCTV":             "OTHERS",
    "DIY":              "OTHERS",
    "IT ACCESSORIES":   "OTHERS",
    "PRINTER":          "OTHERS",
    "PROJECTOR":        "OTHERS",
    "RIG":              "OTHERS",
    "STABILIZER":       "OTHERS",
}

# Products to strip entirely — matches BLACKLIST_PRODUCTS in sales_cleaner.py
_JUNK_PRODUCTS: frozenset[str] = frozenset({
    # From user spec
    "ACCESSORIES",
    "CARE PLUS",
    "CROCKERY",
    "D SPARE",
    "GLAM SHIELD",
    "GLAMSHIELD",
    "HOUSE HOLD",
    "HOUSEHOLD",
    "LG AMC",
    "OSG WARRANTY",
    "PERSONAL CARE",
    "PROTECT MAX",
    "SERVICE",
    "SMART CHOICE",
    "STORAGE DEVICES",
    # Additional — found by comparing with DATA.xlsx reference
    "GDOT CARE",
    "DEMO",
    "DEMO ACCESSORIES",
    "DEMO LAPTOP",
    "SCHEME",
    "MYG VERSE",
    "MYG DOMO",
    "GIFT ITEMS",
    "LAPTOP BAG",
    "FOC",
    "SERVICE CHARGES",
    "FRAGRANCE",
    "IT PRODUCT",
    "MONITOR",
    "CCTV",
    "SPARE",
    "AC OUTDOOR",
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
      7. Pine Lab EMI (contains 'PINE LAB' or 'PINELAB')
      8. PayTM EMI   (contains 'PAYTM')
      9. BENOW FINANCE (contains 'BENOW')
     10. aCASH — CO-OP, SOCIETY, CASH variants, DELHIVERY, OFFICIUM, etc.
     11. aCASH — empty / unrecognised
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
    # Cash-equivalent catch-all
    _CASH_KEYWORDS = (
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


def _emi_totals(table: str, financier: str, date_str: str, timeframe: str, end_date_str: str = None) -> dict:
    """
    {erp_category: qty} straight from an uploaded EMI file table, for the dashboard's
    selected period. Rows are selected purely by transaction date, limited to the dates the
    file(s) uploaded in this session actually cover - so changing the date picker (daily,
    monthly, quarterly or custom, even across month boundaries) always follows the file.
    """
    start, end = emi_state.range_of(date_str, timeframe, end_date_str)
    date_cond = emi_state.date_filter_sql(financier, start, end)
    if date_cond is None:      # no file uploaded for this period -> column stays empty
        return {}
    sql = f"""
        SELECT erp_category, sum(qty) AS total_qty
        FROM {table}
        WHERE erp_category != ''
          AND {date_cond}
        GROUP BY erp_category
    """
    res = query_df(sql, {})
    if res.empty:
        return {}
    return dict(zip(res['erp_category'].astype(str), res['total_qty'].astype(int)))


def get_pine_lab_totals(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
) -> dict:
    """
    Query pine_lab_transactions and return {erp_category: qty} for the given period.
    Counts come directly from the uploaded Pine Lab file (all transactions),
    not from ERP matching, so they represent the true full Pine Lab volume.
    Returns empty dict if table doesn't exist or has no data.
    """
    try:
        return _emi_totals('pine_lab_transactions', emi_state.PINE_FIN, date_str, timeframe, end_date_str)
    except Exception as e:
        logger.warning("get_pine_lab_totals failed: %s", e)
        return {}


def get_paytm_totals(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
) -> dict:
    """
    Query paytm_transactions and return {erp_category: qty} for the given period.
    """
    try:
        return _emi_totals('paytm_transactions', emi_state.PAYTM_FIN, date_str, timeframe, end_date_str)
    except Exception as e:
        logger.warning("get_paytm_totals failed: %s", e)
        return {}


def get_benow_totals(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
) -> dict:
    """
    Query benow_transactions and return {erp_category: qty} for the given period.
    NOTE: Benow raw files currently only contain IMEIs, so this will only return data if a category mapping is added to the Benow upload process.
    """
    try:
        return _emi_totals('benow_transactions', emi_state.BENOW_FIN, date_str, timeframe, end_date_str)
    except Exception as e:
        logger.warning("get_benow_totals failed: %s", e)
        return {}



def get_clickhouse_df(
    date_str: str,
    timeframe: str = "monthly",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    staff: str = "",
    end_date_str: str = None,
    fin_bdm: str = "",
    state: str = "",
    district: str = "",
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
    staff     : Optional staff name filter
    state     : Optional state name filter
    """
    date_filter, params = _build_date_filter(date_str, timeframe, end_date_str)

    # Additional WHERE clauses for hierarchy filters
    extra_filters = []
    if rbm:
        # Match the same expression used in SELECT: upper(trim(coalesce(bm.rbm, inv.rbm, '')))
        # This ensures case-insensitive matching (dropdown values are UPPERCASE from SELECT)
        extra_filters.append("upper(trim(coalesce(bm.rbm, inv.rbm, ''))) = upper(trim({rbm:String}))")
        params["rbm"] = rbm
    if bdm:
        extra_filters.append("upper(trim(coalesce(bm.bdm, inv.bdm, ''))) = upper(trim({bdm:String}))")
        params["bdm"] = bdm
    if store:
        # Branch column in SELECT is s.branch; filter must match exactly the same field
        extra_filters.append("upper(trim(s.branch)) = upper(trim({store:String}))")
        params["store"] = store
    if staff:
        extra_filters.append("upper(trim(coalesce(sd.staff, 'Unknown'))) = upper(trim({staff:String}))")
        params["staff"] = staff
    if state:
        extra_filters.append("""
            CASE 
                WHEN upper(trim(bm.district)) = 'THANE' THEN 'MAHARASHTRA'
                WHEN upper(trim(bm.district)) = 'DAKSHINA KANNADA' THEN 'KARNATAKA'
                WHEN upper(trim(bm.district)) = 'MAHE' THEN 'PUDUCHERRY'
                ELSE 'KERALA'
            END = upper(trim({state:String}))
        """)
        params["state"] = state
    if district:
        extra_filters.append("upper(trim(coalesce(bm.district, ''))) = upper(trim({district:String}))")
        params["district"] = district

    where_clause = " AND ".join([date_filter] + extra_filters) if extra_filters else date_filter

    # Use coarser date granularity in GROUP BY to maximise pre-aggregation:
    #   daily     → keep exact date   (~5K rows/day)
    #   monthly   → truncate to month start  (~5K rows total)
    #   quarterly → truncate to quarter start (~5K rows total)
    #   custom    → use start_date as constant (collapses all days → ~5K rows)
    #               Without this, a 16-day range returns 16×5K=80K rows → Render timeout.
    #               dashboard_aggregator's custom mask (date >= start AND date <= end)
    #               still works because all rows get Date=start_date which is always <= end_date.
    if timeframe == 'custom':
        date_expr = f"toDate('{date_str}')"   # constant: all rows → same date = range start
    else:
        date_expr = {
            'daily':     'toDate(s.date)',
            'monthly':   'toStartOfMonth(s.date)',
            'quarterly': 'toStartOfQuarter(s.date)',
        }.get(timeframe, 'toDate(s.date)')

    # RBM exclusion list for SQL (mirrors sales_cleaner step 1)
    remove_rbm_sql = ", ".join(f"'{r}'" for r in _REMOVE_RBM)

    # Junk products SQL exclusion (moved from Python to SQL for speed)
    junk_sql = ", ".join(f"'{p}'" for p in _JUNK_PRODUCTS)

    # Excluded CH categories SQL
    excluded_cats_sql = ", ".join(f"'{c}'" for c in _EXCLUDED_CH_CATEGORIES)

    # EMI files (Pine Lab / Paytm / Benow) only count for the part of the selected range
    # that an uploaded file covers - works for daily/monthly/quarterly/custom, across months.
    _rng_start, _rng_end = emi_state.range_of(date_str, timeframe, end_date_str)
    bpf_range_sql = emi_state.bpf_filter_sql(_rng_start, _rng_end)

    # Build SQL with all mapping done in ClickHouse (avoids slow Python apply() loops)
    sql = f"""
SELECT
    {date_expr}   AS Date,
    upper(trim(s.branch))                         AS Branch,
    upper(trim(coalesce(bm.rbm, inv.rbm, '')))  AS RBM,
    upper(trim(coalesce(bm.bdm, inv.bdm, '')))  AS BDM,
    upper(trim(coalesce(sd.staff, 'Unknown')))  AS Staff,
    CASE 
        WHEN upper(trim(bm.district)) = 'THANE' THEN 'MAHARASHTRA'
        WHEN upper(trim(bm.district)) = 'DAKSHINA KANNADA' THEN 'KARNATAKA'
        WHEN upper(trim(bm.district)) = 'MAHE' THEN 'PUDUCHERRY'
        ELSE 'KERALA'
    END AS State,
    upper(trim(coalesce(bm.district, '')))      AS District,

    -- Financier: priority order:
    --  1. benow_pine_financier lookup (IMEI-matched, most accurate)
    --  2. azure_invoice_report financier_name / code
    --  3. sales_data.financier fallback (uploaded Excel)
    CASE
        -- Highest priority: invoice-matched manual uploads (no dummy IMEI inflation)
        WHEN bpf_inv.bpf_financier = 'Pine Lab EMI' THEN 'PineLab'
        WHEN bpf_inv.bpf_financier = 'PAYTM' THEN 'PAYTM'
        WHEN bpf_inv.bpf_financier = 'Benow' THEN 'Benow'
        WHEN bpf_inv.bpf_financier IS NOT NULL AND bpf_inv.bpf_financier != '' THEN bpf_inv.bpf_financier
        -- IMEI fallback for direct-IMEI records
        WHEN bpf_imei.bpf_imei_financier = 'Pine Lab EMI' THEN 'PineLab'
        WHEN bpf_imei.bpf_imei_financier = 'PAYTM' THEN 'PAYTM'
        WHEN bpf_imei.bpf_imei_financier = 'Benow' THEN 'Benow'
        WHEN bpf_imei.bpf_imei_financier IS NOT NULL AND bpf_imei.bpf_imei_financier != '' THEN bpf_imei.bpf_imei_financier
        -- Standard financed products
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'BAJAJ%'   THEN 'BAJAJ'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%HDB%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'HDB%'     THEN 'HDB'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%DMI%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'SAMSUNG%' THEN 'DMI'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'IDFC%'    THEN 'IDFC'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%TVS%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'TVSC%'    THEN 'TVS'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%'
          OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'POONA%'   THEN 'PFL'
        ELSE 'aCASH'
    END AS Financier,

    -- Category: product-level mapping takes priority over item_master category
    CASE
        WHEN coalesce(i.product, '') = '' AND coalesce(i.category, '') = '' AND Financier IN ('PineLab', 'PAYTM', 'Benow') THEN 'BANK EMI'
        WHEN upper(coalesce(i.product,'')) IN ('MOBILE','TABLET','TAB')                             THEN 'MOBILE'
        WHEN upper(coalesce(i.product,'')) IN ('LAPTOP','DESKTOP')                                  THEN 'LAPTOP'
        WHEN upper(coalesce(i.product,'')) = 'SMALL APPLIANCES'                                     THEN 'SA'
        WHEN upper(coalesce(i.product,'')) IN ('AIR CONDITIONER','DISH WASHER',
             'DRYER','FREEZER','HOME APPLIANCES','MICROWAVE OVEN',
             'REFRIGERATORS','TV','WASHING MACHINES')                                               THEN 'CD PRODUCTS'
        WHEN upper(coalesce(i.product,'')) IN ('AUDIO','EAR WEARABLES','GAMING',
             'HOME THEATRE','SMART WATCH','CEGI')                                                   THEN 'HVA'
        WHEN upper(coalesce(i.product,'')) IN ('CAMERA','DIY','IT ACCESSORIES',
             'PRINTER','PROJECTOR','STABILIZER','RIG')                                              THEN 'OTHERS'
        WHEN upper(coalesce(i.category,'')) = 'TELECOM'                                             THEN 'MOBILE'
        WHEN upper(coalesce(i.category,'')) IN ('CONSUMER ELECTRONICS','HOME SOLUTIONS')            THEN 'CD PRODUCTS'
        WHEN upper(coalesce(i.category,'')) = 'IT'                                                  THEN 'LAPTOP'
        ELSE 'OTHERS'
    END AS Category,

    upper(trim(coalesce(i.product, '')))            AS Product,
    upper(trim(coalesce(nullIf(i.brand,''), 'UNKNOWN'))) AS Brand,

    sum(toInt64(CASE
        WHEN round(toFloat64(s.sold_price), 0) < 0
            THEN toInt64(-1) * toInt64(abs(toInt64(coalesce(s.qty, 0))))
        ELSE toInt64(abs(toInt64(coalesce(s.qty, 0))))
    END))                                           AS QTY,
    sum(round(toFloat64(coalesce(s.sold_price, 0)), 0)) AS `Sold Price`

FROM azure_sales_report s
LEFT JOIN item_master i            ON s.item_code  = i.item_code
LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
LEFT JOIN branch_master bm         ON upper(trim(s.branch)) = upper(trim(bm.code))
LEFT JOIN (
    SELECT invoice_number, any(financier) AS financier, any(staff) AS staff
    FROM sales_data
    GROUP BY invoice_number
) sd                               ON s.invoice_no = sd.invoice_number
LEFT JOIN (
    SELECT
        bpf_inv.invoice_no AS matched_invoice_no,
        any(bpf_inv.financier) AS bpf_financier
    FROM benow_pine_financier bpf_inv
    WHERE bpf_inv.invoice_no != ''
      {bpf_range_sql}
    GROUP BY bpf_inv.invoice_no
) bpf_inv ON s.invoice_no = bpf_inv.matched_invoice_no
LEFT JOIN (
    SELECT bpf_imei.imei AS fallback_imei, any(bpf_imei.financier) AS bpf_imei_financier
    FROM benow_pine_financier bpf_imei
    WHERE (bpf_imei.invoice_no = '' OR bpf_imei.invoice_no IS NULL)
      {bpf_range_sql}
    GROUP BY bpf_imei.imei
) bpf_imei ON s.imei_batch = bpf_imei.fallback_imei

WHERE {where_clause}
  AND (round(toFloat64(s.sold_price), 0) > {_MIN_PRICE} OR round(toFloat64(s.sold_price), 0) < -{_MIN_PRICE})
  AND upper(trim(coalesce(bm.rbm, inv.rbm, ''))) NOT IN ({remove_rbm_sql})
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%'
  AND (i.category IS NULL OR i.category NOT IN ({excluded_cats_sql}))
  AND upper(trim(coalesce(i.product, ''))) NOT IN ({junk_sql})
  AND trim(coalesce(i.product, '')) != ''

GROUP BY Date, Branch, RBM, BDM, Staff, State, District, Financier, Category, Product, Brand
"""

    logger.info(
        "ClickHouse query (SQL-mapped): timeframe=%s date=%s rbm=%s bdm=%s store=%s",
        timeframe, date_str, rbm or "*", bdm or "*", store or "*",
    )

    df = query_df(sql, params)

    if df.empty:
        logger.info("ClickHouse returned 0 rows for %s / %s", date_str, timeframe)
        return df

    # ------------------------------------------------------------------
    # Minimal post-processing — mapping already done in SQL
    # ------------------------------------------------------------------

    # 1. Ensure Date is datetime
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # 2. Numeric safety
    df["QTY"] = pd.to_numeric(df["QTY"], errors="coerce").fillna(0)
    df["Sold Price"] = pd.to_numeric(df["Sold Price"], errors="coerce").fillna(0.0)

    # 3. Compute Fin BDM from Branch (mirrors raw export logic)
    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _STORE_FIN_BDM
    except ImportError:
        _STORE_FIN_BDM = {}

    _RBM_FIN_BDM_DASH: dict = {
        'ALFAS':   'Aswin',
        'SAMEER':  'Siraj',
        'SAMEEM':  'Sreeju',
        'MAHESH':  'Jackson',
        'RENJITH': 'Arun',
        'VAISAKH': 'Ashik',
    }

    if 'Branch' in df.columns:
        branch_upper = df['Branch'].astype(str).str.strip().str.upper()
        mapped_fin = branch_upper.map(_STORE_FIN_BDM)

        def _fuzzy_fin_bdm(s):
            for k, v in _STORE_FIN_BDM.items():
                if s == k or s in k or k in s:
                    return v
            return None

        still_miss = mapped_fin.isna()
        if still_miss.any():
            mapped_fin[still_miss] = branch_upper[still_miss].apply(_fuzzy_fin_bdm)
        if 'RBM' in df.columns:
            rbm_upper = df['RBM'].astype(str).str.strip().str.upper()
            still_miss = mapped_fin.isna()
            if still_miss.any():
                mapped_fin[still_miss] = rbm_upper[still_miss].map(_RBM_FIN_BDM_DASH)
        df['Fin BDM'] = mapped_fin.fillna('')
    else:
        df['Fin BDM'] = ''

    # 4. Apply Fin BDM filter (post-query, since it's computed in Python)
    if fin_bdm:
        df = df[df['Fin BDM'].str.upper().str.strip() == fin_bdm.upper().strip()]
        if df.empty:
            logger.info("Fin BDM filter '%s' returned 0 rows", fin_bdm)
            return df

    logger.info("ClickHouse DataFrame (cleaned): %d rows, columns: %s", len(df), list(df.columns))
    return df


# ---------------------------------------------------------------------------
# Raw row-level export  (used by /api/export-date-range)
# Returns one row per invoice line — no GROUP BY — for DATA sheet export.
# Applies the same financier mapping & cleaning rules as get_clickhouse_df().
# ---------------------------------------------------------------------------
_FIN_CASE = """
    CASE
        -- Highest priority: invoice-matched manual uploads (no dummy IMEI inflation)
        WHEN bpf_inv.bpf_financier = 'Pine Lab EMI' THEN 'PineLab'
        WHEN bpf_inv.bpf_financier = 'PAYTM' THEN 'PAYTM'
        WHEN bpf_inv.bpf_financier = 'Benow' THEN 'Benow'
        WHEN bpf_inv.bpf_financier IS NOT NULL AND bpf_inv.bpf_financier != '' THEN bpf_inv.bpf_financier
        -- IMEI fallback for direct-IMEI records
        WHEN bpf_imei.bpf_imei_financier = 'Pine Lab EMI' THEN 'PineLab'
        WHEN bpf_imei.bpf_imei_financier = 'PAYTM' THEN 'PAYTM'
        WHEN bpf_imei.bpf_imei_financier = 'Benow' THEN 'Benow'
        WHEN bpf_imei.bpf_imei_financier IS NOT NULL AND bpf_imei.bpf_imei_financier != '' THEN bpf_imei.bpf_imei_financier
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%BAJAJ%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'BAJAJ%'   THEN 'BAJAJ FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%HDB%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'HDB%'     THEN 'HDB FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%DMI%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'SAMSUNG%' THEN 'DMI FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%IDFC%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'IDFC%'    THEN 'IDFC FIRST BANK LTD'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%TVS%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'TVSC%'    THEN 'TVS Credit'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%POONAWALLA%'
          OR upper(coalesce(inv.financier_name,'')) LIKE '%PFL%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'POONA%'   THEN 'Poonawalla Fincorp'
        ELSE 'aCash'
    END"""

_CAT_CASE = """
    CASE
        WHEN upper(coalesce(i.product,'')) IN ('MOBILE','TABLET','TAB')         THEN 'MOBILE'
        WHEN upper(coalesce(i.product,'')) IN ('LAPTOP','DESKTOP')              THEN 'LAPTOP'
        WHEN upper(coalesce(i.product,'')) LIKE '%SMALL APPLIANCE%'             THEN 'SA'
        WHEN upper(coalesce(i.product,'')) IN ('AIR CONDITIONER','AC OUTDOOR',
             'DISH WASHER','DRYER','FREEZER','HOME APPLIANCES',
             'MICROWAVE OVEN','REFRIGERATORS','TV','WASHING MACHINES')          THEN 'CD PRODUCTS'
        WHEN upper(coalesce(i.product,'')) IN ('AUDIO','EAR WEARABLES',
             'GAMING','HOME THEATRE','SMART WATCH')                             THEN 'HVA'
        WHEN upper(coalesce(i.category,'')) = 'TELECOM'                         THEN 'MOBILE'
        WHEN upper(coalesce(i.category,'')) IN ('CONSUMER ELECTRONICS',
             'HOME SOLUTIONS')                                                   THEN 'CD PRODUCTS'
        WHEN upper(coalesce(i.category,'')) = 'IT'                              THEN 'LAPTOP'
        ELSE 'BANK EMI'
    END"""


def get_raw_export_df(start_date_str: str, end_date_str: str) -> "pd.DataFrame":
    """
    Query ClickHouse for individual invoice-line rows in [start_date, end_date].
    Returns a DataFrame matching the sales_cleaner.py output exactly:
    columns = [Slno, STore, Fin BDM, RBM, BDM, Date, Month, Invoice Number,
               Customer Name, Customer Mobile, Pincode, Staff, Product, Category,
               Brand, Item Group, Item Category, Item Code, Item Name, IMEI, QTY,
               Financier, Finance, Delivery Order No, MOP, Sold Price, EMI,
               Price Range, Price, Zone]
    """
    from datetime import datetime as _dt

    try:
        start = _dt.strptime(start_date_str, "%Y-%m-%d").date()
        end   = _dt.strptime(end_date_str,   "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date format — expected YYYY-MM-DD: {exc}") from exc

    remove_rbm_sql = ", ".join(f"'{r}'" for r in _REMOVE_RBM)
    junk_sql       = ", ".join(f"'{p}'" for p in _JUNK_PRODUCTS)

    # Only use columns CONFIRMED to exist in the ClickHouse tables.
    # Missing columns (customer_name, staff_name, pincode, imei, emi, mop,
    # item_name, delivery_order_no, item_category, fin_bdm, zone) → empty strings.
    # Price Range is computed from sold_price.
    sql = f"""
SELECT
    toUInt64(rowNumberInAllBlocks() + 1)                            AS Slno,
    upper(trim(coalesce(nullIf(bm.branch_name,''), s.branch))) AS STore,
    ''                                                              AS `Fin BDM`,
    upper(trim(coalesce(bm.rbm, inv.rbm, '')))                      AS RBM,
    upper(trim(coalesce(bm.bdm, inv.bdm, '')))                      AS BDM,
    toDate(s.date)                                                  AS Date,
    concat(
        CASE toMonth(toDate(s.date))
            WHEN 1  THEN 'January'   WHEN 2  THEN 'February' WHEN 3  THEN 'March'
            WHEN 4  THEN 'April'     WHEN 5  THEN 'May'       WHEN 6  THEN 'June'
            WHEN 7  THEN 'July'      WHEN 8  THEN 'August'    WHEN 9  THEN 'September'
            WHEN 10 THEN 'October'   WHEN 11 THEN 'November'  WHEN 12 THEN 'December'
            ELSE ''
        END,
        ' ', toString(toYear(toDate(s.date)))
    )                                                                   AS Month,

    toString(s.invoice_no)                                          AS `Invoice Number`,
    coalesce(sd.customer_name, '')                                  AS `Customer Name`,
    coalesce(inv.customer_mobile, sd.customer_mobile, '')           AS `Customer Mobile`,
    coalesce(inv.customer_pincode, '')                              AS Pincode,
    coalesce(sd.staff, '')                                          AS Staff,
    upper(trim(coalesce(i.product, '')))                            AS Product,
    {_CAT_CASE}                                                     AS Category,
    upper(trim(coalesce(nullIf(i.brand,''), '')))                   AS Brand,
    upper(trim(coalesce(i.item_group, '')))                         AS `Item Group`,
    upper(trim(coalesce(i.item_category, '')))                      AS `Item Category`,
    s.item_code                                                     AS `Item Code`,
    coalesce(i.item_name, '')                                       AS `Item Name`,
    coalesce(toString(s.imei_batch), '')                            AS IMEI,
    CASE
        WHEN round(toFloat64(s.sold_price), 0) < 0
            THEN -abs(toInt64(coalesce(s.qty, 0)))
        ELSE abs(toInt64(coalesce(s.qty, 0)))
    END                                                             AS QTY,
    {_FIN_CASE}                                                     AS Financier,
    CASE WHEN ({_FIN_CASE}) = 'aCash' THEN 'aCash' ELSE 'FIN' END  AS Finance,
    coalesce(sd.delivery_order_no, '')                              AS `Delivery Order No`,
    coalesce(toString(s.mop), '')                                   AS MOP,
    round(toFloat64(coalesce(s.sold_price, 0)), 0)                  AS `Sold Price`,
    toFloat64(0)                                                    AS EMI,
    CASE
        WHEN round(toFloat64(s.sold_price), 0) < 10000  THEN 'Below 10K'
        WHEN round(toFloat64(s.sold_price), 0) < 15000  THEN '10K to 15K'
        WHEN round(toFloat64(s.sold_price), 0) < 20000  THEN '15K to 20K'
        WHEN round(toFloat64(s.sold_price), 0) < 30000  THEN '20K to 30K'
        WHEN round(toFloat64(s.sold_price), 0) < 40000  THEN '30K to 40K'
        WHEN round(toFloat64(s.sold_price), 0) < 50000  THEN '40K to 50K'
        WHEN round(toFloat64(s.sold_price), 0) < 75000  THEN '50K to 75K'
        WHEN round(toFloat64(s.sold_price), 0) < 100000 THEN '75K to 100K'
        WHEN round(toFloat64(s.sold_price), 0) < 150000 THEN '100K to 150K'
        WHEN round(toFloat64(s.sold_price), 0) < 200000 THEN '150K to 200K'
        ELSE 'Above 200K'
    END                                                             AS `Price Range`,
    round(toFloat64(coalesce(s.sold_price, 0)), 0)                  AS Price,
    CASE
        WHEN upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('ALFAS','SAMEER','SAMEEM') THEN 'NORTH'
        WHEN upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('RENJITH','MAHESH','VAISAKH') THEN 'SOUTH'
        ELSE ''
    END                                                             AS Zone

FROM azure_sales_report s
LEFT JOIN item_master i            ON s.item_code  = i.item_code
LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
LEFT JOIN branch_master bm         ON upper(trim(s.branch)) = upper(trim(bm.code))
LEFT JOIN (
    SELECT invoice_number,
           any(delivery_order_no) AS delivery_order_no,
           any(staff) AS staff,
           any(customer_name) AS customer_name,
           any(customer_mobile) AS customer_mobile,
           any(coalesce(financier, '')) AS financier
    FROM sales_data
    GROUP BY invoice_number
) sd                               ON s.invoice_no = sd.invoice_number
LEFT JOIN (
    SELECT bpf_inv.invoice_no AS matched_invoice_no, any(bpf_inv.financier) AS bpf_financier
    FROM benow_pine_financier bpf_inv
    WHERE bpf_inv.invoice_no != '' {emi_state.hidden_sql(None)}
    GROUP BY bpf_inv.invoice_no
) bpf_inv ON s.invoice_no = bpf_inv.matched_invoice_no
LEFT JOIN (
    SELECT bpf_imei.imei AS fallback_imei, any(bpf_imei.financier) AS bpf_imei_financier
    FROM benow_pine_financier bpf_imei
    WHERE (bpf_imei.invoice_no = '' OR bpf_imei.invoice_no IS NULL) {emi_state.hidden_sql(None)}
    GROUP BY bpf_imei.imei
) bpf_imei ON s.imei_batch = bpf_imei.fallback_imei

WHERE toDate(s.date) >= toDate('{start}')
  AND toDate(s.date) <= toDate('{end}')
  AND (round(toFloat64(s.sold_price), 0) > {_MIN_PRICE} OR round(toFloat64(s.sold_price), 0) < -{_MIN_PRICE})
  AND upper(trim(coalesce(bm.rbm, inv.rbm, ''))) NOT IN ({remove_rbm_sql})
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%'
  AND upper(trim(coalesce(i.product, ''))) NOT IN ({junk_sql})
  AND trim(coalesce(i.product, '')) != ''

ORDER BY toDate(s.date) ASC, s.branch ASC
"""

    logger.info("ClickHouse raw export: %s → %s", start, end)
    df = query_df(sql, {})
    logger.info("ClickHouse raw export returned %d rows", len(df))

    # ── Populate Fin BDM from Store lookup ──────────────────────────────────
    # Primary: exact store → Fin BDM match (from sales_cleaner STORE_FIN_BDM_MAP)
    # Fallback: RBM → Fin BDM (for when store isn't in the map)
    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _STORE_FIN_BDM
    except ImportError:
        _STORE_FIN_BDM = {}

    # RBM → Fin BDM mapping (from the user-provided reference table)
    _RBM_FIN_BDM: dict = {
        'ALFAS':   'Aswin',    # ALFAS has ASWIN & NIKHIL — store-level map resolves
        'SAMEER':  'Siraj',
        'SAMEEM':  'Sreeju',
        'MAHESH':  'Jackson',
        'RENJITH': 'Arun',     # RENJITH has ARUN & FEBIN — store-level map resolves
        'VAISAKH': 'Ashik',
    }

    if not df.empty and 'STore' in df.columns:
        store_upper = df['STore'].astype(str).str.strip().str.upper()

        # Exact map lookup
        mapped = store_upper.map(_STORE_FIN_BDM)

        # Fuzzy fallback for unmatched stores
        def _fuzzy_bdm(s):
            for k, v in _STORE_FIN_BDM.items():
                if s == k or s in k or k in s:
                    return v
            return None

        still_missing = mapped.isna()
        if still_missing.any():
            mapped[still_missing] = store_upper[still_missing].apply(_fuzzy_bdm)

        # RBM-level fallback for anything still empty
        if 'RBM' in df.columns:
            rbm_upper = df['RBM'].astype(str).str.strip().str.upper()
            still_missing = mapped.isna()
            if still_missing.any():
                mapped[still_missing] = rbm_upper[still_missing].map(_RBM_FIN_BDM)

        df['Fin BDM'] = mapped.fillna('')

    return df
