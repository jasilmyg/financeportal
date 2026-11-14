"""
paytm_cleaner.py
================
Cleaning + dashboard-linking logic for the Paytm 'Total Transaction report'.

Same pipeline as pine_lab_cleaner.py, with these Paytm-specific rules:
  1. Txn_type (the file's own column: Bank_EMI | Brand_EMI | Other Mode):
     'Other Mode' rows are REMOVED.  (If a file has no Txn_type column it is
     derived: brand present = Brand_EMI, no brand + Txn_check 'EMI' = Bank_EMI,
     everything else = Other Mode.)
  2. original_amount ("org amount") is the amount used for every calculation
     and invoice match
  3. Rows with original_amount below 1000 are removed
  4. Deduplicate on txn_id (keep first)
  5. brand_name keeps ONLY the brand name ('Apple - PBL' -> 'Apple',
     'Samsung Mobile' -> 'Samsung' ...)
  6. Product / Category mapping re-uses the Pine Labs cascade
     (pine_lab_cleaner.apply_product_mapping)
"""

import io
import os
import json
import re
import zipfile
import logging

import pandas as pd

from pine_lab_cleaner import apply_product_mapping

log = logging.getLogger(__name__)

FINANCIER_NAME = 'PAYTM'          # value stored in benow_pine_financier.financier
MIN_AMOUNT     = 1000             # rows with original_amount below this are removed

# -- Column name constants (Paytm Total Transaction report) -------------------
COL_TXN_ID    = 'txn_id'
COL_DATE      = 'txn_date'
COL_STORE     = 'store_name'
COL_PROD_CAT  = 'product_category'
COL_PROD_NAME = 'productname'
COL_BRAND     = 'brand_name'
COL_SERIAL    = 'imei'
COL_ORG_AMT   = 'original_amount'
COL_TXN_TYPE  = 'Txn_type'
COL_TXN_CHECK = 'Txn_check'
COL_MID       = 'mid'
COL_BRANCH    = 'Branch'      # cleaned branch name (derived from store_name)

_EXPECTED_COLS = [COL_TXN_ID, COL_DATE, COL_STORE, COL_PROD_CAT, COL_PROD_NAME,
                  COL_BRAND, COL_SERIAL, COL_ORG_AMT, COL_TXN_TYPE, COL_TXN_CHECK, COL_MID]

TXN_BRAND_EMI = 'Brand_EMI'
TXN_BANK_EMI  = 'Bank_EMI'
TXN_OTHER     = 'Other Mode'


# -- Brand-name cleaning ------------------------------------------------------
# Canonical display names (anything not listed is Title-cased).
_BRAND_DISPLAY = {
    'HP': 'HP', 'LG': 'LG', 'ASUS': 'ASUS', 'MSI': 'MSI', 'IFB': 'IFB',
    'JBL': 'JBL', 'TCL': 'TCL', 'BOAT': 'BoAt', 'IQOO': 'iQOO',
    'ONEPLUS': 'OnePlus', 'ONE PLUS': 'OnePlus', 'BLUE STAR': 'Blue Star',
    'FIRE BOLTT': 'Fire-Boltt', 'CARRIER MIDEA': 'Carrier Midea',
}
_KNOWN_BRANDS = [
    'SAMSUNG', 'APPLE', 'ONE PLUS', 'ONEPLUS', 'VIVO', 'OPPO', 'REALME', 'XIAOMI',
    'REDMI', 'POCO', 'MOTOROLA', 'NOKIA', 'NOTHING', 'IQOO', 'TECNO', 'INFINIX',
    'LAVA', 'GOOGLE', 'HONOR', 'HUAWEI', 'HP', 'DELL', 'LENOVO', 'ACER', 'ASUS',
    'MSI', 'MICROSOFT', 'DAIKIN', 'VOLTAS', 'CARRIER MIDEA', 'CARRIER', 'BLUE STAR',
    'HITACHI', 'PANASONIC', 'HAIER', 'GODREJ', 'BOAT', 'JBL', 'SONY', 'BOSE', 'NOISE',
    'FIRE BOLTT', 'BOULT', 'LG', 'WHIRLPOOL', 'IFB', 'BOSCH', 'TOSHIBA', 'TCL',
]
# Longest first so 'CARRIER MIDEA' wins over 'CARRIER'
_KNOWN_BRANDS.sort(key=len, reverse=True)


def clean_brand_name(val):
    """
    Reduce a brand/manufacturer string to just the brand name.
        'SAMSUNGCE' / 'Samsung Mobile' / 'Samsung CE' -> 'Samsung'
        'Apple - PBL' / 'Apple Pre-book' / 'Apple MacBook-PBL' -> 'Apple'
        'VIVO' -> 'Vivo',   'realme' -> 'Realme',   'HP' -> 'HP'
    """
    if pd.isna(val):
        return val
    raw = str(val).strip()
    if raw == '' or raw.lower() in ('nan', 'none', 'null'):
        return None
    norm = re.sub(r'[^A-Z0-9]+', ' ', raw.upper()).strip()

    for b in _KNOWN_BRANDS:
        if norm == b or norm.startswith(b + ' '):
            return _BRAND_DISPLAY.get(b, b.title())
        # Glued suffix like 'SAMSUNGCE' / 'APPLEPBL' (only for longer brand names
        # so 'HP' / 'LG' never swallow unrelated words)
        if len(b) >= 5 and ' ' not in b and norm.startswith(b):
            return _BRAND_DISPLAY.get(b, b.title())

    # Unknown brand: keep the first word only
    first = norm.split(' ')[0] if norm else raw
    return _BRAND_DISPLAY.get(first, first.title())


def _blank(series: pd.Series) -> pd.Series:
    """True where a cell is empty / NaN / 'nan' / 'null'."""
    s = series.fillna('').astype(str).str.strip().str.lower()
    return s.isin(['', 'nan', 'none', 'null'])


def _strip_apostrophe(series: pd.Series) -> pd.Series:
    """Paytm exports prefix long ids / IMEIs with a leading apostrophe (Excel text marker)."""
    return series.where(series.isna(), series.astype(str).str.strip().str.lstrip("'").str.strip())


def clean_branch_name(store_name) -> str:
    """'myG THONDAYAD FUTURE' / 'myG_mavelikkara' / 'MyG_KALPETTA' -> branch name only, upper-case."""
    if store_name is None or (isinstance(store_name, float) and pd.isna(store_name)):
        return ''
    s = re.sub(r'(?i)^\s*my\s*g[\s_\-]*', '', str(store_name))
    s = re.sub(r'[\s_]+', ' ', s).strip().upper()
    return STORE_ALIASES.get(s, s)


# Merchants that are not myG sales branches; their rows are left out of the cleaned output
EXCLUDED_BRANCHES = {'CAFE YMCA'}


# -- Core cleaning function ---------------------------------------------------

def clean_paytm(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Apply Paytm cleaning rules and return (cleaned_df, stats_dict).

    stats_dict: raw_rows, after_filter, after_dedup, date_from, date_to,
                total_orig_amt, removed_other_mode, removed_below_min
    """
    stats = {'raw_rows': len(df)}
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Ids / IMEIs: remove the leading apostrophe
    df[COL_TXN_ID] = _strip_apostrophe(df[COL_TXN_ID])
    df[COL_SERIAL] = _strip_apostrophe(df[COL_SERIAL])
    df = df[~_blank(df[COL_TXN_ID])].copy()

    # -- Org amount ------------------------------------------------------------
    df[COL_ORG_AMT] = pd.to_numeric(df[COL_ORG_AMT], errors='coerce').fillna(0)

    # -- Txn_type: file's own column wins; derive only when missing / blank -----
    has_brand = ~_blank(df[COL_BRAND])
    is_bank   = df[COL_TXN_CHECK].fillna('').astype(str).str.strip().str.upper() == 'EMI' \
        if COL_TXN_CHECK in df.columns else pd.Series(False, index=df.index)
    derived = pd.Series(TXN_OTHER, index=df.index)
    derived[is_bank & ~has_brand] = TXN_BANK_EMI
    derived[has_brand]            = TXN_BRAND_EMI

    if COL_TXN_TYPE in df.columns:
        canon = {'bankemi': TXN_BANK_EMI, 'brandemi': TXN_BRAND_EMI, 'othermode': TXN_OTHER}
        raw_t = df[COL_TXN_TYPE].fillna('').astype(str).str.strip()
        norm_t = raw_t.map(lambda v: canon.get(re.sub(r'[\s_]+', '', v).lower(), v))
        df[COL_TXN_TYPE] = norm_t.where(norm_t != '', derived)
        log.info('Paytm: using Txn_type column from the uploaded file')
    else:
        df[COL_TXN_TYPE] = derived
        log.info('Paytm: no Txn_type column in file - derived')

    # Rule: remove Other Mode
    keep_type = df[COL_TXN_TYPE] != TXN_OTHER
    stats['removed_other_mode'] = int((~keep_type).sum())
    df = df[keep_type]

    # Rule: remove amounts below 1000 (on original_amount)
    keep_amt = df[COL_ORG_AMT] >= MIN_AMOUNT
    stats['removed_below_min'] = int((~keep_amt).sum())
    df = df[keep_amt].copy()
    stats['after_filter'] = len(df)
    log.info(f"Paytm: {stats['raw_rows']} raw -> {stats['after_filter']} after filter "
             f"(-{stats['removed_other_mode']} Other Mode, -{stats['removed_below_min']} < {MIN_AMOUNT})")

    # Rule: dedup on txn_id
    df_clean = df.drop_duplicates(subset=[COL_TXN_ID], keep='first').copy()
    stats['after_dedup'] = len(df_clean)

    # Date range
    df_clean['_date'] = pd.to_datetime(df_clean[COL_DATE], errors='coerce')
    stats['date_from'] = df_clean['_date'].min().strftime('%Y-%m-%d') if not df_clean.empty else ''
    stats['date_to']   = df_clean['_date'].max().strftime('%Y-%m-%d') if not df_clean.empty else ''
    stats['total_orig_amt'] = float(df_clean[COL_ORG_AMT].sum())

    # -- Brand: brand name only ----------------------------------------------
    df_clean[COL_BRAND] = df_clean[COL_BRAND].apply(clean_brand_name)

    # -- Product / Category: reuse the Pine Labs cascade ------------------------
    def _norm_cat(v):
        if pd.isna(v):
            return v
        v = re.sub(r'(?i)theater', 'Theatre', str(v))
        return re.sub(r'(?i)\bspeakers\b', 'Speaker', v)

    tmp = pd.DataFrame(index=df_clean.index)
    tmp['Product Category'] = df_clean[COL_PROD_CAT].apply(_norm_cat) if COL_PROD_CAT in df_clean.columns else None
    tmp['Product Name']     = df_clean[COL_PROD_NAME] if COL_PROD_NAME in df_clean.columns else None
    tmp['Manufacturer']     = df_clean[COL_BRAND]
    tmp['Serial']           = df_clean[COL_SERIAL].where(~_blank(df_clean[COL_BRAND]), None)
    tmp = apply_product_mapping(tmp)
    df_clean['Product']  = tmp['Product']
    df_clean['Category'] = tmp['Category']
    if COL_TXN_TYPE in df_clean.columns:
        bank_mask = df_clean[COL_TXN_TYPE] == TXN_BANK_EMI
        if bank_mask.any():
            df_clean.loc[bank_mask, 'Category'] = 'BANK EMI'
            df_clean.loc[bank_mask, 'Product']  = 'BANK EMI'

    # -- Branch: branch name only (drop the 'myG' prefix) ----------------------
    df_clean[COL_BRANCH] = df_clean[COL_STORE].apply(clean_branch_name)
    if COL_MID in df_clean.columns:
        _pt_bmap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'paytm_mid_branch_map.json')
        if os.path.exists(_pt_bmap_path):
            try:
                with open(_pt_bmap_path, encoding='utf-8') as _fh:
                    _pt_bmap = json.load(_fh)
                _mid_s = _strip_apostrophe(df_clean[COL_MID]).fillna('').astype(str).str.strip()
                _mapped_b = _mid_s.map(_pt_bmap)
                df_clean[COL_BRANCH] = _mapped_b.where(_mapped_b.notna() & (_mapped_b != ''), df_clean[COL_BRANCH])
            except Exception:
                pass
    cols = list(df_clean.columns)
    cols.remove(COL_BRANCH)
    cols.insert(cols.index(COL_STORE) + 1, COL_BRANCH)
    df_clean = df_clean[cols]

    # Rule: drop non-branch merchants (e.g. CAFE YMCA) - not ERP sales
    is_excluded = df_clean[COL_BRANCH].isin(EXCLUDED_BRANCHES)
    stats['removed_excluded_branch'] = int(is_excluded.sum())
    if is_excluded.any():
        df_clean = df_clean[~is_excluded].copy()
        stats['after_dedup'] = len(df_clean)
        stats['total_orig_amt'] = float(df_clean[COL_ORG_AMT].sum())
        log.info(f"Paytm: removed {stats['removed_excluded_branch']} rows from excluded branches {sorted(EXCLUDED_BRANCHES)}")

    return df_clean, stats


# -- File loader (CSV / ZIP-of-CSV / XLSX) ------------------------------------

def _read_csv_any(src) -> pd.DataFrame:
    data = src.read() if hasattr(src, 'read') else open(src, 'rb').read()
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError('Could not decode the Paytm CSV file.')


def load_paytm_file(file_storage) -> pd.DataFrame:
    """Load a Paytm report (.csv, .xlsx or .zip containing a .csv)."""
    filename = file_storage.filename.lower()

    if filename.endswith('.zip'):
        zbytes = io.BytesIO(file_storage.read())
        with zipfile.ZipFile(zbytes) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith('.csv')]
            if not names:
                raise ValueError('No CSV file found inside the ZIP archive.')
            with zf.open(names[0]) as f:
                df = _read_csv_any(f)
    elif filename.endswith('.csv'):
        df = _read_csv_any(file_storage)
    elif filename.endswith('.xlsx') or filename.endswith('.xls'):
        df = pd.read_excel(file_storage, dtype=str)
    else:
        raise ValueError('Unsupported file type. Please upload a .csv, .xlsx or .zip file.')

    # Match headers case-insensitively to the canonical names
    canon = {c.lower(): c for c in _EXPECTED_COLS}
    df.columns = [canon.get(str(c).strip().lower(), str(c).strip()) for c in df.columns]

    required = [COL_TXN_ID, COL_DATE, COL_STORE, COL_BRAND, COL_SERIAL, COL_ORG_AMT]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f'Missing required columns in Paytm file: {missing}. '
            f'Please upload the Paytm "Total Transaction report".'
        )
    log.info(f'Paytm: loaded {len(df)} rows')
    return df


# -- ClickHouse upload --------------------------------------------------------

# Paytm store name (without the 'myG ' prefix, upper-case) -> branch_master branch_name.
# Add a line here whenever a Paytm store is spelled differently from branch_master.
STORE_ALIASES = {
    'FEROK FUTURE':          'FEROKE FUTURE',
    'WADAKKANCHERY FUTURE':  'VADAKKENCHERRY FUTURE',
}


def _resolve_branch_code(store_name, branch_map: dict):
    """
    Paytm store names look like 'myG ALUVA' or 'myG THONDAYAD FUTURE'.
    Resolution order:
      1. explicit STORE_ALIASES entry
      2. full name, then name without a trailing 3-letter city code
      3. name + ' FUTURE'  (e.g. 'myG EDAPPALLY' -> 'EDAPPALLY FUTURE')
    """
    s = re.sub(r'\s+', ' ', str(store_name).upper().strip())
    if s.startswith('MYG '):
        s = s[4:]
    if s in STORE_ALIASES and STORE_ALIASES[s] in branch_map:
        return branch_map[STORE_ALIASES[s]]
    candidates = [s]
    parts = s.split(' ')
    if len(parts) > 1 and len(parts[-1]) == 3 and parts[-1].isalpha():
        candidates.append(' '.join(parts[:-1]))
    candidates.append(s + ' FUTURE')
    for c in candidates:
        if c in branch_map:
            return branch_map[c]
    return None


def _load_mid_map() -> dict:
    """MID -> branch code, from data/paytm_mid_map.json (edit that file to add/fix a MID)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'paytm_mid_map.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return {str(k).strip(): str(v).strip().upper() for k, v in json.load(fh).items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning(f'Paytm: could not read MID map {path}: {e}')
        return {}


def upload_to_clickhouse(df_clean: pd.DataFrame, stats: dict) -> dict:
    """
    Invoice-based Paytm matching (single bulk ERP query, matching done in pandas):
      1. (branch, date, original_amount) of each Paytm row is matched to
         azure_sales_report to find the exact invoice_no.
      2. (imei, 'PAYTM', invoice_no, data_month) goes to benow_pine_financier, which the
         dashboard already reads for the PAYTM column.
      3. Real (non-dummy) IMEIs with no invoice match are stored directly.
    """
    from clickhouse_client import get_client
    client = get_client()

    client.command("""
        CREATE TABLE IF NOT EXISTS benow_pine_financier (
            imei       String,
            financier  String,
            invoice_no String DEFAULT '',
            data_month String DEFAULT ''
        )
        ENGINE = ReplacingMergeTree()
        ORDER BY (imei, financier, invoice_no)
    """)
    for ddl in [
        "ALTER TABLE benow_pine_financier ADD COLUMN IF NOT EXISTS invoice_no  String DEFAULT ''",
        "ALTER TABLE benow_pine_financier ADD COLUMN IF NOT EXISTS data_month  String DEFAULT ''",
    ]:
        try:
            client.command(ddl)
        except Exception:
            pass

    branch_result = client.query(
        "SELECT code, upper(branch_name) AS bn FROM branch_master WHERE store_type = 'BRANCH'"
    )
    branch_map = {row[1].strip(): row[0] for row in branch_result.result_rows}

    df_work = df_clean.copy()
    mid_map = _load_mid_map()
    if COL_MID in df_work.columns:
        by_mid = df_work[COL_MID].fillna('').astype(str).str.strip().map(mid_map)
    else:
        by_mid = pd.Series(None, index=df_work.index, dtype=object)
    by_name = df_work[COL_STORE].apply(lambda s: _resolve_branch_code(s, branch_map))
    # MID is the primary key; store-name matching is the fallback for unknown MIDs
    df_work['_branch_code'] = by_mid.where(by_mid.notna(), by_name)
    stats['mapped_by_mid'] = int(by_mid.notna().sum())
    if COL_MID in df_work.columns:
        unk = df_work[df_work['_branch_code'].isna()]
        stats['unmapped_stores'] = (
            unk.groupby([COL_MID, COL_STORE]).size().reset_index(name='rows')
            .astype(str).to_dict('records')
        )
        if stats['unmapped_stores']:
            log.warning(f"Paytm: MIDs with no branch mapping (add to data/paytm_mid_map.json): "
                        f"{stats['unmapped_stores']}")
    df_work['_date']        = pd.to_datetime(df_work[COL_DATE], errors='coerce')
    df_work['_date_str']    = df_work['_date'].dt.strftime('%Y-%m-%d')
    df_work['_amt']         = pd.to_numeric(df_work[COL_ORG_AMT], errors='coerce').round(0)

    first_valid = df_work['_date'].dropna().min()
    last_valid  = df_work['_date'].dropna().max()
    date_min_str = first_valid.strftime('%Y-%m-%d') if pd.notna(first_valid) else ''
    date_max_str = last_valid.strftime('%Y-%m-%d') if pd.notna(last_valid) else ''
    months_covered = sorted(df_work['_date'].dropna().dt.strftime('%Y-%m').unique().tolist())
    data_month = first_valid.strftime('%Y-%m') if pd.notna(first_valid) else ''
    log.info(f'Paytm upload: span = {date_min_str} to {date_max_str}, months = {months_covered}')

    # NOTE: the previous upload's rows for this range are removed *after* the new rows are
    # inserted (clickhouse_client.replace_rows) - deleting first can lose the new rows.

    matchable = df_work[
        df_work['_branch_code'].notna() &
        df_work['_date_str'].notna() & df_work['_date_str'].ne('NaT') &
        (df_work['_amt'] >= MIN_AMOUNT)
    ].copy()
    stats['unmatched_branch'] = int(df_work['_branch_code'].isna().sum())
    log.info(f'Paytm upload: {len(matchable)} matchable (branch+date+amount), '
             f'{stats["unmatched_branch"]} rows with unknown branch')

    records_invoice, matched_count = [], 0

    if not matchable.empty:
        date_min = matchable['_date_str'].min()
        date_max = matchable['_date_str'].max()
        branch_codes = matchable['_branch_code'].dropna().unique().tolist()
        branch_in = "('" + "','".join(str(b).upper().replace("'", "''") for b in branch_codes) + "')"

        erp_res = client.query(f"""
            SELECT upper(trim(branch))                AS branch_code,
                   toString(toDate(date))             AS sale_date,
                   round(toFloat64(sold_price), 0)    AS sale_amt,
                   toString(imei_batch)               AS imei,
                   toString(invoice_no)               AS invoice_no
            FROM azure_sales_report
            WHERE toDate(date) BETWEEN toDate('{date_min}') AND toDate('{date_max}')
              AND upper(trim(branch)) IN {branch_in}
              AND invoice_no IS NOT NULL AND invoice_no != ''
              AND round(toFloat64(sold_price), 0) >= {MIN_AMOUNT}
        """)
        erp_df = pd.DataFrame(erp_res.result_rows,
                              columns=['branch_code', 'sale_date', 'sale_amt', 'imei', 'invoice_no'])
        erp_df['sale_amt'] = pd.to_numeric(erp_df['sale_amt'], errors='coerce')
        log.info(f'Paytm: ERP bulk query returned {len(erp_df)} rows')

        # Prefer ERP rows whose IMEI is actually in the Paytm file, then take at most
        # as many ERP invoices per (branch, date, amount) as there are Paytm txns.
        paytm_serials = set(
            df_work[COL_SERIAL].dropna().astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        )
        erp_df['_imei_hit'] = erp_df['imei'].isin(paytm_serials)
        erp_df = erp_df.sort_values('_imei_hit', ascending=False, kind='stable')

        key_cols = ['branch_code', 'sale_date', 'sale_amt']
        pk = matchable[['_branch_code', '_date_str', '_amt']].copy()
        pk.columns = key_cols
        pk['branch_code'] = pk['branch_code'].str.upper()
        txn_counts = pk.groupby(key_cols).size().rename('_n').reset_index()

        erp_df = erp_df.merge(txn_counts, on=key_cols, how='inner')
        erp_df['_rank'] = erp_df.groupby(key_cols).cumcount()
        merged = erp_df[erp_df['_rank'] < erp_df['_n']]
        log.info(f'Paytm: matched {len(merged)} ERP invoices via (branch, date, amount)')

        for _, row in merged.iterrows():
            inv_no = str(row['invoice_no']) if row['invoice_no'] else ''
            imei   = str(row['imei']) if row['imei'] else ''
            row_month = str(row['sale_date'])[:7] if row['sale_date'] else data_month
            if inv_no:
                records_invoice.append((imei, FINANCIER_NAME, inv_no, row_month))
                matched_count += 1

    # -- Phase 2: direct IMEI fallback for real serials ------------------------
    DUMMY = {'123456789', '1122334455', '000000000', '111111111', '999999999',
             '0', 'na', 'n/a', 'none', 'null', '-', ''}
    imei_series = (
        df_work[COL_SERIAL].dropna().astype(str).str.strip()
        .str.replace(r'\.0$', '', regex=True).replace('', pd.NA).dropna()
    )
    matched_imeis = {r[0] for r in records_invoice if r[0]}
    real_imeis = [i for i in imei_series.unique().tolist()
                  if i.lower() not in DUMMY and len(i) >= 8 and i not in matched_imeis]
    records_direct = [(i, FINANCIER_NAME, '', data_month) for i in real_imeis]

    seen, final_records = set(), []
    for imei, fin, inv, dm in records_invoice + records_direct:
        key = inv if inv else f'_imei_{imei}'
        if key not in seen:
            seen.add(key)
            final_records.append((imei, fin, inv, dm))

    if len(months_covered) == 1:
        month_clause = f"data_month = '{months_covered[0]}'"
    elif months_covered:
        months_in = ", ".join(f"'{m}'" for m in months_covered)
        month_clause = f"data_month IN ({months_in})"
    else:
        month_clause = f"data_month = '{data_month}'"

    log.info(f'Paytm: inserting {len(final_records)} records for months {months_covered}')
    from clickhouse_client import replace_rows
    replace_rows('benow_pine_financier', final_records,
                 ['imei', 'financier', 'invoice_no', 'data_month'],
                 f"{month_clause} AND financier = '{FINANCIER_NAME}'")
    client.command('OPTIMIZE TABLE benow_pine_financier FINAL')

    # ── Insert ALL filtered Paytm rows into paytm_transactions ──────────
    client.command("""
        CREATE TABLE IF NOT EXISTS paytm_transactions (
            txn_date Date,
            txn_id String,
            store String,
            brand String,
            product_name String,
            product_category String,
            pl_product String,
            pl_category String,
            erp_category String,
            qty Float32,
            original_amount Float32,
            data_month String,
            branch String DEFAULT ''
        ) ENGINE = MergeTree()
        ORDER BY (data_month, txn_date)
    """)

    try:
        client.command("ALTER TABLE paytm_transactions ADD COLUMN IF NOT EXISTS branch String DEFAULT ''")
    except Exception:
        pass
    pt_txn_rows = []
    for _, row in df_clean.iterrows():
        r_date = row['_date'].date() if pd.notnull(row['_date']) else pd.to_datetime('2000-01-01').date()
        r_month = r_date.strftime('%Y-%m')
        pt_txn_rows.append((
            r_date,
            str(row[COL_TXN_ID]),
            str(row[COL_STORE]),
            str(row[COL_BRAND]),
            str(row.get(COL_PROD_NAME, '')),
            str(row.get(COL_PROD_CAT, '')),
            str(row.get('Product', '')),
            str(row.get('Category', '')),
            str(row.get('Category', '')),
            1.0,
            float(row[COL_ORG_AMT]) if pd.notnull(row[COL_ORG_AMT]) else 0.0,
            r_month,
            str(row.get(COL_BRANCH, '') or '').strip().upper()
        ))

    # Insert the new rows first, then remove older rows of this exact date range
    from clickhouse_client import replace_rows
    replace_rows(
        'paytm_transactions', pt_txn_rows,
        ['txn_date', 'txn_id', 'store', 'brand', 'product_name', 'product_category',
         'pl_product', 'pl_category', 'erp_category', 'qty', 'original_amount', 'data_month', 'branch'],
        f"toDate(txn_date) BETWEEN toDate('{date_min_str}') AND toDate('{date_max_str}')"
    )
    log.info(f'Paytm: stored {len(pt_txn_rows)} rows in paytm_transactions for range {date_min_str} to {date_max_str}')


    import emi_state
    emi_state.mark_uploaded(emi_state.PAYTM_FIN, months_covered,
                            first_valid, last_valid)

    stats['matched_invoices'] = matched_count
    stats['inserted_records'] = len(final_records)
    stats['phase1_invoice']   = matched_count
    stats['phase2_imei']      = len(records_direct)
    return stats
