"""
In-memory record of which EMI files (Pine Labs / Paytm / Benow) have been uploaded during
this server run, and the transaction-date span each upload covers.

The dashboard only shows PineLab / PAYTM / Benow numbers for a date range that overlaps a
span uploaded in this session; inside that span the numbers follow the dashboard date
picker exactly (daily / monthly / quarterly / custom range, across month boundaries).
The state is saved to `emi_state.json` (next to this module) so it survives server
restarts. Uploading a new sales report, or deleting that file, resets it and the three
columns go back to empty until the EMI files are uploaded again.
"""
import json
import os
import threading
from datetime import date

import pandas as pd

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'emi_state.json')

PINE_FIN  = 'Pine Lab EMI'   # value stored in benow_pine_financier.financier
PAYTM_FIN = 'PAYTM'
BENOW_FIN = 'Benow'
_ALL_FINS = (PINE_FIN, PAYTM_FIN, BENOW_FIN)

_DATE_MIN = date(1900, 1, 1)
_DATE_MAX = date(2999, 12, 31)

_lock = threading.Lock()
_uploaded: dict[str, set] = {PINE_FIN: set(), PAYTM_FIN: set(), BENOW_FIN: set()}   # data_month tags
_spans: dict[str, list] = {PINE_FIN: [], PAYTM_FIN: [], BENOW_FIN: []}              # (first_date, last_date)
_ch_synced: bool = False

_FIN_TABLES = (
    (PINE_FIN, 'pine_lab_transactions'),
    (PAYTM_FIN, 'paytm_transactions'),
    (BENOW_FIN, 'benow_transactions'),
)


def _save_locked() -> None:
    """Write the state to disk (caller holds _lock). Failures are non-fatal."""
    try:
        payload = {
            'uploaded': {k: sorted(v) for k, v in _uploaded.items()},
            'spans': {k: [[lo.isoformat(), hi.isoformat()] for lo, hi in v] for k, v in _spans.items()},
        }
        tmp = _STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


def _load() -> None:
    """Restore the state saved by a previous server run, if any."""
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
        with _lock:
            for k, v in payload.get('uploaded', {}).items():
                _uploaded[k] = set(v)
            for k, v in payload.get('spans', {}).items():
                _spans[k] = [(date.fromisoformat(lo), date.fromisoformat(hi)) for lo, hi in v]
    except Exception:
        pass


def _to_date(value):
    if value is None:
        return None
    try:
        ts = pd.to_datetime(value)
    except Exception:
        return None
    return None if pd.isna(ts) else ts.date()


def _month_bounds(data_month: str):
    first = pd.to_datetime(data_month + '-01').date()
    last = (pd.Timestamp(first) + pd.offsets.MonthEnd(0)).date()
    return first, last


def _merge_span_locked(financier: str, lo: date, hi: date) -> None:
    """Merge [lo, hi] into existing spans for financier, combining overlapping or adjacent ranges."""
    spans = _spans.setdefault(financier, [])
    spans.append((lo, hi))
    spans.sort(key=lambda s: s[0])
    merged = []
    for cur_lo, cur_hi in spans:
        if not merged:
            merged.append((cur_lo, cur_hi))
        else:
            prev_lo, prev_hi = merged[-1]
            if cur_lo <= (pd.Timestamp(prev_hi) + pd.Timedelta(days=1)).date():
                merged[-1] = (prev_lo, max(prev_hi, cur_hi))
            else:
                merged.append((cur_lo, cur_hi))
    _spans[financier] = merged


def mark_uploaded(financier: str, data_month, date_from=None, date_to=None) -> None:
    """
    Record that a file for `financier` was uploaded. `data_month` can be a single 'YYYY-MM' tag
    or an iterable of month strings; `date_from` / `date_to` are the first / last transaction
    dates in the file (defaults to the bounds of the covered months).
    """
    if not data_month:
        return
    if isinstance(data_month, (list, tuple, set)):
        months = [str(m) for m in data_month if m]
    else:
        months = [str(data_month)] if data_month else []
    if not months:
        return

    lo, hi = _to_date(date_from), _to_date(date_to)
    if lo is None or hi is None:
        first_m_lo, _ = _month_bounds(months[0])
        _, last_m_hi = _month_bounds(months[-1])
        lo = lo or first_m_lo
        hi = hi or last_m_hi

    with _lock:
        _uploaded.setdefault(financier, set()).update(months)
        _merge_span_locked(financier, lo, hi)
        _save_locked()


def sync_from_clickhouse(force: bool = False) -> bool:
    """
    Sync uploaded month tags and date spans from ClickHouse card tables so
    historical and pre-loaded data is always visible (including on Render / restarts).
    When force=True, rebuilds the in-memory state cleanly from ClickHouse.
    """
    global _ch_synced
    if _ch_synced and not force:
        return True
    try:
        from clickhouse_client import get_client
        client = get_client()
        found_months: dict[str, set] = {f: set() for f in _ALL_FINS}
        found_spans: dict[str, list] = {f: [] for f in _ALL_FINS}
        for fin, tbl in _FIN_TABLES:
            try:
                res = client.query(
                    f"SELECT data_month, min(toDate(txn_date)), max(toDate(txn_date)) "
                    f"FROM {tbl} WHERE data_month != '' GROUP BY data_month"
                )
                for dm, dmin, dmax in res.result_rows:
                    dm_str = str(dm or '').strip()
                    lo = _to_date(dmin)
                    hi = _to_date(dmax)
                    if dm_str and lo and hi:
                        found_months[fin].add(dm_str)
                        found_spans[fin].append((lo, hi))
            except Exception:
                continue

        with _lock:
            if force:
                for fin in _ALL_FINS:
                    _uploaded[fin] = set()
                    _spans[fin] = []
            for fin in _ALL_FINS:
                _uploaded.setdefault(fin, set()).update(found_months[fin])
                for lo, hi in found_spans[fin]:
                    _merge_span_locked(fin, lo, hi)
            _ch_synced = True
            _save_locked()
        return True
    except Exception:
        return False


def _ensure_synced() -> None:
    if not _ch_synced:
        sync_from_clickhouse(force=False)


def clear(financier: str = None) -> None:
    """Forget uploads for one financier, or for all of them when None."""
    global _ch_synced
    with _lock:
        for k in ([financier] if financier else list(_uploaded)):
            _uploaded.setdefault(k, set()).clear()
            _spans.setdefault(k, []).clear()
        _ch_synced = True
        _save_locked()


def is_uploaded(financier: str, data_month: str = None) -> bool:
    """True if a file was uploaded for `data_month` (or for any month when None)."""
    _ensure_synced()
    with _lock:
        months = _uploaded.get(financier, ())
        return bool(months) if data_month is None else data_month in months


def hidden_sql(data_month: str) -> str:
    """
    SQL fragment ('' or "AND financier NOT IN (...)") that hides every EMI financier
    not uploaded for `data_month`. Use inside a benow_pine_financier subquery.
    """
    hidden = [f for f in _ALL_FINS if not is_uploaded(f, data_month)]
    if not hidden:
        return ''
    return "AND financier NOT IN (" + ", ".join(f"'{h}'" for h in hidden) + ")"


# ---------------------------------------------------------------------------
# Date-range aware helpers (used by the dashboard queries)
# ---------------------------------------------------------------------------
def range_of(date_str: str, timeframe: str, end_date_str: str = None):
    """(first_date, last_date) covered by the dashboard's selected period."""
    d = pd.to_datetime(date_str).date()
    if timeframe == 'daily':
        return d, d
    if timeframe == 'monthly':
        return _month_bounds(d.strftime('%Y-%m'))
    if timeframe == 'quarterly':
        first = date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)
        return first, (pd.Timestamp(first) + pd.offsets.MonthEnd(3)).date()
    if timeframe == 'custom':
        end = pd.to_datetime(end_date_str).date() if end_date_str else d
        return (d, end) if d <= end else (end, d)
    return _DATE_MIN, _DATE_MAX


def overlapping_spans(financier: str, start: date, end: date) -> list:
    """Uploaded date spans of `financier` clipped to [start, end] (empty if none overlap)."""
    _ensure_synced()
    with _lock:
        spans = list(_spans.get(financier, ()))
    return [(max(lo, start), min(hi, end)) for lo, hi in spans if lo <= end and hi >= start]


def date_filter_sql(financier: str, start: date, end: date, column: str = 'txn_date'):
    """
    SQL boolean over `column` selecting only the uploaded part of [start, end] for
    `financier`; None when nothing was uploaded for that range.
    """
    clipped = overlapping_spans(financier, start, end)
    if not clipped:
        return None
    return "(" + " OR ".join(f"toDate({column}) BETWEEN '{lo}' AND '{hi}'" for lo, hi in clipped) + ")"


def bpf_filter_sql(start: date, end: date) -> str:
    """
    'AND ...' fragment for benow_pine_financier subqueries: keeps non-EMI financiers, and
    each EMI financier only if data overlapping [start, end] exists in ClickHouse
    (scoped strictly to the month tags overlapping [start, end]).
    """
    _ensure_synced()
    start_ym = start.strftime('%Y-%m') if start > _DATE_MIN else '0000-00'
    end_ym = end.strftime('%Y-%m') if end < _DATE_MAX else '9999-99'
    allowed = []
    with _lock:
        tags = {f: sorted(_uploaded.get(f, ())) for f in _ALL_FINS}
    for fin in _ALL_FINS:
        if tags[fin] and overlapping_spans(fin, start, end):
            scoped = [m for m in tags[fin] if start_ym <= m <= end_ym]
            if not scoped:
                scoped = tags[fin]
            months = ", ".join(f"'{m}'" for m in scoped)
            allowed.append(f"(financier = '{fin}' AND data_month IN ({months}))")
    if not allowed:
        return "AND 1 = 0"
    return "AND (" + " OR ".join(allowed) + ")"


def signature() -> tuple:
    """Hashable snapshot of the state, for use in cache keys."""
    _ensure_synced()
    with _lock:
        return (
            tuple(sorted((k, tuple(sorted(v))) for k, v in _uploaded.items())),
            tuple(sorted((k, tuple(sorted(v))) for k, v in _spans.items())),
        )


# Restore state saved by the current session so reloader restarts don't wipe uploaded files.
_load()
