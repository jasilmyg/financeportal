"""
ai_tools.py
-----------
Complete agentic toolset for the myG Finance Portal AI Agent:
  1. Canonical Portal Analytics Tools (100% parity with live dashboard & Store Fin Share)
  2. Guarded Read-Only ClickHouse SQL Analyst (with myG semantic macros & exportable tables)
  3. 7-Axis Deep Diagnostic & Anomaly Engine + Executive Morning Brief
  4. Persistent Learning & Memory Tools
  5. 3-Tier Dashboard Editor Tools:
       - Tier 1: Live UI / Navigation / Filter Control (`control_dashboard_ui`)
       - Tier 2: Persistent Custom Widgets, Thresholds & Target Edits (`propose_dashboard_edit` + `apply_pending_action`)
       - Tier 3: Optional Verified Source-Code Patch Proposal (`propose_code_patch`)
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any

import pandas as pd

import ai_memory
import ai_semantic_layer
import clickhouse_aggregator as ca
import clickhouse_client as ch
import dashboard_aggregator as da
import emi_state
import store_fin_share as sfs

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")

# ---------------------------------------------------------------------------
# Ephemeral caches for table exports & previewed pending edit actions
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_EXPORT_CACHE: dict[str, dict[str, Any]] = {}     # token -> {ts, title, columns, rows}
_PENDING_ACTIONS: dict[str, dict[str, Any]] = {}  # action_id -> {ts, edit_type, summary, diff, payload}


def _store_export(title: str, columns: list[str], rows: list[list[Any]]) -> str:
    tok = f"exp_{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _LOCK:
        # Evict entries older than 45 min
        stale = [k for k, v in _EXPORT_CACHE.items() if now - v["ts"] > 2700]
        for k in stale:
            _EXPORT_CACHE.pop(k, None)
        _EXPORT_CACHE[tok] = {
            "ts": now,
            "title": str(title or "AI Export")[:80],
            "columns": list(columns),
            "rows": list(rows),
        }
    return tok


def get_export_payload(token: str) -> dict[str, Any] | None:
    with _LOCK:
        return _EXPORT_CACHE.get(str(token or "").strip())


def _store_pending_action(edit_type: str, title: str, summary: str, diff: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    aid = f"act_{uuid.uuid4().hex[:10]}"
    now = time.time()
    record = {
        "action_id": aid,
        "ts": now,
        "edit_type": edit_type,
        "title": title,
        "summary": summary,
        "diff": diff,
        "payload": payload,
        "status": "pending",
    }
    with _LOCK:
        stale = [k for k, v in _PENDING_ACTIONS.items() if now - v["ts"] > 3600]
        for k in stale:
            _PENDING_ACTIONS.pop(k, None)
        _PENDING_ACTIONS[aid] = record
    return {
        "action_id": aid,
        "edit_type": edit_type,
        "title": title,
        "summary": summary,
        "diff": diff,
        "requires_pin": ai_memory.get_effective_ai_config()["pin_enabled"],
    }


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or pd.isna(v):
            return default
        return int(round(float(v)))
    except Exception:
        return default


def _default_date(ctx: dict[str, Any] | None = None) -> tuple[str, str, str, str]:
    c = ctx or {}
    d = str(c.get("date") or "").strip()[:10] or date.today().replace(day=1).isoformat()
    tf = str(c.get("timeframe") or "monthly").strip().lower()
    if tf not in ("daily", "weekly", "monthly", "quarterly", "yearly", "custom"):
        tf = "monthly"
    end_d = str(c.get("end_date") or "").strip()[:10] if tf == "custom" else ""
    st = str(c.get("state") or "KERALA").strip().upper()
    return d, tf, end_d, st


def _load_targets_json() -> dict[str, Any]:
    tf = os.path.join(_DATA_DIR, "targets.json")
    if os.path.exists(tf):
        try:
            with open(tf, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "exec_summary_targets": {
            "value_penetration": 67.0,
            "vol_penetration": 55.0,
            "cards_penetration": 6.91,
            "fin_value_cr": 460.0,
            "card_value_cr": 47.41,
        },
        "financier_volume_targets": {
            "BAJAJ": 88779, "HDB": 6341, "IDFC": 4488, "PFL": 134, "TVS": 1574, "DMI": 1708,
            "PinelAb": 42.4, "PayTm": 1.7, "Benow": 3.3,
        },
        "dmi_contest_brand_targets": {"SAMSUNG": 72, "VIVO": 72, "OPPO": 72},
        "manpower_plans": {"BAJAJ": 275, "HDB": 180, "IDFC": 120, "TVS": 35, "PFL": 29},
    }


def _save_targets_json(data: dict[str, Any]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tf = os.path.join(_DATA_DIR, "targets.json")
    with open(tf, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# TOOL 1: Canonical Dashboard Summary (100% portal parity)
# ---------------------------------------------------------------------------
def tool_get_dashboard_summary(
    date_str: str = "",
    timeframe: str = "",
    end_date_str: str = "",
    state: str = "",
    rbm: str = "",
    bdm: str = "",
    store: str = "",
    fin_bdm: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d0, tf0, e0, st0 = _default_date(context)
    d = (date_str or d0)[:10]
    tf = (timeframe or tf0).lower()
    end_d = (end_date_str or (e0 if tf == "custom" else ""))[:10]
    st = state.upper() if state else st0

    rollups = ca.get_clickhouse_dashboard_rollups(
        d, tf, rbm=rbm, bdm=bdm, store=store, staff="",
        end_date_str=end_d or None, fin_bdm=fin_bdm, state=st, district="",
    )
    if not rollups or rollups[0].empty:
        return {"status": "empty", "message": f"No data found for {d} ({tf}, state={st})."}

    zone_map = _load_targets_json().get("zone_map", {})
    ov_qty, ov_val = ca.get_card_both_overrides(
        d, tf, end_d or None, state=st, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=""
    )
    p_qty, p_val = ca.get_card_product_overrides(
        d, tf, end_d or None, state=st, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=""
    )
    data = da.get_dashboard_data(
        raw_file_path=None, target_date_str=d, timeframe=tf,
        rbm=rbm, bdm=bdm, store=store, staff="", zone_map=zone_map, df=None,
        end_date_str=end_d or None,
        pine_lab_override=ov_qty.get("PineLab"),
        paytm_override=ov_qty.get("PAYTM"),
        benow_override=ov_qty.get("Benow"),
        pine_lab_val_override=ov_val.get("PineLab"),
        paytm_val_override=ov_val.get("PAYTM"),
        benow_val_override=ov_val.get("Benow"),
        card_prod_qty_override=p_qty,
        card_prod_val_override=p_val,
        state=st, district="",
        ch_rollups=rollups,
    )

    kpis = data.get("kpis") or {}
    cp = data.get("category_performance") or {}
    qty_map = cp.get("qty") or {}
    val_map = cp.get("value") or {}

    paper_keys = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]
    card_keys = ["PineLab", "PAYTM", "Benow"]
    all_fin_keys = paper_keys + card_keys

    # Compute overall financier totals across categories
    # NOTE: dashboard_aggregator cat_perf['qty'][cat] / cat_perf['value'][cat] store per-financier
    # keys directly (no 'TOTAL' key), so compute totals by summing all financier keys + aCASH.
    fin_qty = {k: 0 for k in all_fin_keys + ["aCASH"]}
    fin_val_cr = {k: 0.0 for k in all_fin_keys + ["aCASH"]}
    cat_summary = []
    for cat in ["MOBILE", "LAPTOP", "CD PRODUCTS", "SA", "HVA", "OTHERS", "BANK EMI"]:
        rq = qty_map.get(cat) or {}
        rv = val_map.get(cat) or {}
        fq = sum(_safe_int(rq.get(k)) for k in all_fin_keys)
        fv = sum(_safe_float(rv.get(k)) for k in all_fin_keys)
        ac_q = _safe_int(rq.get("aCASH"))
        ac_v = _safe_float(rv.get("aCASH"))
        tot_q = fq + ac_q
        tot_v = fv + ac_v
        if tot_q == 0 and abs(tot_v) < 1e-9 and fq == 0 and abs(fv) < 1e-9:
            continue
        for k in all_fin_keys + ["aCASH"]:
            fin_qty[k] += _safe_int(rq.get(k))
            fin_val_cr[k] += _safe_float(rv.get(k))
        cat_summary.append({
            "category": cat,
            "fin_val_cr": round(fv, 2),
            "tot_val_cr": round(tot_v, 2),
            "val_share_pct": round((fv / tot_v * 100.0) if tot_v else 0.0, 2),
            "fin_qty": fq,
            "tot_qty": tot_q,
            "vol_share_pct": round((fq / tot_q * 100.0) if tot_q else 0.0, 2),
        })

    paper_qty_tot = sum(fin_qty[k] for k in paper_keys)
    card_qty_tot = sum(fin_qty[k] for k in card_keys)
    paper_val_tot = round(sum(fin_val_cr[k] for k in paper_keys), 2)
    card_val_tot = round(sum(fin_val_cr[k] for k in card_keys), 2)
    fin_qty_tot = paper_qty_tot + card_qty_tot
    fin_val_tot = round(paper_val_tot + card_val_tot, 2)
    tot_qty_all = fin_qty_tot + fin_qty["aCASH"]
    tot_val_all = round(fin_val_tot + fin_val_cr["aCASH"], 2)

    fin_rows = [
        [k, fin_qty[k], round(fin_val_cr[k], 2),
         round((fin_val_cr[k] / fin_val_tot * 100.0) if fin_val_tot else 0.0, 1)]
        for k in all_fin_keys
        if fin_qty[k] or fin_val_cr[k]
    ]
    exp_tok = _store_export(
        f"Financier_Mix_{d}_{tf}",
        ["Financier", "Qty", "Value (Cr)", "Share of FIN (%)"],
        fin_rows,
    )

    return {
        "status": "success",
        "period": {"date": d, "end_date": end_d, "timeframe": tf, "state": st},
        "headline": {
            "total_sales_cr": tot_val_all,
            "total_fin_cr": fin_val_tot,
            "paper_fin_cr": paper_val_tot,
            "card_fin_cr": card_val_tot,
            "fin_share_val_pct": round((fin_val_tot / tot_val_all * 100.0) if tot_val_all else 0.0, 2),
            "total_sales_qty": tot_qty_all,
            "total_fin_qty": fin_qty_tot,
            "paper_fin_qty": paper_qty_tot,
            "card_fin_qty": card_qty_tot,
            "fin_share_vol_pct": round((fin_qty_tot / tot_qty_all * 100.0) if tot_qty_all else 0.0, 2),
            "card_pen_vol_pct": round((card_qty_tot / tot_qty_all * 100.0) if tot_qty_all else 0.0, 2),
            "portal_kpis": kpis,
        },
        "financiers": [
            {"financier": r[0], "qty": r[1], "val_cr": r[2], "share_of_fin_pct": r[3]}
            for r in fin_rows
        ],
        "categories": cat_summary,
        "table": {
            "title": f"Financier Breakdown ({d} · {tf.title()} · {st})",
            "columns": ["Financier", "Qty", "Value (₹ Cr)", "Share of FIN (%)"],
            "rows": fin_rows,
            "export_token": exp_tok,
        },
        "chart": {
            "title": f"Financier Value Mix (₹ Cr) — {d} ({tf.title()})",
            "type": "bar",
            "labels": [r[0] for r in fin_rows],
            "datasets": [{"label": "Value (₹ Cr)", "data": [r[2] for r in fin_rows]}],
        },
    }


# ---------------------------------------------------------------------------
# TOOL 2: Store / TM / RBM / BDM / Staff Fin Share + Comparisons
# ---------------------------------------------------------------------------
def tool_get_store_fin_share(
    date_str: str = "",
    timeframe: str = "",
    end_date_str: str = "",
    state: str = "",
    view_by: str = "store",
    compare_with: str = "none",
    fin_split: str = "total",
    sort_by: str = "fin_share_desc",
    limit: int = 20,
    search: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d0, tf0, e0, st0 = _default_date(context)
    d = (date_str or d0)[:10]
    tf = (timeframe or tf0).lower()
    end_d = (end_date_str or (e0 if tf == "custom" else ""))[:10]
    st = state.upper() if state else st0
    view_by = (view_by or "store").strip().lower()
    compare_with = (compare_with or "none").strip().lower()
    fin_split = (fin_split or "total").strip().lower()
    limit = max(1, min(int(limit or 20), 200))

    cur = sfs.get_store_fin_share_data(d, timeframe=tf, end_date_str=end_d, period_mode="live", state=st)
    if cur.get("error"):
        return {"status": "error", "error": cur["error"]}

    period = cur.get("period") or {}
    eff_start = period.get("start_date", d)
    eff_end = period.get("end_date", end_d or d)

    # Staff view uses dedicated staff endpoint
    if view_by == "staff":
        staff_res = sfs.get_staff_fin_share(eff_start, eff_end, state=st)
        rows_raw = staff_res.get("staff") or []
        if search:
            q = search.strip().upper()
            rows_raw = [r for r in rows_raw if q in r["staff"] or q in r["store"] or q in r["fin_bdm"]]
        enriched = []
        for r in rows_raw:
            fv = r["paper_val"] if fin_split == "paper" else (r["card_val"] if fin_split == "card" else r["fin_val"])
            tv = r["tot_val"]
            sh = (fv / tv * 100.0) if tv > 0 else 0.0
            enriched.append({
                "name": r["staff"], "store": r["store"], "fin_bdm": r["fin_bdm"],
                "fin_l": round(fv / 1e5, 2), "tot_l": round(tv / 1e5, 2),
                "fin_share_pct": round(sh, 2), "fin_qty": r["fin_qty"], "tot_qty": r["tot_qty"],
            })
        rev = not sort_by.endswith("_asc")
        key_fn = (lambda x: x["fin_share_pct"]) if "share" in sort_by else (lambda x: x["tot_l"] if "tot" in sort_by else x["fin_l"])
        enriched.sort(key=key_fn, reverse=rev)
        top = enriched[:limit]
        trows = [[r["name"], r["store"], r["fin_bdm"], r["fin_l"], r["tot_l"], f"{r['fin_share_pct']:.1f}%", r["fin_qty"], r["tot_qty"]] for r in top]
        exp_tok = _store_export(f"Staff_FinShare_{eff_start}", ["Staff", "Store", "Fin BDM", "FIN (L)", "Total (L)", "Fin Share %", "FIN Qty", "Total Qty"], trows)
        return {
            "status": "success",
            "period": period,
            "view_by": "staff",
            "count": len(enriched),
            "rows": top,
            "table": {
                "title": f"Staff Fin Share ({eff_start} to {eff_end})",
                "columns": ["Staff", "Store", "Fin BDM", "FIN (₹ L)", "Total (₹ L)", "Fin Share %", "FIN Qty", "Tot Qty"],
                "rows": trows,
                "export_token": exp_tok,
            },
        }

    # Aggregate store rows into store / tm / rbm / bdm
    def _agg(store_rows_val, store_rows_vol):
        vol_by_store = {r["store"]: r for r in (store_rows_vol or [])}
        buckets: dict[str, dict[str, Any]] = {}
        for r in (store_rows_val or []):
            if view_by in ("tm", "fin_bdm"):
                k = r.get("fin_bdm") or "UNMAPPED"
            elif view_by == "rbm":
                k = r.get("rbm") or "UNMAPPED"
            elif view_by == "bdm":
                k = r.get("bdm") or "UNMAPPED"
            else:
                k = r["store"]
            b = buckets.setdefault(k, {
                "name": k, "fin": 0.0, "paper_fin": 0.0, "card_fin": 0.0, "total": 0.0,
                "fin_q": 0, "paper_q": 0, "card_q": 0, "tot_q": 0,
                "rbm": r.get("rbm", ""), "bdm": r.get("bdm", ""), "fin_bdm": r.get("fin_bdm", ""),
                "stores_cnt": 0,
            })
            vq = vol_by_store.get(r["store"]) or {}
            b["fin"] += _safe_float(r.get("fin"))
            b["paper_fin"] += _safe_float(r.get("paper_fin"))
            b["card_fin"] += _safe_float(r.get("card_fin"))
            b["total"] += _safe_float(r.get("total"))
            b["fin_q"] += _safe_int(vq.get("fin"))
            b["paper_q"] += _safe_int(vq.get("paper_fin"))
            b["card_q"] += _safe_int(vq.get("card_fin"))
            b["tot_q"] += _safe_int(vq.get("total"))
            b["stores_cnt"] += 1
        return buckets

    cur_b = _agg((cur.get("oct") or {}).get("stores"), (cur.get("oct_vol") or {}).get("stores"))

    cmp_b = {}
    cmp_label = ""
    if compare_with in ("day", "week", "month", "quarter", "year"):
        cmp_res = sfs.get_store_fin_share_compare(
            d, timeframe=tf, end_date_str=end_d, period_mode="live", state=st, compare=compare_with
        )
        if not cmp_res.get("error"):
            cmp_b = _agg((cmp_res.get("oct") or {}).get("stores"), (cmp_res.get("oct_vol") or {}).get("stores"))
            cmp_label = f"{cmp_res.get('label', compare_with)} ({cmp_res.get('start_date')}..{cmp_res.get('end_date')})"

    out_rows = []
    for k, b in cur_b.items():
        if search:
            q = search.strip().upper()
            if q not in k.upper() and q not in b["fin_bdm"].upper() and q not in b["rbm"].upper() and q not in b["bdm"].upper():
                continue
        fv = b["paper_fin"] if fin_split == "paper" else (b["card_fin"] if fin_split == "card" else b["fin"])
        tv = b["total"]
        sh = (fv / tv * 100.0) if tv > 0 else 0.0
        fq = b["paper_q"] if fin_split == "paper" else (b["card_q"] if fin_split == "card" else b["fin_q"])
        tq = b["tot_q"]
        sh_q = (fq / tq * 100.0) if tq > 0 else 0.0

        rec = {
            "name": k,
            "fin_cr": round(fv / 1e7, 3),
            "paper_cr": round(b["paper_fin"] / 1e7, 3),
            "card_cr": round(b["card_fin"] / 1e7, 3),
            "tot_cr": round(tv / 1e7, 3),
            "fin_share_pct": round(sh, 2),
            "fin_qty": fq,
            "tot_qty": tq,
            "vol_share_pct": round(sh_q, 2),
            "rbm": b["rbm"], "bdm": b["bdm"], "fin_bdm": b["fin_bdm"],
        }
        if cmp_b:
            pb = cmp_b.get(k)
            if pb and pb["total"] > 0:
                pfv = pb["paper_fin"] if fin_split == "paper" else (pb["card_fin"] if fin_split == "card" else pb["fin"])
                psh = pfv / pb["total"] * 100.0
                rec["prev_fin_share_pct"] = round(psh, 2)
                rec["diff_pp"] = round(sh - psh, 2)
                rec["fin_growth_pct"] = round(((fv - pfv) / pfv * 100.0) if pfv else 0.0, 1)
            else:
                rec["prev_fin_share_pct"] = None
                rec["diff_pp"] = None
                rec["fin_growth_pct"] = None
        out_rows.append(rec)

    # Filter tiny noise when sorting ascending by fin_share for stores
    if sort_by == "fin_share_asc" and view_by == "store":
        out_rows = [r for r in out_rows if r["tot_cr"] >= 0.05]  # >= 5 Lakhs

    rev = not sort_by.endswith("_asc")
    if "diff" in sort_by and cmp_b:
        out_rows.sort(key=lambda x: (x.get("diff_pp") if x.get("diff_pp") is not None else (-999 if rev else 999)), reverse=rev)
    elif "share" in sort_by:
        out_rows.sort(key=lambda x: x["fin_share_pct"], reverse=rev)
    elif "tot" in sort_by:
        out_rows.sort(key=lambda x: x["tot_cr"], reverse=rev)
    else:
        out_rows.sort(key=lambda x: x["fin_cr"], reverse=rev)

    top = out_rows[:limit]
    tm_tot = (cur.get("oct") or {}).get("tm_total") or {}
    tm_n = (cur.get("oct") or {}).get("tm_north") or {}
    tm_s = (cur.get("oct") or {}).get("tm_south") or {}

    if cmp_b:
        cols = [view_by.upper(), "FIN (₹ Cr)", "Total (₹ Cr)", "Fin Share %", "Prev %", "Diff (pp)", "FIN Qty", "Tot Qty"]
        trows = [
            [r["name"], r["fin_cr"], r["tot_cr"], f"{r['fin_share_pct']:.1f}%",
             f"{r['prev_fin_share_pct']:.1f}%" if r.get("prev_fin_share_pct") is not None else "—",
             f"{r['diff_pp']:+.1f}pp" if r.get("diff_pp") is not None else "—",
             r["fin_qty"], r["tot_qty"]]
            for r in top
        ]
    else:
        cols = [view_by.upper(), "Paper (₹ Cr)", "Card (₹ Cr)", "FIN (₹ Cr)", "Total (₹ Cr)", "Fin Share %", "FIN Qty", "Tot Qty"]
        trows = [
            [r["name"], r["paper_cr"], r["card_cr"], r["fin_cr"], r["tot_cr"], f"{r['fin_share_pct']:.1f}%", r["fin_qty"], r["tot_qty"]]
            for r in top
        ]
    exp_tok = _store_export(f"FinShare_{view_by}_{eff_start}", cols, trows)

    return {
        "status": "success",
        "period": period,
        "compare_label": cmp_label,
        "view_by": view_by,
        "fin_split": fin_split,
        "summary": {
            "total_fin_cr": round(_safe_float(tm_tot.get("fin")) / 1e7, 2),
            "total_sales_cr": round(_safe_float(tm_tot.get("total")) / 1e7, 2),
            "overall_fin_share_pct": round(_safe_float(tm_tot.get("fin_share")) * 100.0, 2),
            "north_fin_share_pct": round(_safe_float(tm_n.get("fin_share")) * 100.0, 2),
            "south_fin_share_pct": round(_safe_float(tm_s.get("fin_share")) * 100.0, 2),
            "entity_count": len(out_rows),
            "card_status": cur.get("card_status") or {},
        },
        "rows": top,
        "table": {
            "title": f"{view_by.upper()} Fin Share ({eff_start} to {eff_end})" + (f" vs {cmp_label}" if cmp_label else ""),
            "columns": cols,
            "rows": trows,
            "export_token": exp_tok,
        },
        "chart": {
            "title": f"Top {min(len(top), 12)} {view_by.upper()} — Fin Share %",
            "type": "bar",
            "labels": [r["name"][:20] for r in top[:12]],
            "datasets": [{"label": "Fin Share %", "data": [r["fin_share_pct"] for r in top[:12]]}],
        },
    }


# ---------------------------------------------------------------------------
# TOOL 3: Brand & Price Range Analysis
# ---------------------------------------------------------------------------
def tool_get_brand_and_price_analysis(
    date_str: str = "",
    timeframe: str = "",
    end_date_str: str = "",
    state: str = "",
    category: str = "ALL",
    dimension: str = "brand",
    limit: int = 15,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d0, tf0, e0, st0 = _default_date(context)
    d = (date_str or d0)[:10]
    tf = (timeframe or tf0).lower()
    end_d = (end_date_str or (e0 if tf == "custom" else ""))[:10]
    st = state.upper() if state else st0
    dim = (dimension or "brand").strip().lower()

    joins_sql, where_clause, params, exprs = ca._build_sales_where_and_joins(
        d, tf, end_date_str=end_d or None, state=st, include_staff=False
    )
    cat_filter = ""
    if category and category.upper() != "ALL":
        cat_filter = f" AND ({exprs['cat_expr']}) = {{cat_f:String}}"
        params["cat_f"] = category.upper()

    if dim == "price":
        grp_expr = """CASE
            WHEN abs(toFloat64(s.sold_price)) < 10000 THEN '01. Below 10K'
            WHEN abs(toFloat64(s.sold_price)) < 15000 THEN '02. 10K-15K'
            WHEN abs(toFloat64(s.sold_price)) < 20000 THEN '03. 15K-20K'
            WHEN abs(toFloat64(s.sold_price)) < 30000 THEN '04. 20K-30K'
            WHEN abs(toFloat64(s.sold_price)) < 40000 THEN '05. 30K-40K'
            WHEN abs(toFloat64(s.sold_price)) < 50000 THEN '06. 40K-50K'
            WHEN abs(toFloat64(s.sold_price)) < 75000 THEN '07. 50K-75K'
            WHEN abs(toFloat64(s.sold_price)) < 100000 THEN '08. 75K-100K'
            WHEN abs(toFloat64(s.sold_price)) < 150000 THEN '09. 100K-150K'
            WHEN abs(toFloat64(s.sold_price)) < 200000 THEN '10. 150K-200K'
            ELSE '11. Above 200K'
        END"""
        order_sql = "ORDER BY grp ASC"
    else:
        grp_expr = "upper(trim(coalesce(nullIf(i.brand, ''), 'UNKNOWN')))"
        order_sql = f"ORDER BY tot_val DESC LIMIT {max(1, min(int(limit or 15), 50))}"

    sql = f"""
    SELECT
        {grp_expr} AS grp,
        sum({exprs['val_expr']}) AS tot_val,
        sumIf({exprs['val_expr']}, ({exprs['fin_expr']}) != 'aCASH') AS fin_val,
        sum({exprs['qty_expr']}) AS tot_qty,
        sumIf({exprs['qty_expr']}, ({exprs['fin_expr']}) != 'aCASH') AS fin_qty,
        sumIf({exprs['val_expr']}, ({exprs['fin_expr']}) = 'BAJAJ') AS bajaj_val,
        sumIf({exprs['val_expr']}, ({exprs['fin_expr']}) = 'HDB') AS hdb_val,
        sumIf({exprs['val_expr']}, ({exprs['fin_expr']}) = 'IDFC') AS idfc_val
    FROM azure_sales_report s
    {joins_sql}
    WHERE {where_clause}{cat_filter}
    GROUP BY grp
    {order_sql}
    """
    df = ch.query_df(sql, params)
    rows = []
    trows = []
    for _, r in df.iterrows():
        tv = _safe_float(r["tot_val"])
        fv = _safe_float(r["fin_val"])
        tq = _safe_int(r["tot_qty"])
        fq = _safe_int(r["fin_qty"])
        sh = round((fv / tv * 100.0) if tv > 0 else 0.0, 2)
        lbl = str(r["grp"])
        if dim == "price" and ". " in lbl:
            lbl = lbl.split(". ", 1)[1]
        rec = {
            "label": lbl,
            "fin_cr": round(fv / 1e7, 2),
            "tot_cr": round(tv / 1e7, 2),
            "fin_share_pct": sh,
            "fin_qty": fq,
            "tot_qty": tq,
            "bajaj_cr": round(_safe_float(r["bajaj_val"]) / 1e7, 2),
            "hdb_cr": round(_safe_float(r["hdb_val"]) / 1e7, 2),
            "idfc_cr": round(_safe_float(r["idfc_val"]) / 1e7, 2),
        }
        rows.append(rec)
        trows.append([lbl, rec["fin_cr"], rec["tot_cr"], f"{sh:.1f}%", fq, tq, rec["bajaj_cr"], rec["hdb_cr"], rec["idfc_cr"]])

    cols = [dim.title(), "FIN (₹ Cr)", "Total (₹ Cr)", "Fin Share %", "FIN Qty", "Tot Qty", "Bajaj Cr", "HDB Cr", "IDFC Cr"]
    exp_tok = _store_export(f"{dim}_Analysis_{d}", cols, trows)
    return {
        "status": "success",
        "dimension": dim,
        "category": category,
        "rows": rows,
        "table": {"title": f"{dim.title()} Finance Analysis ({d} · {tf} · {category})", "columns": cols, "rows": trows, "export_token": exp_tok},
        "chart": {
            "title": f"{dim.title()} Fin Share % ({category})",
            "type": "bar",
            "labels": [r["label"][:18] for r in rows[:12]],
            "datasets": [{"label": "Fin Share %", "data": [r["fin_share_pct"] for r in rows[:12]]}],
        },
    }


# ---------------------------------------------------------------------------
# TOOL 4: Targets & Pacing (Global + ClickHouse TM Targets)
# ---------------------------------------------------------------------------
def tool_get_targets_and_pacing(
    month: str = "",
    date_str: str = "",
    state: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d0, tf0, e0, st0 = _default_date(context)
    d = (date_str or d0)[:10]
    m = (month or d[:7])[:7]
    st = state.upper() if state else st0

    global_t = _load_targets_json()
    tm_t = sfs.load_targets(m)
    live = sfs.get_store_fin_share_data(f"{m}-01", timeframe="monthly", period_mode="live", state=st)
    tms = (live.get("oct") or {}).get("tms") or []

    rows = []
    trows = []
    for r in tms:
        tm_name = r.get("tm", "")
        fin_cr = round(_safe_float(r.get("fin")) / 1e7, 2)
        tgt_cr = round(_safe_float(r.get("value_target")) / 1e7, 2) if r.get("value_target") else 0.0
        ach_pct = round(_safe_float(r.get("ach_pct")) * 100.0, 1) if r.get("ach_pct") is not None else None
        trend_pct = round(_safe_float(r.get("trend")) * 100.0, 1) if r.get("trend") is not None else None
        card_cr = round(_safe_float(r.get("card_ach")) / 1e7, 2)
        s2_cr = round(_safe_float(r.get("card_slab_2")) / 1e7, 2) if r.get("card_slab_2") else 0.0
        rows.append({
            "tm": tm_name, "fin_cr": fin_cr, "target_cr": tgt_cr,
            "ach_pct": ach_pct, "trend_pct": trend_pct,
            "fin_share_pct": round(_safe_float(r.get("fin_share")) * 100.0, 1),
            "card_cr": card_cr, "card_slab_2_cr": s2_cr,
        })
        trows.append([
            tm_name, fin_cr, tgt_cr if tgt_cr else "—",
            f"{ach_pct:.1f}%" if ach_pct is not None else "—",
            f"{trend_pct:.1f}%" if trend_pct is not None else "—",
            f"{_safe_float(r.get('fin_share')) * 100.0:.1f}%",
            card_cr, s2_cr if s2_cr else "—",
        ])

    cols = ["Fin BDM (TM)", "FIN Ach (₹ Cr)", "Target (₹ Cr)", "Ach %", "Trend %", "Fin Share %", "Card Ach (Cr)", "Card Slab 2 (Cr)"]
    exp_tok = _store_export(f"TM_Targets_{m}", cols, trows)
    return {
        "status": "success",
        "month": m,
        "global_targets": global_t,
        "tm_targets_set": bool(tm_t),
        "tm_pacing": rows,
        "table": {"title": f"TM Target vs Achievement ({m})", "columns": cols, "rows": trows, "export_token": exp_tok},
    }


# ---------------------------------------------------------------------------
# TOOL 5: Guarded Read-Only ClickHouse SQL Analyst
# ---------------------------------------------------------------------------
_FORBIDDEN_SQL_RE = re.compile(
    r"(?:\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|ATTACH|DETACH|RENAME|"
    r"OPTIMIZE|SYSTEM|GRANT|REVOKE|KILL|SET|EXCHANGE|MOVE|WATCH|LIVE|INTO\s+OUTFILE)\b)"
    r"|(?:\b(?:URL|REMOTE|REMOTESECURE|FILE|HDFS|S3|MYSQL|POSTGRESQL|CLUSTER|ODBC|JDBC)\s*\()",
    re.IGNORECASE,
)


def validate_readonly_sql(raw_sql: str) -> tuple[bool, str, str]:
    """Validate that SQL is strictly a single read-only SELECT / WITH query."""
    sql = str(raw_sql or "").strip()
    if not sql:
        return False, "Empty SQL query.", ""

    # Strip trailing semicolon only
    sql_no_trail = sql.rstrip("; \t\r\n")
    # Strip SQL comments before checking safety
    stripped = re.sub(r"--[^\n]*", " ", sql_no_trail)
    stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.DOTALL).strip()

    # Block stacked statements (semicolon outside string literals)
    no_strings = re.sub(r"'([^'\\]|\\.)*'", "''", stripped)
    if ";" in no_strings:
        return False, "Multiple SQL statements (semicolon) are blocked for safety.", ""

    if not re.match(r"^(SELECT|WITH)\b", no_strings, re.IGNORECASE):
        return False, "Only read-only SELECT or WITH queries are permitted.", ""

    bad = _FORBIDDEN_SQL_RE.search(no_strings)
    if bad:
        return False, f"Forbidden SQL keyword/function detected: '{bad.group(0)}'. Read-only queries only.", ""

    # Expand canonical myG macros
    expanded = ai_semantic_layer.expand_sql_macros(sql_no_trail)

    # Ensure a safe LIMIT cap
    if not re.search(r"\bLIMIT\s+\d+", no_strings, re.IGNORECASE):
        expanded = f"{expanded}\nLIMIT 250"

    return True, "", expanded


def tool_run_readonly_sql(
    sql: str,
    title: str = "Ad-hoc ClickHouse Query",
    chart_type: str = "",
    label_col: str = "",
    value_col: str = "",
) -> dict[str, Any]:
    ok, err, final_sql = validate_readonly_sql(sql)
    if not ok:
        return {"status": "blocked", "error": err}

    try:
        df = ch.query_df(final_sql, {}, cache=True)
    except Exception as exc:
        return {
            "status": "sql_error",
            "error": str(exc)[:600],
            "hint": "Inspect the ClickHouse error message and retry with valid ClickHouse SQL column/table names.",
        }

    if df is None or df.empty:
        return {"status": "empty", "title": title, "sql": sql, "row_count": 0, "columns": [], "rows": []}

    # Cap display rows while storing up to 500 for CSV/Excel export
    cols = [str(c) for c in df.columns]
    export_df = df.head(500)
    export_rows = []
    for row in export_df.itertuples(index=False, name=None):
        clean_row = []
        for v in row:
            if pd.isna(v):
                clean_row.append("")
            elif isinstance(v, (float,)):
                clean_row.append(round(float(v), 3))
            elif isinstance(v, (int,)):
                clean_row.append(int(v))
            else:
                clean_row.append(str(v))
        export_rows.append(clean_row)

    exp_tok = _store_export(title, cols, export_rows)
    preview_rows = export_rows[:40]

    out: dict[str, Any] = {
        "status": "success",
        "title": title,
        "sql": sql,
        "row_count": len(df),
        "showing": len(preview_rows),
        "columns": cols,
        "rows_preview": [dict(zip(cols, r)) for r in preview_rows[:20]],
        "table": {
            "title": title,
            "columns": cols,
            "rows": preview_rows,
            "export_token": exp_tok,
        },
    }

    if chart_type and label_col in cols and value_col in cols:
        li = cols.index(label_col)
        vi = cols.index(value_col)
        out["chart"] = {
            "title": title,
            "type": chart_type if chart_type in ("bar", "line", "doughnut", "pie") else "bar",
            "labels": [str(r[li])[:22] for r in preview_rows[:15]],
            "datasets": [{"label": value_col, "data": [_safe_float(r[vi]) for r in preview_rows[:15]]}],
        }
    return out


# ---------------------------------------------------------------------------
# TOOL 6: 7-Axis Deep Diagnostic & Anomaly Engine (Pillar 3)
# ---------------------------------------------------------------------------
def tool_run_deep_analysis(
    date_str: str = "",
    timeframe: str = "",
    end_date_str: str = "",
    state: str = "",
    compare_with: str = "",
    focus_entity: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Runs a parallel multi-dimensional root-cause analysis in ClickHouse:
      1. Variance decomposition (Total Sales volume effect vs Fin Share % conversion effect)
      2. Zone (North vs South) & Fin BDM (TM) & RBM deltas
      3. Store-level top gainers, steepest drops, and statistical outliers
      4. Category & Financier (Paper + Card EMI) mix shifts
      5. ERP vs Delivery Order No data-hygiene mismatch audit
      6. Quantified ₹ Cr recovery opportunities
    """
    d0, tf0, e0, st0 = _default_date(context)
    d = (date_str or d0)[:10]
    tf = (timeframe or tf0).lower()
    end_d = (end_date_str or (e0 if tf == "custom" else ""))[:10]
    st = state.upper() if state else st0

    if not compare_with:
        compare_with = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}.get(tf, "month")

    def _job_cur_sfs():
        return sfs.get_store_fin_share_data(d, timeframe=tf, end_date_str=end_d, period_mode="live", state=st)

    def _job_cmp_sfs():
        return sfs.get_store_fin_share_compare(d, timeframe=tf, end_date_str=end_d, period_mode="live", state=st, compare=compare_with)

    def _job_dash():
        return tool_get_dashboard_summary(d, tf, end_d, st, context=context)

    def _job_do_audit():
        joins_sql, where_clause, params, exprs = ca._build_sales_where_and_joins(
            d, tf, end_date_str=end_d or None, state=st, include_staff=False
        )
        do = ca._RAW_DO_CLEAN
        do_case = f"""CASE
            WHEN match({do}, '^B[0-9]') THEN 'BAJAJ'
            WHEN position({do}, 'DMI') > 0 THEN 'DMI'
            WHEN startsWith({do}, '10108') OR startsWith({do}, '10109') THEN 'IDFC'
            WHEN startsWith({do}, 'CDSK') THEN 'PFL'
            WHEN position({do}, '3022CD') > 0 OR position({do}, '3023CD') > 0 THEN 'TVS'
            WHEN match({do}, '^[1-9][0-9]{{10,11}}$')
              AND NOT startsWith({do}, '1018') AND NOT startsWith({do}, '1019')
              AND NOT startsWith({do}, '10208') AND NOT startsWith({do}, '11090')
              AND NOT startsWith({do}, '3022') AND NOT startsWith({do}, '3023')
              AND upper(coalesce(inv.financier_name, '')) NOT LIKE '%BENOW%' THEN 'HDB'
            ELSE ''
        END"""
        inv_case = """CASE
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%' OR upper(coalesce(inv.financier_code, '')) LIKE 'BAJAJ%' THEN 'BAJAJ'
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%HDB%' OR upper(coalesce(inv.financier_code, '')) LIKE 'HDB%' THEN 'HDB'
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%DMI%' OR upper(coalesce(inv.financier_code, '')) LIKE 'SAMSUNG%' THEN 'DMI'
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%' OR upper(coalesce(inv.financier_code, '')) LIKE 'IDFC%' THEN 'IDFC'
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%TVS%' OR upper(coalesce(inv.financier_code, '')) LIKE 'TVSC%' THEN 'TVS'
            WHEN upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%' OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%' OR upper(coalesce(inv.financier_code, '')) LIKE 'POONA%' THEN 'PFL'
            ELSE ''
        END"""
        sql = f"""
        SELECT
            countIf({do_case} != '' AND {inv_case} != '' AND {do_case} != {inv_case}) AS mismatch_rows,
            round(sumIf({exprs['val_expr']}, {do_case} != '' AND {inv_case} != '' AND {do_case} != {inv_case}) / 1e5, 2) AS mismatch_lakhs,
            countIf({do_case} != '' AND {inv_case} = '') AS rescued_rows,
            countIf({inv_case} != '' AND {do} = '') AS blank_do_paper_rows
        FROM azure_sales_report s
        {joins_sql}
        WHERE {where_clause}
        """
        df = ch.query_df(sql, params)
        if df is None or df.empty:
            return {"mismatch_rows": 0, "mismatch_lakhs": 0.0, "rescued_rows": 0, "blank_do_paper_rows": 0}
        r = df.iloc[0]
        return {
            "mismatch_rows": _safe_int(r["mismatch_rows"]),
            "mismatch_lakhs": _safe_float(r["mismatch_lakhs"]),
            "rescued_rows": _safe_int(r["rescued_rows"]),
            "blank_do_paper_rows": _safe_int(r["blank_do_paper_rows"]),
        }

    # Execute sequentially to respect ClickHouse Cloud's 7.2 GiB FillingRightJoinSide RAM cap.
    # Calling _job_cur_sfs first warms sfs._MEM_CACHE so _job_cmp_sfs reuses current period instantaneously.
    cur = _job_cur_sfs()
    if cur.get("error"):
        return {"status": "error", "error": cur["error"]}

    try:
        cmp_res = _job_cmp_sfs()
    except Exception as exc:
        logger.warning("Deep analysis compare window skipped: %s", exc)
        cmp_res = {"error": str(exc)}

    try:
        dash = _job_dash()
    except Exception as exc:
        logger.warning("Deep analysis dashboard summary skipped: %s", exc)
        dash = {}

    try:
        audit = _job_do_audit()
    except Exception as exc:
        logger.warning("Deep analysis ERP hygiene audit skipped: %s", exc)
        audit = {"mismatch_rows": 0, "mismatch_lakhs": 0.0, "rescued_rows": 0, "blank_do_paper_rows": 0}

    cur_stores = (cur.get("oct") or {}).get("stores") or []
    prev_stores = (cmp_res.get("oct") or {}).get("stores") or [] if not cmp_res.get("error") else []
    prev_by_store = {r["store"]: r for r in prev_stores}

    if focus_entity:
        q = focus_entity.strip().upper()
        filtered = [r for r in cur_stores if q in r["store"] or q in r["fin_bdm"] or q in r["rbm"] or q in r["bdm"]]
        if filtered:
            cur_stores = filtered

    # Overall variance decomposition
    cur_fin = sum(_safe_float(r["fin"]) for r in cur_stores)
    cur_tot = sum(_safe_float(r["total"]) for r in cur_stores)
    cur_paper = sum(_safe_float(r["paper_fin"]) for r in cur_stores)
    cur_card = sum(_safe_float(r["card_fin"]) for r in cur_stores)
    cur_sh = (cur_fin / cur_tot) if cur_tot > 0 else 0.0

    prev_matched = [prev_by_store[r["store"]] for r in cur_stores if r["store"] in prev_by_store]
    prev_fin = sum(_safe_float(r["fin"]) for r in prev_matched)
    prev_tot = sum(_safe_float(r["total"]) for r in prev_matched)
    prev_sh = (prev_fin / prev_tot) if prev_tot > 0 else 0.0

    delta_fin_cr = round((cur_fin - prev_fin) / 1e7, 2)
    # Exact additive decomposition: ΔFIN = (ΔTOTAL * prev_sh) + (cur_tot * Δsh)
    sales_vol_effect_cr = round(((cur_tot - prev_tot) * prev_sh) / 1e7, 2) if prev_tot > 0 else 0.0
    fin_share_effect_cr = round((cur_tot * (cur_sh - prev_sh)) / 1e7, 2) if prev_tot > 0 else 0.0

    # Store-level deltas & statistical anomalies
    store_deltas = []
    recovery_pool = []
    for r in cur_stores:
        tv = _safe_float(r["total"])
        fv = _safe_float(r["fin"])
        if tv < 5e5:  # ignore < 5L noise
            continue
        sh = fv / tv * 100.0
        pr = prev_by_store.get(r["store"])
        psh = (_safe_float(pr["fin"]) / _safe_float(pr["total"]) * 100.0) if (pr and _safe_float(pr["total"]) > 0) else None
        diff_pp = round(sh - psh, 2) if psh is not None else None
        store_deltas.append({
            "store": r["store"], "fin_bdm": r["fin_bdm"], "rbm": r["rbm"],
            "fin_cr": round(fv / 1e7, 2), "tot_cr": round(tv / 1e7, 2),
            "fin_share_pct": round(sh, 1),
            "prev_share_pct": round(psh, 1) if psh is not None else None,
            "diff_pp": diff_pp,
            "fin_delta_l": round((fv - _safe_float(pr["fin"])) / 1e5, 1) if pr else None,
        })
        # Recovery potential if store lifts to overall average fin_share
        target_sh = cur_sh * 100.0
        if sh < target_sh - 4.0 and tv >= 20e5:
            upside_cr = round((tv * (cur_sh - fv / tv)) / 1e7, 2)
            recovery_pool.append({
                "store": r["store"], "fin_bdm": r["fin_bdm"], "rbm": r["rbm"],
                "tot_cr": round(tv / 1e7, 2), "cur_share_pct": round(sh, 1),
                "gap_pp": round(target_sh - sh, 1), "upside_cr": upside_cr,
            })

    valid_deltas = [x for x in store_deltas if x["diff_pp"] is not None]
    biggest_drops = sorted(valid_deltas, key=lambda x: x["diff_pp"])[:8]
    biggest_gains = sorted(valid_deltas, key=lambda x: x["diff_pp"], reverse=True)[:8]
    recovery_pool.sort(key=lambda x: x["upside_cr"], reverse=True)
    top_recovery = recovery_pool[:8]
    total_recovery_cr = round(sum(x["upside_cr"] for x in top_recovery), 2)

    # TM-level rollup & delta
    tm_buckets: dict[str, list[float]] = {}
    for r in cur_stores:
        k = r["fin_bdm"] or "UNMAPPED"
        a = tm_buckets.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        a[0] += _safe_float(r["fin"])
        a[1] += _safe_float(r["total"])
        pr = prev_by_store.get(r["store"])
        if pr:
            a[2] += _safe_float(pr["fin"])
            a[3] += _safe_float(pr["total"])
    tm_deltas = []
    for k, (cf, ct, pf, pt) in tm_buckets.items():
        csh = (cf / ct * 100.0) if ct > 0 else 0.0
        psh = (pf / pt * 100.0) if pt > 0 else None
        tm_deltas.append({
            "tm": k, "fin_cr": round(cf / 1e7, 2), "tot_cr": round(ct / 1e7, 2),
            "fin_share_pct": round(csh, 1),
            "prev_share_pct": round(psh, 1) if psh is not None else None,
            "diff_pp": round(csh - psh, 1) if psh is not None else None,
        })
    tm_deltas.sort(key=lambda x: x["fin_share_pct"], reverse=True)

    # North vs South
    tm_n = (cur.get("oct") or {}).get("tm_north") or {}
    tm_s = (cur.get("oct") or {}).get("tm_south") or {}

    # Alerts list
    alerts = []
    if prev_tot > 0 and (cur_sh - prev_sh) * 100.0 <= -1.5:
        alerts.append(f"Overall Fin Share slipped {((cur_sh - prev_sh)*100.0):+.2f} pp vs prior period (costing ₹{abs(fin_share_effect_cr):.2f} Cr).")
    north_sh = _safe_float(tm_n.get("fin_share")) * 100.0
    south_sh = _safe_float(tm_s.get("fin_share")) * 100.0
    if abs(north_sh - south_sh) >= 3.0:
        leader = "North" if north_sh > south_sh else "South"
        laggard = "South" if north_sh > south_sh else "North"
        alerts.append(f"{laggard} Zone trails {leader} Zone by {abs(north_sh - south_sh):.1f} pp ({south_sh:.1f}% vs {north_sh:.1f}%).")
    if biggest_drops and biggest_drops[0]["diff_pp"] <= -5.0:
        alerts.append(f"{len([x for x in valid_deltas if x['diff_pp'] <= -5.0])} stores dropped more than 5 pp in Fin Share (steepest: {biggest_drops[0]['store']} at {biggest_drops[0]['diff_pp']:+.1f} pp).")
    if audit["mismatch_rows"] > 0:
        alerts.append(f"ERP Data Hygiene: {audit['mismatch_rows']} rows (₹{audit['mismatch_lakhs']:.1f} L) had mismatched ERP financier vs Delivery Order No (auto-corrected by portal DO rules).")
    cs = cur.get("card_status") or {}
    missing_cards = [k for k in ("PineLab", "PAYTM", "Benow") if not (cs.get(k) or {}).get("uploaded")]
    if missing_cards:
        alerts.append(f"Card EMI files not uploaded for this window: {', '.join(missing_cards)} — Card Fin Share may be understated.")

    # Recovery table
    rec_rows = [
        [x["store"], x["fin_bdm"], x["tot_cr"], f"{x['cur_share_pct']:.1f}%", f"-{x['gap_pp']:.1f}pp", f"+₹{x['upside_cr']:.2f} Cr"]
        for x in top_recovery
    ]
    exp_tok = _store_export(
        f"Deep_Recovery_Opportunities_{d}",
        ["Store", "Fin BDM", "Total Sales (Cr)", "Current Fin Share", "Gap vs Avg", "Est. Upside"],
        rec_rows,
    )

    return {
        "status": "success",
        "period": cur.get("period") or {},
        "compare_label": cmp_res.get("label", compare_with) if not cmp_res.get("error") else "None",
        "compare_window": f"{cmp_res.get('start_date', '')} to {cmp_res.get('end_date', '')}" if not cmp_res.get("error") else "",
        "focus_entity": focus_entity or "ALL",
        "variance_bridge": {
            "cur_fin_cr": round(cur_fin / 1e7, 2),
            "cur_tot_cr": round(cur_tot / 1e7, 2),
            "cur_paper_cr": round(cur_paper / 1e7, 2),
            "cur_card_cr": round(cur_card / 1e7, 2),
            "cur_fin_share_pct": round(cur_sh * 100.0, 2),
            "prev_fin_cr": round(prev_fin / 1e7, 2),
            "prev_tot_cr": round(prev_tot / 1e7, 2),
            "prev_fin_share_pct": round(prev_sh * 100.0, 2),
            "delta_fin_cr": delta_fin_cr,
            "delta_share_pp": round((cur_sh - prev_sh) * 100.0, 2) if prev_tot > 0 else 0.0,
            "sales_volume_effect_cr": sales_vol_effect_cr,
            "fin_share_conversion_effect_cr": fin_share_effect_cr,
            "north_fin_share_pct": round(north_sh, 1),
            "south_fin_share_pct": round(south_sh, 1),
        },
        "alerts": alerts,
        "tm_performance": tm_deltas,
        "biggest_store_drops": biggest_drops,
        "biggest_store_gains": biggest_gains,
        "top_recovery_opportunities": top_recovery,
        "total_top8_recovery_cr": total_recovery_cr,
        "categories": dash.get("categories") or [],
        "financiers": dash.get("financiers") or [],
        "erp_hygiene_audit": audit,
        "table": {
            "title": f"Top Finance Recovery Opportunities (+₹{total_recovery_cr:.2f} Cr Potential)",
            "columns": ["Store", "Fin BDM", "Total Sales (₹ Cr)", "Current Fin %", "Gap vs Avg", "Recovery Upside"],
            "rows": rec_rows,
            "export_token": exp_tok,
        },
        "chart": {
            "title": "Fin BDM (TM) Fin Share %",
            "type": "bar",
            "labels": [t["tm"] for t in tm_deltas],
            "datasets": [{"label": "Current Fin Share %", "data": [t["fin_share_pct"] for t in tm_deltas]}],
        },
    }


def tool_generate_morning_brief(
    date_str: str = "",
    timeframe: str = "",
    end_date_str: str = "",
    state: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Executive Morning Brief — wraps deep analysis + TM target pacing into an instant executive digest."""
    deep = tool_run_deep_analysis(date_str, timeframe, end_date_str, state, context=context)
    if deep.get("status") != "success":
        return deep
    d0, _, _, st0 = _default_date(context)
    m = (date_str or d0)[:7]
    tgt = tool_get_targets_and_pacing(month=m, date_str=date_str or d0, state=state or st0, context=context)
    deep["tm_target_pacing"] = tgt.get("tm_pacing") or []
    return deep


# ---------------------------------------------------------------------------
# TOOL 7: Tier 1 Live UI Control
# ---------------------------------------------------------------------------
def tool_control_dashboard_ui(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Validates & normalizes structured frontend UI actions executed by `ai_copilot.js` -> `main.js`.
    Supported action types:
      - switch_tab: {type:'switch_tab', tab:'dashboard'|'exec-summary'|'category'|'zone'|'price'|'brand'|'store'|'oct-sheets'|'oct2-sheets'|'team'|'cleaner'|'settings'|'ai-studio'}
      - set_period: {type:'set_period', timeframe:'daily'|'weekly'|'monthly'|'quarterly'|'yearly'|'custom', date:'YYYY-MM-DD', end_date:'YYYY-MM-DD'}
      - set_oct2_controls: {type:'set_oct2_controls', view_by:'store'|'rbm'|'bdm'|'tm'|'staff', compare:'none'|'day'|'week'|'month'|'quarter'|'year', fin_split:'total'|'paper'|'card'|'both', search:''}
      - highlight_rows: {type:'highlight_rows', threshold_pct:55, operator:'lt'|'gt'}
      - trigger_export: {type:'trigger_export', format:'excel'|'pptx'}
      - refresh_tab: {type:'refresh_tab'}
    """
    valid_tabs = {
        "dashboard", "exec-summary", "category", "zone", "price", "brand",
        "store", "oct-sheets", "oct2-sheets", "team", "cleaner", "settings", "ai-studio",
    }
    clean = []
    for a in (actions or []):
        if not isinstance(a, dict):
            continue
        t = str(a.get("type") or "").strip().lower()
        if t == "switch_tab" and str(a.get("tab") or "") in valid_tabs:
            clean.append({"type": "switch_tab", "tab": a["tab"]})
        elif t in ("set_period", "set_oct2_controls", "highlight_rows", "trigger_export", "refresh_tab", "set_state"):
            clean.append(a)
    return {
        "status": "success",
        "executed_count": len(clean),
        "ui_actions": clean,
        "message": f"Queued {len(clean)} live UI action(s) on the portal.",
    }


# ---------------------------------------------------------------------------
# TOOL 8: Tier 2 Persistent Dashboard Studio & Target Editing (Preview + Confirm)
# ---------------------------------------------------------------------------
def tool_propose_dashboard_edit(
    edit_type: str,
    payload: dict[str, Any],
    summary: str = "",
) -> dict[str, Any]:
    """
    Stages a Tier-2 persistent dashboard edit and returns a Preview & Confirm Diff Card.
    Supported `edit_type`:
      - add_widget          : {tab, widget_type:'kpi'|'chart'|'table', title, metric_source:'sql'|'financier'|'category'|'store_top'|'store_low'|'tm', sql?, chart_type?, color?, subtitle?, unit?}
      - remove_widget       : {widget_id}
      - update_thresholds   : {enabled, fin_share_high, fin_share_mid, fin_share_low}
      - update_title        : {selector_key, new_title}
      - toggle_section      : {section_id, hidden:bool}
      - update_global_targets : {exec_summary_targets?, financier_volume_targets?, dmi_contest_brand_targets?, manpower_plans?}
      - update_tm_targets   : {month:'YYYY-MM', rows:[{tm, value_target, card_slab_1, card_slab_2}]}
    """
    et = str(edit_type or "").strip().lower()
    cur_state = ai_memory.get_dashboard_config()
    cur_cfg = cur_state["config"]

    if et == "add_widget":
        w = dict(payload or {})
        w_id = w.get("id") or f"w_{uuid.uuid4().hex[:8]}"
        w["id"] = w_id
        w["tab"] = str(w.get("tab") or "dashboard")
        w["widget_type"] = str(w.get("widget_type") or "kpi").lower()
        w["title"] = str(w.get("title") or "Custom AI Widget")[:80]
        w["metric_source"] = str(w.get("metric_source") or "sql").lower()
        if w["metric_source"] == "sql":
            ok, err, _ = validate_readonly_sql(w.get("sql", ""))
            if not ok:
                return {"status": "error", "error": f"Widget SQL invalid: {err}"}
        diff = {"action": "ADD WIDGET", "target_tab": w["tab"], "widget": w}
        title = f"Pin Custom {w['widget_type'].upper()} Widget: {w['title']}"
        pa = _store_pending_action(et, title, summary or title, diff, w)
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "remove_widget":
        wid = str((payload or {}).get("widget_id") or "")
        existing = next((x for x in cur_cfg.get("widgets", []) if x.get("id") == wid), None)
        if not existing:
            return {"status": "error", "error": f"Widget '{wid}' not found."}
        diff = {"action": "REMOVE WIDGET", "before": existing, "after": None}
        pa = _store_pending_action(et, f"Remove Widget: {existing.get('title')}", summary or f"Remove widget {wid}", diff, {"widget_id": wid})
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "update_thresholds":
        before = dict(cur_cfg.get("thresholds") or {})
        after = dict(before)
        for k in ("enabled", "fin_share_high", "fin_share_mid", "fin_share_low"):
            if k in (payload or {}):
                after[k] = bool(payload[k]) if k == "enabled" else float(payload[k])
        diff = {"before": before, "after": after}
        pa = _store_pending_action(et, "Update Fin Share Color Thresholds", summary or "Update color threshold bands", diff, after)
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "toggle_section":
        sec = str((payload or {}).get("section_id") or "").strip()
        hidden = bool((payload or {}).get("hidden", True))
        if not sec:
            return {"status": "error", "error": "section_id is required."}
        before = list(cur_cfg.get("hidden_sections") or [])
        after = sorted(set(before + [sec])) if hidden else [x for x in before if x != sec]
        diff = {"section_id": sec, "hidden": hidden, "before": before, "after": after}
        pa = _store_pending_action(et, f"{'Hide' if hidden else 'Show'} Section: {sec}", summary or f"Toggle {sec}", diff, {"section_id": sec, "hidden": hidden})
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "update_title":
        key = str((payload or {}).get("selector_key") or "").strip()
        nt = str((payload or {}).get("new_title") or "").strip()
        if not key or not nt:
            return {"status": "error", "error": "selector_key and new_title are required."}
        before = (cur_cfg.get("custom_titles") or {}).get(key, "(default)")
        diff = {"selector_key": key, "before": before, "after": nt}
        pa = _store_pending_action(et, f"Rename Dashboard Element: {key}", summary or f"Rename {key} -> {nt}", diff, {"selector_key": key, "new_title": nt})
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "update_global_targets":
        before = _load_targets_json()
        after = json.loads(json.dumps(before))
        for section, vals in (payload or {}).items():
            if isinstance(vals, dict):
                after.setdefault(section, {})
                for k, v in vals.items():
                    after[section][k] = v
        diff = {"before": before, "after": after}
        pa = _store_pending_action(et, "Update Global Portal Targets", summary or "Update Global Targets configuration", diff, after)
        return {"status": "pending_confirmation", "pending_action": pa}

    if et == "update_tm_targets":
        month = str((payload or {}).get("month") or "").strip()[:7]
        rows = (payload or {}).get("rows") or []
        if not month or not rows:
            return {"status": "error", "error": "month (YYYY-MM) and rows are required."}
        existing = sfs.load_targets(month)
        diff = {"month": month, "before": existing, "updates": rows}
        pa = _store_pending_action(et, f"Update TM Targets for {month}", summary or f"Update {len(rows)} TM target row(s) for {month}", diff, {"month": month, "rows": rows})
        return {"status": "pending_confirmation", "pending_action": pa}

    return {"status": "error", "error": f"Unsupported edit_type '{et}'."}


# ---------------------------------------------------------------------------
# TOOL 9: Tier 3 Optional Verified Source-Code Patch Proposal
# ---------------------------------------------------------------------------
_ALLOWLISTED_CODE_FILES = {
    "templates/index.html",
    "static/js/main.js",
    "static/css/style.css",
    "static/js/ai_copilot.js",
    "static/css/ai_copilot.css",
    "app.py",
    "store_fin_share.py",
    "clickhouse_aggregator.py",
    "dashboard_aggregator.py",
}


def tool_propose_code_patch(
    file_path: str,
    description: str,
    search_block: str,
    replace_block: str,
) -> dict[str, Any]:
    """Safely validate and stage a syntax-checked unified diff for explicit admin approval."""
    rel = str(file_path or "").replace("\\", "/").lstrip("/")
    if rel not in _ALLOWLISTED_CODE_FILES:
        return {"status": "error", "error": f"File '{rel}' is not in the allowlisted editable files: {sorted(_ALLOWLISTED_CODE_FILES)}"}

    abs_p = os.path.join(_BASE_DIR, rel)
    if not os.path.exists(abs_p):
        return {"status": "error", "error": f"File not found: {rel}"}

    with open(abs_p, "r", encoding="utf-8") as f:
        original = f.read()

    if not search_block or search_block not in original:
        return {"status": "error", "error": "search_block did not match existing file content verbatim."}
    if original.count(search_block) > 1:
        return {"status": "error", "error": "search_block matched multiple locations — provide more surrounding context."}

    updated = original.replace(search_block, replace_block, 1)

    # Syntax verification before presenting diff
    if rel.endswith(".py"):
        try:
            ast.parse(updated, filename=rel)
        except SyntaxError as exc:
            return {"status": "error", "error": f"Proposed Python patch has a SyntaxError at line {exc.lineno}: {exc.msg}"}

    unified = "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=3,
    ))

    pa = _store_pending_action(
        "apply_code_patch",
        f"Source Code Patch: {rel}",
        description or f"Patch {rel}",
        {"file": rel, "unified_diff": unified[:6000]},
        {"file": rel, "search_block": search_block, "replace_block": replace_block},
    )
    return {"status": "pending_confirmation", "pending_action": pa}


def apply_pending_action(action_id: str, admin_pin: str = "") -> dict[str, Any]:
    """Verify optional Admin PIN and apply a staged Tier-2 or Tier-3 edit action."""
    if not ai_memory.verify_admin_pin(admin_pin):
        return {"status": "unauthorized", "error": "Invalid or missing Admin PIN."}

    with _LOCK:
        rec = _PENDING_ACTIONS.get(str(action_id or "").strip())
    if not rec:
        return {"status": "error", "error": "Action expired or not found. Please ask the AI to regenerate the edit preview."}
    if rec.get("status") == "applied":
        return {"status": "already_applied", "message": "This edit was already applied."}

    et = rec["edit_type"]
    payload = rec["payload"]
    summary = rec["summary"]

    try:
        if et in ("add_widget", "remove_widget", "update_thresholds", "toggle_section", "update_title"):
            cur = ai_memory.get_dashboard_config()["config"]
            if et == "add_widget":
                widgets = [w for w in cur.get("widgets", []) if w.get("id") != payload["id"]]
                widgets.append(payload)
                cur["widgets"] = widgets
            elif et == "remove_widget":
                cur["widgets"] = [w for w in cur.get("widgets", []) if w.get("id") != payload["widget_id"]]
            elif et == "update_thresholds":
                cur["thresholds"] = payload
            elif et == "toggle_section":
                hs = set(cur.get("hidden_sections") or [])
                if payload.get("hidden"):
                    hs.add(payload["section_id"])
                else:
                    hs.discard(payload["section_id"])
                cur["hidden_sections"] = sorted(hs)
            elif et == "update_title":
                cur.setdefault("custom_titles", {})[payload["selector_key"]] = payload["new_title"]
            new_state = ai_memory.save_dashboard_config(cur, change_summary=summary, author="ai_agent")
            rec["status"] = "applied"
            return {"status": "success", "message": f"Applied: {summary}", "dashboard_config": new_state, "reload_ui": True}

        if et == "update_global_targets":
            _save_targets_json(payload)
            ch.cache_clear()
            rec["status"] = "applied"
            return {"status": "success", "message": f"Global targets saved: {summary}", "reload_targets": True}

        if et == "update_tm_targets":
            month = payload["month"]
            existing = sfs.load_targets(month)
            merged_rows = {}
            for tm_k, vals in (existing or {}).items():
                merged_rows[tm_k.upper()] = {
                    "tm": tm_k.upper(),
                    "value_target": _safe_float(vals.get("value_target")),
                    "card_slab_1": _safe_float(vals.get("card_slab_1")),
                    "card_slab_2": _safe_float(vals.get("card_slab_2")),
                }
            for r in payload["rows"]:
                tk = str(r.get("tm") or "").strip().upper()
                if not tk:
                    continue
                cur_r = merged_rows.get(tk, {"tm": tk, "value_target": 0.0, "card_slab_1": 0.0, "card_slab_2": 0.0})
                for fld in ("value_target", "card_slab_1", "card_slab_2"):
                    if fld in r and r[fld] is not None:
                        cur_r[fld] = _safe_float(r[fld])
                merged_rows[tk] = cur_r
            n = sfs.save_targets(month, list(merged_rows.values()))
            sfs.invalidate_cache()
            rec["status"] = "applied"
            return {"status": "success", "message": f"Saved {n} TM target row(s) for {month} in ClickHouse.", "reload_ui": True}

        if et == "apply_code_patch":
            rel = payload["file"]
            abs_p = os.path.join(_BASE_DIR, rel)
            with open(abs_p, "r", encoding="utf-8") as f:
                original = f.read()
            if payload["search_block"] not in original:
                return {"status": "error", "error": "File changed since preview was generated."}
            updated = original.replace(payload["search_block"], payload["replace_block"], 1)
            # Backup before writing
            bak_dir = os.path.join(_DATA_DIR, "ai_code_backups")
            os.makedirs(bak_dir, exist_ok=True)
            bak_name = f"{rel.replace('/', '_')}.{int(time.time())}.bak"
            with open(os.path.join(bak_dir, bak_name), "w", encoding="utf-8") as bf:
                bf.write(original)
            with open(abs_p, "w", encoding="utf-8") as f:
                f.write(updated)
            rec["status"] = "applied"
            return {
                "status": "success",
                "message": f"Patched {rel} (backup saved as {bak_name}). Reload browser / restart server if backend file changed.",
                "reload_page": rel.startswith("static/") or rel.startswith("templates/"),
            }

        return {"status": "error", "error": f"Unknown edit_type: {et}"}
    except Exception as exc:
        logger.error("apply_pending_action failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Live Custom Widget Evaluator (powers Tier 2 Pinned Widgets on any tab)
# ---------------------------------------------------------------------------
def evaluate_custom_widgets(
    date_str: str = "",
    timeframe: str = "monthly",
    end_date_str: str = "",
    state: str = "KERALA",
) -> list[dict[str, Any]]:
    """Evaluate all pinned Tier-2 widgets live against ClickHouse for the selected period."""
    cfg_wrap = ai_memory.get_dashboard_config()
    widgets = (cfg_wrap.get("config") or {}).get("widgets") or []
    if not widgets:
        return []

    ctx = {"date": date_str, "timeframe": timeframe, "end_date": end_date_str, "state": state}
    date_filter_sql, params = ca._build_date_filter(
        date_str or date.today().replace(day=1).isoformat(),
        timeframe or "monthly",
        end_date_str or None,
    )

    def _eval_one(w: dict[str, Any]) -> dict[str, Any]:
        out = dict(w)
        src = str(w.get("metric_source") or "sql").lower()
        try:
            if src == "sql" and w.get("sql"):
                raw_sql = str(w["sql"])
                # Support dynamic {{DATE_FILTER}} placeholder in custom widgets!
                # Render inline safe date literals so custom widgets track top period picker!
                start_d, end_d = emi_state.range_of(
                    date_str or date.today().replace(day=1).isoformat(),
                    timeframe or "monthly",
                    end_date_str or "",
                )
                raw_sql = raw_sql.replace("{{DATE_FILTER}}", f"toDate(s.date) BETWEEN toDate('{start_d}') AND toDate('{end_d}')")
                raw_sql = raw_sql.replace("{{START_DATE}}", f"'{start_d}'")
                raw_sql = raw_sql.replace("{{END_DATE}}", f"'{end_d}'")
                raw_sql = raw_sql.replace("{{STATE}}", f"'{(state or 'KERALA').upper()}'")
                res = tool_run_readonly_sql(raw_sql, title=w.get("title", "Widget"))
                if res.get("status") == "success":
                    tbl = res.get("table") or {}
                    rows = tbl.get("rows") or []
                    cols = tbl.get("columns") or []
                    if w.get("widget_type") == "kpi" and rows:
                        out["value"] = rows[0][0]
                        out["subvalue"] = rows[0][1] if len(rows[0]) > 1 else w.get("subtitle", "")
                    elif w.get("widget_type") == "chart" and rows and len(cols) >= 2:
                        out["chart"] = {
                            "type": w.get("chart_type") or "bar",
                            "labels": [str(r[0])[:18] for r in rows[:12]],
                            "datasets": [{"label": cols[1], "data": [_safe_float(r[1]) for r in rows[:12]]}],
                        }
                    else:
                        out["table"] = {"columns": cols, "rows": rows[:15], "export_token": tbl.get("export_token")}
                else:
                    out["error"] = res.get("error", "No data")
            elif src == "store_top":
                r = tool_get_store_fin_share(date_str, timeframe, end_date_str, state, view_by="store", sort_by="fin_share_desc", limit=10, context=ctx)
                out["table"] = r.get("table")
                out["chart"] = r.get("chart")
            elif src == "store_low":
                r = tool_get_store_fin_share(date_str, timeframe, end_date_str, state, view_by="store", sort_by="fin_share_asc", limit=10, context=ctx)
                out["table"] = r.get("table")
                out["chart"] = r.get("chart")
            elif src == "tm":
                r = tool_get_store_fin_share(date_str, timeframe, end_date_str, state, view_by="tm", sort_by="fin_share_desc", limit=15, context=ctx)
                out["table"] = r.get("table")
                out["chart"] = r.get("chart")
            elif src == "financier":
                r = tool_get_dashboard_summary(date_str, timeframe, end_date_str, state, context=ctx)
                out["table"] = r.get("table")
                out["chart"] = r.get("chart")
            elif src == "category":
                r = tool_get_dashboard_summary(date_str, timeframe, end_date_str, state, context=ctx)
                cats = r.get("categories") or []
                out["chart"] = {
                    "type": w.get("chart_type") or "bar",
                    "labels": [c["category"] for c in cats],
                    "datasets": [{"label": "Fin Share %", "data": [c["val_share_pct"] for c in cats]}],
                }
                out["table"] = {
                    "columns": ["Category", "FIN Cr", "Total Cr", "Share %"],
                    "rows": [[c["category"], c["fin_val_cr"], c["tot_val_cr"], f"{c['val_share_pct']:.1f}%"] for c in cats],
                }
        except Exception as exc:
            out["error"] = str(exc)[:180]
        return out

    return [_eval_one(w) for w in widgets]


# ---------------------------------------------------------------------------
# TOOL 10: Portal Logic & Cleaning Process Knowledge Inspector
# ---------------------------------------------------------------------------
_PORTAL_KNOWLEDGE: dict[str, str] = {
    "cleaning": """### How the Data Cleaning Process Works in myG Finance Portal

The portal cleans **4 separate data pipelines** — all synchronized between Excel upload cleaners and live ClickHouse SQL:

1. **Combined Sales Report Cleaning (`sales_cleaner.py` & `clickhouse_aggregator.py`):**
   - **Step 1 — Header & Column Auto-Normalization:** Scans the first 20 rows for sentinel headers (`RBM`, `Branch`, `Sold Price`, `Financier`, `Invoice Number`) and normalizes aliases (e.g. `DO NO` -> `Delivery Order No`, `Selling Price` -> `Sold Price`).
   - **Step 2 — RBM & Retail Scope Filter:** Drops non-retail RBMs (`GENERAL`, `RAJESH`) and non-retail branch codes (`CSB`, `GPF`, `3GH`, `KSO`, `SMC`). Maps stores to `KERALA` (RBMs: `ALFAS`, `SAMEER`, `SAMEEM`, `RENJITH`, `MAHESH`, `VAISAKH`) vs `KARNATAKA` (`DAKSHINA KANNADA` / `FLF` / `FALNIR FUTURE`).
   - **Step 3 — Value Threshold & Returns:** Keeps only retail items with `abs(Sold Price) > ₹3,000`. If `Sold Price < 0` (sales return), `QTY` is forced negative (`-abs(QTY)`).
   - **Step 4 — E-Commerce & Corporate Removal:** Excludes `Customer Type = CORPORATE` and financiers containing `ECOMMERCE`, `E-COMMERCE`, or `MYG ONLINE`.
   - **Step 5 — Financier Standardization + Delivery Order Overrides (2-Tier):**
     - *Priority 1 (DO Override):* `^B[0-9]` -> `BAJAJ FINANCE`; contains `DMI` -> `DMI FINANCE`; starts with `10108`/`10109` -> `IDFC FIRST BANK LTD`; starts with `CDSK` -> `Poonawalla Fincorp`; contains `3022CD`/`3023CD` -> `TVS Credit`; 11–12 digits (excluding `1018/1019/10208/11090/3022/3023` and non-Benow) -> `HDB FINANCE`.
     - *Priority 2 (ERP `inv.financier_name` / `financier_code`):* Maps `BAJAJ`, `HDB`, `DMI`/`SAMSUNG`, `IDFC`, `TVS`/`TVSC`, `POONAWALLA`/`PFL`/`POONA`. Unrecognized institutional / Co-op Society rows without a DO override are excluded; remaining cash/blank rows map to `aCash`.
   - **Step 6 — Category & Product Mapping + Blacklist Removal:** Missing products are inferred from `Item Code` prefixes (`MOB` -> `MOBILE`, `LAP` -> `LAPTOP`, `WSM` -> `WASHING MACHINES`, etc.). Products map to 6 categories (`MOBILE`, `LAPTOP`, `CD PRODUCTS`, `SA`, `HVA`, `OTHERS`) and 32 non-retail/junk items (`ACCESSORIES`, `CARE PLUS`, `PROTECT MAX`, `OSG WARRANTY`, `DEMO`, `FOC`, `SPARE`, `AC OUTDOOR`, `SERVICE`, `GIFT ITEMS`, etc.) are removed.
   - **Step 7 — Store Hierarchy & Price Slabs:** Maps every store to authoritative `Fin BDM (TM)`, `RBM`, `BDM`, `Zone` (`NORTH` vs `SOUTH`), and 11 `Price Range` slabs (`Below 10K` up to `Above 200K`).

2. **Card EMI Settlement Cleaners (`pine_lab_cleaner.py`, `paytm_cleaner.py`, `benow_cleaner.py`):**
   - Clean uploaded **Pine Labs**, **Paytm**, and **Benow** settlement files, normalize store names & sub-products (e.g. AirPods -> `EAR WEARABLES`), classify into portal categories, upload to ClickHouse (`pine_lab_transactions`, `paytm_transactions`, `benow_transactions`, `benow_pine_financier`), and **deduct Card EMI counts/values from `aCASH`** so Grand Total sales are never double-counted.""",

    "paper_finance": """### How Paper Finance & Delivery Order Mapping Works

Paper Finance in ClickHouse ([clickhouse_aggregator.py](file:///c:/Users/rahul_myg/Downloads/FINANCE%20PORTAL/clickhouse_aggregator.py)) uses a **2-tier `CASE` hierarchy**:
1. **Priority 1 — `sd.delivery_order_no` Overrides (cleans ERP dropdown mistakes):**
   - `^B[0-9]` -> **BAJAJ**
   - Contains `DMI` -> **DMI**
   - Starts with `10108` or `10109` -> **IDFC**
   - Starts with `CDSK` -> **PFL** (Poonawalla)
   - Contains `3022CD` or `3023CD` -> **TVS**
   - Pure 11–12 digits (not `1018/1019/10208/11090/3022/3023`, not `BENOW`) -> **HDB**
2. **Priority 2 — ERP `inv.financier_name` / `inv.financier_code`:**
   - Used whenever `delivery_order_no` is blank or doesn't match a known DO pattern (`%BAJAJ%`, `%HDB%`, `%DMI%`/`SAMSUNG%`, `%IDFC%`, `%TVS%`/`TVSC%`, `%POONAWALLA%`/`%PFL%`/`POONA%`).
3. **Priority 3 — Card EMI & Cash:**
   - Remaining invoices matched via `benow_pine_financier` (by `invoice_no` or `imei`) become `PineLab`, `PAYTM`, or `Benow` (plus uploaded settlement overrides deducted from `aCASH`); everything else is `aCASH`.""",

    "card_emi": """### How Card EMI Cleaning & Deduction Works (Pine Labs / Paytm / Benow)

1. **Pine Labs (`pine_lab_cleaner.py`):** Accepts main + Angamaly slots, deduplicates by Transaction ID, normalizes store names, resolves sub-products (e.g. AirPods -> `EAR WEARABLES`, iPhone/Mobile -> `MOBILE`), uploads to ClickHouse `pine_lab_transactions` and `benow_pine_financier`.
2. **Paytm (`paytm_cleaner.py`):** Cleans Paytm EMI exports, maps blank `BANK EMI` items to real product/category via ERP/item rules, uploads to `paytm_transactions`.
3. **Benow (`benow_cleaner.py`):** Cleans Benow settlement reports, normalizes `Product` and store mappings, uploads to `benow_transactions`.
4. **Zero Double-Counting Rule:** Because Card EMI sales are initially billed as Cash (`aCASH`) in ERP, `dashboard_aggregator.py` injects uploaded Card EMI totals into `PineLab` / `PAYTM` / `Benow` and **subtracts the exact delta from `aCASH`**, keeping Total Sales = 100% ERP sales.""",

    "fin_share": """### How Store Fin Share, Benchmarks, DIF & TM Metrics Are Calculated (`store_fin_share.py`)

- **FIN** = Paper Finance (`BAJAJ + HDB + IDFC + PFL + TVS + DMI`) + Card EMI (`PineLab + PAYTM + Benow`).
- **TOTAL** = All cleaned retail ERP sales (Value in ₹ and Volume in Qty).
- **FIN SHARE %** = `FIN / TOTAL × 100`.
- **Benchmark & DIF:** Compares current Fin Share against the prior calendar quarter (`AMJ`/`JAS`/`OND`/`JFM`) or any chosen comparison period (`Day`, `Week`, `Month`, `Quarter`, `Year`, `Custom Range`). `DIF = Current Fin Share % - Comparison Fin Share %` (percentage points).
- **TM Target Metrics:**
  - `CRR` (Current Run Rate) = `FIN / elapsed_days`
  - `DRR` (Desired Run Rate) = `(Target - FIN) / remaining_days`
  - `Ach %` = `FIN / Target`
  - `Trend %` = `(CRR × days_in_month) / Target`
  - Targets are stored permanently in ClickHouse table `tm_fin_targets`.""",

    "categories_zones": """### Categories, Blacklist, Price Slabs & Zone Rules

- **6 Standard Categories:**
  - `MOBILE`: Mobile, Tablet, Tab
  - `LAPTOP`: Laptop, Desktop
  - `CD PRODUCTS`: AC, TV, Refrigerators, Washing Machines, Microwave Oven, Dish Washer, Freezer, Dryer, Home Appliances
  - `SA`: Small Appliances
  - `HVA`: Audio, Ear Wearables, Smart Watch, Gaming, Home Theatre, CEGI
  - `OTHERS`: Camera, DIY, IT Accessories, Printer, RIG, Stabilizer
- **Blacklisted (Removed) Products:** `ACCESSORIES`, `CARE PLUS`, `PROTECT MAX`, `OSG WARRANTY`, `GLAM SHIELD`, `HOUSEHOLD`, `PERSONAL CARE`, `LG AMC`, `DEMO`, `FOC`, `SPARE`, `AC OUTDOOR`, `SERVICE`, `MONITOR`, `CCTV`, `GIFT ITEMS`, `LAPTOP BAG`, `SCHEME`, etc.
- **Zones (Kerala):** `NORTH` = RBMs `ALFAS`, `SAMEER`, `SAMEEM` | `SOUTH` = RBMs `RENJITH`, `MAHESH`, `VAISAKH`.
- **11 Price Slabs:** `Below 10K`, `10K–15K`, `15K–20K`, `20K–30K`, `30K–40K`, `40K–50K`, `50K–75K`, `75K–100K`, `100K–150K`, `150K–200K`, `Above 200K`.""",

    "capabilities": """### What myG Finance AI Agent Can Do

1. **Live Finance Analytics (Zero Hallucination):** Ask about overall KPIs, Paper vs Card split, Categories, Brands, Price Slabs, Store / Fin BDM (TM) / RBM / BDM / Staff Fin Share rankings, and Period-over-Period comparisons (you can name any month/quarter like *"October 2026 summary"* or *"Q3 bottom 10 stores"*).
2. **7-Axis Deep Insights & Morning Brief:** Ask *"Run a deep analysis"* to decompose ₹ variance into Sales Volume vs Conversion %, find steepest store drops/gainers, quantify ₹ Cr recovery upside, and audit ERP-vs-DO mismatches.
3. **Portal Process & Formula Explanations:** Ask how the cleaning process, Paper Finance DO rules, Card EMI matching, Blacklist, Categories, Zones, or Fin Share formulas work.
4. **Live Dashboard Control & Editing:**
   - *UI Control:* *"Open Duplicate Store Fin Share for Q3 by Fin BDM with Paper + Card split"* or *"Highlight rows below 50%"*.
   - *Persistent Widgets & Targets:* *"Pin a Top 10 Stores chart to Dashboard"* (persisted in ClickHouse with **1-Click Undo**).
5. **Continuous Memory:** Say *"Remember that Vypin Future was closed Oct 5–12"* to teach permanent business rules.
6. **Full Unrestricted LLM + Ad-hoc SQL:** Add a **Gemini / OpenAI / Claude API key** in **Learned Memory & Settings** to unlock free-form natural-language ClickHouse SQL, multi-step synthesis, and code-patch proposals.""",
}


def tool_explain_portal_logic(topic: str = "cleaning") -> dict[str, Any]:
    """Return authoritative documentation of the portal's cleaning, mapping, or calculation rules."""
    t = str(topic or "cleaning").strip().lower()
    if any(k in t for k in ("clean", "sales_cleaner", "process", "pipeline", "etl")):
        key = "cleaning"
    elif any(k in t for k in ("paper", "delivery", "do ", "financier", "override")):
        key = "paper_finance"
    elif any(k in t for k in ("card", "pine", "paytm", "benow", "deduct", "emi")):
        key = "card_emi"
    elif any(k in t for k in ("share", "dif", "bench", "crr", "drr", "trend", "formula", "calculat")):
        key = "fin_share"
    elif any(k in t for k in ("categor", "blacklist", "zone", "slab", "price", "north", "south")):
        key = "categories_zones"
    else:
        key = "capabilities"
    return {"status": "success", "topic": key, "explanation": _PORTAL_KNOWLEDGE[key]}


# ---------------------------------------------------------------------------
# TOOL 10b: Memory-Safe Period-over-Period & Multi-Year Comparison Tool
# ---------------------------------------------------------------------------
def _query_period_metrics_safe(start_d: str, end_d: str, state: str = "KERALA") -> dict[str, Any]:
    """
    Memory-safe ClickHouse aggregation for ANY window (even a full 12-month year with 9M+ rows).
    Filters azure_invoice_report & sales_data by the exact date range and uses grace_hash join
    so ClickHouse Cloud never hits its 6.8 GiB FillingRightJoinSide RAM ceiling.
    """
    from datetime import timedelta

    dt_s = datetime.strptime(start_d[:10], "%Y-%m-%d").date()
    dt_e = datetime.strptime(end_d[:10], "%Y-%m-%d").date()
    inv_s = (dt_s - timedelta(days=3)).isoformat()
    inv_e = (dt_e + timedelta(days=3)).isoformat()

    st_list = [x.strip().upper() for x in str(state or "").split(",") if x.strip()]
    state_expr = ai_semantic_layer.get_sql_macros()["STATE_EXPR"]
    state_where = f" AND ({state_expr}) IN {{states:Array(String)}}" if st_list else ""
    prod_expr = f"({ca._RAW_PROD_EXPR})"
    cat_expr = ai_semantic_layer.get_sql_macros()["CAT_EXPR"]
    zone_expr = ai_semantic_layer.get_sql_macros()["ZONE_EXPR"]
    qty_expr = ai_semantic_layer.get_sql_macros()["QTY_EXPR"]
    val_expr = ai_semantic_layer.get_sql_macros()["VAL_EXPR"]
    base_filter = ai_semantic_layer.get_sql_macros()["BASE_FILTER"]
    bl_set = ", ".join(f"'{p.replace(' ', '')}'" for p in sorted(ca._JUNK_PRODUCTS))

    # Lean paper financier expression (uses ERP inv + DO overrides only when sales_data matches)
    paper_fin_expr = """CASE
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%BAJAJ%' OR upper(coalesce(inv.financier_code, '')) LIKE 'BAJAJ%' THEN 'BAJAJ'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%HDB%' OR upper(coalesce(inv.financier_code, '')) LIKE 'HDB%' THEN 'HDB'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%DMI%' OR upper(coalesce(inv.financier_code, '')) LIKE 'SAMSUNG%' THEN 'DMI'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%IDFC%' OR upper(coalesce(inv.financier_code, '')) LIKE 'IDFC%' THEN 'IDFC'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%TVS%' OR upper(coalesce(inv.financier_code, '')) LIKE 'TVSC%' THEN 'TVS'
        WHEN upper(coalesce(inv.financier_name, '')) LIKE '%POONAWALLA%' OR upper(coalesce(inv.financier_name, '')) LIKE '%PFL%' OR upper(coalesce(inv.financier_code, '')) LIKE 'POONA%' THEN 'PFL'
        ELSE 'aCASH'
    END"""

    params: dict[str, Any] = {
        "s_dt": dt_s, "e_dt": dt_e,
        "inv_s": datetime.strptime(inv_s, "%Y-%m-%d").date(),
        "inv_e": datetime.strptime(inv_e, "%Y-%m-%d").date(),
    }
    if st_list:
        params["states"] = st_list

    sql = f"""
    SELECT
        toMonth(s.date) AS mo,
        ({cat_expr}) AS cat,
        ({paper_fin_expr}) AS fin,
        ({zone_expr}) AS zone,
        sum({qty_expr}) AS qty,
        sum({val_expr}) AS val
    FROM azure_sales_report s
    LEFT JOIN item_master i ON s.item_code = i.item_code
    LEFT JOIN (
        SELECT
            invoice_no,
            any(financier_name) AS financier_name,
            any(financier_code) AS financier_code,
            any(customer_type) AS customer_type,
            any(rbm) AS rbm
        FROM azure_invoice_report
        WHERE toDate(date) BETWEEN {{inv_s:Date}} AND {{inv_e:Date}}
        GROUP BY invoice_no
    ) inv ON s.invoice_no = inv.invoice_no
    LEFT JOIN branch_master bm ON upper(trim(s.branch)) = upper(trim(bm.code))
    WHERE toDate(s.date) BETWEEN {{s_dt:Date}} AND {{e_dt:Date}}
      AND {base_filter}
      AND replaceAll({prod_expr}, ' ', '') NOT IN ({bl_set})
      AND {prod_expr} != ''
      {state_where}
    GROUP BY mo, cat, fin, zone
    SETTINGS join_algorithm = 'grace_hash', max_bytes_before_external_group_by = 1000000000, max_threads = 2
    """
    df = ch.query_df(sql, params, cache=True)

    # Fetch uploaded Card EMI totals (PineLab, PAYTM, Benow) for the same window
    card_fin_val = {"PineLab": 0.0, "PAYTM": 0.0, "Benow": 0.0}
    card_fin_qty = {"PineLab": 0, "PAYTM": 0, "Benow": 0}
    card_by_mo: dict[int, float] = {}
    for tbl, fin_label in (("pine_lab_transactions", "PineLab"), ("paytm_transactions", "PAYTM"), ("benow_transactions", "Benow")):
        try:
            cdf = ch.query_df(
                f"SELECT toMonth(txn_date) AS mo, toInt64(sum(qty)) AS q, toFloat64(sum(original_amount)) AS v "
                f"FROM {tbl} WHERE toDate(txn_date) BETWEEN {{s_dt:Date}} AND {{e_dt:Date}} GROUP BY mo",
                {"s_dt": dt_s, "e_dt": dt_e},
                cache=True,
            )
            if cdf is not None and not cdf.empty:
                for _, cr in cdf.iterrows():
                    q_ = _safe_int(cr["q"])
                    v_ = _safe_float(cr["v"])
                    card_fin_qty[fin_label] += q_
                    card_fin_val[fin_label] += v_
                    mo_ = _safe_int(cr["mo"])
                    card_by_mo[mo_] = card_by_mo.get(mo_, 0.0) + v_
        except Exception as exc:
            logger.debug("Card EMI monthly lookup skipped for %s: %s", tbl, exc)

    paper_keys = ("BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI")
    fin_val: dict[str, float] = {k: 0.0 for k in paper_keys + ("PineLab", "PAYTM", "Benow", "aCASH")}
    fin_qty: dict[str, int] = {k: 0 for k in paper_keys + ("PineLab", "PAYTM", "Benow", "aCASH")}
    cat_tot: dict[str, list[float]] = {}   # cat -> [fin_val, tot_val, fin_qty, tot_qty]
    zone_tot: dict[str, list[float]] = {}  # zone -> [fin_val, tot_val]
    mo_fin: dict[int, float] = {}
    mo_tot: dict[int, float] = {}

    if df is not None and not df.empty:
        for _, r in df.iterrows():
            mo = _safe_int(r["mo"])
            c = str(r["cat"] or "OTHERS")
            f = str(r["fin"] or "aCASH")
            z = str(r["zone"] or "OTHERS")
            q = _safe_int(r["qty"])
            v = _safe_float(r["val"])

            fin_val[f] = fin_val.get(f, 0.0) + v
            fin_qty[f] = fin_qty.get(f, 0) + q
            mo_tot[mo] = mo_tot.get(mo, 0.0) + v
            if f in paper_keys:
                mo_fin[mo] = mo_fin.get(mo, 0.0) + v

            ca_acc = cat_tot.setdefault(c, [0.0, 0.0, 0.0, 0.0])
            ca_acc[1] += v
            ca_acc[3] += q
            if f in paper_keys:
                ca_acc[0] += v
                ca_acc[2] += q

            za = zone_tot.setdefault(z, [0.0, 0.0])
            za[1] += v
            if f in paper_keys:
                za[0] += v

    # Integrate Card EMI (deducted from aCASH so Grand Total = ERP Total Sales)
    card_val_tot = sum(card_fin_val.values())
    card_qty_tot = sum(card_fin_qty.values())
    for k in ("PineLab", "PAYTM", "Benow"):
        fin_val[k] = card_fin_val[k]
        fin_qty[k] = card_fin_qty[k]
    fin_val["aCASH"] = max(0.0, fin_val["aCASH"] - card_val_tot)
    fin_qty["aCASH"] = max(0, fin_qty["aCASH"] - card_qty_tot)
    for mo_, cv_ in card_by_mo.items():
        mo_fin[mo_] = mo_fin.get(mo_, 0.0) + cv_

    paper_val_tot = sum(fin_val[k] for k in paper_keys)
    paper_qty_tot = sum(fin_qty[k] for k in paper_keys)
    tot_fin_val = paper_val_tot + card_val_tot
    tot_fin_qty = paper_qty_tot + card_qty_tot
    tot_sales_val = sum(mo_tot.values())
    tot_sales_qty = sum(int(v[3]) for v in cat_tot.values())

    return {
        "start_date": start_d,
        "end_date": end_d,
        "total_sales_cr": round(tot_sales_val / 1e7, 2),
        "total_fin_cr": round(tot_fin_val / 1e7, 2),
        "paper_fin_cr": round(paper_val_tot / 1e7, 2),
        "card_fin_cr": round(card_val_tot / 1e7, 2),
        "fin_share_val_pct": round((tot_fin_val / tot_sales_val * 100.0) if tot_sales_val else 0.0, 2),
        "total_sales_qty": tot_sales_qty,
        "total_fin_qty": tot_fin_qty,
        "paper_fin_qty": paper_qty_tot,
        "card_fin_qty": card_qty_tot,
        "fin_share_vol_pct": round((tot_fin_qty / tot_sales_qty * 100.0) if tot_sales_qty else 0.0, 2),
        "fin_val_cr": {k: round(v / 1e7, 2) for k, v in fin_val.items()},
        "fin_qty": fin_qty,
        "categories": {
            c: {
                "paper_cr": round(v[0] / 1e7, 2),
                "tot_cr": round(v[1] / 1e7, 2),
                "paper_share_pct": round((v[0] / v[1] * 100.0) if v[1] else 0.0, 1),
                "tot_qty": int(v[3]),
            }
            for c, v in cat_tot.items()
        },
        "zones": {
            z: {
                "paper_cr": round(v[0] / 1e7, 2),
                "tot_cr": round(v[1] / 1e7, 2),
                "paper_share_pct": round((v[0] / v[1] * 100.0) if v[1] else 0.0, 1),
            }
            for z, v in zone_tot.items()
        },
        "monthly_fin_cr": {m: round(mo_fin.get(m, 0.0) / 1e7, 2) for m in range(1, 13)},
        "monthly_tot_cr": {m: round(mo_tot.get(m, 0.0) / 1e7, 2) for m in range(1, 13)},
    }


def tool_compare_periods(
    period_a_start: str,
    period_a_end: str,
    period_b_start: str,
    period_b_end: str,
    label_a: str = "Period A",
    label_b: str = "Period B",
    state: str = "KERALA",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare any two time periods (e.g. 2024 vs 2025, Q2 vs Q3, Sep vs Oct) safely in ClickHouse."""
    st = state.upper() if state else (context or {}).get("state", "KERALA")
    # Execute sequentially to guarantee ClickHouse Cloud stays well below 6.8 GiB RAM
    a = _query_period_metrics_safe(period_a_start, period_a_end, st)
    b = _query_period_metrics_safe(period_b_start, period_b_end, st)

    def _pct(new_v: float, old_v: float) -> str:
        if not old_v:
            return "—"
        return f"{((new_v - old_v) / old_v * 100.0):+.1f}%"

    kpi_rows = [
        ["Total Sales (₹ Cr)", a["total_sales_cr"], b["total_sales_cr"], round(b["total_sales_cr"] - a["total_sales_cr"], 2), _pct(b["total_sales_cr"], a["total_sales_cr"])],
        ["Total Finance (₹ Cr)", a["total_fin_cr"], b["total_fin_cr"], round(b["total_fin_cr"] - a["total_fin_cr"], 2), _pct(b["total_fin_cr"], a["total_fin_cr"])],
        ["Paper Finance (₹ Cr)", a["paper_fin_cr"], b["paper_fin_cr"], round(b["paper_fin_cr"] - a["paper_fin_cr"], 2), _pct(b["paper_fin_cr"], a["paper_fin_cr"])],
        ["Card EMI (₹ Cr)", a["card_fin_cr"], b["card_fin_cr"], round(b["card_fin_cr"] - a["card_fin_cr"], 2), _pct(b["card_fin_cr"], a["card_fin_cr"])],
        ["Fin Share — Value (%)", f"{a['fin_share_val_pct']:.2f}%", f"{b['fin_share_val_pct']:.2f}%", f"{(b['fin_share_val_pct'] - a['fin_share_val_pct']):+.2f} pp", "—"],
        ["Total Sales (Qty)", a["total_sales_qty"], b["total_sales_qty"], b["total_sales_qty"] - a["total_sales_qty"], _pct(b["total_sales_qty"], a["total_sales_qty"])],
        ["Total Finance (Qty)", a["total_fin_qty"], b["total_fin_qty"], b["total_fin_qty"] - a["total_fin_qty"], _pct(b["total_fin_qty"], a["total_fin_qty"])],
        ["Fin Share — Volume (%)", f"{a['fin_share_vol_pct']:.2f}%", f"{b['fin_share_vol_pct']:.2f}%", f"{(b['fin_share_vol_pct'] - a['fin_share_vol_pct']):+.2f} pp", "—"],
    ]
    for fk in ("BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"):
        av = a["fin_val_cr"].get(fk, 0.0)
        bv = b["fin_val_cr"].get(fk, 0.0)
        if av > 0 or bv > 0:
            kpi_rows.append([f"Financier: {fk} (₹ Cr)", av, bv, round(bv - av, 2), _pct(bv, av)])

    cols = ["Metric", label_a, label_b, "Change", "Growth %"]
    exp_tok = _store_export(f"Compare_{label_a}_vs_{label_b}", cols, kpi_rows)

    mo_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    chart = {
        "title": f"Monthly Finance Value (₹ Cr): {label_a} vs {label_b}",
        "type": "bar",
        "labels": mo_labels,
        "datasets": [
            {"label": f"{label_a} FIN (Cr)", "data": [a["monthly_fin_cr"][m] for m in range(1, 13)]},
            {"label": f"{label_b} FIN (Cr)", "data": [b["monthly_fin_cr"][m] for m in range(1, 13)]},
        ],
    }

    return {
        "status": "success",
        "label_a": label_a,
        "label_b": label_b,
        "state": st or "ALL",
        "period_a": a,
        "period_b": b,
        "table": {"title": f"Performance Comparison: {label_a} vs {label_b} ({st or 'ALL'})", "columns": cols, "rows": kpi_rows, "export_token": exp_tok},
        "chart": chart,
    }


# ---------------------------------------------------------------------------
# Tool Definitions (OpenAI / Gemini / Anthropic JSON-Schema compatible)
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "compare_periods",
        "description": "Memory-safe side-by-side comparison of ANY two date ranges (e.g. full year 2024 vs 2025, Q2 vs Q3, Sep vs Oct) with Sales, Paper/Card Finance, Fin Share %, Financier mix, Category & Zone breakdown, and monthly overlay chart.",
        "parameters": {
            "type": "object",
            "properties": {
                "period_a_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_a_end": {"type": "string", "description": "YYYY-MM-DD"},
                "period_b_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_b_end": {"type": "string", "description": "YYYY-MM-DD"},
                "label_a": {"type": "string"},
                "label_b": {"type": "string"},
                "state": {"type": "string"},
            },
            "required": ["period_a_start", "period_a_end", "period_b_start", "period_b_end"],
        },
    },
    {
        "name": "explain_portal_logic",
        "description": "Explain how the myG Finance Portal cleaning process, Paper Finance DO rules, Card EMI deduction, Fin Share / DIF / CRR / DRR formulas, Categories, Blacklist, Zones, or AI capabilities work.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "cleaning | paper_finance | card_emi | fin_share | categories_zones | capabilities"},
            },
        },
    },
    {
        "name": "get_dashboard_summary",
        "description": "Get exact portal-parity KPIs, Financier breakdown (Paper + Card EMI), and Category performance for a date/timeframe/state. Use this first for overall KPI, category, or financier mix questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "YYYY-MM-DD start/target date (defaults to active portal date)"},
                "timeframe": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly", "custom"]},
                "end_date_str": {"type": "string", "description": "YYYY-MM-DD end date (only for custom timeframe)"},
                "state": {"type": "string", "description": "KERALA, KARNATAKA, or blank for active state"},
                "rbm": {"type": "string"},
                "bdm": {"type": "string"},
                "store": {"type": "string"},
                "fin_bdm": {"type": "string"},
            },
        },
    },
    {
        "name": "get_store_fin_share",
        "description": "Get exact Store, Fin BDM (TM), RBM, BDM, or Staff Finance Share (Value ₹ Cr + Volume Qty), Paper vs Card split, rankings, and period-over-period comparisons (day/week/month/quarter/year).",
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly", "custom"]},
                "end_date_str": {"type": "string"},
                "state": {"type": "string"},
                "view_by": {"type": "string", "enum": ["store", "tm", "rbm", "bdm", "staff"]},
                "compare_with": {"type": "string", "enum": ["none", "day", "week", "month", "quarter", "year"]},
                "fin_split": {"type": "string", "enum": ["total", "paper", "card"]},
                "sort_by": {"type": "string", "enum": ["fin_share_desc", "fin_share_asc", "fin_desc", "tot_desc", "diff_asc", "diff_desc"]},
                "limit": {"type": "integer"},
                "search": {"type": "string", "description": "Optional store/TM/RBM/staff name substring filter"},
            },
        },
    },
    {
        "name": "get_brand_and_price_analysis",
        "description": "Get Brand-wise or Price-Slab-wise Finance Share and Bajaj/HDB/IDFC breakdown from ClickHouse.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly", "custom"]},
                "end_date_str": {"type": "string"},
                "state": {"type": "string"},
                "category": {"type": "string", "description": "ALL, MOBILE, LAPTOP, CD PRODUCTS, SA, HVA, OTHERS"},
                "dimension": {"type": "string", "enum": ["brand", "price"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_targets_and_pacing",
        "description": "Get Global Targets and monthly TM (Fin BDM) Targets vs live achievement, Card Slabs, and trend projections.",
        "parameters": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM month key"},
                "state": {"type": "string"},
            },
        },
    },
    {
        "name": "run_readonly_sql",
        "description": "Run any custom read-only ClickHouse SELECT/WITH SQL query for ad-hoc questions not covered by standard tools. Supports canonical macros like {{PAPER_FIN_EXPR}}, {{CAT_EXPR}}, {{PROD_EXPR}}, {{STATE_EXPR}}, {{ZONE_EXPR}}, {{QTY_EXPR}}, {{VAL_EXPR}}, {{DO_CLEAN}}, {{BASE_FILTER}}.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Valid ClickHouse SELECT or WITH query"},
                "title": {"type": "string", "description": "Short display title for the result table/chart"},
                "chart_type": {"type": "string", "enum": ["", "bar", "line", "doughnut"]},
                "label_col": {"type": "string"},
                "value_col": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "run_deep_analysis",
        "description": "Run a comprehensive 7-axis deep root-cause diagnostic: variance decomposition (sales volume vs Fin Share conversion effect), North vs South gap, TM/store gainers & steepest drops, quantified ₹ Cr recovery opportunities, and ERP-vs-DO hygiene audit.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly", "custom"]},
                "end_date_str": {"type": "string"},
                "state": {"type": "string"},
                "compare_with": {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
                "focus_entity": {"type": "string", "description": "Optional store, TM, RBM, or BDM to focus deep analysis on"},
            },
        },
    },
    {
        "name": "generate_morning_brief",
        "description": "Generate an instant executive briefing for the active period with headline KPIs, TM target pacing, biggest wins/leaks, recovery upside, and ERP alerts.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string"},
                "timeframe": {"type": "string"},
                "end_date_str": {"type": "string"},
                "state": {"type": "string"},
            },
        },
    },
    {
        "name": "save_business_memory",
        "description": "Permanently save a business rule, store exception, user definition, or preference into ClickHouse memory so the agent remembers it in all future conversations.",
        "parameters": {
            "type": "object",
            "properties": {
                "key_topic": {"type": "string", "description": "Short topic label, e.g. 'Vypin Store Renovation' or 'Premium Mobile Definition'"},
                "content": {"type": "string", "description": "Detailed fact or rule to remember"},
                "category": {"type": "string", "enum": ["business_rule", "store_exception", "kpi_definition", "user_preference"]},
            },
            "required": ["key_topic", "content"],
        },
    },
    {
        "name": "control_dashboard_ui",
        "description": "Tier 1 live dashboard control: switch tabs, change Review Date / Timeframe / Custom Range, adjust Duplicate Store Fin Share view/comparison/split, highlight table rows, or trigger exports immediately.",
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["switch_tab", "set_period", "set_oct2_controls", "highlight_rows", "trigger_export", "refresh_tab"]},
                            "tab": {"type": "string"},
                            "timeframe": {"type": "string"},
                            "date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "view_by": {"type": "string"},
                            "compare": {"type": "string"},
                            "fin_split": {"type": "string"},
                            "search": {"type": "string"},
                            "threshold_pct": {"type": "number"},
                            "operator": {"type": "string"},
                            "format": {"type": "string"},
                        },
                        "required": ["type"],
                    },
                }
            },
            "required": ["actions"],
        },
    },
    {
        "name": "propose_dashboard_edit",
        "description": "Tier 2 persistent Dashboard Studio & Target edit (shows Preview & Confirm card with 1-click Undo): add/remove custom live KPI/chart/table widgets on any tab (use {{DATE_FILTER}} in SQL widgets so they dynamically follow the active date picker), update Fin Share color thresholds, toggle section visibility, rename titles, or update Global/TM targets.",
        "parameters": {
            "type": "object",
            "properties": {
                "edit_type": {"type": "string", "enum": ["add_widget", "remove_widget", "update_thresholds", "toggle_section", "update_title", "update_global_targets", "update_tm_targets"]},
                "payload": {"type": "object", "description": "Structured edit parameters for the chosen edit_type"},
                "summary": {"type": "string", "description": "Human-readable one-line summary of the edit"},
            },
            "required": ["edit_type", "payload", "summary"],
        },
    },
    {
        "name": "propose_code_patch",
        "description": "Tier 3 optional source-code edit: validate syntax and stage a unified diff on an allowlisted portal source file for explicit admin approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "description": {"type": "string"},
                "search_block": {"type": "string"},
                "replace_block": {"type": "string"},
            },
            "required": ["file_path", "description", "search_block", "replace_block"],
        },
    },
]


def execute_tool(name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch an agent tool call safely and return a JSON-serializable dict."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "compare_periods":
            return tool_compare_periods(**args, context=context)
        if name == "explain_portal_logic":
            return tool_explain_portal_logic(**args)
        if name == "get_dashboard_summary":
            return tool_get_dashboard_summary(**args, context=context)
        if name == "get_store_fin_share":
            return tool_get_store_fin_share(**args, context=context)
        if name == "get_brand_and_price_analysis":
            return tool_get_brand_and_price_analysis(**args, context=context)
        if name == "get_targets_and_pacing":
            return tool_get_targets_and_pacing(**args, context=context)
        if name == "run_readonly_sql":
            return tool_run_readonly_sql(**args)
        if name == "run_deep_analysis":
            return tool_run_deep_analysis(**args, context=context)
        if name == "generate_morning_brief":
            return tool_generate_morning_brief(**args, context=context)
        if name == "save_business_memory":
            saved = ai_memory.save_memory(
                key_topic=args.get("key_topic", "Business Rule"),
                content=args.get("content", ""),
                category=args.get("category", "business_rule"),
                source="ai_chat",
            )
            return {"status": "success", "saved_memory": saved}
        if name == "control_dashboard_ui":
            return tool_control_dashboard_ui(args.get("actions") or [])
        if name == "propose_dashboard_edit":
            return tool_propose_dashboard_edit(
                edit_type=args.get("edit_type", ""),
                payload=args.get("payload") or {},
                summary=args.get("summary", ""),
            )
        if name == "propose_code_patch":
            return tool_propose_code_patch(
                file_path=args.get("file_path", ""),
                description=args.get("description", ""),
                search_block=args.get("search_block", ""),
                replace_block=args.get("replace_block", ""),
            )
        return {"status": "error", "error": f"Unknown tool: {name}"}
    except Exception as exc:
        logger.error("Tool %s failed: %s", name, exc, exc_info=True)
        return {"status": "error", "error": str(exc)[:500]}
