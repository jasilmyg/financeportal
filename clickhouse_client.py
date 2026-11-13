"""
clickhouse_client.py
--------------------
Singleton ClickHouse client for the Finance Portal.

Credentials are loaded from a .env file (never hardcoded).
Uses the official `clickhouse-connect` driver which speaks the
native HTTP interface over HTTPS — ideal for ClickHouse Cloud.

Public API
----------
get_client()          -> clickhouse_connect.driver.Client
query_df(sql, params) -> pandas.DataFrame
is_connected()        -> bool
"""

import logging
import os
import time
import hashlib
from pathlib import Path

import pandas as pd

# Load .env from the same directory as this file (or its parent)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple in-memory TTL cache for heavy ClickHouse queries
# ---------------------------------------------------------------------------
import threading as _threading

_CACHE_LOCK = _threading.Lock()
_INFLIGHT_LOCK = _threading.Lock()
_INFLIGHT_LOCKS: dict[str, _threading.Lock] = {}
_QUERY_CACHE: dict[str, tuple[float, object]] = {}  # key -> (timestamp, result)
_CACHE_TTL_SECONDS = 1800  # 30 minutes — data doesn't change during a review session
_LAST_PING_OK_TS = 0.0
_ROW_COUNTS_CACHE: dict[str, object] = {"ts": 0.0, "counts": {}}


def _get_inflight_lock(key: str) -> _threading.Lock:
    with _INFLIGHT_LOCK:
        lock = _INFLIGHT_LOCKS.get(key)
        if lock is None:
            lock = _threading.Lock()
            _INFLIGHT_LOCKS[key] = lock
        return lock


def _cache_key(sql: str, params: dict | None) -> str:
    raw = sql + str(sorted((params or {}).items()))
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str):
    """Return cached value if still fresh, else None."""
    with _CACHE_LOCK:
        if key in _QUERY_CACHE:
            ts, val = _QUERY_CACHE[key]
            if time.time() - ts < _CACHE_TTL_SECONDS:
                return val
            del _QUERY_CACHE[key]
    return None


def _cache_set(key: str, value) -> None:
    with _CACHE_LOCK:
        _QUERY_CACHE[key] = (time.time(), value)


def cache_clear() -> int:
    """Manually flush the query cache. Returns number of entries cleared."""
    global _LAST_PING_OK_TS
    with _CACHE_LOCK:
        count = len(_QUERY_CACHE)
        _QUERY_CACHE.clear()
        _LAST_PING_OK_TS = 0.0
        _ROW_COUNTS_CACHE["ts"] = 0.0
        _ROW_COUNTS_CACHE["counts"] = {}
        return count

# ---------------------------------------------------------------------------
# Configuration (read once at import time)
# ---------------------------------------------------------------------------
_HOST = os.environ.get("CLICKHOUSE_HOST", "pdhsuv47ec.ap-south-1.aws.clickhouse.cloud")
_PORT = int(os.environ.get("CLICKHOUSE_PORT", 8443))
_USER = os.environ.get("CLICKHOUSE_USER", "default")
_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "ZFlujj9SA_Iei")
_DB = os.environ.get("CLICKHOUSE_DB", "default")

# ---------------------------------------------------------------------------
# Thread-local client (one connection per thread — avoids concurrent-session errors)
# ---------------------------------------------------------------------------
_thread_local = _threading.local()


def _reset_thread_client() -> None:
    """Safely close and clear the current thread's ClickHouse client."""
    client = getattr(_thread_local, 'client', None)
    _thread_local.client = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def _is_non_transient_query_error(exc: Exception) -> bool:
    """True for SQL syntax / schema errors that should not be retried."""
    msg = str(exc).upper()
    return any(tok in msg for tok in (
        "UNKNOWN_TABLE",
        "UNKNOWN_IDENTIFIER",
        "SYNTAX_ERROR",
        "TYPE_MISMATCH",
        "ILLEGAL_TYPE_OF_ARGUMENT",
        "NO_SUCH_COLUMN",
        "TABLE_ALREADY_EXISTS",
    ))


def get_client():
    """Return (and lazily create) a per-thread ClickHouse client with cold-start retry."""
    if getattr(_thread_local, 'client', None) is not None:
        return _thread_local.client

    import clickhouse_connect  # type: ignore

    backoffs = (1.5, 3.0)
    last_exc = None
    for attempt in range(3):
        try:
            client = clickhouse_connect.get_client(
                host=_HOST,
                port=_PORT,
                username=_USER,
                password=_PASSWORD,
                database=_DB,
                secure=True,          # HTTPS
                compress=True,        # LZ4 compression on the wire
                connect_timeout=45,
                send_receive_timeout=180,
            )
            _thread_local.client = client
            logger.info("ClickHouse client connected to %s:%s (thread=%s)", _HOST, _PORT, _threading.current_thread().name)
            return client
        except Exception as exc:
            last_exc = exc
            _reset_thread_client()
            logger.warning("ClickHouse connect attempt %d/3 failed: %s", attempt + 1, exc)
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])

    logger.error("Failed to connect to ClickHouse after retries: %s", last_exc)
    raise last_exc


def query_df(sql: str, params: dict | None = None, cache: bool = True):
    """
    Execute *sql* and return results as a pandas DataFrame.

    Parameters
    ----------
    sql    : The ClickHouse SQL query (use {name:Type} placeholders for params).
    params : Optional dict of query parameters.
    cache  : If True (default), use the in-memory TTL cache + in-flight deduplication.

    Returns
    -------
    pd.DataFrame with column names matching the SELECT list.
    """
    key = _cache_key(sql, params)
    if cache:
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("ClickHouse cache HIT (%s)", key[:8])
            return cached.copy()

    lock = _get_inflight_lock(key) if cache else None
    if lock is not None:
        lock.acquire()
    try:
        if cache:
            cached = _cache_get(key)
            if cached is not None:
                logger.debug("ClickHouse cache HIT after wait (%s)", key[:8])
                return cached.copy()

        last_exc = None
        for attempt in range(2):
            client = get_client()
            try:
                result = client.query_df(sql, parameters=params or {})
                if cache:
                    _cache_set(key, result.copy())  # store a copy so callers can mutate freely
                    logger.debug("ClickHouse cache SET (%s, %d rows)", key[:8], len(result))
                return result
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and not _is_non_transient_query_error(exc):
                    logger.warning("ClickHouse query transient error (retrying once): %s", exc)
                    _reset_thread_client()
                    time.sleep(1.5)
                    continue
                logger.error("ClickHouse query failed: %s\nSQL: %s", exc, sql)
                raise
        raise last_exc
    finally:
        if lock is not None:
            lock.release()


def replace_rows(table: str, rows: list, column_names: list, where: str) -> None:
    """
    Replace the rows of `table` that match the SQL condition `where` with `rows`.

    ClickHouse Cloud can silently lose rows inserted right after an ``ALTER ... DELETE``
    (the mutation may be applied to the new part), so the order is reversed here: the new
    rows are inserted first, stamped with an increasing ``upload_id``, and only rows from
    older uploads (smaller ``upload_id``) that match `where` are deleted afterwards.
    """
    import time

    client = get_client()
    client.command(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS upload_id UInt64 DEFAULT 0",
        settings={"alter_sync": "2"},
    )
    uid = time.time_ns() // 1000
    if rows:
        client.insert(
            table,
            [tuple(r) + (uid,) for r in rows],
            column_names=list(column_names) + ["upload_id"],
        )
    client.command(
        f"ALTER TABLE {table} DELETE WHERE ({where}) AND upload_id < {uid}",
        settings={"mutations_sync": "2"},
    )
    cache_clear()


def is_connected() -> bool:
    """Return True if ClickHouse is reachable, False otherwise (cached 30s + 1 retry)."""
    global _LAST_PING_OK_TS
    if time.time() - _LAST_PING_OK_TS < 30:
        return True
    for attempt in range(2):
        try:
            client = get_client()
            client.ping()
            _LAST_PING_OK_TS = time.time()
            return True
        except Exception:
            _reset_thread_client()
            if attempt == 0:
                time.sleep(1.0)
    return False


def get_row_counts() -> dict:
    """Return {table_name: row_count} for the key Finance Portal tables (600s cache)."""
    cached_ts = float(_ROW_COUNTS_CACHE.get("ts") or 0.0)
    cached_counts = _ROW_COUNTS_CACHE.get("counts") or {}
    if cached_counts and (time.time() - cached_ts < 600):
        return dict(cached_counts)

    tables = [
        "azure_sales_report",
        "azure_invoice_report",
        "item_master",
        "sales_data",
    ]
    counts = {}
    try:
        client = get_client()
        in_sql = ", ".join(f"'{t}'" for t in tables)
        res = client.query(
            f"SELECT name, toUInt64(coalesce(total_rows, 0)) AS cnt "
            f"FROM system.tables "
            f"WHERE database = currentDatabase() AND name IN ({in_sql})"
        )
        for name, cnt in res.result_rows:
            if cnt and int(cnt) > 0:
                counts[str(name)] = int(cnt)
        # Fallback to SELECT count() only for tables missing/zero in system.tables
        for tbl in tables:
            if tbl not in counts:
                r2 = client.query(f"SELECT count() FROM `{_DB}`.`{tbl}`")
                counts[tbl] = int(r2.result_rows[0][0])
        if counts:
            _ROW_COUNTS_CACHE["ts"] = time.time()
            _ROW_COUNTS_CACHE["counts"] = dict(counts)
    except Exception as exc:
        logger.warning("Could not fetch row counts: %s", exc)
    return counts
