"""
finance_report_generator.py  (v2 - Interactive Dropdowns)

Generates Finance Detailed Report.xlsx with 7 sheets.
Sheets 3-7 have working Excel dropdown cells: changing the selection
instantly refreshes the pivot table via COUNTIFS / SUMIFS formulas
that reference a hidden _Data sheet.

Pine Lab EMI, PAYTM, Benow columns are fixed at 0 until
the supplemental card-data upload is implemented.
"""

import datetime
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DS = '_Data'           # hidden data sheet name

# Column letters in every pivot table
COL_CAT      = 'A'    # CATEGORY label
COL_ACASH    = 'B'    # aCash
COL_BAJAJ    = 'C'    # BAJAJ FINANCE
COL_HDB      = 'D'    # HDB FINANCE
COL_IDFC     = 'E'    # IDFC FIRST BANK LTD
COL_PFL      = 'F'    # Poonawalla Fincorp
COL_TVS      = 'G'    # TVS Credit
COL_DMI      = 'H'    # DMI FINANCE
COL_PINE     = 'I'    # Pine Lab EMI  (always 0)
COL_PAYTM    = 'J'    # PAYTM         (always 0)
COL_BENOW    = 'K'    # Benow         (always 0)
COL_TOTAL    = 'L'    # TOTAL
COL_FINSH    = 'M'    # FIN SHARE
COL_PAPER    = 'N'    # Paper Finance Total
COL_CARD     = 'O'    # Card Finance Total (always 0)
COL_DROP     = 'Q'    # Dropdown cell (filter selector)
COL_LSTLAB   = 'S'    # Summary-list label
COL_LSTVAL   = 'T'    # Summary-list value (count)

ALL_HEADER = [
    'CATEGORY',
    'aCASH', 'BAJAJ FINANCE', 'HDB FINANCE', 'IDFC FIRST BANK LTD',
    'Poonawalla Fincorp', 'TVS Credit', 'DMI FINANCE',
    'Pine Lab EMI', 'PAYTM', 'Benow',
    'TOTAL', 'FIN SHARE', 'Paper Finance Total', 'Card Finance Total',
]

# Financier names as they appear in the cleaned data
FIN_NAMES = [
    'aCash', 'BAJAJ FINANCE', 'HDB FINANCE', 'IDFC FIRST BANK LTD',
    'Poonawalla Fincorp', 'TVS Credit', 'DMI FINANCE',
    'Pine Lab EMI', 'PAYTM', 'Benow',
]
PAPER_FINS = ['BAJAJ FINANCE', 'HDB FINANCE', 'IDFC FIRST BANK LTD',
              'Poonawalla Fincorp', 'TVS Credit', 'DMI FINANCE']
CARD_FINS  = ['Pine Lab EMI', 'PAYTM', 'Benow']

CAT_MAP = {
    'CD Products': 'CD PRODUCTS',
    'LAPTOP':      'LAPTOP',
    'MOBILE':      'MOBILE',
    'HVA':         'HVA',
    'SA':          'SA',
    'Others':      'OTHERS',
}
DISPLAY_CATS = ['CD PRODUCTS', 'LAPTOP', 'MOBILE', 'HVA', 'SA', 'OTHERS']

CD_PRODUCT_TYPES = [
    'AIR CONDITIONER', 'DISH WASHER', 'DRYER', 'FREEZER',
    'HOME APPLIANCES', 'MICROWAVE OVEN', 'REFRIGERATORS',
    'TV', 'WASHING MACHINES',
]

PRICE_ORDER = [
    'Below 10K', '10K to 15K', '15K to 20K', '20K to 30K',
    '30K to 40K', '40K to 50K', '50K to 75K', '75K to 100K',
    '100K to 150K', '150K to 200K', 'Above 200K', 'Return',
]

# _Data sheet column letters (for formula references)
DC_CAT    = 'A'   # Report_Category
DC_RBM    = 'B'   # RBM
DC_FIN    = 'C'   # Financier
DC_PRICE  = 'D'   # Sold Price
DC_FINBDM = 'E'   # Fin BDM
DC_STORE  = 'F'   # STore
DC_PRANGE = 'G'   # Price Range
DC_ZONE   = 'H'   # Zone
DC_PROD   = 'I'   # Product (upper)
# Unique-value lists (for dropdown) stored in _Data starting col K
DC_LIST_START = 11   # column index 11 = K  (1-based)

# ─────────────────────────────────────────────────────────────────────────────
# Layout constants for interactive sheets
# ─────────────────────────────────────────────────────────────────────────────

DROP_ROW      = 2    # row that contains the dropdown cell
VOL_HDR_ROW   = 5
VOL_CAT_START = 6    # first category data row
VOL_CAT_END   = 11   # last category data row (6 cats)
VOL_BANK_ROW  = 12
VOL_GT_ROW    = 13
VOL_SHR_ROW   = 15
VOL_SIFV_ROW  = 16

VAL_HDR_ROW   = 19
VAL_CAT_START = 20
VAL_CAT_END   = 25
VAL_BANK_ROW  = 26
VAL_GT_ROW    = 27
VAL_SHR_ROW   = 29
VAL_SIFV_ROW  = 30
VAL_ASP_ROW   = 31

# ─────────────────────────────────────────────────────────────────────────────
# Styling
# ─────────────────────────────────────────────────────────────────────────────

_thin = Side(border_style='thin', color='CCCCCC')
_BDR  = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

_FILLS = {
    'hdr':  PatternFill('solid', fgColor='1F3864'),
    'tot':  PatternFill('solid', fgColor='BDD7EE'),
    'shr':  PatternFill('solid', fgColor='FFF2CC'),
    'alt':  PatternFill('solid', fgColor='EEF3FB'),
    'drp':  PatternFill('solid', fgColor='E2EFDA'),
    'titl': PatternFill('solid', fgColor='D6E4F0'),
}
_FONTS = {
    'hdr':  Font(color='FFFFFF', bold=True, size=9),
    'tot':  Font(bold=True, size=9),
    'shr':  Font(bold=True, size=9),
    'alt':  Font(size=9),
    'nrm':  Font(size=9),
    'drp':  Font(bold=True, size=11, color='1F3864'),
    'titl': Font(bold=True, size=10, color='1F3864'),
}

def _sc(cell, kind, center=True):
    cell.font   = _FONTS.get(kind, _FONTS['nrm'])
    cell.border = _BDR
    if center:
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=False)
    if kind in _FILLS:
        cell.fill = _FILLS[kind]

def _fmt(cell, v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return
    if isinstance(v, (int, np.integer)):
        cell.number_format = '#,##0'
    elif isinstance(v, float):
        cell.number_format = '0.00%' if abs(v) <= 1.5 else '#,##0.00'

def _wr(ws, ri, vals, kinds):
    """Write a row. vals and kinds are parallel lists."""
    for ci, (v, k) in enumerate(zip(vals, kinds), 1):
        safe = None if (isinstance(v, float) and np.isnan(v)) else v
        c = ws.cell(row=ri, column=ci, value=safe)
        _sc(c, k)
        _fmt(c, v)

def _set_widths(ws):
    ws.column_dimensions['A'].width = 22
    for i in range(2, 16):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.column_dimensions['Q'].width = 20
    ws.column_dimensions['S'].width = 18
    ws.column_dimensions['T'].width = 12

# ─────────────────────────────────────────────────────────────────────────────
# Data-sheet builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_data_sheet(wb, df):
    """
    Create hidden _Data sheet with all pivot-source columns.
    Also writes sorted unique-value lists (for dropdowns) starting at column K.
    Returns a dict {group_key: (list_col_letter, count)} for each group.
    """
    ws = wb.create_sheet(DS)
    ws.sheet_state = 'hidden'

    # Write header
    headers = ['Report_Category', 'RBM', 'Financier', 'Sold Price',
               'Fin BDM', 'STore', 'Price Range', 'Zone', 'Product']
    for ci, h in enumerate(headers, 1):
        ws.cell(1, ci, h)

    # Write data rows
    col_map = {
        'Report_Category': 'Report_Category',
        'RBM':             'RBM',
        'Financier':       'Financier',
        'Sold Price':      'Sold Price',
        'Fin BDM':         'Fin BDM',
        'STore':           'STore',
        'Price Range':     'Price Range',
        'Zone':            'Zone',
        'Product':         'Product_Upper',
    }
    for ri, (_, row) in enumerate(df.iterrows(), 2):
        ws.cell(ri, 1, row.get('Report_Category', ''))
        ws.cell(ri, 2, row.get('RBM', ''))
        ws.cell(ri, 3, row.get('Financier', ''))
        ws.cell(ri, 4, row.get('Sold Price', 0))
        ws.cell(ri, 5, row.get('Fin BDM', ''))
        ws.cell(ri, 6, row.get('STore', ''))
        ws.cell(ri, 7, row.get('Price Range', ''))
        ws.cell(ri, 8, row.get('Zone', ''))
        ws.cell(ri, 9, row.get('Product_Upper', ''))

    # Write unique-value lists for dropdowns starting at column K (11)
    groups = {
        'RBM':        ('B', DC_RBM),
        'Fin BDM':    ('E', DC_FINBDM),
        'STore':      ('F', DC_STORE),
        'Price Range':('G', DC_PRANGE),
        'Zone':       ('H', DC_ZONE),
    }
    list_info = {}   # group_name -> (list_col_letter, n_values)
    list_col_idx = DC_LIST_START  # start at column K (11)

    for group_name, (src_df_col, _) in groups.items():
        if group_name == 'Price Range':
            # Keep price ranges in logical order
            uniques = [p for p in PRICE_ORDER if p in df['Price Range'].values]
        elif group_name == 'RBM':
            uniques = sorted(df['RBM'].dropna().unique().tolist())
        elif group_name == 'Fin BDM':
            uniques = sorted(df['Fin BDM'].dropna().replace('', np.nan).dropna().unique().tolist())
        elif group_name == 'STore':
            uniques = sorted(df['STore'].dropna().unique().tolist())
        elif group_name == 'Zone':
            uniques = sorted(df['Zone'].dropna().unique().tolist())
        else:
            uniques = sorted(df[group_name].dropna().unique().tolist())

        list_col_letter = get_column_letter(list_col_idx)
        ws.cell(1, list_col_idx, group_name)   # header
        for ri, val in enumerate(uniques, 2):
            ws.cell(ri, list_col_idx, val)

        list_info[group_name] = (list_col_letter, len(uniques))
        list_col_idx += 1

    return list_info, len(df) + 1   # (list_info, last_data_row)

# ─────────────────────────────────────────────────────────────────────────────
# Formula helpers
# ─────────────────────────────────────────────────────────────────────────────

def _vf(cat, grp_col, fin, drop_ref):
    """COUNTIFS formula for volume of one (category, group_filter, financier)."""
    if fin in CARD_FINS:
        return 0
    return (f"=IFERROR(COUNTIFS('{DS}'!${DC_CAT}:${DC_CAT},\"{cat}\","
            f"'{DS}'!${grp_col}:${grp_col},{drop_ref},"
            f"'{DS}'!${DC_FIN}:${DC_FIN},\"{fin}\"),0)")

def _sf(cat, grp_col, fin, drop_ref):
    """SUMIFS formula for value of one (category, group_filter, financier)."""
    if fin in CARD_FINS:
        return 0
    return (f"=IFERROR(SUMIFS('{DS}'!${DC_PRICE}:${DC_PRICE},"
            f"'{DS}'!${DC_CAT}:${DC_CAT},\"{cat}\","
            f"'{DS}'!${grp_col}:${grp_col},{drop_ref},"
            f"'{DS}'!${DC_FIN}:${DC_FIN},\"{fin}\"),0)")

def _total_f(ri):
    return f"=SUM({COL_ACASH}{ri}:{COL_BENOW}{ri})"

def _finsh_f(ri):
    return f"=IF({COL_TOTAL}{ri}>0,({COL_TOTAL}{ri}-{COL_ACASH}{ri})/{COL_TOTAL}{ri},0)"

def _paper_f(ri):
    return f"=SUM({COL_BAJAJ}{ri}:{COL_DMI}{ri})"

def _gt_col_f(col, start, end):
    return f"=SUM({col}{start}:{col}{end})"

def _share_col_f(col, gt_row):
    return f"=IF(${COL_TOTAL}${gt_row}>0,{col}{gt_row}/${COL_TOTAL}${gt_row},0)"

def _sifv_col_f(col, gt_row):
    return f"=IF(${COL_PAPER}${gt_row}>0,{col}{gt_row}/${COL_PAPER}${gt_row},0)"

def _asp_col_f(col, vol_gt_row, val_gt_row):
    return f"=IF({col}{vol_gt_row}>0,{col}{val_gt_row}/{col}{vol_gt_row},\"\")"

# ─────────────────────────────────────────────────────────────────────────────
# Core interactive sheet writer
# ─────────────────────────────────────────────────────────────────────────────

def _write_interactive_sheet(ws, group_name, grp_col, drop_ref,
                             list_col, list_n, first_val,
                             title_label, df_for_summary):
    """
    Write a fully interactive sheet:
    - Dropdown at COL_DROP / DROP_ROW
    - Volume table with COUNTIFS formulas
    - Value table with SUMIFS formulas
    - Summary list of all group values (with vol counts) to the right
    """
    today = datetime.date.today().strftime('%d-%b-%Y')

    # ── Row 1: Title ──────────────────────────────────────────────────────────
    c = ws.cell(1, 1, f'{title_label}   |   Region as on {today}')
    c.font = _FONTS['titl']
    ws.merge_cells('A1:O1')
    ws.cell(1, 1).alignment = Alignment(horizontal='left', vertical='center')

    # ── Row 2: Group label + dropdown ─────────────────────────────────────────
    lbl = ws.cell(DROP_ROW, 1, f'{group_name}:')
    lbl.font = Font(bold=True, size=10, color='1F3864')

    drop_cell = ws.cell(DROP_ROW, 17, first_val)   # col Q = 17
    drop_cell.fill  = _FILLS['drp']
    drop_cell.font  = _FONTS['drp']
    drop_cell.alignment = Alignment(horizontal='center', vertical='center')
    drop_cell.border = Border(
        left=Side(border_style='medium', color='27AE60'),
        right=Side(border_style='medium', color='27AE60'),
        top=Side(border_style='medium', color='27AE60'),
        bottom=Side(border_style='medium', color='27AE60'),
    )

    # Data validation dropdown
    list_range = f"'{DS}'!${list_col}$2:${list_col}${list_n+1}"
    dv = DataValidation(type='list', formula1=list_range,
                        allow_blank=False, showDropDown=False)
    dv.error       = 'Select a value from the list'
    dv.errorTitle  = 'Invalid Input'
    dv.prompt      = f'Select {group_name}'
    dv.promptTitle = f'Choose {group_name}'
    dv.showErrorMessage = True
    dv.showInputMessage = True
    ws.add_data_validation(dv)
    dv.sqref = f'{COL_DROP}{DROP_ROW}'

    # ── Rows 3-4: note + blank ─────────────────────────────────────────────────
    ws.cell(3, 1, '  ⬆ Select from dropdown to filter the tables').font = \
        Font(italic=True, size=8, color='888888')

    # ── Volume table ──────────────────────────────────────────────────────────
    ws.cell(VOL_HDR_ROW - 1, 1, 'VOLUME (Count)').font = _FONTS['titl']

    # Header row
    kinds_h = ['hdr'] * len(ALL_HEADER)
    _wr(ws, VOL_HDR_ROW, ALL_HEADER, kinds_h)

    # Category rows
    for i, cat in enumerate(DISPLAY_CATS):
        ri = VOL_CAT_START + i
        row_vals  = [cat]
        row_kinds = ['tot']
        for fin in FIN_NAMES:
            row_vals.append(_vf(cat, grp_col, fin, drop_ref))
            row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        # TOTAL
        row_vals.append(_total_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        # FIN SHARE
        row_vals.append(_finsh_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        # Paper
        row_vals.append(_paper_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        # Card (0)
        row_vals.append(0)
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        _wr(ws, ri, row_vals, row_kinds)

    # BANK EMI row
    bank_v = ['BANK EMI'] + [None]*7 + [0, 0, 0] + [None, None, 0, 0]
    bank_k = ['tot'] + ['nrm']*14
    _wr(ws, VOL_BANK_ROW, bank_v, bank_k)

    # Grand Total row
    gt_v = ['Grand Total']
    gt_k = ['tot']
    for col_l in [COL_ACASH, COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS,
                  COL_DMI, COL_PINE, COL_PAYTM, COL_BENOW]:
        gt_v.append(_gt_col_f(col_l, VOL_CAT_START, VOL_CAT_END))
        gt_k.append('tot')
    gt_v.append(_total_f(VOL_GT_ROW))
    gt_k.append('tot')
    gt_v.append(_finsh_f(VOL_GT_ROW))
    gt_k.append('tot')
    gt_v.append(_paper_f(VOL_GT_ROW))
    gt_k.append('tot')
    gt_v.append(0)
    gt_k.append('tot')
    _wr(ws, VOL_GT_ROW, gt_v, gt_k)

    # Share row
    shr_v = ['Share']
    shr_k = ['shr']
    for col_l in [COL_ACASH, COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS,
                  COL_DMI, COL_PINE, COL_PAYTM, COL_BENOW]:
        shr_v.append(_share_col_f(col_l, VOL_GT_ROW))
        shr_k.append('shr')
    shr_v += [None, None,
              f"=IF(${COL_TOTAL}${VOL_GT_ROW}>0,{COL_PAPER}{VOL_GT_ROW}/${COL_TOTAL}${VOL_GT_ROW},0)",
              0]
    shr_k += ['shr', 'shr', 'shr', 'shr']
    _wr(ws, VOL_SHR_ROW, shr_v, shr_k)

    # Share in Fin Value row
    sifv_v = ['Share in Fin Value', None]
    sifv_k = ['shr', 'shr']
    for col_l in [COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS, COL_DMI]:
        sifv_v.append(_sifv_col_f(col_l, VOL_GT_ROW))
        sifv_k.append('shr')
    sifv_v += [0, 0, 0, None, None, None, None]
    sifv_k += ['shr'] * 7
    _wr(ws, VOL_SIFV_ROW, sifv_v, sifv_k)

    # ── Value table ───────────────────────────────────────────────────────────
    ws.cell(VAL_HDR_ROW - 1, 1, 'VALUE (₹)').font = _FONTS['titl']
    _wr(ws, VAL_HDR_ROW, ALL_HEADER, kinds_h)

    for i, cat in enumerate(DISPLAY_CATS):
        ri = VAL_CAT_START + i
        row_vals  = [cat]
        row_kinds = ['tot']
        for fin in FIN_NAMES:
            row_vals.append(_sf(cat, grp_col, fin, drop_ref))
            row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        row_vals.append(_total_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        row_vals.append(_finsh_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        row_vals.append(_paper_f(ri))
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        row_vals.append(0)
        row_kinds.append('alt' if i % 2 == 0 else 'nrm')
        _wr(ws, ri, row_vals, row_kinds)

    bank_v2 = ['BANK EMI'] + [None]*7 + [0, 0, 0] + [None, None, 0, 0]
    _wr(ws, VAL_BANK_ROW, bank_v2, bank_k)

    gt2_v = ['Grand Total']
    gt2_k = ['tot']
    for col_l in [COL_ACASH, COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS,
                  COL_DMI, COL_PINE, COL_PAYTM, COL_BENOW]:
        gt2_v.append(_gt_col_f(col_l, VAL_CAT_START, VAL_CAT_END))
        gt2_k.append('tot')
    gt2_v.append(_total_f(VAL_GT_ROW))
    gt2_k.append('tot')
    gt2_v.append(_finsh_f(VAL_GT_ROW))
    gt2_k.append('tot')
    gt2_v.append(_paper_f(VAL_GT_ROW))
    gt2_k.append('tot')
    gt2_v.append(0)
    gt2_k.append('tot')
    _wr(ws, VAL_GT_ROW, gt2_v, gt2_k)

    shr2_v = ['Share']
    shr2_k = ['shr']
    for col_l in [COL_ACASH, COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS,
                  COL_DMI, COL_PINE, COL_PAYTM, COL_BENOW]:
        shr2_v.append(_share_col_f(col_l, VAL_GT_ROW))
        shr2_k.append('shr')
    shr2_v += [None, None,
               f"=IF(${COL_TOTAL}${VAL_GT_ROW}>0,{COL_PAPER}{VAL_GT_ROW}/${COL_TOTAL}${VAL_GT_ROW},0)",
               0]
    shr2_k += ['shr'] * 4
    _wr(ws, VAL_SHR_ROW, shr2_v, shr2_k)

    sifv2_v = ['Share in Fin Value', None]
    sifv2_k = ['shr', 'shr']
    for col_l in [COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS, COL_DMI]:
        sifv2_v.append(_sifv_col_f(col_l, VAL_GT_ROW))
        sifv2_k.append('shr')
    sifv2_v += [0, 0, 0, None, None, None, None]
    sifv2_k += ['shr'] * 7
    _wr(ws, VAL_SIFV_ROW, sifv2_v, sifv2_k)

    # ASP row
    asp_v = ['ASP', None]
    asp_k = ['shr', 'shr']
    for col_l in [COL_BAJAJ, COL_HDB, COL_IDFC, COL_PFL, COL_TVS, COL_DMI]:
        asp_v.append(_asp_col_f(col_l, VOL_GT_ROW, VAL_GT_ROW))
        asp_k.append('shr')
    asp_v += [None, None, None, None, None, None, None]
    asp_k += ['shr'] * 7
    _wr(ws, VAL_ASP_ROW, asp_v, asp_k)

    # ── Summary list (right side, column S/T) ─────────────────────────────────
    ws.cell(1, 19, group_name).font = Font(bold=True, size=9, color='1F3864')
    ws.cell(1, 20, 'Total Vol').font = Font(bold=True, size=9, color='1F3864')

    group_totals = df_for_summary.groupby(group_name).size().sort_index()
    for ri, (grp_val, cnt) in enumerate(group_totals.items(), 2):
        lc = ws.cell(ri, 19, str(grp_val))
        lc.font = Font(size=9, bold=(str(grp_val) == first_val))
        vc = ws.cell(ri, 20, int(cnt))
        vc.font = Font(size=9)
        vc.number_format = '#,##0'

    # Freeze panes
    ws.freeze_panes = 'B6'

# ─────────────────────────────────────────────────────────────────────────────
# Static product-wise sheet helpers
# ─────────────────────────────────────────────────────────────────────────────

def _vol_pivot(df, row_col):
    p = (df.groupby([row_col, 'Financier']).size()
           .unstack(fill_value=0)
           .reindex(columns=FIN_NAMES, fill_value=0))
    for c in CARD_FINS:
        p[c] = 0
    return p

def _val_pivot(df, row_col):
    p = (df.groupby([row_col, 'Financier'])['Sold Price'].sum()
           .unstack(fill_value=0)
           .reindex(columns=FIN_NAMES, fill_value=0))
    for c in CARD_FINS:
        p[c] = 0
    return p

def _pivot_data_row(label, pivot, key):
    row = {'CATEGORY': label}
    if key in pivot.index:
        for f in FIN_NAMES:
            row[f] = int(pivot.at[key, f]) if isinstance(pivot.at[key, f], (int, np.integer)) else float(pivot.at[key, f])
    else:
        for f in FIN_NAMES:
            row[f] = 0
    total = sum(row[f] for f in FIN_NAMES)
    acash = row['aCash']
    row['TOTAL']               = total
    row['FIN SHARE']           = (total - acash) / total if total > 0 else 0.0
    row['Paper Finance Total'] = sum(row[f] for f in PAPER_FINS)
    row['Card Finance Total']  = sum(row[f] for f in CARD_FINS)
    return row

def _static_summary_rows(data_rows, vol_p=None, cats=None, is_val=False):
    rows = list(data_rows)
    bank = {'CATEGORY': 'BANK EMI'}
    for f in ['aCash'] + PAPER_FINS:
        bank[f] = np.nan
    for f in CARD_FINS:
        bank[f] = 0
    bank.update({'TOTAL': np.nan, 'FIN SHARE': np.nan,
                 'Paper Finance Total': 0, 'Card Finance Total': 0})
    rows.append(bank)

    gt = {'CATEGORY': 'Grand Total'}
    for f in FIN_NAMES:
        gt[f] = sum(r.get(f) or 0 for r in data_rows)
    gt_total  = sum(gt[f] for f in FIN_NAMES)
    gt_acash  = gt['aCash']
    gt['TOTAL']               = gt_total
    gt['FIN SHARE']           = (gt_total - gt_acash) / gt_total if gt_total > 0 else 0.0
    gt['Paper Finance Total'] = sum(gt[f] for f in PAPER_FINS)
    gt['Card Finance Total']  = 0
    rows.append(gt)

    shr = {'CATEGORY': 'Share'}
    for f in FIN_NAMES:
        shr[f] = gt[f] / gt_total if gt_total > 0 else 0.0
    shr.update({'TOTAL': np.nan, 'FIN SHARE': np.nan,
                'Paper Finance Total': gt['Paper Finance Total'] / gt_total if gt_total > 0 else 0.0,
                'Card Finance Total': 0})
    rows.append(shr)

    fin_tot = gt['Paper Finance Total']
    sifv = {'CATEGORY': 'Share in Fin Value', 'aCash': np.nan}
    for f in PAPER_FINS:
        sifv[f] = gt[f] / fin_tot if fin_tot > 0 else 0.0
    for f in CARD_FINS:
        sifv[f] = 0.0
    sifv.update({'TOTAL': np.nan, 'FIN SHARE': np.nan,
                 'Paper Finance Total': np.nan, 'Card Finance Total': np.nan})
    rows.append(sifv)

    if is_val and vol_p is not None and cats is not None:
        gt_val = {f: gt[f] for f in FIN_NAMES}
        gt_vol = {}
        for f in PAPER_FINS:
            gt_vol[f] = sum((int(vol_p.at[c, f]) if c in vol_p.index else 0) for c in cats)
        asp = {'CATEGORY': 'ASP', 'aCash': np.nan}
        for f in PAPER_FINS:
            asp[f] = gt_val[f] / gt_vol[f] if gt_vol.get(f, 0) > 0 else np.nan
        for f in CARD_FINS:
            asp[f] = np.nan
        asp.update({'TOTAL': np.nan, 'FIN SHARE': np.nan,
                    'Paper Finance Total': np.nan, 'Card Finance Total': np.nan})
        rows.append(asp)

    return rows

STATIC_SPECIAL = {'Grand Total', 'Share', 'Share in Fin Value', 'ASP', 'BANK EMI'}

def _write_static_table(ws, rows, ri_start, title=None):
    ri = ri_start
    if title:
        ws.cell(ri, 1, title).font = _FONTS['titl']
        ri += 1
    _wr(ws, ri, ALL_HEADER, ['hdr'] * len(ALL_HEADER))
    ri += 1
    for idx, rd in enumerate(rows):
        cat  = rd.get('CATEGORY', '')
        vals = [rd.get(c, np.nan) for c in ['CATEGORY'] + FIN_NAMES +
                ['TOTAL', 'FIN SHARE', 'Paper Finance Total', 'Card Finance Total']]
        kind = 'shr' if cat in STATIC_SPECIAL else ('alt' if idx % 2 == 0 else 'nrm')
        _wr(ws, ri, vals, [kind] * len(vals))
        ri += 1
    return ri + 1

def _build_product_sheets(ws1, ws2, df):
    """Sheets 1 & 2 – static product-wise tables."""
    cd = df[df['Report_Category'] == 'CD PRODUCTS'].copy()

    vol_p = _vol_pivot(cd, 'Product_Upper')
    val_p = _val_pivot(cd, 'Product_Upper')

    vol_data = [_pivot_data_row(p, vol_p, p) for p in CD_PRODUCT_TYPES]
    val_data = [_pivot_data_row(p, val_p, p) for p in CD_PRODUCT_TYPES]

    vol_rows = _static_summary_rows(vol_data, is_val=False)
    val_rows = _static_summary_rows(val_data, vol_p=vol_p, cats=CD_PRODUCT_TYPES, is_val=True)

    today = datetime.date.today().strftime('%d-%b-%Y')
    for ws in [ws1, ws2]:
        c = ws.cell(1, 1, f'CD Products – Product wise Finance Share   |   {today}')
        c.font = _FONTS['titl']
        ws.merge_cells('A1:O1')

    ri = 3
    ri = _write_static_table(ws1, vol_rows, ri, 'VOLUME (Count)')
    _write_static_table(ws2, vol_rows, 3, 'VOLUME (Count)')

    # Sheet 2 also has value
    ri2 = 3
    ri2 = _write_static_table(ws2, vol_rows, ri2, 'VOLUME (Count)')
    _write_static_table(ws2, val_rows, ri2, 'VALUE (₹)')

    # HOME APPLIANCES sub-breakdown in both sheets
    ha = cd[cd['Product_Upper'] == 'HOME APPLIANCES']
    if not ha.empty and 'Item Group' in ha.columns:
        ha2 = ha.copy()
        ha2['Item_Group_U'] = ha2['Item Group'].str.upper().str.strip()
        ha_groups = sorted(ha2['Item_Group_U'].dropna().unique())
        ha_vol = _vol_pivot(ha2, 'Item_Group_U')
        ha_val = _val_pivot(ha2, 'Item_Group_U')
        ha_vr = _static_summary_rows([_pivot_data_row(g, ha_vol, g) for g in ha_groups])
        ha_vl = _static_summary_rows([_pivot_data_row(g, ha_val, g) for g in ha_groups],
                                     vol_p=ha_vol, cats=ha_groups, is_val=True)
        # Sheet 1
        next_r = ws1.max_row + 3
        ws1.cell(next_r, 1, 'HOME APPLIANCES – Sub-Category Volume').font = _FONTS['titl']
        _write_static_table(ws1, ha_vr, next_r + 2)
        # Sheet 2
        next_r2 = ws2.max_row + 3
        ws2.cell(next_r2, 1, 'HOME APPLIANCES – Sub-Category').font = _FONTS['titl']
        nr2 = _write_static_table(ws2, ha_vr, next_r2 + 2, 'VOLUME')
        _write_static_table(ws2, ha_vl, nr2, 'VALUE (₹)')

    for ws in [ws1, ws2]:
        ws.freeze_panes = 'B4'

# ─────────────────────────────────────────────────────────────────────────────
# Cleaned Data sheet
# ─────────────────────────────────────────────────────────────────────────────

def _write_cleaned_data_sheet(wb, cleaned_df: pd.DataFrame):
    """
    Insert the full cleaned data as the FIRST sheet in the workbook.
    Styled with a dark header, alternating row fills, and frozen panes.
    """
    ws = wb.create_sheet('Cleaned Data', 0)   # index 0 = first sheet

    # ── Column widths (approximate) ───────────────────────────────────────────
    COL_WIDTHS = {
        'Slno': 6, 'STore': 20, 'Fin BDM': 14, 'RBM': 10, 'BDM': 14,
        'Date': 13, 'Month': 8, 'Invoice Number': 18, 'Customer Name': 22,
        'Customer Mobile': 16, 'Pincode': 9, 'Staff': 16, 'Product': 18,
        'Category': 14, 'Brand': 14, 'Item Group': 18, 'Item Category': 16,
        'Item Code': 14, 'Item Name': 32, 'IMEI': 18, 'QTY': 6,
        'Financier': 22, 'Finance': 8, 'Delivery Order No': 18,
        'MOP': 14, 'Sold Price': 13, 'EMI': 10,
        'Price Range': 14, 'Price': 13, 'Zone': 10,
    }

    cols = list(cleaned_df.columns)

    # ── Header row ────────────────────────────────────────────────────────────
    hdr_fill = PatternFill('solid', fgColor='1F3864')
    hdr_font = Font(color='FFFFFF', bold=True, size=9)
    hdr_aln  = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin_s   = Side(border_style='thin', color='CCCCCC')
    bdr      = Border(left=thin_s, right=thin_s, top=thin_s, bottom=thin_s)

    for ci, col in enumerate(cols, 1):
        c = ws.cell(1, ci, col)
        c.fill   = hdr_fill
        c.font   = hdr_font
        c.border = bdr
        c.alignment = hdr_aln
        ws.column_dimensions[get_column_letter(ci)].width = COL_WIDTHS.get(col, 14)

    # ── Data rows ─────────────────────────────────────────────────────────────
    alt_fill = PatternFill('solid', fgColor='EEF3FB')
    nrm_font = Font(size=9)
    ctr_aln  = Alignment(horizontal='center', vertical='center')

    for ri, (_, row) in enumerate(cleaned_df.iterrows(), 2):
        fill = alt_fill if ri % 2 == 0 else None
        for ci, col in enumerate(cols, 1):
            val = row[col]
            # Convert NaT / NaN to None for cleaner output
            if isinstance(val, float) and np.isnan(val):
                val = None
            elif hasattr(val, 'isoformat'):   # datetime / date
                val = val
            c = ws.cell(ri, ci, val)
            c.font   = nrm_font
            c.border = bdr
            c.alignment = ctr_aln
            if fill:
                c.fill = fill
            # Number formatting
            if isinstance(val, (int, np.integer)):
                c.number_format = '#,##0'
            elif isinstance(val, float):
                c.number_format = '#,##0.00'

    ws.freeze_panes = 'A2'       # freeze header row
    ws.auto_filter.ref = f'A1:{get_column_letter(len(cols))}1'  # enable filters
    print(f'  Built: Cleaned Data sheet ({len(cleaned_df)} rows)')


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_finance_report(cleaned_df: pd.DataFrame, output_path: str):
    """
    Generate the Finance Detailed Report Excel (7 interactive sheets).
    Sheets 3-7 have dropdown cells that filter the COUNTIFS/SUMIFS tables.
    """
    df = cleaned_df.copy()
    df['Report_Category'] = df['Category'].map(CAT_MAP).fillna('OTHERS')
    df['Product_Upper']   = df['Product'].str.upper().str.strip()
    if 'Fin BDM' not in df.columns:
        df['Fin BDM'] = df.get('Fin_BDM_Cleaned', '')
    df['Fin BDM'] = df['Fin BDM'].fillna('').astype(str).str.strip()

    wb = Workbook()
    wb.remove(wb.active)

    # ── Sheet 0: Cleaned Data (first sheet) ──────────────────────────────────
    _write_cleaned_data_sheet(wb, cleaned_df)

    # ── _Data sheet (hidden, used by formulas) ────────────────────────────────
    list_info, last_row = _build_data_sheet(wb, df)
    print('  Built: _Data sheet')

    # ── Sheet 1 & 2: Product wise (static) ───────────────────────────────────
    ws1 = wb.create_sheet('Product wise Fin share Vol')
    ws2 = wb.create_sheet('Product wise Fin share')
    _build_product_sheets(ws1, ws2, df)
    for ws in [ws1, ws2]:
        _set_widths(ws)
    print('  Built: Product wise sheets')

    # ── Sheets 3-7: Interactive category sheets ───────────────────────────────
    INTERACTIVE = [
        ('Category wise Fin share Fin',    'Fin BDM',     DC_FINBDM,
         'Category – Finance BDM wise'),
        ('Category wise Fin share Store',  'STore',       DC_STORE,
         'Category – Store wise'),
        ('Category Fin share price range', 'Price Range', DC_PRANGE,
         'Category – Price Range wise'),
        ('Category Fin share Zone wise',   'Zone',        DC_ZONE,
         'Category – Zone wise'),
        ('Category wise Fin share RBM',    'RBM',         DC_RBM,
         'Category – RBM wise'),
    ]

    for sheet_name, group_name, grp_col, title_label in INTERACTIVE:
        ws = wb.create_sheet(sheet_name)
        lc, ln = list_info[group_name]

        # Default to first available value
        if group_name == 'Price Range':
            avail = [p for p in PRICE_ORDER if p in df['Price Range'].values]
        elif group_name == 'Fin BDM':
            avail = sorted(df['Fin BDM'].replace('', np.nan).dropna().unique())
        else:
            avail = sorted(df[group_name].dropna().unique().tolist())

        first_val = avail[0] if avail else ''

        drop_ref = f'${COL_DROP}${DROP_ROW}'  # e.g. $Q$2

        # Filter out blank Fin BDM for summary
        df_for_summary = df if group_name != 'Fin BDM' else df[df['Fin BDM'] != '']

        _write_interactive_sheet(
            ws, group_name, grp_col, drop_ref,
            lc, ln, first_val, title_label, df_for_summary
        )
        _set_widths(ws)
        print(f'  Built: {sheet_name}')

    wb.save(output_path)
    print(f'Finance Detailed Report saved -> {output_path}')
    return output_path
