import io
import os
import zipfile
import logging
import pandas as pd


from clickhouse_client import get_client

log = logging.getLogger(__name__)

# Constants
FINANCIER_NAME = 'Benow'
MIN_AMOUNT = 1000

def load_benow_file(file_storage) -> pd.DataFrame:
    filename = file_storage.filename.lower()
    if filename.endswith('.zip'):
        with zipfile.ZipFile(file_storage) as z:
            csv_files = [f for f in z.namelist() if f.lower().endswith('.csv')]
            if not csv_files:
                raise ValueError("No CSV file found in the uploaded ZIP.")
            with z.open(csv_files[0]) as f:
                return pd.read_csv(f)
    elif filename.endswith('.csv'):
        return pd.read_csv(file_storage)
    elif filename.endswith(('.xls', '.xlsx')):
        return pd.read_excel(file_storage)
    raise ValueError("Unsupported file format. Please upload .csv, .xlsx, or .zip")

def clean_benow(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    stats = {'raw_rows': len(df)}
    if df.empty:
        return df, stats
    
    df_clean = df.copy()

    # Determine column names (case-insensitive)
    col_map = {c.strip().lower(): c for c in df_clean.columns}
    
    col_date = col_map.get('transaction date', 'Transaction Date')
    col_txn_id = col_map.get('transaction id', 'Transaction ID')
    
    # Drop rows without a Transaction ID (avoids ghost/empty rows and trailing spaces)
    if col_txn_id in df_clean.columns:
        df_clean = df_clean.dropna(subset=[col_txn_id])
        df_clean = df_clean[df_clean[col_txn_id].astype(str).str.strip() != '']
        df_clean = df_clean[df_clean[col_txn_id].astype(str).str.lower() != 'nan']
    col_store = col_map.get('store name', 'Store Name')
    col_amt = col_map.get('sale amount', 'Sale Amount')
    if 'sale amount' not in col_map and 'amount' in col_map:
        col_amt = col_map['amount']
    col_cat = col_map.get('product category', 'Product Category')
    # Brand Mapping
    col_brand = col_map.get('brand name', 'Brand Name')
    if 'brand name' not in col_map and 'brand' in col_map:
        col_brand = col_map['brand']
    elif 'brand name' not in col_map and 'brand code' in col_map:
        col_brand = col_map['brand code']

    # Product Mapping
    col_prod = col_map.get('product name', 'Product Name')
    if 'product name' not in col_map and 'model name' in col_map:
        col_prod = col_map['model name']
    elif 'product name' not in col_map and 'model' in col_map:
        col_prod = col_map['model']
    elif 'product name' not in col_map and 'item name' in col_map:
        col_prod = col_map['item name']
    elif 'product name' not in col_map and 'product' in col_map:
        col_prod = col_map['product']
    elif 'product name' not in col_map and col_cat in df_clean.columns:
        col_prod = col_cat
    elif 'product name' not in col_map and 'product code' in col_map:
        col_prod = col_map['product code']
    col_serial = col_map.get('serial number', 'Serial Number')
    if 'serial number' not in col_map and 'imei' in col_map:
        col_serial = col_map['imei']

    # Date parsing (ONLY DATE FORMAT)
    if col_date in df_clean.columns:
        df_clean['_date'] = pd.to_datetime(df_clean[col_date], dayfirst=True, errors='coerce').dt.normalize()
        stats['date_from'] = df_clean['_date'].min().strftime('%Y-%m-%d') if not df_clean['_date'].isna().all() else ''
        stats['date_to']   = df_clean['_date'].max().strftime('%Y-%m-%d') if not df_clean['_date'].isna().all() else ''
    else:
        df_clean['_date'] = pd.NaT

    # Ensure amount is numeric (No filtering below MIN_AMOUNT anymore)
    if col_amt in df_clean.columns:
        df_clean[col_amt] = pd.to_numeric(df_clean[col_amt], errors='coerce')
    col_txn_amt = col_map.get('txn amount', 'Txn Amount')
    if col_txn_amt in df_clean.columns:
        df_clean[col_txn_amt] = pd.to_numeric(df_clean[col_txn_amt], errors='coerce')
    if col_amt in df_clean.columns and col_txn_amt in df_clean.columns:
        df_clean[col_amt] = df_clean[col_amt].fillna(df_clean[col_txn_amt])
    before_min = len(df_clean)
    if col_amt in df_clean.columns or col_txn_amt in df_clean.columns:
        eff_amt = df_clean[col_amt] if col_amt in df_clean.columns else pd.Series(index=df_clean.index, dtype=float)
        if col_txn_amt in df_clean.columns:
            eff_amt = eff_amt.fillna(df_clean[col_txn_amt])
        df_clean = df_clean[eff_amt.isna() | (eff_amt >= MIN_AMOUNT)]
    stats['removed_below_min'] = before_min - len(df_clean)

    # No deduplication on Transaction ID required anymore
    
    stats['after_filter'] = len(df_clean)
    
    # Category Mapping
    # Map ERP Category for Dashboard (CD PRODUCTS, MOBILE, HVA, etc)
    def map_erp_category(cat):
        if pd.isna(cat):
            return 'BANK EMI'
        cat_str = str(cat).strip().lower()
        if not cat_str:
            return 'BANK EMI'
            
        if 'mobile' in cat_str or 'pad' in cat_str or 'tablet' in cat_str:
            return 'Mobile'
        elif 'panal' in cat_str or 'panel' in cat_str or 'tv' in cat_str or 'television' in cat_str or 'wm' in cat_str or 'washing' in cat_str or 'rer' in cat_str or 'ref' in cat_str or 'refrigeranor' in cat_str or 'refrigerator' in cat_str or 'ac' in cat_str or 'air conditioner' in cat_str or 'rac' in cat_str:
            return 'CD Products'
        elif 'smart watch' in cat_str or 'watch' in cat_str or 'wearable' in cat_str or 'ear' in cat_str or 'buds' in cat_str:
            return 'HVA'
        elif 'laptop' in cat_str or 'desktop' in cat_str:
            return 'Laptop'
        
        return str(cat).strip() # Preserve raw category for unmapped values (e.g. LED)
    
    # Map user-specified Product names based on Category column
    def map_product_name(cat):
        if pd.isna(cat):
            return 'Bank EMI'
        cat_str = str(cat).strip().lower()
        if not cat_str:
            return 'Bank EMI'
            
        if 'mobile' in cat_str:
            return 'Mobile'
        elif 'pad' in cat_str or 'tablet' in cat_str:
            return 'Tablet'
        elif 'panal' in cat_str or 'panel' in cat_str or 'tv' in cat_str or 'television' in cat_str:
            return 'TV'
        elif cat_str == 'wm' or 'washing' in cat_str:
            return 'WASHING MACHINES'
        elif 'rer' in cat_str or 'ref' in cat_str or 'refrigeranor' in cat_str or 'refrigerator' in cat_str:
            return 'Refrigerators'
        elif 'ac' in cat_str or 'air conditioner' in cat_str or 'rac' in cat_str:
            return 'AIR CONDITIONER'
        elif 'laptop' in cat_str or 'desktop' in cat_str:
            return 'Laptop'
        elif 'smart watch' in cat_str or 'watch' in cat_str or 'wearable' in cat_str or 'ear' in cat_str or 'buds' in cat_str:
            return 'EAR WEARABLES'
        
        # Fallback just title cases it to look nice
        return str(cat).strip().title()
    
    if col_cat in df_clean.columns:
        df_clean['Category'] = df_clean[col_cat].apply(map_erp_category)
    else:
        df_clean['Category'] = 'BANK EMI'
        
    if col_prod == col_cat:
        df_clean['Product'] = df_clean[col_cat].apply(map_product_name)
    else:
        df_clean['Product'] = df_clean[col_cat].apply(map_product_name) if col_cat in df_clean.columns else (df_clean[col_prod].apply(map_product_name) if col_prod in df_clean.columns else 'Bank EMI')
    df_clean['Brand'] = df_clean[col_brand] if col_brand in df_clean.columns else ''
    col_mid = col_map.get('merchant id', 'Merchant ID')
    df_clean['Branch'] = ''
    if col_mid in df_clean.columns:
        import json as _json
        _bn_bmap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'benow_mid_branch_map.json')
        if os.path.exists(_bn_bmap_path):
            try:
                with open(_bn_bmap_path, encoding='utf-8') as _fh:
                    _bn_bmap = _json.load(_fh)
                _mid_s = df_clean[col_mid].fillna('').astype(str).str.strip().str.lstrip("'").str.replace(r'\.0$', '', regex=True)
                df_clean['Branch'] = _mid_s.map(_bn_bmap).fillna('')
            except Exception:
                pass
    if (df_clean['Branch'] == '').any() and col_store in df_clean.columns:
        from paytm_cleaner import clean_branch_name as _cbn
        _empty_b = df_clean['Branch'] == ''
        df_clean.loc[_empty_b, 'Branch'] = df_clean.loc[_empty_b, col_store].apply(_cbn)
    
    stats['total_orig_amt'] = float(df_clean[col_amt].sum()) if col_amt in df_clean.columns else 0.0

    return df_clean, stats

def upload_to_clickhouse(df_clean: pd.DataFrame, stats: dict) -> dict:
    if df_clean.empty:
        return stats
    
    client = get_client()
    min_date = df_clean['_date'].dropna().min()
    max_date = df_clean['_date'].dropna().max()
    if pd.isna(min_date) or pd.isna(max_date):
        log.warning("Benow: No valid date found. Cannot upload.")
        return stats
    
    date_min_str = min_date.strftime('%Y-%m-%d')
    date_max_str = max_date.strftime('%Y-%m-%d')
    months_covered = sorted(df_clean['_date'].dropna().dt.strftime('%Y-%m').unique().tolist())
    data_month_str = max_date.strftime('%Y-%m')
    log.info(f'Benow upload: span = {date_min_str} to {date_max_str}, months = {months_covered}')

    # Step 1: Update benow_pine_financier (for IMEI mapping)
    col_map = {c.strip().lower(): c for c in df_clean.columns}
    col_serial = col_map.get('serial number', 'Serial Number')
    
    records_direct = []
    if col_serial in df_clean.columns:
        imei_series = (
            df_clean[col_serial].dropna().astype(str).str.strip()
            .str.replace(r'\.0$', '', regex=True).replace('', pd.NA).dropna()
        )
        DUMMY = {'123456789', '1122334455', '000000000', '111111111', '999999999', '0', 'na', 'n/a', 'none', 'null', '-'}
        real_imeis = [i for i in imei_series.unique().tolist() if i.lower() not in DUMMY and len(i) >= 8]
        records_direct = [(i, 'Benow', '', data_month_str) for i in real_imeis]

    if len(months_covered) == 1:
        month_clause = f"data_month = '{months_covered[0]}'"
    elif months_covered:
        months_in = ", ".join(f"'{m}'" for m in months_covered)
        month_clause = f"data_month IN ({months_in})"
    else:
        month_clause = f"data_month = '{data_month_str}'"

    from clickhouse_client import replace_rows
    replace_rows(
        'benow_pine_financier', records_direct,
        ['imei', 'financier', 'invoice_no', 'data_month'],
        f"{month_clause} AND financier = 'Benow'"
    )
    client.command('OPTIMIZE TABLE benow_pine_financier FINAL')

    # Step 2: Insert into benow_transactions (for direct dashboard counts)
    client.command("""
        CREATE TABLE IF NOT EXISTS benow_transactions (
            txn_date Date,
            txn_id String,
            store String,
            brand String,
            product_name String,
            product_category String,
            erp_category String,
            qty Float32,
            original_amount Float32,
            data_month String,
            branch String DEFAULT ''
        ) ENGINE = MergeTree()
        ORDER BY (data_month, txn_date)
    """)

    try:
        client.command("ALTER TABLE benow_transactions ADD COLUMN IF NOT EXISTS branch String DEFAULT ''")
    except Exception:
        pass
    bn_txn_rows = []
    col_txn_id = col_map.get('transaction id', 'Transaction ID')
    col_store = col_map.get('store name', 'Store Name')
    col_amt = col_map.get('sale amount', 'Sale Amount')

    for _, row in df_clean.iterrows():
        r_date = row['_date'].date() if pd.notnull(row['_date']) else pd.to_datetime('2000-01-01').date()
        r_month = r_date.strftime('%Y-%m')
        bn_txn_rows.append((
            r_date,
            str(row[col_txn_id]) if col_txn_id in row else '',
            str(row[col_store]) if col_store in row else '',
            str(row['Brand']),
            str(row['Product']),
            str(row.get(col_map.get('product category', ''), '')),
            str(row['Category']),
            1.0,
            float(row[col_amt]) if col_amt in row and pd.notnull(row[col_amt]) else 0.0,
            r_month,
            str(row.get('Branch', '') or '').strip().upper()
        ))

    # Insert the new rows first, then remove older rows of this exact date range
    from clickhouse_client import replace_rows
    replace_rows(
        'benow_transactions', bn_txn_rows,
        ['txn_date', 'txn_id', 'store', 'brand', 'product_name', 'product_category',
         'erp_category', 'qty', 'original_amount', 'data_month', 'branch'],
        f"toDate(txn_date) BETWEEN toDate('{date_min_str}') AND toDate('{date_max_str}')"
    )
    log.info(f'Benow: stored {len(bn_txn_rows)} rows in benow_transactions for range {date_min_str} to {date_max_str}')

    import emi_state
    emi_state.mark_uploaded(emi_state.BENOW_FIN, months_covered,
                            min_date, max_date)
    
    stats['inserted_records'] = len(bn_txn_rows)
    return stats
