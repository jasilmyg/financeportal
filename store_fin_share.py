"""
Store Fin Share (Value + Volume) - computed entirely from ClickHouse.

Logic (same as the client's OCT / OCT Vol Excel sheets, but nothing is copied from the Excel):

  FIN        = Paper finance (BAJAJ, HDB, IDFC, PFL, TVS, DMI from ERP sales)
             + Card EMI (PineLab + PAYTM + Benow from the uploaded card files, stored in ClickHouse)
  TOTAL      = all ERP sales of the store (value in Rs / quantity)
  FIN SHARE  = FIN / TOTAL
  Benchmark  = the same FIN SHARE for the previous calendar quarter (AMJ / JAS / OND / JFM)
  DIF        = FIN SHARE - Benchmark

  Stores, RBM, BDM  : from the ERP sales + ClickHouse `branch_master`
  Fin BDM (TM)      : the dashboard's existing store -> Fin BDM map (sales_cleaner.STORE_FIN_BDM_MAP)
  TM table          : roll-up of the store rows (+ North / South by the zone of each TM's stores)
  Day counters      : elapsed days = days in the selected period, remaining = days left in the month
  CRR = FIN / elapsed ; DRR = (Target - FIN) / remaining ; Ach % = FIN / Target ;
  Trend = CRR * days_in_month / Target
  Targets (value target, card slab 1/2) are management inputs, kept in ClickHouse table `tm_fin_targets`.
"""
import calendar
import json
import logging
import os
import re
import time
from datetime import date, datetime

import pandas as pd

import emi_state
from clickhouse_client import query_df

logger = logging.getLogger(__name__)

PAPER_FINS = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]
NORTH_RBMS = {"ALFAS", "SAMEER", "SAMEEM"}
CATCHALL_RBMS = {"", "OTHERS", "KARNATAKA RBM"}
CARD_TABLES = (
    ("pine_lab_transactions", emi_state.PINE_FIN, "PineLab"),
    ("paytm_transactions", emi_state.PAYTM_FIN, "PAYTM"),
    ("benow_transactions", emi_state.BENOW_FIN, "Benow"),
)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_MEM_CACHE: dict = {}
_ERP_Q_CACHE: dict = {}
_NAMES_CACHE = {"t": 0.0, "v": {}}

TARGETS_DDL = """
CREATE TABLE IF NOT EXISTS tm_fin_targets (
    month        String,
    tm           String,
    value_target Float64,
    card_slab_1  Float64,
    card_slab_2  Float64,
    updated_at   DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (month, tm)
"""


# --------------------------------------------------------------------------- helpers
def _branch_names() -> dict:
    """branch code -> store name from ClickHouse branch_master (10 minute cache)."""
    if time.time() - _NAMES_CACHE["t"] < 600 and _NAMES_CACHE["v"]:
        return _NAMES_CACHE["v"]
    names = {}
    try:
        df = query_df(
            "SELECT upperUTF8(trim(code)) AS code, upperUTF8(trim(branch_name)) AS name FROM branch_master", {}
        )
        names = {
            str(r["code"]).strip().upper(): str(r["name"]).strip().upper()
            for _, r in df.iterrows()
            if r["code"] and r["name"]
        }
    except Exception as exc:
        logger.warning("branch_master lookup failed: %s", exc)
    _NAMES_CACHE.update(t=time.time(), v=names)
    return names


def _mode_text(series) -> str:
    s = series.astype(str).str.strip()
    s = s[(s != "") & (s.str.upper() != "NAN")]
    return s.mode().iat[0].upper() if len(s) else ""


def _erp_by_store(q_date: str, q_tf: str, q_end: str):
    """ERP totals per store: ({store: (tot_val, tot_qty, fin_val, fin_qty)}, {store: {rbm, bdm, fin_bdm}})."""
    import clickhouse_aggregator as ca

    df = ca.get_clickhouse_df(q_date, q_tf, end_date_str=q_end)
    if df is None or df.empty:
        return {}, {}
    names = _branch_names()
    tmp = df.copy()
    tmp["_S"] = tmp["Branch"].astype(str).str.strip().str.upper().map(lambda c: names.get(c, c))
    tmp["_P"] = tmp["Financier"].astype(str).str.strip().isin(PAPER_FINS)
    tot = tmp.groupby("_S").agg(tv=("Sold Price", "sum"), tq=("QTY", "sum"))
    fin = tmp[tmp["_P"]].groupby("_S").agg(fv=("Sold Price", "sum"), fq=("QTY", "sum"))
    meta_df = tmp.groupby("_S")[["RBM", "BDM", "Fin BDM"]].agg(_mode_text)
    totals, meta = {}, {}
    for s in tot.index:
        totals[s] = (
            float(tot.tv[s]),
            float(tot.tq[s]),
            float(fin.fv.get(s, 0.0)),
            float(fin.fq.get(s, 0.0)),
        )
        m = meta_df.loc[s]
        meta[s] = {"rbm": m["RBM"], "bdm": m["BDM"], "fin_bdm": m["Fin BDM"]}
    return totals, meta


def _card_by_store(start, end, filtered: bool):
    """Card EMI per store from the uploaded-file tables. Returns ({store: [val, qty]}, {partner: info})."""
    out, info = {}, {}
    for table, fin_key, label in CARD_TABLES:
        if filtered:
            cond = emi_state.date_filter_sql(fin_key, start, end)
        else:
            cond = f"txn_date >= toDate('{start}') AND txn_date <= toDate('{end}')"
        if cond is None:
            info[label] = {"uploaded": False, "stores": 0}
            continue
        sql = f"""
            SELECT upperUTF8(trim(branch)) AS br,
                   toFloat64(sum(original_amount)) AS val,
                   toInt64(sum(qty)) AS qty
            FROM {table}
            WHERE {cond}
            GROUP BY br
        """
        n_stores = 0
        try:
            res = query_df(sql, {})
            for _, r in res.iterrows():
                br = str(r["br"]).strip().upper()
                if not br:
                    continue
                n_stores += 1
                d = out.setdefault(br, [0.0, 0])
                d[0] += float(r["val"] or 0.0)
                d[1] += int(r["qty"] or 0)
        except Exception as exc:
            logger.warning("card query %s failed: %s", table, exc)
        info[label] = {"uploaded": n_stores > 0, "stores": n_stores}
    return out, info


def _prev_quarter(d: date):
    q = (d.month - 1) // 3 - 1
    y = d.year
    if q < 0:
        q, y = 3, y - 1
    sm = q * 3 + 1
    start = date(y, sm, 1)
    end = date(y, sm + 2, calendar.monthrange(y, sm + 2)[1])
    label = "".join(calendar.month_abbr[m][0] for m in (sm, sm + 1, sm + 2)).upper()
    return start, end, label


def _erp_quarter(start: date, end: date):
    """ERP totals for a past quarter (static data -> cached in memory and on disk)."""
    key = (start.isoformat(), end.isoformat())
    if key in _ERP_Q_CACHE:
        return _ERP_Q_CACHE[key]
    path = os.path.join(_BASE_DIR, "data", f"_fin_share_erp_{key[0]}_{key[1]}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                totals = {k: tuple(v) for k, v in json.load(fh).items()}
            _ERP_Q_CACHE[key] = totals
            return totals
        except Exception:
            pass
    totals, _ = _erp_by_store(key[0], "custom", key[1])
    _ERP_Q_CACHE[key] = totals
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(totals, fh)
    except Exception:
        pass
    return totals


def _benchmark(eff_start: date):
    """Fin share of the previous quarter per store -> ({store: (value_share, qty_share)}, info)."""
    b_start, b_end, label = _prev_quarter(eff_start)
    info = {
        "start_date": b_start.isoformat(),
        "end_date": b_end.isoformat(),
        "label": label,
        "complete": False,
        "warning": "",
    }
    erp = _erp_quarter(b_start, b_end)
    card, status = _card_by_store(b_start.isoformat(), b_end.isoformat(), filtered=False)
    missing = [k for k, v in status.items() if not v["stores"]]
    if len(missing) == len(status):
        info["warning"] = (
            f"{label} benchmark unavailable: no store-wise PineLab/PAYTM/Benow data for "
            f"{b_start:%d %b %Y} - {b_end:%d %b %Y}. Upload those card files to fill it."
        )
        return {}, info
    if missing:
        info["warning"] = f"{label} benchmark is missing card data for: {', '.join(missing)}."
    else:
        info["complete"] = True
    out = {}
    for s, (tv, tq, fv, fq) in erp.items():
        cv, cq = card.get(s, [0.0, 0])
        v_share = ((fv + cv) / tv) if tv > 0 else None
        q_share = ((fq + cq) / tq) if tq > 0 else None
        out[s] = (v_share, q_share)
    return out, info


# --------------------------------------------------------------------------- targets
def load_targets(month: str) -> dict:
    """{TM: {value_target, card_slab_1, card_slab_2}} for 'YYYY-MM' from ClickHouse (empty if none set)."""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        return {}
    sql = (
        "SELECT upperUTF8(trim(tm)) AS tm, value_target, card_slab_1, card_slab_2 "
        f"FROM tm_fin_targets FINAL WHERE month = '{month}'"
    )
    try:
        df = query_df(sql, {})
    except Exception:
        try:
            from clickhouse_client import get_client
            get_client().command(TARGETS_DDL)
        except Exception as exc:
            logger.warning("could not create tm_fin_targets: %s", exc)
        return {}
    return {
        str(r["tm"]).strip().upper(): {
            "value_target": float(r["value_target"] or 0.0),
            "card_slab_1": float(r["card_slab_1"] or 0.0),
            "card_slab_2": float(r["card_slab_2"] or 0.0),
        }
        for _, r in df.iterrows()
    }


def save_targets(month: str, rows: list) -> int:
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise ValueError("month must be YYYY-MM")
    from clickhouse_client import get_client

    client = get_client()
    client.command(TARGETS_DDL)
    data = []
    for r in rows or []:
        tm = str(r.get("tm", "")).strip().upper()
        if not tm:
            continue
        data.append([
            month,
            tm,
            max(float(r.get("value_target") or 0.0), 0.0),
            max(float(r.get("card_slab_1") or 0.0), 0.0),
            max(float(r.get("card_slab_2") or 0.0), 0.0),
        ])
    if data:
        client.insert(
            "tm_fin_targets", data,
            column_names=["month", "tm", "value_target", "card_slab_1", "card_slab_2"],
        )
    _MEM_CACHE.clear()
    return len(data)


# --------------------------------------------------------------------------- main
def _tm_metrics(label, fin, tot, card, tgt, days):
    """One TM / summary row. `tgt` = {value_target, card_slab_1, card_slab_2} or None."""
    elapsed, remaining, month_days = days
    tgt = tgt or {}
    vt = tgt.get("value_target") or 0.0
    s1 = tgt.get("card_slab_1") or 0.0
    s2 = tgt.get("card_slab_2") or 0.0
    crr = fin / elapsed if elapsed else 0.0
    c_crr = card / elapsed if elapsed else 0.0

    def ratio(a, b):
        return (a / b) if b else None

    def req(target, done):
        return ((target - done) / remaining) if (target and remaining) else None

    return {
        "tm": label,
        "fin": round(fin, 2),
        "total": round(tot, 2),
        "fin_share": (fin / tot) if tot > 0 else 0.0,
        "value_target": round(vt, 2) if vt else None,
        "drr": round(req(vt, fin), 2) if req(vt, fin) is not None else None,
        "crr": round(crr, 2),
        "ach_pct": ratio(fin, vt),
        "trend": ratio(crr * month_days, vt),
        "overall_65_val": None,
        "card_slab_2": round(s2, 2) if s2 else None,
        "card_ach": round(card, 2),
        "card_ach_pct": ratio(card, s2),
        "card_trend": ratio(c_crr * month_days, s2),
        "card_crr": round(c_crr, 2),
        "card_drr": round(req(s2, card), 2) if req(s2, card) is not None else None,
        "card_share_of_fin": ratio(card, fin),
        "card_slab_1": round(s1, 2) if s1 else None,
        "slab1_ach_pct": ratio(card, s1),
        "slab1_trend": ratio(c_crr * month_days, s1),
        "drr_slab_1": round(req(s1, card), 2) if req(s1, card) is not None else None,
    }


def get_store_fin_share_data(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = "",
    period_mode: str = "reference",
) -> dict:
    # ---- period ----
    if period_mode == "reference" and str(date_str or "").startswith("2026-10") and timeframe == "monthly":
        q_date, q_tf, q_end = "2026-10-01", "custom", "2026-10-27"
        eff_start, eff_end = "2026-10-01", "2026-10-27"
    else:
        q_date, q_tf, q_end = date_str, timeframe, end_date_str or ""
        s_dt, e_dt = emi_state.range_of(date_str, timeframe, end_date_str or None)
        eff_start, eff_end = s_dt.isoformat(), e_dt.isoformat()

    cache_key = (eff_start, eff_end, emi_state.signature())
    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    p_start = datetime.strptime(eff_start[:10], "%Y-%m-%d").date()
    p_end = datetime.strptime(eff_end[:10], "%Y-%m-%d").date()
    month_days = calendar.monthrange(p_end.year, p_end.month)[1]
    days = (
        float(max((p_end - p_start).days + 1, 1)),     # elapsed
        float(max(month_days - p_end.day, 0)),         # remaining
        float(month_days),                             # days in month
    )

    # ---- data ----
    erp, meta = _erp_by_store(q_date, q_tf, q_end)
    s_dt2, e_dt2 = emi_state.range_of(q_date, q_tf, q_end or None)
    card, card_info = _card_by_store(s_dt2, e_dt2, filtered=True)
    bench, bench_info = _benchmark(p_start)

    # Only regional retail stores: drop the catch-all RBM buckets (OTHERS / KARNATAKA RBM / blank)
    stores_set = {s for s in erp if meta.get(s, {}).get("rbm") and meta[s]["rbm"] not in CATCHALL_RBMS}

    # Tracked stores (have a Fin BDM assigned) that had no sales in the period still appear, with 0
    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _FIN_BDM_MAP
        regional = {meta[s]["rbm"] for s in stores_set}
        bm = query_df(
            "SELECT upperUTF8(trim(branch_name)) AS name, upperUTF8(trim(rbm)) AS rbm, upperUTF8(trim(bdm)) AS bdm "
            "FROM branch_master WHERE upperUTF8(store_type) = 'BRANCH'", {},
        )
        for _, r in bm.iterrows():
            name = str(r["name"]).strip().upper()
            if name and name not in erp and str(r["rbm"]).strip().upper() in regional and name in _FIN_BDM_MAP:
                erp[name] = (0.0, 0.0, 0.0, 0.0)
                meta[name] = {
                    "rbm": str(r["rbm"]).strip().upper(),
                    "bdm": str(r["bdm"]).strip().upper(),
                    "fin_bdm": str(_FIN_BDM_MAP[name]).strip().upper(),
                }
                stores_set.add(name)
    except Exception as exc:
        logger.warning("zero-sales stores lookup skipped: %s", exc)

    stores = sorted(stores_set)
    oct_rows, vol_rows = [], []
    for i, s in enumerate(stores, start=1):
        tv, tq, fv, fq = erp[s]
        cv, cq = card.get(s, [0.0, 0])
        m = meta[s]
        b_val, b_qty = bench.get(s, (None, None))

        fin_v = fv + cv
        share_v = (fin_v / tv) if tv > 0 else 0.0
        oct_rows.append({
            "row": i, "store": s,
            "fin": round(fin_v, 2), "paper_fin": round(fv, 2), "card_fin": round(cv, 2),
            "total": round(tv, 2), "fin_share": share_v,
            "fin_bdm": m["fin_bdm"], "bdm": m["bdm"], "rbm": m["rbm"],
            "amj": b_val, "dif_mode": "subtract",
            "dif": (share_v - b_val) if b_val is not None else None,
        })

        fin_q = int(round(fq + cq))
        tot_q = int(round(tq))
        share_q = (fin_q / tot_q) if tot_q > 0 else 0.0
        vol_rows.append({
            "row": i, "store": s,
            "fin": fin_q, "paper_fin": int(round(fq)), "card_fin": int(cq),
            "total": tot_q, "fin_share": share_q,
            "fin_bdm": m["fin_bdm"], "bdm": m["bdm"], "rbm": m["rbm"],
            "jas": b_qty,
            "dif": (share_q - b_qty) if b_qty is not None else None,
        })

    # ---- TM / RBM / BDM roll-ups ----
    month_key = f"{p_end.year:04d}-{p_end.month:02d}"
    targets = load_targets(month_key)
    tm_names = sorted({r["fin_bdm"] for r in oct_rows if r["fin_bdm"]})

    def tm_zone(tm):
        n = sum(r["total"] for r in oct_rows if r["fin_bdm"] == tm and r["rbm"] in NORTH_RBMS)
        t = sum(r["total"] for r in oct_rows if r["fin_bdm"] == tm)
        return "NORTH" if t and n / t >= 0.5 else "SOUTH"

    zones = {tm: tm_zone(tm) for tm in tm_names}

    oct_tms, vol_tms = [], []
    for tm in tm_names:
        rows_v = [r for r in oct_rows if r["fin_bdm"] == tm]
        rows_q = [r for r in vol_rows if r["fin_bdm"] == tm]
        fin_v = sum(r["fin"] for r in rows_v)
        tot_v = sum(r["total"] for r in rows_v)
        card_v = sum(r["card_fin"] for r in rows_v)
        tgt = targets.get(tm.upper())
        oct_tms.append(_tm_metrics(tm, fin_v, tot_v, card_v, tgt, days))
        fin_q = sum(r["fin"] for r in rows_q)
        tot_q = sum(r["total"] for r in rows_q)
        s2 = (tgt or {}).get("card_slab_2") or None
        vol_tms.append({
            "tm": tm, "fin": fin_q, "total": tot_q,
            "fin_share": (fin_q / tot_q) if tot_q > 0 else 0.0,
            "value_target": (tgt or {}).get("value_target") or None,
            "card_slab_2": s2,
            "card_ach": round(card_v, 2),
            "card_ach_pct": (card_v / s2) if s2 else None,
        })

    def sum_targets(names):
        out = {"value_target": 0.0, "card_slab_1": 0.0, "card_slab_2": 0.0}
        for n in names:
            for k in out:
                out[k] += (targets.get(n.upper()) or {}).get(k, 0.0)
        return out

    def roll_oct(label, names):
        sub = [r for r in oct_rows if r["fin_bdm"] in names]
        return _tm_metrics(
            label,
            sum(r["fin"] for r in sub), sum(r["total"] for r in sub), sum(r["card_fin"] for r in sub),
            sum_targets(names), days,
        )

    def roll_vol(label, names):
        sub = [r for r in vol_rows if r["fin_bdm"] in names]
        rows_v = [r for r in oct_rows if r["fin_bdm"] in names]
        fin_q, tot_q = sum(r["fin"] for r in sub), sum(r["total"] for r in sub)
        t = sum_targets(names)
        card_v = sum(r["card_fin"] for r in rows_v)
        return {
            "tm": label, "fin": fin_q, "total": tot_q,
            "fin_share": (fin_q / tot_q) if tot_q > 0 else 0.0,
            "value_target": t["value_target"] or None,
            "card_slab_2": t["card_slab_2"] or None,
            "card_ach": round(card_v, 2),
            "card_ach_pct": (card_v / t["card_slab_2"]) if t["card_slab_2"] else None,
        }

    all_tm = set(tm_names)
    north = {t for t in tm_names if zones[t] == "NORTH"}
    south = {t for t in tm_names if zones[t] == "SOUTH"}

    def group(rows, key, label_key):
        out = []
        for name in sorted({r[key] for r in rows if r[key]}):
            sub = [r for r in rows if r[key] == name]
            fin = sum(r["fin"] for r in sub)
            tot = sum(r["total"] for r in sub)
            out.append({label_key: name, "fin": fin, "total": tot, "fin_share": (fin / tot) if tot > 0 else 0.0})
        return out

    result = {
        "status": "success",
        "period": {
            "mode": period_mode, "start_date": eff_start, "end_date": eff_end,
            "is_oct_2026": str(date_str or "").startswith("2026-10"),
            "elapsed_days": int(days[0]), "remaining_days": int(days[1]), "days_in_month": int(days[2]),
        },
        "benchmark": bench_info,
        "card_status": card_info,
        "targets": {"month": month_key, "set": bool(targets)},
        "oct": {
            "stores": oct_rows, "tms": oct_tms,
            "tm_total": roll_oct("Total", all_tm), "tm_north": roll_oct("North", north), "tm_south": roll_oct("South", south),
            "rbms": group(oct_rows, "rbm", "rbm"), "bdms": group(oct_rows, "bdm", "bdm"),
        },
        "oct_vol": {
            "stores": vol_rows, "tms": vol_tms,
            "tm_total": roll_vol("Total", all_tm), "tm_north": roll_vol("North", north), "tm_south": roll_vol("South", south),
            "rbms": group(vol_rows, "rbm", "rbm"), "bdms": group(vol_rows, "bdm", "bdm"),
        },
    }
    _MEM_CACHE[cache_key] = result
    return result
