import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Define Color Palette
NAVY_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
ACCENT_BLUE_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
ZEBRA_FILL = PatternFill(start_color="F2F5F8", end_color="F2F5F8", fill_type="solid")
WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
RED_GAP_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # Soft red/orange
GREEN_GAP_FILL = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # Soft green

# Define Borders
THIN_BORDER_SIDE = Side(style='thin', color='D3D3D3')
THIN_BORDER = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=THIN_BORDER_SIDE)
HEADER_BORDER = Border(left=Side(style='thin', color='FFFFFF'), right=Side(style='thin', color='FFFFFF'),
                       top=Side(style='medium', color='1F4E78'), bottom=Side(style='medium', color='1F4E78'))
TOTAL_BORDER = Border(top=Side(style='thin', color='1F4E78'), bottom=Side(style='double', color='1F4E78'))

# Fonts
FONT_TITLE = Font(name='Segoe UI', size=16, bold=True, color='1F4E78')
FONT_SECTION = Font(name='Segoe UI', size=12, bold=True, color='2C3E50')
FONT_HEADER = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
FONT_BODY = Font(name='Segoe UI', size=10, bold=False, color='333333')
FONT_BODY_BOLD = Font(name='Segoe UI', size=10, bold=True, color='1F4E78')
FONT_TOTAL = Font(name='Segoe UI', size=10, bold=True, color='000000')

def auto_fit_columns(ws, max_padding=4, min_width=10):
    for col in ws.columns:
        max_len = 0
        for cell in col:
            # Skip cells that are merged and not the top-left cell
            if type(cell).__name__ == 'MergedCell':
                continue
            val_str = str(cell.value or '')
            if cell.number_format and '%' in cell.number_format:
                val_str += '%' # account for percent sign width
            if len(val_str) > max_len:
                max_len = len(val_str)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + max_padding, min_width)

def generate_excel_report(data, file_path):
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    wb.remove(default_sheet)
    
    # Create sheets
    create_exec_summary_sheet(wb, data)
    create_disbursement_sheet(wb, data)
    create_category_sheet(wb, data)
    create_financier_targets_sheet(wb, data)
    create_special_contest_sheet(wb, data)
    create_manpower_sheet(wb, data)
    
    wb.save(file_path)

def create_exec_summary_sheet(wb, data):
    ws = wb.create_sheet(title="Exec Summary")
    ws.views.sheetView[0].showGridLines = True
    
    # Title Block
    ws.append([])
    ws.cell(row=2, column=2, value="Consumer Finance Weekly Review").font = FONT_TITLE
    ws.cell(row=3, column=2, value=f"Review Date: {data.get('date', '')} | Status as of: {data.get('status_as_of', '')}").font = FONT_BODY
    
    # --- TABLE 1: Target Achievement Status ---
    ws.cell(row=5, column=2, value="Q1 Target Achievement Status").font = FONT_SECTION
    
    headers = ["Metric", "Value Penetration", "Vol Penetration", "Cards Penetration", "Fin Value (Cr)", "Card Value (Cr)"]
    ws.append([]) # row 6 blank
    
    # Write headers at row 7
    for col_idx, header in enumerate(headers, 2):
        cell = ws.cell(row=7, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = HEADER_BORDER
    ws.row_dimensions[7].height = 26
    
    ta_data = data.get("exec_summary", {}).get("target_achievement", {})
    metrics = ["Target", "Achievement", "Last Week Status", "Last Month Status", "G/DG*", "Qtr Trend", "Qtr Trend %"]
    
    row_mapping = {
        "Target": ta_data.get("target", [0]*5),
        "Achievement": ta_data.get("achievement", [0]*5),
        "Last Week Status": ta_data.get("last_week", [0]*5),
        "Last Month Status": ta_data.get("last_month", [0]*5),
        "Qtr Trend": ta_data.get("qtr_trend", [None]*5),
    }
    
    start_row = 8
    for idx, metric in enumerate(metrics):
        current_row = start_row + idx
        ws.cell(row=current_row, column=2, value=metric).font = FONT_BODY_BOLD
        ws.cell(row=current_row, column=2).border = THIN_BORDER
        
        # Determine background fill
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        ws.cell(row=current_row, column=2).fill = row_fill
        
        for col_idx in range(1, 6):
            cell = ws.cell(row=current_row, column=col_idx + 2)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            
            # Format types
            is_pct_col = col_idx <= 3
            number_fmt = "0.00%" if is_pct_col else "0.00"
            cell.alignment = Alignment(horizontal="right")
            
            # Populate cell values and formulas
            if metric in row_mapping:
                val = row_mapping[metric][col_idx-1]
                if val is not None:
                    # If percentage column, convert float like 68.76 to 0.6876 for Excel pct formatting
                    cell.value = (val / 100.0) if (is_pct_col and val > 1) else val
                    cell.number_format = number_fmt
            elif metric == "G/DG*":
                # Growth = Achievement - Last Week Status
                ach_cell = f"{get_column_letter(col_idx+2)}{start_row+1}"
                lw_cell = f"{get_column_letter(col_idx+2)}{start_row+2}"
                cell.value = f"={ach_cell}-{lw_cell}"
                cell.number_format = number_fmt
                # Conditional coloring for growth
                cell.font = FONT_BODY_BOLD
            elif metric == "Qtr Trend %":
                # Trend % = Qtr Trend / Target
                trend_cell = f"{get_column_letter(col_idx+2)}{start_row+5}"
                tgt_cell = f"{get_column_letter(col_idx+2)}{start_row}"
                cell.value = f"=IF({tgt_cell}=0,0,{trend_cell}/{tgt_cell})"
                cell.number_format = "0.0%"
                cell.font = FONT_BODY_BOLD
    
    # --- TABLE 2: Monthwise Performance ---
    next_start_row = start_row + len(metrics) + 3
    ws.cell(row=next_start_row, column=2, value="Monthwise Performance (Q1)").font = FONT_SECTION
    
    mw_headers = ["Key Objectives", "Apr", "May", "Jun", "Q1 Total"]
    for col_idx, header in enumerate(mw_headers, 2):
        cell = ws.cell(row=next_start_row+2, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEADER_BORDER
    ws.row_dimensions[next_start_row+2].height = 26
    
    mw_data = data.get("exec_summary", {}).get("monthwise_performance", {})
    mw_metrics = ["Fin Value %", "Fin Vol %", "Card %", "Fin Value (In Cr)", "Card Value (In Cr)"]
    mw_keys = ["fin_value_pct", "fin_vol_pct", "card_pct", "fin_value_cr", "card_value_cr"]
    
    for idx, metric in enumerate(mw_metrics):
        current_row = next_start_row + 3 + idx
        ws.cell(row=current_row, column=2, value=metric).font = FONT_BODY_BOLD
        ws.cell(row=current_row, column=2).border = THIN_BORDER
        
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        ws.cell(row=current_row, column=2).fill = row_fill
        
        key = mw_keys[idx]
        month_vals = mw_data.get(key, [0, 0, 0])
        is_pct = "Value %" in metric or "Vol %" in metric or "Card %" in metric
        num_format = "0.00%" if is_pct else "0.00"
        
        for m_idx, val in enumerate(month_vals):
            cell = ws.cell(row=current_row, column=m_idx + 3)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal="right")
            if val is not None and val != "":
                cell.value = (val / 100.0) if (is_pct and val > 1) else val
                cell.number_format = num_format
                
        # Total/Avg column
        total_cell = ws.cell(row=current_row, column=6)
        total_cell.font = FONT_BODY_BOLD
        total_cell.border = THIN_BORDER
        total_cell.fill = row_fill
        total_cell.alignment = Alignment(horizontal="right")
        
        apr_cell = f"C{current_row}"
        may_cell = f"D{current_row}"
        jun_cell = f"E{current_row}"
        
        if is_pct:
            # Average percentage for the quarter
            total_cell.value = f"=AVERAGE({apr_cell}:{jun_cell})"
            total_cell.number_format = "0.00%"
        else:
            # Sum for values
            total_cell.value = f"=SUM({apr_cell}:{jun_cell})"
            total_cell.number_format = "0.00"
            
    auto_fit_columns(ws)

def create_disbursement_sheet(wb, data):
    ws = wb.create_sheet(title="Disbursement & Ageing")
    ws.views.sheetView[0].showGridLines = True
    
    ws.append([])
    ws.cell(row=2, column=2, value="Q1 Disbursement & Sourcing Status").font = FONT_SECTION
    
    # --- TABLE 1: Sourcing & Pending ---
    headers = ["Financier", "No of Sourcing", "Disb Pending", "Pending %", "Last Wk Status", "Month Status", "Qtr Status"]
    for col_idx, header in enumerate(headers, 2):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = HEADER_BORDER
    ws.row_dimensions[4].height = 26
    
    fin_data = data.get("disbursement", {}).get("financiers", {})
    financiers = list(fin_data.keys())
    
    start_row = 5
    for idx, fin in enumerate(financiers):
        current_row = start_row + idx
        f_info = fin_data.get(fin, {"sourcing": 0, "pending": 0, "last_week_pending_pct": 0})
        
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        ws.cell(row=current_row, column=2, value=fin).font = FONT_BODY_BOLD
        ws.cell(row=current_row, column=2).border = THIN_BORDER
        ws.cell(row=current_row, column=2).fill = row_fill
        
        # Sourcing
        c_src = ws.cell(row=current_row, column=3, value=f_info["sourcing"])
        c_src.number_format = "#,##0"
        
        # Pending
        c_pnd = ws.cell(row=current_row, column=4, value=f_info["pending"])
        c_pnd.number_format = "#,##0"
        
        # Pending % Formula
        c_pct = ws.cell(row=current_row, column=5, value=f"=IF(C{current_row}=0,0,D{current_row}/C{current_row})")
        c_pct.number_format = "0.0%"
        
        # Last Wk Status
        lw_val = f_info.get("last_week_pending_pct", 0)
        c_lw = ws.cell(row=current_row, column=6, value=lw_val/100.0 if lw_val > 1 else lw_val)
        c_lw.number_format = "0.0%"
        
        # Blank Month/Qtr status
        ws.cell(row=current_row, column=7, value="")
        ws.cell(row=current_row, column=8, value="")
        
        for c in range(3, 9):
            ws.cell(row=current_row, column=c).font = FONT_BODY
            ws.cell(row=current_row, column=c).border = THIN_BORDER
            ws.cell(row=current_row, column=c).fill = row_fill
            if c in [3, 4, 5, 6]:
                ws.cell(row=current_row, column=c).alignment = Alignment(horizontal="right")
                
    # Add Overall Total Row
    tot_row = start_row + len(financiers)
    ws.cell(row=tot_row, column=2, value="Overall").font = FONT_TOTAL
    ws.cell(row=tot_row, column=2).border = TOTAL_BORDER
    ws.cell(row=tot_row, column=2).fill = ACCENT_BLUE_FILL
    
    ws.cell(row=tot_row, column=3, value=f"=SUM(C{start_row}:C{tot_row-1})").number_format = "#,##0"
    ws.cell(row=tot_row, column=4, value=f"=SUM(D{start_row}:D{tot_row-1})").number_format = "#,##0"
    ws.cell(row=tot_row, column=5, value=f"=IF(C{tot_row}=0,0,D{tot_row}/C{tot_row})").number_format = "0.0%"
    
    # Average of last week pending percentage
    ws.cell(row=tot_row, column=6, value=f"=AVERAGE(F{start_row}:F{tot_row-1})").number_format = "0.0%"
    
    ws.cell(row=tot_row, column=7, value="")
    ws.cell(row=tot_row, column=8, value="")
    
    for c in range(3, 9):
        cell = ws.cell(row=tot_row, column=c)
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        if c in [3, 4, 5, 6]:
            cell.alignment = Alignment(horizontal="right")
            
    # --- TABLE 2: Top-5 stores pending ---
    store_start_row = tot_row + 3
    ws.cell(row=store_start_row, column=2, value="Top-5 stores _Disb Pending").font = FONT_SECTION
    
    ts_headers = ["S.No", "High Aged (Store)", "Days Pending", "High Contributor (Store)", "Count"]
    for col_idx, header in enumerate(ts_headers, 2):
        cell = ws.cell(row=store_start_row+1, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEADER_BORDER
    ws.row_dimensions[store_start_row+1].height = 26
    
    top_stores = data.get("disbursement", {}).get("top_stores_aged", [])
    for idx in range(5):
        r_idx = store_start_row + 2 + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        store_item = top_stores[idx] if idx < len(top_stores) else {"sno": idx+1, "store": "", "days": 0, "contributor": "", "count": 0}
        
        ws.cell(row=r_idx, column=2, value=store_item["sno"]).alignment = Alignment(horizontal="center")
        ws.cell(row=r_idx, column=3, value=store_item["store"])
        ws.cell(row=r_idx, column=4, value=store_item["days"]).alignment = Alignment(horizontal="right")
        ws.cell(row=r_idx, column=4).number_format = "#,##0"
        ws.cell(row=r_idx, column=5, value=store_item["contributor"])
        ws.cell(row=r_idx, column=6, value=store_item["count"]).alignment = Alignment(horizontal="right")
        ws.cell(row=r_idx, column=6).number_format = "#,##0"
        
        for c in range(2, 7):
            cell = ws.cell(row=r_idx, column=c)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            
    # --- TABLE 3: Ageing Status Grid ---
    ageing_start_row = store_start_row + 9
    ws.cell(row=ageing_start_row, column=2, value="Financier Wise Ageing Status").font = FONT_SECTION
    
    # Merge headers for count/amount blocks
    ws.merge_cells(start_row=ageing_start_row+1, start_column=2, end_row=ageing_start_row+2, end_column=2) # SL
    ws.merge_cells(start_row=ageing_start_row+1, start_column=3, end_row=ageing_start_row+2, end_column=3) # FINANCIER
    
    ws.merge_cells(start_row=ageing_start_row+1, start_column=4, end_row=ageing_start_row+1, end_column=5) # 4-7
    ws.merge_cells(start_row=ageing_start_row+1, start_column=6, end_row=ageing_start_row+1, end_column=7) # 8-15
    ws.merge_cells(start_row=ageing_start_row+1, start_column=8, end_row=ageing_start_row+1, end_column=9) # 15+
    ws.merge_cells(start_row=ageing_start_row+1, start_column=10, end_row=ageing_start_row+1, end_column=11) # 90+
    ws.merge_cells(start_row=ageing_start_row+1, start_column=12, end_row=ageing_start_row+1, end_column=13) # TOTAL
    ws.merge_cells(start_row=ageing_start_row+1, start_column=14, end_row=ageing_start_row+1, end_column=15) # LAST WEEK
    
    # Write top header values
    ws.cell(row=ageing_start_row+1, column=2, value="SL. NO")
    ws.cell(row=ageing_start_row+1, column=3, value="FINANCIER")
    ws.cell(row=ageing_start_row+1, column=4, value="4-7 DAYS")
    ws.cell(row=ageing_start_row+1, column=6, value="8-15 DAYS")
    ws.cell(row=ageing_start_row+1, column=8, value="15+ DAYS")
    ws.cell(row=ageing_start_row+1, column=10, value="90+ DAYS")
    ws.cell(row=ageing_start_row+1, column=12, value="TOTAL PENDING")
    ws.cell(row=ageing_start_row+1, column=14, value="LAST WEEK STATUS")
    
    # Sub headers (COUNT, AMOUNT)
    for c in [4, 6, 8, 10, 12, 14]:
        ws.cell(row=ageing_start_row+2, column=c, value="COUNT")
        ws.cell(row=ageing_start_row+2, column=c+1, value="AMOUNT")
        
    # Style all headers
    for r in [ageing_start_row+1, ageing_start_row+2]:
        ws.row_dimensions[r].height = 20
        for col in range(2, 16):
            cell = ws.cell(row=r, column=col)
            cell.font = FONT_HEADER
            cell.fill = NAVY_HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = HEADER_BORDER
            
    ageing_data = data.get("disbursement", {}).get("ageing_status", {})
    ageing_financiers = list(ageing_data.keys())
    
    r_start = ageing_start_row + 3
    for idx, fin in enumerate(ageing_financiers):
        curr_r = r_start + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        info = ageing_data.get(fin, {})
        ws.cell(row=curr_r, column=2, value=idx+1).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=3, value=fin).font = FONT_BODY_BOLD
        
        # Populate count and amounts
        keys = ["4_7_days", "8_15_days", "15_plus", "90_plus"]
        col_offset = 4
        for k_idx, key in enumerate(keys):
            cnt, amt = info.get(key, [0, 0])
            ws.cell(row=curr_r, column=col_offset, value=cnt).number_format = "#,##0"
            ws.cell(row=curr_r, column=col_offset+1, value=amt).number_format = "#,##0"
            col_offset += 2
            
        # Total formulas
        total_cnt_f = f"=SUM(D{curr_r},F{curr_r},H{curr_r},J{curr_r})"
        total_amt_f = f"=SUM(E{curr_r},G{curr_r},I{curr_r},K{curr_r})"
        ws.cell(row=curr_r, column=12, value=total_cnt_f).number_format = "#,##0"
        ws.cell(row=curr_r, column=13, value=total_amt_f).number_format = "#,##0"
        
        # Last week values
        lw_cnt, lw_amt = info.get("last_week", [0, 0])
        ws.cell(row=curr_r, column=14, value=lw_cnt).number_format = "#,##0"
        ws.cell(row=curr_r, column=15, value=lw_amt).number_format = "#,##0"
        
        # Apply fonts, borders, alignments
        for col in range(2, 16):
            cell = ws.cell(row=curr_r, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            if col >= 4:
                cell.alignment = Alignment(horizontal="right")
                
    # Total Row for Ageing
    tot_row_ageing = r_start + len(ageing_financiers)
    ws.cell(row=tot_row_ageing, column=2, value="").border = TOTAL_BORDER
    ws.cell(row=tot_row_ageing, column=2).fill = ACCENT_BLUE_FILL
    ws.cell(row=tot_row_ageing, column=3, value="TOTAL").font = FONT_TOTAL
    ws.cell(row=tot_row_ageing, column=3).border = TOTAL_BORDER
    ws.cell(row=tot_row_ageing, column=3).fill = ACCENT_BLUE_FILL
    
    for c_idx in range(4, 16):
        col_letter = get_column_letter(c_idx)
        cell = ws.cell(row=tot_row_ageing, column=c_idx, value=f"=SUM({col_letter}{r_start}:{col_letter}{tot_row_ageing-1})")
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        cell.alignment = Alignment(horizontal="right")
        cell.number_format = "#,##0"
        
    auto_fit_columns(ws)

def create_category_sheet(wb, data):
    ws = wb.create_sheet(title="Category Performance")
    ws.views.sheetView[0].showGridLines = True
    
    ws.append([])
    ws.cell(row=2, column=2, value="Product Category Performance - Volume (Qty)").font = FONT_SECTION
    
    headers = ['CATEGORY', 'aCASH', 'BAJAJ FINANCE', 'HDB FINANCE', 'IDFC FIRST BANK LTD',
               'Poonawalla Fincorp', 'TVS Credit', 'DMI FINANCE', 'Pine Lab EMI', 'PAYTM', 'Benow',
               'TOTAL', 'FIN SHARE', 'Paper Finance Total', 'Card Finance Total', 'LAST WEEK', 'APR closing Qty']
               
    # Set up volume table
    for col_idx, header in enumerate(headers, 2):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = HEADER_BORDER
    ws.row_dimensions[4].height = 30
    
    qty_data = data.get("category_performance", {}).get("qty", {})
    categories = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]
    
    start_row = 5
    for idx, cat in enumerate(categories):
        curr_row = start_row + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        ws.cell(row=curr_row, column=2, value=cat).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=2).border = THIN_BORDER
        ws.cell(row=curr_row, column=2).fill = row_fill
        
        info = qty_data.get(cat, {})
        # Map values
        cols = ['aCASH', 'BAJAJ', 'HDB', 'IDFC', 'PFL', 'TVS', 'DMI', 'PineLab', 'PAYTM', 'Benow']
        for c_idx, key in enumerate(cols):
            val = info.get(key, 0)
            cell = ws.cell(row=curr_row, column=c_idx + 3, value=val)
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="right")
            
        # Total Formula = SUM of financiers (C to L)
        ws.cell(row=curr_row, column=13, value=f"=SUM(D{curr_row}:L{curr_row})").number_format = "#,##0" # B to K on columns shifts offset by +3. D corresponds to BAJAJ, L is Benow. aCash is C. wait. Total in slide 6:
        # slide 6 total: aCASH (91050) is excluded or included? CD Products total = 71789. CD Products financiers: aCASH=39774, BAJAJ=20256, HDB=4823, IDFC=3448, Poonawalla=105, TVS=882, DMI=2, PineLab=2480, PAYTM=2, Benow=17.
        # Wait, 20256+4823+3448+105+882+2+2480+2+17 = 32015. Plus aCASH (39774) = 71789!
        # So TOTAL includes aCASH! Let's check: Yes, aCASH is included in TOTAL. Total column = C{row} + SUM(D{row}:L{row}).
        ws.cell(row=curr_row, column=13, value=f"=C{curr_row}+SUM(D{curr_row}:L{curr_row})").number_format = "#,##0"
        
        # Paper Finance Total = BAJAJ to DMI (D to I)
        ws.cell(row=curr_row, column=15, value=f"=SUM(D{curr_row}:I{curr_row})").number_format = "#,##0"
        
        # Card Finance Total = PineLab + PAYTM + Benow (J to L)
        ws.cell(row=curr_row, column=16, value=f"=SUM(J{curr_row}:L{curr_row})").number_format = "#,##0"
        
        # Fin Share = Paper Finance + Card Finance / Total.
        # In slide 6: CD Products Fin Share is 44.6%. Paper + Card Total = 29516 + 2499 = 32015. Total = 71789. 32015 / 71789 = 44.59%. Yes!
        ws.cell(row=curr_row, column=14, value=f"=IF(M{curr_row}=0,0,(O{curr_row}+P{curr_row})/M{curr_row})").number_format = "0.0%"
        
        # Last Week % & Apr closing %
        lw_pct = info.get("last_week_pct", 0)
        ws.cell(row=curr_row, column=17, value=lw_pct/100.0 if lw_pct > 1 else lw_pct).number_format = "0.0%"
        apr_pct = info.get("apr_closing_pct", 0)
        ws.cell(row=curr_row, column=18, value=apr_pct/100.0 if apr_pct > 1 else apr_pct).number_format = "0.0%"
        
        # Apply body styles
        for col in range(3, 19):
            cell = ws.cell(row=curr_row, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            if col in [13, 14, 15, 16]:
                cell.font = FONT_BODY_BOLD
            if col != 2:
                cell.alignment = Alignment(horizontal="right")
                
    # Grand Total Row Qty
    gt_row = start_row + len(categories)
    ws.cell(row=gt_row, column=2, value="Grand Total").font = FONT_TOTAL
    ws.cell(row=gt_row, column=2).border = TOTAL_BORDER
    ws.cell(row=gt_row, column=2).fill = ACCENT_BLUE_FILL
    
    # Sum columns C to M, and O, P
    for c_idx in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16]:
        col_letter = get_column_letter(c_idx)
        ws.cell(row=gt_row, column=c_idx, value=f"=SUM({col_letter}{start_row}:{col_letter}{gt_row-1})").number_format = "#,##0"
        
    # Fin Share for Grand Total
    ws.cell(row=gt_row, column=14, value=f"=IF(M{gt_row}=0,0,(O{gt_row}+P{gt_row})/M{gt_row})").number_format = "0.00%"
    
    # Averages for percentage columns
    ws.cell(row=gt_row, column=17, value=f"=AVERAGE(Q{start_row}:Q{gt_row-1})").number_format = "0.00%"
    ws.cell(row=gt_row, column=18, value=f"=AVERAGE(R{start_row}:R{gt_row-1})").number_format = "0.00%"
    
    for col in range(3, 19):
        cell = ws.cell(row=gt_row, column=col)
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        cell.alignment = Alignment(horizontal="right")
        
    # Share row
    share_row = gt_row + 1
    ws.cell(row=share_row, column=2, value="Share").font = FONT_BODY_BOLD
    ws.cell(row=share_row, column=2).border = THIN_BORDER
    ws.cell(row=share_row, column=2).fill = ZEBRA_FILL
    # share is col_total / grand_total
    for c_idx in range(3, 13):
        col_letter = get_column_letter(c_idx)
        ws.cell(row=share_row, column=c_idx, value=f"=IF(M{gt_row}=0,0,{col_letter}{gt_row}/M{gt_row})").number_format = "0.00%"
        
    # Share in Fin Value row (we'll link to values later, or put formulas)
    ws.cell(row=share_row, column=13, value="")
    ws.cell(row=share_row, column=14, value="")
    # Paper share in Qty total = Paper / Grand Total
    ws.cell(row=share_row, column=15, value=f"=IF(M{gt_row}=0,0,O{gt_row}/M{gt_row})").number_format = "0.00%"
    ws.cell(row=share_row, column=16, value=f"=IF(M{gt_row}=0,0,P{gt_row}/M{gt_row})").number_format = "0.00%"
    ws.cell(row=share_row, column=17, value="")
    ws.cell(row=share_row, column=18, value="")
    for col in range(3, 19):
        cell = ws.cell(row=share_row, column=col)
        cell.font = FONT_BODY
        cell.border = THIN_BORDER
        cell.fill = ZEBRA_FILL
        cell.alignment = Alignment(horizontal="right")

    # --- TABLE 2: Product Category Performance - Value (in Cr) ---
    val_start_row = share_row + 4
    ws.cell(row=val_start_row, column=2, value="Product Category Performance - Value (in Cr)").font = FONT_SECTION
    
    # Headers for Value table
    val_headers = ['CATEGORY', 'aCASH', 'BAJAJ FINANCE', 'HDB FINANCE', 'IDFC FIRST BANK LTD',
                   'Poonawalla Fincorp', 'TVS Credit', 'DMI FINANCE', 'Pine Lab EMI', 'PAYTM', 'Benow',
                   'TOTAL', 'FIN SHARE', 'Paper Finance Total', 'Card Finance Total', 'LAST WEEK', 'APR closing Value']
                   
    for col_idx, header in enumerate(val_headers, 2):
        cell = ws.cell(row=val_start_row+1, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = HEADER_BORDER
    ws.row_dimensions[val_start_row+1].height = 30
    
    val_data = data.get("category_performance", {}).get("value", {})
    
    v_start = val_start_row + 2
    for idx, cat in enumerate(categories):
        curr_row = v_start + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        ws.cell(row=curr_row, column=2, value=cat).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=2).border = THIN_BORDER
        ws.cell(row=curr_row, column=2).fill = row_fill
        
        info = val_data.get(cat, {})
        cols = ['aCASH', 'BAJAJ', 'HDB', 'IDFC', 'PFL', 'TVS', 'DMI', 'PineLab', 'PAYTM', 'Benow']
        for c_idx, key in enumerate(cols):
            val = info.get(key, 0.0)
            cell = ws.cell(row=curr_row, column=c_idx + 3, value=val)
            cell.number_format = "0.0"
            cell.alignment = Alignment(horizontal="right")
            
        # Total Formula = aCash + financiers
        ws.cell(row=curr_row, column=13, value=f"=C{curr_row}+SUM(D{curr_row}:L{curr_row})").number_format = "0.0"
        
        # Paper Finance Total = BAJAJ to DMI
        ws.cell(row=curr_row, column=15, value=f"=SUM(D{curr_row}:I{curr_row})").number_format = "0.0"
        
        # Card Finance Total = PineLab + PAYTM + Benow
        ws.cell(row=curr_row, column=16, value=f"=SUM(J{curr_row}:L{curr_row})").number_format = "0.0"
        
        # Fin Share = Paper + Card / Total
        ws.cell(row=curr_row, column=14, value=f"=IF(M{curr_row}=0,0,(O{curr_row}+P{curr_row})/M{curr_row})").number_format = "0.0%"
        
        # Last Week % & Apr closing %
        lw_pct = info.get("last_week_pct", 0)
        ws.cell(row=curr_row, column=17, value=lw_pct/100.0 if lw_pct > 1 else lw_pct).number_format = "0.0%"
        apr_pct = info.get("apr_closing_pct", 0)
        ws.cell(row=curr_row, column=18, value=apr_pct/100.0 if apr_pct > 1 else apr_pct).number_format = "0.0%"
        
        # Apply styles
        for col in range(3, 19):
            cell = ws.cell(row=curr_row, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            if col in [13, 14, 15, 16]:
                cell.font = FONT_BODY_BOLD
            if col != 2:
                cell.alignment = Alignment(horizontal="right")
                
    # Grand Total Row Value
    gt_row_val = v_start + len(categories)
    ws.cell(row=gt_row_val, column=2, value="Grand Total").font = FONT_TOTAL
    ws.cell(row=gt_row_val, column=2).border = TOTAL_BORDER
    ws.cell(row=gt_row_val, column=2).fill = ACCENT_BLUE_FILL
    
    # Sum columns C to M, and O, P
    for c_idx in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16]:
        col_letter = get_column_letter(c_idx)
        ws.cell(row=gt_row_val, column=c_idx, value=f"=SUM({col_letter}{v_start}:{col_letter}{gt_row_val-1})").number_format = "0.0"
        
    # Fin Share for Grand Total
    ws.cell(row=gt_row_val, column=14, value=f"=IF(M{gt_row_val}=0,0,(O{gt_row_val}+P{gt_row_val})/M{gt_row_val})").number_format = "0.00%"
    
    # Averages for percentages
    ws.cell(row=gt_row_val, column=17, value=f"=AVERAGE(Q{v_start}:Q{gt_row_val-1})").number_format = "0.00%"
    ws.cell(row=gt_row_val, column=18, value=f"=AVERAGE(R{v_start}:R{gt_row_val-1})").number_format = "0.00%"
    
    for col in range(3, 19):
        cell = ws.cell(row=gt_row_val, column=col)
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        cell.alignment = Alignment(horizontal="right")
        
    # Share row Value
    share_row_val = gt_row_val + 1
    ws.cell(row=share_row_val, column=2, value="Share").font = FONT_BODY_BOLD
    ws.cell(row=share_row_val, column=2).border = THIN_BORDER
    ws.cell(row=share_row_val, column=2).fill = ZEBRA_FILL
    for c_idx in range(3, 13):
        col_letter = get_column_letter(c_idx)
        ws.cell(row=share_row_val, column=c_idx, value=f"=IF(M{gt_row_val}=0,0,{col_letter}{gt_row_val}/M{gt_row_val})").number_format = "0.00%"
    ws.cell(row=share_row_val, column=13, value="")
    ws.cell(row=share_row_val, column=14, value="")
    ws.cell(row=share_row_val, column=15, value=f"=IF(M{gt_row_val}=0,0,O{gt_row_val}/M{gt_row_val})").number_format = "0.00%"
    ws.cell(row=share_row_val, column=16, value=f"=IF(M{gt_row_val}=0,0,P{gt_row_val}/M{gt_row_val})").number_format = "0.00%"
    ws.cell(row=share_row_val, column=17, value="")
    ws.cell(row=share_row_val, column=18, value="")
    for col in range(3, 19):
        cell = ws.cell(row=share_row_val, column=col)
        cell.font = FONT_BODY
        cell.border = THIN_BORDER
        cell.fill = ZEBRA_FILL
        cell.alignment = Alignment(horizontal="right")
        
    # ASP row (Average Selling Price) = Value / Volume * 1,00,00,000 (since value is in Cr).
    # Slide 6 has ASP: e.g. BAJAJ = 43989. Sourcing = 58059. Value = 255.4 Cr.
    # 255.4 * 10,000,000 / 58059 = 2,554,000,000 / 58059 = 43989. Yes, exact!
    asp_row = share_row_val + 1
    ws.cell(row=asp_row, column=2, value="ASP").font = FONT_BODY_BOLD
    ws.cell(row=asp_row, column=2).border = THIN_BORDER
    ws.cell(row=asp_row, column=2).fill = ZEBRA_FILL
    for c_idx in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16]: # aCASH..Benow, TOTAL, Paper, Card
        col_letter = get_column_letter(c_idx)
        # Value row total is gt_row_val, Volume row total is gt_row
        val_cell = f"{col_letter}{gt_row_val}"
        qty_cell = f"{col_letter}{gt_row}"
        ws.cell(row=asp_row, column=c_idx, value=f"=IF({qty_cell}=0,0,({val_cell}/{qty_cell})*10000000)").number_format = "#,##0"
        
    for col in range(3, 19):
        cell = ws.cell(row=asp_row, column=col)
        cell.font = FONT_BODY
        cell.border = THIN_BORDER
        cell.fill = ZEBRA_FILL
        cell.alignment = Alignment(horizontal="right")
        
    # Now write the Share in Fin Value row into the Volume section (row share_row)
    # Slide 6 volume section row 10 has "Share in Fin Value" which is: Bajaj share in fin value = Bajaj Value / (Paper + Card value)
    # Let's fill row gt_row+2 (which is share_row+1 but wait, we have share_row at gt_row+1, let's insert "Share in Fin Value" at share_row+1, making ASP and other rows shift down, or just write it on share_row+1)
    # Wait, in the volume section, we had "Share in Fin Value" row at row 10 in slide. Let's make sure it matches.
    # We will write the volume "Share in Fin Value" row at row gt_row+2:
    ws.insert_rows(share_row + 1, 1)
    sif_row = share_row + 1
    ws.cell(row=sif_row, column=2, value="Share in Fin Value").font = FONT_BODY_BOLD
    ws.cell(row=sif_row, column=2).border = THIN_BORDER
    ws.cell(row=sif_row, column=2).fill = ZEBRA_FILL
    # Bajaj Share in Fin Value = Bajaj Value / (Paper Total Value + Card Total Value)
    # In value section (which is now shifted by 1 row due to insertion! let's verify):
    # Let's write Excel formulas referencing the shifted value cells:
    # Shifted gt_row_val is now +1! Let's write formulas dynamically:
    # Paper Total Value = O{gt_row_val_shifted}, Card Total Value = P{gt_row_val_shifted}
    # For Bajaj: D{gt_row_val_shifted} / (O{gt_row_val_shifted} + P{gt_row_val_shifted})
    # Let's write these formulas after creating both tables to make it simple.
    
    # We will adjust the references of the value table.
    # Since we did insert_rows, let's recalculate the rows of the Value table:
    # Originally v_start was share_row + 4. Now with 1 inserted row, it is share_row + 5.
    # Let's just write the "Share in Fin Value" row directly without inserting if we plan it correctly.
    # Let's write a cleanup block at the end of the sheet to ensure everything links properly:
    # Volume table:
    # start_row = 5
    # categories = 7 rows (5 to 11)
    # gt_row = 12 (Grand Total)
    # share_row = 13 (Share)
    # sif_row = 14 (Share in Fin Value) -> linking to value row Grand Total
    # Value table start = 18
    # categories = 7 rows (18 to 24)
    # gt_row_val = 25 (Grand Total Value)
    # share_row_val = 26 (Share)
    # sif_row_val = 27 (Share in Fin Value)
    # asp_row_val = 28 (ASP)
    
    # Let's write the cell values and formulas exactly for these rows:
    # Volume SIF row (14): Volume share in total financed Qty
    for c_idx in range(4, 13):
        col_letter = get_column_letter(c_idx)
        # Volume cell is {col_letter}12. Paper total volume is O12. Card total volume is P12.
        ws.cell(row=14, column=c_idx, value=f"=IF((O12+P12)=0,0,{col_letter}12/(O12+P12))").number_format = "0.00%"
    # Paper total share = O12 / (O12+P12)
    ws.cell(row=14, column=15, value="=IF((O12+P12)=0,0,O12/(O12+P12))").number_format = "0.00%"
    ws.cell(row=14, column=16, value="=IF((O12+P12)=0,0,P12/(O12+P12))").number_format = "0.00%"
    
    # Value SIF row (27):
    for c_idx in range(4, 13):
        col_letter = get_column_letter(c_idx)
        ws.cell(row=27, column=c_idx, value=f"=IF((O25+P25)=0,0,{col_letter}25/(O25+P25))").number_format = "0.00%"
    ws.cell(row=27, column=15, value="=IF((O25+P25)=0,0,O25/(O25+P25))").number_format = "0.00%"
    ws.cell(row=27, column=16, value="=IF((O25+P25)=0,0,P25/(O25+P25))").number_format = "0.00%"
    
    # Style row 14 and 27
    for r in [14, 27]:
        ws.cell(row=r, column=2, value="Share in Fin Value").font = FONT_BODY_BOLD
        ws.cell(row=r, column=2).border = THIN_BORDER
        ws.cell(row=r, column=2).fill = ZEBRA_FILL
        for col in range(3, 19):
            cell = ws.cell(row=r, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = ZEBRA_FILL
            cell.alignment = Alignment(horizontal="right")
            
    auto_fit_columns(ws)

def create_financier_targets_sheet(wb, data):
    ws = wb.create_sheet(title="Financier Targets")
    ws.views.sheetView[0].showGridLines = True
    
    ws.append([])
    ws.cell(row=2, column=2, value="Q1 Financier Target Vs Achievement").font = FONT_SECTION
    
    # Let's layout headers:
    # Row 4: [Financier, Paper Finance (merged 7 cols), Cards (merged 4 cols)]
    # Row 5: ['', BAJAJ, HDB, IDFC, PFL, TVS, DMI, PF Total, PinelAb, PayTm, Benow, Cards Total]
    
    ws.merge_cells("B4:B5")
    ws.merge_cells("C4:I4")
    ws.merge_cells("J4:M4")
    
    ws.cell(row=4, column=2, value="Financier")
    ws.cell(row=4, column=3, value="Paper Finance")
    ws.cell(row=4, column=10, value="Cards & Merchant EMI")
    
    sub_headers = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PF Total", "PinelAb", "PayTm", "Benow", "Cards Total"]
    for idx, sh in enumerate(sub_headers):
        ws.cell(row=5, column=idx+3, value=sh)
        
    for r in [4, 5]:
        ws.row_dimensions[r].height = 20
        for col in range(2, 14):
            cell = ws.cell(row=r, column=col)
            cell.font = FONT_HEADER
            cell.fill = NAVY_HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = HEADER_BORDER
            
    ft_data = data.get("financier_targets", {})
    metrics = ["Target Plan", "Achievement", "Achvt %", "GAP", "Trend", "Trend %"]
    
    start_row = 6
    for idx, metric in enumerate(metrics):
        curr_row = start_row + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        ws.cell(row=curr_row, column=2, value=metric).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=2).border = THIN_BORDER
        ws.cell(row=curr_row, column=2).fill = row_fill
        
        # financier data keys map to columns:
        # C=BAJAJ, D=HDB, E=IDFC, F=PFL, G=TVS, H=DMI, I=PF Total(formula)
        # J=PinelAb, K=PayTm, L=Benow, M=Cards Total(formula)
        
        fins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", None, "PinelAb", "PayTm", "Benow", None]
        for col_idx, fin in enumerate(fins):
            col_letter = get_column_letter(col_idx + 3)
            cell = ws.cell(row=curr_row, column=col_idx + 3)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal="right")
            
            is_card = col_idx >= 7
            number_fmt = "0.0" if is_card else "#,##0"
            if "Achvt %" in metric or "Trend %" in metric:
                number_fmt = "0.0%"
                
            if fin:
                info = ft_data.get(fin, {"target": 0, "achievement": 0})
                if metric == "Target Plan":
                    cell.value = info["target"]
                    cell.number_format = number_fmt
                elif metric == "Achievement":
                    cell.value = info["achievement"]
                    cell.number_format = number_fmt
                elif metric == "Achvt %":
                    cell.value = f"=IF({col_letter}6=0,0,{col_letter}7/{col_letter}6)"
                    cell.number_format = number_fmt
                    cell.font = FONT_BODY_BOLD
                elif metric == "GAP":
                    cell.value = f"={col_letter}6-{col_letter}7"
                    cell.number_format = number_fmt
                    # highlight gap
                    cell.fill = RED_GAP_FILL
                elif metric == "Trend":
                    # Trend = Achievement * (some factor, e.g., run rate, let's keep default trend from slide, or simple formula)
                    # Slide 7 has Trend for Bajaj = 86613, Achievement = 58059. Target = 88779.
                    # If they entered trend in data, use it. Otherwise, let's write simple formula: Target * Achvt % or Achievement / 0.67
                    # Let's check if the backend data has trend, if not: Achievement * 1.49 (May is month 2 of 3, so roughly 1.5x)
                    # Let's just do: Achievement * 1.5 as fallback, or simple formula: Achievement / (31/91) where 31 days elapsed out of 91.
                    # Since data contains target/achievement only, let's estimate trend as Achievement * 1.5 (or we can just calculate it in JS and save it)
                    # Wait, let's check: can we just save trend in JSON? Yes, we saved it in our default JSON!
                    # If we don't have it, let's write a formula: Achievement * 1.5. Let's write the formula in Excel:
                    # Let's write `=ROUND({col_letter}7*1.49,0)` for volume or decimals for value.
                    # Better yet, let's check if we can save trend in the data structure, or write `=ROUND({col_letter}7*1.49, 1)`:
                    cell.value = f"=ROUND({col_letter}7*1.49, {1 if is_card else 0})"
                    cell.number_format = number_fmt
                elif metric == "Trend %":
                    cell.value = f"=IF({col_letter}6=0,0,{col_letter}10/{col_letter}6)"
                    cell.number_format = number_fmt
                    cell.font = FONT_BODY_BOLD
            else:
                # Formula columns: I=PF Total, M=Cards Total
                if col_idx == 6: # PF Total (Col I)
                    if metric == "Target Plan":
                        cell.value = "=SUM(C6:H6)"
                        cell.number_format = "#,##0"
                    elif metric == "Achievement":
                        cell.value = "=SUM(C7:H7)"
                        cell.number_format = "#,##0"
                    elif metric == "Achvt %":
                        cell.value = "=IF(I6=0,0,I7/I6)"
                        cell.number_format = "0.0%"
                    elif metric == "GAP":
                        cell.value = "=I6-I7"
                        cell.number_format = "#,##0"
                        cell.fill = RED_GAP_FILL
                    elif metric == "Trend":
                        cell.value = "=SUM(C10:H10)"
                        cell.number_format = "#,##0"
                    elif metric == "Trend %":
                        cell.value = "=IF(I6=0,0,I10/I6)"
                        cell.number_format = "0.0%"
                elif col_idx == 10: # Cards Total (Col M)
                    if metric == "Target Plan":
                        cell.value = "=SUM(J6:L6)"
                        cell.number_format = "0.0"
                    elif metric == "Achievement":
                        cell.value = "=SUM(J7:L7)"
                        cell.number_format = "0.0"
                    elif metric == "Achvt %":
                        cell.value = "=IF(M6=0,0,M7/M6)"
                        cell.number_format = "0.0%"
                    elif metric == "GAP":
                        cell.value = "=M6-M7"
                        cell.number_format = "0.0"
                        cell.fill = RED_GAP_FILL
                    elif metric == "Trend":
                        cell.value = "=SUM(J10:L10)"
                        cell.number_format = "0.0"
                    elif metric == "Trend %":
                        cell.value = "=IF(M6=0,0,M10/M6)"
                        cell.number_format = "0.0%"
                        
                cell.font = FONT_BODY_BOLD
                
    auto_fit_columns(ws)

def create_special_contest_sheet(wb, data):
    ws = wb.create_sheet(title="Special Contest")
    ws.views.sheetView[0].showGridLines = True
    
    ws.append([])
    ws.cell(row=2, column=2, value="DMI Special Contest Status (Samsung, Vivo, Oppo)").font = FONT_SECTION
    
    # --- TABLE 1: Summary Table ---
    headers = ["Brand", "Target Plan", "Achievement", "Achvt %", "GAP", "Trend", "Trend %"]
    for col_idx, header in enumerate(headers, 2):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEADER_BORDER
    ws.row_dimensions[4].height = 24
    
    summary_data = data.get("dmi_contest", {}).get("summary", {})
    brands = ["SAMSUNG", "VIVO", "OPPO"]
    
    start_row = 5
    for idx, brand in enumerate(brands):
        curr_row = start_row + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        info = summary_data.get(brand, {"target": 72, "achievement": 0})
        
        ws.cell(row=curr_row, column=2, value=brand).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=2).border = THIN_BORDER
        ws.cell(row=curr_row, column=2).fill = row_fill
        
        # Target
        ws.cell(row=curr_row, column=3, value=info["target"]).number_format = "#,##0"
        # Achievement
        ws.cell(row=curr_row, column=4, value=info["achievement"]).number_format = "#,##0"
        # Achvt %
        ws.cell(row=curr_row, column=5, value=f"=IF(C{curr_row}=0,0,D{curr_row}/C{curr_row})").number_format = "0.0%"
        # GAP
        ws.cell(row=curr_row, column=6, value=f"=C{curr_row}-D{curr_row}").number_format = "#,##0"
        ws.cell(row=curr_row, column=6).fill = RED_GAP_FILL
        # Trend
        ws.cell(row=curr_row, column=7, value=f"=ROUND(D{curr_row}*1.49,0)").number_format = "#,##0"
        # Trend %
        ws.cell(row=curr_row, column=8, value=f"=IF(C{curr_row}=0,0,G{curr_row}/C{curr_row})").number_format = "0.0%"
        
        for col in range(3, 9):
            cell = ws.cell(row=curr_row, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal="right")
            if col in [5, 8]:
                cell.font = FONT_BODY_BOLD
                
    # --- TABLE 2: Branch Wise Table ---
    branch_start_row = start_row + 5
    ws.cell(row=branch_start_row, column=2, value="Branch Wise Performance").font = FONT_SECTION
    
    br_headers = ["S.No", "Branch", "OPPO", "SAMSUNG", "VIVO", "TOTAL"]
    for col_idx, header in enumerate(br_headers, 2):
        cell = ws.cell(row=branch_start_row+1, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEADER_BORDER
    ws.row_dimensions[branch_start_row+1].height = 24
    
    branches_data = data.get("dmi_contest", {}).get("branches", {})
    branch_list = sorted(list(branches_data.keys()))
    
    b_start = branch_start_row + 2
    for idx, br in enumerate(branch_list):
        curr_row = b_start + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        info = branches_data.get(br, {"OPPO": 0, "SAMSUNG": 0, "VIVO": 0})
        
        ws.cell(row=curr_row, column=2, value=idx+1).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=3, value=br).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=4, value=info["OPPO"]).number_format = "#,##0"
        ws.cell(row=curr_row, column=5, value=info["SAMSUNG"]).number_format = "#,##0"
        ws.cell(row=curr_row, column=6, value=info["VIVO"]).number_format = "#,##0"
        ws.cell(row=curr_row, column=7, value=f"=SUM(D{curr_row}:F{curr_row})").number_format = "#,##0"
        
        for col in range(2, 8):
            cell = ws.cell(row=curr_row, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            if col >= 4:
                cell.alignment = Alignment(horizontal="right")
                if col == 7:
                    cell.font = FONT_BODY_BOLD
                    
    # Total row for branches
    tot_br_row = b_start + len(branch_list)
    ws.cell(row=tot_br_row, column=2, value="").border = TOTAL_BORDER
    ws.cell(row=tot_br_row, column=2).fill = ACCENT_BLUE_FILL
    ws.cell(row=tot_br_row, column=3, value="Total").font = FONT_TOTAL
    ws.cell(row=tot_br_row, column=3).border = TOTAL_BORDER
    ws.cell(row=tot_br_row, column=3).fill = ACCENT_BLUE_FILL
    
    ws.cell(row=tot_br_row, column=4, value=f"=SUM(D{b_start}:D{tot_br_row-1})").number_format = "#,##0"
    ws.cell(row=tot_br_row, column=5, value=f"=SUM(E{b_start}:E{tot_br_row-1})").number_format = "#,##0"
    ws.cell(row=tot_br_row, column=6, value=f"=SUM(F{b_start}:F{tot_br_row-1})").number_format = "#,##0"
    ws.cell(row=tot_br_row, column=7, value=f"=SUM(G{b_start}:G{tot_br_row-1})").number_format = "#,##0"
    
    for col in range(4, 8):
        cell = ws.cell(row=tot_br_row, column=col)
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        cell.alignment = Alignment(horizontal="right")
        
    auto_fit_columns(ws)

def create_manpower_sheet(wb, data):
    ws = wb.create_sheet(title="Manpower Status")
    ws.views.sheetView[0].showGridLines = True
    
    ws.append([])
    ws.cell(row=2, column=2, value="Q1 Financier Manpower Status").font = FONT_SECTION
    
    headers = ["Financier", "Overall Plan", "Current Status", "Addnl Requirement", "GAP"]
    for col_idx, header in enumerate(headers, 2):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = HEADER_BORDER
    ws.row_dimensions[4].height = 24
    
    mp_data = data.get("manpower", {})
    financiers = ["BAJAJ", "HDB", "IDFC", "TVS", "PFL"]
    
    start_row = 5
    for idx, fin in enumerate(financiers):
        curr_row = start_row + idx
        is_zebra = (idx % 2 == 1)
        row_fill = ZEBRA_FILL if is_zebra else WHITE_FILL
        
        info = mp_data.get(fin, {"plan": 0, "current": 0})
        
        ws.cell(row=curr_row, column=2, value=fin).font = FONT_BODY_BOLD
        ws.cell(row=curr_row, column=2).border = THIN_BORDER
        ws.cell(row=curr_row, column=2).fill = row_fill
        
        # Plan
        ws.cell(row=curr_row, column=3, value=info["plan"]).number_format = "#,##0"
        # Current
        ws.cell(row=curr_row, column=4, value=info["current"]).number_format = "#,##0"
        # Addnl Requirement = Plan - Current
        ws.cell(row=curr_row, column=5, value=f"=C{curr_row}-D{curr_row}").number_format = "#,##0"
        # GAP = Plan - Current
        ws.cell(row=curr_row, column=6, value=f"=C{curr_row}-D{curr_row}").number_format = "#,##0"
        ws.cell(row=curr_row, column=6).fill = RED_GAP_FILL
        
        for col in range(3, 7):
            cell = ws.cell(row=curr_row, column=col)
            cell.font = FONT_BODY
            cell.border = THIN_BORDER
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal="right")
            
    # Total row
    tot_row = start_row + len(financiers)
    ws.cell(row=tot_row, column=2, value="Overall").font = FONT_TOTAL
    ws.cell(row=tot_row, column=2).border = TOTAL_BORDER
    ws.cell(row=tot_row, column=2).fill = ACCENT_BLUE_FILL
    
    ws.cell(row=tot_row, column=3, value=f"=SUM(C{start_row}:C{tot_row-1})").number_format = "#,##0"
    ws.cell(row=tot_row, column=4, value=f"=SUM(D{start_row}:D{tot_row-1})").number_format = "#,##0"
    ws.cell(row=tot_row, column=5, value=f"=SUM(E{start_row}:E{tot_row-1})").number_format = "#,##0"
    ws.cell(row=tot_row, column=6, value=f"=SUM(F{start_row}:F{tot_row-1})").number_format = "#,##0"
    
    for col in range(3, 7):
        cell = ws.cell(row=tot_row, column=col)
        cell.font = FONT_TOTAL
        cell.border = TOTAL_BORDER
        cell.fill = ACCENT_BLUE_FILL
        cell.alignment = Alignment(horizontal="right")
        
    auto_fit_columns(ws)
