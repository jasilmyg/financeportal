import pandas as pd
import numpy as np
import os
from datetime import datetime
from sales_cleaner import _normalize_columns, PRODUCT_CATEGORY_MAP

CARD_FINS = ['PINE LAB EMI', 'PINELAB', 'PINE LAB', 'PAYTM', 'BENOW'] # Upper case for matching


def _unuploaded_card_raw_names(target_date_str, timeframe, end_date_str=None):
    """(uppercase_raw_names, std_keys) for card financiers NOT uploaded in emi_state for this period."""
    import emi_state
    rng_start, rng_end = emi_state.range_of(target_date_str, timeframe, end_date_str)
    raw_names = set()
    std_keys = set()
    for std_k, emi_k, aliases in (
        ('PineLab', emi_state.PINE_FIN, ('PINE LAB EMI', 'PINELAB', 'PINE LAB')),
        ('PAYTM',   emi_state.PAYTM_FIN, ('PAYTM', 'PAYTM EMI')),
        ('Benow',   emi_state.BENOW_FIN, ('BENOW', 'BENOW FINANCE')),
    ):
        if not emi_state.overlapping_spans(emi_k, rng_start, rng_end):
            raw_names.update(aliases)
            std_keys.add(std_k)
    return raw_names, std_keys

_cached_df = None
_cached_mtime = 0

def _prepare_finance_flags(frame, unup_raw):
    """Standardize numeric/Financier columns and compute Is_Finance/Is_Card/Fin_* flags in place."""
    for col in ['Sold Price', 'QTY']:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors='coerce').fillna(0)
    if 'Financier' in frame.columns:
        frame['Financier'] = frame['Financier'].fillna('').astype(str).str.strip().str.upper()
    else:
        frame['Financier'] = ''
    if unup_raw:
        frame.loc[frame['Financier'].isin(unup_raw), 'Financier'] = 'ACASH'
    frame['Is_Finance'] = (frame['Financier'] != '') & (frame['Financier'] != 'NAN') & (frame['Financier'].str.lower() != 'acash')
    frame['Is_Card'] = frame['Financier'].isin(CARD_FINS)
    frame['Fin_QTY'] = np.where(frame['Is_Finance'], frame['QTY'], 0)
    frame['Fin_Value'] = np.where(frame['Is_Finance'], frame['Sold Price'], 0.0)
    return frame


def get_dashboard_data(raw_file_path, target_date_str, timeframe, rbm='', bdm='', store='', staff='', zone_map=None, df=None, end_date_str=None, pine_lab_override=None, paytm_override=None, benow_override=None, state='', district='', pine_lab_val_override=None, paytm_val_override=None, benow_val_override=None, ch_rollups=None, card_prod_qty_override=None, card_prod_val_override=None):
    global _cached_df, _cached_mtime
    try:
        _unup_raw, _unup_std = _unuploaded_card_raw_names(target_date_str, timeframe, end_date_str)
        if 'PineLab' in _unup_std:
            pine_lab_override = None
            pine_lab_val_override = None
        if 'PAYTM' in _unup_std:
            paytm_override = None
            paytm_val_override = None
        if 'Benow' in _unup_std:
            benow_override = None
            benow_val_override = None
        _prod_q_ov = {k: v for k, v in (card_prod_qty_override or {}).items() if k not in _unup_std} if card_prod_qty_override else {}
        _prod_v_ov = {k: v for k, v in (card_prod_val_override or {}).items() if k not in _unup_std} if card_prod_val_override else {}

        if ch_rollups is not None:
            df_cat_in, df_brand_in, df_hier_in = ch_rollups
            if df_cat_in is None or df_cat_in.empty:
                return {"error": "No data found for the selected timeframe"}
            df_filtered = _prepare_finance_flags(df_cat_in.copy(), _unup_raw)
            df_brand_src = _prepare_finance_flags(df_brand_in.copy(), _unup_raw) if (df_brand_in is not None and not df_brand_in.empty) else pd.DataFrame()
            df_hier_src = _prepare_finance_flags(df_hier_in.copy(), _unup_raw) if (df_hier_in is not None and not df_hier_in.empty) else pd.DataFrame()
        else:
            # 1. Read data (with caching) — or use a pre-loaded DataFrame (e.g. from ClickHouse)
            if df is not None:
                # Caller supplied the DataFrame directly; skip file I/O
                pass
            else:
                current_mtime = os.path.getmtime(raw_file_path) if os.path.exists(raw_file_path) else 0
                if _cached_df is not None and _cached_mtime == current_mtime:
                    df = _cached_df.copy()
                else:
                    df = pd.read_excel(raw_file_path, engine='calamine')
                    df = _normalize_columns(df)
                    if 'Date' in df.columns:
                        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
                        df = df.dropna(subset=['Date'])
                    _cached_df = df.copy()
                    _cached_mtime = current_mtime

            # 3. Handle Dates
            if 'Date' not in df.columns:
                return {"error": "Date column not found in data"}

            target_date = pd.to_datetime(target_date_str)

            # 4. Filter by timeframe
            if timeframe == 'daily':
                mask = (df['Date'].dt.date == target_date.date())
            elif timeframe == 'monthly':
                mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.month == target_date.month)
            elif timeframe == 'quarterly':
                mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.quarter == target_date.quarter)
            elif timeframe == 'custom':
                end_date = pd.to_datetime(end_date_str) if end_date_str else target_date
                mask = (df['Date'].dt.date >= target_date.date()) & (df['Date'].dt.date <= end_date.date())
            else:
                mask = pd.Series(True, index=df.index) # all data fallback

            df_filtered = df[mask].copy()

            if df_filtered.empty:
                return {"error": "No data found for the selected timeframe"}

            if 'Category' not in df_filtered.columns and 'Product' in df_filtered.columns:
                # Try to map product to category if missing
                df_filtered['Product_upper'] = df_filtered['Product'].fillna('').astype(str).str.strip().str.upper()
                df_filtered['Category'] = df_filtered['Product_upper'].map(PRODUCT_CATEGORY_MAP).fillna('BANK EMI')

            df_filtered = _prepare_finance_flags(df_filtered, _unup_raw)
            df_brand_src = None
            df_hier_src = df_filtered
        
        # --- CALCULATE KPIs ---
        total_value = df_filtered['Sold Price'].sum()
        total_qty = df_filtered['QTY'].sum()
        
        fin_df = df_filtered[df_filtered['Is_Finance']]
        fin_value = fin_df['Sold Price'].sum()
        fin_qty = fin_df['QTY'].sum()
        
        card_df = df_filtered[df_filtered['Is_Card']]
        card_qty = card_df['QTY'].sum()
        
        card_value = card_df['Sold Price'].sum()
        
        kpis = {
            "total_value": float(total_value),
            "total_volume": int(total_qty),
            "finance_penetration_value_pct": float((fin_value / total_value * 100) if total_value > 0 else 0),
            "finance_penetration_volume_pct": float((fin_qty / total_qty * 100) if total_qty > 0 else 0),
            "card_penetration_volume_pct": float((card_qty / total_qty * 100) if total_qty > 0 else 0),
            "fin_value_cr": float(fin_value / 10000000),
            "card_value_cr": float(card_value / 10000000)
        }

        # --- CATEGORY & BRAND PERFORMANCE ---
        # Apply filters just for category performance
        df_cat = df_filtered.copy()
        
        for col in ['RBM', 'BDM', 'Branch', 'Staff', 'State', 'District']:
            if col not in df_cat.columns:
                if col == 'Staff' and ch_rollups is not None:
                    continue
                df_cat[col] = 'Unknown'
            else:
                df_cat[col] = df_cat[col].fillna('Unknown').astype(str)
                
        if rbm:   df_cat = df_cat[df_cat['RBM'].str.upper().str.strip()   == rbm.upper().strip()]
        if bdm:   df_cat = df_cat[df_cat['BDM'].str.upper().str.strip()   == bdm.upper().strip()]
        if store: df_cat = df_cat[df_cat['Branch'].str.upper().str.strip() == store.upper().strip()]
        if staff and 'Staff' in df_cat.columns:
            df_cat = df_cat[df_cat['Staff'].str.upper().str.strip() == staff.upper().strip()]
        if state:
            _sts = [x.strip().upper() for x in str(state).split(',') if x.strip()]
            if _sts: df_cat = df_cat[df_cat['State'].str.upper().str.strip().isin(_sts)]
        if district: df_cat = df_cat[df_cat['District'].str.upper().str.strip() == district.upper().strip()]

        # Map Financier column to standard names
        fin_map = {
            'BAJAJ FINANCE': 'BAJAJ', 'HDB FINANCE': 'HDB', 'IDFC FIRST BANK LTD': 'IDFC', 
            'POONAWALLA FINCORP': 'PFL', 'TVS CREDIT': 'TVS', 'DMI FINANCE': 'DMI', 
            'PINE LAB EMI': 'PineLab', 'PINELAB': 'PineLab', 'PAYTM': 'PAYTM', 
            'BENOW': 'Benow', 'ACASH': 'aCASH'
        }
        # Critical: force plain Python str dtype (not Pandas StringArray) to ensure correct pivot matching
        df_cat['Category'] = df_cat['Category'].astype(object).fillna('BANK EMI').apply(str).str.strip().str.upper()
        df_cat['Category'] = df_cat['Category'].replace({'': 'BANK EMI', 'NAN': 'BANK EMI'})
        df_cat['Financier'] = df_cat['Financier'].astype(object).fillna('').apply(str).str.strip().str.upper()
        df_cat['Fin_Mapped'] = df_cat['Financier'].map(fin_map).fillna(df_cat['Financier'])
        
        standardFins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow", "aCASH"]
        standardCats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]
        
        def _empty_perf():
            return ({cat: {f: 0 for f in standardFins} for cat in standardCats},
                    {cat: {f: 0.0 for f in standardFins} for cat in standardCats})

        def _fill_perf(qty, val, grouped_rows):
            # grouped_rows: iterable of (cat, fin, qty_sum, price_sum)
            for cat, fin, q, p in grouped_rows:
                if cat in qty and fin in qty[cat]:
                    qty[cat][fin] = int(q)
                    val[cat][fin] = float(p) / 10000000.0  # Crores

        def build_performance_dict(df_group):
            qty, val = _empty_perf()
            if not df_group.empty:
                g = df_group.groupby(['Category', 'Fin_Mapped'], sort=False)[['QTY', 'Sold Price']].sum()
                _fill_perf(qty, val, ((c, f, q, p) for (c, f), q, p in
                                      zip(g.index, g['QTY'].tolist(), g['Sold Price'].tolist())))
            return {"qty": qty, "value": val}
            
        cat_perf = build_performance_dict(df_cat)
        _pre_df_card = {
            std_cat: {
                f: int(cat_perf['qty'][std_cat].get(f, 0) or 0)
                for f in ('PineLab', 'PAYTM', 'Benow')
            }
            for std_cat in standardCats
        }
        _pre_df_card_val = {
            std_cat: {
                f: float(cat_perf['value'][std_cat].get(f, 0.0) or 0.0)
                for f in ('PineLab', 'PAYTM', 'Benow')
            }
            for std_cat in standardCats
        }
        _emi_val_applied = {}
        _emi_applied = {}        # fin_key -> {CATEGORY: qty from the uploaded file}
        _emi_prod_q_applied = {} # fin_key -> {CATEGORY: {SUB_PROD: qty}}
        _emi_prod_v_applied = {} # fin_key -> {CATEGORY: {SUB_PROD: val_cr}}

        def _apply_emi_override(fin_key, override, val_override=None):
            # The uploaded file is the source of truth for this financier: categories
            # that are absent from the file must be 0 (ERP-matched invoices can carry a
            # different category, e.g. a PAYTM txn matched to a Small Appliances invoice).
            file_cats = {str(k).strip().upper(): int(v) for k, v in override.items()}
            _emi_applied[fin_key] = file_cats
            if val_override is not None:
                _emi_val_applied[fin_key] = {str(k).strip().upper(): float(v) for k, v in (val_override or {}).items()}
            if fin_key in _prod_q_ov:
                _emi_prod_q_applied[fin_key] = {
                    str(c).strip().upper(): {str(p).strip().upper(): int(q) for p, q in (pmap or {}).items()}
                    for c, pmap in (_prod_q_ov.get(fin_key) or {}).items()
                }
            if fin_key in _prod_v_ov:
                _emi_prod_v_applied[fin_key] = {
                    str(c).strip().upper(): {str(p).strip().upper(): float(v) for p, v in (pmap or {}).items()}
                    for c, pmap in (_prod_v_ov.get(fin_key) or {}).items()
                }
            for std_cat in standardCats:
                if std_cat in file_cats:
                    cat_perf['qty'][std_cat][fin_key] = file_cats[std_cat]
                    if fin_key in _emi_val_applied:
                        cat_perf['value'][std_cat][fin_key] = _emi_val_applied[fin_key].get(std_cat, 0.0)
                else:
                    cat_perf['qty'][std_cat][fin_key] = 0
                    cat_perf['value'][std_cat]['aCASH'] = float(cat_perf['value'][std_cat].get('aCASH', 0.0) or 0.0) + (0.0 if val_override is not None else float(cat_perf['value'][std_cat].get(fin_key, 0.0) or 0.0))
                    cat_perf['value'][std_cat][fin_key] = 0.0

        def _patch_products_for_emi(products_qty, products_val):
            # Restore ERP-matched card rows back into each sub-product's aCASH, apply each
            # uploaded file's sub-product card EMI qty/value directly onto matching sub-product
            # rows (using 'BANK EMI' / 'UNMAPPED' only for any unallocated residual), and
            # deduct card EMI from each corresponding sub-product's aCASH first.
            if not _emi_applied:
                return
            for std_cat in standardCats:
                qrows = products_qty.get(std_cat)
                vrows = products_val.get(std_cat)
                old_cat_vals = {}
                for fin_key, file_cats in _emi_applied.items():
                    old_val = 0.0
                    if qrows:
                        for prod in list(qrows.keys()):
                            if prod != 'UNMAPPED':
                                qrows[prod]['aCASH'] = int(qrows[prod].get('aCASH', 0) or 0) + int(qrows[prod].get(fin_key, 0) or 0)
                            qrows[prod][fin_key] = 0
                    if vrows:
                        for prod in list(vrows.keys()):
                            if (fin_key in _emi_val_applied or not file_cats.get(std_cat, 0)) and prod != 'UNMAPPED':
                                vrows[prod]['aCASH'] = float(vrows[prod].get('aCASH', 0.0) or 0.0) + float(vrows[prod].get(fin_key, 0.0) or 0.0)
                            old_val += float(vrows[prod].get(fin_key, 0.0) or 0.0)
                            vrows[prod][fin_key] = 0.0
                    old_cat_vals[fin_key] = old_val

                if qrows and 'UNMAPPED' in qrows and not any(qrows['UNMAPPED'].get(f, 0) for f in standardFins):
                    del qrows['UNMAPPED']
                if vrows and 'UNMAPPED' in vrows and not any(vrows['UNMAPPED'].get(f, 0.0) for f in standardFins):
                    del vrows['UNMAPPED']

                for fin_key, file_cats in _emi_applied.items():
                    file_qty = int(file_cats.get(std_cat, 0) or 0)
                    target_val = (
                        float(_emi_val_applied[fin_key].get(std_cat, 0.0) or 0.0)
                        if fin_key in _emi_val_applied
                        else float(old_cat_vals.get(fin_key, 0.0) or 0.0)
                    )
                    if not file_qty and abs(target_val) < 1e-12:
                        continue

                    qrows = products_qty.setdefault(std_cat, {})
                    vrows = products_val.setdefault(std_cat, {})
                    existing_key_map = {str(k).strip().upper(): k for k in qrows.keys()}
                    existing_key_map.update({str(k).strip().upper(): k for k in vrows.keys()})

                    pq_map = _emi_prod_q_applied.get(fin_key, {}).get(std_cat, {})
                    pv_map = _emi_prod_v_applied.get(fin_key, {}).get(std_cat, {})
                    fallback_key = 'BANK EMI' if std_cat == 'BANK EMI' else 'UNMAPPED'

                    allocated_q = 0
                    allocated_v = 0.0
                    for sub_u in (set(pq_map.keys()) | set(pv_map.keys())):
                        sub_q_int = int(pq_map.get(sub_u, 0) or 0)
                        sub_v_flt = float(pv_map.get(sub_u, 0.0) or 0.0) if pv_map else (
                            target_val * (sub_q_int / file_qty) if file_qty > 0 else 0.0
                        )
                        if not sub_q_int and abs(sub_v_flt) < 1e-12:
                            continue
                        target_key = existing_key_map.get(sub_u, sub_u)
                        q_row = qrows.setdefault(target_key, {f: 0 for f in standardFins})
                        v_row = vrows.setdefault(target_key, {f: 0.0 for f in standardFins})
                        q_row[fin_key] = int(q_row.get(fin_key, 0) or 0) + sub_q_int
                        v_row[fin_key] = float(v_row.get(fin_key, 0.0) or 0.0) + sub_v_flt
                        allocated_q += sub_q_int
                        allocated_v += sub_v_flt

                    rem_q = file_qty - allocated_q
                    rem_v = target_val - allocated_v
                    if rem_q != 0 or abs(rem_v) > 1e-9:
                        q_row = qrows.setdefault(fallback_key, {f: 0 for f in standardFins})
                        v_row = vrows.setdefault(fallback_key, {f: 0.0 for f in standardFins})
                        q_row[fin_key] = int(q_row.get(fin_key, 0) or 0) + rem_q
                        v_row[fin_key] = float(v_row.get(fin_key, 0.0) or 0.0) + rem_v

                def _deduct_subprod_acash(rows_map, active_fins, cast, eps):
                    if not rows_map or not active_fins:
                        return
                    rem_cat = cast(0)
                    for row in rows_map.values():
                        card_amt = sum(cast(row.get(fk, 0) or 0) for fk in active_fins)
                        if abs(card_amt) <= eps:
                            continue
                        avail = max(cast(0), cast(row.get('aCASH', 0) or 0))
                        take = min(avail, card_amt)
                        row['aCASH'] = cast(row.get('aCASH', 0) or 0) - take
                        rem_cat += (card_amt - take)
                    if rem_cat > eps:
                        for prod in sorted(rows_map.keys(), key=lambda k: cast(rows_map[k].get('aCASH', 0) or 0), reverse=True):
                            if rem_cat <= eps:
                                break
                            if prod in ('UNMAPPED', 'BANK EMI'):
                                continue
                            avail = max(cast(0), cast(rows_map[prod].get('aCASH', 0) or 0))
                            take = min(avail, rem_cat)
                            rows_map[prod]['aCASH'] = cast(rows_map[prod].get('aCASH', 0) or 0) - take
                            rem_cat -= take
                        if rem_cat > eps:
                            sink_k = 'BANK EMI' if 'BANK EMI' in rows_map else ('UNMAPPED' if 'UNMAPPED' in rows_map else next(iter(rows_map)))
                            rows_map[sink_k]['aCASH'] = cast(rows_map[sink_k].get('aCASH', 0) or 0) - rem_cat

                _deduct_subprod_acash(qrows, _emi_applied.keys(), int, 0)
                if _emi_val_applied:
                    _deduct_subprod_acash(vrows, [fk for fk in _emi_val_applied if fk in _emi_applied], float, 1e-12)

        # ── Pine Lab direct override ──────────────────────────────────────────
        # Replace the ERP-matched PineLab counts with direct counts from the
        # Pine Lab file (all transactions, not just matched ones).
        # pine_lab_override = {erp_category: total_qty}
        if pine_lab_override is not None:
            _apply_emi_override('PineLab', pine_lab_override, pine_lab_val_override)

        # ── Paytm direct override ──────────────────────────────────────────
        if paytm_override is not None:
            _apply_emi_override('PAYTM', paytm_override, paytm_val_override)

        # ── Benow direct override ──────────────────────────────────────────
        if benow_override is not None:
            _apply_emi_override('Benow', benow_override, benow_val_override)

        # ── Deduct Paytm, Benow, and Pinelab data count from aCASH ────────────
        # Credit card EMI transactions are initially recorded under cash/aCash in ERP.
        # Deduct Card EMI counts (PineLab, PAYTM, Benow) from aCASH so they are not double-counted.
        for std_cat in standardCats:
            card_count = (cat_perf['qty'][std_cat].get('PineLab', 0) or 0) + \
                         (cat_perf['qty'][std_cat].get('PAYTM', 0) or 0) + \
                         (cat_perf['qty'][std_cat].get('Benow', 0) or 0)
            emi_delta = sum(
                int(cat_perf['qty'][std_cat].get(fk, 0) or 0) - _pre_df_card[std_cat][fk]
                for fk in ('PineLab', 'PAYTM', 'Benow')
                if fk in _emi_applied
            )
            # No clamp at 0: card-EMI units that have no ERP category (the BANK EMI row) are still
            # deducted from aCASH, so the Grand Total always equals the cleaned-report total.
            cat_perf['qty'][std_cat]['aCASH'] = int(cat_perf['qty'][std_cat].get('aCASH', 0) or 0) - emi_delta
            cat_perf['qty'][std_cat]['_emi_deducted'] = True
            if _emi_val_applied:
                emi_val_delta = sum(
                    float(cat_perf['value'][std_cat].get(fk, 0.0) or 0.0) - _pre_df_card_val[std_cat][fk]
                    for fk in ('PineLab', 'PAYTM', 'Benow')
                    if fk in _emi_val_applied
                )
                cat_perf['value'][std_cat]['aCASH'] = float(cat_perf['value'][std_cat].get('aCASH', 0.0) or 0.0) - emi_val_delta
                cat_perf['value'][std_cat]['_emi_deducted'] = True
        
        # --- PRODUCT-LEVEL BREAKDOWN (within each category) ---
        # Use 'Product' as the sub-product label (per user requirement)
        # Fallback to 'Item Group' or 'Item Category' if 'Product' column not found
        product_col = None
        for col_candidate in ['Product', 'Item Group', 'Item Category']:
            if col_candidate in df_cat.columns:
                product_col = col_candidate
                break
        
        products_qty = {}  # {category: {product: {fin: qty}}}
        products_val = {}  # {category: {product: {fin: value_cr}}}
        
        if product_col:
            df_cat[product_col] = df_cat[product_col].astype(object).fillna('UNKNOWN').apply(str).str.strip().str.upper()
            _pg = (df_cat[df_cat['Category'].isin(standardCats)]
                   .groupby(['Category', product_col, 'Fin_Mapped'])[['QTY', 'Sold Price']].sum().reset_index())
            _tmp_q, _tmp_v = {}, {}
            for cat_, prod_, fin_, q_, p_ in zip(_pg['Category'].astype(str), _pg[product_col].astype(str),
                                                 _pg['Fin_Mapped'].astype(str),
                                                 _pg['QTY'].tolist(), _pg['Sold Price'].tolist()):
                fq = _tmp_q.setdefault(cat_, {}).setdefault(prod_, {f: 0 for f in standardFins})
                fv = _tmp_v.setdefault(cat_, {}).setdefault(prod_, {f: 0.0 for f in standardFins})
                if fin_ in fq:
                    fq[fin_] += int(q_)
                    fv[fin_] += float(p_) / 10000000.0
            for cat in standardCats:          # keep the original category order
                if cat in _tmp_q:
                    products_qty[cat] = _tmp_q[cat]
                    products_val[cat] = _tmp_v[cat]
        
        _patch_products_for_emi(products_qty, products_val)
        cat_perf["products_qty"] = products_qty
        cat_perf["products_val"] = products_val
        _paper_fins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]
        _card_fins  = ["PineLab", "PAYTM", "Benow"]
        _fin_cols   = _paper_fins + _card_fins
        _val_tots   = {f: sum(float(cat_perf['value'][c].get(f, 0.0) or 0.0) for c in standardCats) for f in standardFins}
        _qty_tots   = {f: sum(int(cat_perf['qty'][c].get(f, 0) or 0) for c in standardCats) for f in standardFins}
        _paper_val_tot = sum(_val_tots[f] for f in _paper_fins)
        _card_val_tot  = sum(_val_tots[f] for f in _card_fins)
        _tot_fin_val   = _paper_val_tot + _card_val_tot
        cat_perf["sif"] = {
            **{f: (_val_tots[f] / _tot_fin_val * 100.0 if _tot_fin_val > 0 else 0.0) for f in _fin_cols},
            "paper_total": (_paper_val_tot / _tot_fin_val * 100.0 if _tot_fin_val > 0 else 0.0),
            "card_total":  (_card_val_tot  / _tot_fin_val * 100.0 if _tot_fin_val > 0 else 0.0),
        }
        cat_perf["asp"] = {
            f: ((_val_tots[f] / _qty_tots[f]) * 10000000.0 if _qty_tots[f] > 0 else 0.0)
            for f in _fin_cols
        }
        
        brands_performance = {}
        _df_b = df_brand_src if df_brand_src is not None else df_cat
        if not _df_b.empty and 'Brand' in _df_b.columns:
            _df_b['Brand'] = _df_b['Brand'].fillna('UNKNOWN').astype(str).str.strip().str.upper()
            _df_b['Brand'] = _df_b['Brand'].replace('', 'UNKNOWN')
            _df_b['Category'] = _df_b['Category'].astype(object).fillna('BANK EMI').apply(str).str.strip().str.upper()
            _df_b['Category'] = _df_b['Category'].replace({'': 'BANK EMI', 'NAN': 'BANK EMI'})
            _df_b['Financier'] = _df_b['Financier'].astype(object).fillna('').apply(str).str.strip().str.upper()
            _df_b['Fin_Mapped'] = _df_b['Financier'].map(fin_map).fillna(_df_b['Financier'])

            _bg = _df_b.groupby(['Brand', 'Category', 'Fin_Mapped'])[['QTY', 'Sold Price']].sum().reset_index()
            _brand_acc = {}
            for brand_ in sorted(_df_b['Brand'].unique().tolist()):
                _brand_acc[str(brand_)] = _empty_perf()
            for b_, c_, f_, q_, p_ in zip(_bg['Brand'].astype(str), _bg['Category'].astype(str),
                                          _bg['Fin_Mapped'].astype(str),
                                          _bg['QTY'].tolist(), _bg['Sold Price'].tolist()):
                bq, bv = _brand_acc[b_]
                _fill_perf(bq, bv, [(c_, f_, q_, p_)])
            for b_, (bq, bv) in _brand_acc.items():
                for std_cat in standardCats:
                    card_count = (bq[std_cat].get('PineLab', 0) or 0) + \
                                 (bq[std_cat].get('PAYTM', 0) or 0) + \
                                 (bq[std_cat].get('Benow', 0) or 0)
                    # bq[std_cat]['aCASH'] already excludes PineLab/PAYTM/Benow via Fin_Mapped partition
                    bq[std_cat]['_emi_deducted'] = True
                brands_performance[b_] = {"qty": bq, "value": bv}
        fin_vols = {}
        fin_grouped = fin_df.groupby('Financier')['QTY'].sum().to_dict()
        for fin, qty in fin_grouped.items():
            if str(fin).strip():
                fin_vols[str(fin)] = int(qty)
                
        # --- HIERARCHY PERFORMANCE ---
        # Group by RBM -> BDM -> Branch -> Staff
        # If columns don't exist, fill with 'Unknown'
        for col in ['RBM', 'BDM', 'Branch', 'Staff', 'Fin BDM']:
            if col not in df_hier_src.columns:
                df_hier_src[col] = 'Unknown'
            else:
                df_hier_src[col] = df_hier_src[col].fillna('').astype(str)
        # Treat 'Unknown' in non-mandatory cols as empty
        for col in ['RBM', 'BDM', 'Branch', 'Staff']:
            df_hier_src[col] = df_hier_src[col].replace('', 'Unknown')
                
        hierarchy = {}
        
        grouped_hierarchy = df_hier_src.groupby(['RBM', 'BDM', 'Branch', 'Staff']).agg(
            qty=('QTY', 'sum'),
            value=('Sold Price', 'sum'),
            fin_qty=('Fin_QTY', 'sum'),
            fin_value=('Fin_Value', 'sum'),
            fin_bdm=('Fin BDM', 'first')
        ).reset_index()

        # Build nested dict and roll up metrics in one pass
        for row in grouped_hierarchy.to_dict('records'):
            rbm, bdm, branch, staff = str(row['RBM']), str(row['BDM']), str(row['Branch']), str(row['Staff'])
            
            fin_bdm_val = str(row.get('fin_bdm', ''))

            if rbm not in hierarchy:
                hierarchy[rbm] = {"metrics": {"qty": 0, "value": 0.0, "fin_qty": 0, "fin_value": 0.0}, "bdms": {}}
            if bdm not in hierarchy[rbm]["bdms"]:
                hierarchy[rbm]["bdms"][bdm] = {"metrics": {"qty": 0, "value": 0.0, "fin_qty": 0, "fin_value": 0.0}, "branches": {}}
            if branch not in hierarchy[rbm]["bdms"][bdm]["branches"]:
                hierarchy[rbm]["bdms"][bdm]["branches"][branch] = {
                    "metrics": {"qty": 0, "value": 0.0, "fin_qty": 0, "fin_value": 0.0},
                    "staffs": {},
                    "fin_bdm": fin_bdm_val
                }
                
            staff_metrics = {
                "qty": int(row['qty']), "value": float(row['value']),
                "fin_qty": int(row['fin_qty']), "fin_value": float(row['fin_value'])
            }
            hierarchy[rbm]["bdms"][bdm]["branches"][branch]["staffs"][staff] = {"metrics": staff_metrics}
            
            # Roll up metrics to parents
            for level in [hierarchy[rbm], hierarchy[rbm]["bdms"][bdm], hierarchy[rbm]["bdms"][bdm]["branches"][branch]]:
                level["metrics"]["qty"] += staff_metrics["qty"]
                level["metrics"]["value"] += staff_metrics["value"]
                level["metrics"]["fin_qty"] += staff_metrics["fin_qty"]
                level["metrics"]["fin_value"] += staff_metrics["fin_value"]
                        
        # --- ZONE (RBM-level) FINANCIER BREAKDOWN ---
        standardFinsForZone = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"]
        paperFins = {"BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"}
        cardFins  = {"PineLab", "PAYTM", "Benow"}
        
        zone_performance = {}
        df_zone = df_filtered.copy()
        if state and 'State' in df_zone.columns:
            _z_sts = [x.strip().upper() for x in str(state).split(',') if x.strip()]
            if _z_sts:
                df_zone = df_zone[df_zone['State'].fillna('').astype(str).str.upper().str.strip().isin(_z_sts)]
        df_zone['RBM'] = df_zone['RBM'].fillna('Unknown').astype(str)
        df_zone['Category'] = df_zone['Category'].astype(object).fillna('BANK EMI').apply(str).str.strip().str.upper()
        df_zone['Category'] = df_zone['Category'].replace({'': 'BANK EMI', 'NAN': 'BANK EMI'})
        df_zone['Fin_Mapped'] = df_zone['Financier'].map(fin_map).fillna(df_zone['Financier'])
        df_zone['Fin_Mapped'] = df_zone['Fin_Mapped'].replace('ACASH', 'aCASH')
        
        # Exclude rows with blank/empty RBM (warehouses, HEAD OFFICE etc.) before zone grouping
        df_zone = df_zone[df_zone['RBM'].str.strip() != '']
        # Pre-aggregate once instead of re-filtering the frame per zone x category x financier
        def _sum_map(frame, keys):
            g = frame.groupby(keys)[['QTY', 'Sold Price']].sum()
            return dict(zip(g.index.tolist(), zip(g['QTY'].tolist(), g['Sold Price'].tolist())))

        _z_rbm = df_zone.groupby('RBM')[['QTY', 'Sold Price']].sum()
        _z_fin = _sum_map(df_zone, ['RBM', 'Fin_Mapped'])
        _z_cat = _sum_map(df_zone, ['RBM', 'Category', 'Fin_Mapped'])

        for rbm_name in _z_rbm.index:
            # Zone-total financier breakdown
            qty_row = {f: 0 for f in standardFinsForZone}
            val_row = {f: 0.0 for f in standardFinsForZone}
            for fin in standardFinsForZone:
                q_, p_ = _z_fin.get((rbm_name, fin), (0, 0.0))
                qty_row[fin] = int(q_)
                val_row[fin] = float(p_) / 10000000.0
            
            total_qty_z = int(_z_rbm.at[rbm_name, 'QTY'])
            paper_qty   = sum(qty_row[f] for f in paperFins if f in qty_row)
            card_qty_z  = sum(qty_row[f] for f in cardFins  if f in qty_row)
            acash_qty   = qty_row.get('aCASH', 0)
            fin_qty_z   = total_qty_z - acash_qty
            fin_pen_z   = round(fin_qty_z / total_qty_z * 100, 2) if total_qty_z > 0 else 0
            
            total_val_z = float(_z_rbm.at[rbm_name, 'Sold Price']) / 10000000.0
            paper_val   = sum(val_row[f] for f in paperFins if f in val_row)
            card_val_z  = sum(val_row[f] for f in cardFins  if f in val_row)
            acash_val   = val_row.get('aCASH', 0.0)
            fin_val_z   = total_val_z - acash_val
            fin_pen_val_z = round(fin_val_z / total_val_z * 100, 2) if total_val_z > 0 else 0
            
            # Per-category breakdown within this zone
            categories = {}
            for cat in standardCats:
                cqty = {f: 0 for f in standardFinsForZone}
                cval = {f: 0.0 for f in standardFinsForZone}
                for fin in standardFinsForZone:
                    q_, p_ = _z_cat.get((rbm_name, cat, fin), (0, 0.0))
                    cqty[fin] = int(q_)
                    cval[fin] = float(p_) / 10000000.0
                categories[cat] = {"qty": cqty, "val": cval}
            
            zone_performance[str(rbm_name)] = {
                "qty":  {**qty_row, "total": total_qty_z, "fin_pen_pct": fin_pen_z, "paper_total": paper_qty, "card_total": card_qty_z},
                "val":  {**val_row, "total": total_val_z, "fin_pen_pct": fin_pen_val_z, "paper_total": paper_val, "card_total": card_val_z},
                "categories": categories,
            }

        # --- MERGE RBM-level zone_performance into named zones (NORTH/SOUTH) ---
        if zone_map:
            merged_zones = {}
            zone_map_upper = {k.strip().upper(): v.strip().upper() for k, v in zone_map.items()}
            
            for rbm_name in zone_performance:
                zone_label = zone_map_upper.get(rbm_name.strip().upper(), 'OTHER')
                if zone_label not in merged_zones:
                    merged_zones[zone_label] = None
            
            for zone_label in merged_zones:
                rbms_in_zone = [r for r in zone_performance
                                if zone_map_upper.get(r.strip().upper(), 'OTHER') == zone_label]
                mfins = standardFinsForZone
                mqty = {f: 0 for f in mfins}
                mval = {f: 0.0 for f in mfins}
                mcats = {cat: {"qty": {f: 0 for f in mfins}, "val": {f: 0.0 for f in mfins}} for cat in standardCats}
                
                for rbm_name in rbms_in_zone:
                    rd = zone_performance[rbm_name]
                    for f in mfins:
                        mqty[f] += rd["qty"].get(f, 0)
                        mval[f] += rd["val"].get(f, 0.0)
                    for cat in standardCats:
                        if cat in rd.get("categories", {}):
                            for f in mfins:
                                mcats[cat]["qty"][f] += rd["categories"][cat]["qty"].get(f, 0)
                                mcats[cat]["val"][f] += rd["categories"][cat]["val"].get(f, 0.0)
                
                total_qty_m = sum(mqty.values())
                paper_qty_m = sum(mqty[f] for f in paperFins if f in mqty)
                card_qty_m  = sum(mqty[f] for f in cardFins  if f in mqty)
                acash_qty_m = mqty.get('aCASH', 0)
                fin_pen_m   = round((total_qty_m - acash_qty_m) / total_qty_m * 100, 2) if total_qty_m > 0 else 0
                
                total_val_m = sum(mval.values())
                paper_val_m = sum(mval[f] for f in paperFins if f in mval)
                card_val_m  = sum(mval[f] for f in cardFins  if f in mval)
                acash_val_m = mval.get('aCASH', 0.0)
                fin_pen_val_m = round((total_val_m - acash_val_m) / total_val_m * 100, 2) if total_val_m > 0 else 0
                
                merged_zones[zone_label] = {
                    "qty": {**mqty, "total": total_qty_m, "fin_pen_pct": fin_pen_m, "paper_total": paper_qty_m, "card_total": card_qty_m},
                    "val": {**mval, "total": total_val_m, "fin_pen_pct": fin_pen_val_m, "paper_total": paper_val_m, "card_total": card_val_m},
                    "categories": mcats,
                }
            zone_performance = merged_zones
        
        return {
            "status": "success",
            "kpis": kpis,
            "category_performance": cat_perf,
            "brand_performance": brands_performance,
            "hierarchy": hierarchy,
            "zone_performance": zone_performance,
            "states": sorted([s for s in df_cat['State'].unique() if str(s).strip().upper() != 'UNKNOWN']) if 'State' in df_cat.columns else [],
            "districts": sorted([d for d in df_cat['District'].unique() if str(d).strip() and str(d).strip().upper() != 'UNKNOWN']) if 'District' in df_cat.columns else [],
            "district_states": {str(r['District']): str(r['State']) for r in df_cat[['District', 'State']].drop_duplicates().to_dict('records') if str(r['District']).strip() and str(r['District']).strip().upper() != 'UNKNOWN'} if 'District' in df_cat.columns else {}
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

# Price range bucket definitions
PRICE_RANGES = {
    "Below 10K":    (0,      10000),
    "10K to 15K":   (10000,  15000),
    "15K to 20K":   (15000,  20000),
    "20K to 30K":   (20000,  30000),
    "30K to 40K":   (30000,  40000),
    "40K to 50K":   (40000,  50000),
    "50K to 75K":   (50000,  75000),
    "75K to 100K":  (75000,  100000),
    "100K to 150K": (100000, 150000),
    "Above 150K":   (150000, float("inf")),
}

def get_price_range_data(raw_file_path, target_date_str, timeframe, price_range="ALL", df=None, end_date_str=None, state=""):
    global _cached_df, _cached_mtime
    try:
        # Load & cache — or use a pre-loaded DataFrame (e.g. from ClickHouse)
        if df is not None:
            pass  # caller supplied DataFrame directly
        else:
            current_mtime = os.path.getmtime(raw_file_path) if os.path.exists(raw_file_path) else 0
            if _cached_df is not None and _cached_mtime == current_mtime:
                df = _cached_df.copy()
            else:
                df = pd.read_excel(raw_file_path, engine='calamine')
                df = _normalize_columns(df)
                if 'Date' in df.columns:
                    df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
                    df = df.dropna(subset=['Date'])
                _cached_df = df.copy()
                _cached_mtime = current_mtime

        if 'Date' not in df.columns:
            return {"error": "Date column not found"}

        target_date = pd.to_datetime(target_date_str)
        end_date = pd.to_datetime(end_date_str) if end_date_str else target_date

        # Timeframe filter
        if timeframe == 'daily':
            mask = df['Date'].dt.date == target_date.date()
        elif timeframe == 'monthly':
            mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.month == target_date.month)
        elif timeframe == 'quarterly':
            mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.quarter == target_date.quarter)
        elif timeframe == 'custom':
            mask = (df['Date'].dt.date >= target_date.date()) & (df['Date'].dt.date <= end_date.date())
        else:
            mask = pd.Series(True, index=df.index)

        df_f = df[mask].copy()
        if df_f.empty:
            return {"error": "No data found for the selected timeframe"}

        if state and 'State' in df_f.columns:
            _sts = [x.strip().upper() for x in str(state).split(',') if x.strip()]
            if _sts:
                df_f = df_f[df_f['State'].fillna('').astype(str).str.upper().str.strip().isin(_sts)]
                if df_f.empty:
                    return {"qty": {}, "val": {}, "all_ranges": {}, "status": "success", "price_range": price_range}

        # Numeric
        for col in ['Sold Price', 'QTY']:
            if col in df_f.columns:
                df_f[col] = pd.to_numeric(df_f[col], errors='coerce').fillna(0)

        # Price range filter
        if price_range and price_range != "ALL" and price_range in PRICE_RANGES:
            lo, hi = PRICE_RANGES[price_range]
            df_f = df_f[(df_f['Sold Price'] >= lo) & (df_f['Sold Price'] < hi)]

        if df_f.empty:
            return {"qty": {}, "val": {}, "all_ranges": {}}

        # Normalise Financier
        fin_map = {
            'BAJAJ FINANCE': 'BAJAJ', 'BAJAJ FINSERV': 'BAJAJ',
            'HDB FINANCIAL': 'HDB', 'HDB FINANCE': 'HDB',
            'IDFC FIRST BANK': 'IDFC', 'IDFC FIRST BANK LTD': 'IDFC',
            'POONAWALLA FINCORP': 'PFL', 'PFL': 'PFL',
            'TVS CREDIT': 'TVS',
            'DMI FINANCE': 'DMI',
            'PINE LAB EMI': 'PineLab', 'PINELAB': 'PineLab', 'PINE LAB': 'PineLab',
            'PAYTM': 'PAYTM',
            'BENOW': 'Benow',
            'ACASH': 'aCASH', 'A CASH': 'aCASH',
        }
        df_f['Financier'] = df_f['Financier'].fillna('').astype(str).str.strip().str.upper()
        _unup_raw_pr, _ = _unuploaded_card_raw_names(target_date_str, timeframe, end_date_str)
        if _unup_raw_pr:
            df_f.loc[df_f['Financier'].isin(_unup_raw_pr), 'Financier'] = 'ACASH'
        df_f['Fin_Mapped'] = df_f['Financier'].map(lambda x: fin_map.get(x, x))
        df_f.loc[df_f['Fin_Mapped'] == 'ACASH', 'Fin_Mapped'] = 'aCASH'

        # Category
        if 'Category' not in df_f.columns and 'Product' in df_f.columns:
            df_f['Category'] = df_f['Product'].fillna('').astype(str).str.upper().map(PRODUCT_CATEGORY_MAP).fillna('BANK EMI')
        df_f['Category'] = df_f['Category'].fillna('BANK EMI').astype(str).str.strip().str.upper()
        df_f['Category'] = df_f['Category'].replace({'': 'BANK EMI', 'NAN': 'BANK EMI'})

        fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"]
        paperFins = {"BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"}
        cardFins  = {"PineLab", "PAYTM", "Benow"}
        cats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]

        def build_cat_fin(df_sub, is_qty):
            result = {}
            for cat in cats:
                cat_df = df_sub[df_sub['Category'] == cat]
                row = {}
                for fin in fins:
                    sub = cat_df[cat_df['Fin_Mapped'] == fin]
                    row[fin] = int(sub['QTY'].sum()) if is_qty else float(sub['Sold Price'].sum())
                total = sum(row.values())
                paper = sum(row[f] for f in paperFins)
                card  = sum(row[f] for f in cardFins)
                acash = row.get('aCASH', 0)
                fin_share = ((total - acash) / total) if total > 0 else 0
                row['total']     = total
                row['fin_share'] = fin_share
                row['paper']     = paper
                row['card']      = card
                # ASP (only meaningful for value)
                row['qty_total'] = int(cat_df['QTY'].sum())
                row['value_total'] = float(cat_df['Sold Price'].sum())
                result[cat] = row
            return result

        qty_data = build_cat_fin(df_f, True)
        val_data = build_cat_fin(df_f, False)

        # Also pre-compute per-range breakdown for "All Prices" grand total
        all_ranges = {}
        for rng_name, (lo, hi) in PRICE_RANGES.items():
            rdf = df_f if price_range == "ALL" else df_f  # same df when ALL, per-range pre-computed
            sub_df = df[(df['Date'].dt.year == target_date.year) & (df['Date'].dt.month == target_date.month)] if timeframe == 'monthly' else df_f
            sub_df = sub_df[(sub_df.get('Sold Price', pd.Series(dtype=float)) >= lo) & (sub_df.get('Sold Price', pd.Series(dtype=float)) < hi)] if 'Sold Price' in sub_df.columns else sub_df
            all_ranges[rng_name] = int(sub_df['QTY'].sum()) if 'QTY' in sub_df.columns else 0

        return {
            "status": "success",
            "price_range": price_range,
            "qty": qty_data,
            "val": val_data,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def _load_and_filter(raw_file_path, target_date_str, timeframe, df=None, end_date_str=None, state=""):
    """Shared helper: load cached df and apply timeframe filter.

    If *df* is provided (e.g. pre-fetched from ClickHouse) the file-read
    step is skipped entirely and the caller's DataFrame is used directly.
    """
    global _cached_df, _cached_mtime
    if df is None:
        current_mtime = os.path.getmtime(raw_file_path) if os.path.exists(raw_file_path) else 0
        if _cached_df is not None and _cached_mtime == current_mtime:
            df = _cached_df.copy()
        else:
            df = pd.read_excel(raw_file_path, engine='calamine')
            df = _normalize_columns(df)
            if 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
                df = df.dropna(subset=['Date'])
            _cached_df = df.copy()
            _cached_mtime = current_mtime

    if 'Date' not in df.columns:
        return None, "Date column not found"

    target_date = pd.to_datetime(target_date_str)
    if end_date_str:
        end_date = pd.to_datetime(end_date_str)
    else:
        end_date = target_date

    if timeframe == 'daily':
        mask = df['Date'].dt.date == target_date.date()
    elif timeframe == 'monthly':
        mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.month == target_date.month)
    elif timeframe == 'quarterly':
        mask = (df['Date'].dt.year == target_date.year) & (df['Date'].dt.quarter == target_date.quarter)
    elif timeframe == 'custom':
        mask = (df['Date'].dt.date >= target_date.date()) & (df['Date'].dt.date <= end_date.date())
    else:
        mask = pd.Series(True, index=df.index)

    df_f = df[mask].copy()
    if df_f.empty:
        return None, "No data found for the selected timeframe"

    if state and 'State' in df_f.columns:
        _sts = [x.strip().upper() for x in str(state).split(',') if x.strip()]
        if _sts:
            df_f = df_f[df_f['State'].fillna('').astype(str).str.upper().str.strip().isin(_sts)]
            if df_f.empty:
                return None, "No data found for the selected state"

    for col in ['Sold Price', 'QTY']:
        if col in df_f.columns:
            df_f[col] = pd.to_numeric(df_f[col], errors='coerce').fillna(0)

    fin_map = {
        'BAJAJ FINANCE': 'BAJAJ', 'BAJAJ FINSERV': 'BAJAJ',
        'HDB FINANCIAL': 'HDB', 'HDB FINANCE': 'HDB',
        'IDFC FIRST BANK': 'IDFC', 'IDFC FIRST BANK LTD': 'IDFC',
        'POONAWALLA FINCORP': 'PFL', 'PFL': 'PFL',
        'TVS CREDIT': 'TVS', 'DMI FINANCE': 'DMI',
        'PINE LAB EMI': 'PineLab', 'PINELAB': 'PineLab', 'PINE LAB': 'PineLab',
        'PAYTM': 'PAYTM', 'BENOW': 'Benow',
        'ACASH': 'aCASH', 'A CASH': 'aCASH',
    }
    df_f['Financier'] = df_f.get('Financier', pd.Series([''] * len(df_f))).fillna('').astype(str).str.strip().str.upper()
    _unup_raw_lf, _ = _unuploaded_card_raw_names(target_date_str, timeframe, end_date_str)
    if _unup_raw_lf:
        df_f.loc[df_f['Financier'].isin(_unup_raw_lf), 'Financier'] = 'ACASH'
    df_f['Fin_Mapped'] = df_f['Financier'].map(lambda x: fin_map.get(x, x))
    df_f.loc[df_f['Fin_Mapped'] == 'ACASH', 'Fin_Mapped'] = 'aCASH'

    if 'Category' not in df_f.columns and 'Product' in df_f.columns:
        df_f['Category'] = df_f['Product'].fillna('').astype(str).str.upper().map(PRODUCT_CATEGORY_MAP).fillna('BANK EMI')
    df_f['Category'] = df_f.get('Category', pd.Series(['BANK EMI'] * len(df_f))).fillna('BANK EMI').astype(str).str.strip().str.upper()
    df_f['Category'] = df_f['Category'].replace({'': 'BANK EMI', 'NAN': 'BANK EMI'})

    return df_f, None


def get_brand_data(raw_file_path, target_date_str, timeframe, category='ALL', product='ALL', df=None, end_date_str=None, state=""):
    try:
        df_f, err = _load_and_filter(raw_file_path, target_date_str, timeframe, df=df, end_date_str=end_date_str, state=state)
        if err:
            return {"error": err}

        if category and category != 'ALL':
            df_f = df_f[df_f['Category'] == category.strip().upper()]
            
        if product and product != 'ALL':
            product_col = None
            for col_candidate in ['Product', 'Item Group', 'Item Category']:
                if col_candidate in df_f.columns:
                    product_col = col_candidate
                    break
            if product_col:
                df_f = df_f[df_f[product_col].fillna('').astype(str).str.strip().str.upper() == product.strip().upper()]

        if df_f.empty:
            return {"brands": [], "status": "success"}

        fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"]
        paperFins = {"BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"}

        brand_col = None
        for col in ['Brand', 'BRAND', 'brand']:
            if col in df_f.columns:
                brand_col = col
                break
        if not brand_col:
            return {"error": "Brand column not found in data"}

        df_f['Brand_Clean'] = df_f[brand_col].fillna('UNKNOWN').astype(str).str.strip().str.upper()
        brands = df_f['Brand_Clean'].unique().tolist()

        result = []
        for brand in brands:
            bdf = df_f[df_f['Brand_Clean'] == brand]
            row = {'brand': brand}
            total_qty = int(bdf['QTY'].sum())
            total_val = float(bdf['Sold Price'].sum())
            for fin in fins:
                sub = bdf[bdf['Fin_Mapped'] == fin]
                row[fin] = int(sub['QTY'].sum())
            card_count = (row.get('PineLab', 0) or 0) + (row.get('PAYTM', 0) or 0) + (row.get('Benow', 0) or 0)
            # row['aCASH'] already excludes PineLab/PAYTM/Benow via Fin_Mapped partition
            acash = row.get('aCASH', 0)
            fin_total = sum(row.get(f, 0) for f in fins if f != 'aCASH')
            row['total'] = acash + fin_total
            row['fin_pct'] = round(fin_total / row['total'] * 100, 1) if row['total'] > 0 else 0
            row['value'] = round(total_val / 10000000, 4)   # in Crores, consistent with all other value fields
            # Per-financier value breakdown (in Crores) — used by Value mode in the UI
            for fin in fins:
                sub = bdf[bdf['Fin_Mapped'] == fin]
                row[fin + '_val'] = round(float(sub['Sold Price'].sum()) / 10000000, 4)
            row['asp'] = round(total_val / total_qty, 0) if total_qty > 0 else 0
            result.append(row)

        result.sort(key=lambda x: (-x['total'], x['brand']))
        return {"status": "success", "brands": result}

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


def get_store_data(raw_file_path, target_date_str, timeframe, zone='ALL', zone_map=None, df=None, end_date_str=None, state=""):
    try:
        df_f, err = _load_and_filter(raw_file_path, target_date_str, timeframe, df=df, end_date_str=end_date_str, state=state)
        if err:
            return {"error": err}

        zone_map_upper = {k.strip().upper(): v.strip().upper() for k, v in (zone_map or {}).items()}

        branch_col = None
        for col in ['Branch', 'BRANCH', 'STore', 'Store']:
            if col in df_f.columns:
                branch_col = col
                break
        rbm_col = 'RBM' if 'RBM' in df_f.columns else None

        if not branch_col:
            return {"error": "Branch/Store column not found"}

        df_f['Branch_Clean'] = df_f[branch_col].fillna('UNKNOWN').astype(str).str.strip().str.upper()
        if rbm_col:
            df_f['RBM_Clean'] = df_f[rbm_col].fillna('').astype(str).str.strip().str.upper()
        else:
            df_f['RBM_Clean'] = ''

        # Assign zone from zone_map
        df_f['Zone'] = df_f['RBM_Clean'].map(lambda r: zone_map_upper.get(r, 'OTHER'))

        # Filter by zone
        if zone and zone != 'ALL':
            df_f = df_f[df_f['Zone'] == zone.strip().upper()]
        if df_f.empty:
            return {"stores": [], "zones": sorted(df_f['Zone'].unique().tolist()) if 'Zone' in df_f.columns else [], "status": "success"}

        fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab"]
        result = []
        branches = df_f['Branch_Clean'].unique()

        for branch in branches:
            bdf = df_f[df_f['Branch_Clean'] == branch]
            rbm = bdf['RBM_Clean'].mode()[0] if not bdf['RBM_Clean'].empty else ''
            zone_label = zone_map_upper.get(rbm, 'OTHER')

            total_qty = int(bdf['QTY'].sum())
            total_val = float(bdf['Sold Price'].sum())
            acash_qty = int(bdf[bdf['Fin_Mapped'] == 'aCASH']['QTY'].sum())
            fin_qty   = total_qty - acash_qty
            fin_pct   = round(fin_qty / total_qty * 100, 1) if total_qty > 0 else 0

            row = {
                'branch': branch,
                'zone': zone_label,
                'rbm': rbm,
                'total_qty': total_qty,
                'fin_qty': fin_qty,
                'fin_pct': fin_pct,
                'total_val': total_val,
                'asp': round(total_val / total_qty, 0) if total_qty > 0 else 0,
            }
            for fin in fins:
                sub = bdf[bdf['Fin_Mapped'] == fin]
                row[fin] = int(sub['QTY'].sum())
            result.append(row)

        result.sort(key=lambda x: (-x['total_qty'], x['branch']))

        # All zones for filter dropdown
        all_zones = sorted(set(zone_map_upper.values()))

        return {"status": "success", "stores": result, "zones": all_zones}

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}
