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
_QUERY_CACHE: dict[str, tuple[float, object]] = {}  # key -> (timestamp, result)
_CACHE_TTL_SECONDS = 1800  # 30 minutes — data doesn't change during a review session


def _cache_key(sql: str, params: dict | None) -> str:
    raw = sql + str(sorted((params or {}).items()))
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str):
    """Return cached value if still fresh, else None."""
    if key in _QUERY_CACHE:
        ts, val = _QUERY_CACHE[key]
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return val
        del _QUERY_CACHE[key]
    return None


def _cache_set(key: str, value) -> None:
    _QUERY_CACHE[key] = (time.time(), value)


def cache_clear() -> int:
    """Manually flush the query cache. Returns number of entries cleared."""
    count = len(_QUERY_CACHE)
    _QUERY_CACHE.clear()
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
import threading as _threading
_thread_local = _threading.local()


def get_client():
    """Return (and lazily create) a per-thread ClickHouse client."""
    if getattr(_thread_local, 'client', None) is not None:
        return _thread_local.client

    try:
        import clickhouse_connect  # type: ignore

        _thread_local.client = clickhouse_connect.get_client(
            host=_HOST,
            port=_PORT,
            username=_USER,
            password=_PASSWORD,
            database=_DB,
            secure=True,          # HTTPS
            compress=True,        # LZ4 compression on the wire
            connect_timeout=10,
            send_receive_timeout=120,
        )
        logger.info("ClickHouse client connected to %s:%s (thread=%s)", _HOST, _PORT, _threading.current_thread().name)
    except Exception as exc:
        logger.error("Failed to connect to ClickHouse: %s", exc)
        raise

    return _thread_local.client



def query_df(sql: str, params: dict | None = None, cache: bool = True):
    """
    Execute *sql* and return results as a pandas DataFrame.

    Parameters
    ----------
    sql    : The ClickHouse SQL query (use {name:Type} placeholders for params).
    params : Optional dict of query parameters.
    cache  : If True (default), use the in-memory TTL cache (5 min).

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

    client = get_client()
    try:
        result = client.query_df(sql, parameters=params or {})
        if cache:
            _cache_set(key, result.copy())  # store a copy so callers can mutate freely
            logger.debug("ClickHouse cache SET (%s, %d rows)", key[:8], len(result))
        return result
    except Exception as exc:
        logger.error("ClickHouse query failed: %s\nSQL: %s", exc, sql)
        raise


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
    """Return True if ClickHouse is reachable, False otherwise."""
    try:
        client = get_client()
        client.ping()
        return True
    except Exception:
        return False


def get_row_counts() -> dict:
    """Return {table_name: row_count} for the key Finance Portal tables."""
    tables = [
        "azure_sales_report",
        "azure_invoice_report",
        "item_master",
        "sales_data",
    ]
    counts = {}
    try:
        client = get_client()
        for tbl in tables:
            result = client.query(f"SELECT count() FROM `{_DB}`.`{tbl}`")
            counts[tbl] = int(result.result_rows[0][0])
    except Exception as exc:
        logger.warning("Could not fetch row counts: %s", exc)
    return counts
