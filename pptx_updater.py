import os
import re
from pptx import Presentation
from datetime import datetime

def format_date_pptx(date_str):
    # Converts 2026-06-02 to 02-June-2026
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%B-%Y")

def get_pct_str(val):
    if val is None or val == "":
        return ""
    # if it's already a string, return
    if isinstance(val, str):
        return val
    # Format float like 68.76 as 68.76% or 0.6876 as 68.8%
    if val < 1.0 and val > 0:
        return f"{val*100:.2f}%"
    return f"{val:.2f}%"

def get_value_ach_str(ach, tgt):
    # Formats like 407.89(88.67%)
    if tgt == 0:
        return f"{ach} (0.00%)"
    pct = (ach / tgt) * 100
    return f"{ach} ({pct:.2f}%)"

def update_cell_first_run(cell, text):
    # Sets text in the first run of the first paragraph to preserve font styles
    if not cell.text_frame.paragraphs:
        cell.text_frame.add_paragraph()
    p = cell.text_frame.paragraphs[0]
    if p.runs:
        p.runs[0].text = str(text)
        # Clear other runs to avoid duplicates
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.text = str(text)

def update_pptx_slides(template_path, data, output_path):
    prs = Presentation(template_path)
    
    date_str = data.get("date", "2026-06-02")
    formatted_date = format_date_pptx(date_str)
    
    # -------------------------------------------------------------
    # Slide 1: Update Date Title
    # -------------------------------------------------------------
    slide1 = prs.slides[0]
    for shape in slide1.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                # Look for a date string like "02-June-2026" or similar format
                if re.search(r'\d{2}-[a-zA-Z]+-\d{4}', p.text) or "2026" in p.text:
                    # Let's replace the whole text with formatted date if it contains June-2026 or similar
                    if len(p.text) < 25: # safe length check to avoid replacing whole descriptions
                        p.text = formatted_date

    # -------------------------------------------------------------
    # Slide 2 & Slide 16 (Backup): Target Achievement
    # -------------------------------------------------------------
    # Slide 2: Q1 Performance
    # Slide 3: Monthwise Performance
    # Slide 4: Disbursement status & Top-5 stores
    # Slide 5: Ageing status
    # Slide 6: Product Category Performance
    # Slide 7: Financier Target Vs Achievement
    # Slide 8: Special Contest summary
    # Slide 9: Branch-wise DMI contest tables
    # Slide 10: Manpower status
    # Slide 16 & 17: Backups of Disbursement & Ageing
    
    for slide_idx, slide in enumerate(prs.slides, 1):
        # Update any status date string "Status As on 31-05-2026" or similar
        status_as_of_str = data.get("status_as_of", date_str)
        try:
            status_date_formatted = datetime.strptime(status_as_of_str, "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            status_date_formatted = status_as_of_str
            
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    if "Status As on" in p.text or "Status as on" in p.text:
                        p.text = f"Status As on {status_date_formatted}"
                    elif "Disbursement Status As on" in p.text:
                        p.text = f"Disbursement Status As on {status_date_formatted}"
                        
            # If shape is a table, process it
            if shape.has_table:
                table = shape.table
                rows = len(table.rows)
                cols = len(table.columns)
                
                # Identify tables by shape size, headers, and slide index
                
                # --- Slide 2 Target Achievement (9 rows x 6 columns) ---
                if slide_idx == 2 and rows == 9 and cols == 6:
                    ta_data = data.get("exec_summary", {}).get("target_achievement", {})
                    # Row mappings: Target (row 2), Ach (row 3), LW (row 4), LM (row 5)
                    tgt = ta_data.get("target", [0]*5)
                    ach = ta_data.get("achievement", [0]*5)
                    lw = ta_data.get("last_week", [0]*5)
                    lm = ta_data.get("last_month", [0]*5)
                    trend = ta_data.get("qtr_trend", [None]*5)
                    
                    # Row 2: Target
                    update_cell_first_run(table.cell(2, 1), get_pct_str(tgt[0]))
                    update_cell_first_run(table.cell(2, 2), get_pct_str(tgt[1]))
                    update_cell_first_run(table.cell(2, 3), get_pct_str(tgt[2]))
                    update_cell_first_run(table.cell(2, 4), f"{tgt[3]}")
                    update_cell_first_run(table.cell(2, 5), f"{tgt[4]}")
                    
                    # Row 3: Achievement
                    update_cell_first_run(table.cell(3, 1), get_pct_str(ach[0]))
                    update_cell_first_run(table.cell(3, 2), get_pct_str(ach[1]))
                    update_cell_first_run(table.cell(3, 3), get_pct_str(ach[2]))
                    update_cell_first_run(table.cell(3, 4), get_value_ach_str(ach[3], tgt[3]))
                    update_cell_first_run(table.cell(3, 5), get_value_ach_str(ach[4], tgt[4]))
                    
                    # Row 4: Last Week Status
                    update_cell_first_run(table.cell(4, 1), get_pct_str(lw[0]))
                    update_cell_first_run(table.cell(4, 2), get_pct_str(lw[1]))
                    update_cell_first_run(table.cell(4, 3), get_pct_str(lw[2]))
                    update_cell_first_run(table.cell(4, 4), get_value_ach_str(lw[3], tgt[3]))
                    update_cell_first_run(table.cell(4, 5), get_value_ach_str(lw[4], tgt[4]))
                    
                    # Row 5: Last Month Status
                    update_cell_first_run(table.cell(5, 1), get_pct_str(lm[0]))
                    update_cell_first_run(table.cell(5, 2), get_pct_str(lm[1]))
                    update_cell_first_run(table.cell(5, 3), get_pct_str(lm[2]))
                    update_cell_first_run(table.cell(5, 4), get_value_ach_str(lm[3], tgt[3]))
                    update_cell_first_run(table.cell(5, 5), get_value_ach_str(lm[4], tgt[4]))
                    
                    # Row 6: G/DG* (Growth = Ach - Last Week)
                    update_cell_first_run(table.cell(6, 1), get_pct_str(ach[0] - lw[0]))
                    update_cell_first_run(table.cell(6, 2), get_pct_str(ach[1] - lw[1]))
                    update_cell_first_run(table.cell(6, 3), get_pct_str(ach[2] - lw[2]))
                    # For value columns, display growth % in fin value
                    growth_fin = f"{((ach[3] - lw[3]) / lw[3] * 100):.1f}%" if lw[3] != 0 else "0.0%"
                    growth_card = f"{((ach[4] - lw[4]) / lw[4] * 100):.1f}%" if lw[4] != 0 else "0.0%"
                    update_cell_first_run(table.cell(6, 4), growth_fin)
                    update_cell_first_run(table.cell(6, 5), growth_card)
                    
                    # Row 7: Qtr Trend
                    update_cell_first_run(table.cell(7, 4), f"{trend[3]}" if trend[3] else f"{ach[3]*1.49:.1f}")
                    update_cell_first_run(table.cell(7, 5), f"{trend[4]}" if trend[4] else f"{ach[4]*1.49:.1f}")
                    
                    # Row 8: Qtr Trend %
                    t_val = trend[3] if trend[3] else ach[3]*1.49
                    c_val = trend[4] if trend[4] else ach[4]*1.49
                    t_pct = (t_val / tgt[3] * 100) if tgt[3] != 0 else 0
                    c_pct = (c_val / tgt[4] * 100) if tgt[4] != 0 else 0
                    update_cell_first_run(table.cell(8, 4), f"{t_pct:.0f}%")
                    update_cell_first_run(table.cell(8, 5), f"{c_pct:.0f}%")
                    
                # --- Slide 3: Monthwise Performance (7 rows x 4 columns) ---
                elif slide_idx == 3 and rows == 7 and cols == 4:
                    mw_data = data.get("exec_summary", {}).get("monthwise_performance", {})
                    # Rows: Fin Value % (row 2), Fin Vol % (row 3), Card % (row 4), Fin Value Cr (row 5), Card Value Cr (row 6)
                    val_pct = mw_data.get("fin_value_pct", [0, 0, 0])
                    vol_pct = mw_data.get("fin_vol_pct", [0, 0, 0])
                    card_pct = mw_data.get("card_pct", [0, 0, 0])
                    val_cr = mw_data.get("fin_value_cr", [0, 0, 0])
                    card_cr = mw_data.get("card_value_cr", [0, 0, 0])
                    
                    for m_idx in range(3): # Apr, May, Jun columns (col 1, 2, 3)
                        update_cell_first_run(table.cell(2, m_idx+1), get_pct_str(val_pct[m_idx]) if val_pct[m_idx] else "")
                        update_cell_first_run(table.cell(3, m_idx+1), get_pct_str(vol_pct[m_idx]) if vol_pct[m_idx] else "")
                        update_cell_first_run(table.cell(4, m_idx+1), get_pct_str(card_pct[m_idx]) if card_pct[m_idx] else "")
                        update_cell_first_run(table.cell(5, m_idx+1), f"{val_cr[m_idx]}" if val_cr[m_idx] else "")
                        update_cell_first_run(table.cell(6, m_idx+1), f"{card_cr[m_idx]}" if card_cr[m_idx] else "")
                        
                # --- Slide 4 & Slide 16 (Backup): Disbursement Status (8 rows x 8 columns) ---
                elif (slide_idx in [4, 16]) and rows == 8 and cols == 8:
                    fin_data = data.get("disbursement", {}).get("financiers", {})
                    fins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]
                    
                    tot_sourcing = 0
                    tot_pending = 0
                    
                    for f_idx, fin in enumerate(fins):
                        f_info = fin_data.get(fin, {"sourcing": 0, "pending": 0, "last_week_pending_pct": 0})
                        sourcing = f_info.get("sourcing", 0)
                        pending = f_info.get("pending", 0)
                        tot_sourcing += sourcing
                        tot_pending += pending
                        
                        update_cell_first_run(table.cell(2, f_idx+1), f"{sourcing}")
                        update_cell_first_run(table.cell(3, f_idx+1), f"{pending}")
                        
                        pend_pct = (pending / sourcing * 100) if sourcing != 0 else 0
                        update_cell_first_run(table.cell(4, f_idx+1), f"{pend_pct:.1f}%")
                        update_cell_first_run(table.cell(5, f_idx+1), get_pct_str(f_info.get("last_week_pending_pct", 0)))
                        
                    # Overall Column (Col 7)
                    update_cell_first_run(table.cell(2, 7), f"{tot_sourcing}")
                    update_cell_first_run(table.cell(3, 7), f"{tot_pending}")
                    tot_pend_pct = (tot_pending / tot_sourcing * 100) if tot_sourcing != 0 else 0
                    update_cell_first_run(table.cell(4, 7), f"{tot_pend_pct:.1f}%")
                    
                    # Avg of last week pending
                    lw_pcts = [fin_data.get(f, {}).get("last_week_pending_pct", 0) for f in fins]
                    avg_lw_pct = sum(lw_pcts) / len(lw_pcts) if lw_pcts else 0
                    update_cell_first_run(table.cell(5, 7), f"{avg_lw_pct:.1f}%")

                # --- Slide 4 & Slide 16 (Backup): Top-5 Pending (7 rows x 5 columns) ---
                elif (slide_idx in [4, 16]) and rows == 7 and cols == 5:
                    top_stores = data.get("disbursement", {}).get("top_stores_aged", [])
                    for idx in range(5):
                        store_item = top_stores[idx] if idx < len(top_stores) else {"sno": idx+1, "store": "", "days": 0, "contributor": "", "count": 0}
                        # Rows start at 2
                        update_cell_first_run(table.cell(2+idx, 0), f"{store_item['sno']}")
                        update_cell_first_run(table.cell(2+idx, 1), store_item["store"])
                        update_cell_first_run(table.cell(2+idx, 2), f"{store_item['days']}")
                        update_cell_first_run(table.cell(2+idx, 3), store_item["contributor"])
                        update_cell_first_run(table.cell(2+idx, 4), f"{store_item['count']}")
                        
                # --- Slide 5 & Slide 17: Ageing Status (10 rows x 14 columns) ---
                elif (slide_idx in [5, 17]) and rows == 10 and cols == 14:
                    ageing_data = data.get("disbursement", {}).get("ageing_status", {})
                    ageing_fins = ["BAJAJ FINANCE (DPF & CD)", "HDB FINANCE", "IDFC FIRST BANK LTD", "TVS CREDIT", "DMI FINANCE", "POONAWALLA FINCORP"]
                    
                    totals = [0] * 12 # count & amt sums for the 6 brackets
                    
                    for f_idx, fin in enumerate(ageing_fins):
                        info = ageing_data.get(fin, {})
                        c4_7, a4_7 = info.get("4_7_days", [0, 0])
                        c8_15, a8_15 = info.get("8_15_days", [0, 0])
                        c15, a15 = info.get("15_plus", [0, 0])
                        c90, a90 = info.get("90_plus", [0, 0])
                        
                        tot_c = c4_7 + c8_15 + c15 + c90
                        tot_a = a4_7 + a8_15 + a15 + a90
                        
                        lw_c, lw_a = info.get("last_week", [0, 0])
                        
                        # Write data (rows start at 2)
                        r = 2 + f_idx
                        update_cell_first_run(table.cell(r, 2), f"{c4_7}" if c4_7 else "")
                        update_cell_first_run(table.cell(r, 3), f"{a4_7}" if a4_7 else "")
                        update_cell_first_run(table.cell(r, 4), f"{c8_15}" if c8_15 else "")
                        update_cell_first_run(table.cell(r, 5), f"{a8_15}" if a8_15 else "")
                        update_cell_first_run(table.cell(r, 6), f"{c15}" if c15 else "")
                        update_cell_first_run(table.cell(r, 7), f"{a15}" if a15 else "")
                        update_cell_first_run(table.cell(r, 8), f"{c90}" if c90 else "")
                        update_cell_first_run(table.cell(r, 9), f"{a90}" if a90 else "")
                        
                        update_cell_first_run(table.cell(r, 10), f"{tot_c}")
                        update_cell_first_run(table.cell(r, 11), f"{tot_a}")
                        update_cell_first_run(table.cell(r, 12), f"{lw_c}" if lw_c else "")
                        update_cell_first_run(table.cell(r, 13), f"{lw_a}" if lw_a else "")
                        
                        # Accumulate
                        totals[0] += c4_7
                        totals[1] += a4_7
                        totals[2] += c8_15
                        totals[3] += a8_15
                        totals[4] += c15
                        totals[5] += a15
                        totals[6] += c90
                        totals[7] += a90
                        totals[8] += tot_c
                        totals[9] += tot_a
                        totals[10] += lw_c
                        totals[11] += lw_a
                        
                    # Row 8 is TOTAL
                    update_cell_first_run(table.cell(8, 2), f"{totals[0]}")
                    update_cell_first_run(table.cell(8, 3), f"{totals[1]}")
                    update_cell_first_run(table.cell(8, 4), f"{totals[2]}")
                    update_cell_first_run(table.cell(8, 5), f"{totals[3]}")
                    update_cell_first_run(table.cell(8, 6), f"{totals[4]}")
                    update_cell_first_run(table.cell(8, 7), f"{totals[5]}")
                    update_cell_first_run(table.cell(8, 8), f"{totals[6]}")
                    update_cell_first_run(table.cell(8, 9), f"{totals[7]}")
                    update_cell_first_run(table.cell(8, 10), f"{totals[8]}")
                    update_cell_first_run(table.cell(8, 11), f"{totals[9]}")
                    
                # --- Slide 6: Product Category Performance (24 rows x 17 columns) ---
                elif slide_idx == 6 and rows == 24 and cols == 17:
                    qty_data = data.get("category_performance", {}).get("qty", {})
                    val_data = data.get("category_performance", {}).get("value", {})
                    
                    categories = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]
                    fins_qty = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"]
                    
                    # Accumulators for column totals (quantity section)
                    col_totals_qty = [0] * 10
                    tot_all_qty = 0
                    
                    # --- Volume Section (Rows 1 to 7 correspond to CD to Bank EMI) ---
                    for c_idx, cat in enumerate(categories):
                        r = 1 + c_idx
                        info = qty_data.get(cat, {})
                        cat_tot = 0
                        paper_tot = 0
                        card_tot = 0
                        
                        # aCash is index 1 on table columns
                        a_cash = info.get("aCASH", 0)
                        update_cell_first_run(table.cell(r, 1), f"{a_cash}")
                        col_totals_qty[0] += a_cash
                        cat_tot += a_cash
                        
                        # Bajaj is col 2, Benow is col 10
                        for f_idx in range(1, 10):
                            fin_key = fins_qty[f_idx]
                            val = info.get(fin_key, 0)
                            update_cell_first_run(table.cell(r, f_idx+1), f"{val}")
                            col_totals_qty[f_idx] += val
                            cat_tot += val
                            if f_idx < 7: # Bajaj to DMI
                                paper_tot += val
                            else: # Pinelab, Paytm, Benow
                                card_tot += val
                                
                        tot_all_qty += cat_tot
                        
                        # Total (col 11)
                        update_cell_first_run(table.cell(r, 11), f"{cat_tot}")
                        # Fin Share % (col 12) = (Paper + Card) / Total
                        fin_share = ((paper_tot + card_tot) / cat_tot * 100) if cat_tot != 0 else 0
                        update_cell_first_run(table.cell(r, 12), f"{fin_share:.1f}%")
                        # Paper total (col 13)
                        update_cell_first_run(table.cell(r, 13), f"{paper_tot}")
                        # Card total (col 14)
                        update_cell_first_run(table.cell(r, 14), f"{card_tot}")
                        # LW (col 15)
                        update_cell_first_run(table.cell(r, 15), get_pct_str(info.get("last_week_pct", 0)))
                        # Apr closing (col 16)
                        update_cell_first_run(table.cell(r, 16), get_pct_str(info.get("apr_closing_pct", 0)))
                        
                    # Row 8 is Grand Total Qty
                    update_cell_first_run(table.cell(8, 1), f"{col_totals_qty[0]}")
                    for f_idx in range(1, 10):
                        update_cell_first_run(table.cell(8, f_idx+1), f"{col_totals_qty[f_idx]}")
                    update_cell_first_run(table.cell(8, 11), f"{tot_all_qty}")
                    # Grand total Paper (col 13) & Card (col 14)
                    tot_paper_qty = sum(col_totals_qty[1:7])
                    tot_card_qty = sum(col_totals_qty[7:10])
                    update_cell_first_run(table.cell(8, 13), f"{tot_paper_qty}")
                    update_cell_first_run(table.cell(8, 14), f"{tot_card_qty}")
                    tot_fin_share_qty = ((tot_paper_qty + tot_card_qty) / tot_all_qty * 100) if tot_all_qty != 0 else 0
                    update_cell_first_run(table.cell(8, 12), f"{tot_fin_share_qty:.2f}%")
                    
                    # Row 9 is Share %
                    for f_idx in range(10):
                        sh = (col_totals_qty[f_idx] / tot_all_qty * 100) if tot_all_qty != 0 else 0
                        update_cell_first_run(table.cell(9, f_idx+1), f"{sh:.0f}%")
                    paper_sh = (tot_paper_qty / tot_all_qty * 100) if tot_all_qty != 0 else 0
                    card_sh = (tot_card_qty / tot_all_qty * 100) if tot_all_qty != 0 else 0
                    update_cell_first_run(table.cell(9, 13), f"{paper_sh:.2f}%")
                    update_cell_first_run(table.cell(9, 14), f"{card_sh:.2f}%")
                    
                    # Row 10 is Share in Fin Value Qty (linked to values, let's update it below)
                    
                    # --- Value Section (Rows 13 to 19 correspond to CD to Bank EMI) ---
                    col_totals_val = [0.0] * 10
                    tot_all_val = 0.0
                    
                    for c_idx, cat in enumerate(categories):
                        r = 13 + c_idx
                        info = val_data.get(cat, {})
                        cat_tot = 0.0
                        paper_tot = 0.0
                        card_tot = 0.0
                        
                        a_cash = info.get("aCASH", 0.0)
                        update_cell_first_run(table.cell(r, 1), f"{a_cash:.1f}")
                        col_totals_val[0] += a_cash
                        cat_tot += a_cash
                        
                        for f_idx in range(1, 10):
                            fin_key = fins_qty[f_idx]
                            val = info.get(fin_key, 0.0)
                            update_cell_first_run(table.cell(r, f_idx+1), f"{val:.1f}")
                            col_totals_val[f_idx] += val
                            cat_tot += val
                            if f_idx < 7:
                                paper_tot += val
                            else:
                                card_tot += val
                                
                        tot_all_val += cat_tot
                        
                        update_cell_first_run(table.cell(r, 11), f"{cat_tot:.1f}")
                        fin_share_val = ((paper_tot + card_tot) / cat_tot * 100) if cat_tot != 0 else 0
                        update_cell_first_run(table.cell(r, 12), f"{fin_share_val:.1f}%")
                        update_cell_first_run(table.cell(r, 13), f"{paper_tot:.1f}")
                        update_cell_first_run(table.cell(r, 14), f"{card_tot:.1f}")
                        update_cell_first_run(table.cell(r, 15), get_pct_str(info.get("last_week_pct", 0)))
                        update_cell_first_run(table.cell(r, 16), get_pct_str(info.get("apr_closing_pct", 0)))
                        
                    # Row 20 is Grand Total Value
                    update_cell_first_run(table.cell(20, 1), f"{col_totals_val[0]:.1f}")
                    for f_idx in range(1, 10):
                        update_cell_first_run(table.cell(20, f_idx+1), f"{col_totals_val[f_idx]:.1f}")
                    update_cell_first_run(table.cell(20, 11), f"{tot_all_val:.1f}")
                    tot_paper_val = sum(col_totals_val[1:7])
                    tot_card_val = sum(col_totals_val[7:10])
                    update_cell_first_run(table.cell(20, 13), f"{tot_paper_val:.1f}")
                    update_cell_first_run(table.cell(20, 14), f"{tot_card_val:.1f}")
                    tot_fin_share_val = ((tot_paper_val + tot_card_val) / tot_all_val * 100) if tot_all_val != 0 else 0
                    update_cell_first_run(table.cell(20, 12), f"{tot_fin_share_val:.2f}%")
                    
                    # Row 21 is Share % Value
                    for f_idx in range(10):
                        sh = (col_totals_val[f_idx] / tot_all_val * 100) if tot_all_val != 0 else 0
                        update_cell_first_run(table.cell(21, f_idx+1), f"{sh:.2f}%")
                    paper_sh_val = (tot_paper_val / tot_all_val * 100) if tot_all_val != 0 else 0
                    card_sh_val = (tot_card_val / tot_all_val * 100) if tot_all_val != 0 else 0
                    update_cell_first_run(table.cell(21, 13), f"{paper_sh_val:.2f}%")
                    update_cell_first_run(table.cell(21, 14), f"{card_sh_val:.2f}%")
                    
                    # Row 22 is Share in Fin Value for Value Section
                    # Bajaj Share in Fin Value = Bajaj Value / (Paper + Card value)
                    total_fin_val_cr = tot_paper_val + tot_card_val
                    for f_idx in range(1, 10):
                        sif = (col_totals_val[f_idx] / total_fin_val_cr * 100) if total_fin_val_cr != 0 else 0
                        update_cell_first_run(table.cell(22, f_idx+1), f"{sif:.1f}%")
                    paper_sif = (tot_paper_val / total_fin_val_cr * 100) if total_fin_val_cr != 0 else 0
                    card_sif = (tot_card_val / total_fin_val_cr * 100) if total_fin_val_cr != 0 else 0
                    update_cell_first_run(table.cell(22, 13), f"{paper_sif:.2f}%")
                    update_cell_first_run(table.cell(22, 14), f"{card_sif:.2f}%")
                    
                    # Row 10 is Share in Fin Value for Volume section (uses the same values)
                    for f_idx in range(1, 10):
                        sif = (col_totals_val[f_idx] / total_fin_val_cr * 100) if total_fin_val_cr != 0 else 0
                        update_cell_first_run(table.cell(10, f_idx+1), f"{sif:.0f}%")
                    update_cell_first_run(table.cell(10, 13), f"{paper_sif:.2f}%")
                    update_cell_first_run(table.cell(10, 14), f"{card_sif:.2f}%")
                    
                    # Row 23 is ASP = Value / Qty * 10,000,000
                    for f_idx in range(1, 10):
                        qty_col = col_totals_qty[f_idx]
                        val_col = col_totals_val[f_idx]
                        asp = (val_col / qty_col * 10000000) if qty_col != 0 else 0
                        update_cell_first_run(table.cell(23, f_idx+1), f"{asp:.0f}")

                # --- Slide 7: Financier Target Vs Achievement (8 rows x 12 columns) ---
                elif slide_idx == 7 and rows == 8 and cols == 12:
                    ft_data = data.get("financier_targets", {})
                    # Financiers map to col 1=Bajaj, 2=HDB, 3=IDFC, 4=PFL, 5=TVS, 6=DMI, 7=PF Total(sum)
                    # 8=Pinelab, 9=Paytm, 10=Benow, 11=Cards Total(sum)
                    fins_paper = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]
                    fins_card = ["PinelAb", "PayTm", "Benow"]
                    
                    tot_tgt_paper = 0
                    tot_ach_paper = 0
                    
                    # Write Paper Finance
                    for f_idx, fin in enumerate(fins_paper):
                        info = ft_data.get(fin, {"target": 0, "achievement": 0})
                        tgt = info.get("target", 0)
                        ach = info.get("achievement", 0)
                        tot_tgt_paper += tgt
                        tot_ach_paper += ach
                        
                        update_cell_first_run(table.cell(2, f_idx+1), f"{tgt}")
                        update_cell_first_run(table.cell(3, f_idx+1), f"{ach}")
                        
                        ach_pct = (ach / tgt * 100) if tgt != 0 else 0
                        update_cell_first_run(table.cell(4, f_idx+1), f"{ach_pct:.0f}%")
                        update_cell_first_run(table.cell(5, f_idx+1), f"{tgt - ach}")
                        
                        # Trend & Trend %
                        trend = int(ach * 1.49)
                        update_cell_first_run(table.cell(6, f_idx+1), f"{trend}")
                        trend_pct = (trend / tgt * 100) if tgt != 0 else 0
                        update_cell_first_run(table.cell(7, f_idx+1), f"{trend_pct:.2f}%")
                        
                    # Write Paper Total (Col 7)
                    update_cell_first_run(table.cell(2, 7), f"{tot_tgt_paper}")
                    update_cell_first_run(table.cell(3, 7), f"{tot_ach_paper}")
                    tot_ach_pct_p = (tot_ach_paper / tot_tgt_paper * 100) if tot_tgt_paper != 0 else 0
                    update_cell_first_run(table.cell(4, 7), f"{tot_ach_pct_p:.0f}%")
                    update_cell_first_run(table.cell(5, 7), f"{tot_tgt_paper - tot_ach_paper}")
                    tot_trend_p = int(tot_ach_paper * 1.49)
                    update_cell_first_run(table.cell(6, 7), f"{tot_trend_p}")
                    tot_trend_pct_p = (tot_trend_p / tot_tgt_paper * 100) if tot_tgt_paper != 0 else 0
                    update_cell_first_run(table.cell(7, 7), f"{tot_trend_pct_p:.2f}%")
                    
                    # Write Cards Finance
                    tot_tgt_card = 0.0
                    tot_ach_card = 0.0
                    
                    for f_idx, fin in enumerate(fins_card):
                        info = ft_data.get(fin, {"target": 0.0, "achievement": 0.0})
                        tgt = info.get("target", 0.0)
                        ach = info.get("achievement", 0.0)
                        tot_tgt_card += tgt
                        tot_ach_card += ach
                        
                        update_cell_first_run(table.cell(2, f_idx+8), f"{tgt:.1f}")
                        update_cell_first_run(table.cell(3, f_idx+8), f"{ach:.1f}")
                        
                        ach_pct = (ach / tgt * 100) if tgt != 0 else 0
                        update_cell_first_run(table.cell(4, f_idx+8), f"{ach_pct:.0f}%")
                        update_cell_first_run(table.cell(5, f_idx+8), f"{tgt - ach:.1f}")
                        
                        trend = ach * 1.49
                        update_cell_first_run(table.cell(6, f_idx+8), f"{trend:.1f}")
                        trend_pct = (trend / tgt * 100) if tgt != 0 else 0
                        update_cell_first_run(table.cell(7, f_idx+8), f"{trend_pct:.2f}%")
                        
                    # Cards Total (Col 11)
                    update_cell_first_run(table.cell(2, 11), f"{tot_tgt_card:.1f}")
                    update_cell_first_run(table.cell(3, 11), f"{tot_ach_card:.1f}")
                    tot_ach_pct_c = (tot_ach_card / tot_tgt_card * 100) if tot_tgt_card != 0 else 0
                    update_cell_first_run(table.cell(4, 11), f"{tot_ach_pct_c:.0f}%")
                    update_cell_first_run(table.cell(5, 11), f"{tot_tgt_card - tot_ach_card:.1f}")
                    tot_trend_c = tot_ach_card * 1.49
                    update_cell_first_run(table.cell(6, 11), f"{tot_trend_c:.1f}")
                    tot_trend_pct_c = (tot_trend_c / tot_tgt_card * 100) if tot_tgt_card != 0 else 0
                    update_cell_first_run(table.cell(7, 11), f"{tot_trend_pct_c:.2f}%")

                # --- Slide 8: Special Contest Summary (10 rows x 14 columns) ---
                elif slide_idx == 8 and rows == 10 and cols == 14:
                    summary_data = data.get("dmi_contest", {}).get("summary", {})
                    # DMI Brand columns: 6=Samsung, 7=Vivo, 8=Oppo
                    brands = ["SAMSUNG", "VIVO", "OPPO"]
                    cols_idx = [6, 7, 8]
                    
                    for idx, brand in enumerate(brands):
                        info = summary_data.get(brand, {"target": 72, "achievement": 0})
                        tgt = info.get("target", 72)
                        ach = info.get("achievement", 0)
                        
                        col = cols_idx[idx]
                        update_cell_first_run(table.cell(3, col), f"{tgt}")
                        update_cell_first_run(table.cell(4, col), f"{ach}")
                        
                        ach_pct = (ach / tgt * 100) if tgt != 0 else 0
                        update_cell_first_run(table.cell(5, col), f"{ach_pct:.2f}%")
                        update_cell_first_run(table.cell(6, col), f"{tgt - ach}")
                        
                        trend = int(ach * 1.49)
                        update_cell_first_run(table.cell(7, col), f"{trend}")

                # --- Slide 9: Branch-wise DMI contest (multiple tables, 4 columns) ---
                elif slide_idx == 9 and cols == 4:
                    branches_data = data.get("dmi_contest", {}).get("branches", {})
                    # Table headers in row 1 are ['', 'OPPO', 'SAMSUNG', 'VIVO']
                    # We can iterate over rows from 2 to rows-1, get branch name in col 0, and update counts
                    for r in range(2, rows):
                        branch_name = table.cell(r, 0).text.strip()
                        # Clean up formatting/spaces
                        branch_name = re.sub(r'\s+', ' ', branch_name)
                        if branch_name in branches_data:
                            info = branches_data[branch_name]
                            update_cell_first_run(table.cell(r, 1), f"{info.get('OPPO', 0)}")
                            update_cell_first_run(table.cell(r, 2), f"{info.get('SAMSUNG', 0)}")
                            update_cell_first_run(table.cell(r, 3), f"{info.get('VIVO', 0)}")

                # --- Slide 10: Manpower Status (5 rows x 11 columns) ---
                elif slide_idx == 10 and rows == 5 and cols == 11:
                    mp_data = data.get("manpower", {})
                    # Column mapping: 1=Bajaj, 2=HDB, 3=IDFC, 4=TVS, 5=PFL
                    fins = ["BAJAJ", "HDB", "IDFC", "TVS", "PFL"]
                    
                    tot_plan = 0
                    tot_curr = 0
                    
                    for f_idx, fin in enumerate(fins):
                        info = mp_data.get(fin, {"plan": 0, "current": 0})
                        plan = info.get("plan", 0)
                        curr = info.get("current", 0)
                        tot_plan += plan
                        tot_curr += curr
                        
                        update_cell_first_run(table.cell(1, f_idx+1), f"{plan}")
                        update_cell_first_run(table.cell(2, f_idx+1), f"{curr}")
                        
                        req = plan - curr
                        update_cell_first_run(table.cell(3, f_idx+1), f"{req}")
                        update_cell_first_run(table.cell(4, f_idx+1), f"{req}") # GAP is typically same
                        
                    # Overall Column (Col 10)
                    update_cell_first_run(table.cell(1, 10), f"{tot_plan}")
                    update_cell_first_run(table.cell(2, 10), f"{tot_curr}")
                    tot_req = tot_plan - tot_curr
                    update_cell_first_run(table.cell(3, 10), f"{tot_req}")
                    update_cell_first_run(table.cell(4, 10), f"{tot_req}")

    prs.save(output_path)
