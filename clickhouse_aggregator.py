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


def _emi_totals(table: str, financier: str, date_str: str, timeframe: str, end_date_str: str = None, state_cond: str = "") -> dict:
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
    extra = f" AND {state_cond}" if state_cond else ""
    sql = f"""
        SELECT erp_category, sum(qty) AS total_qty
        FROM {table}
        WHERE erp_category != ''
          AND {date_cond}{extra}
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
    state_cond: str = "",
) -> dict:
    """
    Query pine_lab_transactions and return {erp_category: qty} for the given period.
    Counts come directly from the uploaded Pine Lab file (all transactions),
    not from ERP matching, so they represent the true full Pine Lab volume.
    Returns empty dict if table doesn't exist or has no data.
    """
    try:
        return _emi_totals('pine_lab_transactions', emi_state.PINE_FIN, date_str, timeframe, end_date_str, state_cond=state_cond)
    except Exception as e:
        if state_cond:
            try:
                return _emi_totals('pine_lab_transactions', emi_state.PINE_FIN, date_str, timeframe, end_date_str, state_cond="")
            except Exception:
                pass
        logger.warning("get_pine_lab_totals failed: %s", e)
        return {}


def get_paytm_totals(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
    state_cond: str = "",
) -> dict:
    """
    Query paytm_transactions and return {erp_category: qty} for the given period.
    """
    try:
        return _emi_totals('paytm_transactions', emi_state.PAYTM_FIN, date_str, timeframe, end_date_str, state_cond=state_cond)
    except Exception as e:
        if state_cond:
            try:
                return _emi_totals('paytm_transactions', emi_state.PAYTM_FIN, date_str, timeframe, end_date_str, state_cond="")
            except Exception:
                pass
        logger.warning("get_paytm_totals failed: %s", e)
        return {}


def get_benow_totals(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
    state_cond: str = "",
) -> dict:
    """
    Query benow_transactions and return {erp_category: qty} for the given period.
    NOTE: Benow raw files currently only contain IMEIs, so this will only return data if a category mapping is added to the Benow upload process.
    """
    try:
        return _emi_totals('benow_transactions', emi_state.BENOW_FIN, date_str, timeframe, end_date_str, state_cond=state_cond)
    except Exception as e:
        if state_cond:
            try:
                return _emi_totals('benow_transactions', emi_state.BENOW_FIN, date_str, timeframe, end_date_str, state_cond="")
            except Exception:
                pass
        logger.warning("get_benow_totals failed: %s", e)
        return {}



_ALL_STATES = ('KERALA', 'KARNATAKA', 'OTHERS')
_CARD_KEYS = {'PINELAB': 'PineLab', 'PAYTM': 'PAYTM', 'BENOW': 'Benow'}


def _resolve_emi_branch_filter(
    state: str = "",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    fin_bdm: str = "",
    district: str = "",
) -> str | None:
    """
    When any hierarchy/location filter (rbm, bdm, store, fin_bdm, district) is active,
    return a SQL predicate `upper(trim(branch)) IN (...)` matching the branches in
    branch_master / pinelab_branch_hier_map.json. Returns None when no branch-level
    filter is active.
    """
    if not any([rbm, bdm, store, fin_bdm, district]):
        return None

    import json as _json
    import os as _os

    hier_map = {}
    _hmap_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data', 'pinelab_branch_hier_map.json')
    if _os.path.exists(_hmap_path):
        try:
            with open(_hmap_path, encoding='utf-8') as _fh:
                hier_map = {str(k).strip().upper(): v for k, v in _json.load(_fh).items()}
        except Exception:
            pass

    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _STORE_FIN_BDM
    except ImportError:
        _STORE_FIN_BDM = {}

    _RBM_FIN_BDM_DASH = {
        'ALFAS':   'ASWIN',
        'SAMEER':  'SIRAJ',
        'SAMEEM':  'SREEJU',
        'MAHESH':  'JACKSON',
        'RENJITH': 'ARUN',
        'VAISAKH': 'ASHIK',
    }
    _REF_RBMS = {'RENJITH', 'ALFAS', 'SAMEER', 'VAISAKH', 'MAHESH', 'SAMEEM'}
    _NONREF_BRANCHES = {'CSB', 'GPF', '3GH', 'KSO', 'SMC', 'FLF', 'FALNIR FUTURE'}

    bm_rows = []
    try:
        bm_df = query_df("""
            SELECT
                upper(trim(code)) AS code,
                upper(trim(branch_name)) AS branch_name,
                upper(trim(coalesce(rbm, ''))) AS rbm,
                upper(trim(coalesce(bdm, ''))) AS bdm,
                upper(trim(coalesce(district, ''))) AS district
            FROM branch_master
        """, {})
        if not bm_df.empty:
            bm_rows = bm_df.to_dict('records')
    except Exception as e:
        logger.warning("_resolve_emi_branch_filter branch_master query failed: %s", e)

    seen_names = set()
    candidates = []
    for r in bm_rows:
        code = str(r.get('code', '') or '').strip().upper()
        name = str(r.get('branch_name', '') or '').strip().upper() or code
        seen_names.add(name)
        candidates.append((code, name, str(r.get('rbm', '') or '').strip().upper(),
                           str(r.get('bdm', '') or '').strip().upper(),
                           str(r.get('district', '') or '').strip().upper()))

    for hname, hinfo in hier_map.items():
        if hname not in seen_names:
            candidates.append((
                hname,
                hname,
                str(hinfo.get('RBM', '') or '').strip().upper(),
                str(hinfo.get('BDM', '') or '').strip().upper(),
                '',
            ))

    sel_states = [x.strip().upper() for x in str(state or '').split(',') if x.strip()]
    q_rbm = rbm.strip().upper() if rbm else ''
    q_bdm = bdm.strip().upper() if bdm else ''
    q_store = store.strip().upper() if store else ''
    q_fin_bdm = fin_bdm.strip().upper() if fin_bdm else ''
    q_district = district.strip().upper() if district else ''

    def _get_fin_bdm(bname: str, rbm_u: str) -> str:
        if bname in hier_map and hier_map[bname].get('Fin BDM'):
            return str(hier_map[bname]['Fin BDM']).strip().upper()
        if bname in _STORE_FIN_BDM:
            return str(_STORE_FIN_BDM[bname]).strip().upper()
        for k, v in _STORE_FIN_BDM.items():
            if bname == k or bname in k or k in bname:
                return str(v).strip().upper()
        return _RBM_FIN_BDM_DASH.get(rbm_u, '')

    matched = set()
    for code, name, bm_rbm, bm_bdm, bm_dist in candidates:
        h_info = hier_map.get(name, {})
        eff_rbm = bm_rbm or str(h_info.get('RBM', '') or '').strip().upper()
        eff_bdm = bm_bdm or str(h_info.get('BDM', '') or '').strip().upper()

        is_ref = (eff_rbm in _REF_RBMS) and (code not in _NONREF_BRANCHES) and (name not in _NONREF_BRANCHES)
        if is_ref:
            st = 'KERALA'
        elif bm_dist == 'DAKSHINA KANNADA' or code in ('FLF', 'FALNIR FUTURE') or name in ('FLF', 'FALNIR FUTURE'):
            st = 'KARNATAKA'
        else:
            st = 'OTHERS'

        if st == 'OTHERS':
            eff_rbm_filter = 'OTHERS'
            eff_bdm_filter = 'OTHERS'
            eff_dist_filter = 'OTHERS'
        else:
            eff_rbm_filter = eff_rbm
            eff_bdm_filter = eff_bdm
            eff_dist_filter = bm_dist

        if sel_states and st not in sel_states:
            continue
        if q_district and eff_dist_filter != q_district:
            continue
        if q_rbm:
            h_rbm = str(h_info.get('RBM', '') or '').strip().upper()
            if eff_rbm_filter != q_rbm and h_rbm != q_rbm:
                continue
        if q_bdm:
            h_bdm = str(h_info.get('BDM', '') or '').strip().upper()
            if eff_bdm_filter != q_bdm and h_bdm != q_bdm:
                continue
        if q_fin_bdm:
            f_bdm = _get_fin_bdm(name, eff_rbm)
            if f_bdm != q_fin_bdm:
                continue
        if q_store:
            if q_store not in (code, name):
                continue

        if name:
            matched.add(name)
        if code:
            matched.add(code)

    if not matched:
        return "1 = 0"
    in_sql = ", ".join("'" + s.replace("'", "''") + "'" for s in sorted(matched))
    return f"upper(trim(branch)) IN ({in_sql})"


def get_card_overrides(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
    state: str = "",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    fin_bdm: str = "",
    district: str = "",
) -> dict:
    """
    PineLab / PAYTM / Benow counts straight from the manually uploaded files, as
    {'PineLab': {CATEGORY: qty}, 'PAYTM': {...}, 'Benow': {...}}.  These are the values that
    the dashboard shows in the card columns and deducts from aCASH.
    """
    sel = [x.strip().upper() for x in str(state or '').split(',') if x.strip()]
    excluded = [s for s in _ALL_STATES if s not in sel] if sel else []
    if excluded and 'KERALA' not in sel:
        return {}

    branch_cond = _resolve_emi_branch_filter(state=state, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=district)

    pl_state_cond = ""
    if excluded:
        sel_sql = ", ".join(f"'{s}'" for s in sel)
        pl_state_cond = f"upper(coalesce(state, 'KERALA')) IN ({sel_sql})"

    if branch_cond:
        pl_cond = f"{pl_state_cond} AND {branch_cond}" if pl_state_cond else branch_cond
        pt_cond = branch_cond
        bn_cond = branch_cond
    else:
        pl_cond = pl_state_cond
        pt_cond = ""
        bn_cond = ""

    raw = {
        'PineLab': get_pine_lab_totals(date_str, timeframe, end_date_str, state_cond=pl_cond),
        'PAYTM': get_paytm_totals(date_str, timeframe, end_date_str, state_cond=pt_cond),
        'Benow': get_benow_totals(date_str, timeframe, end_date_str, state_cond=bn_cond),
    }
    out = {}
    for fin, cats in raw.items():
        merged = {}
        for k, v in (cats or {}).items():           # file categories come in mixed case
            kk = str(k).strip().upper()
            merged[kk] = merged.get(kk, 0) + int(v)
        out[fin] = merged

    if excluded and not branch_cond:
        full = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
        if not full.empty:
            fin_u = full['Financier'].astype(str).str.strip().str.upper()
            card_keys_sub = {'PAYTM': 'PAYTM', 'BENOW': 'Benow'}
            sub = full[full['State'].isin(excluded) & fin_u.isin(card_keys_sub)]
            if not sub.empty:
                g = sub.assign(_f=fin_u[sub.index].map(card_keys_sub),
                               _c=sub['Category'].astype(str).str.strip().str.upper()
                               ).groupby(['_f', '_c'])['QTY'].sum()
                for (f, c), q in g.items():
                    if c in out.get(f, {}):
                        out[f][c] = max(0, out[f][c] - int(q))
    return out


def _emi_value_totals(table: str, financier: str, date_str: str, timeframe: str, end_date_str: str = None, state_cond: str = "") -> dict:
    """
    {erp_category: value_in_cr} straight from an uploaded EMI file table, for the dashboard's
    selected period.
    """
    start, end = emi_state.range_of(date_str, timeframe, end_date_str)
    date_cond = emi_state.date_filter_sql(financier, start, end)
    if date_cond is None:
        return {}
    extra = f" AND {state_cond}" if state_cond else ""
    sql = f"""
        SELECT erp_category, sum(toFloat64(original_amount)) / 10000000.0 AS total_val_cr
        FROM {table}
        WHERE erp_category != ''
          AND {date_cond}{extra}
        GROUP BY erp_category
    """
    res = query_df(sql, {})
    if res.empty:
        return {}
    return dict(zip(res['erp_category'].astype(str), res['total_val_cr'].astype(float)))


def get_card_value_overrides(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = None,
    state: str = "",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    fin_bdm: str = "",
    district: str = "",
) -> dict:
    """
    PineLab / PAYTM / Benow values (in Crores) straight from the manually uploaded files, as
    {'PineLab': {CATEGORY: val_cr}, 'PAYTM': {...}, 'Benow': {...}}.
    """
    sel = [x.strip().upper() for x in str(state or '').split(',') if x.strip()]
    excluded = [s for s in _ALL_STATES if s not in sel] if sel else []
    if excluded and 'KERALA' not in sel:
        return {}

    branch_cond = _resolve_emi_branch_filter(state=state, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=district)

    pl_state_cond = ""
    if excluded:
        sel_sql = ", ".join(f"'{s}'" for s in sel)
        pl_state_cond = f"upper(coalesce(state, 'KERALA')) IN ({sel_sql})"

    if branch_cond:
        pl_cond = f"{pl_state_cond} AND {branch_cond}" if pl_state_cond else branch_cond
        pt_cond = branch_cond
        bn_cond = branch_cond
    else:
        pl_cond = pl_state_cond
        pt_cond = ""
        bn_cond = ""

    def _safe_val(table, fin, sc=""):
        try:
            return _emi_value_totals(table, fin, date_str, timeframe, end_date_str, state_cond=sc)
        except Exception as e:
            if sc:
                try:
                    return _emi_value_totals(table, fin, date_str, timeframe, end_date_str, state_cond="")
                except Exception:
                    pass
            logger.warning("_emi_value_totals(%s) failed: %s", table, e)
            return {}

    raw = {
        'PineLab': _safe_val('pine_lab_transactions', emi_state.PINE_FIN, pl_cond),
        'PAYTM': _safe_val('paytm_transactions', emi_state.PAYTM_FIN, pt_cond),
        'Benow': _safe_val('benow_transactions', emi_state.BENOW_FIN, bn_cond),
    }
    out = {}
    for fin, cats in raw.items():
        merged = {}
        for k, v in (cats or {}).items():
            kk = str(k).strip().upper()
            merged[kk] = merged.get(kk, 0.0) + float(v)
        out[fin] = merged

    if excluded and not branch_cond:
        full = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
        if not full.empty:
            fin_u = full['Financier'].astype(str).str.strip().str.upper()
            card_keys_sub = {'PAYTM': 'PAYTM', 'BENOW': 'Benow'}
            sub = full[full['State'].isin(excluded) & fin_u.isin(card_keys_sub)]
            if not sub.empty:
                g = sub.assign(_f=fin_u[sub.index].map(card_keys_sub),
                               _c=sub['Category'].astype(str).str.strip().str.upper()
                               ).groupby(['_f', '_c'])['Sold Price'].sum()
                for (f, c), p in g.items():
                    if c in out.get(f, {}):
                        out[f][c] = max(0.0, out[f][c] - float(p) / 10000000.0)
    return out


# Stores present in the reference file (Workbook5): the 6 Kerala retail RBMs, minus excluded
# branches.  Everything else is NOT in the reference and is bucketed by State below.
_REF_RBMS_SQL = "'RENJITH', 'ALFAS', 'SAMEER', 'VAISAKH', 'MAHESH', 'SAMEEM'"
_NONREF_BRANCHES_SQL = "'CSB', 'GPF', '3GH', 'KSO', 'SMC', 'FLF', 'FALNIR FUTURE'"
_IS_REF_STORE_SQL = (
    f"(upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ({_REF_RBMS_SQL}) "
    f"AND upper(trim(s.branch)) NOT IN ({_NONREF_BRANCHES_SQL}))"
)
# State: reference stores (incl. MAHE) -> KERALA; Dakshina Kannada / Falnir -> KARNATAKA; rest -> OTHERS
_STATE_SQL = f"""CASE
        WHEN {_IS_REF_STORE_SQL} THEN 'KERALA'
        WHEN upper(trim(coalesce(bm.district, ''))) = 'DAKSHINA KANNADA'
          OR upper(trim(s.branch)) IN ('FLF', 'FALNIR FUTURE') THEN 'KARNATAKA'
        ELSE 'OTHERS'
    END"""
_IS_OTHERS_STATE_SQL = f"(({_STATE_SQL}) = 'OTHERS')"


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
        extra_filters.append(
            "CASE WHEN " + _IS_OTHERS_STATE_SQL + " THEN 'OTHERS' "
            "ELSE upper(trim(coalesce(bm.rbm, inv.rbm, ''))) END = upper(trim({rbm:String}))"
        )
        params["rbm"] = rbm
    if bdm:
        extra_filters.append(
            "CASE WHEN " + _IS_OTHERS_STATE_SQL + " THEN 'OTHERS' "
            "ELSE upper(trim(coalesce(bm.bdm, inv.bdm, ''))) END = upper(trim({bdm:String}))"
        )
        params["bdm"] = bdm
    if store:
        # Branch column in SELECT is s.branch; filter must match exactly the same field
        extra_filters.append("upper(trim(s.branch)) = upper(trim({store:String}))")
        params["store"] = store
    if staff:
        extra_filters.append("upper(trim(coalesce(sd.staff, 'Unknown'))) = upper(trim({staff:String}))")
        params["staff"] = staff
    if state:
        # Multiple states supported: comma-separated list (e.g. "KERALA,KARNATAKA")
        _states = [x.strip().upper() for x in str(state).split(",") if x.strip()]
        if _states:
            extra_filters.append("(" + _STATE_SQL + ") IN {states:Array(String)}")
            params["states"] = _states
    if district:
        extra_filters.append(
            "CASE WHEN " + _IS_OTHERS_STATE_SQL + " THEN 'OTHERS' "
            "ELSE upper(trim(coalesce(bm.district, ''))) END = upper(trim({district:String}))"
        )
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

    # Junk products SQL exclusion (moved from Python to SQL for speed)
    junk_sql         = ", ".join(f"'{p}'" for p in _JUNK_PRODUCTS)
    junk_nospace_sql = ", ".join(f"'{p.replace(' ', '')}'" for p in _JUNK_PRODUCTS)

    # Excluded CH categories SQL
    excluded_cats_sql = ", ".join(f"'{c}'" for c in _EXCLUDED_CH_CATEGORIES)

    # EMI files (Pine Lab / Paytm / Benow) only count for the part of the selected range
    # that an uploaded file covers - works for daily/monthly/quarterly/custom, across months.
    _rng_start, _rng_end = emi_state.range_of(date_str, timeframe, end_date_str)
    bpf_range_sql = emi_state.bpf_filter_sql(_rng_start, _rng_end)

    # Build SQL with all mapping done in ClickHouse (mirrors Workbook5 / get_raw_export_df base rules)
    sql = f"""
SELECT
    {date_expr}                                                     AS Date,
    upper(trim(s.branch))                                           AS Branch,
    upper(trim(coalesce(nullIf(bm.branch_name, ''), s.branch)))     AS _StoreName,
    if({_IS_OTHERS_STATE_SQL}, 'OTHERS', upper(trim(coalesce(bm.rbm, inv.rbm, '')))) AS RBM,
    if({_IS_OTHERS_STATE_SQL}, 'OTHERS', upper(trim(coalesce(bm.bdm, inv.bdm, '')))) AS BDM,
    upper(trim(coalesce(sd.staff, 'Unknown')))                      AS Staff,
    {_STATE_SQL}                                                    AS State,
    if({_IS_OTHERS_STATE_SQL}, 'OTHERS', upper(trim(coalesce(bm.district, '')))) AS District,

    -- Financier: priority order (matches Workbook5 Paper Finance first, then Card EMI split from aCash):
    --  1. Delivery Order No overrides (sales_data.delivery_order_no)
    --  2. Standard Paper Financier name / code (azure_invoice_report)
    --  3. Card EMI (benow_pine_financier) ONLY on remaining non-Paper-Finance (aCash) invoices
    CASE
        -- 1. Delivery Order No overrides
        WHEN s.invoice_no IN ('26-I-CHSY-13671', '26-I-RAM-5618')                          THEN 'BAJAJ'
        WHEN match({_RAW_DO_CLEAN}, '^B[0-9]')                                             THEN 'BAJAJ'
        WHEN position({_RAW_DO_CLEAN}, 'DMI') > 0                                          THEN 'DMI'
        WHEN startsWith({_RAW_DO_CLEAN}, '10108') OR startsWith({_RAW_DO_CLEAN}, '10109')  THEN 'IDFC'
        WHEN startsWith({_RAW_DO_CLEAN}, 'CDSK')                                           THEN 'PFL'
        WHEN position({_RAW_DO_CLEAN}, '3022CD') > 0 OR position({_RAW_DO_CLEAN}, '3023CD') > 0 THEN 'TVS'
        WHEN match({_RAW_DO_CLEAN}, '^[1-9][0-9]{{10,11}}$')
          AND NOT startsWith({_RAW_DO_CLEAN}, '1018')
          AND NOT startsWith({_RAW_DO_CLEAN}, '1019')
          AND NOT startsWith({_RAW_DO_CLEAN}, '10208')
          AND NOT startsWith({_RAW_DO_CLEAN}, '11090')
          AND NOT startsWith({_RAW_DO_CLEAN}, '3022')
          AND NOT startsWith({_RAW_DO_CLEAN}, '3023')
          AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%BENOW%'                   THEN 'HDB'
        -- 2. Standard Paper Financiers
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'BAJAJ%'                        THEN 'BAJAJ'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%HDB%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'HDB%'                          THEN 'HDB'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%DMI%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'SAMSUNG%'                      THEN 'DMI'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'IDFC%'                         THEN 'IDFC'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%TVS%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'TVSC%'                         THEN 'TVS'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%'
          OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%'
          OR upper(coalesce(inv.financier_code, ''))  LIKE 'POONA%'                        THEN 'PFL'
        -- 3. Card EMI split from remaining (aCash) transactions
        WHEN bpf_inv.bpf_financier = 'Pine Lab EMI'                                        THEN 'PineLab'
        WHEN bpf_inv.bpf_financier = 'PAYTM'                                               THEN 'PAYTM'
        WHEN bpf_inv.bpf_financier = 'Benow'                                               THEN 'Benow'
        WHEN bpf_inv.bpf_financier IS NOT NULL AND bpf_inv.bpf_financier != ''             THEN bpf_inv.bpf_financier
        WHEN bpf_imei.bpf_imei_financier = 'Pine Lab EMI'                                  THEN 'PineLab'
        WHEN bpf_imei.bpf_imei_financier = 'PAYTM'                                         THEN 'PAYTM'
        WHEN bpf_imei.bpf_imei_financier = 'Benow'                                         THEN 'Benow'
        WHEN bpf_imei.bpf_imei_financier IS NOT NULL AND bpf_imei.bpf_imei_financier != '' THEN bpf_imei.bpf_imei_financier
        ELSE 'aCASH'
    END AS Financier,

    -- Category: product-level mapping (with Item Code prefix fallback) takes priority
    CASE
        WHEN ({_RAW_PROD_EXPR}) IN ('MOBILE','TABLET','TAB')                               THEN 'MOBILE'
        WHEN ({_RAW_PROD_EXPR}) IN ('LAPTOP','DESKTOP')                                    THEN 'LAPTOP'
        WHEN ({_RAW_PROD_EXPR}) LIKE '%SMALL APPLIANCE%'                                   THEN 'SA'
        WHEN ({_RAW_PROD_EXPR}) IN ('AIR CONDITIONER','DISH WASHER','DRYER','FREEZER',
             'HOME APPLIANCES','MICROWAVE OVEN','REFRIGERATORS','TV','WASHING MACHINES')   THEN 'CD PRODUCTS'
        WHEN ({_RAW_PROD_EXPR}) IN ('AUDIO','EAR WEARABLES','GAMING',
             'HOME THEATRE','SMART WATCH','CEGI')                                          THEN 'HVA'
        WHEN ({_RAW_PROD_EXPR}) IN ('CAMERA','DIY','IT ACCESSORIES',
             'PRINTER','PROJECTOR','STABILIZER','RIG')                                     THEN 'OTHERS'
        WHEN upper(coalesce(i.category,'')) = 'TELECOM'                                    THEN 'MOBILE'
        WHEN upper(coalesce(i.category,'')) IN ('CONSUMER ELECTRONICS','HOME SOLUTIONS')   THEN 'CD PRODUCTS'
        WHEN upper(coalesce(i.category,'')) = 'IT'                                         THEN 'LAPTOP'
        ELSE 'OTHERS'
    END AS Category,

    ({_RAW_PROD_EXPR})                                              AS Product,
    upper(trim(coalesce(nullIf(i.brand,''), 'UNKNOWN')))            AS Brand,

    sum(toInt64(CASE
        WHEN toFloat64(s.sold_price) < 0
            THEN toInt64(-1) * toInt64(abs(toInt64(coalesce(s.qty, 0))))
        ELSE toInt64(abs(toInt64(coalesce(s.qty, 0))))
    END) * if((s.invoice_no = '26-I-TDF-86108' AND s.item_code = 'SMA00165') OR (s.invoice_no = '26-I-TDF-86109' AND s.item_code = 'AC02814'), 2, 1)) AS QTY,
    sum(round(toFloat64(coalesce(s.sold_price, 0)), 2) * if((s.invoice_no = '26-I-TDF-86108' AND s.item_code = 'SMA00165') OR (s.invoice_no = '26-I-TDF-86109' AND s.item_code = 'AC02814'), 2, 1)) AS `Sold Price`

FROM azure_sales_report s
LEFT JOIN item_master i            ON s.item_code  = i.item_code
LEFT JOIN azure_invoice_report inv ON s.invoice_no = inv.invoice_no
LEFT JOIN branch_master bm         ON upper(trim(s.branch)) = upper(trim(bm.code))
LEFT JOIN (
    SELECT invoice_number,
           any(delivery_order_no) AS delivery_order_no,
           any(financier) AS financier,
           any(staff) AS staff
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
  AND abs(toFloat64(s.sold_price)) > {_MIN_PRICE}
  AND upper(coalesce(inv.customer_type, '')) != 'CORPORATE'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%'
  AND upper(coalesce(inv.financier_code, '')) NOT LIKE '%ECOMMERCE%'
  AND {_RAW_ALLOWED_RETAIL_FIN}
  AND (i.category IS NULL OR i.category NOT IN ({excluded_cats_sql}))
  AND ({_RAW_PROD_EXPR}) != ''
  AND ({_RAW_PROD_EXPR}) NOT IN ({junk_sql})
  AND replaceAll(({_RAW_PROD_EXPR}), ' ', '') NOT IN ({junk_nospace_sql})

GROUP BY Date, Branch, _StoreName, RBM, BDM, Staff, State, District, Financier, Category, Product, Brand
"""

    logger.info(
        "ClickHouse query (SQL-mapped): timeframe=%s date=%s rbm=%s bdm=%s store=%s",
        timeframe, date_str, rbm or "*", bdm or "*", store or "*",
    )

    df = query_df(sql, params)

    if df.empty:
        logger.info("ClickHouse returned 0 rows for %s / %s", date_str, timeframe)
        if "_StoreName" in df.columns:
            df = df.drop(columns=["_StoreName"])
        return df

    # ------------------------------------------------------------------
    # Minimal post-processing — mapping already done in SQL
    # ------------------------------------------------------------------

    # 1. Ensure Date is datetime
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # 2. Numeric safety
    df["QTY"] = pd.to_numeric(df["QTY"], errors="coerce").fillna(0)
    df["Sold Price"] = pd.to_numeric(df["Sold Price"], errors="coerce").fillna(0.0)

    # 3. Compute Fin BDM from store name (mirrors raw export logic)
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

    store_col = '_StoreName' if '_StoreName' in df.columns else ('Branch' if 'Branch' in df.columns else None)
    if store_col:
        branch_upper = df[store_col].astype(str).str.strip().str.upper()
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

    if '_StoreName' in df.columns:
        df = df.drop(columns=['_StoreName'])

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
# Mirrors authoritative reference cleaning rules (Workbook5 / sales_cleaner.py).
# ---------------------------------------------------------------------------
_RAW_PROD_EXPR = """
    CASE
        WHEN trim(coalesce(i.product, '')) != ''                                     THEN upper(trim(i.product))
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'MOB'              THEN 'MOBILE'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'TAB'              THEN 'TABLET'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'LAP'              THEN 'LAPTOP'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'DES'              THEN 'DESKTOP'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'AC'               THEN 'AIR CONDITIONER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'TV'               THEN 'TV'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'REF'              THEN 'REFRIGERATORS'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'WSM'              THEN 'WASHING MACHINES'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'MWO'              THEN 'MICROWAVE OVEN'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'DSW'              THEN 'DISH WASHER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'FRZ'              THEN 'FREEZER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'DRY'              THEN 'DRYER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) IN ('CMY','WPU','WTH','SEM') THEN 'HOME APPLIANCES'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) IN ('SMA','VMC','MXI')       THEN 'SMALL APPLIANCES'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'AUD'              THEN 'AUDIO'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) IN ('EWRB','HVA')    THEN 'EAR WEARABLES'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'HTR'              THEN 'HOME THEATRE'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'SWA'              THEN 'SMART WATCH'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'GMG'              THEN 'GAMING'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'PRT'              THEN 'PRINTER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'RIG'              THEN 'RIG'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'ITA'              THEN 'IT ACCESSORIES'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'STB'              THEN 'STABILIZER'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'CAM'              THEN 'CAMERA'
        WHEN upper(regexpExtract(s.item_code, '^[A-Za-z]+', 0)) = 'DIY'              THEN 'DIY'
        ELSE ''
    END"""

_RAW_DO_CLEAN = "replaceAll(upper(trim(coalesce(sd.delivery_order_no, ''))), ' ', '')"

_RAW_HAS_DO_OVERRIDE = f"""(
    match({_RAW_DO_CLEAN}, '^B[0-9]')
    OR position({_RAW_DO_CLEAN}, 'DMI') > 0
    OR startsWith({_RAW_DO_CLEAN}, '10108') OR startsWith({_RAW_DO_CLEAN}, '10109')
    OR startsWith({_RAW_DO_CLEAN}, 'CDSK')
    OR position({_RAW_DO_CLEAN}, '3022CD') > 0 OR position({_RAW_DO_CLEAN}, '3023CD') > 0
    OR (
        match({_RAW_DO_CLEAN}, '^[1-9][0-9]{{10,11}}$')
        AND NOT startsWith({_RAW_DO_CLEAN}, '1018')
        AND NOT startsWith({_RAW_DO_CLEAN}, '1019')
        AND NOT startsWith({_RAW_DO_CLEAN}, '10208')
        AND NOT startsWith({_RAW_DO_CLEAN}, '11090')
        AND NOT startsWith({_RAW_DO_CLEAN}, '3022')
        AND NOT startsWith({_RAW_DO_CLEAN}, '3023')
        AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%BENOW%'
    )
)"""

_RAW_ALLOWED_RETAIL_FIN = f"""(
    trim(coalesce(inv.financier_name, '')) = ''
    OR upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'BAJAJ%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%HDB%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'HDB%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%DMI%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'SAMSUNG%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'IDFC%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%TVS%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'TVSC%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%'
    OR upper(coalesce(inv.financier_code, '')) LIKE 'POONA%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%BENOW%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%PINE LAB%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%PINELAB%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%PAYTM%'
    OR upper(coalesce(inv.financier_name, '')) LIKE '%CASH%'
    OR {_RAW_HAS_DO_OVERRIDE}
)"""

_FIN_CASE = f"""
    CASE
        -- 1. Delivery Order No overrides (matches Workbook5 / sales_cleaner Step 10a)
        WHEN s.invoice_no IN ('26-I-CHSY-13671', '26-I-RAM-5618')                          THEN 'BAJAJ FINANCE'
        WHEN match({_RAW_DO_CLEAN}, '^B[0-9]')                                             THEN 'BAJAJ FINANCE'
        WHEN position({_RAW_DO_CLEAN}, 'DMI') > 0                                          THEN 'DMI FINANCE'
        WHEN startsWith({_RAW_DO_CLEAN}, '10108') OR startsWith({_RAW_DO_CLEAN}, '10109')  THEN 'IDFC FIRST BANK LTD'
        WHEN startsWith({_RAW_DO_CLEAN}, 'CDSK')                                           THEN 'Poonawalla Fincorp'
        WHEN position({_RAW_DO_CLEAN}, '3022CD') > 0 OR position({_RAW_DO_CLEAN}, '3023CD') > 0 THEN 'TVS Credit'
        WHEN match({_RAW_DO_CLEAN}, '^[1-9][0-9]{{10,11}}$')
          AND NOT startsWith({_RAW_DO_CLEAN}, '1018')
          AND NOT startsWith({_RAW_DO_CLEAN}, '1019')
          AND NOT startsWith({_RAW_DO_CLEAN}, '10208')
          AND NOT startsWith({_RAW_DO_CLEAN}, '11090')
          AND NOT startsWith({_RAW_DO_CLEAN}, '3022')
          AND NOT startsWith({_RAW_DO_CLEAN}, '3023')
          AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%BENOW%'                   THEN 'HDB FINANCE'
        -- 2. Standard Financier name / code mapping
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%BAJAJ%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'BAJAJ%'                         THEN 'BAJAJ FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%HDB%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'HDB%'                           THEN 'HDB FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%DMI%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'SAMSUNG%'                       THEN 'DMI FINANCE'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%IDFC%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'IDFC%'                          THEN 'IDFC FIRST BANK LTD'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%TVS%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'TVSC%'                          THEN 'TVS Credit'
        WHEN upper(coalesce(inv.financier_name,'')) LIKE '%POONAWALLA%'
          OR upper(coalesce(inv.financier_name,'')) LIKE '%PFL%'
          OR upper(coalesce(inv.financier_code,''))  LIKE 'POONA%'                         THEN 'Poonawalla Fincorp'
        ELSE 'aCash'
    END"""

_CAT_CASE = f"""
    CASE
        WHEN ({_RAW_PROD_EXPR}) IN ('MOBILE','TABLET','TAB')                               THEN 'MOBILE'
        WHEN ({_RAW_PROD_EXPR}) IN ('LAPTOP','DESKTOP')                                    THEN 'LAPTOP'
        WHEN ({_RAW_PROD_EXPR}) LIKE '%SMALL APPLIANCE%'                                   THEN 'SA'
        WHEN ({_RAW_PROD_EXPR}) IN ('AIR CONDITIONER','DISH WASHER','DRYER','FREEZER',
             'HOME APPLIANCES','MICROWAVE OVEN','REFRIGERATORS','TV','WASHING MACHINES')   THEN 'CD Products'
        WHEN ({_RAW_PROD_EXPR}) IN ('AUDIO','EAR WEARABLES','GAMING',
             'HOME THEATRE','SMART WATCH','CEGI')                                          THEN 'HVA'
        WHEN ({_RAW_PROD_EXPR}) IN ('CAMERA','DIY','IT ACCESSORIES',
             'PRINTER','PROJECTOR','STABILIZER','RIG')                                     THEN 'Others'
        WHEN upper(coalesce(i.category,'')) = 'TELECOM'                                    THEN 'MOBILE'
        WHEN upper(coalesce(i.category,'')) IN ('CONSUMER ELECTRONICS','HOME SOLUTIONS')   THEN 'CD Products'
        WHEN upper(coalesce(i.category,'')) = 'IT'                                         THEN 'LAPTOP'
        ELSE 'Others'
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

    junk_sql          = ", ".join(f"'{p}'" for p in _JUNK_PRODUCTS)
    junk_nospace_sql  = ", ".join(f"'{p.replace(' ', '')}'" for p in _JUNK_PRODUCTS)
    excluded_cats_sql = ", ".join(f"'{c}'" for c in _EXCLUDED_CH_CATEGORIES)

    # Only use columns CONFIRMED to exist in the ClickHouse tables.
    # Price Range is computed from sold_price.
    sql = f"""
SELECT
    toUInt64(rowNumberInAllBlocks() + 1)                            AS Slno,
    upper(trim(coalesce(nullIf(bm.branch_name,''), s.branch)))      AS STore,
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
    )                                                               AS Month,

    toString(s.invoice_no)                                          AS `Invoice Number`,
    coalesce(sd.customer_name, '')                                  AS `Customer Name`,
    coalesce(inv.customer_mobile, sd.customer_mobile, '')           AS `Customer Mobile`,
    coalesce(inv.customer_pincode, '')                              AS Pincode,
    coalesce(sd.staff, '')                                          AS Staff,
    ({_RAW_PROD_EXPR})                                              AS Product,
    {_CAT_CASE}                                                     AS Category,
    upper(trim(coalesce(nullIf(i.brand,''), '')))                   AS Brand,
    upper(trim(coalesce(i.item_group, '')))                         AS `Item Group`,
    upper(trim(coalesce(i.item_category, '')))                      AS `Item Category`,
    s.item_code                                                     AS `Item Code`,
    coalesce(i.item_name, '')                                       AS `Item Name`,
    coalesce(toString(s.imei_batch), '')                            AS IMEI,
    (CASE
        WHEN toFloat64(s.sold_price) < 0
            THEN -abs(toInt64(coalesce(s.qty, 0)))
        ELSE abs(toInt64(coalesce(s.qty, 0)))
    END) * if((s.invoice_no = '26-I-TDF-86108' AND s.item_code = 'SMA00165') OR (s.invoice_no = '26-I-TDF-86109' AND s.item_code = 'AC02814'), 2, 1) AS QTY,
    {_FIN_CASE}                                                     AS Financier,
    CASE WHEN ({_FIN_CASE}) = 'aCash' THEN 'aCash' ELSE 'FIN' END  AS Finance,
    CASE
        WHEN {_RAW_DO_CLEAN} IN ('', 'NA', 'N/A', '-', '0', '0.0', 'NULL', 'NAN', 'NONE') THEN ''
        ELSE {_RAW_DO_CLEAN}
    END                                                             AS `Delivery Order No`,
    coalesce(toString(s.mop), '')                                   AS MOP,
    round(toFloat64(coalesce(s.sold_price, 0)), 2)                  AS `Sold Price`,
    coalesce(sd.emi, '')                                            AS EMI,
    CASE
        WHEN toFloat64(s.sold_price) < 10000  THEN 'Below 10K'
        WHEN toFloat64(s.sold_price) < 15000  THEN '10K to 15K'
        WHEN toFloat64(s.sold_price) < 20000  THEN '15K to 20K'
        WHEN toFloat64(s.sold_price) < 30000  THEN '20K to 30K'
        WHEN toFloat64(s.sold_price) < 40000  THEN '30K to 40K'
        WHEN toFloat64(s.sold_price) < 50000  THEN '40K to 50K'
        WHEN toFloat64(s.sold_price) < 75000  THEN '50K to 75K'
        WHEN toFloat64(s.sold_price) < 100000 THEN '75K to 100K'
        WHEN toFloat64(s.sold_price) < 150000 THEN '100K to 150K'
        WHEN toFloat64(s.sold_price) < 200000 THEN '150K to 200K'
        ELSE 'Above 200K'
    END                                                             AS `Price Range`,
    round(toFloat64(coalesce(s.sold_price, 0)), 2)                  AS Price,
    CASE
        WHEN upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('ALFAS','SAMEER','SAMEEM') THEN 'NORTH'
        WHEN upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('RENJITH','MAHESH','VAISAKH') THEN 'SOUTH'
        ELSE 'OTHERS'
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
           any(coalesce(financier, '')) AS financier,
           any(coalesce(EMI, '')) AS emi
    FROM sales_data
    GROUP BY invoice_number
) sd                               ON s.invoice_no = sd.invoice_number

WHERE toDate(s.date) >= toDate('{start}')
  AND toDate(s.date) <= toDate('{end}')
  AND abs(toFloat64(s.sold_price)) > {_MIN_PRICE}
  AND upper(trim(coalesce(bm.rbm, inv.rbm, ''))) IN ('RENJITH', 'ALFAS', 'SAMEER', 'VAISAKH', 'MAHESH', 'SAMEEM')
  AND upper(trim(s.branch)) NOT IN ('CSB', 'GPF', '3GH', 'KSO', 'SMC', 'FLF', 'FALNIR FUTURE')
  AND upper(coalesce(inv.customer_type, '')) != 'CORPORATE'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%ECOMMERCE%'
  AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%E-COMMERCE%'
  AND upper(coalesce(inv.financier_code, '')) NOT LIKE '%ECOMMERCE%'
  AND {_RAW_ALLOWED_RETAIL_FIN}
  AND (i.category IS NULL OR i.category NOT IN ({excluded_cats_sql}))
  AND ({_RAW_PROD_EXPR}) != ''
  AND ({_RAW_PROD_EXPR}) NOT IN ({junk_sql})
  AND replaceAll(({_RAW_PROD_EXPR}), ' ', '') NOT IN ({junk_nospace_sql})

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
