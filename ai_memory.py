"""
ai_memory.py
------------
ClickHouse-backed persistent storage for the myG Finance Portal AI Agent:
  - ai_memory           : Learned business facts, store exceptions, custom KPI definitions
  - ai_golden_queries   : Verified SQL query templates learned from positive user feedback
  - ai_conversations    : Persistent multi-turn chat history across refreshes/deploys
  - ai_dashboard_config : Versioned Tier-2 dashboard customizations (widgets, thresholds, layout) with Undo
  - ai_settings         : Runtime LLM provider/model/PIN settings (API keys never returned plaintext)

Uses ReplacingMergeTree in ClickHouse Cloud (matching `tm_fin_targets` in store_fin_share.py)
with automatic local JSON fallback in `data/ai_fallback/` if ClickHouse is temporarily unreachable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_FALLBACK_DIR = os.path.join(_BASE_DIR, "data", "ai_fallback")
os.makedirs(_FALLBACK_DIR, exist_ok=True)

_INIT_LOCK = threading.Lock()
_TABLES_INITIALIZED = False

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS ai_memory (
        id          String,
        category    String,
        key_topic   String,
        content     String,
        source      String,
        active      UInt8 DEFAULT 1,
        updated_at  DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (id)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_golden_queries (
        id               String,
        question_pattern String,
        sql_query        String,
        description      String,
        upvotes          Int32 DEFAULT 1,
        active           UInt8 DEFAULT 1,
        updated_at       DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (id)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_conversations (
        conv_id       String,
        title         String,
        messages_json String,
        context_json  String,
        active        UInt8 DEFAULT 1,
        updated_at    DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (conv_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_dashboard_config (
        config_id      String,
        version        UInt64,
        config_json    String,
        change_summary String,
        author         String,
        updated_at     DateTime DEFAULT now()
    ) ENGINE = MergeTree()
    ORDER BY (config_id, version)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_settings (
        key        String,
        value      String,
        updated_at DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (key)
    """,
]

DEFAULT_DASHBOARD_CONFIG: dict[str, Any] = {
    "widgets": [],
    "thresholds": {
        "fin_share_high": 65.0,
        "fin_share_mid": 50.0,
        "fin_share_low": 40.0,
        "enabled": False,
    },
    "hidden_sections": [],
    "custom_titles": {},
    "saved_views": [],
}


# ---------------------------------------------------------------------------
# Helpers: ClickHouse access + Local JSON fallback
# ---------------------------------------------------------------------------
def _ch_client():
    import clickhouse_client
    return clickhouse_client.get_client()


def _ch_query_df(sql: str, params: dict | None = None):
    import clickhouse_client
    return clickhouse_client.query_df(sql, params or {}, cache=False)


def ensure_tables() -> bool:
    """Idempotently create AI persistence tables in ClickHouse."""
    global _TABLES_INITIALIZED
    if _TABLES_INITIALIZED:
        return True
    with _INIT_LOCK:
        if _TABLES_INITIALIZED:
            return True
        try:
            client = _ch_client()
            for ddl in _DDL_STATEMENTS:
                client.command(ddl)
            _TABLES_INITIALIZED = True
            logger.info("AI ClickHouse tables verified/created.")
            return True
        except Exception as exc:
            logger.warning("AI ClickHouse table init failed (using local fallback): %s", exc)
            return False


def _fb_path(name: str) -> str:
    return os.path.join(_FALLBACK_DIR, f"{name}.json")


def _fb_load(name: str, default: Any) -> Any:
    p = _fb_path(name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _fb_save(name: str, data: Any) -> None:
    try:
        with open(_fb_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("Local fallback write failed for %s: %s", name, exc)


def _now_dt() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# 1. Learned Business Memory (ai_memory)
# ---------------------------------------------------------------------------
def list_memories(limit: int = 100) -> list[dict[str, Any]]:
    """Return active learned business rules & memories (newest first)."""
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT id, category, key_topic, content, source, active, updated_at
                FROM ai_memory FINAL
                WHERE active = 1
                ORDER BY updated_at DESC
                LIMIT {lim:UInt32}
                """,
                {"lim": int(limit)},
            )
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "id": str(r["id"]),
                    "category": str(r["category"]),
                    "key_topic": str(r["key_topic"]),
                    "content": str(r["content"]),
                    "source": str(r["source"]),
                    "updated_at": str(r["updated_at"]),
                })
            return rows
        except Exception as exc:
            logger.warning("list_memories CH failed, falling back: %s", exc)

    items = _fb_load("ai_memory", [])
    return [m for m in items if m.get("active", 1) == 1][:limit]


def save_memory(
    key_topic: str,
    content: str,
    category: str = "business_rule",
    source: str = "user",
    memory_id: str | None = None,
) -> dict[str, Any]:
    """Insert or update a learned memory item."""
    mid = memory_id or f"mem_{uuid.uuid4().hex[:10]}"
    key_topic = str(key_topic or "General Rule").strip()[:120]
    content = str(content or "").strip()[:2000]
    category = str(category or "business_rule").strip()[:60]
    source = str(source or "user").strip()[:40]
    now = _now_dt()

    item = {
        "id": mid,
        "category": category,
        "key_topic": key_topic,
        "content": content,
        "source": source,
        "active": 1,
        "updated_at": now.isoformat(),
    }

    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_memory",
                [(mid, category, key_topic, content, source, 1, now)],
                column_names=["id", "category", "key_topic", "content", "source", "active", "updated_at"],
            )
            return item
        except Exception as exc:
            logger.warning("save_memory CH failed, saving locally: %s", exc)

    items = [m for m in _fb_load("ai_memory", []) if m.get("id") != mid]
    items.insert(0, item)
    _fb_save("ai_memory", items)
    return item


def delete_memory(memory_id: str) -> bool:
    """Soft-delete a learned memory item (ReplacingMergeTree tombstone)."""
    mid = str(memory_id or "").strip()
    if not mid:
        return False
    now = _now_dt()
    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_memory",
                [(mid, "deleted", "", "", "user", 0, now)],
                column_names=["id", "category", "key_topic", "content", "source", "active", "updated_at"],
            )
            return True
        except Exception as exc:
            logger.warning("delete_memory CH failed: %s", exc)

    items = _fb_load("ai_memory", [])
    items = [m for m in items if m.get("id") != mid]
    _fb_save("ai_memory", items)
    return True


# ---------------------------------------------------------------------------
# 2. Golden SQL Query Library (ai_golden_queries)
# ---------------------------------------------------------------------------
def list_golden_queries(limit: int = 30) -> list[dict[str, Any]]:
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT id, question_pattern, sql_query, description, upvotes, updated_at
                FROM ai_golden_queries FINAL
                WHERE active = 1
                ORDER BY upvotes DESC, updated_at DESC
                LIMIT {lim:UInt32}
                """,
                {"lim": int(limit)},
            )
            return [
                {
                    "id": str(r["id"]),
                    "question_pattern": str(r["question_pattern"]),
                    "sql_query": str(r["sql_query"]),
                    "description": str(r["description"]),
                    "upvotes": int(r["upvotes"]),
                    "updated_at": str(r["updated_at"]),
                }
                for _, r in df.iterrows()
            ]
        except Exception as exc:
            logger.warning("list_golden_queries CH failed: %s", exc)
    return _fb_load("ai_golden_queries", [])[:limit]


def save_golden_query(question_pattern: str, sql_query: str, description: str = "") -> dict[str, Any]:
    qid = f"gq_{hashlib.md5((question_pattern + sql_query).encode()).hexdigest()[:10]}"
    now = _now_dt()
    row = {
        "id": qid,
        "question_pattern": str(question_pattern)[:300],
        "sql_query": str(sql_query)[:4000],
        "description": str(description)[:300],
        "upvotes": 1,
        "active": 1,
        "updated_at": now.isoformat(),
    }
    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_golden_queries",
                [(qid, row["question_pattern"], row["sql_query"], row["description"], 1, 1, now)],
                column_names=["id", "question_pattern", "sql_query", "description", "upvotes", "active", "updated_at"],
            )
            return row
        except Exception as exc:
            logger.warning("save_golden_query CH failed: %s", exc)
    items = [x for x in _fb_load("ai_golden_queries", []) if x.get("id") != qid]
    items.insert(0, row)
    _fb_save("ai_golden_queries", items)
    return row


# ---------------------------------------------------------------------------
# 3. Persistent Conversations (ai_conversations)
# ---------------------------------------------------------------------------
def list_conversations(limit: int = 30) -> list[dict[str, Any]]:
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT conv_id, title, updated_at, length(messages_json) AS msg_bytes
                FROM ai_conversations FINAL
                WHERE active = 1
                ORDER BY updated_at DESC
                LIMIT {lim:UInt32}
                """,
                {"lim": int(limit)},
            )
            return [
                {
                    "conv_id": str(r["conv_id"]),
                    "title": str(r["title"]),
                    "updated_at": str(r["updated_at"]),
                }
                for _, r in df.iterrows()
            ]
        except Exception as exc:
            logger.warning("list_conversations CH failed: %s", exc)

    convs = _fb_load("ai_conversations", {})
    out = [
        {"conv_id": cid, "title": c.get("title", "Conversation"), "updated_at": c.get("updated_at", "")}
        for cid, c in convs.items()
        if c.get("active", 1) == 1
    ]
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out[:limit]


def load_conversation(conv_id: str) -> dict[str, Any] | None:
    cid = str(conv_id or "").strip()
    if not cid:
        return None
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT conv_id, title, messages_json, context_json, updated_at
                FROM ai_conversations FINAL
                WHERE conv_id = {cid:String} AND active = 1
                LIMIT 1
                """,
                {"cid": cid},
            )
            if df is not None and not df.empty:
                r = df.iloc[0]
                return {
                    "conv_id": str(r["conv_id"]),
                    "title": str(r["title"]),
                    "messages": json.loads(str(r["messages_json"] or "[]")),
                    "context": json.loads(str(r["context_json"] or "{}")),
                    "updated_at": str(r["updated_at"]),
                }
        except Exception as exc:
            logger.warning("load_conversation CH failed: %s", exc)

    convs = _fb_load("ai_conversations", {})
    c = convs.get(cid)
    if c and c.get("active", 1) == 1:
        return c
    return None


def save_conversation(
    conv_id: str,
    title: str,
    messages: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cid = str(conv_id or "").strip() or f"conv_{uuid.uuid4().hex[:10]}"
    title = str(title or "New Conversation").strip()[:120]
    # Keep last 40 messages so payload stays compact and fast
    trimmed = messages[-40:]
    msg_json = json.dumps(trimmed, ensure_ascii=False, default=str)
    ctx_json = json.dumps(context or {}, ensure_ascii=False, default=str)
    now = _now_dt()
    payload = {
        "conv_id": cid,
        "title": title,
        "messages": trimmed,
        "context": context or {},
        "active": 1,
        "updated_at": now.isoformat(),
    }

    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_conversations",
                [(cid, title, msg_json, ctx_json, 1, now)],
                column_names=["conv_id", "title", "messages_json", "context_json", "active", "updated_at"],
            )
            return payload
        except Exception as exc:
            logger.warning("save_conversation CH failed: %s", exc)

    convs = _fb_load("ai_conversations", {})
    convs[cid] = payload
    _fb_save("ai_conversations", convs)
    return payload


def delete_conversation(conv_id: str) -> bool:
    cid = str(conv_id or "").strip()
    if not cid:
        return False
    now = _now_dt()
    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_conversations",
                [(cid, "", "[]", "{}", 0, now)],
                column_names=["conv_id", "title", "messages_json", "context_json", "active", "updated_at"],
            )
            return True
        except Exception as exc:
            logger.warning("delete_conversation CH failed: %s", exc)
    convs = _fb_load("ai_conversations", {})
    convs.pop(cid, None)
    _fb_save("ai_conversations", convs)
    return True


# ---------------------------------------------------------------------------
# 4. Versioned Dashboard Studio Config (ai_dashboard_config) with 1-Click Undo
# ---------------------------------------------------------------------------
def _merge_default_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    base = json.loads(json.dumps(DEFAULT_DASHBOARD_CONFIG))
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k == "thresholds" and isinstance(v, dict):
                base["thresholds"].update(v)
            else:
                base[k] = v
    return base


def get_dashboard_config() -> dict[str, Any]:
    """Return latest dashboard customization config + version metadata."""
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT version, config_json, change_summary, author, updated_at
                FROM ai_dashboard_config
                WHERE config_id = 'default'
                ORDER BY version DESC
                LIMIT 10
                """,
                {},
            )
            if df is not None and not df.empty:
                top = df.iloc[0]
                cfg = _merge_default_config(json.loads(str(top["config_json"] or "{}")))
                history = [
                    {
                        "version": int(r["version"]),
                        "change_summary": str(r["change_summary"]),
                        "author": str(r["author"]),
                        "updated_at": str(r["updated_at"]),
                    }
                    for _, r in df.iterrows()
                ]
                return {
                    "version": int(top["version"]),
                    "config": cfg,
                    "can_undo": len(df) > 1,
                    "history": history,
                }
        except Exception as exc:
            logger.warning("get_dashboard_config CH failed: %s", exc)

    hist = _fb_load("ai_dashboard_config_hist", [])
    if hist:
        top = hist[-1]
        return {
            "version": int(top.get("version", 1)),
            "config": _merge_default_config(top.get("config")),
            "can_undo": len(hist) > 1,
            "history": [
                {
                    "version": int(h.get("version", 0)),
                    "change_summary": h.get("change_summary", ""),
                    "author": h.get("author", "ai"),
                    "updated_at": h.get("updated_at", ""),
                }
                for h in reversed(hist[-10:])
            ],
        }
    return {
        "version": 0,
        "config": _merge_default_config(None),
        "can_undo": False,
        "history": [],
    }


def save_dashboard_config(
    config: dict[str, Any],
    change_summary: str = "Updated dashboard configuration",
    author: str = "ai_agent",
) -> dict[str, Any]:
    """Append a new versioned snapshot of dashboard customization config."""
    merged = _merge_default_config(config)
    version = int(time.time() * 1000)
    cfg_json = json.dumps(merged, ensure_ascii=False, default=str)
    summary = str(change_summary or "Dashboard edit")[:300]
    now = _now_dt()

    if ensure_tables():
        try:
            client = _ch_client()
            client.insert(
                "ai_dashboard_config",
                [("default", version, cfg_json, summary, author, now)],
                column_names=["config_id", "version", "config_json", "change_summary", "author", "updated_at"],
            )
            return get_dashboard_config()
        except Exception as exc:
            logger.warning("save_dashboard_config CH failed: %s", exc)

    hist = _fb_load("ai_dashboard_config_hist", [])
    hist.append({
        "version": version,
        "config": merged,
        "change_summary": summary,
        "author": author,
        "updated_at": now.isoformat(),
    })
    _fb_save("ai_dashboard_config_hist", hist[-25:])
    return get_dashboard_config()


def undo_dashboard_config() -> dict[str, Any]:
    """Roll back to the previous dashboard customization snapshot by writing it as a new version."""
    if ensure_tables():
        try:
            df = _ch_query_df(
                """
                SELECT version, config_json, change_summary
                FROM ai_dashboard_config
                WHERE config_id = 'default'
                ORDER BY version DESC
                LIMIT 2
                """,
                {},
            )
            if df is not None and len(df) >= 2:
                prev_row = df.iloc[1]
                prev_cfg = json.loads(str(prev_row["config_json"] or "{}"))
                return save_dashboard_config(
                    prev_cfg,
                    change_summary=f"Undo -> restored v{int(prev_row['version'])}",
                    author="undo",
                )
            elif df is not None and len(df) == 1:
                return save_dashboard_config(
                    DEFAULT_DASHBOARD_CONFIG,
                    change_summary="Undo -> reset to defaults",
                    author="undo",
                )
        except Exception as exc:
            logger.warning("undo_dashboard_config CH failed: %s", exc)

    hist = _fb_load("ai_dashboard_config_hist", [])
    if len(hist) >= 2:
        hist.pop()
        _fb_save("ai_dashboard_config_hist", hist)
        return get_dashboard_config()
    if len(hist) == 1:
        _fb_save("ai_dashboard_config_hist", [])
        return get_dashboard_config()
    return get_dashboard_config()


# ---------------------------------------------------------------------------
# 5. Runtime AI Settings & Optional Admin PIN (ai_settings)
# ---------------------------------------------------------------------------
def _encode_secret(raw: str) -> str:
    if not raw:
        return ""
    return "b64:" + base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_secret(val: str) -> str:
    if not val:
        return ""
    if val.startswith("b64:"):
        try:
            return base64.b64decode(val[4:].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    return val


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(("myg_ai_pin::" + str(pin).strip()).encode("utf-8")).hexdigest()


def get_all_settings() -> dict[str, str]:
    """Internal helper returning raw setting key->value map."""
    out: dict[str, str] = {}
    if ensure_tables():
        try:
            df = _ch_query_df("SELECT key, value FROM ai_settings FINAL", {})
            for _, r in df.iterrows():
                out[str(r["key"])] = str(r["value"])
            return out
        except Exception as exc:
            logger.warning("get_all_settings CH failed: %s", exc)
    return dict(_fb_load("ai_settings", {}))


def set_setting(key: str, value: str) -> None:
    k = str(key).strip()
    v = str(value)
    now = _now_dt()
    if ensure_tables():
        try:
            client = _ch_client()
            client.insert("ai_settings", [(k, v, now)], column_names=["key", "value", "updated_at"])
            return
        except Exception as exc:
            logger.warning("set_setting CH failed: %s", exc)
    data = _fb_load("ai_settings", {})
    data[k] = v
    _fb_save("ai_settings", data)


def get_effective_ai_config() -> dict[str, Any]:
    """
    Resolve effective LLM configuration from environment variables first,
    falling back to saved `ai_settings` in ClickHouse.
    Never returns raw API keys in public responses (use `api_key` internally only).
    """
    saved = get_all_settings()

    provider = (
        os.environ.get("AI_PROVIDER")
        or saved.get("provider")
        or ""
    ).strip().lower()

    keys = {
        "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or _decode_secret(saved.get("gemini_api_key", "")),
        "openai": os.environ.get("OPENAI_API_KEY") or _decode_secret(saved.get("openai_api_key", "")),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY") or _decode_secret(saved.get("anthropic_api_key", "")),
        "openrouter": os.environ.get("OPENROUTER_API_KEY") or _decode_secret(saved.get("openrouter_api_key", "")),
    }

    if not provider:
        for cand in ("gemini", "openai", "anthropic", "openrouter"):
            if keys[cand]:
                provider = cand
                break
        if not provider:
            provider = "gemini"

    api_key = keys.get(provider, "") or ""

    default_models = {
        "gemini": ("gemini-2.5-flash", "gemini-2.5-pro"),
        "openai": ("gpt-4.1-mini", "gpt-4.1"),
        "anthropic": ("claude-sonnet-4-20250514", "claude-sonnet-4-20250514"),
        "openrouter": ("google/gemini-2.5-flash", "google/gemini-2.5-pro"),
    }
    def_fast, def_deep = default_models.get(provider, ("gemini-2.5-flash", "gemini-2.5-pro"))

    fast_model = (os.environ.get("AI_FAST_MODEL") or saved.get("fast_model") or def_fast).strip()
    deep_model = (os.environ.get("AI_DEEP_MODEL") or saved.get("deep_model") or def_deep).strip()
    base_url = (os.environ.get("AI_BASE_URL") or saved.get("base_url") or "").strip()

    env_pin = (os.environ.get("AI_ADMIN_PIN") or "").strip()
    pin_hash = _hash_pin(env_pin) if env_pin else (saved.get("admin_pin_hash") or "").strip()

    return {
        "provider": provider,
        "api_key": api_key,
        "has_key": bool(api_key),
        "configured_providers": {p: bool(v) for p, v in keys.items()},
        "fast_model": fast_model,
        "deep_model": deep_model,
        "base_url": base_url,
        "pin_enabled": bool(pin_hash),
        "pin_hash": pin_hash,
    }


def save_ai_settings_update(body: dict[str, Any]) -> dict[str, Any]:
    """Update provider, models, optional API key, or Admin PIN."""
    if "provider" in body and body["provider"]:
        set_setting("provider", str(body["provider"]).strip().lower())
    if "fast_model" in body:
        set_setting("fast_model", str(body["fast_model"] or "").strip())
    if "deep_model" in body:
        set_setting("deep_model", str(body["deep_model"] or "").strip())
    if "base_url" in body:
        set_setting("base_url", str(body["base_url"] or "").strip())
    for p in ("gemini", "openai", "anthropic", "openrouter"):
        kname = f"{p}_api_key"
        if kname in body and body[kname] is not None:
            val = str(body[kname]).strip()
            if val == "__CLEAR__":
                set_setting(kname, "")
            elif val:
                set_setting(kname, _encode_secret(val))
    if "admin_pin" in body and body["admin_pin"] is not None:
        pin_val = str(body["admin_pin"]).strip()
        if pin_val == "__CLEAR__":
            set_setting("admin_pin_hash", "")
        elif pin_val:
            set_setting("admin_pin_hash", _hash_pin(pin_val))
    return get_public_ai_status()


def verify_admin_pin(pin: str | None) -> bool:
    """Return True if Admin PIN is disabled OR the supplied PIN matches."""
    cfg = get_effective_ai_config()
    if not cfg["pin_enabled"]:
        return True
    if not pin:
        return False
    return _hash_pin(str(pin).strip()) == cfg["pin_hash"]


def get_public_ai_status() -> dict[str, Any]:
    cfg = get_effective_ai_config()
    return {
        "mode": "llm" if cfg["has_key"] else "builtin",
        "provider": cfg["provider"],
        "has_key": cfg["has_key"],
        "configured_providers": cfg["configured_providers"],
        "fast_model": cfg["fast_model"],
        "deep_model": cfg["deep_model"],
        "base_url": cfg["base_url"],
        "pin_enabled": cfg["pin_enabled"],
    }
