import os
import json
import logging
import tempfile
from flask import Flask, jsonify, request, send_file, render_template, make_response
from datetime import datetime, timedelta

from excel_generator import generate_excel_report
from pptx_updater import update_pptx_slides
from sales_cleaner import clean_sales_report
from finance_report_generator import generate_finance_report
import pandas as pd
from dashboard_aggregator import get_dashboard_data

# --- ClickHouse integration (optional — portal works without it) ---
try:
    from clickhouse_client import (
        is_connected as ch_is_connected,
        get_row_counts as ch_get_row_counts,
        cache_clear as ch_cache_clear,
        _QUERY_CACHE as _ch_cache,
    )
    from clickhouse_aggregator import get_clickhouse_df
    _CLICKHOUSE_AVAILABLE = True
except ImportError:
    _CLICKHOUSE_AVAILABLE = False

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, template_folder=os.path.join(basedir, 'templates'), static_folder=os.path.join(basedir, 'static'))
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable static file caching
logging.basicConfig(level=logging.INFO)

# Holds path to the latest cleaned sales report (temp file)
_latest_cleaned_report_path = None
# Holds path to the latest finance detailed report (temp file)
_latest_finance_report_path = None

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

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
        connected = ch_is_connected()
        counts = ch_get_row_counts() if connected else {}
        cache_entries = len(_ch_cache) if _CLICKHOUSE_AVAILABLE else 0
        return jsonify({
            'connected': connected,
            'row_counts': counts,
            'cache_entries': cache_entries,
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
    return jsonify({'status': 'success', 'cleared': cleared})


@app.route('/api/clickhouse/prewarm', methods=['POST'])
def api_clickhouse_prewarm():
    """
    Fire-and-forget background fetch for all 3 timeframes for a given date.
    Call this when the user selects a date so daily/monthly/quarterly are
    all cached before they click the timeframe selector.

    Body: { "date": "YYYY-MM-DD" }
    """
    if not _CLICKHOUSE_AVAILABLE:
        return jsonify({'status': 'skipped', 'reason': 'ClickHouse not available'}), 200

    data = request.get_json(silent=True) or {}
    date_str = data.get('date')
    end_date_str = request.args.get('end_date') or (request.is_json and request.get_json(silent=True).get('end_date')) or request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date is required'}), 400

    import threading

    def _warm(d, tf):
        try:
            get_clickhouse_df(d, tf, end_date_str=end_date_str)
            app.logger.info('Prewarm complete: %s / %s', d, tf)
        except Exception as exc:
            app.logger.warning('Prewarm failed %s/%s: %s', d, tf, exc)

    for tf in ('daily', 'monthly', 'quarterly'):
        t = threading.Thread(target=_warm, args=(date_str, tf), daemon=True)
        t.start()

    return jsonify({'status': 'warming', 'date': date_str, 'timeframes': ['daily', 'monthly', 'quarterly']})


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
    source = request.args.get('source', 'excel')  # 'excel' | 'clickhouse'
    
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    # --- ClickHouse path ---
    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available on this server.'}), 503
        try:
            ch_df = get_clickhouse_df(date_str, timeframe, rbm, bdm, store, staff, end_date_str=end_date_str)
            if ch_df.empty:
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
            # Reuse existing aggregation logic, passing the ClickHouse DataFrame
            data = get_dashboard_data(
                raw_file_path=None, target_date_str=date_str, timeframe=timeframe,
                rbm=rbm, bdm=bdm, store=store, staff=staff, zone_map=zone_map, df=ch_df,
                end_date_str=end_date_str  # ← was missing, caused custom range to show 1 day only
            )
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
    
    data = get_dashboard_data(raw_file, date_str, timeframe, rbm, bdm, store, staff, zone_map=zone_map, end_date_str=end_date_str)
    return jsonify(data)

@app.route('/api/price-range-data', methods=['GET'])
def api_price_range_data():
    from dashboard_aggregator import get_price_range_data
    date_str    = request.args.get('date')
    end_date_str= request.args.get('end_date', '')
    timeframe   = request.args.get('timeframe', 'monthly')
    price_range = request.args.get('price_range', 'ALL')
    source      = request.args.get('source', 'excel')
    
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            ch_df = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
            if ch_df.empty:
                return jsonify({'error': 'No ClickHouse data for this timeframe.'}), 404
            return jsonify(get_price_range_data(None, date_str, timeframe, price_range, df=ch_df, end_date_str=end_date_str))
        except Exception as exc:
            app.logger.error('ClickHouse price-range query failed: %s', exc, exc_info=True)
            return jsonify({'error': str(exc)}), 500
    
    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet."}), 404
    
    data = get_price_range_data(raw_file, date_str, timeframe, price_range, end_date_str=end_date_str)
    return jsonify(data)

@app.route('/api/brand-data', methods=['GET'])
def api_brand_data():
    from dashboard_aggregator import get_brand_data
    date_str  = request.args.get('date')
    end_date_str = request.args.get('end_date', '')
    timeframe = request.args.get('timeframe', 'monthly')
    category  = request.args.get('category', 'ALL')
    product   = request.args.get('product', 'ALL')
    source    = request.args.get('source', 'excel')
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            ch_df = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
            if ch_df.empty:
                return jsonify({'error': 'No ClickHouse data for this timeframe.'}), 404
            return jsonify(get_brand_data(None, date_str, timeframe, category, product, df=ch_df, end_date_str=end_date_str))
        except Exception as exc:
            app.logger.error('ClickHouse brand query failed: %s', exc, exc_info=True)
            return jsonify({'error': str(exc)}), 500

    raw_file = os.path.join(DATA_DIR, 'uploaded_sales_report.xlsx')
    if not os.path.exists(raw_file):
        return jsonify({"error": "Raw sales report not uploaded yet."}), 404
    return jsonify(get_brand_data(raw_file, date_str, timeframe, category, product, end_date_str=end_date_str))

@app.route('/api/store-data', methods=['GET'])
def api_store_data():
    from dashboard_aggregator import get_store_data
    date_str  = request.args.get('date')
    end_date_str = request.args.get('end_date', '')
    timeframe = request.args.get('timeframe', 'monthly')
    zone      = request.args.get('zone', 'ALL')
    source    = request.args.get('source', 'excel')
    if not date_str:
        return jsonify({"error": "date is required"}), 400

    if source == 'clickhouse':
        if not _CLICKHOUSE_AVAILABLE:
            return jsonify({'error': 'ClickHouse not available.'}), 503
        try:
            ch_df = get_clickhouse_df(date_str, timeframe, end_date_str=end_date_str)
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
            return jsonify(get_store_data(None, date_str, timeframe, zone, zone_map, df=ch_df, end_date_str=end_date_str))
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
    return jsonify(get_store_data(raw_file, date_str, timeframe, zone, zone_map, end_date_str=end_date_str))

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


if __name__ == '__main__':
    # Start the server on localhost port 8005
    app.run(host='127.0.0.1', port=8005, debug=True)
