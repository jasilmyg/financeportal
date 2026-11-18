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
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import threading
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
_MEM_CACHE_LOCK = threading.Lock()
_NAMES_CACHE = {"t": 0.0, "v": {}}
_BRANCH_META_CACHE = {"t": 0.0, "v": []}


def invalidate_cache() -> None:
    """Clear in-memory caches held by store_fin_share."""
    with _MEM_CACHE_LOCK:
        _MEM_CACHE.clear()
        _ERP_Q_CACHE.clear()
    _NAMES_CACHE.update(t=0.0, v={})
    _BRANCH_META_CACHE.update(t=0.0, v=[])

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
    """branch code -> store name from ClickHouse branch_master (30 minute cache)."""
    if time.time() - _NAMES_CACHE["t"] < 1800 and _NAMES_CACHE["v"]:
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


def _branch_meta_rows() -> list:
    """Cached list of (name, rbm, bdm) tuples for BRANCH rows in branch_master (30 minute cache)."""
    if time.time() - _BRANCH_META_CACHE["t"] < 1800 and _BRANCH_META_CACHE["v"]:
        return _BRANCH_META_CACHE["v"]
    rows = []
    try:
        bm = query_df(
            "SELECT upperUTF8(trim(branch_name)) AS name, upperUTF8(trim(rbm)) AS rbm, upperUTF8(trim(bdm)) AS bdm "
            "FROM branch_master WHERE upperUTF8(store_type) = 'BRANCH'", {},
        )
        rows = [
            (
                str(r["name"]).strip().upper(),
                str(r["rbm"]).strip().upper(),
                str(r["bdm"]).strip().upper(),
            )
            for _, r in bm.iterrows()
        ]
    except Exception as exc:
        logger.warning("branch_master meta lookup failed: %s", exc)
    _BRANCH_META_CACHE.update(t=time.time(), v=rows)
    return rows


def _pick_mode(counts: dict, default: str = "") -> str:
    if not counts:
        return default
    return min((-cnt, val) for val, cnt in counts.items())[1]


def _erp_by_store(q_date: str, q_tf: str, q_end: str):
    """ERP totals per store: ({store: (tot_val, tot_qty, fin_val, fin_qty)}, {store: {rbm, bdm, fin_bdm, state}})."""
    import clickhouse_aggregator as ca

    df = ca.get_store_erp_summary(q_date, q_tf, end_date_str=q_end)
    if df is None or df.empty:
        return {}, {}
    names = _branch_names()

    sums = {}       # store -> [tot_val, tot_qty, fin_val, fin_qty]
    meta_votes = {} # store -> {"rbm": {}, "bdm": {}, "fin_bdm": {}, "state": {}}

    for code_raw, rbm_v, bdm_v, fin_bdm_v, state_v, tv_v, tq_v, fv_v, fq_v, wt_v in zip(
        df["Branch"].tolist(),
        df["RBM"].tolist(),
        df["BDM"].tolist(),
        df["Fin BDM"].tolist(),
        df["State"].tolist(),
        df["tot_val"].tolist(),
        df["tot_qty"].tolist(),
        df["fin_val"].tolist(),
        df["fin_qty"].tolist(),
        df["row_cnt"].tolist(),
    ):
        code = str(code_raw or "").strip().upper()
        if not code:
            continue
        s = names.get(code, code)
        acc = sums.get(s)
        if acc is None:
            acc = [0.0, 0.0, 0.0, 0.0]
            sums[s] = acc
            meta_votes[s] = {"rbm": {}, "bdm": {}, "fin_bdm": {}, "state": {}}
        acc[0] += float(tv_v or 0.0)
        acc[1] += float(tq_v or 0.0)
        acc[2] += float(fv_v or 0.0)
        acc[3] += float(fq_v or 0.0)

        wt = max(int(wt_v or 1), 1)
        mv = meta_votes[s]
        for dst_k, raw_v in (
            ("rbm", rbm_v),
            ("bdm", bdm_v),
            ("fin_bdm", fin_bdm_v),
            ("state", state_v),
        ):
            val = str(raw_v or "").strip().upper()
            if val and val != "NAN":
                mv[dst_k][val] = mv[dst_k].get(val, 0) + wt

    totals = {s: tuple(v) for s, v in sums.items()}
    meta = {
        s: {
            "rbm": _pick_mode(mv["rbm"]),
            "bdm": _pick_mode(mv["bdm"]),
            "fin_bdm": _pick_mode(mv["fin_bdm"]),
            "state": _pick_mode(mv["state"], default="OTHERS") or "OTHERS",
        }
        for s, mv in meta_votes.items()
    }
    return totals, meta


def _card_by_store(start, end, filtered: bool):
    """Card EMI per store from the uploaded-file tables via single UNION ALL SQL."""
    out, info = {}, {}
    active = []
    for table, fin_key, label in CARD_TABLES:
        if filtered:
            cond = emi_state.date_filter_sql(fin_key, start, end)
        else:
            cond = f"txn_date >= toDate('{start}') AND txn_date <= toDate('{end}')"
        if cond is None:
            info[label] = {"uploaded": False, "stores": 0}
            continue
        active.append((table, label, cond))
        info[label] = {"uploaded": False, "stores": 0}

    if not active:
        return out, info

    union_sql = "\nUNION ALL\n".join(
        f"SELECT '{label}' AS partner, upperUTF8(trim(branch)) AS br, "
        f"toFloat64(sum(original_amount)) AS val, toInt64(sum(qty)) AS qty "
        f"FROM {table} WHERE {cond} GROUP BY br"
        for table, label, cond in active
    )
    try:
        res = query_df(union_sql, {})
        for _, r in res.iterrows():
            partner = str(r["partner"])
            br = str(r["br"]).strip().upper()
            if not br:
                continue
            info[partner]["stores"] += 1
            info[partner]["uploaded"] = True
            d = out.setdefault(br, [0.0, 0])
            d[0] += float(r["val"] or 0.0)
            d[1] += int(r["qty"] or 0)
        return out, info
    except Exception as exc:
        logger.warning("combined card UNION ALL failed (%s); falling back per-table", exc)

    for table, label, cond in active:
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
        except Exception as exc2:
            logger.warning("card query %s failed: %s", table, exc2)
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
    with _MEM_CACHE_LOCK:
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
    state: str = "",
) -> dict:
    emi_state._ensure_synced()
    sel_states = sorted({x.strip().upper() for x in str(state or "").split(",") if x.strip()})
    # ---- period ----
    if period_mode == "reference" and str(date_str or "").startswith("2026-10") and timeframe == "monthly":
        q_date, q_tf, q_end = "2026-10-01", "custom", "2026-10-27"
        eff_start, eff_end = "2026-10-01", "2026-10-27"
    else:
        q_date, q_tf, q_end = date_str, timeframe, end_date_str or ""
        s_dt, e_dt = emi_state.range_of(date_str, timeframe, end_date_str or None)
        eff_start, eff_end = s_dt.isoformat(), e_dt.isoformat()

    cache_key = (eff_start, eff_end, tuple(sel_states), emi_state.signature(sync=False))
    with _MEM_CACHE_LOCK:
        cached = _MEM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    p_start = datetime.strptime(eff_start[:10], "%Y-%m-%d").date()
    p_end = datetime.strptime(eff_end[:10], "%Y-%m-%d").date()
    month_days = calendar.monthrange(p_end.year, p_end.month)[1]
    days = (
        float(max((p_end - p_start).days + 1, 1)),     # elapsed
        float(max(month_days - p_end.day, 0)),         # remaining
        float(month_days),                             # days in month
    )
    month_key = f"{p_end.year:04d}-{p_end.month:02d}"
    s_dt2, e_dt2 = emi_state.range_of(q_date, q_tf, q_end or None)

    # ---- data (parallel ClickHouse execution) ----
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_erp = pool.submit(_erp_by_store, q_date, q_tf, q_end)
        f_card = pool.submit(_card_by_store, s_dt2, e_dt2, True)
        f_bench = pool.submit(_benchmark, p_start)
        f_tgt = pool.submit(load_targets, month_key)
        f_bm = pool.submit(_branch_meta_rows)
        erp, meta = f_erp.result()
        card, card_info = f_card.result()
        bench, bench_info = f_bench.result()
        targets = f_tgt.result()
        bm_rows = f_bm.result()

    # Known retail stores only (a store must exist in branch_master by name; this drops unmapped
    # internal codes such as corporate / warehouse billing branches) with a real RBM bucket.
    known = set(_branch_names().values())
    stores_set = {s for s in erp if s in known and meta.get(s, {}).get("rbm")}

    # Tracked stores (have a Fin BDM assigned) that had no sales in the period still appear, with 0
    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _FIN_BDM_MAP
        regional = {meta[s]["rbm"] for s in stores_set} - CATCHALL_RBMS
        rbm_state = {}
        for s in stores_set:
            rbm_state.setdefault(meta[s]["rbm"], []).append(meta[s]["state"])
        rbm_state = {k: max(set(v), key=v.count) for k, v in rbm_state.items()}
        for name, rb, bd in bm_rows:
            if name and name not in erp and rb in regional and name in _FIN_BDM_MAP:
                erp[name] = (0.0, 0.0, 0.0, 0.0)
                meta[name] = {
                    "rbm": rb,
                    "bdm": bd,
                    "fin_bdm": str(_FIN_BDM_MAP[name]).strip().upper(),
                    "state": rbm_state.get(rb, "KERALA"),
                }
                stores_set.add(name)
    except Exception as exc:
        logger.warning("zero-sales stores lookup skipped: %s", exc)

    all_states = sorted({meta[s]["state"] for s in stores_set})
    if sel_states:
        stores_set = {s for s in stores_set if meta[s]["state"] in sel_states}
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
            "fin_bdm": m["fin_bdm"], "bdm": m["bdm"], "rbm": m["rbm"], "state": m["state"],
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
            "fin_bdm": m["fin_bdm"], "bdm": m["bdm"], "rbm": m["rbm"], "state": m["state"],
            "jas": b_qty,
            "dif": (share_q - b_qty) if b_qty is not None else None,
        })

    # ---- TM / RBM / BDM roll-ups ----
    month_key = f"{p_end.year:04d}-{p_end.month:02d}"
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
        sub = [r for r in oct_rows if names is None or r["fin_bdm"] in names]
        return _tm_metrics(
            label,
            sum(r["fin"] for r in sub), sum(r["total"] for r in sub), sum(r["card_fin"] for r in sub),
            sum_targets(all_tm if names is None else names), days,
        )

    def roll_vol(label, names):
        sub = [r for r in vol_rows if names is None or r["fin_bdm"] in names]
        rows_v = [r for r in oct_rows if names is None or r["fin_bdm"] in names]
        fin_q, tot_q = sum(r["fin"] for r in sub), sum(r["total"] for r in sub)
        t = sum_targets(all_tm if names is None else names)
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
        "states": all_states,
        "selected_states": sel_states,
        "card_status": card_info,
        "targets": {"month": month_key, "set": bool(targets)},
        "oct": {
            "stores": oct_rows, "tms": oct_tms,
            "tm_total": roll_oct("Total", None), "tm_north": roll_oct("North", north), "tm_south": roll_oct("South", south),
            "rbms": group(oct_rows, "rbm", "rbm"), "bdms": group(oct_rows, "bdm", "bdm"),
        },
        "oct_vol": {
            "stores": vol_rows, "tms": vol_tms,
            "tm_total": roll_vol("Total", None), "tm_north": roll_vol("North", north), "tm_south": roll_vol("South", south),
            "rbms": group(vol_rows, "rbm", "rbm"), "bdms": group(vol_rows, "bdm", "bdm"),
        },
    }
    with _MEM_CACHE_LOCK:
        _MEM_CACHE[cache_key] = result
    return result


# --------------------------------------------------------------------------- compare / staff views
# Used by the "Duplicate Store Fin Share" tab: compare the selected period with the previous
# day / week / month / year, and list performance per Staff.
COMPARE_LABELS = {"day": "Prev Day", "week": "Prev Week", "month": "Prev Month", "quarter": "Prev Quarter", "year": "Prev Year"}
CARD_ERP_FINS = ("PineLab", "PAYTM", "Benow")


def _add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m0 = divmod(idx, 12)
    return date(y, m0 + 1, min(d.day, calendar.monthrange(y, m0 + 1)[1]))


def shift_period(start: date, end: date, compare: str):
    """Same-length window one day / week / month / year earlier."""
    from datetime import timedelta

    if compare == "day":
        return start - timedelta(days=1), end - timedelta(days=1)
    if compare == "week":
        return start - timedelta(days=7), end - timedelta(days=7)
    if compare == "month":
        return _add_months(start, -1), _add_months(end, -1)
    if compare == "quarter":
        return _add_months(start, -3), _add_months(end, -3)
    if compare == "year":
        return _add_months(start, -12), _add_months(end, -12)
    raise ValueError(f"unknown compare mode: {compare}")


def _slim_store_rows(rows: list) -> list:
    return [
        {
            "store": r["store"], "fin": r["fin"], "paper_fin": r["paper_fin"], "card_fin": r["card_fin"],
            "total": r["total"], "rbm": r["rbm"], "bdm": r["bdm"], "fin_bdm": r["fin_bdm"],
        }
        for r in rows
    ]


def get_store_fin_share_compare(
    date_str: str,
    timeframe: str = "monthly",
    end_date_str: str = "",
    period_mode: str = "reference",
    state: str = "",
    compare: str = "month",
    compare_start: str = "",
    compare_end: str = "",
) -> dict:
    """
    Store rows (Value + Volume) for a comparison window. The window is either explicit
    (compare_start / compare_end, any day / week / month / quarter / year / range the user
    picked) or the selected period shifted back by one day / week / month / quarter / year.
    """
    compare = str(compare or "").strip().lower()
    explicit = bool(str(compare_start or "").strip() and str(compare_end or "").strip())
    if not explicit and compare not in COMPARE_LABELS:
        return {"error": f"Unknown compare mode '{compare}' (use day, week, month, quarter or year)."}

    cur = get_store_fin_share_data(date_str, timeframe=timeframe, end_date_str=end_date_str,
                                   period_mode=period_mode, state=state)
    period = cur.get("period") or {}
    if not period.get("start_date"):
        return {"error": "Current period could not be resolved."}

    if explicit:
        try:
            c_start = datetime.strptime(str(compare_start).strip()[:10], "%Y-%m-%d").date()
            c_end = datetime.strptime(str(compare_end).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return {"error": "compare_start and compare_end must be dates in YYYY-MM-DD format."}
        if c_end < c_start:
            return {"error": "The comparison end date is before its start date."}
        label = "Selected period"
    else:
        c_start, c_end = shift_period(
            datetime.strptime(period["start_date"][:10], "%Y-%m-%d").date(),
            datetime.strptime(period["end_date"][:10], "%Y-%m-%d").date(),
            compare,
        )
        label = COMPARE_LABELS[compare]

    prev = get_store_fin_share_data(c_start.isoformat(), timeframe="custom", end_date_str=c_end.isoformat(),
                                    period_mode="live", state=state)
    if prev.get("error"):
        return prev
    return {
        "status": "success",
        "compare": "custom" if explicit else compare,
        "label": label,
        "start_date": c_start.isoformat(),
        "end_date": c_end.isoformat(),
        "card_status": prev.get("card_status") or {},
        "oct": {"stores": _slim_store_rows((prev.get("oct") or {}).get("stores", []))},
        "oct_vol": {"stores": _slim_store_rows((prev.get("oct_vol") or {}).get("stores", []))},
    }


def get_staff_fin_share(start_date: str, end_date: str, state: str = "") -> dict:
    """
    Per-staff Value + Volume for [start_date, end_date] from the ERP sales.
    FIN = paper finance + card EMI invoices matched to the ERP (the uploaded card files carry no
    staff information, so card EMI that could not be matched to an invoice is not attributed).
    """
    import clickhouse_aggregator as ca

    emi_state._ensure_synced()
    sel_states = tuple(sorted({x.strip().upper() for x in str(state or "").split(",") if x.strip()}))
    cache_key = ("staff", start_date, end_date, sel_states, emi_state.signature(sync=False))
    with _MEM_CACHE_LOCK:
        cached = _MEM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    joins_sql, where_clause, params, exprs = ca._build_sales_where_and_joins(
        start_date, "custom", end_date_str=end_date, state=",".join(sel_states), include_staff=True,
    )
    sql = f"""
SELECT Branch, _StoreName, Staff, Financier, sum(_qty) AS QTY, sum(_val) AS VAL
FROM (
    SELECT
        upper(trim(s.branch))                                       AS Branch,
        upper(trim(coalesce(nullIf(bm.branch_name, ''), s.branch))) AS _StoreName,
        upper(trim(coalesce(sd.staff, 'Unknown')))                  AS Staff,
        {exprs['fin_expr']}                                         AS Financier,
        {exprs['qty_expr']}                                         AS _qty,
        {exprs['val_expr']}                                         AS _val
    FROM azure_sales_report s
    {joins_sql}
    WHERE {where_clause}
)
GROUP BY Branch, _StoreName, Staff, Financier
"""
    df = query_df(sql, params)

    meta = {name: (rbm, bdm) for name, rbm, bdm in _branch_meta_rows()}
    try:
        from sales_cleaner import STORE_FIN_BDM_MAP as _FIN_BDM_MAP
    except Exception:
        _FIN_BDM_MAP = {}

    staff = {}
    if df is not None and not df.empty:
        for store_name, st, fin, qty, val in zip(
            df["_StoreName"].tolist(), df["Staff"].tolist(), df["Financier"].tolist(),
            df["QTY"].tolist(), df["VAL"].tolist(),
        ):
            st = str(st or "").strip().upper() or "UNKNOWN"
            d = staff.setdefault(st, {
                "paper_val": 0.0, "card_val": 0.0, "tot_val": 0.0,
                "paper_qty": 0.0, "card_qty": 0.0, "tot_qty": 0.0, "stores": {},
            })
            qty = float(qty or 0.0)
            val = float(val or 0.0)
            d["tot_val"] += val
            d["tot_qty"] += qty
            if fin in PAPER_FINS:
                d["paper_val"] += val
                d["paper_qty"] += qty
            elif fin in CARD_ERP_FINS:
                d["card_val"] += val
                d["card_qty"] += qty
            sn = str(store_name or "").strip().upper()
            d["stores"][sn] = d["stores"].get(sn, 0.0) + val

    rows = []
    for st, d in staff.items():
        if not d["tot_val"] and not d["tot_qty"]:
            continue
        top_store = max(d["stores"].items(), key=lambda kv: kv[1])[0] if d["stores"] else ""
        rbm, bdm = meta.get(top_store, ("", ""))
        rows.append({
            "staff": st, "store": top_store, "stores": len(d["stores"]),
            "rbm": rbm, "bdm": bdm, "fin_bdm": str(_FIN_BDM_MAP.get(top_store, "")).strip().upper(),
            "fin_val": round(d["paper_val"] + d["card_val"], 2), "paper_val": round(d["paper_val"], 2),
            "card_val": round(d["card_val"], 2), "tot_val": round(d["tot_val"], 2),
            "fin_qty": int(round(d["paper_qty"] + d["card_qty"])), "paper_qty": int(round(d["paper_qty"])),
            "card_qty": int(round(d["card_qty"])), "tot_qty": int(round(d["tot_qty"])),
        })
    rows.sort(key=lambda r: r["tot_val"], reverse=True)
    result = {"status": "success", "start_date": start_date, "end_date": end_date, "staff": rows}
    with _MEM_CACHE_LOCK:
        _MEM_CACHE[cache_key] = result
    return result
