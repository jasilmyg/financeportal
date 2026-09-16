import os
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# Store → Fin BDM mapping (sourced from reference june 1-4 corrected report)
# ─────────────────────────────────────────────────────────────────────────────
STORE_FIN_BDM_MAP = {
    'AAKKULAM FUTURE':       'Ashik',
    'ADIMALI':               'Febin',
    'ADOOR':                 'Arun',
    'ADOOR FUTURE':          'Arun',
    'ALAPPUZHA':             'Arun',
    'ALAPPUZHA FUTURE':      'Arun',
    'ALUVA':                 'Febin',
    'ANGAMALY':              'Febin',
    'ANGAMALY FUTURE':       'Febin',
    'AREEKODE FUTURE':       'Siraj',
    'ATTINGAL':              'Ashik',
    'ATTINGAL FUTURE':       'Ashik',
    'BALUSSERY':             'Aswin',
    'BALUSSERY FUTURE':      'Aswin',
    'BANK ROAD':             'Sreeju',
    'BATHERY FUTURE':        'Aswin',
    'BYPASS ROAD':           'Siraj',
    'CAMWORLD':              'Junais',
    'CHALAKKUDI':            'Febin',
    'CHALAKKUDY FUTURE':     'Febin',
    'CHANGANASSERY':         'Arun',
    'CHAVAKKAD FUTURE':      'Febin',
    'CHEMMAD FUTURE':        'Siraj',
    'CHERTHALA':             'Arun',
    'CHERTHALA FUTURE':      'Arun',
    'EAST FORT':             'Febin',
    'EDAKKARA':              'Siraj',
    'EDAPPAL':               'Siraj',
    'EDAPPAL FUTURE':        'Siraj',
    'EDAPPALLY FUTURE':      'Arun',
    'ERATTUPETTA FUTURE':    'Arun',
    'FEROKE FUTURE':         'Junais',
    'FORUM MALL':            'Arun',
    'HARIPAD FUTURE':        'Arun',
    'HI LITE MALL':          'Junais',
    'IRINJALAKUDA FUTURE':   'Febin',
    'IRITTY':                'Sreeju',
    'KADAVANTHRA':           'Arun',
    'KALPETTA':              'Aswin',
    'KANHANGAD':             'Sreeju',
    'KANHANGAD FUTURE':      'Sreeju',
    'KANJIRAPPALLY FUTURE':  'Arun',
    'KANNUR FUTURE':         'Sreeju',
    'KARAMANA':              'Ashik',
    'KARUNAGAPPALLY':        'Ashik',
    'KARUNAGAPPALLY FUTURE': 'Ashik',
    'KASARGOD':              'Sreeju',
    'KASARGOD FUTURE':       'Sreeju',
    'KATTAPPANA FUTURE':     'Febin',
    'KAYAMKULAM':            'Arun',
    'KILIMANOOR':            'Ashik',
    'KINGSWAY':              'Junais',
    'KOLLAM':                'Ashik',
    'KOLLAM FUTURE':         'Ashik',
    'KONDOTTY':              'Siraj',
    'KONDOTTY FUTURE':       'Siraj',
    'KOTHAMANGALAM FUTURE':  'Febin',
    'KOTTAKKAL':             'Siraj',
    'KOTTAKKAL FUTURE':      'Siraj',
    'KOTTARAKKARA':          'Ashik',
    'KOTTAYAM FUTURE':       'Arun',
    'KOYILANDY':             'Aswin',
    'KOYILANDY 2':           'Aswin',
    'KUNNAMANGALAM FUTURE':  'Junais',
    'KUNNAMKULAM':           'Febin',
    'KURUPPAM ROAD':         'Febin',
    'KUTTIYADI':             'Aswin',
    'KUTTIYADI FUTURE':      'Aswin',
    'LANDSHIP MALL':         'Junais',
    'MAHE':                  'Sreeju',
    'MALAPPURAM':            'Siraj',
    'MANANTHAVADY':          'Aswin',
    'MANANTHAVADY FUTURE':   'Aswin',
    'MANJERY':               'Siraj',
    'MANNARKAD FUTURE':      'Siraj',
    'MAVELIKARA FUTURE':     'Arun',
    'MENAKA':                'Febin',
    'MUKKOM':                'Junais',
    'MUVATTUPUZHA':          'Febin',
    'MUVATTUPUZHA FUTURE':   'Febin',
    'NADAKKAVU FUTURE':      'Junais',
    'NAGAMPADAM':            'Arun',
    'NARIKKUNI':             'Aswin',
    'NILAMBUR FUTURE':       'Siraj',
    'OTTAPALAM':             'Siraj',
    'OTTUPARA FUTURE':       'Febin',
    'P TOWER':               'Junais',
    'PALAKKAD FUTURE':       'Siraj',
    'PALARIVATTOM':          'Arun',
    'PANAVILA FUTURE':       'Ashik',
    'PARAVOOR':              'Febin',
    'PATHANAMTHITTA FUTURE': 'Arun',
    'PATTAMBI FUTURE':       'Siraj',
    'PATTOM':                'Ashik',
    'PAYYANNUR':             'Sreeju',
    'PAYYANNUR FUTURE':      'Sreeju',
    'PAZHAVANGADI':          'Ashik',
    'PERAMBRA':              'Aswin',
    'PERAMBRA FUTURE':       'Aswin',
    'PERINTHALMANNA FUTURE': 'Siraj',
    'PERUMBAVOOR FUTURE':    'Febin',
    'PIRAVOM FUTURE':        'Febin',
    'POOTHOLE FUTURE':       'Febin',
    'POTTAMMAL FUTURE':      'Junais',
    'PUNALUR FUTURE':        'Ashik',
    'PUTHIYARA':             'Junais',
    'RAMANATTUKARA':         'Junais',
    'RANNI FUTURE':          'Arun',
    'SABA':                  'Junais',
    'SABA MAHARANI':         'Junais',
    'SNPARK':                'Sreeju',
    'TALIPARAMBA':           'Sreeju',
    'TB ROAD':               'Siraj',
    'THALASSERY':            'Sreeju',
    'THALASSERY FUTURE':     'Sreeju',
    'THAMARASSERY FUTURE':   'Aswin',
    'THANA':                 'Sreeju',
    'THIRUNAKKARA':          'Arun',
    'THIRUNAKKARA FUTURE':   'Arun',
    'THIRUVALLA':            'Arun',
    'THIRUVALLA FUTURE':     'Arun',
    'THODUPUZHA':            'Febin',
    'THODUPUZHA FUTURE':     'Febin',
    'THONDAYAD FUTURE':      'Junais',
    'THOPPUMPADY FUTURE':    'Arun',
    'THRIPUNITHURA':         'Febin',
    'THRIPUNITHURA FUTURE':  'Febin',
    'TIRUR':                 'Siraj',
    'UPPALA':                'Sreeju',
    'VADAKARA':              'Aswin',
    'VADAKARA FUTURE':       'Aswin',
    'VADAKKENCHERRY FUTURE': 'Siraj',
    'VALANCHERY FUTURE':     'Siraj',
    'VENGARA':               'Siraj',
    'VENJARAMOODU FUTURE':   'Ashik',
    'VYPIN FUTURE':          'Febin',
    'WANDOOR':               'Siraj',
    'myG CARE BATHERY':      'Aswin',
    'myG CARE PERUMANNA':    'Junais',
}

# ─────────────────────────────────────────────────────────────────────────────
# Product → Category mapping  (casing matches reference exactly)
# ─────────────────────────────────────────────────────────────────────────────
PRODUCT_CATEGORY_MAP = {
    # CD Products
    'AIR CONDITIONER':  'CD PRODUCTS',
    'DISH WASHER':      'CD PRODUCTS',
    'DRYER':            'CD PRODUCTS',
    'FREEZER':          'CD PRODUCTS',
    'HOME APPLIANCES':  'CD PRODUCTS',
    'MICROWAVE OVEN':   'CD PRODUCTS',
    'REFRIGERATORS':    'CD PRODUCTS',
    'TV':               'CD PRODUCTS',
    'WASHING MACHINES': 'CD PRODUCTS',
    # HVA
    'AUDIO':        'HVA',
    'EAR WEARABLES':'HVA',
    'GAMING':       'HVA',
    'HOME THEATRE': 'HVA',
    'SMART WATCH':  'HVA',
    # Laptop
    'LAPTOP':   'LAPTOP',
    'DESKTOP':  'LAPTOP',
    # Mobile
    'MOBILE':  'MOBILE',
    'TABLET':  'MOBILE',
    # SA
    'SMALL APPLIANCES': 'SA',
    # Others
    'CAMERA':        'OTHERS',
    'DIY':           'OTHERS',
    'IT ACCESSORIES':'OTHERS',
    'PRINTER':       'OTHERS',
    'STABILIZER':    'OTHERS',
    'MYG VERSE':     'OTHERS',
    'RIG':           'OTHERS',
}

# ─────────────────────────────────────────────────────────────────────────────
# Blacklisted products → removed from output
# ─────────────────────────────────────────────────────────────────────────────
BLACKLIST_PRODUCTS = {
    'ACCESSORIES',
    'CARE PLUS',
    'CEGI',
    'CROCKERY',
    'D SPARE',
    'GLAMSHIELD',
    'HOUSE HOLD',
    'LAPTOP BAG',
    'LG AMC',
    'OSG WARRANTY',
    'PERSONAL CARE',
    'PROTECT MAX',
    'SERVICE',
    'SMART CHOICE',
    'STORAGE DEVICES',
}


def _normalize_columns(df):
    """
    Strip whitespace from all column names and attempt case-insensitive
    matching to map common variations to the exact names expected by
    clean_sales_report().  Returns the renamed DataFrame.
    """
    # Map of canonical name → list of acceptable aliases (all UPPER, stripped)
    COLUMN_ALIASES = {
        'Slno':               ['SLNO', 'SL NO', 'SL.NO', 'S.NO', 'SNO', 'SR NO', 'SR.NO', 'SERIAL NO', 'SERIAL NUMBER'],
        'Branch':             ['BRANCH', 'STORE', 'BRANCH NAME', 'STORE NAME', 'OUTLET', 'SHOP NAME'],
        'RBM':                ['RBM', 'REGIONAL BUSINESS MANAGER', 'REGIONAL BDM', 'REGION MANAGER'],
        'BDM':                ['BDM', 'BUSINESS DEVELOPMENT MANAGER', 'BD MANAGER'],
        'Date':               ['DATE', 'SALE DATE', 'INVOICE DATE', 'TRANSACTION DATE', 'BILL DATE'],
        'Time':               ['TIME', 'MONTH', 'PERIOD', 'WEEK'],
        'Invoice Number':     ['INVOICE NUMBER', 'INVOICE NO', 'INVOICE', 'BILL NUMBER', 'BILL NO'],
        'Customer Name':      ['CUSTOMER NAME', 'CUST NAME', 'CLIENT NAME', 'BUYER NAME', 'CUSTOMER'],
        'Customer Mobile':    ['CUSTOMER MOBILE', 'MOBILE', 'MOBILE NUMBER', 'PHONE', 'PHONE NUMBER', 'CONTACT NUMBER', 'CUST MOBILE'],
        'Pincode':            ['PINCODE', 'PIN CODE', 'ZIP', 'POSTAL CODE'],
        'Staff':              ['STAFF', 'STAFF NAME', 'SALESPERSON', 'SALES PERSON', 'EMPLOYEE', 'SALES STAFF'],
        'Product':            ['PRODUCT', 'PRODUCT NAME', 'PRODUCT TYPE', 'ITEM TYPE', 'CATEGORY NAME'],
        'Brand':              ['BRAND', 'BRAND NAME', 'MAKE', 'MANUFACTURER'],
        'Item Group':         ['ITEM GROUP', 'ITEM GRP', 'GROUP', 'PRODUCT GROUP'],
        'Item Category':      ['ITEM CATEGORY', 'ITEM CAT', 'SUB CATEGORY', 'SUBCATEGORY'],
        'Item Code':          ['ITEM CODE', 'PRODUCT CODE', 'SKU', 'MODEL CODE', 'CODE'],
        'Item Name':          ['ITEM NAME', 'PRODUCT DESCRIPTION', 'MODEL NAME', 'DESCRIPTION', 'ITEM DESCRIPTION'],
        'IMEI':               ['IMEI', 'IMEI NUMBER', 'IMEI NO', 'SERIAL', 'SERIAL NO'],
        'QTY':                ['QTY', 'QUANTITY', 'UNITS', 'NOS', 'COUNT', 'NO OF UNITS'],
        'Financier':          ['FINANCIER', 'LENDER', 'FINANCE COMPANY', 'FINANCER', 'FINANCIER NAME'],
        'Delivery Order No':  ['DELIVERY ORDER NO', 'DO NO', 'DO NUMBER', 'DELIVERY NO', 'DELIVERY ORDER', 'DO'],
        'MOP':                ['MOP', 'MODE OF PAYMENT', 'PAYMENT MODE', 'PAY MODE'],
        'Sold Price':         ['SOLD PRICE', 'SELLING PRICE', 'SALE PRICE', 'PRICE', 'AMOUNT', 'NET AMOUNT', 'SELL PRICE'],
        'EMI':                ['EMI', 'EMI AMOUNT', 'MONTHLY INSTALMENT', 'MONTHLY INSTALLMENT'],
    }

    # Build lookup: UPPER stripped alias → canonical name
    alias_lookup = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_lookup[alias.upper().strip()] = canonical

    # Strip whitespace from all actual columns
    stripped = {col: col.strip() for col in df.columns}
    df.rename(columns=stripped, inplace=True)

    # Build rename map for actual columns
    rename_map = {}
    for actual_col in list(df.columns):
        key = actual_col.upper().strip()
        if key in alias_lookup:
            canonical = alias_lookup[key]
            if actual_col != canonical:
                rename_map[actual_col] = canonical

    if rename_map:
        print(f"  Column rename map applied: {rename_map}")
        df.rename(columns=rename_map, inplace=True)

    # Drop the raw 'Finance' input column if it exists alongside 'Financier'
    # (the file has both; 'Finance' is a pre-computed flag we recalculate ourselves)
    if 'Finance' in df.columns and 'Financier' in df.columns:
        df.drop(columns=['Finance'], inplace=True)
        print("  Dropped raw 'Finance' column (will be recomputed from Financier)")

    # Deduplicate any remaining duplicate columns (keep first occurrence)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
        print("  Deduplicated columns (kept first occurrence of each)")

    return df


def _find_header_row(input_path):
    """
    Scan the first 20 rows of the Excel file to find the actual header row.
    The Combined Sales Report export includes several metadata rows (title,
    blank rows, 'Taken By', 'Action Date') before the real column headers.
    Returns the 0-based row index of the header row.
    """
    RAW_SENTINEL_COLS = {'RBM', 'SLNO', 'SL NO', 'BRANCH', 'SOLD PRICE', 'FINANCIER', 'INVOICE NUMBER'}
    probe = pd.read_excel(input_path, header=None, nrows=20, engine='calamine')
    for row_idx, row in probe.iterrows():
        row_vals = {str(v).strip().upper() for v in row.values if pd.notna(v) and str(v).strip()}
        if row_vals & RAW_SENTINEL_COLS:
            print(f"  Auto-detected header row at index {row_idx}")
            return row_idx
    return 0  # fallback: assume first row is header


def clean_sales_report(input_path, output_path):
    print(f"Reading input sales report: {input_path}")

    # ── Auto-detect header row (handles metadata rows before actual headers) ──
    header_row = _find_header_row(input_path)
    df = pd.read_excel(input_path, header=header_row, engine='calamine')
    print(f"Original shape: {df.shape}")
    print(f"Detected columns: {list(df.columns)}")

    # ── Auto-normalize column names (case-insensitive, strip whitespace) ──────
    df = _normalize_columns(df)
    print(f"Normalized columns: {list(df.columns)}")

    # ── Validate required columns exist ───────────────────────────────────────
    REQUIRED_COLUMNS = ['RBM', 'Sold Price', 'Financier', 'Product', 'Branch']
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(
            f"Required column(s) not found after normalization: {missing}. "
            f"Detected columns in your file: {list(df.columns)}"
        )

    # ── 1. Remove RBM = GENERAL and RAJESH ───────────────────────────────────
    remove_rbm = {'GENERAL', 'RAJESH'}
    rbm_clean = df['RBM'].astype(str).str.strip().str.upper()
    df = df[~rbm_clean.isin(remove_rbm)]
    print(f"Shape after RBM filter (remove GENERAL/RAJESH): {df.shape}")

    # ── 2. Price filter: keep > 3000 OR significant returns (< -3000) ────────
    price = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0.0)
    df = df[(price > 3000) | (price < -3000)]
    print(f"Shape after price filter (>3000 or <-3000): {df.shape}")

    # ── 3. Remove E-commerce entries ─────────────────────────────────────────
    financier = df['Financier'].astype(str).str.strip().str.upper()
    ecommerce_mask = financier.str.contains('ECOMMERCE|E-COMMERCE', na=False)
    df = df[~ecommerce_mask]
    print(f"Shape after E-commerce filter: {df.shape}")

    # ── 4. Standardise Financier ──────────────────────────────────────────────
    financier = df['Financier'].astype(str).str.strip().str.upper()
    res_fin = pd.Series('aCash', index=financier.index)
    res_fin = np.where(financier.str.contains('BAJAJ', na=False), 'BAJAJ FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('HDB', na=False), 'HDB FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('DMI', na=False), 'DMI FINANCE', res_fin)
    res_fin = np.where(financier.str.contains('IDFC', na=False), 'IDFC FIRST BANK LTD', res_fin)
    res_fin = np.where(financier.str.contains('TVS', na=False), 'TVS Credit', res_fin)
    res_fin = np.where(financier.str.contains('POONAWALLA|PFL', na=False), 'Poonawalla Fincorp', res_fin)
    
    cash_pattern = 'BENOW|PINELAB|PAYTM|DELHIVERY|OFFICIUM|COCHIN|CASH|ACASH|CO-OP|COOP|SOCIETY|THAYANOOR'
    res_fin = np.where(financier.str.contains(cash_pattern, na=False), 'aCash', res_fin)
    res_fin = np.where(financier.isin(['', 'NAN', 'NONE']), 'aCash', res_fin)
    df['Financier_Cleaned'] = res_fin

    # ── 5. Finance column ─────────────────────────────────────────────────────
    df['Finance_Cleaned'] = np.where(df['Financier_Cleaned'] == 'aCash', 'aCash', 'FIN')

    # ── 6. Category mapping & blacklist removal ───────────────────────────────
    prod = df['Product'].astype(str).str.strip().str.upper()
    prod_clean = prod.str.replace(' ', '', regex=False)
    
    res_cat = pd.Series('OTHERS', index=prod.index)
    res_cat = np.where(prod.str.contains('SMALL APPLIANCE', na=False, regex=False), 'SA', res_cat)
    res_cat = np.where(prod.str.contains('TABLET|TAB', na=False), 'MOBILE', res_cat)
    
    bl_clean_list = [bl.replace(' ', '') for bl in BLACKLIST_PRODUCTS]
    res_cat = np.where(prod_clean.isin(bl_clean_list), 'REMOVE', res_cat)
    res_cat = np.where(prod.isin(BLACKLIST_PRODUCTS), 'REMOVE', res_cat)
    
    mapped_cat = prod.map(PRODUCT_CATEGORY_MAP)
    res_cat = np.where(mapped_cat.notna(), mapped_cat, res_cat)
    
    df['Category_Cleaned'] = res_cat
    df = df[df['Category_Cleaned'] != 'REMOVE']
    print(f"Shape after category removal: {df.shape}")

    # ── 7. Zone classification ─────────────────────────────────────────────────
    rbm_clean = df['RBM'].astype(str).str.strip().str.upper()
    res_zone = pd.Series('UNKNOWN', index=rbm_clean.index)
    res_zone = np.where(rbm_clean.isin(['ALFAS', 'SAMEER', 'SAMEEM']), 'NORTH', res_zone)
    res_zone = np.where(rbm_clean.isin(['RENJITH', 'MAHESH', 'VAISAKH']), 'SOUTH', res_zone)
    res_zone = np.where(rbm_clean.str.contains('KARNATAKA', na=False), 'KARNATAKA', res_zone)
    df['Zone_Cleaned'] = res_zone

    # ── 8. Price Range classification ─────────────────────────────────────────
    price = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0.0)
    bins = [-np.inf, 0, 10000, 15000, 20000, 30000, 40000, 50000, 75000, 100000, 150000, 200000, np.inf]
    labels = ['Return', 'Below 10K', '10K to 15K', '15K to 20K', '20K to 30K', '30K to 40K', '40K to 50K', '50K to 75K', '75K to 100K', '100K to 150K', '150K to 200K', 'Above 200K']
    df['Price_Range_Cleaned'] = pd.cut(price, bins=bins, labels=labels, right=False).astype(str)

    # ── 9. Store → Fin BDM lookup ─────────────────────────────────────────────
    store = df['Branch'].astype(str).str.strip().str.upper()
    res_bdm = store.map(STORE_FIN_BDM_MAP)
    
    def fuzzy_bdm(s):
        for k, v in STORE_FIN_BDM_MAP.items():
            if s == k or s in k or k in s: return v
        return ''
    
    missing_bdm = res_bdm.isna()
    if missing_bdm.any():
        res_bdm[missing_bdm] = store[missing_bdm].apply(fuzzy_bdm)
    df['Fin_BDM_Cleaned'] = res_bdm.fillna('')

    # ── 10. Clean Delivery Order No ───────────────────────────────────────────
    do = df['Delivery Order No'].astype(str).str.strip().str.upper()
    do = do.str.replace(' ', '', regex=False)
    do = np.where(do.isin(['', 'NA', 'N/A', '-', '0', '0.0', 'NULL', 'NAN', 'NONE']), '', do)
    df['DO_Cleaned'] = do

    # ── 10a. Delivery Order Overrides ─────────────────────────────────────────
    do_clean = df['DO_Cleaned']

    # BAJAJ FINANCE: all DO numbers starting with B followed by digits (B24, B42, B43, B44, B45, etc.)
    # Using regex: starts with B then a digit
    import re as _re
    bajaj_mask = do_clean.str.match(r'^B\d', na=False)
    df.loc[bajaj_mask, 'Financier_Cleaned'] = 'BAJAJ FINANCE'
    df.loc[bajaj_mask, 'Finance_Cleaned']   = 'FIN'
    bajaj_overridden = bajaj_mask.sum()
    if bajaj_overridden:
        print(f"  BAJAJ DO override applied: {bajaj_overridden} rows set to BAJAJ FINANCE")

    # DMI FINANCE: DO numbers that contain 'DMI' anywhere (handles 0DMI..., DMI..., etc.)
    dmi_mask = do_clean.str.contains('DMI', na=False, regex=False)
    df.loc[dmi_mask, 'Financier_Cleaned'] = 'DMI FINANCE'
    df.loc[dmi_mask, 'Finance_Cleaned']   = 'FIN'
    dmi_overridden = dmi_mask.sum()
    if dmi_overridden:
        print(f"  DMI DO override applied: {dmi_overridden} rows set to DMI FINANCE")

    # IDFC FIRST BANK: DO starts with 10108
    idfc_mask = do_clean.str.startswith('10108', na=False)
    df.loc[idfc_mask, 'Financier_Cleaned'] = 'IDFC FIRST BANK LTD'
    df.loc[idfc_mask, 'Finance_Cleaned']   = 'FIN'
    idfc_overridden = idfc_mask.sum()
    if idfc_overridden:
        print(f"  IDFC DO override applied: {idfc_overridden} rows set to IDFC FIRST BANK LTD")

    # POONAWALLA FINCORP: DO starts with CDSK
    poonawalla_mask = do_clean.str.startswith('CDSK', na=False)
    df.loc[poonawalla_mask, 'Financier_Cleaned'] = 'Poonawalla Fincorp'
    df.loc[poonawalla_mask, 'Finance_Cleaned']   = 'FIN'
    poonawalla_overridden = poonawalla_mask.sum()
    if poonawalla_overridden:
        print(f"  Poonawalla DO override applied: {poonawalla_overridden} rows set to Poonawalla Fincorp")

    # TVS CREDIT: DO starts with 3022CD or 3023CD (or any 30xxCD pattern)
    tvs_mask = do_clean.str.startswith(('3022CD', '3023CD'), na=False)
    df.loc[tvs_mask, 'Financier_Cleaned'] = 'TVS Credit'
    df.loc[tvs_mask, 'Finance_Cleaned']   = 'FIN'
    tvs_overridden = tvs_mask.sum()
    if tvs_overridden:
        print(f"  TVS DO override applied: {tvs_overridden} rows set to TVS Credit")

    # HDB FINANCE: long numeric DO numbers (8+ digits, all numeric)
    # These are DOs that start with a digit but didn't match IDFC (10108) prefix.
    # Any DO number that is purely numeric and not caught by IDFC → HDB FINANCE.
    hdb_mask = (
        do_clean.str.match(r'^\d+$', na=False)   # purely numeric
        & ~idfc_mask                              # not already mapped to IDFC
        & (do_clean.str.len() >= 8)              # long enough to be a real finance DO
    )
    df.loc[hdb_mask, 'Financier_Cleaned'] = 'HDB FINANCE'
    df.loc[hdb_mask, 'Finance_Cleaned']   = 'FIN'
    hdb_overridden = hdb_mask.sum()
    if hdb_overridden:
        print(f"  HDB DO override applied: {hdb_overridden} rows set to HDB FINANCE")

    print(f"Final output shape will be: ({len(df)}, 30)")

    out_df = pd.DataFrame()
    out_df['Slno']              = df['Slno']
    out_df['STore']             = df['Branch']
    out_df['Fin BDM']           = df['Fin_BDM_Cleaned']
    out_df['RBM']               = df['RBM'].apply(lambda x: str(x).strip() if pd.notna(x) else x)
    out_df['BDM']               = df['BDM']
    out_df['Date']              = df['Date']
    out_df['Month']             = df['Time']       
    out_df['Invoice Number']    = df['Invoice Number']
    out_df['Customer Name']     = df['Customer Name']
    out_df['Customer Mobile']   = df['Customer Mobile']
    out_df['Pincode']           = df['Pincode']
    out_df['Staff']             = df['Staff']
    out_df['Product']           = df['Product']
    out_df['Category']          = df['Category_Cleaned']
    out_df['Brand']             = df['Brand']
    out_df['Item Group']        = df['Item Group']
    out_df['Item Category']     = df['Item Category']
    out_df['Item Code']         = df['Item Code']
    out_df['Item Name']         = df['Item Name']
    out_df['IMEI']              = df['IMEI']
    out_df['QTY']               = df['QTY']
    out_df['Financier']         = df['Financier_Cleaned']
    out_df['Finance']           = df['Finance_Cleaned']
    out_df['Delivery Order No'] = df['DO_Cleaned']
    out_df['MOP']               = df['MOP']
    out_df['Sold Price']        = df['Sold Price']
    out_df['EMI']               = df['EMI']
    out_df['Price Range']       = df['Price_Range_Cleaned']
    out_df['Price']             = df['Sold Price']   
    out_df['Zone']              = df['Zone_Cleaned']

    # ── 12. Write plain Excel – Sheet1 (no template dependency) ──────────────
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        out_df.to_excel(writer, sheet_name='Sheet1', index=False)

    print(f"Cleaned report written to: {output_path} ({len(out_df)} rows, Sheet1)")


if __name__ == "__main__":
    clean_sales_report(
        r"c:\Users\rahul_myg\Downloads\21-07-2026_10_10_39_569187_CombinedSalesReport.xlsx",
        r"c:\Users\rahul_myg\Downloads\FINANCE PORTAL\data\test_cleaned.xlsx"
    )
