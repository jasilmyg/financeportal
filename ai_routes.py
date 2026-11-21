"""
ai_routes.py
------------
Flask Blueprint mounting all AI Agent, Deep Insights, Learning Memory,
Dashboard Studio (Tiers 1-3), and Settings endpoints under `/api/ai/*`.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from typing import Any

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

import ai_memory
import ai_provider
import ai_tools

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai_bp", __name__)


def _sse(event_obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(event_obj, ensure_ascii=False, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Status & Settings
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/status", methods=["GET"])
def api_ai_status():
    pub = ai_memory.get_public_ai_status()
    mems = ai_memory.list_memories(limit=50)
    gqs = ai_memory.list_golden_queries(limit=30)
    dcfg = ai_memory.get_dashboard_config()
    pub.update({
        "memory_count": len(mems),
        "golden_query_count": len(gqs),
        "widget_count": len((dcfg.get("config") or {}).get("widgets") or []),
        "can_undo": bool(dcfg.get("can_undo")),
    })
    return jsonify(pub)


@ai_bp.route("/api/ai/settings", methods=["GET", "POST"])
def api_ai_settings():
    if request.method == "GET":
        return jsonify(ai_memory.get_public_ai_status())
    body = request.get_json(silent=True) or {}
    # If an Admin PIN is already active, require it to change settings/keys/PIN
    cfg = ai_memory.get_effective_ai_config()
    if cfg["pin_enabled"]:
        supplied = str(body.get("current_admin_pin") or body.get("admin_pin_verify") or "")
        if not ai_memory.verify_admin_pin(supplied):
            return jsonify({"status": "unauthorized", "error": "Current Admin PIN is required to modify AI settings."}), 403
    updated = ai_memory.save_ai_settings_update(body)
    return jsonify({"status": "success", "settings": updated})


@ai_bp.route("/api/ai/test-provider", methods=["POST"])
def api_ai_test_provider():
    return jsonify(ai_provider.test_provider_connection())


# ---------------------------------------------------------------------------
# Streaming Chat Endpoint (SSE)
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    body = request.get_json(silent=True) or {}
    message = str(body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    conv_id = str(body.get("conversation_id") or "").strip() or f"conv_{uuid.uuid4().hex[:10]}"
    context = body.get("context") or {}
    force_deep = bool(body.get("deep", False))
    non_stream = str(request.args.get("stream", "1")).lower() in ("0", "false", "no")

    existing = ai_memory.load_conversation(conv_id)
    history = list((existing or {}).get("messages") or [])
    title = (existing or {}).get("title") or (message[:64] + ("..." if len(message) > 64 else ""))

    if non_stream:
        events = list(ai_provider.run_agent_stream(message, history=history, context=context, force_deep=force_deep))
        answer_ev = next((e for e in reversed(events) if e.get("type") == "answer"), {})
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer_ev.get("text", ""), "sql": answer_ev.get("sql", "")})
        ai_memory.save_conversation(conv_id, title, history, context)
        return jsonify({"conversation_id": conv_id, "events": events})

    def _generate():
        yield _sse({"type": "meta", "conversation_id": conv_id, "title": title})
        final_text = ""
        final_sql = ""
        saved_tables = []
        saved_charts = []
        try:
            for ev in ai_provider.run_agent_stream(message, history=history, context=context, force_deep=force_deep):
                if ev.get("type") == "answer":
                    final_text = str(ev.get("text") or "")
                    final_sql = str(ev.get("sql") or "")
                elif ev.get("type") == "table" and ev.get("table"):
                    saved_tables.append(ev["table"])
                elif ev.get("type") == "chart" and ev.get("chart"):
                    saved_charts.append(ev["chart"])
                yield _sse(ev)
        except Exception as exc:
            logger.error("SSE chat stream error: %s", exc, exc_info=True)
            yield _sse({"type": "error", "error": str(exc)})
        finally:
            if final_text:
                history.append({"role": "user", "content": message})
                history.append({
                    "role": "assistant",
                    "content": final_text,
                    "sql": final_sql,
                    "charts": saved_charts[:2],
                    "tables": [
                        {"title": t.get("title"), "columns": t.get("columns"), "rows": (t.get("rows") or [])[:15], "export_token": t.get("export_token")}
                        for t in saved_tables[:2]
                    ],
                })
                try:
                    ai_memory.save_conversation(conv_id, title, history, context)
                except Exception as exc:
                    logger.warning("Failed to save conversation: %s", exc)
            yield _sse({"type": "done", "conversation_id": conv_id})

    resp = Response(stream_with_context(_generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------------
# Instant Executive Morning Brief & 7-Axis Deep Analysis
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/brief", methods=["POST"])
def api_ai_brief():
    body = request.get_json(silent=True) or {}
    try:
        res = ai_tools.tool_generate_morning_brief(
            date_str=body.get("date", ""),
            timeframe=body.get("timeframe", "monthly"),
            end_date_str=body.get("end_date", ""),
            state=body.get("state", "KERALA"),
            context=body,
        )
        return jsonify(res), (200 if res.get("status") == "success" else 400)
    except Exception as exc:
        logger.error("/api/ai/brief failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "error": str(exc)}), 500


@ai_bp.route("/api/ai/deep-analysis", methods=["POST"])
def api_ai_deep_analysis():
    body = request.get_json(silent=True) or {}
    try:
        res = ai_tools.tool_run_deep_analysis(
            date_str=body.get("date", ""),
            timeframe=body.get("timeframe", "monthly"),
            end_date_str=body.get("end_date", ""),
            state=body.get("state", "KERALA"),
            compare_with=body.get("compare_with", ""),
            focus_entity=body.get("focus_entity", ""),
            context=body,
        )
        return jsonify(res), (200 if res.get("status") == "success" else 400)
    except Exception as exc:
        logger.error("/api/ai/deep-analysis failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Confirm Staged Edit Actions (Tier 2 & Tier 3)
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/confirm-action", methods=["POST"])
def api_ai_confirm_action():
    body = request.get_json(silent=True) or {}
    action_id = str(body.get("action_id") or "").strip()
    admin_pin = str(body.get("admin_pin") or "")
    if not action_id:
        return jsonify({"status": "error", "error": "action_id is required"}), 400
    res = ai_tools.apply_pending_action(action_id, admin_pin=admin_pin)
    code = 403 if res.get("status") == "unauthorized" else (200 if res.get("status") in ("success", "already_applied") else 400)
    return jsonify(res), code


# ---------------------------------------------------------------------------
# Dashboard Studio Config & Live Custom Widgets (Tier 2 + 1-Click Undo)
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/dashboard-config", methods=["GET"])
def api_ai_dashboard_config():
    state_wrap = ai_memory.get_dashboard_config()
    eval_flag = str(request.args.get("evaluate", "1")).lower() not in ("0", "false", "no")
    evaluated = []
    if eval_flag and ((state_wrap.get("config") or {}).get("widgets") or []):
        evaluated = ai_tools.evaluate_custom_widgets(
            date_str=request.args.get("date", ""),
            timeframe=request.args.get("timeframe", "monthly"),
            end_date_str=request.args.get("end_date", ""),
            state=request.args.get("state", "KERALA"),
        )
    return jsonify({
        "version": state_wrap["version"],
        "can_undo": state_wrap["can_undo"],
        "history": state_wrap["history"],
        "config": state_wrap["config"],
        "evaluated_widgets": evaluated,
    })


@ai_bp.route("/api/ai/dashboard-config/undo", methods=["POST"])
def api_ai_dashboard_undo():
    body = request.get_json(silent=True) or {}
    if not ai_memory.verify_admin_pin(body.get("admin_pin")):
        return jsonify({"status": "unauthorized", "error": "Invalid Admin PIN."}), 403
    new_state = ai_memory.undo_dashboard_config()
    return jsonify({"status": "success", "dashboard_config": new_state})


@ai_bp.route("/api/ai/dashboard-config/widget", methods=["POST"])
def api_ai_dashboard_add_widget():
    body = request.get_json(silent=True) or {}
    if not ai_memory.verify_admin_pin(body.get("admin_pin")):
        return jsonify({"status": "unauthorized", "error": "Invalid Admin PIN."}), 403
    widget = body.get("widget") or body
    if widget.get("metric_source") == "sql":
        ok, err, _ = ai_tools.validate_readonly_sql(widget.get("sql", ""))
        if not ok:
            return jsonify({"status": "error", "error": err}), 400
    wid = str(widget.get("id") or f"w_{uuid.uuid4().hex[:8]}")
    clean_w = {
        "id": wid,
        "tab": str(widget.get("tab") or "dashboard"),
        "widget_type": str(widget.get("widget_type") or "chart").lower(),
        "title": str(widget.get("title") or "Custom Widget")[:80],
        "metric_source": str(widget.get("metric_source") or "financier").lower(),
        "chart_type": str(widget.get("chart_type") or "bar").lower(),
        "sql": str(widget.get("sql") or ""),
        "subtitle": str(widget.get("subtitle") or ""),
    }
    cur = ai_memory.get_dashboard_config()["config"]
    cur["widgets"] = [w for w in (cur.get("widgets") or []) if w.get("id") != wid] + [clean_w]
    new_state = ai_memory.save_dashboard_config(cur, change_summary=f"Studio added widget: {clean_w['title']}", author="studio_ui")
    return jsonify({"status": "success", "widget": clean_w, "dashboard_config": new_state})


@ai_bp.route("/api/ai/dashboard-config/widget/<wid>", methods=["DELETE"])
def api_ai_dashboard_delete_widget(wid: str):
    pin = request.args.get("admin_pin") or (request.get_json(silent=True) or {}).get("admin_pin")
    if not ai_memory.verify_admin_pin(pin):
        return jsonify({"status": "unauthorized", "error": "Invalid Admin PIN."}), 403
    cur = ai_memory.get_dashboard_config()["config"]
    before = len(cur.get("widgets") or [])
    cur["widgets"] = [w for w in (cur.get("widgets") or []) if w.get("id") != wid]
    if len(cur["widgets"]) == before:
        return jsonify({"status": "error", "error": "Widget not found"}), 404
    new_state = ai_memory.save_dashboard_config(cur, change_summary=f"Removed widget {wid}", author="studio_ui")
    return jsonify({"status": "success", "dashboard_config": new_state})


@ai_bp.route("/api/ai/dashboard-config/thresholds", methods=["POST"])
def api_ai_dashboard_thresholds():
    body = request.get_json(silent=True) or {}
    if not ai_memory.verify_admin_pin(body.get("admin_pin")):
        return jsonify({"status": "unauthorized", "error": "Invalid Admin PIN."}), 403
    cur = ai_memory.get_dashboard_config()["config"]
    th = dict(cur.get("thresholds") or {})
    for k in ("enabled", "fin_share_high", "fin_share_mid", "fin_share_low"):
        if k in body:
            th[k] = bool(body[k]) if k == "enabled" else float(body[k])
    cur["thresholds"] = th
    new_state = ai_memory.save_dashboard_config(cur, change_summary="Updated Fin Share color thresholds", author="studio_ui")
    return jsonify({"status": "success", "dashboard_config": new_state})


# ---------------------------------------------------------------------------
# Learned Business Memory & Feedback
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/memory", methods=["GET", "POST"])
def api_ai_memory():
    if request.method == "GET":
        return jsonify({
            "memories": ai_memory.list_memories(limit=100),
            "golden_queries": ai_memory.list_golden_queries(limit=30),
        })
    body = request.get_json(silent=True) or {}
    topic = str(body.get("key_topic") or "").strip()
    content = str(body.get("content") or "").strip()
    if not topic or not content:
        return jsonify({"error": "key_topic and content are required"}), 400
    saved = ai_memory.save_memory(topic, content, category=body.get("category", "business_rule"), source="ui")
    return jsonify({"status": "success", "memory": saved})


@ai_bp.route("/api/ai/memory/<mid>", methods=["DELETE"])
def api_ai_memory_delete(mid: str):
    ok = ai_memory.delete_memory(mid)
    return jsonify({"status": "success" if ok else "error"})


@ai_bp.route("/api/ai/feedback", methods=["POST"])
def api_ai_feedback():
    body = request.get_json(silent=True) or {}
    rating = str(body.get("rating") or "").lower()  # 'up' | 'down'
    question = str(body.get("question") or "").strip()
    sql = str(body.get("sql") or "").strip()
    note = str(body.get("note") or "").strip()

    learned_sql = None
    learned_mem = None
    if rating == "up" and sql and question:
        learned_sql = ai_memory.save_golden_query(question, sql, description="Verified via user thumbs-up")
    if note:
        learned_mem = ai_memory.save_memory(
            key_topic=f"User Feedback: {question[:40] or 'Correction'}",
            content=note,
            category="user_preference",
            source="feedback",
        )
    return jsonify({"status": "success", "learned_sql": bool(learned_sql), "learned_memory": bool(learned_mem)})


# ---------------------------------------------------------------------------
# Conversations History
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/conversations", methods=["GET"])
def api_ai_conversations():
    return jsonify({"conversations": ai_memory.list_conversations(limit=30)})


@ai_bp.route("/api/ai/conversations/<cid>", methods=["GET", "DELETE"])
def api_ai_conversation_item(cid: str):
    if request.method == "DELETE":
        ai_memory.delete_conversation(cid)
        return jsonify({"status": "success"})
    conv = ai_memory.load_conversation(cid)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(conv)


# ---------------------------------------------------------------------------
# Table Export (CSV / Formatted Excel via xlsxwriter)
# ---------------------------------------------------------------------------
@ai_bp.route("/api/ai/export/<token>", methods=["GET"])
def api_ai_export(token: str):
    payload = ai_tools.get_export_payload(token)
    if not payload:
        return jsonify({"error": "Export expired or not found. Re-run the query to export again."}), 404
    fmt = str(request.args.get("format", "xlsx")).lower()
    safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in payload["title"])[:50] or "ai_export"

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(payload["columns"])
        writer.writerows(payload["rows"])
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
        )

    import xlsxwriter
    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    ws = wb.add_worksheet("AI Analysis")
    hdr_fmt = wb.add_format({"bold": True, "bg_color": "#1E293B", "font_color": "#FFFFFF", "border": 1})
    cell_fmt = wb.add_format({"border": 1})
    for ci, col in enumerate(payload["columns"]):
        ws.write(0, ci, col, hdr_fmt)
        ws.set_column(ci, ci, max(14, min(32, len(str(col)) + 4)))
    for ri, row in enumerate(payload["rows"], start=1):
        for ci, val in enumerate(row):
            ws.write(ri, ci, val, cell_fmt)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(payload["rows"]), max(0, len(payload["columns"]) - 1))
    wb.close()
    bio.seek(0)
    return send_file(
        bio,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{safe_name}.xlsx",
    )
