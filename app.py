import os
import sys
import json
import time
import hashlib
import logging
import tempfile
import threading
from flask import Flask, jsonify, request, send_file, render_template, make_response
from datetime import datetime, timedelta

from excel_generator import generate_excel_report
from pptx_updater import update_pptx_slides
from sales_cleaner import clean_sales_report
from finance_report_generator import generate_finance_report
import pandas as pd
from dashboard_aggregator import get_dashboard_data
import emi_state

# Pine Labs EMI cleaner (optional — only needed when ClickHouse is available)
try:
    from pine_lab_cleaner import load_pine_lab_file, clean_pine_lab, upload_to_clickhouse as pine_upload_to_ch
    _PINELAB_AVAILABLE = True
except ImportError:
    _PINELAB_AVAILABLE = False

# Paytm EMI cleaner (same pipeline as Pine Labs, Paytm-specific rules)
try:
    from paytm_cleaner import load_paytm_file, clean_paytm, upload_to_clickhouse as paytm_upload_to_ch
    _PAYTM_AVAILABLE = True
except ImportError:
    _PAYTM_AVAILABLE = False

# Benow cleaner
try:
    from benow_cleaner import load_benow_file, clean_benow, upload_to_clickhouse as benow_upload_to_ch
    _BENOW_AVAILABLE = True
except ImportError:
    _BENOW_AVAILABLE = False

# --- ClickHouse integration (optional — portal works without it) ---
try:
    from clickhouse_client import (
        is_connected as ch_is_connected,
        quick_is_connected as ch_quick_is_connected,
        is_warming as ch_is_warming,
        set_warming as ch_set_warming,
        get_row_counts as ch_get_row_counts,
        cache_clear as ch_cache_clear,
        _QUERY_CACHE as _ch_cache,
    )
    from clickhouse_aggregator import (
        get_clickhouse_df,
        get_clickhouse_dashboard_rollups,
        get_clickhouse_brand_df,
        get_clickhouse_store_df,
        get_card_both_overrides,
        get_card_product_overrides,
        get_pine_lab_totals,
    )
    _CLICKHOUSE_AVAILABLE = True
except ImportError:
    _CLICKHOUSE_AVAILABLE = False

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, template_folder=os.path.join(basedir, 'templates'), static_folder=os.path.join(basedir, 'static'))
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable static file caching
logging.basicConfig(level=logging.INFO)

# --- Inbuilt AI Agent, Deep Insights & Dashboard Studio Blueprint ---
try:
    from ai_routes import ai_bp
    app.register_blueprint(ai_bp)
    app.logger.info("AI Agent Blueprint (/api/ai/*) registered.")
except Exception as _ai_exc:
    app.logger.warning("AI Agent Blueprint not loaded (optional): %s", _ai_exc)

# Holds path to the latest cleaned sales report (temp file)
_latest_cleaned_report_path = None
# Holds path to the latest finance detailed report (temp file)
_latest_finance_report_path = None
# Holds the latest cleaned Pine Lab DataFrame in memory for download
_latest_pinelab_cleaned_df = None
_latest_pinelab_main_df = None
_latest_pinelab_angamaly_df = None
_latest_pinelab_slots = {'main': None, 'angamaly': None, 'combined': None}
# Holds the latest cleaned Paytm DataFrame in memory for download
_latest_paytm_cleaned_df = None
_latest_benow_cleaned_df = None

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

_PINELAB_MAIN_PKL   = os.path.join(DATA_DIR, '_pinelab_main_cleaned.pkl')
_PINELAB_ANG_PKL    = os.path.join(DATA_DIR, '_pinelab_angamaly_cleaned.pkl')
_PINELAB_SLOTS_JSON = os.path.join(DATA_DIR, '_pinelab_slots_meta.json')

def _save_pinelab_slots():
    try:
        with open(_PINELAB_SLOTS_JSON, 'w', encoding='utf-8') as f:
            json.dump(_latest_pinelab_slots, f)
    except Exception:
        pass

def _load_pinelab_cache_if_needed():
    global _latest_pinelab_main_df, _latest_pinelab_angamaly_df, _latest_pinelab_cleaned_df, _latest_pinelab_slots
    if not emi_state.is_uploaded(emi_state.PINE_FIN):
        return
    if _latest_pinelab_slots.get('main') is None and _latest_pinelab_slots.get('angamaly') is None:
        if os.path.exists(_PINELAB_SLOTS_JSON):
            try:
                with open(_PINELAB_SLOTS_JSON, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        _latest_pinelab_slots.update(loaded)
            except Exception:
                pass
    if _latest_pinelab_main_df is None and os.path.exists(_PINELAB_MAIN_PKL):
        try:
            _latest_pinelab_main_df = pd.read_pickle(_PINELAB_MAIN_PKL)
        except Exception:
            pass
    if _latest_pinelab_main_df is None and isinstance(_latest_pinelab_slots.get('main'), dict):
        src_p = _latest_pinelab_slots['main'].get('source_path')
        if src_p and os.path.exists(src_p) and _PINELAB_AVAILABLE:
            try:
                _df_m, _st_m = clean_pine_lab(pd.read_csv(src_p))
                _latest_pinelab_main_df = _df_m
                _df_m.to_pickle(_PINELAB_MAIN_PKL)
            except Exception:
                pass
    if _latest_pinelab_angamaly_df is None and os.path.exists(_PINELAB_ANG_PKL):
        try:
            _latest_pinelab_angamaly_df = pd.read_pickle(_PINELAB_ANG_PKL)
        except Exception:
            pass
    if _latest_pinelab_cleaned_df is None:
        frames = [d for d in (_latest_pinelab_main_df, _latest_pinelab_angamaly_df) if d is not None and not d.empty]
        if frames:
            from pine_lab_cleaner import COL_TXN_ID
            _latest_pinelab_cleaned_df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=[COL_TXN_ID], keep='first').copy()

def _clear_pinelab_cache():
    global _latest_pinelab_main_df, _latest_pinelab_angamaly_df, _latest_pinelab_cleaned_df, _latest_pinelab_slots
    _latest_pinelab_main_df = None
    _latest_pinelab_angamaly_df = None
    _latest_pinelab_cleaned_df = None
    _latest_pinelab_slots = {'main': None, 'angamaly': None, 'combined': None}
    for p in (_PINELAB_MAIN_PKL, _PINELAB_ANG_PKL, _PINELAB_SLOTS_JSON):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

# PPTX template: use env var (cloud) or fallback to local Windows path
TEMPLATE_PPTX_PATH = os.environ.get(
    'TEMPLATE_PPTX_PATH',
    r"c:\Users\rahul_myg\Downloads\Weekly TM review Template (1).pptx"
)

@app.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# ClickHouse health endpoint
# ---------------------------------------------------------------------------
@app.route('/api/clickhouse/status', methods=['GET'])
def api_clickhouse_status():
    """Returns ClickHouse connection status, live row counts, and cache info."""
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({
            'connected': False,
            'error': 'clickhouse-connect or python-dotenv not installed.'
        }), 503
    try:
        connected = ch_quick_is_connected()
        warming = ch_is_warming()
        if not connected and not warming:
            start_background_prewarm()
            warming = ch_is_warming()
        counts = ch_get_row_counts() if connected else {}
        cache_entries = len(_ch_cache) if _CLICKHOUSE_AVAILABLE else 0
        emi_sig = repr(emi_state.signature(sync=connected))
        return jsonify({
            'connected': connected,
            'warming': bool(not connected and warming),
            'row_counts': counts,
            'cache_entries': cache_entries,
            'emi_version': hashlib.md5(emi_sig.encode()).hexdigest()[:12],
        })
    except Exception as exc:
        app.logger.error('ClickHouse status check failed: %s', exc)
        return jsonify({'connected': False, 'error': str(exc)}), 500


@app.route('/api/clickhouse/cache-clear', methods=['POST'])
def api_clickhouse_cache_clear():
    """Flush the in-memory ClickHouse query cache."""
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'error': 'ClickHouse not available.'}), 503
    cleared = ch_cache_clear()
    try:
        import store_fin_share
        store_fin_share.invalidate_cache()
    except Exception:
        pass
    return jsonify({'status': 'success', 'cleared': cleared})


_prewarm_lock = threading.Lock()
_prewarm_seq = 0


@app.route('/api/clickhouse/prewarm', methods=['POST'])
def api_clickhouse_prewarm():
    """
    Polite debounced background cache warm for a selected date.
    Waits briefly so the user's active request finishes first, aborts if superseded
    by a newer click, and avoids unprompted heavy quarterly scans.

    Body: { "date": "YYYY-MM-DD", "timeframe": "daily|monthly|quarterly|custom" }
    """
    global _prewarm_seq
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'skipped', 'reason': 'ClickHouse not available'}), 200

    data = request.get_json(silent=True) or {}
    date_str = data.get('date')
    active_tf = str(data.get('timeframe') or '').strip().lower()
    end_date_str = request.args.get('end_date') or data.get('end_date') or request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date is required'}), 400

    targets = [tf for tf in ('monthly', 'daily') if tf != active_tf]
    if not targets:
        return jsonify({'status': 'skipped', 'reason': 'already warming active timeframe'}), 200

    with _prewarm_lock:
        _prewarm_seq += 1
        my_seq = _prewarm_seq

    def _warm_seq(d, end_d, seq_id, tfs):
        time.sleep(2.5)
        for tf in tfs:
            if seq_id != _prewarm_seq:
                return
            try:
                get_clickhouse_dashboard_rollups(d, tf, end_date_str=end_d)
                app.logger.info('Prewarm complete: %s / %s', d, tf)
            except Exception as exc:
                app.logger.warning('Prewarm failed %s/%s: %s', d, tf, exc)

    threading.Thread(target=_warm_seq, args=(date_str, end_date_str, my_seq, targets), daemon=True).start()

    return jsonify({'status': 'warming', 'date': date_str, 'timeframes': targets})


@app.route('/api/targets', methods=['GET', 'POST'])
def api_targets():
    targets_file = os.path.join(DATA_DIR, 'targets.json')
    if request.method == 'POST':
        data = request.json
        with open(targets_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return jsonify({"status": "success", "message": "Targets updated successfully"})
    
    # GET method
    if os.path.exists(targets_file):
        with open(targets_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    else:
        # Return empty default structures if targets file not found
        return jsonify({
            "exec_summary_targets": {
                "value_penetration": 67.0,
                "vol_penetration": 55.0,
                "cards_penetration": 6.91,
                "fin_value_cr": 460.0,
                "card_value_cr": 47.41
            },
            "financier_volume_targets": {
                "BAJAJ": 88779, "HDB": 6341, "IDFC": 4488, "PFL": 134, "TVS": 1574, "DMI": 1708,
                "PinelAb": 42.4, "PayTm": 1.7, "Benow": 3.3
            },
            "dmi_contest_brand_targets": { "SAMSUNG": 72, "VIVO": 72, "OPPO": 72 },
            "manpower_plans": { "BAJAJ": 275, "HDB": 180, "IDFC": 120, "TVS": 35, "PFL": 29 }
        })

@app.route('/api/reviews', methods=['GET'])
def api_reviews():
    files = os.listdir(DATA_DIR)
    reviews = []
    for f in files:
        if f.startswith('weekly_data_') and f.endswith('.json'):
            date_str = f.replace('weekly_data_', '').replace('.json', '')
            reviews.append(date_str)
    # Sort reviews in descending order (newest first)
    reviews.sort(reverse=True)
    return jsonify(reviews)

@app.route('/api/dashboard-data', methods=['GET'])
def api_dashboard_data():
    date_str = request.args.get('date')
    end_date_str = request.args.get('end_date') or (request.is_json and request.get_json(silent=True).get('end_date'))
    timeframe = request.args.get('timeframe', 'daily')
    rbm = request.args.get('rbm', '')
    bdm = request.args.get('bdm', '')
    store = request.args.get('store', '')
    staff = request.args.get('staff', '')
    fin_bdm = request.args.get('fin_bdm', '')
    state = request.args.get('state', '')
    district = request.args.get('district', '')
    source = request.args.get('source', 'excel')  # 'excel' | 'clickhouse'
    
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    # --- ClickHouse path ---
    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available on this server.'}), 503
        try:
            # Result-level cache: the SQL result was already cached, but the pandas aggregation
            # (several seconds on a monthly range) was recomputed on every request.
            from clickhouse_client import _cache_get, _cache_set, cache_clear as ch_cache_clear
            if str(request.args.get('refresh', '')).lower() in ('1', 'true', 'yes'):
                ch_cache_clear()
            _tf = os.path.join(DATA_DIR, 'targets.json')
            _tf_mtime = os.path.getmtime(_tf) if os.path.exists(_tf) else 0
            _ckey = 'dash|' + repr((date_str, timeframe, end_date_str, rbm, bdm, store, staff, fin_bdm, state, district, _tf_mtime, emi_state.signature()))
            _hit = _cache_get(_ckey)
            if _hit is not None:
                return jsonify(_hit)
            rollups = get_clickhouse_dashboard_rollups(date_str, timeframe, rbm, bdm, store, staff, end_date_str=end_date_str, fin_bdm=fin_bdm, state=state, district=district)
            if not rollups or rollups[0].empty:
                return jsonify({'error': 'No data found in ClickHouse for the selected timeframe.'}), 404
            # Load zone_map for RBM→Zone grouping
            zone_map = {}
            targets_file = os.path.join(DATA_DIR, 'targets.json')
            if os.path.exists(targets_file):
                try:
                    with open(targets_file, 'r', encoding='utf-8') as tf:
                        zone_map = json.load(tf).get('zone_map', {})
                except Exception:
                    pass
            # PineLab / PAYTM / Benow = counts from the manually uploaded files; they are
            # deducted from aCASH.  The files carry no store info, so the full file counts are
            # used when no store-level filter is active (State handled in get_card_overrides).
            pine_lab_override = None
            paytm_override = None
            benow_override = None
            pine_lab_val_override = None
            paytm_val_override = None
            benow_val_override = None
            card_prod_qty_override = None
            card_prod_val_override = None
            if not staff:
                try:
                    _ov, _vov = get_card_both_overrides(
                        date_str, timeframe, end_date_str,
                        state=state, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=district
                    )
                    pine_lab_override = _ov.get('PineLab')
                    paytm_override = _ov.get('PAYTM')
                    benow_override = _ov.get('Benow')
                    pine_lab_val_override = _vov.get('PineLab')
                    paytm_val_override = _vov.get('PAYTM')
                    benow_val_override = _vov.get('Benow')
                    card_prod_qty_override, card_prod_val_override = get_card_product_overrides(
                        date_str, timeframe, end_date_str,
                        state=state, rbm=rbm, bdm=bdm, store=store, fin_bdm=fin_bdm, district=district
                    )
                except Exception as _ov_exc:
                    app.logger.warning('Card EMI override failed: %s', _ov_exc)
            # Reuse existing aggregation logic, passing ClickHouse grouped rollups
            data = get_dashboard_data(
                raw_file_path=None, target_date_str=date_str, timeframe=timeframe,
                rbm=rbm, bdm=bdm, store=store, staff=staff, zone_map=zone_map, df=None,
                end_date_str=end_date_str,
                pine_lab_override=pine_lab_override,
                paytm_override=paytm_override,
                benow_override=benow_override,
                pine_lab_val_override=pine_lab_val_override,
                paytm_val_override=paytm_val_override,
                benow_val_override=benow_val_override,
                card_prod_qty_override=card_prod_qty_override,
                card_prod_val_override=card_prod_val_override,
                state=state, district=district,
                ch_rollups=rollups
            )
            for _fk, _ek in (('PineLab', emi_state.PINE_FIN), ('PAYTM', emi_state.PAYTM_FIN), ('Benow', emi_state.BENOW_FIN)):
                if not emi_state.is_uploaded(_ek, sync=emi_state._ch_synced) and "category_performance" in data:
                    _cp = data["category_performance"]
                    for _c, _row in (_cp.get("qty") or {}).items():
                        if isinstance(_row, dict) and _row.get(_fk):
                            _row["aCASH"] = int(_row.get("aCASH", 0) or 0) + int(_row.get(_fk, 0) or 0)
                            _row[_fk] = 0
                    for _c, _row in (_cp.get("value") or {}).items():
                        if isinstance(_row, dict): _row[_fk] = 0.0
                    for _pobj in filter(None, [_cp.get("products_qty"), (_cp.get("products") or {}).get("qty")]):
                        for _c, _prods in _pobj.items():
                            for _p, _pr in (_prods or {}).items():
                                if isinstance(_pr, dict) and _pr.get(_fk):
                                    _pr["aCASH"] = int(_pr.get("aCASH", 0) or 0) + int(_pr.get(_fk, 0) or 0)
                                    _pr[_fk] = 0
                    for _vobj in filter(None, [_cp.get("products_val"), (_cp.get("products") or {}).get("value")]):
                        for _c, _prods in _vobj.items():
                            for _p, _pr in (_prods or {}).items():
                                if isinstance(_pr, dict): _pr[_fk] = 0.0
            if isinstance(data, dict) and 'error' not in data:
                _cache_set(_ckey, data)
            return jsonify(data)
        except Exception as exc:
            app.logger.error('ClickHouse dashboard query failed: %s', exc, exc_info=True)
            return jsonify({'error': f'ClickHouse error: {str(exc)}'}), 500

    # --- Excel (default) path ---
    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet. Please upload it in Data Cleaner."}), 404
        
    # Load zone_map from targets.json for RBM→Zone grouping
    zone_map = {}
    targets_file = os.path.join(DATA_DIR, 'targets.json')
    if os.path.exists(targets_file):
        try:
            with open(targets_file, 'r', encoding='utf-8') as tf:
                zone_map = json.load(tf).get('zone_map', {})
        except Exception:
            pass
    
    data = get_dashboard_data(raw_file, date_str, timeframe, rbm, bdm, store, staff, zone_map=zone_map, end_date_str=end_date_str, state=state, district=district)
    return jsonify(data)

@app.route('/api/price-range-data', methods=['GET'])
def api_price_range_data():
    from dashboard_aggregator import get_price_range_data
    date_str    = request.args.get('date')
    end_date_str= request.args.get('end_date', '')
    timeframe   = request.args.get('timeframe', 'monthly')
    price_range = request.args.get('price_range', 'ALL')
    state       = request.args.get('state', '')
    source      = request.args.get('source', 'excel')
    
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            ch_df = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str, state=state)
            if ch_df.empty:
                return jsonify({'error': 'No ClickHouse data for this timeframe.'}), 404
            return jsonify(get_price_range_data(None, date_str, timeframe, price_range, df=ch_df, end_date_str=end_date_str, state=state))
        except Exception as exc:
            app.logger.error('ClickHouse price-range query failed: %s', exc, exc_info=True)
            return jsonify({'error': str(exc)}), 500
    
    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet."}), 404
    
    data = get_price_range_data(raw_file, date_str, timeframe, price_range, end_date_str=end_date_str, state=state)
    return jsonify(data)

@app.route('/api/branch-districts', methods=['GET'])
def api_branch_districts():
    """Map of branch code -> district, used to narrow the Store dropdown by District."""
    try:
        import clickhouse_client
        df = clickhouse_client.query_df("SELECT upper(trim(code)) AS code, upper(trim(district)) AS district FROM branch_master")
        dists = {str(r['code']): str(r['district']).strip()
                 for r in df.to_dict('records') if str(r['district']).strip()}
        for b in ['CSB', 'GPF', '3GH', 'KSO', 'SMC']:
            dists[b] = 'OTHERS'
        return jsonify(dists)
    except Exception:
        return jsonify({})

@app.route('/api/branch-names', methods=['GET'])
def api_branch_names():
    """Map of branch code -> branch name, used to show names in the Store dropdown."""
    try:
        import clickhouse_client
        df = clickhouse_client.query_df("SELECT upper(trim(code)) AS code, trim(branch_name) AS name FROM branch_master")
        names = {str(r['code']): str(r['name']).strip().upper()
                 for r in df.to_dict('records') if str(r['name']).strip()}
        for b in ['CSB', 'GPF', '3GH', 'KSO', 'SMC']:
            if b not in names:
                names[b] = b
        return jsonify(names)
    except Exception as e:
        return jsonify({})

@app.route('/api/brand-data', methods=['GET'])
def api_brand_data():
    from dashboard_aggregator import get_brand_data
    date_str  = request.args.get('date')
    end_date_str = request.args.get('end_date', '')
    timeframe = request.args.get('timeframe', 'monthly')
    category  = request.args.get('category', 'ALL')
    product   = request.args.get('product', 'ALL')
    source    = request.args.get('source', 'excel')
    rbm       = request.args.get('rbm', '')
    bdm       = request.args.get('bdm', '')
    store     = request.args.get('store', '')
    staff     = request.args.get('staff', '')
    fin_bdm   = request.args.get('fin_bdm', '')
    state     = request.args.get('state', '')
    district  = request.args.get('district', '')
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            from clickhouse_client import _cache_get, _cache_set
            _bkey = 'brand|' + repr((date_str, timeframe, end_date_str, category, product, rbm, bdm, store, staff, fin_bdm, state, district))
            _bhit = _cache_get(_bkey)
            if _bhit is not None:
                return jsonify(_bhit)
            ch_df = get_clickhouse_brand_df(date_str, timeframe, rbm, bdm, store, staff, end_date_str=end_date_str, fin_bdm=fin_bdm, state=state, district=district, category=category, product=product)
            if ch_df.empty:
                return jsonify({'error': 'No ClickHouse data for this timeframe.'}), 404
            bdata = get_brand_data(None, date_str, timeframe, category, product, df=ch_df, end_date_str=end_date_str, state=state)
            if isinstance(bdata, dict) and 'error' not in bdata:
                _cache_set(_bkey, bdata)
            return jsonify(bdata)
        except Exception as exc:
            app.logger.error('ClickHouse brand query failed: %s', exc, exc_info=True)
            return jsonify({'error': str(exc)}), 500

    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet."}), 404
    return jsonify(get_brand_data(raw_file, date_str, timeframe, category, product, end_date_str=end_date_str, state=state))

@app.route('/api/store-data', methods=['GET'])
def api_store_data():
    from dashboard_aggregator import get_store_data
    date_str  = request.args.get('date')
    end_date_str = request.args.get('end_date', '')
    timeframe = request.args.get('timeframe', 'monthly')
    zone      = request.args.get('zone', 'ALL')
    state     = request.args.get('state', '')
    source    = request.args.get('source', 'excel')
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            from clickhouse_client import _cache_get, _cache_set
            _tf = os.path.join(DATA_DIR, 'targets.json')
            _tf_mtime = os.path.getmtime(_tf) if os.path.exists(_tf) else 0
            _skey = 'store|' + repr((date_str, timeframe, end_date_str, zone, state, _tf_mtime))
            _shit = _cache_get(_skey)
            if _shit is not None:
                return jsonify(_shit)
            ch_df = get_clickhouse_store_df(date_str, timeframe, end_date_str=end_date_str, state=state)
            if ch_df.empty:
                return jsonify({'error': 'No ClickHouse data for this timeframe.'}), 404
            # Load zone_map
            zone_map = {}
            targets_file = os.path.join(DATA_DIR, 'targets.json')
            if os.path.exists(targets_file):
                try:
                    with open(targets_file, 'r', encoding='utf-8') as tf:
                        zone_map = json.load(tf).get('zone_map', {})
                except Exception:
                    pass
            sdata = get_store_data(None, date_str, timeframe, zone, zone_map, df=ch_df, end_date_str=end_date_str, state=state)
            if isinstance(sdata, dict) and 'error' not in sdata:
                _cache_set(_skey, sdata)
            return jsonify(sdata)
        except Exception as exc:
            app.logger.error('ClickHouse store query failed: %s', exc, exc_info=True)
            return jsonify({'error': str(exc)}), 500

    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet."}), 404
    # Load zone_map
    zone_map = {}
    targets_file = os.path.join(DATA_DIR, 'targets.json')
    if os.path.exists(targets_file):
        try:
            with open(targets_file, 'r', encoding='utf-8') as tf:
                zone_map = json.load(tf).get('zone_map', {})
        except Exception:
            pass
    return jsonify(get_store_data(raw_file, date_str, timeframe, zone, zone_map, end_date_str=end_date_str, state=state))


@app.route('/api/oct-sheets-data', methods=['GET'])
def api_oct_sheets_data():
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'error': 'ClickHouse not available.'}), 503
    date_str = request.args.get('date') or '2026-10-01'
    end_date_str = request.args.get('end_date', '')
    timeframe = request.args.get('timeframe', 'monthly')
    period_mode = request.args.get('period_mode', 'reference')
    state = request.args.get('state', '')
    try:
        from store_fin_share import get_store_fin_share_data
        return jsonify(get_store_fin_share_data(date_str, timeframe=timeframe, end_date_str=end_date_str, period_mode=period_mode, state=state))
    except Exception as exc:
        app.logger.error('api_oct_sheets_data failed: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/oct-sheets-compare', methods=['GET'])
def api_oct_sheets_compare():
    """Store rows for the window one day / week / month / year before the selected period."""
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'error': 'ClickHouse not available.'}), 503
    try:
        from store_fin_share import get_store_fin_share_compare
        out = get_store_fin_share_compare(
            request.args.get('date') or '2026-10-01',
            timeframe=request.args.get('timeframe', 'monthly'),
            end_date_str=request.args.get('end_date', ''),
            period_mode=request.args.get('period_mode', 'reference'),
            state=request.args.get('state', ''),
            compare=request.args.get('compare', 'month'),
            compare_start=request.args.get('compare_start', ''),
            compare_end=request.args.get('compare_end', ''),
        )
        return jsonify(out), (400 if out.get('error') else 200)
    except Exception as exc:
        app.logger.error('api_oct_sheets_compare failed: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/oct-sheets-staff', methods=['GET'])
def api_oct_sheets_staff():
    """Per-staff Value + Volume for an explicit date range."""
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'error': 'ClickHouse not available.'}), 503
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '') or start_date
    if not start_date:
        return jsonify({'error': 'start_date is required'}), 400
    try:
        from store_fin_share import get_staff_fin_share
        return jsonify(get_staff_fin_share(start_date[:10], end_date[:10], request.args.get('state', '')))
    except Exception as exc:
        app.logger.error('api_oct_sheets_staff failed: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/tm-targets', methods=['GET', 'POST'])
def api_tm_targets():
    """Monthly TM targets (value target, card slab 1/2) stored in ClickHouse table tm_fin_targets."""
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'error': 'ClickHouse not available.'}), 503
    try:
        from store_fin_share import load_targets, save_targets
        if request.method == 'POST':
            body = request.get_json(force=True) or {}
            n = save_targets(str(body.get('month', '')), body.get('rows') or [])
            return jsonify({'status': 'success', 'saved': n})
        return jsonify(load_targets(request.args.get('month', '')))
    except Exception as exc:
        app.logger.error('api_tm_targets failed: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500

@app.route('/api/review/<date>', methods=['GET', 'POST'])
def api_review(date):
    file_path = os.path.join(DATA_DIR, f'weekly_data_{date}.json')
    
    if request.method == 'POST':
        data = request.json
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return jsonify({"status": "success", "message": f"Review data for {date} saved successfully"})
    
    # GET method
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            # Backwards compatibility: inject products if missing
            if "category_performance" in data and "products" not in data["category_performance"]:
                data["category_performance"]["products"] = {"qty": {}, "value": {}}
                PRODUCT_CAT_MAP = {
                    'CD PRODUCTS': ['AIR CONDITIONER', 'DISH WASHER', 'DRYER', 'FREEZER', 'HOME APPLIANCES', 'MICROWAVE OVEN', 'REFRIGERATORS', 'TV', 'WASHING MACHINES'],
                    'HVA': ['AUDIO', 'EAR WEARABLES', 'GAMING', 'HOME THEATRE', 'SMART WATCH'],
                    'LAPTOP': ['LAPTOP', 'DESKTOP'],
                    'MOBILE': ['MOBILE', 'TABLET'],
                    'SA': ['SMALL APPLIANCES'],
                    'OTHERS': ['CAMERA', 'DIY', 'IT ACCESSORIES', 'PRINTER', 'STABILIZER', 'MYG VERSE', 'RIG'],
                    'BANK EMI': []
                }
                for cat in ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]:
                    data["category_performance"]["products"]["qty"][cat] = {}
                    data["category_performance"]["products"]["value"][cat] = {}
                    prods = PRODUCT_CAT_MAP.get(cat, [])
                    for i, prod in enumerate(prods):
                        if i == 0:
                            # Preserve existing category totals into the first product
                            data["category_performance"]["products"]["qty"][cat][prod] = dict(data["category_performance"].get("qty", {}).get(cat, {}))
                            data["category_performance"]["products"]["value"][cat][prod] = dict(data["category_performance"].get("value", {}).get(cat, {}))
                        else:
                            data["category_performance"]["products"]["qty"][cat][prod] = {
                                "aCASH": 0, "BAJAJ": 0, "HDB": 0, "IDFC": 0, "PFL": 0, "TVS": 0, "DMI": 0, "PineLab": 0, "PAYTM": 0, "Benow": 0, "last_week_pct": 0.0, "apr_closing_pct": 0.0
                            }
                            data["category_performance"]["products"]["value"][cat][prod] = {
                                "aCASH": 0.0, "BAJAJ": 0.0, "HDB": 0.0, "IDFC": 0.0, "PFL": 0.0, "TVS": 0.0, "DMI": 0.0, "PineLab": 0.0, "PAYTM": 0.0, "Benow": 0.0, "last_week_pct": 0.0, "apr_closing_pct": 0.0
                            }
                        
            for _fk, _ek in (('PineLab', emi_state.PINE_FIN), ('PAYTM', emi_state.PAYTM_FIN), ('Benow', emi_state.BENOW_FIN)):
                if not emi_state.is_uploaded(_ek, sync=emi_state._ch_synced) and "category_performance" in data:
                    _cp = data["category_performance"]
                    for _c, _row in (_cp.get("qty") or {}).items():
                        if isinstance(_row, dict) and _row.get(_fk):
                            _row["aCASH"] = int(_row.get("aCASH", 0) or 0) + int(_row.get(_fk, 0) or 0)
                            _row[_fk] = 0
                    for _c, _row in (_cp.get("value") or {}).items():
                        if isinstance(_row, dict): _row[_fk] = 0.0
                    for _pobj in filter(None, [_cp.get("products_qty"), (_cp.get("products") or {}).get("qty")]):
                        for _c, _prods in _pobj.items():
                            for _p, _pr in (_prods or {}).items():
                                if isinstance(_pr, dict) and _pr.get(_fk):
                                    _pr["aCASH"] = int(_pr.get("aCASH", 0) or 0) + int(_pr.get(_fk, 0) or 0)
                                    _pr[_fk] = 0
                    for _vobj in filter(None, [_cp.get("products_val"), (_cp.get("products") or {}).get("value")]):
                        for _c, _prods in _vobj.items():
                            for _p, _pr in (_prods or {}).items():
                                if isinstance(_pr, dict): _pr[_fk] = 0.0
            return jsonify(data)
    else:
        # Let's check if we can initialize from a template or find the "Last Week" data
        # To make it user-friendly, let's create a blank or semi-filled dataset
        # We can pull from the targets
        targets_file = os.path.join(DATA_DIR, 'targets.json')
        targets = {}
        if os.path.exists(targets_file):
            with open(targets_file, 'r', encoding='utf-8') as f:
                targets = json.load(f)
        
        # Try to find last week's data to copy last_week stats
        prev_date_str = ""
        try:
            current_date = datetime.strptime(date, "%Y-%m-%d")
            prev_date = current_date - timedelta(days=7)
            prev_date_str = prev_date.strftime("%Y-%m-%d")
        except Exception:
            pass
            
        prev_data = {}
        if prev_date_str:
            prev_file = os.path.join(DATA_DIR, f'weekly_data_{prev_date_str}.json')
            if os.path.exists(prev_file):
                with open(prev_file, 'r', encoding='utf-8') as pf:
                    prev_data = json.load(pf)

        # Build blank data template
        blank_data = create_blank_data_template(date, targets, prev_data)
        return jsonify(blank_data)

def create_blank_data_template(date, targets, prev_data):
    # Helper to generate a default skeleton for a review date
    est = targets.get("exec_summary_targets", {})
    fvt = targets.get("financier_volume_targets", {})
    dbt = targets.get("dmi_contest_brand_targets", {})
    mwp = targets.get("manpower_plans", {})
    
    # Default values for last week if prev_data is not available
    prev_est_ach = prev_data.get("exec_summary", {}).get("target_achievement", {}).get("achievement", [0, 0, 0, 0, 0]) if prev_data else [0, 0, 0, 0, 0]
    
    # Build skeleton
    template = {
      "date": date,
      "status_as_of": date, # Default same
      "exec_summary": {
        "target_achievement": {
          "headers": ["Metric", "Value Penetration", "Vol Penetration", "Cards Penetration", "Fin Value (Cr)", "Card Value (Cr)"],
          "target": [
              est.get("value_penetration", 67.0),
              est.get("vol_penetration", 55.0),
              est.get("cards_penetration", 6.91),
              est.get("fin_value_cr", 460.0),
              est.get("card_value_cr", 47.41)
          ],
          "achievement": [0.0, 0.0, 0.0, 0.0, 0.0],
          "last_week": prev_est_ach,
          "last_month": [0.0, 0.0, 0.0, 0.0, 0.0],
          "qtr_trend": [None, None, None, 0.0, 0.0]
        },
        "monthwise_performance": {
          "months": ["Apr", "May", "Jun"],
          "fin_value_pct": [0.0, 0.0, 0.0],
          "fin_vol_pct": [0.0, 0.0, 0.0],
          "card_pct": [0.0, 0.0, 0.0],
          "fin_value_cr": [0.0, 0.0, 0.0],
          "card_value_cr": [0.0, 0.0, 0.0]
        }
      },
      "disbursement": {
        "financiers": {
          "BAJAJ": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 },
          "HDB": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 },
          "IDFC": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 },
          "PFL": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 },
          "TVS": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 },
          "DMI": { "sourcing": 0, "pending": 0, "last_week_pending_pct": 0.0 }
        },
        "top_stores_aged": [
          { "sno": 1, "store": "", "days": 0, "contributor": "", "count": 0 },
          { "sno": 2, "store": "", "days": 0, "contributor": "", "count": 0 },
          { "sno": 3, "store": "", "days": 0, "contributor": "", "count": 0 },
          { "sno": 4, "store": "", "days": 0, "contributor": "", "count": 0 },
          { "sno": 5, "store": "", "days": 0, "contributor": "", "count": 0 }
        ],
        "ageing_status": {
          "BAJAJ FINANCE (DPF & CD)": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] },
          "HDB FINANCE": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] },
          "IDFC FIRST BANK LTD": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] },
          "TVS CREDIT": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] },
          "DMI FINANCE": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] },
          "POONAWALLA FINCORP": { "4_7_days": [0, 0], "8_15_days": [0, 0], "15_plus": [0, 0], "90_plus": [0, 0], "last_week": [0, 0] }
        }
      },
      "category_performance": {
        "qty": {},
        "value": {}
      },
      "financier_targets": {},
      "dmi_contest": {
        "summary": {
          "SAMSUNG": { "target": dbt.get("SAMSUNG", 72), "achievement": 0 },
          "VIVO": { "target": dbt.get("VIVO", 72), "achievement": 0 },
          "OPPO": { "target": dbt.get("OPPO", 72), "achievement": 0 }
        },
        "branches": {}
      },
      "manpower": {}
    }
    
    # Initialize categories and products
    template["category_performance"]["products"] = {"qty": {}, "value": {}}
    
    PRODUCT_CAT_MAP = {
        'CD PRODUCTS': ['AIR CONDITIONER', 'DISH WASHER', 'DRYER', 'FREEZER', 'HOME APPLIANCES', 'MICROWAVE OVEN', 'REFRIGERATORS', 'TV', 'WASHING MACHINES'],
        'HVA': ['AUDIO', 'EAR WEARABLES', 'GAMING', 'HOME THEATRE', 'SMART WATCH'],
        'LAPTOP': ['LAPTOP', 'DESKTOP'],
        'MOBILE': ['MOBILE', 'TABLET'],
        'SA': ['SMALL APPLIANCES'],
        'OTHERS': ['CAMERA', 'DIY', 'IT ACCESSORIES', 'PRINTER', 'STABILIZER', 'MYG VERSE', 'RIG'],
        'BANK EMI': []
    }
    
    for cat in ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]:
        template["category_performance"]["qty"][cat] = {
            "aCASH": 0, "BAJAJ": 0, "HDB": 0, "IDFC": 0, "PFL": 0, "TVS": 0, "DMI": 0, "PineLab": 0, "PAYTM": 0, "Benow": 0,
            "last_week_pct": 0.0, "apr_closing_pct": 0.0
        }
        template["category_performance"]["value"][cat] = {
            "aCASH": 0.0, "BAJAJ": 0.0, "HDB": 0.0, "IDFC": 0.0, "PFL": 0.0, "TVS": 0.0, "DMI": 0.0, "PineLab": 0.0, "PAYTM": 0.0, "Benow": 0.0,
            "last_week_pct": 0.0, "apr_closing_pct": 0.0
        }
        
        template["category_performance"]["products"]["qty"][cat] = {}
        template["category_performance"]["products"]["value"][cat] = {}
        for prod in PRODUCT_CAT_MAP.get(cat, []):
            template["category_performance"]["products"]["qty"][cat][prod] = {
                "aCASH": 0, "BAJAJ": 0, "HDB": 0, "IDFC": 0, "PFL": 0, "TVS": 0, "DMI": 0, "PineLab": 0, "PAYTM": 0, "Benow": 0,
                "last_week_pct": 0.0, "apr_closing_pct": 0.0
            }
            template["category_performance"]["products"]["value"][cat][prod] = {
                "aCASH": 0.0, "BAJAJ": 0.0, "HDB": 0.0, "IDFC": 0.0, "PFL": 0.0, "TVS": 0.0, "DMI": 0.0, "PineLab": 0.0, "PAYTM": 0.0, "Benow": 0.0,
                "last_week_pct": 0.0, "apr_closing_pct": 0.0
            }
        
    # Initialize financier targets
    for fin, tgt in fvt.items():
        template["financier_targets"][fin] = { "target": tgt, "achievement": 0 }
        
    # Initialize branch contest details (pull branch names from default if possible)
    # If not, let's pre-populate the 18+ branches from our seeded data
    default_branches = ["BALUSSERY", "BATHERY FUTURE", "KALPETTA", "KOYILANDY", "KOYILANDY 2", "KUTTIYADI", "MANANTHAVADY",
                        "NARIKKUNI", "PERAMBRA", "VADAKARA", "BALUSSERY FUTURE", "KUTTIYADI FUTURE", "VADAKARA FUTURE",
                        "PERAMBRA FUTURE", "THAMARASSERY FUTURE", "myG CARE BATHERY", "MANANTHAVADY FUTURE", "CAFE YMCA",
                        "P TOWER", "HI LITE MALL", "KINGSWAY", "KUNNAMANGALAM FUTURE", "LANDSHIP MALL", "MUKKOM",
                        "CAMWORLD", "POTTAMMAL FUTURE", "PUTHIYARA", "RAMANATTUKARA", "SABA", "SABA MAHARANI", "FEROKE FUTURE",
                        "myG CARE PERUMANNA", "NADAKKAVU FUTURE", "THONDAYAD FUTURE", "BYPASS ROAD", "CHEMMAD FUTURE",
                        "EDAKKARA", "EDAPPAL", "KONDOTTY", "KOTTAKKAL", "KOTTAKKAL FUTURE", "MALAPPURAM", "MANJERY",
                        "OTTAPALAM", "PERINTHALMANNA FUTURE", "TB ROAD", "TIRUR", "VADAKKENCHERRY FUTURE", "VALANCHERY FUTURE",
                        "VENGARA", "WANDOOR", "MANNARKAD FUTURE", "AREEKODE FUTURE", "NILAMBUR FUTURE", "PALAKKAD FUTURE",
                        "PATTAMBI FUTURE", "EDAPPAL FUTURE", "KONDOTTY FUTURE", "BANK ROAD", "IRITTY", "KANHANGAD",
                        "KANHANGAD FUTURE", "KASARGOD", "MAHE", "PAYYANNUR", "PAYYANNUR FUTURE", "SNPARK", "TALIPARAMBA",
                        "THALASSERY", "UPPALA", "THANA", "KANNUR FUTURE", "THALASSERY FUTURE", "KASARGOD FUTURE"]
    
    for br in default_branches:
        template["dmi_contest"]["branches"][br] = { "OPPO": 0, "SAMSUNG": 0, "VIVO": 0 }
        
    # Initialize manpower status
    for fin, plan in mwp.items():
        template["manpower"][fin] = { "plan": plan, "current": 0 }
        
    return template

@app.route('/api/export/excel/<date>', methods=['GET'])
def api_export_excel(date):
    file_path = os.path.join(DATA_DIR, f'weekly_data_{date}.json')
    if not os.path.exists(file_path):
        return make_response(f"Data for {date} not found. Please save first.", 404)
        
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Generate the excel report path
    output_filename = f"Weekly_TM_Review_{date}.xlsx"
    output_path = os.path.join(DATA_DIR, output_filename)
    
    try:
        generate_excel_report(data, output_path)
        return send_file(output_path, as_attachment=True, download_name=output_filename)
    except Exception as e:
        app.logger.error(f"Error generating Excel: {e}", exc_info=True)
        return make_response(f"Failed to generate Excel report: {str(e)}", 500)

@app.route('/api/export/pptx/<date>', methods=['GET'])
def api_export_pptx(date):
    file_path = os.path.join(DATA_DIR, f'weekly_data_{date}.json')
    if not os.path.exists(file_path):
        return make_response(f"Data for {date} not found. Please save first.", 404)
        
    if not os.path.exists(TEMPLATE_PPTX_PATH):
        return make_response(f"PowerPoint Template slide deck not found at {TEMPLATE_PPTX_PATH}.", 404)
        
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Generate the PPTX report path
    output_filename = f"Weekly_TM_Review_{date}.pptx"
    output_path = os.path.join(DATA_DIR, output_filename)
    
    try:
        update_pptx_slides(TEMPLATE_PPTX_PATH, data, output_path)
        return send_file(output_path, as_attachment=True, download_name=output_filename)
    except Exception as e:
        app.logger.error(f"Error updating PowerPoint: {e}", exc_info=True)
        return make_response(f"Failed to update PowerPoint deck: {str(e)}", 500)

@app.route('/api/clean-sales-report', methods=['POST'])
def api_clean_sales_report():
    global _latest_cleaned_report_path
    
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected"}), 400
    
    # Use OS temp files so we never conflict with any open Excel file
    input_tmp = None
    output_tmp = None
    try:
        # Save uploaded file to a unique temp path
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False, dir=DATA_DIR) as f:
            input_tmp = f.name
        file.save(input_tmp)
        
        # Create unique temp path for cleaned output
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False, dir=DATA_DIR) as f:
            output_tmp = f.name
        
        clean_sales_report(input_tmp, output_tmp)
        
        if _CLICKHOUSE_AVAILABLE:
            ch_cache_clear()
        
        # Delete old temp file if one existed
        if _latest_cleaned_report_path and os.path.exists(_latest_cleaned_report_path):
            try:
                os.remove(_latest_cleaned_report_path)
            except Exception:
                pass
        
        # Store the new temp path for download
        _latest_cleaned_report_path = output_tmp
        
        # Also copy it to uploaded_sales_report.xlsx so the dashboard can access it
        dashboard_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
        import shutil
        shutil.copy2(output_tmp, dashboard_file)
        
        # Read count from cleaned file (using calamine for speed)
        cleaned_df = pd.read_excel(output_tmp, sheet_name='Sheet1', engine='calamine')
        records_count = len(cleaned_df)
        
        return jsonify({
            "status": "success",
            "message": "Sales report cleaned successfully",
            "count": records_count,
            "download_url": "/api/download-cleaned"
        })
    except Exception as e:
        app.logger.error(f"Error cleaning sales report: {e}", exc_info=True)
        # Clean up failed temp files
        for p in [input_tmp, output_tmp]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        return jsonify({"status": "error", "message": f"Failed to clean file: {str(e)}"}), 500
    finally:
        # Always clean up input temp file after processing
        if input_tmp and os.path.exists(input_tmp):
            try:
                os.remove(input_tmp)
            except Exception:
                pass

@app.route('/api/upload-pinelab', methods=['POST'])
def api_upload_pinelab():
    """Upload Pine Labs EMI transaction CSV/ZIP, clean it, and push to ClickHouse."""
    global _latest_pinelab_cleaned_df
    if not _PINELAB_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'Pine Lab cleaner module not available.'}), 500
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected. Cannot upload Pine Lab data.'}), 503

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded.'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': 'No file selected.'}), 400

    fname = file.filename.lower()
    if not (fname.endswith('.csv') or fname.endswith('.xlsx') or fname.endswith('.xls') or fname.endswith('.zip')):
        return jsonify({'status': 'error', 'message': 'Please upload a .csv, .xlsx or .zip file.'}), 400

    try:
        # Step 1: Load file (handles CSV and ZIP)
        df_raw = load_pine_lab_file(file)

        # Step 2: Apply Pine Labs cleaning rules
        global _latest_pinelab_main_df, _latest_pinelab_angamaly_df, _latest_pinelab_slots
        _load_pinelab_cache_if_needed()

        req_slot = (request.form.get('file_type') or '').strip().lower()
        # Auto-detect Angamaly store file (NOT Angamaly Future) if all rows are Angamaly
        is_angamaly_file = False
        if not df_raw.empty and len(df_raw) < 500:
            store_vals = df_raw['Store Name'].dropna().astype(str).str.upper() if 'Store Name' in df_raw.columns else pd.Series(dtype=str)
            city_vals  = df_raw['City'].dropna().astype(str).str.upper() if 'City' in df_raw.columns else pd.Series(dtype=str)
            if not store_vals.empty and store_vals.str.contains('ANGAMALY').all() and (~store_vals.str.contains('FUTURE')).all():
                is_angamaly_file = True
            elif not city_vals.empty and city_vals.str.contains('ANGAMALY').all() and (store_vals.empty or (~store_vals.str.contains('FUTURE')).all()):
                is_angamaly_file = True

        slot = 'angamaly' if (req_slot == 'angamaly' or is_angamaly_file) else 'main'
        force_br = 'ANGAMALY' if slot == 'angamaly' else ''
        df_clean, slot_stats = clean_pine_lab(df_raw, force_branch=force_br)
        stats = dict(slot_stats)

        if df_clean.empty:
            return jsonify({
                'status': 'warning',
                'message': 'No valid records found after applying cleaning rules.',
                **stats
            })

        # Step 3: Match IMEI → invoice_no and insert into ClickHouse
        from pine_lab_cleaner import COL_TXN_ID, COL_ORIG_AMT
        if slot == 'angamaly':
            _latest_pinelab_angamaly_df = df_clean.copy()
            try:
                _latest_pinelab_angamaly_df.to_pickle(_PINELAB_ANG_PKL)
            except Exception:
                pass
            _latest_pinelab_slots['angamaly'] = {'filename': file.filename, **slot_stats}
        else:
            _latest_pinelab_main_df = df_clean.copy()
            try:
                _latest_pinelab_main_df.to_pickle(_PINELAB_MAIN_PKL)
            except Exception:
                pass
            _latest_pinelab_slots['main'] = {'filename': file.filename, **slot_stats}

        frames = [d for d in (_latest_pinelab_main_df, _latest_pinelab_angamaly_df) if d is not None and not d.empty]
        df_combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=[COL_TXN_ID], keep='first').copy() if frames else df_clean.copy()
        _latest_pinelab_cleaned_df = df_combined.copy()   # cache combined for download

        m_st = _latest_pinelab_slots.get('main') or {}
        a_st = _latest_pinelab_slots.get('angamaly') or {}
        stats = {
            'raw_rows':       int(m_st.get('raw_rows', 0)) + int(a_st.get('raw_rows', 0)),
            'after_filter':   int(m_st.get('after_filter', 0)) + int(a_st.get('after_filter', 0)),
            'after_dedup':    len(df_combined),
            'date_from':      df_combined['_date'].min().strftime('%Y-%m-%d') if '_date' in df_combined.columns and not df_combined.empty else slot_stats.get('date_from', ''),
            'date_to':        df_combined['_date'].max().strftime('%Y-%m-%d') if '_date' in df_combined.columns and not df_combined.empty else slot_stats.get('date_to', ''),
            'total_orig_amt': float(df_combined[COL_ORIG_AMT].sum()) if COL_ORIG_AMT in df_combined.columns else 0.0,
        }
        stats = pine_upload_to_ch(df_combined, stats)
        emi_state.sync_from_clickhouse(force=True)
        _latest_pinelab_slots['combined'] = dict(stats)
        _save_pinelab_slots()
        ch_cache_clear()

        return jsonify({
            'status': 'success',
            'message': (
                f'Pine Lab EMI ({("Main + Angamaly combined" if (m_st and a_st) else ("Angamaly Store" if slot == "angamaly" else "Main Report"))}) '
                f'uploaded successfully. {stats.get("inserted_records", 0)} invoice records updated.'
            ),
            'file_type':         slot,
            'main_uploaded':     bool(m_st),
            'angamaly_uploaded': bool(a_st),
            'main_stats':        m_st or None,
            'angamaly_stats':    a_st or None,
            'raw_rows':          stats.get('raw_rows', 0),
            'after_filter':      stats.get('after_filter', 0),
            'after_dedup':       stats.get('after_dedup', 0),
            'matched_invoices':  stats.get('matched_invoices', 0),
            'inserted_records':  stats.get('inserted_records', 0),
            'date_from':         stats.get('date_from', ''),
            'date_to':           stats.get('date_to', ''),
            'total_orig_amt':    stats.get('total_orig_amt', 0),
        })

    except ValueError as ve:
        app.logger.warning(f'Pine Lab upload validation error: {ve}')
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        app.logger.error(f'Pine Lab upload failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': f'Upload failed: {str(e)}'}), 500


@app.route('/api/pinelab-status', methods=['GET'])
def api_pinelab_status():
    """Return current Pine Labs upload status for Main and Angamaly files."""
    _load_pinelab_cache_if_needed()
    m_st = _latest_pinelab_slots.get('main') or {}
    a_st = _latest_pinelab_slots.get('angamaly') or {}
    c_st = _latest_pinelab_slots.get('combined') or {}
    has_any = bool((m_st or a_st) and emi_state.is_uploaded(emi_state.PINE_FIN))
    return jsonify({
        'status': 'success',
        'uploaded': has_any,
        'main_uploaded': bool(m_st) and has_any,
        'angamaly_uploaded': bool(a_st) and has_any,
        'main_stats': m_st if has_any else None,
        'angamaly_stats': a_st if has_any else None,
        'combined': c_st if has_any else None,
    })

@app.route('/api/reset-pinelab', methods=['POST'])
def api_reset_pinelab():
    """Clear the current session's uploaded Pine Labs months from memory and ClickHouse (preserving historical archive)."""
    _load_pinelab_cache_if_needed()
    months_to_clear = set()
    if _latest_pinelab_cleaned_df is not None and not _latest_pinelab_cleaned_df.empty and '_date' in _latest_pinelab_cleaned_df.columns:
        months_to_clear.update(_latest_pinelab_cleaned_df['_date'].dropna().dt.strftime('%Y-%m').unique().tolist())
    _clear_pinelab_cache()
    if _CLICKHOUSE_AVAILABLE:
        try:
            from clickhouse_client import get_client
            ch_client = get_client()
            if months_to_clear:
                m_in = ", ".join(f"'{m}'" for m in sorted(months_to_clear))
                ch_client.command(f"ALTER TABLE pine_lab_transactions DELETE WHERE data_month IN ({m_in})")
                ch_client.command(f"ALTER TABLE benow_pine_financier DELETE WHERE financier = 'Pine Lab EMI' AND data_month IN ({m_in})")
            emi_state.sync_from_clickhouse(force=True)
            ch_cache_clear()
        except Exception as e:
            app.logger.warning(f"Failed to reset PineLab ClickHouse tables: {e}")
    return jsonify({'status': 'success', 'message': 'Pine Labs current upload cleared.'})

@app.route('/api/download-pinelab-cleaned', methods=['GET'])
@app.route('/api/files/PineLab_Cleaned.xlsx', methods=['GET'])
def api_download_pinelab_cleaned():
    """Serve the most recently cleaned Pine Lab file as an Excel download."""
    _load_pinelab_cache_if_needed()
    global _latest_pinelab_cleaned_df
    if _latest_pinelab_cleaned_df is None or _latest_pinelab_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Pine Lab data available. Please upload a file first.'}), 404

    import io as _io
    export_df = _latest_pinelab_cleaned_df.drop(
        columns=[c for c in ['_date'] if c in _latest_pinelab_cleaned_df.columns]
    ).copy()

    # Format Bank Txn Date as clean string date
    if 'Bank Txn Date' in export_df.columns:
        export_df['Bank Txn Date'] = pd.to_datetime(
            export_df['Bank Txn Date'], dayfirst=True, format='mixed', errors='coerce'
        ).dt.strftime('%Y-%m-%d 00:00:00')

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        export_df.to_excel(writer, index=False, sheet_name='PineLab_Cleaned')
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='PineLab_Cleaned.xlsx'
    )

@app.route('/api/filter-pinelab-by-date', methods=['POST'])
def api_filter_pinelab_by_date():
    _load_pinelab_cache_if_needed()
    """
    Filter the cached cleaned Pine Lab DataFrame by a date range (Bank Txn Date),
    then call upload_to_clickhouse() with the filtered subset.
    Uses invoice_no-based matching to precisely tag only the exact transactions.
    """
    global _latest_pinelab_cleaned_df
    if _latest_pinelab_cleaned_df is None or _latest_pinelab_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Pine Lab data available. Please upload a file first.'}), 404

    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected.'}), 503

    data = request.get_json(force=True) or {}
    date_from = data.get('date_from', '').strip()   # YYYY-MM-DD
    date_to   = data.get('date_to',   '').strip()   # YYYY-MM-DD

    if not date_from or not date_to:
        return jsonify({'status': 'error', 'message': 'Both date_from and date_to are required.'}), 400

    try:
        from clickhouse_client import get_client
        from pine_lab_cleaner import upload_to_clickhouse as _upload_to_ch, COL_DATE

        df = _latest_pinelab_cleaned_df.copy()

        # Parse Bank Txn Date and filter by selected range
        df['_txn_date'] = pd.to_datetime(df[COL_DATE], dayfirst=True, format='mixed', errors='coerce').dt.date
        date_from_d = pd.to_datetime(date_from).date()
        date_to_d   = pd.to_datetime(date_to).date()

        df_filtered = df[
            (df['_txn_date'] >= date_from_d) &
            (df['_txn_date'] <= date_to_d)
        ]
        filtered_rows = len(df_filtered)

        if df_filtered.empty:
            return jsonify({
                'status': 'warning',
                'message': f'No rows found for {date_from} to {date_to}. Dashboard not changed.',
                'filtered_rows': 0,
                'imeis_inserted': 0
            })

        client = get_client()

        # Replace only the month(s) covered by this cached upload so historical archive stays intact
        full_months = sorted(pd.to_datetime(df[COL_DATE], dayfirst=True, format='mixed', errors='coerce').dropna().dt.strftime('%Y-%m').unique().tolist())
        if full_months:
            m_in = ", ".join(f"'{m}'" for m in full_months)
            client.command(f"ALTER TABLE pine_lab_transactions DELETE WHERE data_month IN ({m_in})")
            client.command(f"ALTER TABLE benow_pine_financier DELETE WHERE financier = 'Pine Lab EMI' AND data_month IN ({m_in})")

        # Use invoice_no-based upload logic (solves dummy IMEI problem)
        stats = _upload_to_ch(df_filtered, {})
        emi_state.sync_from_clickhouse(force=True)
        ch_cache_clear()

        matched_invoices = stats.get('phase1_invoice', 0)
        total_inserted   = stats.get('inserted_records', 0)

        app.logger.info(
            f'Pine Lab date filter {date_from}->{date_to}: '
            f'{filtered_rows} rows -> {matched_invoices} invoice-matched + {stats.get("phase2_imei", 0)} IMEI fallback'
        )

        return jsonify({
            'status': 'success',
            'filtered_rows':    filtered_rows,
            'imeis_inserted':   total_inserted,
            'date_from': date_from,
            'date_to':   date_to,
            'message': f'Dashboard updated for {date_from} - {date_to}. {filtered_rows} transactions, {matched_invoices} invoice-matched, {stats.get("phase2_imei", 0)} IMEI fallback.'
        })

    except Exception as e:
        app.logger.error(f'Pine Lab date filter failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/upload-paytm', methods=['POST'])
def api_upload_paytm():
    """Upload Paytm EMI report (CSV/XLSX/ZIP), clean it, and push to ClickHouse."""
    global _latest_paytm_cleaned_df
    if not _PAYTM_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'Paytm cleaner module not available.'}), 500
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected. Cannot upload Paytm data.'}), 503

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded.'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': 'No file selected.'}), 400

    fname = file.filename.lower()
    if not (fname.endswith('.csv') or fname.endswith('.xlsx') or fname.endswith('.xls') or fname.endswith('.zip')):
        return jsonify({'status': 'error', 'message': 'Please upload a .csv, .xlsx or .zip file.'}), 400

    try:
        df_raw = load_paytm_file(file)
        df_clean, stats = clean_paytm(df_raw)

        if df_clean.empty:
            return jsonify({
                'status': 'warning',
                'message': 'No valid records found after applying cleaning rules.',
                **stats
            })

        _latest_paytm_cleaned_df = df_clean.copy()   # cache for download / date filter
        stats = paytm_upload_to_ch(df_clean, stats)
        emi_state.sync_from_clickhouse(force=True)
        ch_cache_clear()

        return jsonify({
            'status': 'success',
            'message': (f'Paytm EMI data uploaded successfully. '
                        f'Removed {stats.get("removed_other_mode", 0)} Other Mode and '
                        f'{stats.get("removed_below_min", 0)} rows below 1000'
                        + (f', and {stats["removed_excluded_branch"]} non-branch (CAFE YMCA) rows.' if stats.get('removed_excluded_branch') else '.')),
            'raw_rows':          stats.get('raw_rows', 0),
            'after_filter':      stats.get('after_filter', 0),
            'after_dedup':       stats.get('after_dedup', 0),
            'matched_invoices':  stats.get('matched_invoices', 0),
            'inserted_records':  stats.get('inserted_records', 0),
            'date_from':         stats.get('date_from', ''),
            'date_to':           stats.get('date_to', ''),
            'total_orig_amt':    stats.get('total_orig_amt', 0),
        })
    except ValueError as ve:
        app.logger.warning(f'Paytm upload validation error: {ve}')
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        app.logger.error(f'Paytm upload failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': f'Upload failed: {str(e)}'}), 500


@app.route('/api/upload-benow', methods=['POST'])
def api_upload_benow():
    global _latest_benow_cleaned_df
    """Upload Benow report (CSV/XLSX/ZIP), clean it, and push to ClickHouse."""
    if not _BENOW_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'Benow cleaner module not available.'}), 500
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected. Cannot upload Benow data.'}), 503

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded.'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': 'No file selected.'}), 400

    fname = file.filename.lower()
    if not (fname.endswith('.csv') or fname.endswith('.xlsx') or fname.endswith('.xls') or fname.endswith('.zip')):
        return jsonify({'status': 'error', 'message': 'Please upload a .csv, .xlsx or .zip file.'}), 400

    try:
        df_raw = load_benow_file(file)
        df_clean, stats = clean_benow(df_raw)

        if df_clean.empty:
            return jsonify({
                'status': 'warning',
                'message': 'No valid records found after applying cleaning rules.',
                **stats
            })

        _latest_benow_cleaned_df = df_clean.copy()
        stats = benow_upload_to_ch(df_clean, stats)
        emi_state.sync_from_clickhouse(force=True)
        ch_cache_clear()

        return jsonify({
            'status': 'success',
            'message': (f'Benow data uploaded successfully. {stats.get("inserted_records", 0)} records updated. '
                        f'Removed {stats.get("removed_below_min", 0)} rows below 1000.'),
            'raw_rows':          stats.get('raw_rows', 0),
            'after_filter':      stats.get('after_filter', 0),
            'inserted_records':  stats.get('inserted_records', 0),
            'date_from':         stats.get('date_from', ''),
            'date_to':           stats.get('date_to', ''),
            'total_orig_amt':    stats.get('total_orig_amt', 0),
        })
    except ValueError as ve:
        app.logger.warning(f'Benow upload validation error: {ve}')
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        app.logger.error(f'Benow upload failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': f'Upload failed: {str(e)}'}), 500


@app.route('/api/download-paytm-cleaned', methods=['GET'])
@app.route('/api/files/Paytm_Cleaned.xlsx', methods=['GET'])
def api_download_paytm_cleaned():
    """Serve the most recently cleaned Paytm file as an Excel download."""
    global _latest_paytm_cleaned_df
    if _latest_paytm_cleaned_df is None or _latest_paytm_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Paytm data available. Please upload a file first.'}), 404

    import io as _io
    export_df = _latest_paytm_cleaned_df.drop(
        columns=[c for c in ['_date'] if c in _latest_paytm_cleaned_df.columns]
    ).copy()

    # Keep date only (no time) for txn_date
    if 'txn_date' in export_df.columns:
        export_df['txn_date'] = (
            pd.to_datetime(export_df['txn_date'], errors='coerce').dt.date
        )

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        export_df.to_excel(writer, index=False, sheet_name='Paytm_Cleaned')
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Paytm_Cleaned.xlsx'
    )


@app.route('/api/filter-paytm-by-date', methods=['POST'])
def api_filter_paytm_by_date():
    """Filter the cached cleaned Paytm DataFrame by Transaction Date range and re-link to the dashboard."""
    global _latest_paytm_cleaned_df
    if _latest_paytm_cleaned_df is None or _latest_paytm_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Paytm data available. Please upload a file first.'}), 404
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected.'}), 503

    data = request.get_json(force=True) or {}
    date_from = data.get('date_from', '').strip()
    date_to   = data.get('date_to',   '').strip()
    if not date_from or not date_to:
        return jsonify({'status': 'error', 'message': 'Both date_from and date_to are required.'}), 400

    try:
        from clickhouse_client import get_client
        from paytm_cleaner import COL_DATE as _PAYTM_DATE, FINANCIER_NAME as _PAYTM_FIN

        df = _latest_paytm_cleaned_df.copy()
        df['_txn_date'] = pd.to_datetime(df[_PAYTM_DATE], errors='coerce').dt.date
        d_from = pd.to_datetime(date_from).date()
        d_to   = pd.to_datetime(date_to).date()
        df_filtered = df[(df['_txn_date'] >= d_from) & (df['_txn_date'] <= d_to)]
        filtered_rows = len(df_filtered)

        if df_filtered.empty:
            return jsonify({
                'status': 'warning',
                'message': f'No rows found for {date_from} to {date_to}. Dashboard not changed.',
                'filtered_rows': 0, 'imeis_inserted': 0
            })

        client = get_client()
        full_months = sorted(pd.to_datetime(df[_PAYTM_DATE], errors='coerce').dropna().dt.strftime('%Y-%m').unique().tolist())
        if full_months:
            m_in = ", ".join(f"'{m}'" for m in full_months)
            client.command(f"ALTER TABLE paytm_transactions DELETE WHERE data_month IN ({m_in})")
            client.command(f"ALTER TABLE benow_pine_financier DELETE WHERE financier = '{_PAYTM_FIN}' AND data_month IN ({m_in})")

        stats = paytm_upload_to_ch(df_filtered, {})
        emi_state.sync_from_clickhouse(force=True)
        ch_cache_clear()
        matched = stats.get('phase1_invoice', 0)
        return jsonify({
            'status': 'success',
            'filtered_rows':  filtered_rows,
            'imeis_inserted': stats.get('inserted_records', 0),
            'date_from': date_from,
            'date_to':   date_to,
            'message': f'Dashboard updated for {date_from} - {date_to}. {filtered_rows} transactions, '
                       f'{matched} invoice-matched, {stats.get("phase2_imei", 0)} IMEI fallback.'
        })
    except Exception as e:
        app.logger.error(f'Paytm date filter failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/download-benow-cleaned', methods=['GET'])
@app.route('/api/files/Benow_Cleaned.xlsx', methods=['GET'])
def api_download_benow_cleaned():
    """Serve the most recently cleaned Benow file as an Excel download."""
    global _latest_benow_cleaned_df
    if _latest_benow_cleaned_df is None or _latest_benow_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Benow data available. Please upload a file first.'}), 404

    import io as _io
    export_df = _latest_benow_cleaned_df.drop(
        columns=[c for c in ['_date'] if c in _latest_benow_cleaned_df.columns]
    ).copy()

    # Keep date only (no time) for txn_date
    if 'txn_date' in export_df.columns:
        export_df['txn_date'] = (
            pd.to_datetime(export_df['txn_date'], errors='coerce').dt.date
        )

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        export_df.to_excel(writer, index=False, sheet_name='Benow_Cleaned')
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Benow_Cleaned.xlsx'
    )


@app.route('/api/filter-benow-by-date', methods=['POST'])
def api_filter_benow_by_date():
    """Filter the cached cleaned Benow DataFrame by Transaction Date range and re-link to the dashboard."""
    global _latest_benow_cleaned_df
    if _latest_benow_cleaned_df is None or _latest_benow_cleaned_df.empty:
        return jsonify({'status': 'error', 'message': 'No cleaned Benow data available. Please upload a file first.'}), 404
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not connected.'}), 503

    data = request.get_json(force=True) or {}
    date_from = data.get('date_from', '').strip()
    date_to   = data.get('date_to',   '').strip()
    if not date_from or not date_to:
        return jsonify({'status': 'error', 'message': 'Both date_from and date_to are required.'}), 400

    try:
        from clickhouse_client import get_client
        from benow_cleaner import FINANCIER_NAME as _BENOW_FIN, upload_to_clickhouse as benow_upload_to_ch

        df = _latest_benow_cleaned_df.copy()
        df['_txn_date'] = pd.to_datetime(df['_date'], errors='coerce').dt.date
        d_from = pd.to_datetime(date_from).date()
        d_to   = pd.to_datetime(date_to).date()
        df_filtered = df[(df['_txn_date'] >= d_from) & (df['_txn_date'] <= d_to)]
        filtered_rows = len(df_filtered)

        if df_filtered.empty:
            return jsonify({
                'status': 'warning',
                'message': f'No rows found for {date_from} to {date_to}. Dashboard not changed.',
                'filtered_rows': 0, 'imeis_inserted': 0
            })

        client = get_client()
        full_months = sorted(pd.to_datetime(df['_date'], errors='coerce').dropna().dt.strftime('%Y-%m').unique().tolist())
        if full_months:
            m_in = ", ".join(f"'{m}'" for m in full_months)
            client.command(f"ALTER TABLE benow_transactions DELETE WHERE data_month IN ({m_in})")
            client.command(f"ALTER TABLE benow_pine_financier DELETE WHERE financier = '{_BENOW_FIN}' AND data_month IN ({m_in})")

        stats = benow_upload_to_ch(df_filtered, {})
        emi_state.sync_from_clickhouse(force=True)
        ch_cache_clear()
        matched = stats.get('phase1_invoice', 0)
        return jsonify({
            'status': 'success',
            'filtered_rows':  filtered_rows,
            'imeis_inserted': stats.get('inserted_records', 0),
            'date_from': date_from,
            'date_to':   date_to,
            'message': f'Dashboard updated for {date_from} - {date_to}. {filtered_rows} transactions, '
                       f'{matched} invoice-matched, {stats.get("phase2_imei", 0)} IMEI fallback.'
        })
    except Exception as e:
        app.logger.error(f'Benow date filter failed: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500



@app.route('/api/download-cleaned', methods=['GET'])
def api_download_cleaned():
    global _latest_cleaned_report_path
    if not _latest_cleaned_report_path or not os.path.exists(_latest_cleaned_report_path):
        return make_response("Cleaned report not found. Please clean a file first.", 404)
    return send_file(
        _latest_cleaned_report_path,
        as_attachment=True,
        download_name='Cleaned_Sales_Report.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def _write_cleaned_export_xlsx(df: pd.DataFrame, target) -> None:
    """Stream cleaned DATA DataFrame into an .xlsx file path or BytesIO with constant memory."""
    import xlsxwriter as _xw

    opts = {
        'constant_memory': True,
        'strings_to_urls': False,
        'strings_to_formulas': False,
    }
    if not isinstance(target, (str, os.PathLike)):
        opts['in_memory'] = True

    workbook = _xw.Workbook(target, opts)
    worksheet = workbook.add_worksheet('Sheet1')

    hdr_fmt = workbook.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#1e3a5f',
        'border': 1, 'align': 'center', 'valign': 'vcenter',
        'font_name': 'Calibri', 'font_size': 10,
    })
    nrm_fmt = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 9,
    })
    num_fmt = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 9,
        'num_format': '#,##0', 'align': 'right',
    })
    price_fmt = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 9,
        'num_format': '#,##0.00', 'align': 'right',
    })

    cols = list(df.columns)
    num_cols = {'QTY', 'Slno'}
    price_cols = {'Sold Price', 'Price'}

    sample = df.head(1000)
    for col_num, col_name in enumerate(cols):
        col_vals = sample[col_name].astype(str)
        max_width = max(len(str(col_name)), int(col_vals.str.len().max()) if not col_vals.empty else 0)
        col_fmt = price_fmt if col_name in price_cols else (num_fmt if col_name in num_cols else nrm_fmt)
        worksheet.set_column(col_num, col_num, min(max_width + 2, 35), col_fmt)

    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, len(df), len(cols) - 1)
    worksheet.set_row(0, 18)
    worksheet.write_row(0, 0, cols, hdr_fmt)

    col_lists = []
    for col_name in cols:
        s = df[col_name]
        if col_name in price_cols:
            col_lists.append(pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float).tolist())
        elif col_name in num_cols:
            col_lists.append(pd.to_numeric(s, errors='coerce').fillna(0).astype('int64').tolist())
        elif col_name == 'Date':
            dt_s = pd.to_datetime(s, errors='coerce')
            col_lists.append(dt_s.dt.strftime('%Y-%m-%d').fillna('').tolist())
        else:
            col_lists.append(s.fillna('').astype(str).tolist())

    for row_idx, row_vals in enumerate(zip(*col_lists), start=1):
        worksheet.write_row(row_idx, 0, row_vals)

    workbook.close()


@app.route('/api/export-date-range', methods=['GET'])
def api_export_date_range():
    """
    Export the cleaned DATA sheet filtered to a date range.
    Priority:
      1. Uploaded cleaned file (filtered by date) — fastest
      2. ClickHouse live query               — falls back if no file
    Query params: start_date=YYYY-MM-DD, end_date=YYYY-MM-DD, source=file|clickhouse
    """
    import io as _io
    start_date = request.args.get('start_date', '').strip()
    end_date   = request.args.get('end_date',   '').strip()
    source     = request.args.get('source', 'auto').strip()   # 'file', 'clickhouse', or 'auto'

    if not start_date or not end_date:
        return jsonify({'error': 'start_date and end_date are required (YYYY-MM-DD)'}), 400

    try:
        df = None

        # ── Try uploaded / cleaned file first (or if explicitly requested) ──
        file_path = _latest_cleaned_report_path or os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
        use_file = (source in ('file', 'auto')) and file_path and os.path.exists(file_path)

        if use_file:
            raw = pd.read_excel(file_path, sheet_name='Sheet1', engine='calamine')
            raw['Date'] = pd.to_datetime(raw['Date'], errors='coerce', dayfirst=True)
            s_dt = pd.to_datetime(start_date)
            e_dt = pd.to_datetime(end_date)
            df = raw[(raw['Date'] >= s_dt) & (raw['Date'] <= e_dt)].copy()
            if df.empty and source == 'auto':
                df = None   # fall through to ClickHouse

        # ── Fall back to ClickHouse ──
        if df is None and _CLICKHOUSE_AVAILABLE and source != 'file':
            from clickhouse_aggregator import get_raw_export_df
            df = get_raw_export_df(start_date, end_date)

        if df is None or (hasattr(df, 'empty') and df.empty):
            return jsonify({'error': f'No data found for {start_date} → {end_date}. '
                                      'Upload a cleaned sales report or connect ClickHouse.'}), 404

        # ── Build Excel in memory via constant-memory streaming writer ──
        output = _io.BytesIO()
        _write_cleaned_export_xlsx(df, output)

        output.seek(0)
        safe_start = start_date.replace('-', '')
        safe_end   = end_date.replace('-', '')
        filename   = f'CleanedData_{safe_start}_to_{safe_end}.xlsx'

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as exc:
        app.logger.error('export-date-range error: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/export-finance-report', methods=['POST'])
def api_export_finance_report():
    """Generate Finance Detailed Report from the latest cleaned sales data."""
    global _latest_finance_report_path

    if not _latest_cleaned_report_path or not os.path.exists(_latest_cleaned_report_path):
        return jsonify({'status': 'error',
                        'message': 'No cleaned report found. Please clean a sales file first.'}), 400

    output_tmp = None
    try:
        # Read the cleaned report
        cleaned_df = pd.read_excel(_latest_cleaned_report_path, sheet_name='Sheet1', engine='calamine')

        # Write to a temp file
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False, dir=DATA_DIR) as f:
            output_tmp = f.name

        generate_finance_report(cleaned_df, output_tmp)

        # Clean up old finance report
        if _latest_finance_report_path and os.path.exists(_latest_finance_report_path):
            try:
                os.remove(_latest_finance_report_path)
            except Exception:
                pass

        _latest_finance_report_path = output_tmp

        return jsonify({'status': 'success',
                        'message': 'Finance Detailed Report generated successfully.',
                        'download_url': '/api/download-finance-report'})
    except Exception as e:
        app.logger.error(f'Error generating Finance report: {e}', exc_info=True)
        if output_tmp and os.path.exists(output_tmp):
            try:
                os.remove(output_tmp)
            except Exception:
                pass
        return jsonify({'status': 'error', 'message': f'Failed to generate report: {str(e)}'}), 500


@app.route('/api/download-finance-report', methods=['GET'])
def api_download_finance_report():
    global _latest_finance_report_path
    if not _latest_finance_report_path or not os.path.exists(_latest_finance_report_path):
        return make_response('Finance report not found. Please generate it first.', 404)
    return send_file(
        _latest_finance_report_path,
        as_attachment=True,
        download_name='Finance_Detailed_Report.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )



@app.route('/api/get-cleaned-aggregation', methods=['GET'])
def api_get_cleaned_aggregation():
    global _latest_cleaned_report_path
    
    if not _latest_cleaned_report_path or not os.path.exists(_latest_cleaned_report_path):
        return jsonify({"status": "error", "message": "No cleaned report found"}), 404
        
    try:
        df = pd.read_excel(_latest_cleaned_report_path, sheet_name='Sheet1', engine='calamine')
        
        # Initialize summary structures
        standardFins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow", "aCASH"]
        standardCats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]
        
        sourcing = {f: {"sourcing": 0, "pending": 0} for f in standardFins}
        qty = {cat: {f: 0 for f in standardFins} for cat in standardCats}
        val = {cat: {f: 0.0 for f in standardFins} for cat in standardCats}
        
        PRODUCT_CAT_MAP = {
            'CD PRODUCTS': ['AIR CONDITIONER', 'DISH WASHER', 'DRYER', 'FREEZER', 'HOME APPLIANCES', 'MICROWAVE OVEN', 'REFRIGERATORS', 'TV', 'WASHING MACHINES'],
            'HVA': ['AUDIO', 'EAR WEARABLES', 'GAMING', 'HOME THEATRE', 'SMART WATCH'],
            'LAPTOP': ['LAPTOP', 'DESKTOP'],
            'MOBILE': ['MOBILE', 'TABLET'],
            'SA': ['SMALL APPLIANCES'],
            'OTHERS': ['CAMERA', 'DIY', 'IT ACCESSORIES', 'PRINTER', 'STABILIZER', 'MYG VERSE', 'RIG'],
            'BANK EMI': []
        }
        products_qty = {cat: {prod: {f: 0 for f in standardFins} for prod in prods} for cat, prods in PRODUCT_CAT_MAP.items()}
        products_val = {cat: {prod: {f: 0.0 for f in standardFins} for prod in prods} for cat, prods in PRODUCT_CAT_MAP.items()}
        dmi_contest = {"SAMSUNG": 0, "VIVO": 0, "OPPO": 0}
        branches = {}
        brands_performance = {}
        
        fin_map = {
            'BAJAJ FINANCE': 'BAJAJ',
            'HDB FINANCE': 'HDB',
            'IDFC FIRST BANK LTD': 'IDFC',
            'Poonawalla Fincorp': 'PFL',
            'TVS Credit': 'TVS',
            'DMI FINANCE': 'DMI',
            'Pine Lab EMI': 'PineLab',
            'PAYTM': 'PAYTM',
            'Benow': 'Benow',
            'aCash': 'aCASH'
        }
        
        # Iterate and aggregate
        for idx, row in df.iterrows():
            fin_raw = row.get('Financier', '')
            cat = str(row.get('Category', '')).strip().upper()
            
            try:
                qty_val = int(row.get('QTY', 1))
            except:
                qty_val = 1
                
            price_val = 0.0
            try:
                price_val = float(row.get('Sold Price', 0.0))
            except:
                pass
                
            fin = fin_map.get(fin_raw, None)
            if not fin:
                continue
                
            cat_mapping = {
                'CD PRODUCTS': 'CD PRODUCTS',
                'LAPTOP': 'LAPTOP',
                'MOBILE': 'MOBILE',
                'HVA': 'HVA',
                'SA': 'SA',
                'OTHERS': 'OTHERS'
            }
            mapped_cat = cat_mapping.get(cat, 'OTHERS')
            prod = str(row.get('Product', '')).strip().upper()
            
            qty[mapped_cat][fin] += qty_val
            val[mapped_cat][fin] += price_val / 10000000.0  # Crores
            
            if prod in products_qty.get(mapped_cat, {}):
                products_qty[mapped_cat][prod][fin] += qty_val
                products_val[mapped_cat][prod][fin] += price_val / 10000000.0
                
            brand = str(row.get('Brand', '')).upper().strip()
            if not brand:
                brand = 'UNKNOWN'
                
            if brand not in brands_performance:
                brands_performance[brand] = {
                    "qty": {c: {f: 0 for f in standardFins} for c in standardCats},
                    "value": {c: {f: 0.0 for f in standardFins} for c in standardCats},
                    "products_qty": {c: {p: {f: 0 for f in standardFins} for p in PRODUCT_CAT_MAP[c]} for c in standardCats},
                    "products_val": {c: {p: {f: 0.0 for f in standardFins} for p in PRODUCT_CAT_MAP[c]} for c in standardCats}
                }
                
            brands_performance[brand]["qty"][mapped_cat][fin] += qty_val
            brands_performance[brand]["value"][mapped_cat][fin] += price_val / 10000000.0
            if prod in brands_performance[brand]["products_qty"].get(mapped_cat, {}):
                brands_performance[brand]["products_qty"][mapped_cat][prod][fin] += qty_val
                brands_performance[brand]["products_val"][mapped_cat][prod][fin] += price_val / 10000000.0
            
            sourcing[fin]['sourcing'] += qty_val
            
            if fin == 'DMI':
                brand = str(row.get('Brand', '')).upper().strip()
                if brand in dmi_contest:
                    dmi_contest[brand] += qty_val
                
                branch = str(row.get('STore', '')).upper().strip()
                if branch not in branches:
                    branches[branch] = {"OPPO": 0, "SAMSUNG": 0, "VIVO": 0}
                if brand in branches[branch]:
                    branches[branch][brand] += qty_val

        for _c in standardCats:
            qty[_c]['_emi_deducted'] = True
            for _b in (brands_perf if 'brands_perf' in locals() else brands_performance).values():
                _b['qty'][_c]['_emi_deducted'] = True
                    
        return jsonify({
            "status": "success",
            "data": {
                "sourcing": sourcing,
                "qty": qty,
                "val": val,
                "products_qty": products_qty,
                "products_val": products_val,
                "brands_performance": brands_performance,
                "dmi": dmi_contest,
                "branches": branches
            }
        })
    except Exception as e:
        app.logger.error(f"Error aggregating: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/clickhouse/aggregate-review', methods=['GET'])
def api_clickhouse_aggregate_review():
    """
    Fetch and aggregate ClickHouse data for the active review date,
    returning the same shape as /api/get-cleaned-aggregation so the
    frontend can call applyCleanedDataToActiveReview() unchanged.

    Query params: date (required), timeframe (default: monthly)
    """
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'ClickHouse not available.'}), 503

    date_str  = request.args.get('date')
    timeframe = request.args.get('timeframe', 'monthly')
    if not date_str:
        return jsonify({'status': 'error', 'message': 'date is required'}), 400

    try:
        end_date_str = request.args.get('end_date', '')
        df = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
        if df.empty:
            return jsonify({'status': 'error', 'message': f'No ClickHouse data for {date_str} ({timeframe}).'}), 404

        # --- Aggregate into the same structure as get-cleaned-aggregation ---
        standardFins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow", "aCASH"]
        standardCats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"]
        PRODUCT_CAT_MAP = {
            'CD PRODUCTS': ['AIR CONDITIONER', 'DISH WASHER', 'DRYER', 'FREEZER', 'HOME APPLIANCES', 'MICROWAVE OVEN', 'REFRIGERATORS', 'TV', 'WASHING MACHINES'],
            'HVA': ['AUDIO', 'EAR WEARABLES', 'GAMING', 'HOME THEATRE', 'SMART WATCH'],
            'LAPTOP': ['LAPTOP', 'DESKTOP'],
            'MOBILE': ['MOBILE', 'TABLET'],
            'SA': ['SMALL APPLIANCES'],
            'OTHERS': ['CAMERA', 'DIY', 'IT ACCESSORIES', 'PRINTER', 'STABILIZER', 'MYG VERSE', 'RIG'],
            'BANK EMI': []
        }

        sourcing        = {f: {"sourcing": 0, "pending": 0} for f in standardFins}
        qty             = {cat: {f: 0 for f in standardFins} for cat in standardCats}
        val             = {cat: {f: 0.0 for f in standardFins} for cat in standardCats}
        products_qty    = {cat: {prod: {f: 0 for f in standardFins} for prod in prods} for cat, prods in PRODUCT_CAT_MAP.items()}
        products_val    = {cat: {prod: {f: 0.0 for f in standardFins} for prod in prods} for cat, prods in PRODUCT_CAT_MAP.items()}
        dmi_contest     = {"SAMSUNG": 0, "VIVO": 0, "OPPO": 0}
        branches        = {}
        brands_perf     = {}

        for _, row in df.iterrows():
            fin      = str(row.get('Financier', '') or 'aCASH').strip()
            cat      = str(row.get('Category', '') or 'OTHERS').strip().upper()
            prod     = str(row.get('Product',  '') or '').strip().upper()
            brand    = str(row.get('Brand',    '') or 'UNKNOWN').strip().upper() or 'UNKNOWN'
            branch   = str(row.get('Branch',   '') or '').strip().upper()

            try:    qty_val   = int(float(row.get('QTY', 1) or 1))
            except: qty_val   = 1
            try:    price_val = float(row.get('Sold Price', 0.0) or 0.0)
            except: price_val = 0.0

            if fin not in standardFins:
                fin = 'aCASH'
            mapped_cat = cat if cat in standardCats else 'OTHERS'

            # Category + product tallies
            qty[mapped_cat][fin] += qty_val
            val[mapped_cat][fin] += price_val / 10_000_000.0  # Crores

            if prod in products_qty.get(mapped_cat, {}):
                products_qty[mapped_cat][prod][fin] += qty_val
                products_val[mapped_cat][prod][fin] += price_val / 10_000_000.0

            # Brand performance
            if brand not in brands_perf:
                brands_perf[brand] = {
                    "qty":  {c: {f: 0   for f in standardFins} for c in standardCats},
                    "value":{c: {f: 0.0 for f in standardFins} for c in standardCats},
                    "products_qty": {c: {p: {f: 0   for f in standardFins} for p in PRODUCT_CAT_MAP[c]} for c in standardCats if PRODUCT_CAT_MAP[c]},
                    "products_val": {c: {p: {f: 0.0 for f in standardFins} for p in PRODUCT_CAT_MAP[c]} for c in standardCats if PRODUCT_CAT_MAP[c]},
                }
            brands_perf[brand]["qty"][mapped_cat][fin]   += qty_val
            brands_perf[brand]["value"][mapped_cat][fin] += price_val / 10_000_000.0

            # Sourcing
            sourcing[fin]['sourcing'] += qty_val

            # DMI contest
            if fin == 'DMI':
                if brand in dmi_contest:
                    dmi_contest[brand] += qty_val
                if branch not in branches:
                    branches[branch] = {"OPPO": 0, "SAMSUNG": 0, "VIVO": 0}
                if brand in branches.get(branch, {}):
                    branches[branch][brand] += qty_val

        for _c in standardCats:
            qty[_c]['_emi_deducted'] = True
            for _b in (brands_perf if 'brands_perf' in locals() else brands_performance).values():
                _b['qty'][_c]['_emi_deducted'] = True

        return jsonify({
            'status': 'success',
            'source': 'clickhouse',
            'date': date_str,
            'timeframe': timeframe,
            'data': {
                'sourcing':          sourcing,
                'qty':               qty,
                'val':               val,
                'products_qty':      products_qty,
                'products_val':      products_val,
                'brands_performance':brands_perf,
                'dmi':               dmi_contest,
                'branches':          branches,
            }
        })

    except Exception as exc:
        app.logger.error('ClickHouse aggregate-review failed: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'message': str(exc)}), 500

# ---------------------------------------------------------------------------
# Startup: pre-warm ClickHouse connection + cache sequentially on boot
# ---------------------------------------------------------------------------
_startup_prewarm_lock = threading.Lock()
_startup_prewarm_running = False


def _prewarm_clickhouse_cache():
    """Run in background thread to wake ClickHouse and pre-load key caches."""
    global _startup_prewarm_running
    if not _CLICKHOUSE_AVAILABLE:
        return
    ch_set_warming(True)
    try:
        from datetime import date
        import store_fin_share

        app.logger.info("Cache pre-warm: syncing EMI state & waking ClickHouse...")
        emi_state.sync_from_clickhouse()
        ch_get_row_counts()

        today = date.today()
        month_start = today.replace(day=1).isoformat()
        app.logger.info("Cache pre-warm: querying current month rollups (%s)...", month_start)
        get_clickhouse_dashboard_rollups(month_start, "monthly")

        app.logger.info("Cache pre-warm: warming Store Fin Share (2026-10 reference)...")
        store_fin_share.get_store_fin_share_data("2026-10-01", "monthly", period_mode="reference")
        app.logger.info("Cache pre-warm complete.")
    except Exception as e:
        app.logger.warning("Cache pre-warm failed (non-fatal): %s", e)
    finally:
        ch_set_warming(False)
        with _startup_prewarm_lock:
            _startup_prewarm_running = False


def start_background_prewarm():
    """Idempotently launch background warm-up thread if not already running."""
    global _startup_prewarm_running
    if not _CLICKHOUSE_AVAILABLE:
        return False
    with _startup_prewarm_lock:
        if _startup_prewarm_running:
            return False
        _startup_prewarm_running = True
        ch_set_warming(True)
    threading.Thread(target=_prewarm_clickhouse_cache, daemon=True).start()
    return True


# Auto-start pre-warm under direct Flask/dev execution; under Gunicorn, gunicorn.conf.py
# triggers start_background_prewarm() in post_worker_init after worker fork.
_running_under_gunicorn = (
    "gunicorn" in sys.modules
    or "gunicorn" in os.environ.get("SERVER_SOFTWARE", "").lower()
)
if (not _running_under_gunicorn) and (__name__ != '__main__' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
    start_background_prewarm()


if __name__ == '__main__':
    # Start the server on localhost port 8005
    app.run(host='127.0.0.1', port=8005, debug=True)
