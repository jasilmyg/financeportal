"""
bulk_load_card_history.py
=========================
Reusable, incremental bulk loader for historical Pine Lab, PayTM, and Benow
transaction archives (2020 - 2026+) into ClickHouse.

Features:
  - Reads quarterly/monthly .zip archives and standalone .xlsx/.xls/.csv files
    from the source folder (default: Downloads/finance pinelab and paytm data).
  - Handles all historical schema eras across 2020-2026:
      * Pine Lab: combines Bank EMI + Brand EMI + myEMI per month, uses clean
        'Date' column (fixing swapped DD/MM in 2023 Bank Txn Date), handles
        2023 Settled/Unsettled vs 2024+ success, Original Txn Amt=0 fallback
        to Total Txn Amt, excludes INSTANT_DISCOUNT / No EMI / BID_* /
        Brand EMI Discount* non-EMI programs, deduplicates on Transaction Id.
      * PayTM: prefers 'Dump Filtered' over leftover 'Dump' template tabs,
        handles Era 1/2 EDC dumps (filtering SUCCESS + EMI payment modes >= 1000)
        and Era 3 Total Transaction reports (EMI_check == Yes / Txn_type != Other Mode).
      * Benow: handles 2024-2026 column shifts (incl. Mar 26 Transaction Status shift
        and Jan 25 payment_method_tag == EMI filter), maps categories & branches.
  - Maps all historical short store names (e.g. PENTA -> MENAKA, GALA POTTAMMAL ->
    POTTAMMAL FUTURE, SABA -> SABA MAHARANI, EDAPPALLY -> EDAPPALLY FUTURE, etc.)
    to canonical ClickHouse branch_master names/codes.
  - Matches transactions to ERP invoices (azure_sales_report) in fast quarterly
    batches and updates:
      * pine_lab_transactions
      * paytm_transactions
      * benow_transactions
      * benow_pine_financier
    strictly scoped to the processed YYYY-MM months (never touching other months
    like Sep-Nov 2026).
  - Tracks processed files in data/_bulk_card_ingested.json so newly added ZIPs
    are ingested incrementally on subsequent runs without re-doing finished archives.

Usage:
  python bulk_load_card_history.py --dry-run
  python bulk_load_card_history.py
  python bulk_load_card_history.py --force
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

import pandas as pd
from python_calamine import CalamineWorkbook

from clickhouse_client import get_client, replace_rows
from paytm_cleaner import clean_brand_name
from pine_lab_cleaner import apply_product_mapping
import emi_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bulk_card_loader")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE_DIR = r"c:\Users\rahul_myg\Downloads\finance pinelab and paytm data"
STATE_TRACKER_PATH = os.path.join(BASE_DIR, "data", "_bulk_card_ingested.json")

# Historical short/variant branch names -> canonical branch_master.branch_name (or ERP code)
HIST_BRANCH_ALIASES: dict[str, str] = {
    "CHAVAKKAD":            "CHAVAKKAD FUTURE",
    "CHEMMAD":              "CHEMMAD FUTURE",
    "EDAPPALLY":            "EDAPPALLY FUTURE",
    "FEROK FUTURE":         "FEROKE FUTURE",
    "FEROKE":               "FEROKE FUTURE",
    "GALA POTTAMMAL":       "POTTAMMAL FUTURE",
    "POTTAMMAL":            "POTTAMMAL FUTURE",
    "IRINJALAKKUDA FUTURE": "IRINJALAKUDA FUTURE",
    "IRINJALAKKUDA":        "IRINJALAKUDA FUTURE",
    "IRINJALAKUDA":         "IRINJALAKUDA FUTURE",
    "KANJIRAPPALLY":        "KANJIRAPPALLY FUTURE",
    "KATTAPPANA":           "KATTAPPANA FUTURE",
    "KINGS WAY":            "KINGSWAY",
    "KOTHAMANGALAM":        "KOTHAMANGALAM FUTURE",
    "MANNARKKAD":           "MANNARKAD FUTURE",
    "MANNARKKAD FUTURE":    "MANNARKAD FUTURE",
    "MANNARKAD":            "MANNARKAD FUTURE",
    "NILAMBUR":             "NILAMBUR FUTURE",
    "PATTAMBI":             "PATTAMBI FUTURE",
    "OLD PATTAMBI":         "PATTAMBI FUTURE",
    "PENTA":                "MENAKA",
    "PENTA MENAKA":         "MENAKA",
    "PERUMBAVOOR":          "PERUMBAVOOR FUTURE",
    "PIRAVOM":              "PIRAVOM FUTURE",
    "SABA":                 "SABA MAHARANI",
    "SEEMA":                "SMC",
    "THAMARASSERY":         "THAMARASSERY FUTURE",
    "OLD THAMARASSERY":     "THAMARASSERY FUTURE",
    "THONDAYAD":            "THONDAYAD FUTURE",
    "THOPPUMPADY":          "THOPPUMPADY FUTURE",
    "VADAKKENCHERRY":       "VADAKKENCHERRY FUTURE",
    "WADAKKANCHERY FUTURE": "VADAKKENCHERRY FUTURE",
    "WADAKKANCHERY":        "VADAKKENCHERRY FUTURE",
    "MAVELIKKARA":          "MAVELIKARA FUTURE",
    "MAVELIKKARA FUTURE":   "MAVELIKARA FUTURE",
    "MAVELIKARA":           "MAVELIKARA FUTURE",
    "CHALAKKUDY":           "CHALAKKUDY FUTURE",
    "MANGALURU":            "FALNIR FUTURE",
    "MANGALORE":            "FALNIR FUTURE",
    "FALNIR":               "FALNIR FUTURE",
}

EXCLUDED_BRANCHES: set[str] = {
    "", "NAN", "NONE", "NULL", "0", "CAFE YMCA",
    "HEAD OFFICE", "HO SUPPORT CENTER", "HO", "CORPORATE SALE",
}

DUMMY_IMEIS: set[str] = {
    "123456789", "12345678", "1234567890", "123456789012345",
    "1122334455", "00000000", "000000000", "000000000000000",
    "11111111", "111111111", "111111111111111",
    "99999999", "999999999", "999999999999999",
    "0", "na", "n/a", "none", "null", "-", "",
}

CANON_ERP_CATS: dict[str, str] = {
    "MOBILE": "MOBILE",
    "MOBILES": "MOBILE",
    "SMARTPHONE": "MOBILE",
    "SMARTPHONES": "MOBILE",
    "TABLET": "MOBILE",
    "TAB": "MOBILE",
    "LAPTOP": "LAPTOP",
    "LAPTOPS": "LAPTOP",
    "DESKTOP": "LAPTOP",
    "CD PRODUCTS": "CD PRODUCTS",
    "CD PRODUCT": "CD PRODUCTS",
    "CD": "CD PRODUCTS",
    "TV": "CD PRODUCTS",
    "TELEVISION": "CD PRODUCTS",
    "REFRIGERATOR": "CD PRODUCTS",
    "REFRIGERATORS": "CD PRODUCTS",
    "WASHING MACHINE": "CD PRODUCTS",
    "WASHING MACHINES": "CD PRODUCTS",
    "AIR CONDITIONER": "CD PRODUCTS",
    "AIR CONDITIONERS": "CD PRODUCTS",
    "AC": "CD PRODUCTS",
    "RAC": "CD PRODUCTS",
    "MICROWAVE OVEN": "CD PRODUCTS",
    "DISH WASHER": "CD PRODUCTS",
    "HVA": "HVA",
    "SMART WATCH": "HVA",
    "AUDIO": "HVA",
    "EAR WEARABLES": "HVA",
    "HOME THEATRE": "HVA",
    "SA": "SA",
    "SMALL APPLIANCES": "SA",
    "OTHERS": "OTHERS",
    "OTHER": "OTHERS",
    "BANK EMI": "BANK EMI",
    "BANK_EMI": "BANK EMI",
    "BANKEMI": "BANK EMI",
}

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _load_json_map(rel_path: str) -> dict[str, str]:
    full = os.path.join(BASE_DIR, rel_path)
    if not os.path.exists(full):
        return {}
    try:
        with open(full, "r", encoding="utf-8") as fh:
            return {str(k).strip(): str(v).strip().upper() for k, v in json.load(fh).items()}
    except Exception:
        return {}


POS_BRANCH_MAP = _load_json_map(os.path.join("data", "pinelab_pos_branch_map.json"))
PAYTM_MID_BRANCH_MAP = _load_json_map(os.path.join("data", "paytm_mid_branch_map.json"))
PAYTM_MID_CODE_MAP = _load_json_map(os.path.join("data", "paytm_mid_map.json"))
BENOW_MID_BRANCH_MAP = _load_json_map(os.path.join("data", "benow_mid_branch_map.json"))


def is_real_imei(val: str) -> bool:
    if not val:
        return False
    s = str(val).strip().lstrip("'").strip()
    if s.endswith(".0"):
        s = s[:-2]
    sl = s.lower()
    if sl in DUMMY_IMEIS or len(s) < 8:
        return False
    # Reject repeated single-character strings like '0000000000' or 'XXXXXXXXXX'
    if len(set(sl)) <= 1:
        return False
    return True


def clean_id_series(s: pd.Series) -> pd.Series:
    return (
        s.fillna("")
        .astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def infer_ym_from_filename(name: str) -> Optional[str]:
    base = os.path.basename(name).lower()
    m = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"[\s\-_']*(\d{2,4})\b",
        base,
    )
    if not m:
        return None
    mon = MONTH_MAP.get(m.group(1)[:3])
    yr = int(m.group(2))
    if yr < 100:
        yr += 2000
    if mon and 2018 <= yr <= 2035:
        return f"{yr:04d}-{mon:02d}"
    return None


def detect_provider(name: str) -> Optional[str]:
    low = os.path.basename(name).lower()
    if low.startswith("~$") or low.startswith("."):
        return None
    if "pine" in low or "myemi" in low or "pl " in low or "emi transactions report" in low:
        return "pinelab"
    if "paytm" in low:
        return "paytm"
    if "benow" in low:
        return "benow"
    return None


class BranchResolver:
    def __init__(self, branch_name_to_code: dict[str, str], code_to_branch_name: dict[str, str]):
        self.name_to_code = {k.upper().strip(): v.upper().strip() for k, v in branch_name_to_code.items()}
        self.code_to_name = {k.upper().strip(): v.upper().strip() for k, v in code_to_branch_name.items()}
        # Extra ERP codes that exist in azure_sales_report without branch_master rows
        self.extra_codes = {"SMC", "CSB", "3GH", "KSO"}

    def normalize(self, raw_branch: str, raw_store: str = "", pos_or_mid: str = "", provider: str = "") -> tuple[str, Optional[str]]:
        """
        Return (canonical_branch_name, erp_branch_code_or_None).
        Returns ('', None) if excluded / non-retail.
        """
        pm = str(pos_or_mid or "").strip().lstrip("'").strip()
        if pm.endswith(".0"):
            pm = pm[:-2]

        cand = str(raw_branch or "").strip().lstrip("'").strip().upper()
        if cand in ("", "NAN", "NONE", "NULL", "0", "#N/A", "N/A"):
            cand = ""

        # Try POS / MID maps first whencand is empty
        if not cand and pm:
            if provider == "pinelab" and pm in POS_BRANCH_MAP:
                cand = POS_BRANCH_MAP[pm]
            elif provider == "paytm":
                if pm in PAYTM_MID_BRANCH_MAP:
                    cand = PAYTM_MID_BRANCH_MAP[pm]
                elif pm in PAYTM_MID_CODE_MAP:
                    code = PAYTM_MID_CODE_MAP[pm]
                    cand = self.code_to_name.get(code, code)
            elif provider == "benow" and pm in BENOW_MID_BRANCH_MAP:
                cand = BENOW_MID_BRANCH_MAP[pm]

        if not cand and raw_store:
            s = str(raw_store).strip().lstrip("'").upper()
            if "ANGAMALY" in s and "FUTURE" not in s and "3G" in s:
                cand = "ANGAMALY"
            else:
                s = re.sub(r"^\s*(?:MY\s*G|3G\s*MOBILE\s*WORLD)[\s_\-]*", "", s)
                s = re.sub(r"[\s_]+", " ", s).strip()
                # Strip trailing numeric/store code in Pine Lab Store Name ('MYG ALUVA ALU' -> 'ALUVA')
                if provider == "pinelab":
                    parts = s.split()
                    if len(parts) > 1 and (parts[-1] in self.code_to_name or len(parts[-1]) <= 4) and parts[-1] != "MALL" and parts[-1] != "ROAD" and parts[-1] != "FORT" and parts[-1] != "2":
                        maybe = " ".join(parts[:-1])
                        if maybe in self.name_to_code or maybe in HIST_BRANCH_ALIASES or (maybe + " FUTURE") in self.name_to_code:
                            s = maybe
                cand = s

        cand = re.sub(r"^\s*MY\s*G[\s_\-]*", "", cand)
        cand = re.sub(r"[\s_]+", " ", cand).strip().upper()

        if cand in EXCLUDED_BRANCHES:
            return "", None

        if cand in HIST_BRANCH_ALIASES:
            cand = HIST_BRANCH_ALIASES[cand]

        if cand in EXCLUDED_BRANCHES:
            return "", None

        if cand in self.name_to_code:
            return cand, self.name_to_code[cand]

        if cand in self.code_to_name:
            bname = self.code_to_name[cand]
            return bname, cand

        if (cand + " FUTURE") in self.name_to_code:
            f_cand = cand + " FUTURE"
            return f_cand, self.name_to_code[f_cand]

        if cand in self.extra_codes:
            return cand, cand

        return cand, self.name_to_code.get(cand)


def pick_sheet_and_read(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    low = filename.lower()
    if low.endswith(".csv"):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(raw_bytes), encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Cannot decode CSV: {filename}")

    wb = CalamineWorkbook.from_filelike(io.BytesIO(raw_bytes))
    sheets = wb.sheet_names
    s_lower = {s.strip().lower(): s for s in sheets}

    # Always prefer 'Dump Filtered' over leftover template 'Dump' tabs!
    chosen = None
    for pref in ("dump filtered", "dump", "data", "sheet1"):
        if pref in s_lower:
            chosen = s_lower[pref]
            break
    if chosen is None:
        for s in sheets:
            sl = s.strip().lower()
            if "pivot" not in sl and "old" not in sl and "summary" not in sl:
                chosen = s
                break
    if chosen is None:
        chosen = sheets[0]

    return pd.read_excel(io.BytesIO(raw_bytes), sheet_name=chosen, engine="calamine")


def parse_clean_dates(series: pd.Series, expected_ym: Optional[str], dayfirst: bool = True) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce")
    elif pd.api.types.is_numeric_dtype(series):
        num = pd.to_numeric(series, errors="coerce")
        valid_excel = num.where((num >= 30000) & (num <= 70000))
        dt = pd.to_datetime(valid_excel, unit="D", origin="1899-12-30", errors="coerce")
    else:
        # Handle mixed object columns containing datetime objects, Excel serial numbers, or strings
        num = pd.to_numeric(series, errors="coerce")
        excel_mask = num.notna() & (num >= 30000) & (num <= 70000)
        dt = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
        if excel_mask.any():
            dt.loc[excel_mask] = pd.to_datetime(
                num.loc[excel_mask], unit="D", origin="1899-12-30", errors="coerce"
            )
        rem_mask = series.notna() & (~excel_mask)
        if rem_mask.any():
            rem_s = series.loc[rem_mask]
            s_clean = rem_s.astype(str).str.strip().str.strip("'").str.strip()
            sample = s_clean.dropna().head(20)
            is_iso = sample.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}").all() if not sample.empty else False
            dt.loc[rem_mask] = pd.to_datetime(
                s_clean, dayfirst=(False if is_iso else dayfirst), format="mixed", errors="coerce"
            )

    # Reject impossible pre-2018 or post-2035 timestamps
    dt = dt.where(dt.isna() | ((dt.dt.year >= 2018) & (dt.dt.year <= 2035)), pd.NaT)

    if expected_ym and not dt.dropna().empty:
        exp_yr, exp_mo = int(expected_ym[:4]), int(expected_ym[5:7])

        def _fix_swapped(ts):
            if pd.isna(ts):
                return ts
            if ts.year == exp_yr and ts.month != exp_mo and ts.day == exp_mo and 1 <= ts.month <= 12:
                try:
                    return pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
                except ValueError:
                    return ts
            return ts

        bad_mask = dt.notna() & (dt.dt.year == exp_yr) & (dt.dt.month != exp_mo) & (dt.dt.day == exp_mo)
        if bad_mask.any():
            dt.loc[bad_mask] = dt.loc[bad_mask].apply(_fix_swapped)
    return dt.dt.normalize()


def canon_erp_cat(val: str, default: str = "BANK EMI") -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    s = str(val).strip().upper()
    if not s or s in ("NAN", "NONE", "NULL"):
        return default
    return CANON_ERP_CATS.get(s, default if s == "" else "OTHERS")


# ---------------------------------------------------------------------------
# Provider-specific cleaners returning standardized DataFrames
# ---------------------------------------------------------------------------

def clean_pinelab_archive_df(
    df_raw: pd.DataFrame, filename: str, expected_ym: Optional[str], resolver: BranchResolver
) -> pd.DataFrame:
    if df_raw.empty:
        return pd.DataFrame()

    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cmap = {c.lower(): c for c in df.columns}
    fname_low = filename.lower()

    is_bank_file = ("bank emi" in fname_low) or ("myemi" in fname_low) or ("my emi" in fname_low)

    # 1. Drop empty / ghost rows
    id_col = cmap.get("transaction id") or cmap.get("txn id") or cmap.get("tid")
    date_col = cmap.get("date") or cmap.get("bank txn date") or cmap.get("txn date")
    if not date_col:
        log.warning("PineLab %s: no date column found in %s", filename, list(df.columns)[:10])
        return pd.DataFrame()

    if id_col:
        df["_txn_id"] = clean_id_series(df[id_col])
        df = df[df["_txn_id"].ne("") & ~df["_txn_id"].str.lower().isin(["nan", "none", "null"])].copy()
    else:
        df["_txn_id"] = [f"{filename}_{i}" for i in range(len(df))]

    df["_date"] = parse_clean_dates(df[date_col], expected_ym, dayfirst=(date_col.lower() != "date"))
    df = df[df["_date"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    # 2. Type == SALE
    type_col = cmap.get("txn type") or cmap.get("type")
    if type_col:
        t_up = df[type_col].fillna("").astype(str).str.strip().str.upper()
        df = df[t_up == "SALE"].copy()

    # 3. Status filter (2024+ 'Txn Status' == 'success'; 2023 'Status' in Settled/Unsettled/Approved/success)
    if "txn status" in cmap:
        st_low = df[cmap["txn status"]].fillna("").astype(str).str.strip().str.lower()
        df = df[st_low.isin(["success", "settled", "approved"])].copy()
    elif "status" in cmap:
        st_low = df[cmap["status"]].fillna("").astype(str).str.strip().str.lower()
        df = df[st_low.isin(["settled", "unsettled", "approved", "success"])].copy()

    if df.empty:
        return pd.DataFrame()

    # 4. Program exclusions (INSTANT_DISCOUNT, No EMI, BID_*, Brand EMI Discount*)
    prog_col = cmap.get("program")
    if prog_col:
        p_str = df[prog_col].fillna("").astype(str).str.strip()
        p_low = p_str.str.lower()
        excl = p_low.isin(["instant_discount", "instant discount", "no emi"])
        if not is_bank_file:
            excl = excl | p_low.str.startswith("bid_") | p_low.str.startswith("brand emi discount")
        df = df[~excl].copy()

    if df.empty:
        return pd.DataFrame()

    # 5. Amount (Original Txn Amt > 0, fallback to Total Txn Amt / Auth Txn Amt)
    amt = pd.Series(0.0, index=df.index)
    for acol_key in ("original txn amt", "original txn amount", "total txn amt", "total txn amount", "auth txn amt", "auth amount", "amount"):
        if acol_key in cmap:
            cand_amt = pd.to_numeric(df[cmap[acol_key]], errors="coerce").fillna(0.0)
            amt = amt.where(amt > 0, cand_amt)
    df["_amt"] = amt

    # 6. Branch & State
    br_col = cmap.get("branch")
    st_col = cmap.get("store name") or cmap.get("city") or cmap.get("address1")
    pos_col = cmap.get("pos") or cmap.get("pos id")

    raw_br = df[br_col] if br_col else pd.Series("", index=df.index)
    raw_st = df[st_col] if st_col else pd.Series("", index=df.index)
    raw_pos = df[pos_col] if pos_col else pd.Series("", index=df.index)

    resolved = [
        resolver.normalize(b, s, p, provider="pinelab")
        for b, s, p in zip(raw_br, raw_st, raw_pos)
    ]
    df["_branch"] = [r[0] for r in resolved]
    df["_branch_code"] = [r[1] for r in resolved]
    df = df[df["_branch"] != ""].copy()
    if df.empty:
        return pd.DataFrame()

    rbm_col = cmap.get("rbm")
    rbm_up = df[rbm_col].fillna("").astype(str).str.strip().str.upper() if rbm_col else pd.Series("", index=df.index)
    df["_state"] = [
        "KARNATAKA" if (r == "KARNATAKA RBM" or b in ("FALNIR FUTURE", "FLF")) else "KERALA"
        for r, b in zip(rbm_up, df["_branch"])
    ]

    # 7. Serial / IMEI
    ser_col = cmap.get("serial") or cmap.get("imei")
    df["_imei"] = clean_id_series(df[ser_col]) if ser_col else ""

    # 8. Product & ERP Category
    if is_bank_file:
        df["_product"] = "BANK EMI"
        df["_erp_cat"] = "BANK EMI"
        df["_is_bank_emi"] = True
    else:
        df["_is_bank_emi"] = False
        # Prepare columns for apply_product_mapping (without DB IMEI scan; quarter ERP lookup handles IMEIs)
        tmp = pd.DataFrame(index=df.index)
        for src_k, dst_k in (
            ("product category", "Product Category"),
            ("product name", "Product Name"),
            ("txn category", "Txn Category"),
            ("manufacturer", "Manufacturer"),
        ):
            if src_k in cmap:
                tmp[dst_k] = df[cmap[src_k]]
        if "Manufacturer" not in tmp.columns and "brand" in cmap:
            tmp["Manufacturer"] = df[cmap["brand"]]
        if "Product Name" not in tmp.columns:
            tmp["Product Name"] = ""
        tmp["Serial"] = ""
        tmp = apply_product_mapping(tmp)

        # Prefer workbook's explicit Product/Category when valid, else apply_product_mapping result
        wb_prod = df[cmap["product"]].fillna("").astype(str).str.strip().str.upper() if "product" in cmap else pd.Series("", index=df.index)
        wb_cat = df[cmap["category"]].fillna("").astype(str).str.strip().str.upper() if "category" in cmap else pd.Series("", index=df.index)

        prod_out = tmp["Product"].fillna("").astype(str).str.strip().str.upper()
        cat_out = tmp["Category"].apply(lambda v: canon_erp_cat(v, default="BANK EMI"))

        valid_wb_cat = wb_cat.isin(CANON_ERP_CATS.keys()) & ~wb_cat.isin(["", "NAN", "NONE", "0"])
        cat_out = cat_out.where(~valid_wb_cat, wb_cat.map(lambda x: CANON_ERP_CATS.get(x, "OTHERS")))
        valid_wb_prod = ~wb_prod.isin(["", "NAN", "NONE", "0"])
        prod_out = prod_out.where(~valid_wb_prod, wb_prod)

        df["_product"] = prod_out
        df["_erp_cat"] = cat_out

    return df[[
        "_txn_id", "_date", "_amt", "_branch", "_branch_code",
        "_state", "_imei", "_product", "_erp_cat", "_is_bank_emi",
    ]].copy()


def clean_paytm_archive_df(
    df_raw: pd.DataFrame, filename: str, expected_ym: Optional[str], resolver: BranchResolver
) -> pd.DataFrame:
    if df_raw.empty:
        return pd.DataFrame()

    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cmap = {c.lower(): c for c in df.columns}

    is_modern = ("emi_check" in cmap) or ("txn_type" in cmap) or ("original_amount" in cmap)

    if is_modern:
        # Era 3 (Apr 2025 - 2026+)
        id_col = cmap.get("txn_id") or cmap.get("transaction_id") or cmap.get("order_id")
        df["_txn_id"] = clean_id_series(df[id_col]) if id_col else [f"{filename}_{i}" for i in range(len(df))]
        df = df[df["_txn_id"].ne("") & ~df["_txn_id"].str.lower().isin(["nan", "none", "null"])].copy()

        # Filter EMI rows
        if "emi_check" in cmap:
            ec = df[cmap["emi_check"]].fillna("").astype(str).str.strip().str.upper()
            df = df[ec == "YES"].copy()
        elif "txn_type" in cmap:
            tt = df[cmap["txn_type"]].fillna("").astype(str).str.strip().str.lower().str.replace(r"[\s_]+", "", regex=True)
            df = df[tt.isin(["brandemi", "bankemi"])].copy()

        if df.empty:
            return pd.DataFrame()

        # Amount: prefer Txn Value when > 0, else original_amount
        amt = pd.Series(0.0, index=df.index)
        for k in ("txn value", "original_amount", "amount"):
            if k in cmap:
                ca = pd.to_numeric(df[cmap[k]], errors="coerce").fillna(0.0)
                amt = amt.where(amt > 0, ca)
        df["_amt"] = amt
        df = df[df["_amt"] >= 1000].copy()
        df = df.drop_duplicates(subset=["_txn_id"], keep="first").copy()
        if df.empty:
            return pd.DataFrame()

        # Date: prefer clean 'Txn Date' / 'Date' column if present, else 'txn_date'
        dcol = cmap.get("txn date") or cmap.get("date") or cmap.get("txn_date") or cmap.get("transaction_date")
        df["_date"] = parse_clean_dates(df[dcol], expected_ym, dayfirst=False) if dcol else pd.NaT
        df = df[df["_date"].notna()].copy()

        br_col = cmap.get("store") or cmap.get("branch")
        st_col = cmap.get("store_name") or br_col
        mid_col = cmap.get("mid")

        raw_br = df[br_col] if br_col else pd.Series("", index=df.index)
        raw_st = df[st_col] if st_col else pd.Series("", index=df.index)
        raw_mid = clean_id_series(df[mid_col]) if mid_col else pd.Series("", index=df.index)

        resolved = [
            resolver.normalize(b, s, m, provider="paytm")
            for b, s, m in zip(raw_br, raw_st, raw_mid)
        ]
        df["_branch"] = [r[0] for r in resolved]
        df["_branch_code"] = [r[1] for r in resolved]
        df = df[df["_branch"] != ""].copy()
        if df.empty:
            return pd.DataFrame()

        df["_store_raw"] = raw_st.loc[df.index].astype(str).str.strip()
        ser_col = cmap.get("imei") or cmap.get("serial")
        df["_imei"] = clean_id_series(df[ser_col]) if ser_col else ""

        brand_col = cmap.get("brand") or cmap.get("brand_name")
        df["_brand"] = df[brand_col].apply(clean_brand_name).fillna("") if brand_col else ""

        pcat_col = cmap.get("product_category")
        pname_col = cmap.get("productname")
        df["_prod_cat_raw"] = df[pcat_col].fillna("").astype(str).str.strip() if pcat_col else ""
        df["_prod_name_raw"] = df[pname_col].fillna("").astype(str).str.strip() if pname_col else ""

        tmp = pd.DataFrame(index=df.index)
        tmp["Product Category"] = df["_prod_cat_raw"].replace("", None)
        tmp["Product Name"] = df["_prod_name_raw"].replace("", None)
        tmp["Manufacturer"] = df["_brand"].replace("", None)
        tmp["Serial"] = ""
        tmp = apply_product_mapping(tmp)

        wb_prod = df[cmap["product"]].fillna("").astype(str).str.strip().str.upper() if "product" in cmap else pd.Series("", index=df.index)
        tt_col = cmap.get("txn_type")
        is_bank_emi = (
            df[tt_col].fillna("").astype(str).str.strip().str.lower().str.replace("_", "") == "bankemi"
            if tt_col else pd.Series(False, index=df.index)
        ) & (df["_brand"] == "")

        df["_pl_product"] = tmp["Product"].where(wb_prod.isin(["", "NAN", "NONE", "0"]), wb_prod)
        df["_erp_cat"] = tmp["Category"].apply(lambda v: canon_erp_cat(v, default="BANK EMI"))
        # If workbook PRODUCT column explicitly names a known category/product, ensure consistency
        wb_mapped = wb_prod.map(lambda x: CANON_ERP_CATS.get(x, ""))
        df["_erp_cat"] = df["_erp_cat"].where(wb_mapped == "", wb_mapped)
        df.loc[is_bank_emi, "_erp_cat"] = "BANK EMI"
        df["_needs_erp_cat"] = False

        return df[[
            "_txn_id", "_date", "_amt", "_branch", "_branch_code", "_store_raw",
            "_imei", "_brand", "_prod_name_raw", "_prod_cat_raw", "_pl_product",
            "_erp_cat", "_needs_erp_cat",
        ]].copy()

    else:
        # Era 1 & 2 (2020 - Mar 2025 EDC dumps)
        if "status" in cmap:
            st = clean_id_series(df[cmap["status"]]).str.upper()
            df = df[st == "SUCCESS"].copy()

        pm_col = cmap.get("payment_mode") or cmap.get("payment mode")
        if pm_col:
            pm = clean_id_series(df[pm_col]).str.upper()
            df = df[pm.isin(["EMI", "EMI_DC", "BRAND_EMI", "BANK_EMI", "EMI_CARDLESS"])].copy()
            df["_pm"] = pm.loc[df.index]
        else:
            df["_pm"] = "EMI"

        if df.empty:
            return pd.DataFrame()

        id_col = cmap.get("transaction_id") or cmap.get("txn id") or cmap.get("order_id") or cmap.get("merchant_ref_id")
        df["_txn_id"] = clean_id_series(df[id_col]) if id_col else [f"{filename}_{i}" for i in range(len(df))]
        df = df[df["_txn_id"].ne("") & ~df["_txn_id"].str.lower().isin(["nan", "none", "null"])].copy()

        amt = pd.Series(0.0, index=df.index)
        for k in ("tot amount", "amount", "txn amount"):
            if k in cmap:
                ca = pd.to_numeric(clean_id_series(df[cmap[k]]), errors="coerce").fillna(0.0)
                amt = amt.where(amt > 0, ca)
        df["_amt"] = amt
        df = df[df["_amt"] >= 1000].copy()
        df = df.drop_duplicates(subset=["_txn_id"], keep="first").copy()
        if df.empty:
            return pd.DataFrame()

        dcol = cmap.get("txn date") or cmap.get("date") or cmap.get("transaction_date")
        df["_date"] = parse_clean_dates(df[dcol], expected_ym, dayfirst=True)
        df = df[df["_date"].notna()].copy()

        br_col = cmap.get("store") or cmap.get("branch")
        st_col = cmap.get("merchant_name") or cmap.get("udf1") or cmap.get("pos_id") or br_col
        mid_col = cmap.get("mid")

        raw_br = df[br_col] if br_col else pd.Series("", index=df.index)
        raw_st = df[st_col] if st_col else pd.Series("", index=df.index)
        raw_mid = clean_id_series(df[mid_col]) if mid_col else pd.Series("", index=df.index)

        resolved = [
            resolver.normalize(b, s, m, provider="paytm")
            for b, s, m in zip(raw_br, raw_st, raw_mid)
        ]
        df["_branch"] = [r[0] for r in resolved]
        df["_branch_code"] = [r[1] for r in resolved]
        df = df[df["_branch"] != ""].copy()
        if df.empty:
            return pd.DataFrame()

        df["_store_raw"] = df["_branch"]
        df["_imei"] = ""
        df["_brand"] = ""
        df["_prod_name_raw"] = ""
        df["_prod_cat_raw"] = ""
        df["_pl_product"] = "BANK EMI"
        df["_erp_cat"] = "BANK EMI"
        # Allow ERP invoice match to enrich category unless explicitly tagged BANK_EMI
        df["_needs_erp_cat"] = df["_pm"] != "BANK_EMI"

        return df[[
            "_txn_id", "_date", "_amt", "_branch", "_branch_code", "_store_raw",
            "_imei", "_brand", "_prod_name_raw", "_prod_cat_raw", "_pl_product",
            "_erp_cat", "_needs_erp_cat",
        ]].copy()


def clean_benow_archive_df(
    df_raw: pd.DataFrame, filename: str, expected_ym: Optional[str], resolver: BranchResolver
) -> pd.DataFrame:
    if df_raw.empty:
        return pd.DataFrame()

    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cmap = {c.lower(): c for c in df.columns}

    # 1. Drop empty / summary rows
    br_col = cmap.get("store name") or cmap.get("branch")
    st1_col = cmap.get("store name.1") or cmap.get("merchant name")
    date_col = cmap.get("date") or cmap.get("txn date") or cmap.get("transaction date") or cmap.get("transaction_date")
    if not date_col:
        return pd.DataFrame()

    df["_date"] = parse_clean_dates(df[date_col], expected_ym, dayfirst=(date_col.lower() != "date"))
    df = df[df["_date"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    # 2. Filter payment_method_tag == 'EMI' when present (fixes Jan 25 NON-EMI rows)
    if "payment_method_tag" in cmap:
        pmt = df[cmap["payment_method_tag"]].fillna("").astype(str).str.strip().str.upper()
        df = df[pmt != "NON-EMI"].copy()

    # 3. Exclude explicit failed/cancelled statuses (note Mar 26 has TX IDs in Transaction Status, so only reject failure words!)
    for scol in ("txn status", "transaction status", "transaction_status"):
        if scol in cmap:
            st_low = df[cmap[scol]].fillna("").astype(str).str.strip().str.lower()
            df = df[~st_low.isin(["failed", "failure", "cancelled", "void", "rejected", "pending"])].copy()

    if df.empty:
        return pd.DataFrame()

    # 4. Transaction ID
    id_col = cmap.get("transaction id") or cmap.get("txn_ref_number") or cmap.get("txn id")
    df["_txn_id"] = clean_id_series(df[id_col]) if id_col else [f"{filename}_{i}" for i in range(len(df))]

    # 5. Amount: Sale Amount -> Total Amount -> Txn Amount -> Txn Amt -> paid_amount
    amt = pd.Series(0.0, index=df.index)
    for k in ("sale amount", "total amount", "txn amount", "txn amt", "paid_amount", "amount"):
        if k in cmap:
            ca = pd.to_numeric(df[cmap[k]], errors="coerce").fillna(0.0)
            amt = amt.where(amt > 0, ca)
    df["_amt"] = amt
    df = df[df["_amt"] >= 1000].copy()
    if df.empty:
        return pd.DataFrame()

    # 6. Branch resolution
    mid_col = cmap.get("merchant id") or cmap.get("merchant_code")
    raw_br = df[br_col] if br_col else pd.Series("", index=df.index)
    raw_st = df[st1_col] if st1_col else raw_br
    raw_mid = clean_id_series(df[mid_col]) if mid_col else pd.Series("", index=df.index)

    resolved = [
        resolver.normalize(b, s, m, provider="benow")
        for b, s, m in zip(raw_br, raw_st, raw_mid)
    ]
    df["_branch"] = [r[0] for r in resolved]
    df["_branch_code"] = [r[1] for r in resolved]
    df = df[df["_branch"] != ""].copy()
    if df.empty:
        return pd.DataFrame()

    df["_store_raw"] = raw_st.loc[df.index].fillna("").astype(str).str.strip()

    # 7. Serial / IMEI
    ser_col = cmap.get("serial number") or cmap.get("imei") or cmap.get("product_serial")
    df["_imei"] = clean_id_series(df[ser_col]) if ser_col else ""

    # 8. Brand, Product, Category
    b_col = cmap.get("brand") or cmap.get("brand name") or cmap.get("brand_code")
    raw_brand = df[b_col].fillna("").astype(str).str.strip() if b_col else pd.Series("", index=df.index)
    # Clean region codes like 'SAMSUNG_SOUTH 1' / 'OPPOIN10' / 'Non Brand'
    def _clean_bn_brand(v: str) -> str:
        u = v.upper().strip()
        if u in ("", "NAN", "NONE", "NON BRAND", "BANK EMI", "INKL01"):
            return ""
        for kb in ("SAMSUNG", "APPLE", "VIVO", "OPPO", "REALME", "XIAOMI", "ONEPLUS", "HP", "DELL", "LENOVO", "ASUS", "ACER", "LG", "HAIER", "BOSCH", "IFB", "WHIRLPOOL", "VOLTAS", "DAIKIN", "BLUE STAR", "LLOYD", "GODREJ", "SONY", "PANASONIC", "TCL", "MOTOROLA", "GOOGLE", "NOTHING", "TECNO", "INFINIX", "HONOR", "IQOO"):
            if kb in u:
                return kb.title() if len(kb) > 3 else kb
        return clean_brand_name(v) or ""

    df["_brand"] = raw_brand.apply(_clean_bn_brand)

    p_col = cmap.get("product") or cmap.get("product name") or cmap.get("model_number")
    pc_col = cmap.get("product category") or cmap.get("category_code")
    df["_prod_cat_raw"] = df[pc_col].fillna("").astype(str).str.strip() if pc_col else ""
    raw_prod = df[p_col].fillna("").astype(str).str.strip() if p_col else pd.Series("", index=df.index)

    def _map_bn_cat(prod_val: str, pcat_val: str, brand_raw_val: str) -> tuple[str, str, bool]:
        bu = brand_raw_val.strip().upper()
        if bu in ("BANK EMI", "NON BRAND"):
            return "BANK EMI", "BANK EMI", False
        for txt in (prod_val, pcat_val):
            s = txt.strip().lower()
            if not s or s in ("nan", "none"):
                continue
            if any(w in s for w in ("mobile", "phone", "galaxy", "iphone", "pad", "tablet", "sm-")):
                return ("TABLET" if ("pad" in s or "tablet" in s) else "MOBILE"), "MOBILE", False
            if any(w in s for w in ("laptop", "desktop", "macbook", "notebook")):
                return "LAPTOP", "LAPTOP", False
            if any(w in s for w in ("panal", "panel", "tv", "television", "led", "qled", "oled")):
                return "TV", "CD PRODUCTS", False
            if s == "wm" or "washing" in s:
                return "WASHING MACHINES", "CD PRODUCTS", False
            if any(w in s for w in ("rer", "ref", "refrigeranor", "refrigerator")):
                return "REFRIGERATORS", "CD PRODUCTS", False
            if s in ("ac", "rac") or "air conditioner" in s:
                return "AIR CONDITIONER", "CD PRODUCTS", False
            if any(w in s for w in ("smart watch", "watch", "wearable", "ear", "buds", "audio")):
                return "EAR WEARABLES", "HVA", False
            if "prebook" in s:
                return "MOBILE", "MOBILE", False
        return "BANK EMI", "BANK EMI", True

    mapped_bn = [
        _map_bn_cat(p, pc, br_r)
        for p, pc, br_r in zip(raw_prod, df["_prod_cat_raw"], raw_brand)
    ]
    df["_product"] = [m[0] for m in mapped_bn]
    df["_erp_cat"] = [m[1] for m in mapped_bn]
    df["_needs_erp_cat"] = [m[2] for m in mapped_bn]

    return df[[
        "_txn_id", "_date", "_amt", "_branch", "_branch_code", "_store_raw",
        "_imei", "_brand", "_product", "_prod_cat_raw", "_erp_cat", "_needs_erp_cat",
    ]].copy()


# ---------------------------------------------------------------------------
# ERP Quarter Fetch & Matching (Fast Bulk Matching + Category Enrichment)
# ---------------------------------------------------------------------------

def fetch_erp_range(client, d_min: str, d_max: str) -> pd.DataFrame:
    sql = f"""
    SELECT
        upper(trim(s.branch))                       AS branch_code,
        toString(toDate(s.date))                    AS sale_date,
        round(toFloat64(s.sold_price), 0)           AS sale_amt,
        toString(s.imei_batch)                      AS imei,
        toString(s.invoice_no)                      AS invoice_no,
        upper(trim(coalesce(i.brand, '')))          AS erp_brand,
        upper(trim(coalesce(i.product, '')))        AS erp_product,
        CASE
            WHEN upper(trim(coalesce(i.product, ''))) IN ('MOBILE','TABLET','TAB') THEN 'MOBILE'
            WHEN upper(trim(coalesce(i.product, ''))) IN ('LAPTOP','DESKTOP') THEN 'LAPTOP'
            WHEN upper(trim(coalesce(i.product, ''))) LIKE '%SMALL APPLIANCE%' THEN 'SA'
            WHEN upper(trim(coalesce(i.product, ''))) IN (
                'AIR CONDITIONER','DISH WASHER','DRYER','FREEZER',
                'HOME APPLIANCES','MICROWAVE OVEN','REFRIGERATORS','TV','WASHING MACHINES'
            ) THEN 'CD PRODUCTS'
            WHEN upper(trim(coalesce(i.product, ''))) IN (
                'AUDIO','EAR WEARABLES','GAMING','HOME THEATRE','SMART WATCH','CEGI'
            ) THEN 'HVA'
            WHEN upper(trim(coalesce(i.category, ''))) = 'TELECOM' THEN 'MOBILE'
            WHEN upper(trim(coalesce(i.category, ''))) IN ('CONSUMER ELECTRONICS','HOME SOLUTIONS') THEN 'CD PRODUCTS'
            WHEN upper(trim(coalesce(i.category, ''))) = 'IT' THEN 'LAPTOP'
            ELSE 'OTHERS'
        END                                         AS erp_cat
    FROM azure_sales_report s
    LEFT JOIN item_master i ON s.item_code = i.item_code
    WHERE toDate(s.date) BETWEEN toDate('{d_min}') AND toDate('{d_max}')
      AND s.invoice_no IS NOT NULL AND s.invoice_no != ''
      AND round(toFloat64(s.sold_price), 0) >= 1000
      AND (i.category IS NULL OR upper(trim(i.category)) NOT IN (
          'VALUE ADDED SERVICE', 'ENDPOINT PROTECTION', 'INFRA', 'PROJECT'
      ))
    """
    res = client.query(sql)
    erp_df = pd.DataFrame(
        res.result_rows,
        columns=["branch_code", "sale_date", "sale_amt", "imei", "invoice_no", "erp_brand", "erp_product", "erp_cat"],
    )
    if not erp_df.empty:
        erp_df["sale_amt"] = pd.to_numeric(erp_df["sale_amt"], errors="coerce")
    return erp_df


def match_and_enrich_month(
    df_m: pd.DataFrame, financier_label: str, ym: str, erp_df: pd.DataFrame, min_match_amt: float = 1000.0
) -> tuple[pd.DataFrame, list[tuple[str, str, str, str]]]:
    """
    Match a single month's cleaned card DataFrame against ERP rows:
      1. Enriches _erp_cat (and _brand/_product where flagged _needs_erp_cat) via IMEI or (branch, date, amt).
      2. Builds benow_pine_financier tuples: (imei, financier_label, invoice_no, ym).
    """
    if df_m.empty:
        return df_m, []

    df = df_m.copy()
    df["_date_str"] = df["_date"].dt.strftime("%Y-%m-%d")
    df["_round_amt"] = df["_amt"].round(0)

    # Slice ERP to this month
    erp_m = erp_df[erp_df["sale_date"].str.startswith(ym)].copy() if not erp_df.empty else pd.DataFrame()

    # Optional category enrichment for historical rows lacking product columns
    if "_needs_erp_cat" in df.columns and df["_needs_erp_cat"].any() and not erp_m.empty:
        # First by real IMEI
        imei_lookup = (
            erp_m[erp_m["imei"].ne("") & (erp_m["erp_cat"] != "")]
            .drop_duplicates(subset=["imei"], keep="first")
            .set_index("imei")[["erp_cat", "erp_product", "erp_brand"]]
        )
        need_idx = df.index[df["_needs_erp_cat"] & df["_imei"].isin(imei_lookup.index)]
        if len(need_idx) > 0:
            matched_info = imei_lookup.loc[df.loc[need_idx, "_imei"]]
            df.loc[need_idx, "_erp_cat"] = matched_info["erp_cat"].values
            if "_product" in df.columns:
                df.loc[need_idx, "_product"] = matched_info["erp_product"].values
            if "_pl_product" in df.columns:
                df.loc[need_idx, "_pl_product"] = matched_info["erp_product"].values
            if "_brand" in df.columns:
                df.loc[need_idx, "_brand"] = matched_info["erp_brand"].values
            df.loc[need_idx, "_needs_erp_cat"] = False

        # Second by (branch_code, sale_date, sale_amt)
        still_need = df.index[df["_needs_erp_cat"] & df["_branch_code"].notna()]
        if len(still_need) > 0:
            key_lookup = (
                erp_m[erp_m["erp_cat"] != ""]
                .drop_duplicates(subset=["branch_code", "sale_date", "sale_amt"], keep="first")
                .set_index(["branch_code", "sale_date", "sale_amt"])[["erp_cat", "erp_product", "erp_brand"]]
            )
            keys = list(zip(
                df.loc[still_need, "_branch_code"].astype(str).str.upper(),
                df.loc[still_need, "_date_str"],
                df.loc[still_need, "_round_amt"],
            ))
            cats, prods, brands = [], [], []
            for idx_i, k in zip(still_need, keys):
                if k in key_lookup.index:
                    row_k = key_lookup.loc[k]
                    df.at[idx_i, "_erp_cat"] = str(row_k["erp_cat"])
                    if "_product" in df.columns and not df.at[idx_i, "_product"]:
                        df.at[idx_i, "_product"] = str(row_k["erp_product"])
                    if "_pl_product" in df.columns:
                        df.at[idx_i, "_pl_product"] = str(row_k["erp_product"])
                    if "_brand" in df.columns and not df.at[idx_i, "_brand"]:
                        df.at[idx_i, "_brand"] = str(row_k["erp_brand"]).title()

    # Also enrich Brand EMI rows that fell through to BANK EMI if their real IMEI is in erp_m
    if "_is_bank_emi" in df.columns and not erp_m.empty:
        brand_fallback_idx = df.index[(~df["_is_bank_emi"]) & (df["_erp_cat"] == "BANK EMI") & df["_imei"].ne("")]
        if len(brand_fallback_idx) > 0:
            imei_lookup = (
                erp_m[erp_m["imei"].ne("") & (erp_m["erp_cat"] != "")]
                .drop_duplicates(subset=["imei"], keep="first")
                .set_index("imei")[["erp_cat", "erp_product"]]
            )
            hit_idx = [i for i in brand_fallback_idx if df.at[i, "_imei"] in imei_lookup.index]
            for i in hit_idx:
                info = imei_lookup.loc[df.at[i, "_imei"]]
                df.at[i, "_erp_cat"] = str(info["erp_cat"])
                df.at[i, "_product"] = str(info["erp_product"])

    # Build benow_pine_financier records (Phase 1: (branch, date, amount) invoice match; Phase 2: real IMEI fallback)
    records_inv: list[tuple[str, str, str, str]] = []
    matchable = df[
        df["_branch_code"].notna() & (df["_branch_code"] != "") & (df["_round_amt"] >= min_match_amt)
    ]
    if not matchable.empty and not erp_m.empty:
        serial_set = set(df["_imei"].loc[df["_imei"].apply(is_real_imei)].tolist())
        erp_work = erp_m.copy()
        erp_work["_imei_hit"] = erp_work["imei"].isin(serial_set)
        erp_work = erp_work.sort_values("_imei_hit", ascending=False, kind="stable")

        key_cols = ["branch_code", "sale_date", "sale_amt"]
        pk = matchable[["_branch_code", "_date_str", "_round_amt"]].copy()
        pk.columns = key_cols
        pk["branch_code"] = pk["branch_code"].astype(str).str.upper()
        txn_counts = pk.groupby(key_cols).size().rename("_n").reset_index()

        merged = erp_work.merge(txn_counts, on=key_cols, how="inner")
        merged["_rank"] = merged.groupby(key_cols).cumcount()
        merged = merged[merged["_rank"] < merged["_n"]]

        for imei_v, inv_v, sdate_v in zip(merged["imei"], merged["invoice_no"], merged["sale_date"]):
            inv_s = str(inv_v or "").strip()
            if inv_s:
                records_inv.append((str(imei_v or "").strip(), financier_label, inv_s, str(sdate_v)[:7] or ym))

    matched_imeis = {r[0] for r in records_inv if r[0]}
    real_imeis = [
        im for im in df["_imei"].unique().tolist()
        if is_real_imei(im) and im not in matched_imeis
    ]
    records_direct = [(im, financier_label, "", ym) for im in real_imeis]

    seen_keys: set[str] = set()
    final_bpf: list[tuple[str, str, str, str]] = []
    for imei_v, fin_v, inv_v, dm_v in records_inv + records_direct:
        k = inv_v if inv_v else f"_imei_{imei_v}_{dm_v}"
        if k not in seen_keys:
            seen_keys.add(k)
            final_bpf.append((imei_v, fin_v, inv_v, dm_v))

    return df, final_bpf


# ---------------------------------------------------------------------------
# Archive Discovery & Orchestration
# ---------------------------------------------------------------------------

def load_tracker() -> dict:
    if os.path.exists(STATE_TRACKER_PATH):
        try:
            with open(STATE_TRACKER_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"processed_sources": {}}


def save_tracker(tracker: dict) -> None:
    os.makedirs(os.path.dirname(STATE_TRACKER_PATH), exist_ok=True)
    tmp = STATE_TRACKER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(tracker, fh, indent=2)
    os.replace(tmp, STATE_TRACKER_PATH)


def clean_pinelab_portal_csv_df(
    df_raw: pd.DataFrame, filename: str, expected_ym: Optional[str], resolver: BranchResolver
) -> pd.DataFrame:
    """Clean raw Pine Labs portal CSV exports ('EMI Transactions report ...') using canonical pine_lab_cleaner."""
    import pine_lab_cleaner as plc

    if df_raw.empty:
        return pd.DataFrame()

    df_clean, _ = plc.clean_pine_lab(df_raw)
    if df_clean.empty:
        return pd.DataFrame()

    # Preserve Oct 2026 Angamaly separate-login slot if present locally
    ang_pkl = os.path.join(BASE_DIR, "data", "_pinelab_angamaly_cleaned.pkl")
    if expected_ym == "2026-10" and os.path.exists(ang_pkl):
        try:
            df_ang = pd.read_pickle(ang_pkl)
            if df_ang is not None and not df_ang.empty:
                df_clean = pd.concat([df_clean, df_ang], ignore_index=True)
                df_clean[plc.COL_TXN_ID] = df_clean[plc.COL_TXN_ID].astype(str).str.strip()
                df_clean = df_clean.drop_duplicates(subset=[plc.COL_TXN_ID], keep="first").reset_index(drop=True)
                log.info("  Merged Oct 2026 Angamaly slot (%d rows) -> %d combined rows", len(df_ang), len(df_clean))
        except Exception as exc:
            log.warning("  Could not merge Oct 2026 Angamaly pkl: %s", exc)

    out = pd.DataFrame(index=df_clean.index)
    out["_txn_id"] = clean_id_series(df_clean[plc.COL_TXN_ID])
    out["_date"] = pd.to_datetime(df_clean[plc.COL_DATE], dayfirst=True, format="mixed", errors="coerce").dt.normalize()
    out["_amt"] = pd.to_numeric(df_clean[plc.COL_ORIG_AMT], errors="coerce").fillna(0.0)

    pos_col = "POS" if "POS" in df_clean.columns else ("POS ID" if "POS ID" in df_clean.columns else None)
    raw_br = df_clean["Branch"] if "Branch" in df_clean.columns else pd.Series("", index=df_clean.index)
    raw_st = df_clean[plc.COL_STORE] if plc.COL_STORE in df_clean.columns else pd.Series("", index=df_clean.index)
    raw_pos = df_clean[pos_col] if pos_col else pd.Series("", index=df_clean.index)

    resolved = [resolver.normalize(b, s, p, provider="pinelab") for b, s, p in zip(raw_br, raw_st, raw_pos)]
    # Keep clean_pine_lab's Branch if resolver couldn't map a rare branch name
    br_names, br_codes = [], []
    for (rn, rc), orig_b in zip(resolved, raw_br):
        if rn:
            br_names.append(rn)
            br_codes.append(rc)
        else:
            ob = str(orig_b or "").strip().upper()
            br_names.append(ob)
            br_codes.append(resolver.name_to_code.get(ob))
    out["_branch"] = br_names
    out["_branch_code"] = br_codes

    rbm_s = df_clean["RBM"].fillna("").astype(str).str.strip().str.upper() if "RBM" in df_clean.columns else pd.Series("", index=df_clean.index)
    out["_state"] = [
        "KARNATAKA" if (r == "KARNATAKA RBM" or b in ("FALNIR FUTURE", "FLF")) else "KERALA"
        for r, b in zip(rbm_s, out["_branch"])
    ]
    out["_imei"] = clean_id_series(df_clean[plc.COL_SERIAL]) if plc.COL_SERIAL in df_clean.columns else ""
    out["_product"] = df_clean["Product"].fillna("").astype(str).str.strip().str.upper() if "Product" in df_clean.columns else ""
    out["_erp_cat"] = df_clean["Category"].fillna("OTHERS").astype(str).str.strip().str.upper() if "Category" in df_clean.columns else "OTHERS"
    out["_is_bank_emi"] = out["_erp_cat"] == "BANK EMI"

    out = out[out["_date"].notna()].copy()
    return out


def discover_work_items(folder: str, force: bool, tracker: dict):
    """
    Recursively scan `folder` (including quarterly subfolders like AMJ 2026 / JAS 2026 / OND 2026)
    for .zip archives and loose .xlsx/.xls/.csv files.
    Returns (new_source_keys_meta, all_items_for_touched_months).
    """
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Source folder not found: {folder}")

    processed = tracker.get("processed_sources", {})
    all_entries = []
    new_sources = {}

    file_list = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            if f.startswith("~$") or f.startswith("."):
                continue
            fp = os.path.join(root, f)
            rel_key = os.path.relpath(fp, folder).replace("\\", "/")
            file_list.append((rel_key, fp))

    for entry, full_path in file_list:
        st = os.stat(full_path)
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        is_new = force or (processed.get(entry) != sig)

        low = entry.lower()
        if low.endswith(".zip"):
            if is_new:
                new_sources[entry] = sig
            with zipfile.ZipFile(full_path, "r") as zf:
                for member in sorted(zf.namelist()):
                    if member.endswith("/") or not member.lower().endswith((".xlsx", ".xls", ".csv")):
                        continue
                    prov = detect_provider(member) or detect_provider(entry)
                    if not prov:
                        continue
                    ym = infer_ym_from_filename(member) or infer_ym_from_filename(entry)
                    all_entries.append((entry, is_new, full_path, member, prov, ym))
        elif low.endswith((".xlsx", ".xls", ".csv")):
            prov = detect_provider(entry)
            if prov:
                if is_new:
                    new_sources[entry] = sig
                ym = infer_ym_from_filename(entry)
                all_entries.append((entry, is_new, full_path, None, prov, ym))

    return new_sources, all_entries


def ensure_tables(client) -> None:
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
    client.command("""
        CREATE TABLE IF NOT EXISTS pine_lab_transactions (
            txn_date        Date,
            data_month      String,
            pl_category     String DEFAULT '',
            erp_category    String DEFAULT 'OTHERS',
            qty             Int32  DEFAULT 1,
            state           String DEFAULT 'KERALA',
            original_amount Float64 DEFAULT 0.0,
            branch          String DEFAULT ''
        )
        ENGINE = MergeTree()
        ORDER BY (data_month, txn_date, erp_category)
    """)
    client.command("""
        CREATE TABLE IF NOT EXISTS paytm_transactions (
            txn_date         Date,
            txn_id           String,
            store            String,
            brand            String,
            product_name     String,
            product_category String,
            pl_product       String,
            pl_category      String,
            erp_category     String,
            qty              Float32,
            original_amount  Float32,
            data_month       String,
            branch           String DEFAULT ''
        )
        ENGINE = MergeTree()
        ORDER BY (data_month, txn_date)
    """)
    client.command("""
        CREATE TABLE IF NOT EXISTS benow_transactions (
            txn_date         Date,
            txn_id           String,
            store            String,
            brand            String,
            product_name     String,
            product_category String,
            erp_category     String,
            qty              Float32,
            original_amount  Float32,
            data_month       String,
            branch           String DEFAULT ''
        )
        ENGINE = MergeTree()
        ORDER BY (data_month, txn_date)
    """)


def main():
    parser = argparse.ArgumentParser(description="Bulk load historical Pine Lab, PayTM, and Benow archives into ClickHouse.")
    parser.add_argument("--folder", default=DEFAULT_SOURCE_DIR, help="Folder containing quarterly ZIPs or Excel/CSV files")
    parser.add_argument("--force", action="store_true", help="Re-process all archives even if already in _bulk_card_ingested.json")
    parser.add_argument("--dry-run", action="store_true", help="Clean and summarize all files without writing to ClickHouse")
    args = parser.parse_args()

    t0 = time.time()
    client = get_client()
    if not args.dry_run:
        ensure_tables(client)

    # Load branch_master lookup once
    bm_rows = client.query(
        "SELECT upperUTF8(trim(code)) AS c, upperUTF8(trim(branch_name)) AS bn FROM branch_master WHERE code != '' AND branch_name != ''"
    ).result_rows
    name_to_code = {bn: c for c, bn in bm_rows}
    code_to_name = {c: bn for c, bn in bm_rows}
    resolver = BranchResolver(name_to_code, code_to_name)

    tracker = load_tracker()
    new_sources, all_entries = discover_work_items(args.folder, args.force, tracker)
    if not new_sources:
        log.info("No new or modified archives found in %s. Pass --force to reload all.", args.folder)
        emi_state.sync_from_clickhouse(force=True)
        return

    log.info("Found %d new/updated archive(s) to ingest: %s", len(new_sources), sorted(new_sources.keys()))

    # Identify (provider, ym) touched by new sources; include any sibling files for the same (provider, ym)
    touched_prov_ym = {(prov, ym) for _, is_new, _, _, prov, ym in all_entries if is_new and ym}
    selected_entries = [
        e for e in all_entries
        if e[1] or ((e[4], e[5]) in touched_prov_ym)
    ]

    # Read & clean files grouped by (provider, YYYY-MM)
    grouped_dfs: dict[tuple[str, str], list[pd.DataFrame]] = defaultdict(list)
    zip_handles: dict[str, zipfile.ZipFile] = {}

    try:
        for src_name, _, full_path, member, prov, ym_hint in selected_entries:
            display_name = f"{src_name}::{os.path.basename(member)}" if member else src_name
            if member:
                zf = zip_handles.get(full_path)
                if zf is None:
                    zf = zipfile.ZipFile(full_path, "r")
                    zip_handles[full_path] = zf
                raw_bytes = zf.read(member)
                fname = os.path.basename(member)
            else:
                with open(full_path, "rb") as fh:
                    raw_bytes = fh.read()
                fname = src_name

            if prov == "pinelab" and "emi transactions report" in fname.lower() and fname.lower().endswith(".csv"):
                df_sheet = pd.read_csv(io.BytesIO(raw_bytes), low_memory=False)
                cleaned = clean_pinelab_portal_csv_df(df_sheet, fname, ym_hint, resolver)
            else:
                df_sheet = pick_sheet_and_read(raw_bytes, fname)
                if prov == "pinelab":
                    cleaned = clean_pinelab_archive_df(df_sheet, fname, ym_hint, resolver)
                elif prov == "paytm":
                    cleaned = clean_paytm_archive_df(df_sheet, fname, ym_hint, resolver)
                else:
                    cleaned = clean_benow_archive_df(df_sheet, fname, ym_hint, resolver)

            if cleaned.empty:
                log.warning("  [EMPTY] %s -> 0 valid rows", display_name)
                continue

            # Split by actual transaction YYYY-MM so misplaced rows land in their true month
            cleaned["_ym"] = cleaned["_date"].dt.strftime("%Y-%m")
            for ym_val, sub_df in cleaned.groupby("_ym"):
                grouped_dfs[(prov, str(ym_val))].append(sub_df.drop(columns=["_ym"]))
            log.info("  [CLEANED] %-55s -> %5d rows | Rs %13.2f", display_name, len(cleaned), cleaned["_amt"].sum())
    finally:
        for zf in zip_handles.values():
            zf.close()

    if not grouped_dfs:
        log.warning("No valid transactions extracted from selected files.")
        return

    # Consolidate per (provider, ym) & deduplicate Pine Lab across Bank+Brand+myEMI
    monthly_consolidated: dict[tuple[str, str], pd.DataFrame] = {}
    all_yms = sorted({ym for _, ym in grouped_dfs.keys()})

    for (prov, ym), frames in sorted(grouped_dfs.items()):
        comb = pd.concat(frames, ignore_index=True)
        if prov == "pinelab":
            # Prefer Brand EMI (specific product category) if a Transaction Id ever appears twice, else keep first
            comb = comb.sort_values("_is_bank_emi", ascending=True, kind="stable")
            comb = comb.drop_duplicates(subset=["_txn_id"], keep="first").reset_index(drop=True)
        elif prov == "paytm":
            comb = comb.drop_duplicates(subset=["_txn_id"], keep="first").reset_index(drop=True)
        monthly_consolidated[(prov, ym)] = comb

    # Group months by calendar quarter (YYYY-Q) so we run only 1 ERP query per quarter!
    quarters: dict[str, list[str]] = defaultdict(list)
    for ym in all_yms:
        yr, mo = int(ym[:4]), int(ym[5:7])
        q_key = f"{yr}-Q{(mo - 1) // 3 + 1}"
        quarters[q_key].append(ym)

    summary_rows = []

    for q_key, q_months in sorted(quarters.items()):
        q_min_d = f"{min(q_months)}-01"
        last_ym = max(q_months)
        last_end = (pd.Timestamp(f"{last_ym}-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
        log.info("=== Processing Quarter %s (%s to %s) ===", q_key, q_min_d, last_end)

        erp_q_df = fetch_erp_range(client, q_min_d, last_end)
        log.info("  Fetched %d ERP sales rows for %s", len(erp_q_df), q_key)

        pl_rows_batch, pl_months_batch, pl_bpf_batch = [], [], []
        pt_rows_batch, pt_months_batch, pt_bpf_batch = [], [], []
        bn_rows_batch, bn_months_batch, bn_bpf_batch = [], [], []

        for ym in sorted(q_months):
            # 1. Pine Lab
            if ("pinelab", ym) in monthly_consolidated:
                df_pl, bpf_pl = match_and_enrich_month(
                    monthly_consolidated[("pinelab", ym)], "Pine Lab EMI", ym, erp_q_df, min_match_amt=3000.0
                )
                pl_months_batch.append(ym)
                pl_bpf_batch.extend(bpf_pl)
                pl_rows_batch.extend(zip(
                    df_pl["_date"].dt.date,
                    [ym] * len(df_pl),
                    df_pl["_product"].astype(str),
                    df_pl["_erp_cat"].astype(str),
                    [1] * len(df_pl),
                    df_pl["_state"].astype(str),
                    df_pl["_amt"].astype(float),
                    df_pl["_branch"].astype(str),
                ))
                summary_rows.append((ym, "PineLab", len(df_pl), float(df_pl["_amt"].sum()), len(bpf_pl)))

            # 2. PayTM
            if ("paytm", ym) in monthly_consolidated:
                df_pt, bpf_pt = match_and_enrich_month(
                    monthly_consolidated[("paytm", ym)], "PAYTM", ym, erp_q_df, min_match_amt=1000.0
                )
                pt_months_batch.append(ym)
                pt_bpf_batch.extend(bpf_pt)
                pt_rows_batch.extend(zip(
                    df_pt["_date"].dt.date,
                    df_pt["_txn_id"].astype(str),
                    df_pt["_store_raw"].astype(str),
                    df_pt["_brand"].astype(str),
                    df_pt["_prod_name_raw"].astype(str),
                    df_pt["_prod_cat_raw"].astype(str),
                    df_pt["_pl_product"].astype(str),
                    df_pt["_erp_cat"].astype(str),
                    df_pt["_erp_cat"].astype(str),
                    [1.0] * len(df_pt),
                    df_pt["_amt"].astype(float),
                    [ym] * len(df_pt),
                    df_pt["_branch"].astype(str),
                ))
                summary_rows.append((ym, "PAYTM", len(df_pt), float(df_pt["_amt"].sum()), len(bpf_pt)))

            # 3. Benow
            if ("benow", ym) in monthly_consolidated:
                df_bn, bpf_bn = match_and_enrich_month(
                    monthly_consolidated[("benow", ym)], "Benow", ym, erp_q_df, min_match_amt=1000.0
                )
                bn_months_batch.append(ym)
                bn_bpf_batch.extend(bpf_bn)
                bn_rows_batch.extend(zip(
                    df_bn["_date"].dt.date,
                    df_bn["_txn_id"].astype(str),
                    df_bn["_store_raw"].astype(str),
                    df_bn["_brand"].astype(str),
                    df_bn["_product"].astype(str),
                    df_bn["_prod_cat_raw"].astype(str),
                    df_bn["_erp_cat"].astype(str),
                    [1.0] * len(df_bn),
                    df_bn["_amt"].astype(float),
                    [ym] * len(df_bn),
                    df_bn["_branch"].astype(str),
                ))
                summary_rows.append((ym, "Benow", len(df_bn), float(df_bn["_amt"].sum()), len(bpf_bn)))

        if not args.dry_run:
            if pl_months_batch:
                m_in = ", ".join(f"'{m}'" for m in pl_months_batch)
                replace_rows(
                    "pine_lab_transactions", pl_rows_batch,
                    ["txn_date", "data_month", "pl_category", "erp_category", "qty", "state", "original_amount", "branch"],
                    f"data_month IN ({m_in})",
                )
                replace_rows(
                    "benow_pine_financier", pl_bpf_batch,
                    ["imei", "financier", "invoice_no", "data_month"],
                    f"data_month IN ({m_in}) AND financier = 'Pine Lab EMI'",
                )
                log.info("  Inserted PineLab %s: %d txns, %d BPF links", pl_months_batch, len(pl_rows_batch), len(pl_bpf_batch))

            if pt_months_batch:
                m_in = ", ".join(f"'{m}'" for m in pt_months_batch)
                replace_rows(
                    "paytm_transactions", pt_rows_batch,
                    ["txn_date", "txn_id", "store", "brand", "product_name", "product_category",
                     "pl_product", "pl_category", "erp_category", "qty", "original_amount", "data_month", "branch"],
                    f"data_month IN ({m_in})",
                )
                replace_rows(
                    "benow_pine_financier", pt_bpf_batch,
                    ["imei", "financier", "invoice_no", "data_month"],
                    f"data_month IN ({m_in}) AND financier = 'PAYTM'",
                )
                log.info("  Inserted PayTM %s: %d txns, %d BPF links", pt_months_batch, len(pt_rows_batch), len(pt_bpf_batch))

            if bn_months_batch:
                m_in = ", ".join(f"'{m}'" for m in bn_months_batch)
                replace_rows(
                    "benow_transactions", bn_rows_batch,
                    ["txn_date", "txn_id", "store", "brand", "product_name", "product_category",
                     "erp_category", "qty", "original_amount", "data_month", "branch"],
                    f"data_month IN ({m_in})",
                )
                replace_rows(
                    "benow_pine_financier", bn_bpf_batch,
                    ["imei", "financier", "invoice_no", "data_month"],
                    f"data_month IN ({m_in}) AND financier = 'Benow'",
                )
                log.info("  Inserted Benow %s: %d txns, %d BPF links", bn_months_batch, len(bn_rows_batch), len(bn_bpf_batch))

    if not args.dry_run:
        client.command("OPTIMIZE TABLE benow_pine_financier FINAL")
        emi_state.sync_from_clickhouse(force=True)
        tracker.setdefault("processed_sources", {}).update(new_sources)
        tracker["last_run_at"] = datetime.now().isoformat(timespec="seconds")
        save_tracker(tracker)

    print("\n" + "=" * 78)
    print(f"{'MONTH':<10} {'PROVIDER':<12} {'TXN QTY':>10} {'AMOUNT (Cr)':>14} {'ERP/IMEI LINKS':>16}")
    print("-" * 78)
    tot_qty, tot_amt, tot_bpf = 0, 0.0, 0
    for ym, prov, q, amt, bpf_n in sorted(summary_rows):
        tot_qty += q
        tot_amt += amt
        tot_bpf += bpf_n
        print(f"{ym:<10} {prov:<12} {q:>10,d} {amt / 1e7:>14.2f} {bpf_n:>16,d}")
    print("-" * 78)
    print(f"{'TOTAL':<23} {tot_qty:>10,d} {tot_amt / 1e7:>14.2f} {tot_bpf:>16,d}")
    print("=" * 78)
    log.info("Completed in %.1fs (dry_run=%s)", time.time() - t0, args.dry_run)


if __name__ == "__main__":
    main()
