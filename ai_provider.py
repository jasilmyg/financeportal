"""
ai_provider.py
--------------
Provider-agnostic multi-step AI Agent Orchestrator for the myG Finance Portal.

Supports natively via Python stdlib HTTPS (zero extra pip dependencies):
  - Google Gemini (gemini-2.5-flash / gemini-2.5-pro)
  - OpenAI (gpt-4.1-mini / gpt-4.1)
  - Anthropic Claude (claude-sonnet-4-20250514)
  - OpenRouter / any OpenAI-compatible endpoint

Also includes a fast **Built-in Smart Analytics Engine** that executes live
ClickHouse diagnostics, Store/TM/Brand/Category lookups, live UI control,
memory saving, and custom widget creation even before an LLM API key is added.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from typing import Any, Generator

import ai_memory
import ai_semantic_layer
import ai_tools

logger = logging.getLogger(__name__)

_DEEP_KEYWORDS = re.compile(
    r"\b(deep|root\s*cause|why\s+did|diagnos|anomal|audit|variance|insight|brief|forecast|"
    r"explain\s+drop|strategy|recover|recommend|compare\s+all|sql|code|patch|widget|edit)\b",
    re.IGNORECASE,
)


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"LLM HTTP {e.code}: {err_body}") from e
    except Exception as e:
        raise RuntimeError(f"LLM request failed: {e}") from e


def _compact_tool_result_for_llm(result: dict[str, Any]) -> str:
    """Keep LLM context lean while preserving all exact numbers and schema."""
    slim = dict(result)
    # Trim oversized raw table rows sent back to LLM (UI still gets the full table separately!)
    if "table" in slim and isinstance(slim["table"], dict):
        t = dict(slim["table"])
        if isinstance(t.get("rows"), list) and len(t["rows"]) > 25:
            t["rows"] = t["rows"][:25]
            t["truncated_for_llm"] = True
        slim["table"] = t
    if "rows" in slim and isinstance(slim["rows"], list) and len(slim["rows"]) > 25:
        slim["rows"] = slim["rows"][:25]
    raw = json.dumps(slim, ensure_ascii=False, default=str)
    if len(raw) > 22000:
        raw = raw[:22000] + '..."truncated"}'
    return raw


# ---------------------------------------------------------------------------
# Provider-Specific Single-Turn Callers (normalized output)
# Normalized return: {"text": str, "tool_calls": [{"id", "name", "args"}], "raw_msg": Any}
# ---------------------------------------------------------------------------
def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON schema to Gemini FunctionDeclaration schema."""
    if not isinstance(schema, dict):
        return {}
    out: dict[str, Any] = {}
    t = str(schema.get("type", "object")).upper()
    out["type"] = t
    if "description" in schema:
        out["description"] = schema["description"]
    if "enum" in schema:
        out["enum"] = [str(x) for x in schema["enum"] if x != ""]
    if "properties" in schema and isinstance(schema["properties"], dict):
        out["properties"] = {k: _to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = _to_gemini_schema(schema["items"])
    if "required" in schema:
        out["required"] = list(schema["required"])
    return out


def _call_gemini(
    api_key: str,
    model: str,
    system_prompt: str,
    contents: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    decls = [
        {
            "name": t["name"],
            "description": t["description"],
            "parameters": _to_gemini_schema(t.get("parameters") or {"type": "object", "properties": {}}),
        }
        for t in tools
    ]
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"functionDeclarations": decls}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    resp = _http_post_json(url, payload, {"Content-Type": "application/json"})
    cands = resp.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini returned no candidates: {json.dumps(resp)[:300]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    texts = []
    tool_calls = []
    for idx, p in enumerate(parts):
        if "text" in p and p["text"]:
            texts.append(p["text"])
        if "functionCall" in p:
            fc = p["functionCall"]
            tool_calls.append({
                "id": f"call_{idx}_{fc.get('name', 'tool')}",
                "name": fc.get("name", ""),
                "args": fc.get("args") or {},
            })
    return {"text": "\n".join(texts).strip(), "tool_calls": tool_calls, "raw_parts": parts}


def _call_openai_compat(
    api_key: str,
    model: str,
    base_url: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    oai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": messages,
        "tools": oai_tools,
        "tool_choice": "auto",
        "temperature": 0.2,
    }
    resp = _http_post_json(url, payload, headers)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {json.dumps(resp)[:300]}")
    msg = choices[0].get("message") or {}
    tcs = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            parsed_args = {}
        tcs.append({"id": tc.get("id", "tc"), "name": fn.get("name", ""), "args": parsed_args})
    return {"text": (msg.get("content") or "").strip(), "tool_calls": tcs, "raw_msg": msg}


def _call_anthropic(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    url = "https://api.anthropic.com/v1/messages"
    ant_tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": messages,
        "tools": ant_tools,
        "temperature": 0.2,
    }
    resp = _http_post_json(url, payload, headers)
    blocks = resp.get("content") or []
    texts = []
    tcs = []
    for b in blocks:
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tcs.append({"id": b.get("id", "tu"), "name": b.get("name", ""), "args": b.get("input") or {}})
    return {"text": "\n".join(texts).strip(), "tool_calls": tcs, "raw_blocks": blocks}


def test_provider_connection() -> dict[str, Any]:
    """Quick connectivity test for the currently configured LLM provider."""
    cfg = ai_memory.get_effective_ai_config()
    if not cfg["has_key"]:
        return {"ok": False, "error": "No API key configured for the selected provider."}
    prov = cfg["provider"]
    model = cfg["fast_model"]
    try:
        if prov == "gemini":
            res = _call_gemini(cfg["api_key"], model, "Reply OK", [{"role": "user", "parts": [{"text": "Say OK"}]}], [])
            return {"ok": True, "provider": prov, "model": model, "reply": res["text"][:80]}
        elif prov in ("openai", "openrouter"):
            burl = cfg["base_url"] or ("https://openrouter.ai/api/v1" if prov == "openrouter" else "https://api.openai.com/v1")
            res = _call_openai_compat(cfg["api_key"], model, burl, [{"role": "user", "content": "Say OK"}], [])
            return {"ok": True, "provider": prov, "model": model, "reply": res["text"][:80]}
        elif prov == "anthropic":
            res = _call_anthropic(cfg["api_key"], model, "Reply OK", [{"role": "user", "content": "Say OK"}], [])
            return {"ok": True, "provider": prov, "model": model, "reply": res["text"][:80]}
        return {"ok": False, "error": f"Unknown provider: {prov}"}
    except Exception as exc:
        return {"ok": False, "provider": prov, "model": model, "error": str(exc)}


# ---------------------------------------------------------------------------
# Built-in Smart Analytics Fallback Engine (works with ZERO API key!)
# ---------------------------------------------------------------------------
_MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _extract_period_from_text(low: str, d0: str, tf0: str, e0: str) -> tuple[str, str, str]:
    """Detect explicit month/quarter/year/ISO dates in the user prompt so queries match what was asked."""
    yr_m = re.search(r"\b(202[3-9]|203\d)\b", low)
    yr = yr_m.group(1) if yr_m else (d0[:4] or "2026")

    iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", low)
    if len(iso_dates) >= 2:
        return iso_dates[0], "custom", iso_dates[1]
    if len(iso_dates) == 1:
        return iso_dates[0], "daily", ""

    q_m = re.search(r"\bq([1-4])\b", low)
    if q_m:
        q_start = {1: "01", 2: "04", 3: "07", 4: "10"}[int(q_m.group(1))]
        return f"{yr}-{q_start}-01", "quarterly", ""

    for m_name, m_num in _MONTH_MAP.items():
        if re.search(rf"\b{m_name}\b", low):
            return f"{yr}-{m_num:02d}-01", "monthly", ""

    if re.search(r"\b(full\s+year|yearly|ytd|annual)\b", low):
        return f"{yr}-01-01", "yearly", ""

    return d0, tf0, e0


def _run_builtin_agent(
    user_message: str,
    context: dict[str, Any],
) -> Generator[dict[str, Any], None, None]:
    msg = (user_message or "").strip()
    low = msg.lower()
    d0, tf0, e0, st0 = ai_tools._default_date(context)
    d0, tf0, e0 = _extract_period_from_text(low, d0, tf0, e0)

    if "karnataka" in low:
        st0 = "KARNATAKA"
    elif "all state" in low:
        st0 = ""
    elif "kerala" in low:
        st0 = "KERALA"

    # 0. Portal Process / Cleaning / Mapping / Formula / Help questions
    is_howto = bool(re.search(
        r"\b(how\s+(is|are|does|do|to)|explain|what\s+is|what\s+are|what\s+can\s+you|"
        r"clean(?:ing|er|ed)?|blacklist|formula|calculat|mapping|mapped|rule|workflow|"
        r"delivery\s*order|do\s+override|deduct|capabilities|help)\b",
        low,
    ))
    has_data_metric_ask = bool(re.search(
        r"\b(show|list|top|bottom|compare|rank|how\s+much|total\s+sales|total\s+fin|drop|gainer|target|pacing)\b",
        low,
    ))
    if is_howto and not has_data_metric_ask:
        yield {"type": "tool_start", "tool": "explain_portal_logic", "label": "Reading portal architecture & cleaning rules..."}
        res = ai_tools.tool_explain_portal_logic(low)
        yield {"type": "tool_done", "tool": "explain_portal_logic", "summary": f"Topic: {res['topic']}"}
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": res["explanation"],
        }
        return

    # 1. Teach / Remember business memory
    m_rem = re.match(r"^(?:remember|note|learn|save\s+rule)\s*(?:that|:)?\s+(.+)$", msg, re.IGNORECASE)
    if m_rem:
        rule_text = m_rem.group(1).strip()
        topic = rule_text[:48] + ("..." if len(rule_text) > 48 else "")
        yield {"type": "tool_start", "tool": "save_business_memory", "label": "Saving to ClickHouse AI Memory..."}
        saved = ai_memory.save_memory(topic, rule_text, category="business_rule", source="chat")
        yield {"type": "tool_done", "tool": "save_business_memory", "summary": f"Saved rule: {saved['key_topic']}"}
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": f"Saved permanently to **myG AI Memory** (`{saved['id']}`):\n\n> **{saved['key_topic']}**\n> {saved['content']}\n\nI will apply this rule in all future insights and queries.",
        }
        return

    # 2. Live UI navigation & filter controls (Tier 1)
    tab_aliases = {
        "duplicate": "oct2-sheets",
        "store fin share": "oct-sheets",
        "fin share": "oct2-sheets",
        "exec summary": "exec-summary",
        "category": "category",
        "zone": "zone",
        "price": "price",
        "brand": "brand",
        "store performance": "store",
        "team": "team",
        "cleaner": "cleaner",
        "global target": "settings",
        "dashboard": "dashboard",
    }
    if re.search(r"\b(open|switch\s+to|go\s+to|navigate|show\s+tab|highlight)\b", low):
        actions = []
        for phrase, tid in tab_aliases.items():
            if phrase in low:
                actions.append({"type": "switch_tab", "tab": tid})
                break
        if "q1" in low:
            actions.append({"type": "set_period", "timeframe": "quarterly", "date": f"{d0[:4]}-01-01"})
        elif "q2" in low:
            actions.append({"type": "set_period", "timeframe": "quarterly", "date": f"{d0[:4]}-04-01"})
        elif "q3" in low:
            actions.append({"type": "set_period", "timeframe": "quarterly", "date": f"{d0[:4]}-07-01"})
        elif "q4" in low:
            actions.append({"type": "set_period", "timeframe": "quarterly", "date": f"{d0[:4]}-10-01"})
        elif "weekly" in low:
            actions.append({"type": "set_period", "timeframe": "weekly", "date": d0})
        elif "yearly" in low:
            actions.append({"type": "set_period", "timeframe": "yearly", "date": f"{d0[:4]}-01-01"})
        elif "monthly" in low:
            actions.append({"type": "set_period", "timeframe": "monthly", "date": d0})

        oct2_ctrl: dict[str, Any] = {"type": "set_oct2_controls"}
        if "fin bdm" in low or "by tm" in low:
            oct2_ctrl["view_by"] = "tm"
        elif "by rbm" in low:
            oct2_ctrl["view_by"] = "rbm"
        elif "by bdm" in low:
            oct2_ctrl["view_by"] = "bdm"
        elif "by staff" in low:
            oct2_ctrl["view_by"] = "staff"
        if "paper + card" in low or "paper and card" in low:
            oct2_ctrl["fin_split"] = "both"
        elif "paper finance" in low:
            oct2_ctrl["fin_split"] = "paper"
        elif "card finance" in low:
            oct2_ctrl["fin_split"] = "card"
        if "compare" in low:
            for cm in ("day", "week", "month", "quarter", "year"):
                if cm in low:
                    oct2_ctrl["compare"] = cm
                    break
        if len(oct2_ctrl) > 1:
            actions.append(oct2_ctrl)

        m_hl = re.search(r"below\s+(\d+)\s*%?", low)
        if m_hl:
            actions.append({"type": "highlight_rows", "threshold_pct": float(m_hl.group(1)), "operator": "lt"})

        if actions:
            yield {"type": "tool_start", "tool": "control_dashboard_ui", "label": "Applying live UI controls..."}
            res = ai_tools.tool_control_dashboard_ui(actions)
            yield {"type": "ui_actions", "actions": res["ui_actions"]}
            yield {"type": "tool_done", "tool": "control_dashboard_ui", "summary": res["message"]}
            yield {
                "type": "answer",
                "mode": "builtin",
                "text": f"Done — applied **{len(actions)}** live dashboard action(s): " + ", ".join(a["type"] for a in actions) + ".",
            }
            return

    # 3. Tier 2 Quick Widget Creation ("add widget / pin chart / pin kpi")
    if re.search(r"\b(add|pin|create)\b.*\b(widget|kpi|card|chart|table)\b", low):
        tab = "dashboard"
        for phrase, tid in tab_aliases.items():
            if phrase in low:
                tab = tid
                break
        if "low" in low or "bottom" in low or "lagging" in low:
            src, wtype, title = "store_low", "table", "Bottom 10 Stores by Fin Share"
        elif "top" in low and "store" in low:
            src, wtype, title = "store_top", "chart", "Top 10 Stores by Fin Share"
        elif "tm" in low or "fin bdm" in low:
            src, wtype, title = "tm", "chart", "Fin BDM (TM) Fin Share %"
        elif "category" in low:
            src, wtype, title = "category", "chart", "Category Finance Share %"
        else:
            src, wtype, title = "financier", "chart", "Financier Value Mix (₹ Cr)"

        yield {"type": "tool_start", "tool": "propose_dashboard_edit", "label": "Preparing Dashboard Studio preview..."}
        res = ai_tools.tool_propose_dashboard_edit(
            "add_widget",
            {"tab": tab, "widget_type": wtype, "title": title, "metric_source": src, "chart_type": "bar"},
            summary=f"Pin '{title}' ({wtype}) onto {tab} tab",
        )
        if res.get("pending_action"):
            yield {"type": "pending_action", "action": res["pending_action"]}
        yield {"type": "tool_done", "tool": "propose_dashboard_edit", "summary": "Preview ready for confirmation"}
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": f"I've staged a live **{wtype.upper()} widget** (**{title}**) pinned to `{tab}`.\n\nReview the preview card below and click **Approve & Apply** — it will persist in ClickHouse across restarts and supports **1-Click Undo**.",
        }
        return

    # 4. Direct Read-Only SQL passthrough if user types SELECT / WITH
    if re.match(r"^(SELECT|WITH)\b", msg, re.IGNORECASE):
        yield {"type": "tool_start", "tool": "run_readonly_sql", "label": "Executing read-only ClickHouse SQL..."}
        res = ai_tools.tool_run_readonly_sql(msg, title="Custom SQL Result")
        if res.get("status") == "success":
            yield {"type": "table", "table": res["table"]}
            yield {"type": "tool_done", "tool": "run_readonly_sql", "summary": f"{res['row_count']} rows returned"}
            yield {"type": "answer", "mode": "builtin", "text": f"Query succeeded — **{res['row_count']}** rows returned (showing top {res['showing']}). You can download the full result as CSV/Excel below.", "sql": msg}
        else:
            yield {"type": "tool_done", "tool": "run_readonly_sql", "summary": "SQL error"}
            yield {"type": "answer", "mode": "builtin", "text": f"Query could not execute: `{res.get('error')}`"}
        return

    # 5. Deep Analysis / Morning Brief / Root Cause
    if re.search(r"\b(deep|insight|brief|why|drop|anomal|audit|recover|root\s*cause|health|diagnos|mismatch)\b", low):
        yield {"type": "tool_start", "tool": "run_deep_analysis", "label": f"Running 7-axis ClickHouse deep diagnostic ({d0} · {tf0})..."}
        res = ai_tools.tool_generate_morning_brief(d0, tf0, e0, st0, context=context)
        if res.get("status") != "success":
            yield {"type": "answer", "mode": "builtin", "text": f"Could not complete deep analysis for `{d0}` ({tf0}): {res.get('error', 'No data')}"}
            return
        if res.get("chart"):
            yield {"type": "chart", "chart": res["chart"]}
        if res.get("table"):
            yield {"type": "table", "table": res["table"]}
        yield {"type": "tool_done", "tool": "run_deep_analysis", "summary": "7-axis diagnostic completed"}

        vb = res["variance_bridge"]
        audit = res["erp_hygiene_audit"]
        alerts = res.get("alerts") or []
        drops = res.get("biggest_store_drops") or []
        gains = res.get("biggest_store_gains") or []

        lines = [
            f"### Deep Finance Intelligence — {res['period'].get('start_date')} to {res['period'].get('end_date')} ({st0 or 'ALL STATES'})",
            "",
            f"- **Total Finance:** **₹{vb['cur_fin_cr']:.2f} Cr** out of **₹{vb['cur_tot_cr']:.2f} Cr** total sales (**{vb['cur_fin_share_pct']:.2f}% Fin Share** — Paper ₹{vb['cur_paper_cr']:.2f} Cr + Card ₹{vb['cur_card_cr']:.2f} Cr).",
            f"- **Variance vs {res.get('compare_label')}:** **{vb['delta_fin_cr']:+.2f} Cr** (`{vb['delta_share_pp']:+.2f} pp` Fin Share). Decomposed into **{vb['sales_volume_effect_cr']:+.2f} Cr** sales-volume effect + **{vb['fin_share_conversion_effect_cr']:+.2f} Cr** pure Fin Share conversion effect.",
            f"- **Zone Split:** North **{vb['north_fin_share_pct']:.1f}%** vs South **{vb['south_fin_share_pct']:.1f}%**.",
            f"- **ERP Hygiene Audit:** `{audit['mismatch_rows']}` ERP-vs-DO mismatches (₹{audit['mismatch_lakhs']:.1f} L) & `{audit['rescued_rows']}` non-paper rows auto-rescued via DO rules.",
            f"- **Top-8 Store Recovery Upside:** Lifting the top 8 lagging high-volume stores to average Fin Share unlocks **+₹{res['total_top8_recovery_cr']:.2f} Cr**.",
        ]
        if alerts:
            lines.append("\n#### Key Alerts")
            for a in alerts:
                lines.append(f"- {a}")
        if drops:
            lines.append("\n#### Steepest Store Drops vs Prior Period")
            for x in drops[:5]:
                lines.append(f"- **{x['store']}** ({x['fin_bdm']}): `{x['fin_share_pct']}%` (`{x['diff_pp']:+.1f} pp`, Sales ₹{x['tot_cr']} Cr)")
        if gains:
            lines.append("\n#### Top Store Gainers")
            for x in gains[:5]:
                lines.append(f"- **{x['store']}** ({x['fin_bdm']}): `{x['fin_share_pct']}%` (`{x['diff_pp']:+.1f} pp`, Sales ₹{x['tot_cr']} Cr)")
        yield {"type": "answer", "mode": "builtin", "text": "\n".join(lines)}
        return

    # 6. Targets & Pacing
    if re.search(r"\b(target|pacing|slab|achiev|trend)\b", low):
        yield {"type": "tool_start", "tool": "get_targets_and_pacing", "label": "Loading Global & TM Targets vs Live Achievement..."}
        res = ai_tools.tool_get_targets_and_pacing(month=d0[:7], date_str=d0, state=st0, context=context)
        if res.get("table"):
            yield {"type": "table", "table": res["table"]}
        yield {"type": "tool_done", "tool": "get_targets_and_pacing", "summary": f"Loaded {len(res.get('tm_pacing') or [])} TMs"}
        gt = (res.get("global_targets") or {}).get("exec_summary_targets") or {}
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": (
                f"### Target & Pacing Summary ({res['month']})\n\n"
                f"- **Global Targets:** Value Pen `{gt.get('value_penetration', 0)}%` · Vol Pen `{gt.get('vol_penetration', 0)}%` · Fin Value `₹{gt.get('fin_value_cr', 0)} Cr` · Card Value `₹{gt.get('card_value_cr', 0)} Cr`.\n"
                f"- **TM Monthly Targets Loaded:** {'Yes' if res['tm_targets_set'] else 'Not yet set for this month'} ({len(res['tm_pacing'])} Fin BDMs shown below)."
            ),
        }
        return

    # 7. Brand / Price Range
    if re.search(r"\b(brand|samsung|apple|vivo|oppo|lg|redmi|realme|oneplus|price\s*range|price\s*slab)\b", low):
        dim = "price" if "price" in low else "brand"
        cat = "ALL"
        for c_cand in ("MOBILE", "LAPTOP", "CD PRODUCTS", "SA", "HVA"):
            if c_cand.lower() in low:
                cat = c_cand
                break
        yield {"type": "tool_start", "tool": "get_brand_and_price_analysis", "label": f"Analyzing {dim} performance ({d0} · {tf0} · {cat})..."}
        res = ai_tools.tool_get_brand_and_price_analysis(d0, tf0, e0, st0, category=cat, dimension=dim, context=context)
        if res.get("chart"):
            yield {"type": "chart", "chart": res["chart"]}
        if res.get("table"):
            yield {"type": "table", "table": res["table"]}
        yield {"type": "tool_done", "tool": "get_brand_and_price_analysis", "summary": f"{len(res.get('rows') or [])} groups"}
        top3 = (res.get("rows") or [])[:3]
        bullets = "\n".join(f"- **{r['label']}:** ₹{r['fin_cr']} Cr FIN / ₹{r['tot_cr']} Cr Total (**{r['fin_share_pct']}%** Fin Share)" for r in top3)
        yield {"type": "answer", "mode": "builtin", "text": f"### {dim.title()} Finance Breakdown ({d0} · {tf0} · {cat})\n\n{bullets}"}
        return

    # 8. Store / TM / RBM / BDM / Staff Fin Share
    if re.search(r"\b(store|branch|tm\b|fin\s*bdm|rbm|bdm|staff|top|bottom|worst|best|lowest|highest|compare|zone|north|south)\b", low):
        view_by = "store"
        if "staff" in low:
            view_by = "staff"
        elif "fin bdm" in low or re.search(r"\btm\b", low):
            view_by = "tm"
        elif "rbm" in low:
            view_by = "rbm"
        elif "bdm" in low:
            view_by = "bdm"
        sort_by = "fin_share_asc" if re.search(r"\b(bottom|worst|lowest|lagging|weak)\b", low) else "fin_share_desc"
        cmp_w = "month" if "compare" in low else "none"
        yield {"type": "tool_start", "tool": "get_store_fin_share", "label": f"Querying {view_by.upper()} Fin Share ({d0} · {tf0})..."}
        res = ai_tools.tool_get_store_fin_share(d0, tf0, e0, st0, view_by=view_by, compare_with=cmp_w, sort_by=sort_by, limit=15, context=context)
        if res.get("status") != "success":
            yield {"type": "answer", "mode": "builtin", "text": f"Query failed: {res.get('error')}"}
            return
        if res.get("chart"):
            yield {"type": "chart", "chart": res["chart"]}
        if res.get("table"):
            yield {"type": "table", "table": res["table"]}
        yield {"type": "tool_done", "tool": "get_store_fin_share", "summary": f"Analyzed {res['summary'].get('entity_count', len(res.get('rows', [])))} {view_by}s"}
        s = res.get("summary") or {}
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": (
                f"### {view_by.upper()} Fin Share Ranking ({res['period'].get('start_date')} to {res['period'].get('end_date')})\n\n"
                f"- **Overall:** ₹{s.get('total_fin_cr', 0)} Cr FIN / ₹{s.get('total_sales_cr', 0)} Cr Total (**{s.get('overall_fin_share_pct', 0)}%**)\n"
                f"- **North vs South:** `{s.get('north_fin_share_pct', 0)}%` vs `{s.get('south_fin_share_pct', 0)}%`"
            ),
        }
        return

    # 9. Summary / Financier / Category / Sales / KPI ask
    if re.search(r"\b(summary|kpi|sales|financier|finance|bajaj|hdb|idfc|dmi|tvs|pfl|poonawalla|pinelab|paytm|benow|category|mobile|laptop|cd\s*product|overview|report|status)\b", low):
        yield {"type": "tool_start", "tool": "get_dashboard_summary", "label": f"Fetching live ClickHouse summary ({d0} · {tf0} · {st0})..."}
        res = ai_tools.tool_get_dashboard_summary(d0, tf0, e0, st0, context=context)
        if res.get("status") != "success":
            yield {"type": "answer", "mode": "builtin", "text": f"No data found for `{d0}` ({tf0}, {st0}). Try selecting a period with sales data (e.g. Oct 2026 or Q3 2026)."}
            return
        if res.get("chart"):
            yield {"type": "chart", "chart": res["chart"]}
        if res.get("table"):
            yield {"type": "table", "table": res["table"]}
        yield {"type": "tool_done", "tool": "get_dashboard_summary", "summary": "Live summary loaded"}

        h = res["headline"]
        cats = "\n".join(
            f"- **{c['category']}:** ₹{c['fin_val_cr']} Cr / ₹{c['tot_val_cr']} Cr (**{c['val_share_pct']}%** Val · **{c['vol_share_pct']}%** Vol)"
            for c in (res.get("categories") or [])
        )
        yield {
            "type": "answer",
            "mode": "builtin",
            "text": (
                f"### Live Finance Summary — {d0} ({tf0.title()} · {st0 or 'ALL STATES'})\n\n"
                f"- **Total Finance:** **₹{h['total_fin_cr']:.2f} Cr** (`{h['total_fin_qty']:,}` units) — **Paper:** ₹{h['paper_fin_cr']:.2f} Cr · **Card EMI:** ₹{h['card_fin_cr']:.2f} Cr\n"
                f"- **Total Sales:** **₹{h['total_sales_cr']:.2f} Cr** (`{h['total_sales_qty']:,}` units)\n"
                f"- **Fin Share:** **{h['fin_share_val_pct']:.2f}%** (Value) · **{h['fin_share_vol_pct']:.2f}%** (Volume) · **Card Pen:** **{h['card_pen_vol_pct']:.2f}%**\n\n"
                f"#### Category Breakdown\n{cats}"
            ),
        }
        return

    # 10. Honest guidance when an open-ended question requires a connected LLM API key
    yield {
        "type": "answer",
        "mode": "builtin",
        "text": (
            f"### Connect an LLM API Key for Unrestricted Free-Form Answers\n\n"
            f"Right now the portal is running in **Built-in Fast Analytics Mode** (no Gemini / OpenAI / Claude API key configured yet), so it couldn't freely reason about: *\"{msg}\"*\n\n"
            f"#### How to unlock full unrestricted AI (30 seconds)\n"
            f"- Click the **📚 Learned Memory & Settings** sub-tab above (or click the amber pill at top-left).\n"
            f"- Select **Google Gemini (2.5 Flash + Pro)**, **OpenAI**, or **Anthropic Claude**, paste your API key, and click **Save AI Settings**.\n\n"
            f"#### What you can ask immediately right now (no API key needed)\n"
            f"- *\"October 2026 finance summary\"* · *\"Run a deep analysis for Q3\"*\n"
            f"- *\"Top 10 stores vs bottom 10 stores by Fin Share\"* · *\"TM target pacing\"*\n"
            f"- *\"How is the cleaning process done?\"* · *\"How is Paper Finance mapped?\"*\n"
            f"- *\"Open Duplicate Store Fin Share for Q3 by Fin BDM\"* · *\"Pin a Top 10 Stores chart to Dashboard\"*\n"
            f"- *\"Remember that Vypin Future was closed Oct 5–12\"*"
        ),
    }


# ---------------------------------------------------------------------------
# Main Multi-Step Streaming Agent Runner (LLM + Auto-Fallback)
# ---------------------------------------------------------------------------
def run_agent_stream(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    force_deep: bool = False,
) -> Generator[dict[str, Any], None, None]:
    """
    Yields structured SSE event dicts:
      - {"type": "status", "text": "..."}
      - {"type": "tool_start", "tool": "...", "label": "..."}
      - {"type": "tool_done", "tool": "...", "summary": "..."}
      - {"type": "table", "table": {...}}
      - {"type": "chart", "chart": {...}}
      - {"type": "ui_actions", "actions": [...]}
      - {"type": "pending_action", "action": {...}}
      - {"type": "answer", "text": "...", "mode": "llm"|"builtin", "model": "...", "sql": "..."}
      - {"type": "error", "error": "..."}
    """
    cfg = ai_memory.get_effective_ai_config()
    if not cfg["has_key"]:
        yield from _run_builtin_agent(user_message, context or {})
        return

    provider = cfg["provider"]
    api_key = cfg["api_key"]
    use_deep = force_deep or bool(_DEEP_KEYWORDS.search(user_message or ""))
    model = cfg["deep_model"] if use_deep else cfg["fast_model"]
    system_prompt = ai_semantic_layer.build_system_prompt(context)
    tools = ai_tools.TOOL_DEFINITIONS

    yield {"type": "status", "text": f"Thinking with {provider.title()} ({model})..."}

    # Build conversation history cleanly per provider
    recent_hist = [m for m in (history or [])[-12:] if m.get("role") in ("user", "assistant") and m.get("content")]
    last_sql = ""
    max_iters = 7

    try:
        if provider == "gemini":
            contents: list[dict[str, Any]] = []
            for m in recent_hist:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": str(m["content"])[:3000]}]})
            contents.append({"role": "user", "parts": [{"text": user_message}]})

            for _step in range(max_iters):
                resp = _call_gemini(api_key, model, system_prompt, contents, tools)
                tcs = resp["tool_calls"]
                if not tcs:
                    yield {"type": "answer", "text": resp["text"] or "Completed.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}
                    return

                contents.append({"role": "model", "parts": resp["raw_parts"]})
                fn_response_parts = []
                for tc in tcs:
                    tname, targs = tc["name"], tc["args"]
                    if tname == "run_readonly_sql" and targs.get("sql"):
                        last_sql = str(targs["sql"])
                    yield {"type": "tool_start", "tool": tname, "label": f"Running {tname}..."}
                    result = ai_tools.execute_tool(tname, targs, context=context)
                    for ev in _extract_ui_events(result):
                        yield ev
                    yield {"type": "tool_done", "tool": tname, "summary": str(result.get("status", "done"))}
                    fn_response_parts.append({
                        "functionResponse": {
                            "name": tname,
                            "response": {"result": json.loads(_compact_tool_result_for_llm(result))},
                        }
                    })
                contents.append({"role": "user", "parts": fn_response_parts})

            yield {"type": "answer", "text": "Reached maximum tool-reasoning steps. Please narrow the question slightly.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}

        elif provider in ("openai", "openrouter"):
            burl = cfg["base_url"] or ("https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1")
            extra_hdrs = {"HTTP-Referer": "https://myg-finance-portal.onrender.com", "X-Title": "myG Finance AI"} if provider == "openrouter" else None
            messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            for m in recent_hist:
                messages.append({"role": m["role"], "content": str(m["content"])[:3000]})
            messages.append({"role": "user", "content": user_message})

            for _step in range(max_iters):
                resp = _call_openai_compat(api_key, model, burl, messages, tools, extra_hdrs)
                tcs = resp["tool_calls"]
                if not tcs:
                    yield {"type": "answer", "text": resp["text"] or "Completed.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}
                    return
                messages.append(resp["raw_msg"])
                for tc in tcs:
                    tname, targs = tc["name"], tc["args"]
                    if tname == "run_readonly_sql" and targs.get("sql"):
                        last_sql = str(targs["sql"])
                    yield {"type": "tool_start", "tool": tname, "label": f"Running {tname}..."}
                    result = ai_tools.execute_tool(tname, targs, context=context)
                    for ev in _extract_ui_events(result):
                        yield ev
                    yield {"type": "tool_done", "tool": tname, "summary": str(result.get("status", "done"))}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": _compact_tool_result_for_llm(result),
                    })
            yield {"type": "answer", "text": "Reached maximum tool-reasoning steps.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}

        elif provider == "anthropic":
            messages = []
            for m in recent_hist:
                messages.append({"role": m["role"], "content": str(m["content"])[:3000]})
            messages.append({"role": "user", "content": user_message})

            for _step in range(max_iters):
                resp = _call_anthropic(api_key, model, system_prompt, messages, tools)
                tcs = resp["tool_calls"]
                if not tcs:
                    yield {"type": "answer", "text": resp["text"] or "Completed.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}
                    return
                messages.append({"role": "assistant", "content": resp["raw_blocks"]})
                tool_results = []
                for tc in tcs:
                    tname, targs = tc["name"], tc["args"]
                    if tname == "run_readonly_sql" and targs.get("sql"):
                        last_sql = str(targs["sql"])
                    yield {"type": "tool_start", "tool": tname, "label": f"Running {tname}..."}
                    result = ai_tools.execute_tool(tname, targs, context=context)
                    for ev in _extract_ui_events(result):
                        yield ev
                    yield {"type": "tool_done", "tool": tname, "summary": str(result.get("status", "done"))}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": _compact_tool_result_for_llm(result),
                    })
                messages.append({"role": "user", "content": tool_results})
            yield {"type": "answer", "text": "Reached maximum tool-reasoning steps.", "mode": "llm", "provider": provider, "model": model, "sql": last_sql}

        else:
            yield {"type": "error", "error": f"Unsupported AI provider: {provider}"}

    except Exception as exc:
        logger.error("LLM agent loop failed (%s): %s", provider, exc, exc_info=True)
        yield {"type": "status", "text": f"LLM error ({str(exc)[:120]}) — switching to Built-in Fast Analytics..."}
        yield from _run_builtin_agent(user_message, context or {})


def _extract_ui_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    evs = []
    if isinstance(result, dict):
        if result.get("chart"):
            evs.append({"type": "chart", "chart": result["chart"]})
        if result.get("table"):
            evs.append({"type": "table", "table": result["table"]})
        if result.get("ui_actions"):
            evs.append({"type": "ui_actions", "actions": result["ui_actions"]})
        if result.get("pending_action"):
            evs.append({"type": "pending_action", "action": result["pending_action"]})
    return evs
