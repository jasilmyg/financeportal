import os
import json
import re
import sys

base_dir = r"c:\Users\rahul_myg\Downloads\FINANCE PORTAL"
sys.path.insert(0, base_dir)

templates_dir = os.path.join(base_dir, "templates")
static_dir = os.path.join(base_dir, "static")
data_dir = os.path.join(base_dir, "data")

# ---------------------------------------------------------------------------
# Read HTML template
# ---------------------------------------------------------------------------
with open(os.path.join(templates_dir, "index.html"), "r", encoding="utf-8") as f:
    html_content = f.read()

# ---------------------------------------------------------------------------
# Inline CSS
# ---------------------------------------------------------------------------
css_file = os.path.join(static_dir, "css", "style.css")
with open(css_file, "r", encoding="utf-8") as f:
    css_content = f.read()
html_content = re.sub(
    r'<link rel="stylesheet" href="/static/css/style\.css[^>]*>',
    lambda m: f'<style>\n{css_content}\n</style>',
    html_content
)

# ---------------------------------------------------------------------------
# Inline JS
# ---------------------------------------------------------------------------
js_files = ["lucide.min.js", "chart.min.js", "main.js"]
for js_file in js_files:
    js_path = os.path.join(static_dir, "js", js_file)
    if os.path.exists(js_path):
        with open(js_path, "r", encoding="utf-8") as f:
            js_content = f.read()
        escaped_js_file = js_file.replace('.', r'\.')
        pattern = rf'<script src="/static/js/{escaped_js_file}[^>]*></script>'
        html_content = re.sub(
            pattern,
            lambda m, js=js_content: f'<script>\n{js}\n</script>',
            html_content
        )

# ---------------------------------------------------------------------------
# Load targets.json
# ---------------------------------------------------------------------------
targets_file = os.path.join(data_dir, "targets.json")
targets_data = "{}"
if os.path.exists(targets_file):
    with open(targets_file, "r", encoding="utf-8") as f:
        targets_data = f.read()

# ---------------------------------------------------------------------------
# Load weekly review data
# ---------------------------------------------------------------------------
reviews = []
review_data_dict = {}
for file in os.listdir(data_dir):
    if file.startswith("weekly_data_") and file.endswith(".json"):
        date_str = file.replace("weekly_data_", "").replace(".json", "")
        reviews.append(date_str)
        with open(os.path.join(data_dir, file), "r", encoding="utf-8") as f:
            review_data_dict[date_str] = json.loads(f.read())
reviews.sort(reverse=True)
latest_date = reviews[0] if reviews else "2026-07-10"

# ---------------------------------------------------------------------------
# Pre-generate dashboard data from Excel (all timeframes)
# ---------------------------------------------------------------------------
dashboard_mock = {}
raw_file = os.path.join(data_dir, "uploaded_sales_report.xlsx")
excel_latest_date = latest_date  # fallback

if os.path.exists(raw_file):
    try:
        import pandas as pd
        from dashboard_aggregator import get_dashboard_data
        zone_map = {}
        if os.path.exists(targets_file):
            with open(targets_file, "r", encoding="utf-8") as tf:
                zone_map = json.load(tf).get("zone_map", {})

        # Auto-detect the latest date that actually has data in the Excel file
        print("Detecting latest date in Excel file...")
        df_dates = pd.read_excel(raw_file, usecols=["Date"], engine="calamine")
        df_dates["Date"] = pd.to_datetime(df_dates["Date"], dayfirst=True, errors="coerce")
        df_dates = df_dates.dropna(subset=["Date"])
        excel_latest_date = df_dates["Date"].max().strftime("%Y-%m-%d")
        print(f"Latest date in Excel: {excel_latest_date}")

        # Generate daily data using the actual latest date in the Excel
        # Generate monthly/quarterly using the review date (month-level data)
        date_map = {
            "daily":     excel_latest_date,
            "monthly":   latest_date,
            "quarterly": latest_date,
        }

        for timeframe, use_date in date_map.items():
            try:
                print(f"  Generating {timeframe} data for {use_date}...")
                data = get_dashboard_data(
                    raw_file, use_date, timeframe,
                    zone_map=zone_map
                )
                # If data has an error (e.g. no rows), try with excel_latest_date
                if isinstance(data, dict) and "error" in data:
                    print(f"  Retrying {timeframe} with excel date {excel_latest_date}...")
                    data = get_dashboard_data(
                        raw_file, excel_latest_date, timeframe,
                        zone_map=zone_map
                    )
                # Store under BOTH the review date and the excel date keys for fallback
                dashboard_mock[f"{latest_date}_{timeframe}"] = data
                dashboard_mock[f"{excel_latest_date}_{timeframe}"] = data
                print(f"  OK {timeframe} done")
            except Exception as e:
                print(f"  FAILED {timeframe}: {e}")
                dashboard_mock[f"{latest_date}_{timeframe}"] = {}
                dashboard_mock[f"{excel_latest_date}_{timeframe}"] = {}
    except Exception as e:
        print(f"Warning: Could not generate dashboard data: {e}")
else:
    print("Warning: No uploaded_sales_report.xlsx found — dashboard will show empty data on Netlify.")

# ---------------------------------------------------------------------------
# Build fetch mock interceptor script
# ---------------------------------------------------------------------------
mock_script = f"""
<script>
// ── Standalone / Netlify mock layer ────────────────────────────────────────
window.__STANDALONE_MODE = true;
window.__MOCK_TARGETS = {targets_data};
window.__MOCK_REVIEWS = {json.dumps(reviews)};
window.__MOCK_REVIEW_DATA = {json.dumps(review_data_dict)};
window.__MOCK_DASHBOARD_DATA = {json.dumps(dashboard_mock)};
window.__MOCK_LATEST_DATE = "{latest_date}";

(function() {{
  const _origFetch = window.fetch;

  window.fetch = async function(input, options) {{
    const urlStr = (typeof input === 'string') ? input : input.toString();

    // ── ClickHouse endpoints ─────────────────────────────────────────────
    if (urlStr.includes('/api/clickhouse/status')) {{
      return new Response(
        JSON.stringify({{ connected: false, error: 'Standalone / Netlify mode — no ClickHouse' }}),
        {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}
    if (urlStr.includes('/api/clickhouse/')) {{
      return new Response(
        JSON.stringify({{ status: 'skipped', reason: 'Standalone mode' }}),
        {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Targets ──────────────────────────────────────────────────────────
    if (urlStr.includes('/api/targets')) {{
      if (options && (options.method || '').toUpperCase() === 'POST') {{
        return new Response(
          JSON.stringify({{ status: 'success', message: 'Saved in standalone mode (not persisted)' }}),
          {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
        );
      }}
      return new Response(
        JSON.stringify(window.__MOCK_TARGETS),
        {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Reviews list ─────────────────────────────────────────────────────
    if (urlStr.includes('/api/reviews')) {{
      return new Response(
        JSON.stringify(window.__MOCK_REVIEWS),
        {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Single review ────────────────────────────────────────────────────
    if (urlStr.includes('/api/review/')) {{
      if (options && (options.method || '').toUpperCase() === 'POST') {{
        return new Response(
          JSON.stringify({{ status: 'success', message: 'Saved in standalone mode (not persisted)' }}),
          {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
        );
      }}
      const parts = urlStr.split('/');
      const date = parts[parts.length - 1].split('?')[0];
      const data = window.__MOCK_REVIEW_DATA[date] || {{}};
      return new Response(
        JSON.stringify(data),
        {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Dashboard data ───────────────────────────────────────────────────
    if (urlStr.includes('/api/dashboard-data')) {{
      try {{
        const urlObj = new URL(urlStr, window.location.href);
        const date      = urlObj.searchParams.get('date')      || window.__MOCK_LATEST_DATE;
        const timeframe = urlObj.searchParams.get('timeframe') || 'daily';
        const key = date + '_' + timeframe;

        // Try exact match first, then fall back to latest available
        let data = window.__MOCK_DASHBOARD_DATA[key];
        if (!data) {{
          // fall back to same timeframe for the latest date
          const fallbackKey = window.__MOCK_LATEST_DATE + '_' + timeframe;
          data = window.__MOCK_DASHBOARD_DATA[fallbackKey] ||
                 window.__MOCK_DASHBOARD_DATA[Object.keys(window.__MOCK_DASHBOARD_DATA)[0]] || {{}};
        }}
        return new Response(
          JSON.stringify(data),
          {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}
        );
      }} catch(e) {{
        return new Response(JSON.stringify({{}}), {{ status: 200 }});
      }}
    }}

    // ── Sub-page analytics (price-range, brand, store, zone, team) ────────
    if (
      urlStr.includes('/api/price-range-data') ||
      urlStr.includes('/api/brand-data')        ||
      urlStr.includes('/api/store-data')         ||
      urlStr.includes('/api/zone-data')          ||
      urlStr.includes('/api/team-data')
    ) {{
      return new Response(
        JSON.stringify({{ error: 'Live data not available in standalone mode. Run the local server to use this feature.' }}),
        {{ status: 404, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Block all other /api/ calls gracefully ───────────────────────────
    if (urlStr.includes('/api/')) {{
      return new Response(
        JSON.stringify({{ error: 'Not available in standalone / Netlify mode.' }}),
        {{ status: 404, headers: {{ 'Content-Type': 'application/json' }} }}
      );
    }}

    // ── Pass through all non-API requests ────────────────────────────────
    return _origFetch.apply(this, arguments);
  }};
}})();
</script>
"""

html_content = html_content.replace('</head>', f'{mock_script}</head>')

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
output_file = os.path.join(base_dir, "myG_Finance_Portal.html")
with open(output_file, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"\nGenerated: {output_file}")
print(f"   Review date: {latest_date}")
print(f"   Excel latest date: {excel_latest_date}")
print(f"   Dashboard keys: {list(dashboard_mock.keys())}")
print(f"   Reviews embedded: {len(reviews)}")
