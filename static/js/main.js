// Global State
let activeDate = "";
let activeEndDate = "";
let reviewData = {};
let globalTargets = {};
let financierChart = null;

// ClickHouse Data Source State
// 'excel' = uploaded file  |  'clickhouse' = live DB (default)
let _dataSource = 'clickhouse';

/** Return the current source param string for API calls (e.g. '&source=clickhouse') */
function _src() {
  return _dataSource === 'clickhouse' ? '&source=clickhouse' : '';
}

/** Return the end_date param for API calls — only populated in custom timeframe */
function _endDate() {
  const tf = (document.getElementById('timeframeSelect') || {}).value || '';
  return (tf === 'custom' && activeEndDate) ? activeEndDate : '';
}


// ── Theme toggle (Dark ↔ Light) ────────────────────────────────────────────
(function initTheme() {
  const saved = localStorage.getItem('myg-theme') || 'dark';
  applyTheme(saved);
})();

function applyTheme(theme) {
  const html = document.documentElement;
  const icon = document.querySelector('#themeToggleBtn .theme-icon');

  if (theme === 'light') {
    html.setAttribute('data-theme', 'light');
    if (icon) icon.textContent = '☀️';
  } else {
    html.removeAttribute('data-theme');
    if (icon) icon.textContent = '🌙';
  }
  localStorage.setItem('myg-theme', theme);
}

window.toggleTheme = function() {
  const current = localStorage.getItem('myg-theme') || 'dark';
  applyTheme(current === 'dark' ? 'light' : 'dark');
};

/** Toggle between Excel and ClickHouse data sources */
window.toggleDataSource = function() {
  _dataSource = (_dataSource === 'excel') ? 'clickhouse' : 'excel';
  const btn   = document.getElementById('btnSourceToggle');
  const label = document.getElementById('chSourceLabel');
  if (_dataSource === 'clickhouse') {
    btn.classList.add('active-ch');
    label.textContent = 'Live DB';
    btn.title = 'Currently using: ClickHouse (Live). Click to switch to Uploaded Excel.';
  } else {
    btn.classList.remove('active-ch');
    label.textContent = 'Uploaded';
    btn.title = 'Currently using: Uploaded Excel. Click to switch to Live ClickHouse.';
  }
  // Reload dashboard with the new source
  const timeframe = document.getElementById('timeframeSelect') ? document.getElementById('timeframeSelect').value : 'daily';
  if (activeDate) loadDashboardData(activeDate, timeframe);
};

/**
 * Refresh only the currently active tab's data — no full page reload.
 * Clears the API cache for the current date then re-runs the relevant loader.
 */
window.refreshCurrentTab = function() {
  const btn  = document.getElementById('btnRefreshTab');
  const icon = document.getElementById('refreshTabIcon');

  // Spin animation
  if (icon) icon.style.animation = 'spin360 0.7s linear infinite';
  if (btn)  btn.disabled = true;

  const stopSpin = () => {
    if (icon) icon.style.animation = '';
    if (btn)  btn.disabled = false;
  };

  // Detect active tab from sidebar nav
  const activeNav = document.querySelector('.nav-item.active');
  const tab = activeNav ? activeNav.getAttribute('data-tab') : 'dashboard';

  // Clear cached data for the current date so we get fresh data
  if (activeDate) _apiCacheClearDate(activeDate);

  const timeframe = (document.getElementById('timeframeSelect') || {}).value || 'daily';

  const done = () => { stopSpin(); showToast(`Tab refreshed ✓`, 'success'); };
  const fail = (e) => { stopSpin(); showToast(`Refresh failed: ${e}`, 'error'); };

  try {
    switch (tab) {
      case 'dashboard':
        loadDashboardData(activeDate, timeframe);
        setTimeout(done, 800);
        break;
      case 'exec-summary':
        loadDashboardData(activeDate, timeframe);
        setTimeout(() => { renderExecSummary(); done(); }, 900);
        break;
      case 'disbursement':
        loadDashboardData(activeDate, timeframe);
        setTimeout(() => { renderDisbursement(); done(); }, 900);
        break;
      case 'category':
        loadDashboardData(activeDate, timeframe);
        setTimeout(() => { renderCategoryPerformance(); done(); }, 900);
        break;
      case 'contest':
        loadDashboardData(activeDate, timeframe);
        setTimeout(() => { renderContest(); done(); }, 900);
        break;
      case 'manpower':
        loadDashboardData(activeDate, timeframe);
        setTimeout(() => { renderManpower(); done(); }, 900);
        break;
      case 'price':
        loadPriceRangeData();
        setTimeout(done, 800);
        break;
      case 'brand':
        loadBrandData();
        setTimeout(done, 800);
        break;
      case 'store':
        loadStoreData();
        setTimeout(done, 800);
        break;
      case 'oct-sheets':
        loadOctSheetsData(true);
        setTimeout(done, 800);
        break;
      default:
        // Generic fallback — reload all dashboard data
        loadDashboardData(activeDate, timeframe);
        setTimeout(done, 900);
    }
  } catch(e) {
    fail(e);
  }
};


let _emiVersion = null;
let _chWakeupRetries = 0;
let _chRetryTimer = null;
let _lastChRowsLabel = 'Live';

function _markClickHouseOnline(rowCountLabel) {
  if (rowCountLabel) _lastChRowsLabel = rowCountLabel;
  _chWakeupRetries = 0;
  clearTimeout(_chRetryTimer);
  const badge = document.getElementById('chStatusBadge');
  const text  = document.getElementById('chStatusText');
  if (badge) badge.className = 'ch-status-badge online';
  if (text) text.textContent = _lastChRowsLabel || 'Live';
}

/** Poll /api/clickhouse/status and update the badge */
function _pollClickHouseStatus() {
  clearTimeout(_chRetryTimer);
  fetch('/api/clickhouse/status')
    .then(async r => {
      const txt = await r.text();
      try { return JSON.parse(txt); } catch (e) { throw new Error('Non-JSON status response'); }
    })
    .then(data => {
      if (data.emi_version !== undefined) {
        if (_emiVersion !== null && _emiVersion !== data.emi_version) {
          // Pine/Paytm/Benow upload state changed on the server — drop stale cached responses
          _apiCache.clear();
          if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
        }
        _emiVersion = data.emi_version;
      }
      const badge = document.getElementById('chStatusBadge');
      const text  = document.getElementById('chStatusText');
      if (!badge) return;
      if (data.connected) {
        const rows = data.row_counts && data.row_counts.azure_sales_report
          ? (data.row_counts.azure_sales_report / 1e6).toFixed(1) + 'M rows'
          : 'Live';
        _markClickHouseOnline(rows);
        badge.title = `ClickHouse connected\nCache entries: ${data.cache_entries || 0}\n` +
          Object.entries(data.row_counts || {}).map(([k,v]) => `${k}: ${v.toLocaleString()}`).join('\n');
      } else {
        if (data.warming || _chWakeupRetries < 25) {
          _chWakeupRetries++;
          badge.className = 'ch-status-badge';
          if (text) text.textContent = data.warming ? 'Warming up\u2026' : 'Connecting\u2026';
          badge.title = data.warming ? 'Warming ClickHouse Cloud cache\u2026' : 'Waking ClickHouse Cloud\u2026';
          _chRetryTimer = setTimeout(_pollClickHouseStatus, 4000);
        } else {
          badge.className = 'ch-status-badge offline';
          if (text) text.textContent = 'Offline';
          badge.title = data.error || 'ClickHouse not reachable';
        }
      }
    })
    .catch(() => {
      const badge = document.getElementById('chStatusBadge');
      const text  = document.getElementById('chStatusText');
      if (_chWakeupRetries < 25) {
        _chWakeupRetries++;
        if (badge) badge.className = 'ch-status-badge';
        if (text) text.textContent = 'Connecting\u2026';
        _chRetryTimer = setTimeout(_pollClickHouseStatus, 4000);
      } else {
        if (badge) badge.className = 'ch-status-badge offline';
        if (text) text.textContent = 'Offline';
      }
    });
}

// ---------------------------------------------------------------------------
// JS-side API response cache — instant timeframe switching after first load
// ---------------------------------------------------------------------------
const _apiCache = new Map();   // url → { data, ts }
const _API_CACHE_TTL = 30 * 60 * 1000;  // 30 min in ms

function _apiCacheGet(url) {
  const entry = _apiCache.get(url);
  if (!entry) return null;
  if (Date.now() - entry.ts > _API_CACHE_TTL) { _apiCache.delete(url); return null; }
  return entry.data;
}
function _apiCacheSet(url, data) {
  _apiCache.set(url, { data, ts: Date.now() });
}
function _apiCacheClearDate(date) {
  // Remove all cached entries for this date when date changes
  for (const key of _apiCache.keys()) {
    if (key.includes(`date=${date}`)) _apiCache.delete(key);
  }
}

// ---------------------------------------------------------------------------
// Global data-loading indicator: top progress bar + floating status pill with a live
// elapsed-time counter, and the page content is dimmed while any request is in flight.
// ---------------------------------------------------------------------------
let _loadingCount = 0, _loadingStart = 0, _loadingTick = null, _loadingHideT = null, _loadingWakeup = false;
function _loadingEls() {
  let pill = document.getElementById('global-loading-pill');
  if (!pill) {
    const bar = document.createElement('div');
    bar.id = 'global-loading-bar';
    document.body.appendChild(bar);
    pill = document.createElement('div');
    pill.id = 'global-loading-pill';
    pill.innerHTML = '<span class="gl-spinner"></span><span class="gl-text"></span>';
    document.body.appendChild(pill);
  }
  return { bar: document.getElementById('global-loading-bar'), pill, text: pill.querySelector('.gl-text') };
}
function _loadingLabel(secs) {
  return _loadingWakeup ? `Connecting to live database\u2026 ${secs}s` : `Loading data\u2026 ${secs}s`;
}
function _loadingBegin() {
  const el = _loadingEls();
  clearTimeout(_loadingHideT);
  if (_loadingCount++ === 0) {
    _loadingWakeup = false;
    _loadingStart = Date.now();
    document.body.classList.add('data-loading');
    el.pill.className = 'visible';
    el.bar.className = 'active';
    el.text.textContent = _loadingLabel(0);
    clearInterval(_loadingTick);
    _loadingTick = setInterval(() => {
      el.text.textContent = _loadingLabel(Math.floor((Date.now() - _loadingStart) / 1000));
    }, 500);
  }
}
function _loadingEnd(ok) {
  _loadingCount = Math.max(0, _loadingCount - 1);
  if (_loadingCount > 0) return;
  _loadingWakeup = false;
  const el = _loadingEls();
  clearInterval(_loadingTick);
  document.body.classList.remove('data-loading');
  el.bar.className = '';
  const secs = ((Date.now() - _loadingStart) / 1000).toFixed(1);
  el.pill.className = 'visible ' + (ok ? 'done' : 'failed');
  el.text.textContent = ok ? `\u2713 Data loaded in ${secs}s` : '\u2717 Failed to load data - try Refresh';
  _loadingHideT = setTimeout(() => { el.pill.className = ''; }, ok ? 1500 : 4000);
}

// Monotonic id so a slow, older response can never overwrite a newer date/filter selection.
let _dashReqSeq = 0; let _catFilterReqSeq = 0; let _reviewReqSeq = 0;
const _inFlightFetches = new Map();  // url -> Promise

function _livePeriodKey(date, tf, endD) {
  return `${date || ''}|${tf || 'daily'}|${tf === 'custom' ? (endD || '') : ''}`;
}

function _setCategoryTableStatus(html) {
  ['#table-cat-qty tbody', '#table-cat-val tbody'].forEach(sel => {
    const tb = document.querySelector(sel);
    if (tb) tb.innerHTML = html;
  });
}

function _isTransientErrorMsg(msg) {
  const m = String(msg || '').toLowerCase();
  return /timed? ?out|connect|reset|broken pipe|eof|502|503|504|temporarily|clickhouse|starting|network|failed to fetch/.test(m);
}

function _fetchJsonWithRetry(url, opts) {
  const silent = !!(opts && opts.silent);
  const delays = [2000, 4000, 6000, 8000];

  function attempt(idx) {
    return fetch(url)
      .then(async res => {
        const text = await res.text();
        let data = null;
        try {
          data = text ? JSON.parse(text) : {};
        } catch (e) {
          if (res.status >= 500 || res.status === 0) {
            const err = new Error(`HTTP ${res.status} non-JSON response`);
            err.retryable = true;
            throw err;
          }
          throw new Error(`Invalid JSON (${res.status})`);
        }
        if (res.status === 502 || res.status === 503 || res.status === 504) {
          const err = new Error((data && data.error) || `HTTP ${res.status}`);
          err.retryable = true;
          throw err;
        }
        if (res.status >= 500 && data && (data.error || data.message) && _isTransientErrorMsg(data.error || data.message)) {
          const err = new Error(data.error || data.message);
          err.retryable = true;
          throw err;
        }
        return data;
      })
      .catch(err => {
        const isNet = err instanceof TypeError;
        if ((err.retryable || isNet) && idx < delays.length) {
          if (!silent) {
            _loadingWakeup = true;
            const el = _loadingEls();
            el.text.textContent = _loadingLabel(Math.floor((Date.now() - _loadingStart) / 1000));
          }
          return new Promise(resolve => setTimeout(resolve, delays[idx])).then(() => attempt(idx + 1));
        }
        throw err;
      });
  }
  return attempt(0);
}

/** Fetch with JS-side cache + in-flight deduplication + cold-start retry. Returns a Promise<data>. */
function _cachedFetch(url, opts) {
  const silent = !!(opts && opts.silent);
  const cached = _apiCacheGet(url);
  if (cached) {
    console.debug('[cache HIT]', url);
    if (url.includes('source=clickhouse')) _markClickHouseOnline();
    return Promise.resolve(cached);
  }
  if (_inFlightFetches.has(url)) {
    return _inFlightFetches.get(url);
  }
  if (!silent) _loadingBegin();
  const p = _fetchJsonWithRetry(url, opts)
    .then(data => {
      _inFlightFetches.delete(url);
      if (data && !data.error) {
        _apiCacheSet(url, data);  // only cache successful responses
        if (url.includes('source=clickhouse')) _markClickHouseOnline();
      }
      if (!silent) _loadingEnd(!(data && data.error));
      return data;
    })
    .catch(err => {
      _inFlightFetches.delete(url);
      if (!silent) _loadingEnd(false);
      throw err;
    });
  _inFlightFetches.set(url, p);
  return p;
}

/** Fire polite debounced background prewarm for a date + current timeframe. */
function _prewarmClickHouse(date, timeframe) {
  if (_dataSource !== 'clickhouse' || !date) return;
  const tf = timeframe || ((document.getElementById('timeframeSelect') || {}).value || 'monthly');
  fetch('/api/clickhouse/prewarm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date, timeframe: tf })
  }).catch(() => {});  // fire-and-forget
}


// Initialize Page
document.addEventListener("DOMContentLoaded", () => {
  initSidebar();
  initDateSelector();
  initCSVUpload();
  initCleanerPage();
  initActions();
  initScrollPerformance();
  loadGlobalTargetsForChart();
  initModals();

  // Poll ClickHouse status immediately + every 60 s
  _pollClickHouseStatus();
  setInterval(_pollClickHouseStatus, 60000);

  const catFilter = document.getElementById("filter-category");
  if (catFilter) {
    catFilter.addEventListener("change", applyCategoryFilter);
  }
  
  const brandFilter = document.getElementById("filter-brand");
  if (brandFilter) {
    brandFilter.addEventListener("change", () => {
      renderCategoryPerformance();
      // Re-apply category filter after re-rendering to keep UI consistent
      applyCategoryFilter();
    });
  }
  
  // Removed redundant zone-timeframe logic
  
  // Zone RBM filter — re-render tables without a new API call
  const zoneRbmFilter = document.getElementById("zone-rbm-filter");
  if (zoneRbmFilter) {
    zoneRbmFilter.addEventListener("change", () => {
      renderZoneCategoryTable();
    });
  }
  
  // Price Range tab
  const priceRangeFilter = document.getElementById("price-range-filter");
  if (priceRangeFilter) {
    priceRangeFilter.addEventListener("change", () => loadPriceRangeData());
  }

  // Brand Performance tab
  const brandCatFilter = document.getElementById("brand-category-filter");
  const brandProductFilter = document.getElementById("brand-product-filter");
  const brandProductFilterContainer = document.getElementById("brand-product-filter-container");
  if (brandCatFilter) {
    brandCatFilter.addEventListener("change", () => {
      if (brandCatFilter.value === "CD PRODUCTS") {
        if (brandProductFilterContainer) brandProductFilterContainer.style.display = "flex";
      } else {
        if (brandProductFilterContainer) brandProductFilterContainer.style.display = "none";
        if (brandProductFilter) brandProductFilter.value = "ALL";
      }
      loadBrandData();
    });
  }
  if (brandProductFilter) brandProductFilter.addEventListener("change", loadBrandData);

  // Store Performance tab
  const storeZoneFilter = document.getElementById("store-zone-filter");
  if (storeZoneFilter) storeZoneFilter.addEventListener("change", loadStoreData);

  // Initialize multi-select State filters across all tabs with default states
  renderStateMulti(["KARNATAKA", "KERALA", "OTHERS"]);
});

// =====================================================
// PRICE RANGE RENDERING
// =====================================================

function loadPriceRangeData() {
  if (!activeDate) return;
  const timeframe  = (document.getElementById("timeframeSelect") || {}).value || "monthly";
  const priceRange = (document.getElementById("price-range-filter") || {}).value || "ALL";
  const state      = ((document.getElementById("price-filter-state") || document.getElementById("cat-filter-state") || {}).value || "");
  
  const qtyBody = document.getElementById("price-qty-body");
  const valBody = document.getElementById("price-val-body");
  if (qtyBody) qtyBody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--text-muted); padding:20px;">Loading...</td></tr>`;
  if (valBody) valBody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--text-muted); padding:20px;">Loading...</td></tr>`;
  
  const url = `/api/price-range-data?date=${activeDate}&end_date=${_endDate()}&timeframe=${timeframe}&price_range=${encodeURIComponent(priceRange)}&state=${encodeURIComponent(state)}${_src()}`;
  _cachedFetch(url)
    .then(data => {
      if (data.error) {
        if (qtyBody) qtyBody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--color-warning); padding:20px;">${data.error}</td></tr>`;
        if (valBody) valBody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--color-warning); padding:20px;">${data.error}</td></tr>`;
        return;
      }
      renderPriceRangeTable(data.qty, data.val);
    })
    .catch(err => {
      console.error("Price range fetch error:", err);
      if (qtyBody) qtyBody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--color-danger); padding:20px;">Failed to load data</td></tr>`;
    });
}

function renderPriceRangeTable(qtyData, valData) {
  const cats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"];
  const fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  const paperFins = new Set(["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]);
  const cardFins  = new Set(["PineLab", "PAYTM", "Benow"]);
  
  const qtyBody = document.getElementById("price-qty-body");
  const valBody = document.getElementById("price-val-body");
  if (!qtyBody || !valBody) return;
  qtyBody.innerHTML = "";
  valBody.innerHTML = "";
  
  function buildTable(body, dataObj, isQty) {
    const fmt = isQty ? (v) => Math.round(v).toLocaleString() : (v) => formatNumber(v, "decimal");
    
    const gtRow = {}; fins.forEach(f => { gtRow[f] = 0; });
    let gtTotal = 0, gtPaper = 0, gtCard = 0, gtQtySum = 0, gtValSum = 0;
    const rowItems = [];
    
    cats.forEach(cat => {
      const d = dataObj[cat];
      if (!d) return;
      const total = fins.reduce((s, f) => s + (d[f] || 0), 0);
      if (total === 0) return;
      
      const paper = [...paperFins].reduce((s, f) => s + (d[f] || 0), 0);
      const card  = [...cardFins].reduce((s,  f) => s + (d[f] || 0), 0);
      const acash = d["aCASH"] || 0;
      const finShare = total > 0 ? ((total - acash) / total) : 0;
      
      fins.forEach(f => { gtRow[f] += (d[f] || 0); });
      gtTotal += total; gtPaper += paper; gtCard += card;
      gtQtySum += (d.qty_total || 0);
      gtValSum += (d.value_total || 0);
      
      rowItems.push({ cat, d, total, paper, card, finShare });
    });
    
    // Data rows
    rowItems.forEach(({ cat, d, total, paper, card, finShare }) => {
      const tr = document.createElement("tr");
      let tds = `<td style="font-weight:600;">${cat}</td>`;
      fins.forEach(f => { tds += `<td class="num-align">${fmt(d[f] || 0)}</td>`; });
      tds += `
        <td class="num-align" style="font-weight:700; color:#2563eb;">${fmt(total)}</td>
        <td class="num-align" style="color:#d97706;">${formatNumber(finShare * 100, "percent")}</td>
        <td class="num-align" style="color:#059669;">${fmt(paper)}</td>
        <td class="num-align" style="color:#4338ca;">${fmt(card)}</td>
      `;
      tr.innerHTML = tds;
      body.appendChild(tr);
    });
    
    // Grand Total row
    const gtTr = document.createElement("tr");
    gtTr.className = "total-row";
    const gtAcash = gtRow["aCASH"] || 0;
    const gtFinShare = gtTotal > 0 ? ((gtTotal - gtAcash) / gtTotal) : 0;
    let gtTds = `<td>Grand Total</td>`;
    fins.forEach(f => { gtTds += `<td class="num-align">${fmt(gtRow[f] || 0)}</td>`; });
    gtTds += `
      <td class="num-align" style="font-weight:700; color:#2563eb;">${fmt(gtTotal)}</td>
      <td class="num-align" style="color:#d97706;">${formatNumber(gtFinShare * 100, "percent")}</td>
      <td class="num-align" style="color:#059669;">${fmt(gtPaper)}</td>
      <td class="num-align" style="color:#4338ca;">${fmt(gtCard)}</td>
    `;
    gtTr.innerHTML = gtTds;
    body.appendChild(gtTr);
    
    // Share row (% of grand total)
    const shareTr = document.createElement("tr");
    shareTr.style.cssText = "background:rgba(255,255,255,0.01); color:var(--text-muted);";
    let shareTds = `<td style="font-weight:600;">Share</td>`;
    fins.forEach(f => {
      const sh = gtTotal > 0 ? ((gtRow[f] || 0) / gtTotal * 100) : 0;
      shareTds += `<td class="num-align">${formatNumber(sh, "percent")}</td>`;
    });
    shareTds += `<td class="num-align">-</td><td class="num-align">-</td>
      <td class="num-align">${formatNumber(gtTotal > 0 ? (gtPaper / gtTotal * 100) : 0, "percent")}</td>
      <td class="num-align">${formatNumber(gtTotal > 0 ? (gtCard / gtTotal * 100) : 0, "percent")}</td>`;
    shareTr.innerHTML = shareTds;
    body.appendChild(shareTr);
    
    // Share in Fin Value (fin only %)
    const sfvTr = document.createElement("tr");
    sfvTr.style.cssText = "background:rgba(255,255,255,0.01); color:var(--text-muted);";
    const finOnlyTotal = gtTotal - (gtRow["aCASH"] || 0);
    let sfvTds = `<td style="font-weight:600;">Share in Fin Value</td>`;
    fins.forEach(f => {
      const v = (f === "aCASH") ? 0 : (finOnlyTotal > 0 ? ((gtRow[f] || 0) / finOnlyTotal * 100) : 0);
      sfvTds += `<td class="num-align">${f === "aCASH" ? "-" : formatNumber(v, "percent")}</td>`;
    });
    sfvTds += `<td class="num-align">-</td><td class="num-align">-</td>
      <td class="num-align">${formatNumber(finOnlyTotal > 0 ? (gtPaper / finOnlyTotal * 100) : 0, "percent")}</td>
      <td class="num-align">${formatNumber(finOnlyTotal > 0 ? (gtCard / finOnlyTotal * 100) : 0, "percent")}</td>`;
    sfvTr.innerHTML = sfvTds;
    body.appendChild(sfvTr);
    
    // ASP row (value only)
    if (!isQty) {
      const aspTr = document.createElement("tr");
      aspTr.style.cssText = "background:rgba(255,255,255,0.01); color:var(--text-muted);";
      let aspTds = `<td style="font-weight:600; color:#d97706;">ASP</td>`;
      fins.forEach(f => {
        // ASP per fin = val[fin] / (approx qty)
        aspTds += `<td class="num-align">-</td>`;
      });
      const asp = gtQtySum > 0 ? (gtValSum / gtQtySum) : 0;
      aspTds += `<td class="num-align" style="font-weight:700; color:#d97706;">${formatNumber(asp, "decimal")}</td>
        <td class="num-align">-</td><td class="num-align">-</td><td class="num-align">-</td>`;
      aspTr.innerHTML = aspTds;
      body.appendChild(aspTr);
    }
  }
  
  buildTable(qtyBody, qtyData, true);
  buildTable(valBody, valData, false);
}



// =====================================================
// EXPORT TO EXCEL
// =====================================================
function exportTableToExcel(tbodyId, filename) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) { showToast("Table not found", "error"); return; }
  const table = tbody.closest("table");
  if (!table) { showToast("Table not found", "error"); return; }
  try {
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.table_to_sheet(table);
    XLSX.utils.book_append_sheet(wb, ws, filename.substring(0, 31));
    XLSX.writeFile(wb, `${filename}_${activeDate || "data"}.xlsx`);
    showToast(`✅ Exported ${filename}.xlsx`, "success");
  } catch(e) {
    showToast("Export failed: " + e.message, "error");
  }
}

// =====================================================
// BRAND PERFORMANCE
// =====================================================
function loadBrandData() {
  if (!activeDate) return;
  const timeframe = (document.getElementById("timeframeSelect") || {}).value || "monthly";
  const category  = (document.getElementById("brand-category-filter") || {}).value || "ALL";
  const product   = (document.getElementById("brand-product-filter") || {}).value || "ALL";
  const state     = ((document.getElementById("brand-filter-state") || document.getElementById("cat-filter-state") || {}).value || "");
  const tbody = document.getElementById("brand-perf-body");
  if (tbody) tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--text-muted); padding:20px;">Loading...</td></tr>`;
  const url = `/api/brand-data?date=${activeDate}&end_date=${_endDate()}&timeframe=${timeframe}&category=${encodeURIComponent(category)}&product=${encodeURIComponent(product)}&state=${encodeURIComponent(state)}${_src()}`;
  _cachedFetch(url)
    .then(data => {
      if (data.error) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--color-warning); padding:20px;">${data.error}</td></tr>`;
        return;
      }
      renderBrandTable(data.brands || []);
    }).catch(e => {
      if (tbody) tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--color-danger); padding:20px;">Failed to load</td></tr>`;
    });
}

function renderBrandTable(brands) {
  const tbody = document.getElementById("brand-perf-body");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!brands.length) {
    tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; color:var(--text-muted); padding:30px;">No data</td></tr>`;
    return;
  }
  const fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  brands.forEach((b, i) => {
    const tr = document.createElement("tr");
    const finPctColor = b.fin_pct >= 60 ? "#059669" : b.fin_pct >= 40 ? "#d97706" : "#f87171";
    let tds = `<td style="font-weight:600;">${i+1}. ${b.brand}</td>`;
    fins.forEach(f => { tds += `<td class="num-align">${(b[f] || 0).toLocaleString()}</td>`; });
    tds += `
      <td class="num-align" style="font-weight:700; color:#2563eb;">${(b.total || 0).toLocaleString()}</td>
      <td class="num-align" style="color:${finPctColor}; font-weight:600;">${b.fin_pct || 0}%</td>
      <td class="num-align">₹${formatNumber(b.value || 0, "decimal")}</td>
      <td class="num-align" style="color:#4338ca;">₹${(b.asp || 0).toLocaleString()}</td>
    `;
    tr.innerHTML = tds;
    tbody.appendChild(tr);
  });
  // Grand Total row
  const totals = { total: 0, value: 0 };
  fins.forEach(f => { totals[f] = 0; });
  brands.forEach(b => { fins.forEach(f => { totals[f] += (b[f] || 0); }); totals.total += (b.total || 0); totals.value += (b.value || 0); });
  const gtTr = document.createElement("tr");
  gtTr.className = "total-row";
  let gtTds = `<td>Grand Total</td>`;
  fins.forEach(f => { gtTds += `<td class="num-align">${(totals[f] || 0).toLocaleString()}</td>`; });
  const gtAcash = totals["aCASH"] || 0;
  const gtFinPct = totals.total > 0 ? ((totals.total - gtAcash) / totals.total * 100).toFixed(1) : 0;
  gtTds += `
    <td class="num-align" style="font-weight:700; color:#2563eb;">${totals.total.toLocaleString()}</td>
    <td class="num-align" style="color:#d97706;">${gtFinPct}%</td>
    <td class="num-align">₹${formatNumber(totals.value, "decimal")}</td>
    <td class="num-align" style="color:#4338ca;">₹${totals.total > 0 ? Math.round(totals.value / totals.total).toLocaleString() : 0}</td>
  `;
  gtTr.innerHTML = gtTds;
  tbody.appendChild(gtTr);
}

// =====================================================
// STORE PERFORMANCE
// =====================================================
function loadStoreData() {
  if (!activeDate) return;
  const timeframe = (document.getElementById("timeframeSelect") || {}).value || "monthly";
  const zone      = (document.getElementById("store-zone-filter") || {}).value || "ALL";
  const state     = ((document.getElementById("store-filter-state") || document.getElementById("cat-filter-state") || {}).value || "");
  const tbody = document.getElementById("store-perf-body");
  if (tbody) tbody.innerHTML = `<tr><td colspan="16" style="text-align:center; color:var(--text-muted); padding:20px;">Loading...</td></tr>`;
  const url = `/api/store-data?date=${activeDate}&end_date=${_endDate()}&timeframe=${timeframe}&zone=${encodeURIComponent(zone)}&state=${encodeURIComponent(state)}${_src()}`;
  _cachedFetch(url)
    .then(data => {
      if (data.error) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="16" style="text-align:center; color:var(--color-warning); padding:20px;">${data.error}</td></tr>`;
        return;
      }
      // Populate zone dropdown
      const zoneSelect = document.getElementById("store-zone-filter");
      if (zoneSelect && data.zones && data.zones.length) {
        const currentVal = zoneSelect.value;
        zoneSelect.innerHTML = `<option value="ALL">All Zones</option>`;
        data.zones.forEach(z => { zoneSelect.innerHTML += `<option value="${z}">${z}</option>`; });
        if (currentVal && [...zoneSelect.options].some(o => o.value === currentVal)) zoneSelect.value = currentVal;
      }
      renderStoreTable(data.stores || []);
    }).catch(() => {
      if (tbody) tbody.innerHTML = `<tr><td colspan="16" style="text-align:center; color:var(--color-danger); padding:20px;">Failed to load</td></tr>`;
    });
}

function renderStoreTable(stores) {
  const tbody = document.getElementById("store-perf-body");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!stores.length) {
    tbody.innerHTML = `<tr><td colspan="16" style="text-align:center; color:var(--text-muted); padding:30px;">No data</td></tr>`;
    return;
  }
  const fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab"];
  const zoneColors = { "NORTH": "#2563eb", "SOUTH": "#059669", "KARNATAKA": "#d97706", "OTHER": "#94a3b8" };

  stores.forEach((s, i) => {
    const tr = document.createElement("tr");
    const finPctColor = s.fin_pct >= 60 ? "#059669" : s.fin_pct >= 40 ? "#d97706" : "#f87171";
    const zoneColor = zoneColors[s.zone] || "#94a3b8";
    let tds = `<td style="font-weight:600;">${i+1}. ${s.branch}</td>
      <td class="num-align"><span style="color:${zoneColor}; font-weight:600;">${s.zone}</span></td>
      <td class="num-align" style="color:var(--text-muted);">${s.rbm}</td>
      <td class="num-align" style="font-weight:700;">${(s.total_qty || 0).toLocaleString()}</td>
      <td class="num-align">${(s.fin_qty || 0).toLocaleString()}</td>
      <td class="num-align" style="color:${finPctColor}; font-weight:600;">${s.fin_pct || 0}%</td>`;
    fins.forEach(f => { tds += `<td class="num-align">${(s[f] || 0).toLocaleString()}</td>`; });
    tds += `<td class="num-align" style="color:#2563eb;">₹${formatNumber(s.total_val || 0, "decimal")}</td>
      <td class="num-align" style="color:#4338ca;">₹${(s.asp || 0).toLocaleString()}</td>`;
    tr.innerHTML = tds;
    tbody.appendChild(tr);
  });

  // Grand Total
  const gt = { total_qty: 0, fin_qty: 0, total_val: 0 };
  fins.forEach(f => { gt[f] = 0; });
  stores.forEach(s => {
    gt.total_qty += (s.total_qty || 0); gt.fin_qty += (s.fin_qty || 0); gt.total_val += (s.total_val || 0);
    fins.forEach(f => { gt[f] += (s[f] || 0); });
  });
  const gtFin = gt.total_qty > 0 ? ((gt.fin_qty / gt.total_qty) * 100).toFixed(1) : 0;
  const gtAsp = gt.total_qty > 0 ? Math.round(gt.total_val / gt.total_qty).toLocaleString() : 0;
  const gtTr = document.createElement("tr");
  gtTr.className = "total-row";
  let gtTds = `<td>Grand Total</td><td class="num-align">-</td><td class="num-align">-</td>
    <td class="num-align" style="font-weight:700;">${gt.total_qty.toLocaleString()}</td>
    <td class="num-align">${gt.fin_qty.toLocaleString()}</td>
    <td class="num-align" style="color:#d97706;">${gtFin}%</td>`;
  fins.forEach(f => { gtTds += `<td class="num-align">${(gt[f] || 0).toLocaleString()}</td>`; });
  gtTds += `<td class="num-align" style="color:#2563eb;">₹${formatNumber(gt.total_val, "decimal")}</td>
    <td class="num-align" style="color:#4338ca;">₹${gtAsp}</td>`;
  gtTr.innerHTML = gtTds;
  tbody.appendChild(gtTr);
}

// Scroll Performance: disable hover paint during scroll
function initScrollPerformance() {
  const pageBody = document.querySelector(".page-body");
  if (!pageBody) return;
  let scrollTimer = null;
  pageBody.addEventListener("scroll", () => {
    if (!pageBody.classList.contains("is-scrolling")) {
      pageBody.classList.add("is-scrolling");
    }
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      pageBody.classList.remove("is-scrolling");
    }, 150);
  }, { passive: true });
}

// Sidebar Navigation
function initSidebar() {
  const navItems = document.querySelectorAll(".nav-item");
  const tabPages = document.querySelectorAll(".tab-page");

  navItems.forEach(item => {
    item.addEventListener("click", () => {
      const tab = item.getAttribute("data-tab");
      
      // Update active nav class
      navItems.forEach(n => n.classList.remove("active"));
      item.classList.add("active");
      
      // Update visible tab page
      tabPages.forEach(p => p.classList.remove("active"));
      document.getElementById(`tab-${tab}`).classList.add("active");
      
      // Re-render chart if switching to dashboard
      if (tab === "dashboard") {
        setTimeout(renderChart, 100);
      }

      if (tab === "exec-summary" && activeDate) {
        setTimeout(() => loadMonthwisePerformance(activeDate), 50);
      }
      
      // Load price range data on tab switch
      if (tab === "price") {
        setTimeout(loadPriceRangeData, 100);
      }
      
      // Load brand data on tab switch
      if (tab === "brand") {
        setTimeout(loadBrandData, 100);
      }
      
      // Load store data on tab switch
      if (tab === "oct-sheets") {
        setTimeout(() => loadOctSheetsData(false), 50);
      }
      if (tab === "store") {
        setTimeout(loadStoreData, 100);
      }

      
      // Re-trigger icon updates
      lucide.createIcons();
    });
  });
}

// Date Selector Management
function initDateSelector() {
  const dateInput   = document.getElementById("reviewDateSelect");
  const endDateInput= document.getElementById("endDateSelect");
  const endSep      = document.getElementById("endDateSeparator");
  const yearInput   = document.getElementById("periodYear");
  const monthPills  = document.getElementById("monthPills");
  const quarterPills= document.getElementById("quarterPills");
  const tfSelect    = document.getElementById("timeframeSelect");

  // Quarter → month start map  (Q1=Jan, Q2=Apr, Q3=Jul, Q4=Oct)
  const Q_START_MO  = { 1:1, 2:4, 3:7, 4:10 };

  // --- UI switch based on selected timeframe ---
  function applyPickerMode(tf) {
    if (tf === 'daily') {
      dateInput.style.display    = 'block';
      if (endDateInput) endDateInput.style.display = 'none';
      if (endSep) endSep.style.display = 'none';
      yearInput.style.display    = 'none';
      monthPills.style.display   = 'none';
      quarterPills.style.display = 'none';
    } else if (tf === 'custom') {
      dateInput.style.display    = 'block';
      if (endDateInput) endDateInput.style.display = 'block';
      if (endSep) endSep.style.display = 'inline-block';
      yearInput.style.display    = 'none';
      monthPills.style.display   = 'none';
      quarterPills.style.display = 'none';
    } else if (tf === 'monthly') {
      dateInput.style.display    = 'none';
      if (endDateInput) endDateInput.style.display = 'none';
      if (endSep) endSep.style.display = 'none';
      yearInput.style.display    = 'block';
      monthPills.style.display   = 'flex';
      quarterPills.style.display = 'none';
    } else {  // quarterly
      dateInput.style.display    = 'none';
      if (endDateInput) endDateInput.style.display = 'none';
      if (endSep) endSep.style.display = 'none';
      yearInput.style.display    = 'block';
      monthPills.style.display   = 'none';
      quarterPills.style.display = 'flex';
    }
  }

  // --- Build ISO date from year + month ---
  function makeDate(year, month) {
    return `${year}-${String(month).padStart(2, '0')}-01`;
  }

  // --- Mark the correct pill as active ---
  function highlightPills(tf) {
    if (!activeDate) return;
    const d = new Date(activeDate + 'T00:00:00');
    const mo = d.getMonth() + 1;
    const yr = d.getFullYear();

    if (tf === 'monthly') {
      monthPills.querySelectorAll('.period-pill').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.mo) === mo);
      });
    } else if (tf === 'quarterly') {
      const q = Math.ceil(mo / 3);
      quarterPills.querySelectorAll('.period-pill').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.q) === q);
      });
    }
  }

  // --- Trigger full reload ---
  function reloadAll(tf) {
    _apiCacheClearDate(activeDate);
    loadData(activeDate, { skipDashboard: true });
    loadDashboardData(activeDate, tf);
    if (document.getElementById("tab-price")?.classList.contains("active")) loadPriceRangeData();
    if (document.getElementById("tab-brand")?.classList.contains("active")) loadBrandData();
    if (document.getElementById("tab-store")?.classList.contains("active")) loadStoreData();
    if (document.getElementById("tab-oct-sheets")?.classList.contains("active") && typeof loadOctSheetsData === "function") loadOctSheetsData(false);
    _prewarmClickHouse(activeDate, tf);
  }

  // --- Initialise year input from current activeDate ---
  function syncYearInput() {
    if (activeDate) {
      yearInput.value = activeDate.split('-')[0];
    }
  }

  // ----------------------------------------------------------------
  // Bootstrap: fetch saved review dates and set defaults
  // ----------------------------------------------------------------
  _fetchJsonWithRetry("/api/reviews", { silent: true })
    .then(dates => {
      const tf = tfSelect.value;
      if (!Array.isArray(dates) || dates.length === 0) {
        // No saved reviews — default to current month in monthly mode
        const today = new Date();
        const monthStart = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-01`;
        activeDate = monthStart;
        dateInput.value = monthStart;
        // Switch to monthly timeframe for a better initial view
        if (tfSelect.value === 'daily') {
          tfSelect.value = 'monthly';
          applyPickerMode('monthly');
        }
      } else {
        dateInput.value = dates[0];
        activeDate = dates[0];
      }
      
      if (endDateInput) {
        endDateInput.value = activeDate;
        activeEndDate = activeDate;
      }
      
      const activeTf = tfSelect.value;
      syncYearInput();
      applyPickerMode(activeTf);
      highlightPills(activeTf);
      loadData(activeDate, { skipDashboard: true });
      loadDashboardData(activeDate, activeTf);
    })
    .catch(err => {
      showToast("Error loading review dates", "error");
      console.error(err);
    });

  // ----------------------------------------------------------------
  // Daily / Custom start date input change
  // ----------------------------------------------------------------
  dateInput.addEventListener("change", (e) => {
    activeDate = e.target.value;
    syncYearInput();
    reloadAll(tfSelect.value);
  });
  
  // ----------------------------------------------------------------
  // Custom end date input change
  // ----------------------------------------------------------------
  if (endDateInput) {
    endDateInput.addEventListener("change", (e) => {
      activeEndDate = e.target.value;
      reloadAll(tfSelect.value);
    });
  }

  // ----------------------------------------------------------------
  // Year input change (monthly / quarterly)
  // ----------------------------------------------------------------
  yearInput.addEventListener("change", () => {
    const yr  = parseInt(yearInput.value);
    if (!yr || yr < 2018 || yr > 2035) return;
    const tf  = tfSelect.value;
    // Keep current month/quarter, just change year
    const d   = new Date((activeDate || new Date().toISOString().split('T')[0]) + 'T00:00:00');
    const mo  = d.getMonth() + 1;
    if (tf === 'monthly') {
      activeDate = makeDate(yr, mo);
    } else {
      const q = Math.ceil(mo / 3);
      activeDate = makeDate(yr, Q_START_MO[q]);
    }
    highlightPills(tf);
    reloadAll(tf);
  });

  // ----------------------------------------------------------------
  // Month pill clicks
  // ----------------------------------------------------------------
  monthPills.querySelectorAll('.period-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const mo = parseInt(btn.dataset.mo);
      const yr = parseInt(yearInput.value) || new Date().getFullYear();
      activeDate = makeDate(yr, mo);
      monthPills.querySelectorAll('.period-pill').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      reloadAll('monthly');
    });
  });

  // ----------------------------------------------------------------
  // Quarter pill clicks
  // ----------------------------------------------------------------
  quarterPills.querySelectorAll('.period-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const q  = parseInt(btn.dataset.q);
      const yr = parseInt(yearInput.value) || new Date().getFullYear();
      activeDate = makeDate(yr, Q_START_MO[q]);
      quarterPills.querySelectorAll('.period-pill').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      reloadAll('quarterly');
    });
  });

  // ----------------------------------------------------------------
  // Timeframe selector change → switch picker mode
  // ----------------------------------------------------------------
  tfSelect.addEventListener("change", (e) => {
    const tf = e.target.value;
    applyPickerMode(tf);
    if (activeDate) {
      const d = new Date(activeDate + 'T00:00:00');
      const yr = d.getFullYear();
      const mo = d.getMonth() + 1;
      if (tf === 'monthly') {
        activeDate = makeDate(yr, mo);
      } else if (tf === 'quarterly') {
        const q = Math.ceil(mo / 3);
        activeDate = makeDate(yr, Q_START_MO[q]);
      } else if (tf === 'daily') {
        dateInput.value = activeDate || '';
      }
    }
    syncYearInput();
    highlightPills(tf);
    reloadAll(tf);
  });
}

/** Called after a Pine Labs / Paytm upload or "Apply to Dashboard": drop every cached API
 *  response and reload the dashboard so the new EMI numbers show immediately. */
window.refreshDashboardAfterEmi = function () {
  _apiCache.clear();
  const tf = (document.getElementById('timeframeSelect') || {}).value || 'daily';
  if (activeDate) loadDashboardData(activeDate, tf);
};

let _monthwiseTimer = null;
let _monthwiseLastKey = '';

function loadDashboardData(date, timeframe) {
  if (!date) return;
  const endDate = (timeframe === 'custom' && activeEndDate) ? activeEndDate : '';
  const url = `/api/dashboard-data?date=${date}&end_date=${endDate}&timeframe=${timeframe}${_src()}`;
  const _pkey = _livePeriodKey(date, timeframe, endDate);
  if (window._catDrillState) window._catDrillState = { level: 0, category: null, product: null };
  if (_dataSource === 'clickhouse' && !_apiCacheGet(url) && reviewData && reviewData._fromLiveDb !== _pkey) {
    reviewData._fromLiveDb = null;
    _setCategoryTableStatus("<tr><td colspan='16' style='text-align:center; color: var(--text-muted); padding: 28px;'>Loading ClickHouse data\u2026</td></tr>");
  }
  const _seq = ++_dashReqSeq;
  _cachedFetch(url)
    .then(data => {
      if (_seq !== _dashReqSeq) return;   // a newer date/filter request superseded this one
      const tbody = document.querySelector("#table-team-perf tbody");
      if (data.error) {
        console.warn("Dashboard data error:", data.error);
        if (reviewData) reviewData._fromLiveDb = null;
        if (tbody) tbody.innerHTML = `<tr><td colspan='5' style='text-align:center; color: var(--color-warning); padding: 20px;'>${data.error}</td></tr>`;
        _setCategoryTableStatus(`<tr><td colspan='16' style='text-align:center; color: var(--color-warning); padding: 20px;'>${data.error}</td></tr>`);
        return;
      }
      
      // Always update KPIs and team performance
      updateNewDashboardKPIs(data.kpis);
      renderTeamPerformance(data.hierarchy);
      
      // Always populate hierarchy filters regardless of category data
      if (data.hierarchy) {
        initCategoryFilters(data.hierarchy, data.states, data.districts, data.district_states);
      }
      
      // Auto-populate exec_summary achievement from live KPIs
      if (data.kpis && reviewData.exec_summary && reviewData.exec_summary.target_achievement) {
        const ta = reviewData.exec_summary.target_achievement;
        const kpis = data.kpis;
        
        // Only update if all achievement values are still zero (haven't been manually edited)
        const isAllZero = ta.achievement.every(v => !v || v === 0);
        if (isAllZero) {
          ta.achievement[0] = parseFloat(kpis.finance_penetration_value_pct.toFixed(2));
          ta.achievement[1] = parseFloat(kpis.finance_penetration_volume_pct.toFixed(2));
          ta.achievement[2] = parseFloat(kpis.card_penetration_volume_pct.toFixed(2));
          ta.achievement[3] = parseFloat((kpis.fin_value_cr || 0).toFixed(2));
          ta.achievement[4] = parseFloat((kpis.card_value_cr || 0).toFixed(2));
        }
        
        recalculateAll();
        renderExecSummary();
        // Auto-fill monthwise table only when Exec Summary tab is active
        clearTimeout(_monthwiseTimer);
        if (document.getElementById("tab-exec-summary")?.classList.contains("active")) {
          loadMonthwisePerformance(activeDate);
        }
      }
      
      // Always update category performance if available (re-run active filter if one is selected)
      const _hasActiveCatFilter = Boolean(
        _selectedStates().length > 0 ||
        ((document.getElementById("cat-filter-district") || {}).value || "") ||
        ((document.getElementById("cat-filter-rbm") || {}).value || "") ||
        ((document.getElementById("cat-filter-fin-bdm") || {}).value || "") ||
        ((document.getElementById("cat-filter-bdm") || {}).value || "") ||
        ((document.getElementById("cat-filter-store") || {}).value || "") ||
        ((document.getElementById("cat-filter-staff") || {}).value || "")
      );
      if (_hasActiveCatFilter) {
        triggerCategoryFilterLoad();
      } else if (data.category_performance) {
        reviewData.category_performance = data.category_performance;
        reviewData._fromLiveDb = _pkey;
        reviewData.brand_performance = data.brand_performance || {};
        populateBrandFilter();
        renderCategoryPerformance();
        if (typeof recalculateAll === 'function') {
            recalculateAll();
            renderCategoryPerformance();
        }
      }
      
      // Zone Performance
      if (data.zone_performance) {
        renderZonePerformance(data.zone_performance);
        
        // Extract live financier achievements from zone totals for the chart
        const fins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
        const liveQty = {}; const liveVal = {};
        fins.forEach(f => { liveQty[f] = 0; liveVal[f] = 0; });
        Object.values(data.zone_performance).forEach(zd => {
          if (!zd || !zd.qty) return;
          fins.forEach(f => {
            liveQty[f] += (zd.qty[f] || 0);
            liveVal[f] += (zd.val[f] || 0);
          });
        });
        window._liveFinQty = liveQty;
        window._liveFinVal = liveVal;
        renderChart();
      }
    })
    .catch(err => {
      if (_seq !== _dashReqSeq) return;
      console.error("Error loading dashboard data:", err);
      if (reviewData) reviewData._fromLiveDb = null;
      const tbody = document.querySelector("#table-team-perf tbody");
      if (tbody) tbody.innerHTML = `<tr><td colspan='5' style='text-align:center; color: var(--color-danger); padding: 20px;'>Failed to load data</td></tr>`;
      _setCategoryTableStatus("<tr><td colspan='16' style='text-align:center; color: var(--color-danger); padding: 20px;'>Failed to load data \u2014 click Refresh to retry</td></tr>");
    });
}

// // =====================================================
// ZONE PERFORMANCE RENDERING
// =====================================================
let _zoneData = null;
let _zoneCatPerfByRbm = {}; // { rbmName: { qty: {...}, value: {...} } }

function renderZonePerformance(zoneData) {
  if (!zoneData) return;
  _zoneData = zoneData;
  
  // Populate zone dropdown
  const zoneSelect = document.getElementById("zone-rbm-filter");
  if (zoneSelect) {
    const currentVal = zoneSelect.value;
    zoneSelect.innerHTML = `<option value="ALL">All Zones</option>`;
    Object.keys(zoneData).sort().forEach(zone => {
      zoneSelect.innerHTML += `<option value="${zone}">${zone}</option>`;
    });
    if (currentVal && [...zoneSelect.options].some(o => o.value === currentVal)) {
      zoneSelect.value = currentVal;
    }
  }
  
  renderZoneCategoryTable();
}

function renderZoneCategoryTable() {
  if (!_zoneData) return;
  
  const selectedZone = (document.getElementById("zone-rbm-filter") || {}).value || "ALL";
  
  const cats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"];
  const fins = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  const paperFins = new Set(["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"]);
  const cardFins  = new Set(["PineLab", "PAYTM", "Benow"]);

  const qtyBody = document.getElementById("zone-cat-qty-body");
  const valBody = document.getElementById("zone-cat-val-body");
  if (!qtyBody || !valBody) return;
  qtyBody.innerHTML = "";
  valBody.innerHTML = "";

  // Aggregate category data for selected zone(s)
  const catQty = {}, catVal = {};
  cats.forEach(cat => {
    catQty[cat] = {}; catVal[cat] = {};
    fins.forEach(f => { catQty[cat][f] = 0; catVal[cat][f] = 0; });
  });

  const zonesToShow = selectedZone === "ALL" ? Object.keys(_zoneData) : [selectedZone];
  zonesToShow.forEach(rbm => {
    const zd = _zoneData[rbm];
    if (!zd || !zd.categories) return;
    cats.forEach(cat => {
      const cd = zd.categories[cat];
      if (!cd) return;
      fins.forEach(f => {
        catQty[cat][f] += (cd.qty[f] || 0);
        catVal[cat][f] += (cd.val[f] || 0);
      });
    });
  });

  function buildRows(body, dataObj, isQty) {
    const fmt = isQty ? (v) => v.toLocaleString() : (v) => formatNumber(v, "decimal");
    const gtRow = {}; fins.forEach(f => { gtRow[f] = 0; });
    let gtTotal = 0, gtPaper = 0, gtCard = 0;

    cats.forEach(cat => {
      const d = dataObj[cat];
      const total = fins.reduce((s, f) => s + (d[f] || 0), 0);
      if (total === 0) return; // skip empty

      const paperTotal = [...paperFins].reduce((s, f) => s + (d[f] || 0), 0);
      const cardTotal  = [...cardFins].reduce((s,  f) => s + (d[f] || 0), 0);
      const finShare   = total > 0 ? ((total - (d["aCASH"] || 0)) / total * 100) : 0;

      fins.forEach(f => { gtRow[f] += (d[f] || 0); });
      gtTotal += total; gtPaper += paperTotal; gtCard += cardTotal;

      const tr = document.createElement("tr");
      let tds = `<td style="font-weight:600;">${cat}</td>`;
      fins.forEach(f => { tds += `<td class="num-align">${fmt(d[f] || 0)}</td>`; });
      tds += `
        <td class="num-align" style="font-weight:700; color:#2563eb;">${fmt(total)}</td>
        <td class="num-align" style="color:#d97706;">${formatNumber(finShare, "percent")}</td>
        <td class="num-align" style="color:#059669;">${fmt(paperTotal)}</td>
        <td class="num-align" style="color:#4338ca;">${fmt(cardTotal)}</td>
      `;
      tr.innerHTML = tds;
      body.appendChild(tr);
    });

    // Grand Total row
    const gtTr = document.createElement("tr");
    gtTr.className = "total-row";
    const gtFinShare = gtTotal > 0 ? ((gtTotal - (gtRow["aCASH"] || 0)) / gtTotal * 100) : 0;
    let gtTds = `<td>Grand Total</td>`;
    fins.forEach(f => { gtTds += `<td class="num-align">${fmt(gtRow[f] || 0)}</td>`; });
    gtTds += `
      <td class="num-align" style="font-weight:700; color:#2563eb;">${fmt(gtTotal)}</td>
      <td class="num-align" style="color:#d97706;">${formatNumber(gtFinShare, "percent")}</td>
      <td class="num-align" style="color:#059669;">${fmt(gtPaper)}</td>
      <td class="num-align" style="color:#4338ca;">${fmt(gtCard)}</td>
    `;
    gtTr.innerHTML = gtTds;
    body.appendChild(gtTr);

    // Share row
    const shareTr = document.createElement("tr");
    shareTr.style.background = "rgba(255,255,255,0.01)";
    let shareTds = `<td style="font-weight:600; color:var(--text-muted);">Share</td>`;
    fins.forEach(f => {
      const sh = gtTotal > 0 ? ((gtRow[f] || 0) / gtTotal * 100) : 0;
      shareTds += `<td class="num-align" style="color:var(--text-muted);">${formatNumber(sh, "percent")}</td>`;
    });
    shareTds += `
      <td class="num-align">-</td>
      <td class="num-align">-</td>
      <td class="num-align" style="color:var(--text-muted);">${formatNumber(gtTotal > 0 ? (gtPaper / gtTotal * 100) : 0, "percent")}</td>
      <td class="num-align" style="color:var(--text-muted);">${formatNumber(gtTotal > 0 ? (gtCard / gtTotal * 100) : 0, "percent")}</td>
    `;
    shareTr.innerHTML = shareTds;
    body.appendChild(shareTr);
  }

  buildRows(qtyBody, catQty, true);
  buildRows(valBody, catVal, false);
}

function updateNewDashboardKPIs(kpis) {
  if (!kpis) return;
  document.getElementById("kpi-val-pen").innerText = kpis.finance_penetration_value_pct.toFixed(1) + "%";
  document.getElementById("kpi-vol-pen").innerText = kpis.finance_penetration_volume_pct.toFixed(1) + "%";
  
  const cardEl = document.getElementById("kpi-card-pen");
  if (cardEl) cardEl.innerText = kpis.card_penetration_volume_pct.toFixed(1) + "%";
  
  document.getElementById("kpi-vol-src").innerText = kpis.total_volume.toLocaleString();
}

function renderTeamPerformance(hierarchy) {
  const tbody = document.querySelector("#table-team-perf tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  
  if (!hierarchy || Object.keys(hierarchy).length === 0) {
    tbody.innerHTML = "<tr><td colspan='5' style='text-align:center;'>No team performance data available for this timeframe</td></tr>";
    return;
  }
  
  function createRow(level, name, metrics, indentStr, id, parentId) {
    const tr = document.createElement("tr");
    tr.className = `hierarchy-row row-${level}`;
    tr.setAttribute("data-id", id);
    if (parentId) {
      tr.setAttribute("data-parent", parentId);
      tr.style.display = "none"; // collapsed by default
    }
    
    let icon = level === 'staff' ? '' : '<i data-lucide="chevron-right" style="width:14px; height:14px; margin-right:4px; transition:transform 0.2s; cursor:pointer;" class="expand-icon"></i>';
    let padding = level === 'rbm' ? 10 : level === 'bdm' ? 30 : level === 'branch' ? 50 : 70;
    
    tr.innerHTML = `
      <td style="padding-left: ${padding}px; cursor:${level==='staff'?'default':'pointer'};" class="hierarchy-toggle">
        <span style="display:inline-flex; align-items:center;">
          ${icon}
          <span class="badge" style="margin-right:8px; font-size:10px;">${level.toUpperCase()}</span> 
          ${name}
        </span>
      </td>
      <td class="num-align">${metrics.qty.toLocaleString()}</td>
      <td class="num-align">₹${metrics.value.toLocaleString(undefined, {maximumFractionDigits:0})}</td>
      <td class="num-align">${metrics.fin_qty.toLocaleString()}</td>
      <td class="num-align">₹${metrics.fin_value.toLocaleString(undefined, {maximumFractionDigits:0})}</td>
    `;
    
    if (level !== 'staff') {
      tr.querySelector('.hierarchy-toggle').addEventListener('click', () => {
        const iconEl = tr.querySelector('.expand-icon');
        const isExpanded = iconEl.style.transform === 'rotate(90deg)';
        iconEl.style.transform = isExpanded ? 'rotate(0deg)' : 'rotate(90deg)';
        
        // toggle direct children
        const children = tbody.querySelectorAll(`tr[data-parent="${id}"]`);
        children.forEach(child => {
          child.style.display = isExpanded ? 'none' : 'table-row';
          // if collapsing, also collapse all descendants
          if (isExpanded) {
            collapseDescendants(child.getAttribute('data-id'));
          }
        });
      });
    }
    return tr;
  }
  
  function collapseDescendants(parentId) {
    const children = tbody.querySelectorAll(`tr[data-parent="${parentId}"]`);
    children.forEach(child => {
      child.style.display = 'none';
      const icon = child.querySelector('.expand-icon');
      if (icon) icon.style.transform = 'rotate(0deg)';
      collapseDescendants(child.getAttribute('data-id'));
    });
  }

  // Iterate RBM -> BDM -> Branch -> Staff
  let idCounter = 0;
  for (const [rbm, rbmData] of Object.entries(hierarchy)) {
    const rbmId = `rbm_${++idCounter}`;
    tbody.appendChild(createRow('rbm', rbm, rbmData.metrics, "", rbmId, null));
    
    for (const [bdm, bdmData] of Object.entries(rbmData.bdms)) {
      const bdmId = `bdm_${++idCounter}`;
      tbody.appendChild(createRow('bdm', bdm, bdmData.metrics, "  ", bdmId, rbmId));
      
      for (const [branch, branchData] of Object.entries(bdmData.branches)) {
        const branchId = `branch_${++idCounter}`;
        tbody.appendChild(createRow('branch', branch, branchData.metrics, "    ", branchId, bdmId));
        
        for (const [staff, staffData] of Object.entries(branchData.staffs)) {
          const staffId = `staff_${++idCounter}`;
          tbody.appendChild(createRow('staff', staff, staffData.metrics, "      ", staffId, branchId));
        }
      }
    }
  }
  lucide.createIcons();
}

let _catHierarchy = null;
let _catFiltersAttached = false;
let _districtList = [];
let _districtStateMap = {};

// Branch code -> branch name (for the Store dropdown: shows names, filters by code)
let _branchNames = {};
function _branchLabel(code) {
  return _branchNames[String(code).trim().toUpperCase()] || code;
}
// Branch code -> district (used to narrow the Store dropdown by District / State)
let _branchDistricts = {};
Promise.all([
  fetch("/api/branch-names").then(r => r.json()).catch(() => ({})),
  fetch("/api/branch-districts").then(r => r.json()).catch(() => ({}))
]).then(([names, dists]) => {
  _branchNames = names || {};
  _branchDistricts = dists || {};
  if (_catHierarchy) initCategoryFilters(_catHierarchy);
});

// ---- Multi-select State filter (shared across Category, Zone, Price, Brand, Store tabs) ----
const _STATE_FILTER_PREFIXES = ["cat", "zone", "price", "brand", "store", "oct"];

function _selectedStates(prefix = "cat") {
  const el = document.getElementById(`${prefix}-filter-state`) || document.getElementById("cat-filter-state");
  return ((el && el.value) || "").split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
}

function _updateStateBtnLabel(prefix) {
  const prefixes = prefix ? [prefix] : _STATE_FILTER_PREFIXES;
  prefixes.forEach(p => {
    const btn = document.getElementById(`${p}-filter-state-btn`);
    if (!btn) return;
    const sel = _selectedStates(p);
    btn.innerText = sel.length === 0 ? "All States" : (sel.length <= 2 ? sel.join(", ") : `${sel.length} States`);
    btn.title = sel.length ? sel.join(", ") : "All States";
  });
}

function _syncAllStateFilters(csvVal) {
  const selSet = new Set((csvVal || "").split(",").map(s => s.trim().toUpperCase()).filter(Boolean));
  _STATE_FILTER_PREFIXES.forEach(p => {
    const hidden = document.getElementById(`${p}-filter-state`);
    const panel  = document.getElementById(`${p}-filter-state-panel`);
    if (hidden) hidden.value = Array.from(selSet).join(",");
    if (panel) {
      const allCb = panel.querySelector(`#${p}-filter-state-all`);
      if (allCb) allCb.checked = selSet.size === 0;
      panel.querySelectorAll("input[type=checkbox][data-state]").forEach(cb => {
        cb.checked = selSet.has(cb.dataset.state);
      });
    }
    _updateStateBtnLabel(p);
  });
}

function renderStateMulti(states) {
  const rawList = (states && states.length) ? states : ["KARNATAKA", "KERALA", "OTHERS"];
  const list = Array.from(new Set(rawList.map(s => String(s).trim().toUpperCase())))
    .filter(s => s && s !== "UNKNOWN").sort();
  const prev = new Set(_selectedStates("cat"));
  const syncedVal = list.filter(s => prev.has(s)).join(",");
  const rowStyle = "display:flex;align-items:center;gap:8px;padding:5px 8px;font-size:13px;cursor:pointer;border-radius:4px;color:var(--text-main, inherit);";

  _STATE_FILTER_PREFIXES.forEach(prefix => {
    const panel  = document.getElementById(`${prefix}-filter-state-panel`);
    const btn    = document.getElementById(`${prefix}-filter-state-btn`);
    const hidden = document.getElementById(`${prefix}-filter-state`);
    if (!panel || !btn || !hidden) return;

    hidden.value = syncedVal;
    panel.innerHTML = "";

    const fire = () => {
      const newVal = Array.from(panel.querySelectorAll("input[type=checkbox][data-state]"))
        .filter(c => c.checked).map(c => c.dataset.state).join(",");
      _syncAllStateFilters(newVal);

      const catHidden = document.getElementById("cat-filter-state");
      if (catHidden) catHidden.dispatchEvent(new Event("change"));
      if (prefix === "zone" && !_catFiltersAttached) triggerCategoryFilterLoad();
      if (prefix === "price" || document.getElementById("tab-price")?.classList.contains("active")) loadPriceRangeData();
      if (prefix === "brand" || document.getElementById("tab-brand")?.classList.contains("active")) loadBrandData();
      if (prefix === "store" || document.getElementById("tab-store")?.classList.contains("active")) loadStoreData();
      if (prefix === "oct" || document.getElementById("tab-oct-sheets")?.classList.contains("active")) loadOctSheetsData(true);
    };

    const allRow = document.createElement("label");
    allRow.style.cssText = rowStyle + "font-weight:600;border-bottom:1px solid var(--card-border);margin-bottom:4px;";
    allRow.innerHTML = `<input type="checkbox" id="${prefix}-filter-state-all"> <span>All States</span>`;
    panel.appendChild(allRow);
    const allCb = allRow.querySelector("input");
    allCb.checked = !hidden.value;
    allCb.addEventListener("change", () => {
      panel.querySelectorAll("input[data-state]").forEach(c => { c.checked = false; });
      allCb.checked = true;
      fire();
    });

    list.forEach(st => {
      const row = document.createElement("label");
      row.style.cssText = rowStyle;
      row.innerHTML = `<input type="checkbox" data-state="${st}"> <span>${st}</span>`;
      const cb = row.querySelector("input");
      cb.checked = prev.has(st);
      cb.addEventListener("change", () => {
        const anyChecked = panel.querySelector("input[data-state]:checked");
        allCb.checked = !anyChecked;
        fire();
      });
      panel.appendChild(row);
    });
    _updateStateBtnLabel(prefix);

    if (!btn.dataset.bound) {
      btn.dataset.bound = "1";
      btn.addEventListener("click", e => {
        e.stopPropagation();
        panel.style.display = panel.style.display === "none" ? "block" : "none";
      });
      document.addEventListener("click", e => {
        if (!panel.contains(e.target) && e.target !== btn) panel.style.display = "none";
      });
    }
  });
}

function initCategoryFilters(hierarchy, states = [], districts = [], districtStates = null) {
  if (!hierarchy) return;
  _catHierarchy = hierarchy;
  
  const stateSelect  = document.getElementById("cat-filter-state");
  const districtSelect = document.getElementById("cat-filter-district");
  const rbmSelect    = document.getElementById("cat-filter-rbm");
  const finBdmSelect = document.getElementById("cat-filter-fin-bdm");
  const bdmSelect    = document.getElementById("cat-filter-bdm");
  const storeSelect  = document.getElementById("cat-filter-store");
  const staffSelect  = document.getElementById("cat-filter-staff");
  if (!rbmSelect) return;
  
  // Helper to populate a select (skip blank/Unknown entries)
  function fillSelect(sel, options, allLabel, labelFn) {
    const prev = sel.value;
    const lbl = labelFn || (v => v);
    sel.innerHTML = `<option value="">${allLabel}</option>`;
    options
      .filter(v => v && v.trim() && v.trim().toUpperCase() !== 'UNKNOWN')
      .sort((a, b) => lbl(a).localeCompare(lbl(b)))
      .forEach(val => {
        const opt = document.createElement("option");
        opt.value = val; opt.innerText = lbl(val);
        sel.appendChild(opt);
      });
    if (options.includes(prev)) sel.value = prev;
  }
  
  // Helper to dynamically collect options across the hierarchy
  function getOptions(level, rFilter, bFilter, sFilter) {
    const opts = new Set();
    const currentHierarchy = _catHierarchy || hierarchy; // Use globally updated hierarchy if available
    
    const match = (val, filter) => !filter || val.trim().toLowerCase() === filter.trim().toLowerCase();

    for (const [r, rData] of Object.entries(currentHierarchy)) {
      if (match(r, rFilter)) {
        if (level === 'bdm') {
          Object.keys(rData.bdms || {}).forEach(b => opts.add(b));
        } else {
          for (const [b, bData] of Object.entries(rData.bdms || {})) {
            if (match(b, bFilter)) {
              if (level === 'store') {
                const selDist  = districtSelect ? districtSelect.value : '';
                const selStates = _selectedStates();
                Object.keys(bData.branches || {}).forEach(s => {
                  if (selDist || selStates.length) {
                    const d = _branchDistricts[String(s).trim().toUpperCase()] || '';
                    if (selDist && d.toUpperCase() !== selDist.toUpperCase()) return;
                    if (selStates.length && !selStates.includes((_districtStateMap[d] || '').toUpperCase())) return;
                  }
                  opts.add(s);
                });
              } else if (level === 'staff') {
                for (const [s, sData] of Object.entries(bData.branches || {})) {
                  if (match(s, sFilter)) {
                    Object.keys(sData.staffs || {}).forEach(st => opts.add(st));
                  }
                }
              }
            }
          }
        }
      }
    }
    return Array.from(opts).sort();
  }
  
  // Always refresh RBM list from new hierarchy
  fillSelect(rbmSelect, Object.keys(hierarchy), "All RBMs");

  // Populate Fin BDM from the unique Fin BDM values in the hierarchy
  function getFinBdmOptions() {
    const opts = new Set();
    for (const rData of Object.values(_catHierarchy || hierarchy)) {
      if (rData.fin_bdm) opts.add(rData.fin_bdm);
      for (const bData of Object.values(rData.bdms || {})) {
        for (const sData of Object.values(bData.branches || {})) {
          if (sData.fin_bdm) opts.add(sData.fin_bdm);
        }
      }
    }
    return Array.from(opts).sort();
  }
  fillSelect(finBdmSelect, getFinBdmOptions(), "All Fin BDMs");
  if (stateSelect && states && states.length > 0) {
    renderStateMulti(states);
  }
  // District options (narrowed to the selected State, when one is chosen)
  if (districts && districts.length > 0) _districtList = districts;
  if (districtStates) _districtStateMap = districtStates;
  function refreshDistricts() {
    if (!districtSelect) return;
    const sts = _selectedStates();
    const opts = _districtList.filter(d => !sts.length || sts.includes((_districtStateMap[d] || '').toUpperCase()));
    fillSelect(districtSelect, opts, "All Districts");
  }
  refreshDistricts();
  // Re-fill Store (and reset Staff) after State / District changes
  function refreshStores() {
    fillSelect(storeSelect, getOptions('store', rbmSelect.value, bdmSelect.value), "All Stores", _branchLabel);
    fillSelect(staffSelect, [], "All Staff");
  }

  // Restore cascading dropdowns if a value was previously selected, but flexibly
  const selRbm = rbmSelect.value;
  fillSelect(bdmSelect, getOptions('bdm', selRbm), "All BDMs");
  const selBdm = bdmSelect.value;
  fillSelect(storeSelect, getOptions('store', selRbm, selBdm), "All Stores", _branchLabel);
  const selStore = storeSelect.value;
  // Staff logic unchanged conceptually: must have store selected (though we find it flexibly)
  if (selStore) {
    fillSelect(staffSelect, getOptions('staff', selRbm, selBdm, selStore), "All Staff");
  } else {
    fillSelect(staffSelect, [], "All Staff");
  }
  
  // Attach events only once
  if (!_catFiltersAttached) {
    _catFiltersAttached = true;

    if (stateSelect) {
      stateSelect.addEventListener("change", () => {
        refreshDistricts();
        refreshStores();
        triggerCategoryFilterLoad();
      });
    }

    if (districtSelect) districtSelect.addEventListener("change", () => {
      refreshStores();
      triggerCategoryFilterLoad();
    });

    rbmSelect.addEventListener("change", () => {
      fillSelect(bdmSelect, getOptions('bdm', rbmSelect.value), "All BDMs");
      fillSelect(storeSelect, getOptions('store', rbmSelect.value, bdmSelect.value), "All Stores", _branchLabel);
      fillSelect(staffSelect, [], "All Staff");
      triggerCategoryFilterLoad();
    });

    finBdmSelect.addEventListener("change", () => {
      // Fin BDM is independent — just reload
      triggerCategoryFilterLoad();
    });

    bdmSelect.addEventListener("change", () => {
      fillSelect(storeSelect, getOptions('store', rbmSelect.value, bdmSelect.value), "All Stores", _branchLabel);
      fillSelect(staffSelect, [], "All Staff");
      triggerCategoryFilterLoad();
    });
    
    storeSelect.addEventListener("change", () => {
      const sVal = storeSelect.value;
      if (sVal) {
        fillSelect(staffSelect, getOptions('staff', rbmSelect.value, bdmSelect.value, sVal), "All Staff");
      } else {
        fillSelect(staffSelect, [], "All Staff");
      }
      triggerCategoryFilterLoad();
    });
    
    staffSelect.addEventListener("change", triggerCategoryFilterLoad);
  }
}

function triggerCategoryFilterLoad() {
    const timeframe = document.getElementById("timeframeSelect") ? document.getElementById("timeframeSelect").value : "daily";
    const rbm     = encodeURIComponent(document.getElementById("cat-filter-rbm").value);
    const finBdm  = encodeURIComponent((document.getElementById("cat-filter-fin-bdm") || {}).value || '');
    const bdm     = encodeURIComponent(document.getElementById("cat-filter-bdm").value);
    const store   = encodeURIComponent(document.getElementById("cat-filter-store").value);
    const staff   = encodeURIComponent(document.getElementById("cat-filter-staff").value);
    const stateElement = document.getElementById("cat-filter-state");
    const state   = stateElement ? encodeURIComponent(stateElement.value) : '';
    const districtElement = document.getElementById("cat-filter-district");
    const district = districtElement ? encodeURIComponent(districtElement.value) : '';
    
    const endD = _endDate();
    const _pkey = _livePeriodKey(activeDate, timeframe, endD);
    const url = `/api/dashboard-data?date=${activeDate}&end_date=${endD}&timeframe=${timeframe}&state=${state}&district=${district}&rbm=${rbm}&fin_bdm=${finBdm}&bdm=${bdm}&store=${store}&staff=${staff}${_src()}`;
    
    if (_dataSource === 'clickhouse' && !_apiCacheGet(url)) {
      _setCategoryTableStatus("<tr><td colspan='16' style='text-align:center; color: var(--text-muted); padding: 28px;'>Filtering ClickHouse data\u2026</td></tr>");
    }
    const _seq = ++_catFilterReqSeq;
    _cachedFetch(url)
      .then(data => {
        if (_seq !== _catFilterReqSeq) return;   // superseded by a newer request
        if (data.error) {
          console.warn("Dashboard filter error:", data.error);
          if (reviewData) reviewData._fromLiveDb = null;
          _setCategoryTableStatus(`<tr><td colspan='16' style='text-align:center; color: var(--color-warning); padding: 20px;'>${data.error}</td></tr>`);
          return;
        }
        if (data.category_performance) {
          // IMPORTANT: update the module-level `reviewData`, not window.reviewData
          reviewData.category_performance = data.category_performance;
          reviewData._fromLiveDb = _pkey;
          reviewData.brand_performance = data.brand_performance || {};
          
          populateBrandFilter();
          renderCategoryPerformance();
          if (typeof recalculateAll === 'function') recalculateAll();
          renderCategoryPerformance();
        }
        if (data.zone_performance) {
          renderZonePerformance(data.zone_performance);
        }
      })
      .catch(err => {
        if (_seq !== _catFilterReqSeq) return;
        console.error("Error loading filtered category data:", err);
        _setCategoryTableStatus("<tr><td colspan='16' style='text-align:center; color: var(--color-danger); padding: 20px;'>Failed to load filtered data</td></tr>");
      });
}

// Load Global Targets & Review Data
function loadData(date, opts) {
  const skipDashboard = !!(opts && opts.skipDashboard);
  const _revSeq = ++_reviewReqSeq;
  const tfEl = document.getElementById("timeframeSelect");
  const tfVal = tfEl ? tfEl.value : "daily";
  const endD = (tfVal === "custom" && typeof activeEndDate !== "undefined" && activeEndDate) ? activeEndDate : "";
  const _pkey = _livePeriodKey(date, tfVal, endD);
  // Load targets config & review data with automatic cold-start retry
  _fetchJsonWithRetry("/api/targets", { silent: true })
    .then(targets => {
      globalTargets = targets;
      populateTargetsForm();
      return _fetchJsonWithRetry(`/api/review/${date}`, { silent: true });
    })
    .then(data => {
      if (_revSeq !== _reviewReqSeq) return;
      const _keepCat = (_dataSource === 'clickhouse' && reviewData && reviewData._fromLiveDb === _pkey) ? reviewData.category_performance : null;
      const _keepBrand = (_dataSource === 'clickhouse' && reviewData && reviewData._fromLiveDb === _pkey) ? reviewData.brand_performance : null;
      reviewData = data;
      if (_keepCat) { reviewData.category_performance = _keepCat; reviewData.brand_performance = _keepBrand || {}; reviewData._fromLiveDb = _pkey; }
      recalculateAll();
      populateBrandFilter();
      const _skipCatRender = (skipDashboard && _dataSource === 'clickhouse' && !_keepCat);
      renderAllTables({ skipCategory: _skipCatRender });
      updateDashboardKPIs();
      renderChart();
      lucide.createIcons();
      
      // Load dashboard data only if not already triggered by caller
      if (!skipDashboard) {
        loadDashboardData(activeDate, document.getElementById("timeframeSelect").value);
      }
    })
    .catch(err => {
      showToast("Error loading review data", "error");
      console.error(err);
    });
}

// Populate Settings Config form
function populateTargetsForm() {
  const est = globalTargets.exec_summary_targets || {};
  document.getElementById("cfg-val-pen").value = est.value_penetration || 67;
  document.getElementById("cfg-vol-pen").value = est.vol_penetration || 55;
  document.getElementById("cfg-cards-pen").value = est.cards_penetration || 6.91;
  document.getElementById("cfg-fin-val").value = est.fin_value_cr || 460;
  document.getElementById("cfg-card-val").value = est.card_value_cr || 47.41;
  
  const fvt = globalTargets.financier_volume_targets || {};
  document.getElementById("cfg-fin-bajaj").value = fvt.BAJAJ || 88779;
  document.getElementById("cfg-fin-hdb").value = fvt.HDB || 6341;
  document.getElementById("cfg-fin-idfc").value = fvt.IDFC || 4488;
  document.getElementById("cfg-fin-pfl").value = fvt.PFL || 134;
  document.getElementById("cfg-fin-tvs").value = fvt.TVS || 1574;
  document.getElementById("cfg-fin-dmi").value = fvt.DMI || 1708;
  document.getElementById("cfg-fin-pine").value = fvt.PinelAb || 42.4;
  document.getElementById("cfg-fin-paytm").value = fvt.PayTm || 1.7;
  document.getElementById("cfg-fin-benow").value = fvt.Benow || 3.3;
}

// Math Recalculation Engine
function recalculateAll() {
  if (!reviewData.exec_summary) return;

  // 1. Disbursement pending percentage
  const disb = reviewData.disbursement ? reviewData.disbursement.financiers : {};
  let totSourcing = 0;
  let totPending = 0;
  
  Object.keys(disb).forEach(fin => {
    const info = disb[fin];
    info.pending_pct = info.sourcing > 0 ? (info.pending / info.sourcing * 100) : 0;
    totSourcing += info.sourcing;
    totPending += info.pending;
  });
  
  if (reviewData.disbursement) {
    reviewData.disbursement.totals = {
      sourcing: totSourcing,
      pending: totPending,
      pending_pct: totSourcing > 0 ? (totPending / totSourcing * 100) : 0
    };
  }

  // 2. Ageing totals
  const ageing = reviewData.disbursement ? reviewData.disbursement.ageing_status : {};
  let ageTotals = {
    "4_7_days": [0, 0],
    "8_15_days": [0, 0],
    "15_plus": [0, 0],
    "90_plus": [0, 0],
    "total": [0, 0],
    "last_week": [0, 0]
  };
  
  Object.keys(ageing).forEach(fin => {
    const info = ageing[fin];
    const c4_7 = info["4_7_days"][0], a4_7 = info["4_7_days"][1];
    const c8_15 = info["8_15_days"][0], a8_15 = info["8_15_days"][1];
    const c15 = info["15_plus"][0], a15 = info["15_plus"][1];
    const c90 = info["90_plus"][0], a90 = info["90_plus"][1];
    
    info.total = [
      c4_7 + c8_15 + c15 + c90,
      a4_7 + a8_15 + a15 + a90
    ];
    
    ageTotals["4_7_days"][0] += c4_7; ageTotals["4_7_days"][1] += a4_7;
    ageTotals["8_15_days"][0] += c8_15; ageTotals["8_15_days"][1] += a8_15;
    ageTotals["15_plus"][0] += c15; ageTotals["15_plus"][1] += a15;
    ageTotals["90_plus"][0] += c90; ageTotals["90_plus"][1] += a90;
    ageTotals["total"][0] += info.total[0]; ageTotals["total"][1] += info.total[1];
    ageTotals["last_week"][0] += info.last_week[0]; ageTotals["last_week"][1] += info.last_week[1];
  });
  if (reviewData.disbursement) reviewData.disbursement.ageing_totals = ageTotals;

  // 3. Category Quantity and Value Totals
  recalculateCategoryMath(reviewData.category_performance);
  if (reviewData.brand_performance) {
    Object.values(reviewData.brand_performance).forEach(brandData => {
      recalculateCategoryMath(brandData);
    });
  }

  // 4. SIF (Share in Fin Value) - Value & Volume - Bajaj to Benow
  const qtyTotals = reviewData.category_performance.qty_totals;
  const valTotals = reviewData.category_performance.val_totals;
  const tot_fin_val_cr = valTotals.paper_total + valTotals.card_total;
  const tot_fin_qty = qtyTotals.paper_total + qtyTotals.card_total;
  reviewData.category_performance.sif = {};
  reviewData.category_performance.sif_qty = {};
  const fins_keys = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  fins_keys.slice(1).forEach(k => { // Skip aCASH
    const v = valTotals.totals[k];
    reviewData.category_performance.sif[k] = tot_fin_val_cr > 0 ? (v / tot_fin_val_cr * 100) : 0;
    const q = qtyTotals.totals[k];
    reviewData.category_performance.sif_qty[k] = tot_fin_qty > 0 ? (q / tot_fin_qty * 100) : 0;
  });
  
  // Paper & Card SIF Total
  reviewData.category_performance.sif.paper_total = tot_fin_val_cr > 0 ? (valTotals.paper_total / tot_fin_val_cr * 100) : 0;
  reviewData.category_performance.sif.card_total = tot_fin_val_cr > 0 ? (valTotals.card_total / tot_fin_val_cr * 100) : 0;
  reviewData.category_performance.sif_qty.paper_total = tot_fin_qty > 0 ? (qtyTotals.paper_total / tot_fin_qty * 100) : 0;
  reviewData.category_performance.sif_qty.card_total = tot_fin_qty > 0 ? (qtyTotals.card_total / tot_fin_qty * 100) : 0;

  // 5. ASP (Average Selling Price)
  reviewData.category_performance.asp = {};
  const aspQtyTotals = reviewData.category_performance.qty_totals;
  const aspValTotals = reviewData.category_performance.val_totals;
  fins_keys.forEach(k => {
    const qty = aspQtyTotals.totals[k];
    const val = aspValTotals.totals[k];
    reviewData.category_performance.asp[k] = qty > 0 ? ((val / qty) * 10000000) : 0;
  });
  reviewData.category_performance.asp.total = aspQtyTotals.grand_total > 0 ? ((aspValTotals.grand_total / aspQtyTotals.grand_total) * 10000000) : 0;
  reviewData.category_performance.asp.paper_total = aspQtyTotals.paper_total > 0 ? ((aspValTotals.paper_total / aspQtyTotals.paper_total) * 10000000) : 0;
  reviewData.category_performance.asp.card_total = aspQtyTotals.card_total > 0 ? ((aspValTotals.card_total / aspQtyTotals.card_total) * 10000000) : 0;

  // 6. Financier Targets & GAPs
  const ft = reviewData.financier_targets;
  let tot_tgt_paper = 0, tot_ach_paper = 0;
  let tot_tgt_card = 0.0, tot_ach_card = 0.0;
  
  Object.keys(ft).forEach((fin, idx) => {
    const info = ft[fin];
    info.gap = info.target - info.achievement;
    info.achvt_pct = info.target > 0 ? (info.achievement / info.target * 100) : 0;
    info.trend = info.achievement * 1.49; // standard weekly projection factor
    info.trend_pct = info.target > 0 ? (info.trend / info.target * 100) : 0;
    
    // Bajaj to DMI are Volume (index 0 to 5)
    // Pinelab, Paytm, Benow are Value in Cr
    const is_card = ["PinelAb", "PayTm", "Benow"].includes(fin);
    if (!is_card) {
      tot_tgt_paper += info.target;
      tot_ach_paper += info.achievement;
    } else {
      tot_tgt_card += info.target;
      tot_ach_card += info.achievement;
    }
  });
  
  reviewData.financier_targets_totals = {
    paper: {
      target: tot_tgt_paper,
      achievement: tot_ach_paper,
      gap: tot_tgt_paper - tot_ach_paper,
      achvt_pct: tot_tgt_paper > 0 ? (tot_ach_paper / tot_tgt_paper * 100) : 0,
      trend: tot_ach_paper * 1.49,
      trend_pct: tot_tgt_paper > 0 ? ((tot_ach_paper * 1.49) / tot_tgt_paper * 100) : 0
    },
    card: {
      target: tot_tgt_card,
      achievement: tot_ach_card,
      gap: tot_tgt_card - tot_ach_card,
      achvt_pct: tot_tgt_card > 0 ? (tot_ach_card / tot_tgt_card * 100) : 0,
      trend: tot_ach_card * 1.49,
      trend_pct: tot_tgt_card > 0 ? ((tot_ach_card * 1.49) / tot_tgt_card * 100) : 0
    }
  };

  // 7. DMI Contest Summary & branch math
  const dmi_br = reviewData.dmi_contest.branches;
  let dmi_sum = { OPPO: 0, SAMSUNG: 0, VIVO: 0 };
  Object.keys(dmi_br).forEach(br => {
    const info = dmi_br[br];
    dmi_sum.OPPO += info.OPPO || 0;
    dmi_sum.SAMSUNG += info.SAMSUNG || 0;
    dmi_sum.VIVO += info.VIVO || 0;
  });
  
  const dmi_tgt = reviewData.dmi_contest.summary;
  dmi_tgt.OPPO.achievement = dmi_sum.OPPO;
  dmi_tgt.OPPO.gap = dmi_tgt.OPPO.target - dmi_sum.OPPO;
  dmi_tgt.OPPO.achvt_pct = dmi_tgt.OPPO.target > 0 ? (dmi_sum.OPPO / dmi_tgt.OPPO.target * 100) : 0;
  dmi_tgt.OPPO.trend = dmi_sum.OPPO * 1.49;
  dmi_tgt.OPPO.trend_pct = dmi_tgt.OPPO.target > 0 ? ((dmi_sum.OPPO * 1.49) / dmi_tgt.OPPO.target * 100) : 0;
  
  dmi_tgt.SAMSUNG.achievement = dmi_sum.SAMSUNG;
  dmi_tgt.SAMSUNG.gap = dmi_tgt.SAMSUNG.target - dmi_sum.SAMSUNG;
  dmi_tgt.SAMSUNG.achvt_pct = dmi_tgt.SAMSUNG.target > 0 ? (dmi_sum.SAMSUNG / dmi_tgt.SAMSUNG.target * 100) : 0;
  dmi_tgt.SAMSUNG.trend = dmi_sum.SAMSUNG * 1.49;
  dmi_tgt.SAMSUNG.trend_pct = dmi_tgt.SAMSUNG.target > 0 ? ((dmi_sum.SAMSUNG * 1.49) / dmi_tgt.SAMSUNG.target * 100) : 0;
  
  dmi_tgt.VIVO.achievement = dmi_sum.VIVO;
  dmi_tgt.VIVO.gap = dmi_tgt.VIVO.target - dmi_sum.VIVO;
  dmi_tgt.VIVO.achvt_pct = dmi_tgt.VIVO.target > 0 ? (dmi_sum.VIVO / dmi_tgt.VIVO.target * 100) : 0;
  dmi_tgt.VIVO.trend = dmi_sum.VIVO * 1.49;
  dmi_tgt.VIVO.trend_pct = dmi_tgt.VIVO.target > 0 ? ((dmi_sum.VIVO * 1.49) / dmi_tgt.VIVO.target * 100) : 0;

  // 8. Manpower Status
  const mp = reviewData.manpower || {};
  let tot_mp_plan = 0, tot_mp_curr = 0;
  Object.keys(mp).forEach(fin => {
    const info = mp[fin];
    info.req = info.plan - info.current;
    info.gap = info.plan - info.current;
    tot_mp_plan += info.plan;
    tot_mp_curr += info.current;
  });
  reviewData.manpower_totals = {
    plan: tot_mp_plan,
    current: tot_mp_curr,
    req: tot_mp_plan - tot_mp_curr,
    gap: tot_mp_plan - tot_mp_curr
  };
}

// Render all data tables
function renderAllTables(opts) {
  const skipCategory = !!(opts && opts.skipCategory);
  renderExecSummary();
  renderDisbursement();
  if (!skipCategory) renderCategoryPerformance();
  renderContest();
  renderManpower();
}

// =====================================================
// MONTHWISE PERFORMANCE — Auto-fill from live data
// =====================================================
function loadMonthwisePerformance(dateStr) {
  if (!dateStr) return;
  const baseDate = new Date(dateStr);
  const year  = baseDate.getFullYear();
  const month = baseDate.getMonth(); // 0-based

  // Determine quarter months (0-based indices)
  const quarterStart = Math.floor(month / 3) * 3;
  const months = [quarterStart, quarterStart + 1, quarterStart + 2];
  const qKey = `${year}-Q${Math.floor(month / 3) + 1}|${_emiVersion || ''}|${_dataSource}`;
  if (_monthwiseLastKey === qKey) return;

  // Month labels for the table headers
  const monthNames = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

  // Update column headers dynamically
  const theadRow = document.querySelector("#table-monthwise thead tr");
  if (theadRow) {
    const ths = theadRow.querySelectorAll("th");
    if (ths.length >= 4) {
      ths[1].innerText = monthNames[months[0]];
      ths[2].innerText = monthNames[months[1]];
      ths[3].innerText = monthNames[months[2]];
      ths[4].innerText = `${monthNames[months[0]].charAt(0)}${monthNames[months[1]].charAt(0)}${monthNames[months[2]].charAt(0)} TOTAL / AVG`;
    }
  }

  // Fetch each month's data in parallel
  const promises = months.map(m => {
    const d = new Date(year, m, 10); // day 10 is safe for any month
    const dateParam = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-10`;
    return _cachedFetch(`/api/dashboard-data?date=${dateParam}&end_date=&timeframe=monthly${_src()}`, { silent: true })
      .catch(() => null);
  });

  Promise.all(promises).then(results => {
    const mw = reviewData.exec_summary && reviewData.exec_summary.monthwise_performance;
    if (!mw) return;

    let anyLoaded = false;
    results.forEach((data, idx) => {
      if (!data || data.error || !data.kpis) return;
      anyLoaded = true;
      const k = data.kpis;
      mw.fin_value_pct[idx]  = parseFloat((k.finance_penetration_value_pct || 0).toFixed(1));
      mw.fin_vol_pct[idx]    = parseFloat((k.finance_penetration_volume_pct || 0).toFixed(1));
      mw.card_pct[idx]       = parseFloat((k.card_penetration_volume_pct || 0).toFixed(1));
      mw.fin_value_cr[idx]   = parseFloat((k.fin_value_cr || 0).toFixed(2));
      mw.card_value_cr[idx]  = parseFloat((k.card_value_cr || 0).toFixed(2));
    });
    if (anyLoaded) _monthwiseLastKey = qKey;

    renderExecSummary();
  });
}

function renderExecSummary() {
  const ta_data = reviewData.exec_summary.target_achievement;
  const tbody_ta = document.querySelector("#table-target-ach tbody");
  tbody_ta.innerHTML = "";
  
  const metrics = ["Target", "Achievement", "G/DG*", "Qtr Trend", "Qtr Trend %"];
  
  // Row styling to match the presentation slide
    const rowStyles = {
    "Target":            { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "600" },
    "Achievement":       { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "600" },
    "Last Week Status":  { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Last Month Status": { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "G/DG*":             { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Qtr Trend":         { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Qtr Trend %":       { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
  };
  
  metrics.forEach((metric, rIdx) => {
    const tr = document.createElement("tr");
    const style = rowStyles[metric] || {};
    if (style.bg) tr.style.background = style.bg;
    
    // Metric Name
    const tdName = document.createElement("td");
    tdName.innerText = metric;
    tdName.style.fontWeight = style.fontWeight || "bold";
    if (style.color) tdName.style.color = style.color;
    tr.appendChild(tdName);
    
    const is_growth = metric === "G/DG*";
    const is_trend_pct = metric === "Qtr Trend %";
    
    for (let cIdx = 0; cIdx < 5; cIdx++) {
      const td = document.createElement("td");
      td.className = "num-align";
      if (style.color && !is_growth) td.style.color = style.color;
      const is_pct = cIdx <= 2;
      
      let val = "";
      if (metric === "Target") val = ta_data.target[cIdx];
      else if (metric === "Achievement") val = ta_data.achievement[cIdx];
      else if (metric === "Last Week Status") val = ta_data.last_week[cIdx];
      else if (metric === "Last Month Status") val = ta_data.last_month[cIdx];
      else if (is_growth) {
        val = ta_data.achievement[cIdx] - ta_data.last_week[cIdx];
      } else if (metric === "Qtr Trend") {
        if (cIdx >= 3) {
          val = ta_data.qtr_trend[cIdx] || (ta_data.achievement[cIdx] * 1.49);
        } else {
          val = "";
        }
      } else if (is_trend_pct) {
        if (cIdx >= 3) {
          const tVal = ta_data.qtr_trend[cIdx] || (ta_data.achievement[cIdx] * 1.49);
          const target = ta_data.target[cIdx];
          val = target > 0 ? (tVal / target * 100) : 0;
        } else {
          val = "";
        }
      }
      
      if (val !== "" && val !== null) {
        td.innerText = formatNumber(val, is_pct || is_trend_pct || is_growth ? "percent" : "decimal");
        if (is_growth) {
          td.style.color = val >= 0 ? "var(--color-success)" : "var(--color-danger)";
          td.style.fontWeight = "600";
        }
      } else {
        td.innerText = "-";
      }
      
      // Make Editable
      const editable_metrics = ["Target", "Achievement", "Last Week Status", "Last Month Status"];
      if (editable_metrics.includes(metric) && !(metric === "Target" && cIdx >= 3)) { // Block manual input of Cr targets (pulled from global settings)
        td.className += " editable";
        td.contentEditable = "true";
        td.addEventListener("blur", (e) => {
          const new_val = parseFloat(e.target.innerText.replace(/[%]/g, "")) || 0;
          if (metric === "Target") ta_data.target[cIdx] = new_val;
          else if (metric === "Achievement") ta_data.achievement[cIdx] = new_val;
          else if (metric === "Last Week Status") ta_data.last_week[cIdx] = new_val;
          else if (metric === "Last Month Status") ta_data.last_month[cIdx] = new_val;
          
          recalculateAll();
          renderExecSummary();
          updateDashboardKPIs();
        });
      }
      
      tr.appendChild(td);
    }
    tbody_ta.appendChild(tr);
  });
  
  // 2. Monthwise
  const mw_data = reviewData.exec_summary.monthwise_performance;
  const tbody_mw = document.querySelector("#table-monthwise tbody");
  tbody_mw.innerHTML = "";
  
  const mw_metrics = ["Fin Value %", "Fin Vol %", "Card %", "Fin Value (In Cr)", "Card Value (In Cr)"];
  const mw_keys = ["fin_value_pct", "fin_vol_pct", "card_pct", "fin_value_cr", "card_value_cr"];
  
  mw_metrics.forEach((metric, rIdx) => {
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.innerText = metric;
    tdName.style.fontWeight = "bold";
    tr.appendChild(tdName);
    
    const key = mw_keys[rIdx];
    const vals = mw_data[key];
    const is_pct = metric.includes("%");
    
    for (let cIdx = 0; cIdx < 3; cIdx++) {
      const td = document.createElement("td");
      td.className = "num-align editable";
      td.contentEditable = "true";
      
      const val = vals[cIdx];
      const hasVal = val !== null && val !== "" && val !== 0;
      
      // Color code: Fin Value% / Fin Vol% → green if >= 60%, amber if >= 40%
      let color = "";
      if (is_pct && hasVal) {
        color = val >= 60 ? "#059669" : val >= 40 ? "#d97706" : "#f87171";
      } else if (!is_pct && hasVal) {
        color = "#2563eb"; // cyan for Cr values
      }
      
      td.innerText = hasVal ? formatNumber(val, is_pct ? "percent" : "decimal") : "—";
      if (color) td.style.color = color;
      if (color) td.style.fontWeight = "600";
      if (!hasVal) td.style.color = "var(--text-muted)";
      
      td.addEventListener("blur", (e) => {
        const raw = e.target.innerText.replace(/[%,₹—\s]/g, "");
        const new_val = parseFloat(raw) || 0;
        vals[cIdx] = new_val;
        recalculateAll();
        renderExecSummary();
      });
      tr.appendChild(td);
    }
    
    // Q1 Average / Total
    const tdTotal = document.createElement("td");
    tdTotal.className = "num-align";
    tdTotal.style.fontWeight = "bold";
    
    const active_vals = vals.filter(v => v !== null && v !== "" && v !== 0);
    const sum = active_vals.reduce((a, b) => a + b, 0);
    const avg = active_vals.length > 0 ? (sum / active_vals.length) : 0;
    const totalVal = is_pct ? avg : sum;
    
    // Color total
    if (is_pct && totalVal > 0) {
      tdTotal.style.color = totalVal >= 60 ? "#059669" : totalVal >= 40 ? "#d97706" : "#f87171";
    } else if (!is_pct && totalVal > 0) {
      tdTotal.style.color = "#2563eb";
    }
    
    tdTotal.innerText = formatNumber(totalVal, is_pct ? "percent" : "decimal");
    tr.appendChild(tdTotal);
    tbody_mw.appendChild(tr);
  });

}

function renderDisbursement() {
  if (!reviewData.disbursement) return;
  const disb = reviewData.disbursement.financiers;
  const tbody = document.querySelector("#table-disb-status tbody");
  if (!tbody) return; // tab was removed from HTML
  tbody.innerHTML = "";
  
  Object.keys(disb).forEach(fin => {
    const info = disb[fin];
    const tr = document.createElement("tr");
    
    tr.innerHTML = `
      <td style="font-weight: 500;">${fin}</td>
      <td class="num-align editable" contenteditable="true" data-field="sourcing">${info.sourcing}</td>
      <td class="num-align editable" contenteditable="true" data-field="pending">${info.pending}</td>
      <td class="num-align" style="font-weight:600;">${formatNumber(info.pending_pct, "percent")}</td>
      <td class="num-align editable" contenteditable="true" data-field="last_week_pending_pct">${formatNumber(info.last_week_pending_pct, "percent")}</td>
    `;
    
    // Event listeners for edits
    tr.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const field = td.getAttribute("data-field");
        const val = parseFloat(td.innerText.replace(/[%]/g, "")) || 0;
        info[field] = val;
        recalculateAll();
        renderDisbursement();
        updateDashboardKPIs();
      });
    });
    
    tbody.appendChild(tr);
  });
  
  // Overall Row
  const totals = reviewData.disbursement.totals;
  const trTotal = document.createElement("tr");
  trTotal.className = "total-row";
  trTotal.innerHTML = `
    <td>Overall</td>
    <td class="num-align">${formatNumber(totals.sourcing, "integer")}</td>
    <td class="num-align">${formatNumber(totals.pending, "integer")}</td>
    <td class="num-align">${formatNumber(totals.pending_pct, "percent")}</td>
    <td class="num-align">-</td>
  `;
  tbody.appendChild(trTotal);
  
  // Top 5 stores aged
  const aged_body = document.querySelector("#table-top-aged tbody");
  aged_body.innerHTML = "";
  const contributors_body = document.querySelector("#table-top-contributors tbody");
  contributors_body.innerHTML = "";
  
  const top_stores = reviewData.disbursement.top_stores_aged;
  for (let i = 0; i < 5; i++) {
    const item = top_stores[i] || { sno: i+1, store: "", days: 0, contributor: "", count: 0 };
    
    const trAged = document.createElement("tr");
    trAged.innerHTML = `
      <td style="text-align:center; width: 40px;">${item.sno}</td>
      <td class="editable" contenteditable="true" data-idx="${i}" data-field="store">${item.store}</td>
      <td class="num-align editable" contenteditable="true" data-idx="${i}" data-field="days">${item.days}</td>
    `;
    trAged.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const idx = parseInt(td.getAttribute("data-idx"));
        const field = td.getAttribute("data-field");
        const val = field === "days" ? (parseInt(td.innerText) || 0) : td.innerText;
        top_stores[idx][field] = val;
      });
    });
    aged_body.appendChild(trAged);
    
    const trContr = document.createElement("tr");
    trContr.innerHTML = `
      <td style="text-align:center; width: 40px;">${item.sno}</td>
      <td class="editable" contenteditable="true" data-idx="${i}" data-field="contributor">${item.contributor}</td>
      <td class="num-align editable" contenteditable="true" data-idx="${i}" data-field="count">${item.count}</td>
    `;
    trContr.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const idx = parseInt(td.getAttribute("data-idx"));
        const field = td.getAttribute("data-field");
        const val = field === "count" ? (parseInt(td.innerText) || 0) : td.innerText;
        top_stores[idx][field] = val;
      });
    });
    contributors_body.appendChild(trContr);
  }

  // Ageing Status Grid
  const ageing = reviewData.disbursement.ageing_status;
  const tbody_age = document.querySelector("#table-ageing-status tbody");
  tbody_age.innerHTML = "";
  
  Object.keys(ageing).forEach((fin, idx) => {
    const info = ageing[fin];
    const tr = document.createElement("tr");
    
    tr.innerHTML = `
      <td style="text-align:center;">${idx+1}</td>
      <td style="font-weight: 500;">${fin}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="4_7_days" data-type="0">${info["4_7_days"][0]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="4_7_days" data-type="1">${info["4_7_days"][1]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="8_15_days" data-type="0">${info["8_15_days"][0]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="8_15_days" data-type="1">${info["8_15_days"][1]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="15_plus" data-type="0">${info["15_plus"][0]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="15_plus" data-type="1">${info["15_plus"][1]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="90_plus" data-type="0">${info["90_plus"][0]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="90_plus" data-type="1">${info["90_plus"][1]}</td>
      
      <td class="num-align" style="font-weight:600;">${info.total[0]}</td>
      <td class="num-align" style="font-weight:600;">${info.total[1]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="last_week" data-type="0">${info.last_week[0]}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-bracket="last_week" data-type="1">${info.last_week[1]}</td>
    `;
    
    tr.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const fin_key = td.getAttribute("data-fin");
        const bracket = td.getAttribute("data-bracket");
        const typ = parseInt(td.getAttribute("data-type"));
        const val = parseFloat(td.innerText.replace(/,/g, "")) || 0;
        ageing[fin_key][bracket][typ] = val;
        recalculateAll();
        renderDisbursement();
      });
    });
    
    tbody_age.appendChild(tr);
  });
  
  // Total Row for Ageing
  const ageTotals = reviewData.disbursement.ageing_totals;
  const trAgeTotal = document.createElement("tr");
  trAgeTotal.className = "total-row";
  trAgeTotal.innerHTML = `
    <td></td>
    <td>Total</td>
    <td class="num-align">${ageTotals["4_7_days"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["4_7_days"][1], "currency")}</td>
    <td class="num-align">${ageTotals["8_15_days"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["8_15_days"][1], "currency")}</td>
    <td class="num-align">${ageTotals["15_plus"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["15_plus"][1], "currency")}</td>
    <td class="num-align">${ageTotals["90_plus"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["90_plus"][1], "currency")}</td>
    <td class="num-align">${ageTotals["total"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["total"][1], "currency")}</td>
    <td class="num-align">${ageTotals["last_week"][0]}</td>
    <td class="num-align">${formatNumber(ageTotals["last_week"][1], "currency")}</td>
  `;
  tbody_age.appendChild(trAgeTotal);
}

function populateBrandFilter() {
  const brandSelect = document.getElementById("filter-brand");
  if (!brandSelect) return;
  
  const currentVal = brandSelect.value;
  brandSelect.innerHTML = '<option value="ALL">All Brands</option>';
  
  if (reviewData.brand_performance) {
    const brands = Object.keys(reviewData.brand_performance).sort();
    brands.forEach(b => {
      const opt = document.createElement("option");
      opt.value = b;
      opt.innerText = b;
      brandSelect.appendChild(opt);
    });
  }
  
  // Try to keep selection
  const options = Array.from(brandSelect.options).map(o => o.value);
  if (options.includes(currentVal)) {
    brandSelect.value = currentVal;
  }
}

function renderCategoryPerformance() {
  // Delegate to drill-down module (drill_down.js) when loaded
  if (typeof window._renderCategoryPerfDrillDown === 'function') {
    return window._renderCategoryPerfDrillDown();
  }
  const cats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"];
  const cols = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  const _pct2 = v => (v === null || v === undefined || isNaN(v)) ? "-" : `${Number(v).toFixed(2)}%`;
  
  const brandSelect = document.getElementById("filter-brand");
  const selectedBrand = brandSelect ? brandSelect.value : "ALL";
  let activeData = reviewData.category_performance;
  if (selectedBrand !== "ALL" && reviewData.brand_performance && reviewData.brand_performance[selectedBrand]) {
    activeData = reviewData.brand_performance[selectedBrand];
    if (!activeData.products && activeData.products_qty) {
      activeData.products = {
        qty: activeData.products_qty,
        value: activeData.products_val
      };
    }
  }
  
  // 1. Qty Table
  const qty_body = document.querySelector("#table-cat-qty tbody");
  qty_body.innerHTML = "";
  
    cats.forEach(cat => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-category", cat);
    const info = activeData.qty[cat] || {};
    
    // Compute derived totals dynamically when using the new API format
    const finCols = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
    const cardCols = ["PineLab", "PAYTM", "Benow"];
    const paperCols = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"];
    
    const computedTotal = info.total != null ? info.total : cols.reduce((s, c) => s + (info[c] || 0), 0);
    const computedFinTotal = finCols.reduce((s, c) => s + (info[c] || 0), 0);
    const computedPaper = info.paper_total != null ? info.paper_total : paperCols.reduce((s, c) => s + (info[c] || 0), 0);
    const computedCard = info.card_total != null ? info.card_total : cardCols.reduce((s, c) => s + (info[c] || 0), 0);
    const computedFinShare = info.fin_share != null ? info.fin_share : (computedTotal > 0 ? (computedFinTotal / computedTotal * 100) : 0);
    
    let tds = `<td style="font-weight: 500;">${cat}</td>`;
    cols.forEach(col => {
      tds += `<td class="num-align" data-cat="${cat}" data-col="${col}" data-type="qty">${info[col] || 0}</td>`;
    });
    
    tds += `
      <td class="num-align" style="font-weight:600;">${computedTotal}</td>
      <td class="num-align" style="font-weight:600;">${_pct2(computedFinShare)}</td>
      <td class="num-align" style="font-weight:600;">${computedPaper}</td>
      <td class="num-align" style="font-weight:600;">${computedCard}</td>
      <td class="num-align" data-cat="${cat}" data-col="last_week_pct" data-type="qty">${info.last_week_pct != null ? _pct2(info.last_week_pct) : '-'}</td>
      <td class="num-align" data-cat="${cat}" data-col="apr_closing_pct" data-type="qty">${info.apr_closing_pct != null ? _pct2(info.apr_closing_pct) : '-'}</td>
    `;
    tr.innerHTML = tds;
    qty_body.appendChild(tr);
    
    // Support both new API format (products_qty) and old static format (products.qty)
    const prodQtySource = (activeData.products_qty && activeData.products_qty[cat])
      ? activeData.products_qty[cat]
      : (activeData.products && activeData.products.qty && activeData.products.qty[cat])
        ? activeData.products.qty[cat]
        : null;
        
    if (prodQtySource) {
      Object.keys(prodQtySource).sort().forEach(prod => {
        // (removed: no longer skip products that share a name with their category)
        
        const pTr = document.createElement("tr");
        pTr.setAttribute("data-parent-category", cat);
        pTr.style.display = "none";
        pTr.style.background = "rgba(0, 242, 254, 0.02)";
        const pInfo = prodQtySource[prod];
        
        let pTds = `<td style="padding-left: 20px; font-weight: 400; color: var(--text-muted);">- ${prod}</td>`;
        cols.forEach(col => {
          pTds += `<td class="num-align" data-cat="${cat}" data-prod="${prod}" data-col="${col}" data-type="qty">${pInfo[col] || 0}</td>`;
        });
        
        const finCols = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"];
        const cardCols = ["PineLab", "PAYTM", "Benow"];
        let pTotal = cols.reduce((s, c) => s + (pInfo[c] || 0), 0);
        let pPaper = finCols.reduce((s, c) => s + (pInfo[c] || 0), 0);
        let pCard = cardCols.reduce((s, c) => s + (pInfo[c] || 0), 0);
        const pFinShare = pTotal > 0 ? ((pPaper + pCard) / pTotal * 100) : 0;
        
        pTds += `
          <td class="num-align" style="font-weight:500;">${pTotal}</td>
          <td class="num-align">${_pct2(pFinShare)}</td>
          <td class="num-align">${pPaper}</td>
          <td class="num-align">${pCard}</td>
          <td class="num-align">-</td>
          <td class="num-align">-</td>
        `;
        pTr.innerHTML = pTds;
        qty_body.appendChild(pTr);
      });
    }
  });
  
  // --- Grand Total Volume Row (computed dynamically from category data) ---
  const _finCols = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"];
  const _cardCols = ["PineLab", "PAYTM", "Benow"];
  
  // Try static totals first, fall back to computing from categories
  let qty_tots = activeData.qty_totals || (reviewData.category_performance && reviewData.category_performance.qty_totals);
  if (!qty_tots || !qty_tots.totals) {
    const totalsMap = {};
    cols.forEach(c => { totalsMap[c] = 0; });
    cats.forEach(cat => {
      const info = activeData.qty[cat] || {};
      cols.forEach(c => { totalsMap[c] += (info[c] || 0); });
    });
    const grandQty = cols.reduce((s, c) => s + totalsMap[c], 0);
    const paperQty = _finCols.reduce((s, c) => s + (totalsMap[c] || 0), 0);
    const cardQty = _cardCols.reduce((s, c) => s + (totalsMap[c] || 0), 0);
    qty_tots = { totals: totalsMap, grand_total: grandQty, paper_total: paperQty, card_total: cardQty };
  }
  
  const trQtyTot = document.createElement("tr");
  trQtyTot.className = "total-row";
  let qty_tot_tds = `<td>Grand Total</td>`;
  cols.forEach(col => {
    qty_tot_tds += `<td class="num-align">${qty_tots.totals[col] || 0}</td>`;
  });
  const tot_fin_share_qty = qty_tots.grand_total > 0 ? ((qty_tots.paper_total + qty_tots.card_total) / qty_tots.grand_total * 100) : 0;
  qty_tot_tds += `
    <td class="num-align">${qty_tots.grand_total}</td>
    <td class="num-align">${_pct2(tot_fin_share_qty)}</td>
    <td class="num-align">${qty_tots.paper_total}</td>
    <td class="num-align">${qty_tots.card_total}</td>
    <td class="num-align">-</td>
    <td class="num-align">-</td>
  `;
  trQtyTot.innerHTML = qty_tot_tds;
  qty_body.appendChild(trQtyTot);
  
  // Share Row
  const trQtyShare = document.createElement("tr");
  trQtyShare.style.background = "rgba(255,255,255,0.01)";
  let qty_sh_tds = `<td style="font-weight:600; color:var(--text-muted);">Share</td>`;
  cols.forEach(col => {
    const sh = qty_tots.grand_total > 0 ? ((qty_tots.totals[col] || 0) / qty_tots.grand_total * 100) : 0;
    qty_sh_tds += `<td class="num-align">${_pct2(sh)}</td>`;
  });
  qty_sh_tds += `
    <td class="num-align">-</td>
    <td class="num-align">-</td>
    <td class="num-align">${_pct2(qty_tots.grand_total > 0 ? (qty_tots.paper_total / qty_tots.grand_total * 100) : 0)}</td>
    <td class="num-align">${_pct2(qty_tots.grand_total > 0 ? (qty_tots.card_total / qty_tots.grand_total * 100) : 0)}</td>
    <td class="num-align">-</td>
    <td class="num-align">-</td>
  `;
  trQtyShare.innerHTML = qty_sh_tds;
  qty_body.appendChild(trQtyShare);
  
  // Share in Fin Value row (Volume mode: Fin Qty / Total Fin Qty)
  const qtyFinTot = (qty_tots.paper_total || 0) + (qty_tots.card_total || 0);
  const sif_qty = (activeData && activeData.sif_qty) || (reviewData.category_performance && reviewData.category_performance.sif_qty);
  const trQtySif = document.createElement("tr");
  trQtySif.style.background = "rgba(255,255,255,0.01)";
  let qty_sif_tds = `<td style="font-weight:600; color:var(--text-muted);">Share in Fin Value</td>`;
  if (sif_qty || qtyFinTot > 0) {
    qty_sif_tds += `<td class="num-align">-</td>`;
    cols.slice(1).forEach(col => {
      const pct = sif_qty && sif_qty[col] != null ? sif_qty[col] : (qtyFinTot > 0 ? ((qty_tots.totals[col] || 0) / qtyFinTot * 100) : 0);
      qty_sif_tds += `<td class="num-align">${_pct2(pct)}</td>`;
    });
    const paperSifPct = sif_qty && sif_qty.paper_total != null ? sif_qty.paper_total : (qtyFinTot > 0 ? ((qty_tots.paper_total || 0) / qtyFinTot * 100) : 0);
    const cardSifPct = sif_qty && sif_qty.card_total != null ? sif_qty.card_total : (qtyFinTot > 0 ? ((qty_tots.card_total || 0) / qtyFinTot * 100) : 0);
    qty_sif_tds += `
      <td class="num-align">-</td>
      <td class="num-align">-</td>
      <td class="num-align">${_pct2(paperSifPct)}</td>
      <td class="num-align">${_pct2(cardSifPct)}</td>
      <td class="num-align">-</td>
      <td class="num-align">-</td>
    `;
  } else {
    qty_sif_tds += `<td class="num-align" colspan="${cols.length + 6}">-</td>`;
  }
  trQtySif.innerHTML = qty_sif_tds;
  qty_body.appendChild(trQtySif);

  // 2. Value Table
  const val_body = document.querySelector("#table-cat-value tbody");
  val_body.innerHTML = "";
  
  cats.forEach(cat => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-category", cat);
    const info = activeData.value[cat] || {};
    
    let tds = `<td style="font-weight: 500;">${cat}</td>`;
    cols.forEach(col => {
      tds += `<td class="num-align" data-cat="${cat}" data-col="${col}" data-type="val">${formatNumber(info[col] || 0.0, "decimal")}</td>`;
    });
    
    // Compute value totals dynamically (new API format doesn't have pre-computed totals)
    const vComputedTotal = info.total != null ? info.total : cols.reduce((s, c) => s + (parseFloat(info[c]) || 0), 0);
    const vFinTotal = _finCols.reduce((s, c) => s + (parseFloat(info[c]) || 0), 0);
    const vCardTotal = _cardCols.reduce((s, c) => s + (parseFloat(info[c]) || 0), 0);
    const vPaperTotal = info.paper_total != null ? info.paper_total : vFinTotal;
    const vCardTotalComp = info.card_total != null ? info.card_total : vCardTotal;
    const vFinShare = info.fin_share != null ? info.fin_share : (vComputedTotal > 0 ? ((vFinTotal + vCardTotal) / vComputedTotal * 100) : 0);
    tds += `
      <td class="num-align" style="font-weight:600;">${formatNumber(vComputedTotal, "decimal")}</td>
      <td class="num-align" style="font-weight:600;">${_pct2(vFinShare)}</td>
      <td class="num-align" style="font-weight:600;">${formatNumber(vPaperTotal, "decimal")}</td>
      <td class="num-align" style="font-weight:600;">${formatNumber(vCardTotalComp, "decimal")}</td>
      <td class="num-align" data-cat="${cat}" data-col="last_week_pct" data-type="val">${info.last_week_pct != null ? _pct2(info.last_week_pct) : '-'}</td>
      <td class="num-align" data-cat="${cat}" data-col="apr_closing_pct" data-type="val">${info.apr_closing_pct != null ? _pct2(info.apr_closing_pct) : '-'}</td>
    `;
    tr.innerHTML = tds;
    val_body.appendChild(tr);
    
    // Support both new API format (products_val) and old static format (products.value)
    const prodValSource = (activeData.products_val && activeData.products_val[cat])
      ? activeData.products_val[cat]
      : (activeData.products && activeData.products.value && activeData.products.value[cat])
        ? activeData.products.value[cat]
        : null;
    
    if (prodValSource) {
      Object.keys(prodValSource).sort().forEach(prod => {
        // (removed: no longer skip products that share a name with their category)
        
        const pTr = document.createElement("tr");
        pTr.setAttribute("data-parent-category", cat);
        pTr.style.display = "none";
        pTr.style.background = "rgba(0, 242, 254, 0.02)";
        const pInfo = prodValSource[prod];
        
        let pTds = `<td style="padding-left: 20px; font-weight: 400; color: var(--text-muted);">- ${prod}</td>`;
        cols.forEach(col => {
          pTds += `<td class="num-align" data-cat="${cat}" data-prod="${prod}" data-col="${col}" data-type="val">${formatNumber(pInfo[col] || 0.0, "decimal")}</td>`;
        });
        
        const finCols = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"];
        const cardCols = ["PineLab", "PAYTM", "Benow"];
        let pTotal = cols.reduce((s, c) => s + (parseFloat(pInfo[c]) || 0), 0);
        let pPaper = finCols.reduce((s, c) => s + (parseFloat(pInfo[c]) || 0), 0);
        let pCard = cardCols.reduce((s, c) => s + (parseFloat(pInfo[c]) || 0), 0);
        const pFinShare = pTotal > 0 ? ((pPaper + pCard) / pTotal * 100) : 0;
        
        pTds += `
          <td class="num-align" style="font-weight:500;">${formatNumber(pTotal, "decimal")}</td>
          <td class="num-align">${_pct2(pFinShare)}</td>
          <td class="num-align">${formatNumber(pPaper, "decimal")}</td>
          <td class="num-align">${formatNumber(pCard, "decimal")}</td>
          <td class="num-align">-</td>
          <td class="num-align">-</td>
        `;
        pTr.innerHTML = pTds;
        val_body.appendChild(pTr);
      });
    }
  });
  
  // --- Grand Total Value Row (computed dynamically from category data) ---
  let val_tots = activeData.val_totals || (reviewData.category_performance && reviewData.category_performance.val_totals);
  if (!val_tots || !val_tots.totals) {
    const vTotalsMap = {};
    cols.forEach(c => { vTotalsMap[c] = 0; });
    cats.forEach(cat => {
      const info = activeData.value[cat] || {};
      cols.forEach(c => { vTotalsMap[c] += (parseFloat(info[c]) || 0); });
    });
    const grandVal = cols.reduce((s, c) => s + vTotalsMap[c], 0);
    const paperVal = _finCols.reduce((s, c) => s + (vTotalsMap[c] || 0), 0);
    const cardVal = _cardCols.reduce((s, c) => s + (vTotalsMap[c] || 0), 0);
    val_tots = { totals: vTotalsMap, grand_total: grandVal, paper_total: paperVal, card_total: cardVal };
  }
  
  const trValTot = document.createElement("tr");
  trValTot.className = "total-row";
  let val_tot_tds = `<td>Grand Total</td>`;
  cols.forEach(col => {
    val_tot_tds += `<td class="num-align">${formatNumber(val_tots.totals[col] || 0, "decimal")}</td>`;
  });
  const tot_fin_share_val = val_tots.grand_total > 0 ? ((val_tots.paper_total + val_tots.card_total) / val_tots.grand_total * 100) : 0;
  val_tot_tds += `
    <td class="num-align">${formatNumber(val_tots.grand_total, "decimal")}</td>
    <td class="num-align">${_pct2(tot_fin_share_val)}</td>
    <td class="num-align">${formatNumber(val_tots.paper_total, "decimal")}</td>
    <td class="num-align">${formatNumber(val_tots.card_total, "decimal")}</td>
    <td class="num-align">-</td>
    <td class="num-align">-</td>
  `;
  trValTot.innerHTML = val_tot_tds;
  val_body.appendChild(trValTot);
  
  // Share Row (Value)
  const trValShare = document.createElement("tr");
  trValShare.style.background = "rgba(255,255,255,0.01)";
  let val_sh_tds = `<td style="font-weight:600; color:var(--text-muted);">Share</td>`;
  cols.forEach(col => {
    const sh = val_tots.grand_total > 0 ? (val_tots.totals[col] / val_tots.grand_total * 100) : 0;
    val_sh_tds += `<td class="num-align">${_pct2(sh)}</td>`;
  });
  val_sh_tds += `
    <td class="num-align">-</td>
    <td class="num-align">-</td>
    <td class="num-align">${_pct2(val_tots.grand_total > 0 ? (val_tots.paper_total / val_tots.grand_total * 100) : 0)}</td>
    <td class="num-align">${_pct2(val_tots.grand_total > 0 ? (val_tots.card_total / val_tots.grand_total * 100) : 0)}</td>
    <td class="num-align">-</td>
    <td class="num-align">-</td>
  `;
  trValShare.innerHTML = val_sh_tds;
  val_body.appendChild(trValShare);

  const val_sif = activeData.sif || (reviewData.category_performance && reviewData.category_performance.sif);
  const val_asp = activeData.asp || (reviewData.category_performance && reviewData.category_performance.asp);

  // Share in Fin Value row (Value)
  const trValSif = document.createElement("tr");
  trValSif.style.background = "rgba(255,255,255,0.01)";
  let val_sif_tds = `<td style="font-weight:600; color:var(--text-muted);">Share in Fin Value</td>`;
  if (val_sif) {
    val_sif_tds += `<td class="num-align">-</td>`;
    cols.slice(1).forEach(col => {
      val_sif_tds += `<td class="num-align">${_pct2(val_sif[col])}</td>`;
    });
    val_sif_tds += `
      <td class="num-align">-</td>
      <td class="num-align">-</td>
      <td class="num-align">${_pct2(val_sif.paper_total)}</td>
      <td class="num-align">${_pct2(val_sif.card_total)}</td>
      <td class="num-align">-</td>
      <td class="num-align">-</td>
    `;
  } else {
    val_sif_tds += `<td class="num-align" colspan="${cols.length + 6}">-</td>`;
  }
  trValSif.innerHTML = val_sif_tds;
  val_body.appendChild(trValSif);
  
  // ASP Row (Value)
  const trValAsp = document.createElement("tr");
  trValAsp.style.background = "rgba(255,255,255,0.01)";
  let val_asp_tds = `<td style="font-weight:600; color:var(--text-muted);">ASP</td>`;
  if (val_asp) {
    val_asp_tds += `<td class="num-align">${val_asp.aCASH != null ? formatNumber(val_asp.aCASH, "currency") : '-'}</td>`;
    cols.slice(1).forEach(col => {
      val_asp_tds += `<td class="num-align">${formatNumber(val_asp[col], "currency")}</td>`;
    });
    val_asp_tds += `
      <td class="num-align">${val_asp.total != null ? formatNumber(val_asp.total, "currency") : '-'}</td>
      <td class="num-align">-</td>
      <td class="num-align">${val_asp.paper_total != null ? formatNumber(val_asp.paper_total, "currency") : '-'}</td>
      <td class="num-align">${val_asp.card_total != null ? formatNumber(val_asp.card_total, "currency") : '-'}</td>
      <td class="num-align">-</td>
      <td class="num-align">-</td>
    `;
  } else {
    val_asp_tds += `<td class="num-align" colspan="${cols.length + 6}">-</td>`;
  }
  trValAsp.innerHTML = val_asp_tds;
  val_body.appendChild(trValAsp);

  applyCategoryFilter();
}

function applyCategoryFilter() {
  // Replaced by drill-down navigation when drill_down.js is loaded
  if (window._useDrillDown) return;
  const filterVal = document.getElementById("filter-category") ? document.getElementById("filter-category").value : "ALL";
  
  const qtyCatRows = document.querySelectorAll("#table-cat-qty tbody tr[data-category]");
  const valCatRows = document.querySelectorAll("#table-cat-value tbody tr[data-category]");
  
  const qtyProdRows = document.querySelectorAll("#table-cat-qty tbody tr[data-parent-category]");
  const valProdRows = document.querySelectorAll("#table-cat-value tbody tr[data-parent-category]");
  
  const qtyTotalRows = document.querySelectorAll("#table-cat-qty tbody tr:not([data-category]):not([data-parent-category])");
  const valTotalRows = document.querySelectorAll("#table-cat-value tbody tr:not([data-category]):not([data-parent-category])");
  
  if (filterVal === "ALL") {
    qtyCatRows.forEach(row => row.style.display = "");
    valCatRows.forEach(row => row.style.display = "");
    qtyProdRows.forEach(row => row.style.display = "none");
    valProdRows.forEach(row => row.style.display = "none");
    qtyTotalRows.forEach(row => row.style.display = "");
    valTotalRows.forEach(row => row.style.display = "");
  } else {
    // QTY table: show only the selected category row + any sub-product rows for it
    qtyCatRows.forEach(row => {
      row.style.display = (row.getAttribute("data-category") === filterVal) ? "" : "none";
    });
    qtyProdRows.forEach(row => {
      row.style.display = (row.getAttribute("data-parent-category") === filterVal) ? "" : "none";
    });
    
    // VALUE table: hide category rows, show sub-product rows for the selected category
    valCatRows.forEach(row => {
      // If no sub-product rows exist for this category, show the category row itself
      const hasProdRows = document.querySelectorAll(`#table-cat-value tbody tr[data-parent-category="${filterVal}"]`).length > 0;
      if (!hasProdRows) {
        row.style.display = (row.getAttribute("data-category") === filterVal) ? "" : "none";
      } else {
        row.style.display = "none";
      }
    });
    valProdRows.forEach(row => {
      row.style.display = (row.getAttribute("data-parent-category") === filterVal) ? "" : "none";
    });
    
    qtyTotalRows.forEach(row => row.style.display = "none");
    valTotalRows.forEach(row => row.style.display = "none");
  }
}

function renderContest() {
  // 1. Summary
  if (!reviewData.dmi_contest) return;
  const summary = reviewData.dmi_contest.summary;
  const tbody_sum = document.querySelector("#table-contest-summary tbody");
  if (!tbody_sum) return; // tab was removed from HTML
  tbody_sum.innerHTML = "";
  
  Object.keys(summary).forEach(brand => {
    const info = summary[brand];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-weight:500;">${brand}</td>
      <td class="num-align editable" contenteditable="true" data-brand="${brand}" data-field="target">${info.target}</td>
      <td class="num-align" style="font-weight: 500;">${info.achievement}</td>
      <td class="num-align" style="font-weight: 600;">${formatNumber(info.achvt_pct, "percent")}</td>
      <td class="num-align" style="color:var(--color-danger);">${info.gap}</td>
      <td class="num-align">${formatNumber(info.trend, "integer")}</td>
      <td class="num-align" style="font-weight: 600;">${formatNumber(info.trend_pct, "percent")}</td>
    `;
    
    tr.querySelector(".editable").addEventListener("blur", (e) => {
      const b = tr.querySelector(".editable").getAttribute("data-brand");
      const val = parseInt(e.target.innerText) || 0;
      summary[b].target = val;
      recalculateAll();
      renderContest();
    });
    tbody_sum.appendChild(tr);
  });
  
  // 2. Branches
  const branches = reviewData.dmi_contest.branches;
  const tbody_br = document.querySelector("#table-contest-branches tbody");
  tbody_br.innerHTML = "";
  
  let sno = 1;
  let tot_oppo = 0, tot_samsung = 0, tot_vivo = 0;
  
  Object.keys(branches).sort().forEach(br => {
    const info = branches[br];
    const tr = document.createElement("tr");
    const row_tot = (info.OPPO || 0) + (info.SAMSUNG || 0) + (info.VIVO || 0);
    
    tr.innerHTML = `
      <td style="text-align:center; width:50px;">${sno++}</td>
      <td style="font-weight: 500;">${br}</td>
      <td class="num-align editable" contenteditable="true" data-branch="${br}" data-brand="OPPO">${info.OPPO || 0}</td>
      <td class="num-align editable" contenteditable="true" data-branch="${br}" data-brand="SAMSUNG">${info.SAMSUNG || 0}</td>
      <td class="num-align editable" contenteditable="true" data-branch="${br}" data-brand="VIVO">${info.VIVO || 0}</td>
      <td class="num-align" style="font-weight:600;">${row_tot}</td>
    `;
    
    tot_oppo += info.OPPO || 0;
    tot_samsung += info.SAMSUNG || 0;
    tot_vivo += info.VIVO || 0;
    
    tr.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const branch_key = td.getAttribute("data-branch");
        const brand_key = td.getAttribute("data-brand");
        const val = parseInt(td.innerText) || 0;
        branches[branch_key][brand_key] = val;
        recalculateAll();
        renderContest();
      });
    });
    tbody_br.appendChild(tr);
  });
  
  // Add Branch Totals Row
  const trBrTot = document.createElement("tr");
  trBrTot.className = "total-row";
  trBrTot.innerHTML = `
    <td></td>
    <td>Total</td>
    <td class="num-align">${tot_oppo}</td>
    <td class="num-align">${tot_samsung}</td>
    <td class="num-align">${tot_vivo}</td>
    <td class="num-align">${tot_oppo + tot_samsung + tot_vivo}</td>
  `;
  tbody_br.appendChild(trBrTot);
}

function renderManpower() {
  const mp = reviewData.manpower || {};
  const tbody = document.querySelector("#table-manpower tbody");
  if (!tbody) return; // tab was removed from HTML
  tbody.innerHTML = "";
  
  Object.keys(mp).forEach(fin => {
    const info = mp[fin];
    const tr = document.createElement("tr");
    
    tr.innerHTML = `
      <td style="font-weight: 500;">${fin}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-field="plan">${info.plan}</td>
      <td class="num-align editable" contenteditable="true" data-fin="${fin}" data-field="current">${info.current}</td>
      <td class="num-align">${info.req}</td>
      <td class="num-align" style="color:var(--color-danger);">${info.gap}</td>
    `;
    
    tr.querySelectorAll(".editable").forEach(td => {
      td.addEventListener("blur", (e) => {
        const f = td.getAttribute("data-fin");
        const field = td.getAttribute("data-field");
        const val = parseInt(td.innerText) || 0;
        mp[f][field] = val;
        recalculateAll();
        renderManpower();
      });
    });
    tbody.appendChild(tr);
  });
  
  // Total Row
  const mpt = reviewData.manpower_totals;
  const trTot = document.createElement("tr");
  trTot.className = "total-row";
  trTot.innerHTML = `
    <td>Overall</td>
    <td class="num-align">${mpt.plan}</td>
    <td class="num-align">${mpt.current}</td>
    <td class="num-align">${mpt.req}</td>
    <td class="num-align">${mpt.gap}</td>
  `;
  tbody.appendChild(trTot);
}

// Update dashboard KPI cards
function updateDashboardKPIs() {
  if (!reviewData.exec_summary) return;
  
  // 1. Target total (assuming first row is target, column 4 is fin_val)
  const tgt_fin_val = reviewData.exec_summary.target_achievement.target[3] || 0;
  
  // 2. Volume sourcing total
  const totals = (reviewData.disbursement && reviewData.disbursement.totals)
    ? reviewData.disbursement.totals
    : { sourcing: 0, pending: 0, pending_pct: 0 };
    
  const elVolSrc = document.getElementById("kpi-vol-src");
  if (elVolSrc) elVolSrc.innerText = formatNumber(totals.sourcing, "integer");
  
  const elPendPct = document.getElementById("kpi-pend-pct");
  if (elPendPct) elPendPct.innerText = formatNumber(totals.pending_pct, "percent");
  
  // 3. Current Fin Value (from Cat performance totals)
  let cur_fin_val = 0;
  if (reviewData.category_performance && reviewData.category_performance.val_totals) {
    cur_fin_val = reviewData.category_performance.val_totals.grand_total || 0;
  }
  
  const elFinVal = document.getElementById("kpi-fin-val");
  if (elFinVal) elFinVal.innerText = formatNumber(cur_fin_val, "currency") + " / " + formatNumber(tgt_fin_val, "currency");
  
  // 4. Samsung contest achievement
  if (reviewData.dmi_contest && reviewData.dmi_contest.summary && reviewData.dmi_contest.summary.SAMSUNG) {
    const samsung_ach = reviewData.dmi_contest.summary.SAMSUNG.achievement || 0;
    const samsung_tgt = reviewData.dmi_contest.summary.SAMSUNG.target || 72;
    const elSamsung = document.getElementById("kpi-contest-samsung");
    if (elSamsung) elSamsung.innerText = `${samsung_ach} / ${samsung_tgt}`;
    
    const pct = samsung_tgt > 0 ? (samsung_ach / samsung_tgt * 100) : 0;
    const elSamsungPct = document.getElementById("kpi-contest-samsung-pct");
    if (elSamsungPct) elSamsungPct.innerText = `${formatNumber(pct, "percent")} Achieved`;
  }
  
  // 5. Exec Summary Metrics
  const ach_val_pen = reviewData.exec_summary.target_achievement.achievement[0] || 0;
  const ach_vol_pen = reviewData.exec_summary.target_achievement.achievement[1] || 0;
  const lw_val_pen = reviewData.exec_summary.target_achievement.last_week[0] || 0;
  
  const elValPen = document.getElementById("kpi-val-pen");
  if (elValPen) elValPen.innerText = formatNumber(ach_val_pen, "percent");
  
  const elVolPen = document.getElementById("kpi-vol-pen");
  if (elVolPen) elVolPen.innerText = formatNumber(ach_vol_pen, "percent");
  
  const val_pen_diff = ach_val_pen - lw_val_pen;
  const vs_el = document.getElementById("kpi-val-pen-vs");
  if (vs_el) {
    vs_el.innerText = `${val_pen_diff >= 0 ? "+" : ""}${formatNumber(val_pen_diff, "percent")} vs Last Week`;
    vs_el.className = val_pen_diff >= 0 ? "trend-up" : "trend-down";
  }
}

// Render Financier Target vs Achievement chart on Dashboard
let _chartMode = 'volume'; // 'volume' or 'value'

function setChartMode(mode) {
  _chartMode = mode;
  // Update button styles
  const volBtn = document.getElementById("btn-chart-volume");
  const valBtn = document.getElementById("btn-chart-value");
  const title  = document.getElementById("chartTitle");
  if (volBtn && valBtn) {
    if (mode === 'volume') {
      volBtn.style.cssText = "padding:5px 14px; border-radius:20px; border:1.5px solid #2563eb; background:rgba(79,172,254,0.2); color:#2563eb; font-size:12px; font-weight:600; cursor:pointer;";
      valBtn.style.cssText = "padding:5px 14px; border-radius:20px; border:1.5px solid rgba(255,255,255,0.15); background:transparent; color:#94a3b8; font-size:12px; font-weight:600; cursor:pointer;";
    } else {
      valBtn.style.cssText = "padding:5px 14px; border-radius:20px; border:1.5px solid #d97706; background:rgba(245,214,122,0.15); color:#d97706; font-size:12px; font-weight:600; cursor:pointer;";
      volBtn.style.cssText = "padding:5px 14px; border-radius:20px; border:1.5px solid rgba(255,255,255,0.15); background:transparent; color:#94a3b8; font-size:12px; font-weight:600; cursor:pointer;";
    }
  }
  if (title) title.innerText = mode === 'volume'
    ? "Financier Target vs Achievement — Volume (Units)"
    : "Financier Target vs Achievement — Value (₹ Cr)";
  renderChart();
}

function renderChart() {
  const canvas = document.getElementById("financierChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  if (financierChart) {
    financierChart.destroy();
    financierChart = null;
  }

  // Paper financiers (Volume targets)
  const paperFins  = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI"];
  // Card financiers (Value targets in Cr)
  const cardFins   = ["PineLab", "PAYTM", "Benow"];

  // --- Annual targets from targets.json (loaded into globalTargets on init) ---
  const annualTargets = window._globalFinTargets || {};

  // --- Live achievements from zone_performance totals ---
  const liveQty = window._liveFinQty || {};
  const liveVal = window._liveFinVal || {};

  // --- Timeframe-adjusted targets ---
  const timeframe = (document.getElementById("timeframeSelect") || {}).value || "monthly";
  const tfFactor = timeframe === "quarterly" ? 3/12 : timeframe === "daily" ? 1/365 : 1/12;

  const isVolume = _chartMode === 'volume';
  const fins     = isVolume ? paperFins : cardFins;
  const labels   = fins;

  const targets = fins.map(f => {
    const annual = annualTargets[f] || annualTargets[f.toLowerCase()] || 0;
    return parseFloat((annual * tfFactor).toFixed(isVolume ? 0 : 2));
  });

  const achievements = fins.map(f => {
    if (isVolume) return liveQty[f] || 0;
    // Value in Cr
    return parseFloat(((liveVal[f] || 0)).toFixed(2)); // already in Cr from backend
  });

  // Color each achievement bar based on hit% (red < 60%, amber 60–90%, green >= 90%)
  const barColors = achievements.map((ach, i) => {
    const tgt = targets[i] || 1;
    const pct = ach / tgt;
    if (pct >= 0.9)  return 'rgba(52, 211, 153, 0.80)';  // green
    if (pct >= 0.6)  return 'rgba(251, 191, 36, 0.80)';  // amber
    return           'rgba(248, 113, 113, 0.80)';          // red
  });
  const borderColors = achievements.map((ach, i) => {
    const tgt = targets[i] || 1;
    const pct = ach / tgt;
    if (pct >= 0.9)  return '#059669';
    if (pct >= 0.6)  return '#d97706';
    return           '#f87171';
  });

  financierChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: `Target (${timeframe})`,
          data: targets,
          backgroundColor: 'rgba(255,255,255,0.07)',
          borderColor: 'rgba(255,255,255,0.25)',
          borderWidth: 1.5,
          borderRadius: 6,
          borderDash: [4,4],
          order: 2
        },
        {
          label: 'Actual',
          data: achievements,
          backgroundColor: barColors,
          borderColor: borderColors,
          borderWidth: 1.5,
          borderRadius: 6,
          order: 1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(0,0,0,0.05)' },
          ticks: {
            color: '#94a3b8',
            font: { family: 'Inter', size: 11 },
            callback: v => isVolume ? v.toLocaleString() : v.toFixed(2) + ' Cr'
          },
          title: {
            display: true,
            text: isVolume ? 'Quantity (Units)' : 'Value (₹ Cr)',
            color: '#64748b',
            font: { size: 11 }
          }
        },
        x: {
          grid: { display: false },
          ticks: { color: '#94a3b8', font: { family: 'Inter', size: 12, weight: '600' } }
        }
      },
      plugins: {
        legend: {
          labels: { color: '#f1f5f9', font: { family: 'Inter', size: 12 }, padding: 16 }
        },
        tooltip: {
          backgroundColor: 'rgba(10,18,40,0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          titleColor: '#f1f5f9',
          bodyColor: '#94a3b8',
          padding: 12,
          callbacks: {
            afterLabel: function(context) {
              if (context.datasetIndex === 1) {
                const tgt = context.chart.data.datasets[0].data[context.dataIndex] || 1;
                const ach = context.parsed.y;
                const pct = (ach / tgt * 100).toFixed(1);
                return `Achievement: ${pct}%`;
              }
              return '';
            }
          }
        },
        // Achievement % labels on top of bars
        datalabels: false
      }
    }
  });
}

// Load annual targets for chart from API
function loadGlobalTargetsForChart() {
  fetch('/api/targets')
    .then(r => r.json())
    .then(t => {
      const fvt = t.financier_volume_targets || {};
      // Normalise keys to match fin names in chart
      const keyMap = {
        'BAJAJ': 'BAJAJ', 'HDB': 'HDB', 'IDFC': 'IDFC', 'PFL': 'PFL',
        'TVS': 'TVS', 'DMI': 'DMI',
        'PinelAb': 'PineLab', 'PayTm': 'PAYTM', 'Benow': 'Benow'
      };
      const mapped = {};
      Object.entries(fvt).forEach(([k, v]) => {
        const normKey = keyMap[k] || k;
        mapped[normKey] = v;
      });
      window._globalFinTargets = mapped;
      renderChart();
    })
    .catch(() => {});
}

// Modal handling
function initModals() {
  const modal = document.getElementById("newDateModal");
  const btnNewDate = document.getElementById("btnNewDate");
  const btnCancel = document.getElementById("btnCancelModal");
  const btnConfirm = document.getElementById("btnConfirmModal");
  
  btnNewDate.addEventListener("click", () => {
    const today = new Date().toISOString().split('T')[0];
    document.getElementById("newReviewDate").value = today;
    document.getElementById("newStatusAsOf").value = today;
    modal.classList.add("active");
  });
  
  btnCancel.addEventListener("click", () => {
    modal.classList.remove("active");
  });
  
  btnConfirm.addEventListener("click", () => {
    const new_date = document.getElementById("newReviewDate").value;
    const status_date = document.getElementById("newStatusAsOf").value;
    if (!new_date) {
      showToast("Please select a date", "error");
      return;
    }

    // reviewDateSelect is an <input type="date"> — just set its value directly
    const selectEl = document.getElementById("reviewDateSelect");
    if (selectEl) {
      selectEl.value = new_date;
    }

    // Request server to create a template initialized with global targets
    activeDate = new_date;
    modal.classList.remove("active");

    loadData(activeDate);
    showToast(`Created review template for ${new_date}`, "success");
  });
}

function robustParseCSV(text) {
  let p = '', r = [];
  let q = false;
  let row = [''];
  for (let i = 0; i < text.length; i++) {
    let c = text[i];
    let next = text[i+1];
    if (c === '"') {
      if (q && next === '"') { row[row.length - 1] += '"'; i++; }
      else { q = !q; }
    } else if (c === ',' && !q) {
      row.push('');
    } else if ((c === '\r' || c === '\n') && !q) {
      if (c === '\r' && next === '\n') { i++; }
      r.push(row);
      row = [''];
    } else {
      row[row.length - 1] += c;
    }
  }
  if (row.length > 1 || row[0] !== '') { r.push(row); }
  return r.filter(row => row.some(cell => cell.trim() !== ''));
}

// Drag & Drop CSV Sourcing Data Import
function initCSVUpload() {
  const zone = document.getElementById("csvDragZone");
  
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--accent-cyan)";
    zone.style.background = "rgba(0, 242, 254, 0.05)";
  });
  
  zone.addEventListener("dragleave", () => {
    zone.style.borderColor = "var(--panel-border)";
    zone.style.background = "rgba(255, 255, 255, 0.01)";
  });
  
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--panel-border)";
    zone.style.background = "rgba(255, 255, 255, 0.01)";
    
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".csv")) {
      const reader = new FileReader();
      reader.onload = (evt) => {
        parseSourcingCSV(evt.target.result);
      };
      reader.readAsText(file);
    } else {
      showToast("Please upload a valid CSV file", "error");
    }
  });
}

// Parse Raw Sourcing CSV Data
function parseSourcingCSV(csvText) {
  try {
    const lines = csvText.split('\n');
    let importedCount = 0;
    
    lines.forEach(line => {
      const cols = line.split(',').map(c => c.trim().replace(/"/g, ''));
      if (cols.length >= 3) {
        const fin_input = cols[0].toUpperCase();
        const sourcing = parseInt(cols[1]);
        const pending = parseInt(cols[2]);
        
        if (!isNaN(sourcing) && !isNaN(pending)) {
          // Match financier names and set them
          const disb = reviewData.disbursement.financiers;
          const ft = reviewData.financier_targets;
          
          Object.keys(disb).forEach(fin => {
            if (fin_input.includes(fin) || fin.includes(fin_input)) {
              disb[fin].sourcing = sourcing;
              disb[fin].pending = pending;
              if (ft[fin]) {
                ft[fin].achievement = sourcing;
              }
              importedCount++;
            }
          });
        }
      }
    });
    
    if (importedCount > 0) {
      recalculateAll();
      renderAllTables();
      updateDashboardKPIs();
      renderChart();
      showToast(`Imported sourcing data for ${importedCount} financiers!`, "success");
    } else {
      showToast("CSV format not recognized. Format should be: Financier, Sourcing, Pending", "error");
    }
  } catch (err) {
    showToast("Failed to parse CSV file", "error");
    console.error(err);
  }
}

// =====================================================
// MODAL INIT
// =====================================================
function initModals() {
  const modal   = document.getElementById('newDateModal');
  const btnOpen = document.getElementById('btnNewDate');
  const btnCancel = document.getElementById('btnCancelModal');
  const btnConfirm = document.getElementById('btnConfirmModal');

  if (!modal) return;

  // Open modal
  if (btnOpen) {
    btnOpen.addEventListener('click', () => {
      const today = new Date().toISOString().split('T')[0];
      document.getElementById('newReviewDate').value = today;
      document.getElementById('newStatusAsOf').value = today;
      modal.classList.add('open');
    });
  }

  // Close on backdrop click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.remove('open');
  });

  // Cancel button
  if (btnCancel) btnCancel.addEventListener('click', () => modal.classList.remove('open'));

  // Confirm button — create new review record
  if (btnConfirm) {
    btnConfirm.addEventListener('click', () => {
      const newDate = document.getElementById('newReviewDate').value;
      if (!newDate) { showToast('Please select a review date', 'error'); return; }

      activeDate = newDate;
      const dateInput = document.getElementById('reviewDateSelect');
      if (dateInput) dateInput.value = newDate;

      modal.classList.remove('open');
      loadData(activeDate);
      loadDashboardData(activeDate, document.getElementById('timeframeSelect').value || 'daily');
      showToast(`Created new review for ${newDate}`, 'success');
    });
  }
}

// Global actions button events
function initActions() {
  // Save Data
  document.getElementById("btnSaveData").addEventListener("click", () => {
    if (!activeDate) { showToast("No active date selected", "error"); return; }
    fetch(`/api/review/${activeDate}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reviewData)
    })
      .then(res => res.json())
      .then(res => {
        if (res.status === "success") {
          showToast("Review data saved successfully!", "success");
        } else {
          showToast(res.message || "Error saving", "error");
        }
      })
      .catch(err => {
        showToast("Error saving data", "error");
      });
  });
  
  // Export Excel
  document.getElementById("btnExportExcel").addEventListener("click", () => {
    if (!activeDate) { showToast("No active date selected", "error"); return; }
    window.location.href = `/api/export/excel/${activeDate}`;
    showToast("Downloading Excel spreadsheet...", "success");
  });
  
  // Export PPTX
  document.getElementById("btnExportPptx").addEventListener("click", () => {
    if (!activeDate) { showToast("No active date selected", "error"); return; }
    window.location.href = `/api/export/pptx/${activeDate}`;
    showToast("Generating PowerPoint deck...", "success");
  });
  
  // Filters
  const filterBrand = document.getElementById("filter-brand");
  if (filterBrand) filterBrand.addEventListener("change", renderCategoryPerformance);
  
  const filterCategory = document.getElementById("filter-category");
  if (filterCategory) filterCategory.addEventListener("change", applyCategoryFilter);
  
  // Save global settings
  document.getElementById("btnSaveConfig").addEventListener("click", () => {
    const config = {
      exec_summary_targets: {
        value_penetration: parseFloat(document.getElementById("cfg-val-pen").value),
        vol_penetration: parseFloat(document.getElementById("cfg-vol-pen").value),
        cards_penetration: parseFloat(document.getElementById("cfg-cards-pen").value),
        fin_value_cr: parseFloat(document.getElementById("cfg-fin-val").value),
        card_value_cr: parseFloat(document.getElementById("cfg-card-val").value),
      },
      financier_volume_targets: {
        BAJAJ: parseInt(document.getElementById("cfg-fin-bajaj").value),
        HDB: parseInt(document.getElementById("cfg-fin-hdb").value),
        IDFC: parseInt(document.getElementById("cfg-fin-idfc").value),
        PFL: parseInt(document.getElementById("cfg-fin-pfl").value),
        TVS: parseInt(document.getElementById("cfg-fin-tvs").value),
        DMI: parseInt(document.getElementById("cfg-fin-dmi").value),
        PinelAb: parseFloat(document.getElementById("cfg-fin-pine").value),
        PayTm: parseFloat(document.getElementById("cfg-fin-paytm").value),
        Benow: parseFloat(document.getElementById("cfg-fin-benow").value),
      },
      dmi_contest_brand_targets: {
        SAMSUNG: 72, VIVO: 72, OPPO: 72 // constants as per Slide
      },
      manpower_plans: {
        BAJAJ: parseInt(document.getElementById("cfg-fin-bajaj").value) ? 275 : 275, // fallbacks
        HDB: 180,
        IDFC: 120,
        TVS: 35,
        PFL: 29
      }
    };
    
    fetch("/api/targets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    })
      .then(res => res.json())
      .then(res => {
        if (res.status === "success") {
          globalTargets = config;
          showToast("Global targets configured successfully!", "success");
          // Re-load data to merge settings
          loadData(activeDate);
        } else {
          showToast(res.message, "error");
        }
      })
      .catch(err => {
        showToast("Error saving targets config", "error");
      });
  });
}

// Helpers
function formatNumber(val, type) {
  if (val === null || val === undefined || isNaN(val)) return "-";
  
  if (type === "percent") {
    return `${Number(val).toFixed(1)}%`;
  }
  
  if (type === "currency") {
    // Format Rs with grouping
    return val.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  
  if (type === "decimal") {
    return val.toFixed(2);
  }
  
  if (type === "integer") {
    return Math.round(val).toLocaleString("en-IN");
  }
  
  return val.toString();
}

function showToast(message, type = "success") {
  const toast = document.getElementById("toastNotification");
  const text = document.getElementById("toastText");
  const icon = document.getElementById("toastIcon");
  
  text.innerText = message;
  toast.className = `toast active toast-${type}`;
  
  if (type === "success") {
    icon.innerHTML = `<i data-lucide="check-circle" style="color:var(--color-success)"></i>`;
  } else if (type === "error") {
    icon.innerHTML = `<i data-lucide="x-circle" style="color:var(--color-danger)"></i>`;
  } else {
    icon.innerHTML = `<i data-lucide="info" style="color:var(--color-info)"></i>`;
  }
  
  lucide.createIcons();
  
  setTimeout(() => {
    toast.classList.remove("active");
  }, 4000);
}

// -------------------------------------------------------------
// Data Cleaner Module
// -------------------------------------------------------------
let cleanerRawData = [];
let cleanerHeaders = [];
let cleanedSummaryData = {
  sourcing: {},
  dmi: { SAMSUNG: 0, VIVO: 0, OPPO: 0 },
  qty: {},
  val: {}
};

const FINANCIER_CLEAN_MAP = {
  "BAJAJ": "BAJAJ",
  "BFL": "BAJAJ",
  "BAJAJ FINANCE": "BAJAJ",
  "BAJAJ FINANCE LTD": "BAJAJ",
  "BAJAJ CD": "BAJAJ",
  "BAJAJ DPF": "BAJAJ",
  "HDB": "HDB",
  "HDB FINANCE": "HDB",
  "HDBFS": "HDB",
  "IDFC": "IDFC",
  "IDFC FIRST": "IDFC",
  "IDFC FIRST BANK": "IDFC",
  "TVS": "TVS",
  "TVS CREDIT": "TVS",
  "TVS CREDIT SERVICES": "TVS",
  "DMI": "DMI",
  "DMI FINANCE": "DMI",
  "PFL": "PFL",
  "POONAWALLA": "PFL",
  "POONAWALLA FINCORP": "PFL",
  "PINE LAB": "PineLab",
  "PINELAB": "PineLab",
  "PINE LABS": "PineLab",
  "PINELAB EMI": "PineLab",
  "PAYTM": "PAYTM",
  "PAYTM EMI": "PAYTM",
  "BENOW": "Benow",
  "CASH": "aCASH",
  "ACASH": "aCASH"
};

function initCleanerPage() {
  const zone = document.getElementById("cleanerDragZone");
  if (zone) {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.style.borderColor = "var(--accent-cyan)";
      zone.style.background = "rgba(0, 242, 254, 0.05)";
    });

    zone.addEventListener("dragleave", () => {
      zone.style.borderColor = "var(--panel-border)";
      zone.style.background = "rgba(255, 255, 255, 0.01)";
    });

    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.style.borderColor = "var(--panel-border)";
      zone.style.background = "rgba(255, 255, 255, 0.01)";

      const file = e.dataTransfer.files[0];
      if (file && file.name.endsWith(".csv")) {
        const reader = new FileReader();
        reader.onload = (evt) => {
          handleCleanerCSV(evt.target.result);
        };
        reader.readAsText(file);
      } else {
        showToast("Please upload a valid CSV file", "error");
      }
    });
  }

  // Combined Sales Report cleaner pipeline
  // ── Pine Labs EMI Upload ──────────────────────────────────────────────────
  (function initPineLab() {
    const zone      = document.getElementById('pinelabDragZone');
    const fileInput = document.getElementById('pinelabFileInput');
    if (!zone || !fileInput) return;

    // Helper: show progress bar with animated stages
    function showProgress(text) {
      document.getElementById('pinelabProgress').style.display = 'block';
      document.getElementById('pinelabResult').style.display   = 'none';
      document.getElementById('pinelabError').style.display    = 'none';
      const zonesWrap = document.getElementById('pinelabUploadZones'); if (zonesWrap) zonesWrap.style.opacity = '0.5';
      const pb = document.getElementById('pinelabProgressBar');
      const pt = document.getElementById('pinelabProgressText');
      pt.textContent = text || 'Processing Pine Labs data…';
      pb.style.width = '15%';
      // Staged animation
      const stages = [
        {w: '30%', t: 'Applying cleaning rules (Status = Success, Type = Sale)…'},
        {w: '55%', t: 'Deduplicating on Transaction ID…'},
        {w: '75%', t: 'Matching IMEIs to invoices in ClickHouse…'},
        {w: '90%', t: 'Updating dashboard data…'},
      ];
      let i = 0;
      const interval = setInterval(() => {
        if (i < stages.length) {
          pb.style.width  = stages[i].w;
          pt.textContent  = stages[i].t;
          i++;
        } else {
          clearInterval(interval);
        }
      }, 900);
      return interval;
    }

    function finishProgress(interval) {
      clearInterval(interval);
      document.getElementById('pinelabProgressBar').style.width = '100%';
      document.getElementById('pinelabProgressText').textContent = 'Done!';
      setTimeout(() => {
        document.getElementById('pinelabProgress').style.display = 'none';
        const zw = document.getElementById('pinelabUploadZones'); if (zw) zw.style.opacity = '1';
      }, 400);
    }

    // Helper: show results panel
    function showResult(data) {
      const fmt = (n) => Number(n || 0).toLocaleString('en-IN');
      document.getElementById('pinelabResult').style.display = 'block';
      const mainBadge = document.getElementById('pinelabMainStatusBadge');
      const angBadge  = document.getElementById('pinelabAngamalyStatusBadge');
      if (mainBadge) {
        if (data.main_uploaded && data.main_stats) {
          mainBadge.textContent = '✓ Main Uploaded (' + fmt(data.main_stats.after_dedup) + ' dedup rows)';
          mainBadge.style.background = 'rgba(16,185,129,0.18)';
          mainBadge.style.color = '#34d399';
        } else {
          mainBadge.textContent = '1. Main PineLab File (All Stores)';
          mainBadge.style.background = 'rgba(245,158,11,0.12)';
          mainBadge.style.color = '#f59e0b';
        }
      }
      if (angBadge) {
        if (data.angamaly_uploaded && data.angamaly_stats) {
          angBadge.textContent = '✓ Angamaly Uploaded (' + fmt(data.angamaly_stats.after_dedup) + ' rows · ANGAMALY)';
          angBadge.style.background = 'rgba(16,185,129,0.22)';
          angBadge.style.color = '#34d399';
        } else {
          angBadge.textContent = '2. Angamaly Store Only (NOT Angamaly Future)';
          angBadge.style.background = 'rgba(16,185,129,0.12)';
          angBadge.style.color = '#10b981';
        }
      }
      const titleEl = document.getElementById('pinelabResultTitle');
      if (titleEl) {
        if (data.main_uploaded && data.angamaly_uploaded) {
          titleEl.textContent = 'Pine Labs EMI Uploaded (Main + Angamaly Store Combined)';
        } else if (data.angamaly_uploaded) {
          titleEl.textContent = 'Pine Labs EMI Uploaded (Angamaly Store — Upload Main File Above)';
        } else {
          titleEl.textContent = 'Pine Labs EMI Uploaded (Main File — Upload Angamaly File Above)';
        }
      }
      document.getElementById('pinelabResultMsg').textContent = data.message || '';
      document.getElementById('statRawRows').textContent     = fmt(data.raw_rows);
      document.getElementById('statAfterFilter').textContent = fmt(data.after_filter);
      document.getElementById('statAfterDedup').textContent  = fmt(data.after_dedup);
      document.getElementById('statMatchedInv').textContent  = fmt(data.matched_invoices);
      document.getElementById('statInserted').textContent    = fmt(data.inserted_records);
      // Period badge
      if (data.date_from && data.date_to) {
        document.getElementById('pinelabPeriodBadge').textContent =
          data.date_from + ' → ' + data.date_to;
      }
      lucide.createIcons();
    }

    function showError(msg) {
      const el = document.getElementById('pinelabError');
      el.style.display = 'block';
      el.innerHTML = '⚠️ ' + msg;
      document.getElementById('pinelabProgress').style.display = 'none';
      zone.style.display = 'block';
    }

    // Core upload function
    function uploadPineLab(file, fileType) {
      const fname = file.name.toLowerCase();
      if (!fname.endsWith('.csv') && !fname.endsWith('.zip')) {
        showToast('Please upload a .csv or .zip file', 'error');
        return;
      }
      const interval = showProgress('Uploading ' + file.name + '…');
      const fd = new FormData();
      fd.append('file', file);
      if (typeof fileType !== 'undefined') fd.append('file_type', fileType || 'main');
      fetch('/api/upload-pinelab', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
          finishProgress(interval);
          if (data.status === 'success' || data.status === 'warning') {
            showResult(data); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            // Step 2 (trim by date) is no longer shown: the dashboard date picker filters by transaction date.
            showToast(data.status === 'success'
              ? '✅ Pine Lab EMI data uploaded to dashboard'
              : '⚠️ ' + data.message, data.status === 'success' ? 'success' : 'warning');
          } else {
            showError(data.message || 'Upload failed.');
            showToast('Pine Lab upload failed: ' + (data.message || ''), 'error');
          }
        })
        .catch(err => {
          finishProgress(interval);
          showError('Network error: ' + err.message);
          showToast('Upload error: ' + err.message, 'error');
        });
    }

    // Drag & drop
    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.style.borderColor = '#f59e0b';
      zone.style.background  = 'rgba(245,158,11,0.07)';
    });
    zone.addEventListener('dragleave', () => {
      zone.style.borderColor = 'rgba(245,158,11,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
    });
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.style.borderColor = 'rgba(245,158,11,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
      const file = e.dataTransfer.files[0];
      if (file) uploadPineLab(file, 'main');
    });

    // File picker
    fileInput.addEventListener('change', () => {
      if (fileInput.files[0]) uploadPineLab(fileInput.files[0], 'main');
      fileInput.value = '';
    });

    // Angamaly PineLab Drag & Drop + File Picker
    const angZone = document.getElementById('pinelabAngamalyDragZone');
    const angInput = document.getElementById('pinelabAngamalyFileInput');
    if (angZone && angInput) {
      angZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        angZone.style.borderColor = '#10b981';
        angZone.style.background  = 'rgba(16,185,129,0.08)';
      });
      angZone.addEventListener('dragleave', () => {
        angZone.style.borderColor = 'rgba(16,185,129,0.45)';
        angZone.style.background  = 'rgba(255,255,255,0.01)';
      });
      angZone.addEventListener('drop', (e) => {
        e.preventDefault();
        angZone.style.borderColor = 'rgba(16,185,129,0.45)';
        angZone.style.background  = 'rgba(255,255,255,0.01)';
        const file = e.dataTransfer.files[0];
        if (file) uploadPineLab(file, 'angamaly');
      });
      angInput.addEventListener('change', () => {
        if (angInput.files[0]) uploadPineLab(angInput.files[0], 'angamaly');
        angInput.value = '';
      });
    }

    const btnReset = document.getElementById('btnPinelabReset');
    if (btnReset) {
      btnReset.addEventListener('click', () => {
        fetch('/api/reset-pinelab', { method: 'POST' })
          .then(r => r.json())
          .then(() => {
            document.getElementById('pinelabResult').style.display = 'none';
            document.getElementById('pinelabError').style.display  = 'none';
            showResult({ main_uploaded: false, angamaly_uploaded: false });
            document.getElementById('pinelabResult').style.display = 'none';
            if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            showToast('Cleared Pine Labs uploads', 'info');
          });
      });
    }

    // Restore PineLab upload status on page load if already uploaded in this session
    fetch('/api/pinelab-status')
      .then(r => r.json())
      .then(st => {
        if (st && st.uploaded && st.combined) {
          showResult({
            ...st.combined,
            main_uploaded: st.main_uploaded,
            angamaly_uploaded: st.angamaly_uploaded,
            main_stats: st.main_stats,
            angamaly_stats: st.angamaly_stats,
            message: 'Pine Lab EMI (' + (st.main_uploaded && st.angamaly_uploaded ? 'Main + Angamaly combined' : (st.angamaly_uploaded ? 'Angamaly Store' : 'Main Report')) + ') active. ' + (st.combined.inserted_records || 0) + ' invoice records updated.'
          });
        }
      })
      .catch(() => {});

    // "Upload Another" resets the UI
    const btnAnother = document.getElementById('btnPinelabUploadAnother');
    if (btnAnother) {
      btnAnother.addEventListener('click', () => {
        document.getElementById('pinelabResult').style.display = 'none';
        document.getElementById('pinelabError').style.display  = 'none';
        const step2 = document.getElementById('pinelabStep2');
        if (step2) step2.style.display = 'none';
        zone.style.display = 'block';
      });
    }
    // ── Step 2: Date filter ───────────────────────────────────────────────
    (function initPineLabDateFilter() {
      const btn      = document.getElementById('btnPinelabApplyDate');
      if (!btn) return;

      btn.addEventListener('click', () => {
        const dateFrom = document.getElementById('pinelabDateFrom').value;
        const dateTo   = document.getElementById('pinelabDateTo').value;

        if (!dateFrom || !dateTo) {
          showToast('Please select both From and To dates', 'error'); return;
        }
        if (dateFrom > dateTo) {
          showToast('From Date must be before To Date', 'error'); return;
        }

        const progress = document.getElementById('pinelabDateProgress');
        const bar      = document.getElementById('pinelabDateBar');
        const result   = document.getElementById('pinelabDateResult');
        const errEl    = document.getElementById('pinelabDateError');
        result.style.display = 'none';
        errEl.style.display  = 'none';
        progress.style.display = 'block';
        btn.disabled = true;

        let pct = 0;
        const ticker = setInterval(() => { pct = Math.min(pct + 3, 85); bar.style.width = pct + '%'; }, 150);

        fetch('/api/filter-pinelab-by-date', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_from: dateFrom, date_to: dateTo })
        })
          .then(r => r.json())
          .then(data => {
            clearInterval(ticker);
            bar.style.width = '100%';
            setTimeout(() => { progress.style.display = 'none'; bar.style.width = '0%'; btn.disabled = false; }, 600);
            if (data.status === 'success') {
              result.style.display = 'block';
              result.innerHTML = '✅ ' + data.message +
                '<br><span style="opacity:0.7;">Transactions: ' + data.filtered_rows +
                ' &nbsp;|&nbsp; IMEIs updated: ' + data.imeis_inserted + '</span>';
              showToast('✅ Dashboard updated for ' + dateFrom + ' → ' + dateTo, 'success'); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            } else {
              errEl.style.display = 'block';
              errEl.textContent = '⚠️ ' + (data.message || 'Filter failed.');
              showToast('Error: ' + (data.message || ''), 'error');
            }
          })
          .catch(err => {
            clearInterval(ticker);
            progress.style.display = 'none';
            btn.disabled = false;
            errEl.style.display = 'block';
            errEl.textContent = '⚠️ Network error: ' + err.message;
          });
      });
    })();

  })();

  // ── Paytm EMI Upload ──────────────────────────────────────────────────────
  (function initPaytm() {
    const zone      = document.getElementById('paytmDragZone');
    const fileInput = document.getElementById('paytmFileInput');
    if (!zone || !fileInput) return;
    const $ = (id) => document.getElementById(id);
    const fmt = (n) => Number(n || 0).toLocaleString('en-IN');

    function showProgress(text) {
      $('paytmProgress').style.display = 'block';
      $('paytmResult').style.display   = 'none';
      $('paytmError').style.display    = 'none';
      const zonesWrap = document.getElementById('pinelabUploadZones'); if (zonesWrap) zonesWrap.style.opacity = '0.5';
      const pb = $('paytmProgressBar');
      const pt = $('paytmProgressText');
      pt.textContent = text || 'Processing Paytm data…';
      pb.style.width = '15%';
      const stages = [
        {w: '30%', t: 'Removing Other Mode & amounts below 1000…'},
        {w: '55%', t: 'Deduplicating on Transaction ID…'},
        {w: '75%', t: 'Matching to invoices in ClickHouse…'},
        {w: '90%', t: 'Updating dashboard data…'},
      ];
      let i = 0;
      const interval = setInterval(() => {
        if (i < stages.length) { pb.style.width = stages[i].w; pt.textContent = stages[i].t; i++; }
        else clearInterval(interval);
      }, 900);
      return interval;
    }

    function finishProgress(interval) {
      clearInterval(interval);
      $('paytmProgressBar').style.width = '100%';
      $('paytmProgressText').textContent = 'Done!';
      setTimeout(() => { $('paytmProgress').style.display = 'none'; }, 400);
    }

    function showResult(data) {
      $('paytmResult').style.display = 'block';
      $('paytmResultMsg').textContent = data.message || '';
      $('paytmStatRaw').textContent      = fmt(data.raw_rows);
      $('paytmStatFilter').textContent   = fmt(data.after_filter);
      $('paytmStatDedup').textContent    = fmt(data.after_dedup);
      $('paytmStatMatched').textContent  = fmt(data.matched_invoices);
      $('paytmStatInserted').textContent = fmt(data.inserted_records);
      if (data.date_from && data.date_to) {
        $('paytmPeriodBadge').textContent = data.date_from + ' → ' + data.date_to;
        $('paytmDateFrom').value = data.date_from;
        $('paytmDateTo').value   = data.date_to;
      }
      lucide.createIcons();
    }

    function showError(msg) {
      const el = $('paytmError');
      el.style.display = 'block';
      el.textContent = '⚠️ ' + msg;
      $('paytmProgress').style.display = 'none';
      zone.style.display = 'block';
    }

    function uploadPaytm(file) {
      const fname = file.name.toLowerCase();
      if (!/\.(csv|xlsx|xls|zip)$/.test(fname)) {
        showToast('Please upload a .csv, .xlsx or .zip file', 'error');
        return;
      }
      const interval = showProgress('Uploading ' + file.name + '…');
      const fd = new FormData();
      fd.append('file', file);
      if (typeof fileType !== 'undefined') fd.append('file_type', fileType || 'main');
      fetch('/api/upload-paytm', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
          finishProgress(interval);
          if (data.status === 'success' || data.status === 'warning') {
            showResult(data); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            // Step 2 (trim by date) is no longer shown: the dashboard date picker filters by transaction date.
            showToast(data.status === 'success'
              ? '✅ Paytm EMI data uploaded to dashboard'
              : '⚠️ ' + data.message, data.status === 'success' ? 'success' : 'warning');
          } else {
            showError(data.message || 'Upload failed.');
            showToast('Paytm upload failed: ' + (data.message || ''), 'error');
          }
        })
        .catch(err => {
          finishProgress(interval);
          showError('Network error: ' + err.message);
          showToast('Upload error: ' + err.message, 'error');
        });
    }

    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.style.borderColor = '#00baf2';
      zone.style.background  = 'rgba(0,186,242,0.07)';
    });
    zone.addEventListener('dragleave', () => {
      zone.style.borderColor = 'rgba(0,186,242,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
    });
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.style.borderColor = 'rgba(0,186,242,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
      const file = e.dataTransfer.files[0];
      if (file) uploadPaytm(file);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files[0]) uploadPaytm(fileInput.files[0]);
      fileInput.value = '';
    });

    const btnAnother = $('btnPaytmUploadAnother');
    if (btnAnother) {
      btnAnother.addEventListener('click', () => {
        $('paytmResult').style.display = 'none';
        $('paytmError').style.display  = 'none';
        const step2 = $('paytmStep2');
        if (step2) step2.style.display = 'none';
        zone.style.display = 'block';
      });
    }

    // ── Step 2: Date filter ───────────────────────────────────────────────
    const btn = $('btnPaytmApplyDate');
    if (btn) {
      btn.addEventListener('click', () => {
        const dateFrom = $('paytmDateFrom').value;
        const dateTo   = $('paytmDateTo').value;
        if (!dateFrom || !dateTo) { showToast('Please select both From and To dates', 'error'); return; }
        if (dateFrom > dateTo)    { showToast('From Date must be before To Date', 'error'); return; }

        const progress = $('paytmDateProgress');
        const bar      = $('paytmDateBar');
        const result   = $('paytmDateResult');
        const errEl    = $('paytmDateError');
        result.style.display = 'none';
        errEl.style.display  = 'none';
        progress.style.display = 'block';
        btn.disabled = true;

        let pct = 0;
        const ticker = setInterval(() => { pct = Math.min(pct + 3, 85); bar.style.width = pct + '%'; }, 150);

        fetch('/api/filter-paytm-by-date', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_from: dateFrom, date_to: dateTo })
        })
          .then(r => r.json())
          .then(data => {
            clearInterval(ticker);
            bar.style.width = '100%';
            setTimeout(() => { progress.style.display = 'none'; bar.style.width = '0%'; btn.disabled = false; }, 600);
            if (data.status === 'success') {
              result.style.display = 'block';
              result.innerHTML = '✅ ' + data.message +
                '<br><span style="opacity:0.7;">Transactions: ' + data.filtered_rows +
                ' &nbsp;|&nbsp; Records updated: ' + data.imeis_inserted + '</span>';
              showToast('✅ Dashboard updated for ' + dateFrom + ' → ' + dateTo, 'success'); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            } else {
              errEl.style.display = 'block';
              errEl.textContent = '⚠️ ' + (data.message || 'Filter failed.');
              showToast('Error: ' + (data.message || ''), 'error');
            }
          })
          .catch(err => {
            clearInterval(ticker);
            progress.style.display = 'none';
            btn.disabled = false;
            errEl.style.display = 'block';
            errEl.textContent = '⚠️ Network error: ' + err.message;
          });
      });
    }
  })();

// ── Benow EMI Upload ──────────────────────────────────────────────────────
  (function initBenow() {
    const zone      = document.getElementById('benowDragZone');
    const fileInput = document.getElementById('benowFileInput');
    if (!zone || !fileInput) return;
    const $ = (id) => document.getElementById(id);
    const fmt = (n) => Number(n || 0).toLocaleString('en-IN');

    function showProgress(text) {
      $('benowProgress').style.display = 'block';
      $('benowResult').style.display   = 'none';
      $('benowError').style.display    = 'none';
      const zonesWrap = document.getElementById('pinelabUploadZones'); if (zonesWrap) zonesWrap.style.opacity = '0.5';
      const pb = $('benowProgressBar');
      const pt = $('benowProgressText');
      pt.textContent = text || 'Processing Benow data…';
      pb.style.width = '15%';
      const stages = [
        {w: '30%', t: 'Removing Other Mode & amounts below 1000…'},
        {w: '55%', t: 'Deduplicating on Transaction ID…'},
        {w: '75%', t: 'Matching to invoices in ClickHouse…'},
        {w: '90%', t: 'Updating dashboard data…'},
      ];
      let i = 0;
      const interval = setInterval(() => {
        if (i < stages.length) { pb.style.width = stages[i].w; pt.textContent = stages[i].t; i++; }
        else clearInterval(interval);
      }, 900);
      return interval;
    }

    function finishProgress(interval) {
      clearInterval(interval);
      $('benowProgressBar').style.width = '100%';
      $('benowProgressText').textContent = 'Done!';
      setTimeout(() => { $('benowProgress').style.display = 'none'; }, 400);
    }

    function showResult(data) {
      $('benowResult').style.display = 'block';
      $('benowResultMsg').textContent = data.message || '';
      $('benowStatRaw').textContent      = fmt(data.raw_rows);
      $('benowStatFilter').textContent   = fmt(data.after_filter);
      $('benowStatDedup').textContent    = fmt(data.after_dedup);
      $('benowStatMatched').textContent  = fmt(data.matched_invoices);
      $('benowStatInserted').textContent = fmt(data.inserted_records);
      if (data.date_from && data.date_to) {
        $('benowPeriodBadge').textContent = data.date_from + ' → ' + data.date_to;
        $('benowDateFrom').value = data.date_from;
        $('benowDateTo').value   = data.date_to;
      }
      lucide.createIcons();
    }

    function showError(msg) {
      const el = $('benowError');
      el.style.display = 'block';
      el.textContent = '⚠️ ' + msg;
      $('benowProgress').style.display = 'none';
      zone.style.display = 'block';
    }

    function uploadBenow(file) {
      const fname = file.name.toLowerCase();
      if (!/\.(csv|xlsx|xls|zip)$/.test(fname)) {
        showToast('Please upload a .csv, .xlsx or .zip file', 'error');
        return;
      }
      const interval = showProgress('Uploading ' + file.name + '…');
      const fd = new FormData();
      fd.append('file', file);
      if (typeof fileType !== 'undefined') fd.append('file_type', fileType || 'main');
      fetch('/api/upload-benow', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
          finishProgress(interval);
          if (data.status === 'success' || data.status === 'warning') {
            showResult(data); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            // Step 2 (trim by date) is no longer shown: the dashboard date picker filters by transaction date.
            showToast(data.status === 'success'
              ? '✅ Benow EMI data uploaded to dashboard'
              : '⚠️ ' + data.message, data.status === 'success' ? 'success' : 'warning');
          } else {
            showError(data.message || 'Upload failed.');
            showToast('Benow upload failed: ' + (data.message || ''), 'error');
          }
        })
        .catch(err => {
          finishProgress(interval);
          showError('Network error: ' + err.message);
          showToast('Upload error: ' + err.message, 'error');
        });
    }

    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.style.borderColor = '#f2a900';
      zone.style.background  = 'rgba(0,186,242,0.07)';
    });
    zone.addEventListener('dragleave', () => {
      zone.style.borderColor = 'rgba(0,186,242,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
    });
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.style.borderColor = 'rgba(0,186,242,0.4)';
      zone.style.background  = 'rgba(255,255,255,0.01)';
      const file = e.dataTransfer.files[0];
      if (file) uploadBenow(file);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files[0]) uploadBenow(fileInput.files[0]);
      fileInput.value = '';
    });

    const btnAnother = $('btnBenowUploadAnother');
    if (btnAnother) {
      btnAnother.addEventListener('click', () => {
        $('benowResult').style.display = 'none';
        $('benowError').style.display  = 'none';
        const step2 = $('benowStep2');
        if (step2) step2.style.display = 'none';
        zone.style.display = 'block';
      });
    }

    // ── Step 2: Date filter ───────────────────────────────────────────────
    const btn = $('btnBenowApplyDate');
    if (btn) {
      btn.addEventListener('click', () => {
        const dateFrom = $('benowDateFrom').value;
        const dateTo   = $('benowDateTo').value;
        if (!dateFrom || !dateTo) { showToast('Please select both From and To dates', 'error'); return; }
        if (dateFrom > dateTo)    { showToast('From Date must be before To Date', 'error'); return; }

        const progress = $('benowDateProgress');
        const bar      = $('benowDateBar');
        const result   = $('benowDateResult');
        const errEl    = $('benowDateError');
        result.style.display = 'none';
        errEl.style.display  = 'none';
        progress.style.display = 'block';
        btn.disabled = true;

        let pct = 0;
        const ticker = setInterval(() => { pct = Math.min(pct + 3, 85); bar.style.width = pct + '%'; }, 150);

        fetch('/api/filter-benow-by-date', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_from: dateFrom, date_to: dateTo })
        })
          .then(r => r.json())
          .then(data => {
            clearInterval(ticker);
            bar.style.width = '100%';
            setTimeout(() => { progress.style.display = 'none'; bar.style.width = '0%'; btn.disabled = false; }, 600);
            if (data.status === 'success') {
              result.style.display = 'block';
              result.innerHTML = '✅ ' + data.message +
                '<br><span style="opacity:0.7;">Transactions: ' + data.filtered_rows +
                ' &nbsp;|&nbsp; Records updated: ' + data.imeis_inserted + '</span>';
              showToast('✅ Dashboard updated for ' + dateFrom + ' → ' + dateTo, 'success'); if (window.refreshDashboardAfterEmi) window.refreshDashboardAfterEmi();
            } else {
              errEl.style.display = 'block';
              errEl.textContent = '⚠️ ' + (data.message || 'Filter failed.');
              showToast('Error: ' + (data.message || ''), 'error');
            }
          })
          .catch(err => {
            clearInterval(ticker);
            progress.style.display = 'none';
            btn.disabled = false;
            errEl.style.display = 'block';
            errEl.textContent = '⚠️ Network error: ' + err.message;
          });
      });
    }
  })();


    const salesZone = document.getElementById("salesReportDragZone");
  if (salesZone) {
    salesZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      salesZone.style.borderColor = "var(--accent-blue)";
      salesZone.style.background = "rgba(79, 172, 254, 0.05)";
    });

    salesZone.addEventListener("dragleave", () => {
      salesZone.style.borderColor = "rgba(79, 172, 254, 0.4)";
      salesZone.style.background = "rgba(255, 255, 255, 0.01)";
    });

    salesZone.addEventListener("drop", (e) => {
      e.preventDefault();
      salesZone.style.borderColor = "rgba(79, 172, 254, 0.4)";
      salesZone.style.background = "rgba(255, 255, 255, 0.01)";

      const file = e.dataTransfer.files[0];
      if (file && (file.name.endsWith(".xlsx") || file.name.endsWith(".xls"))) {
        uploadSalesReportToClean(file);
      } else {
        showToast("Please upload a valid Excel file (.xlsx)", "error");
      }
    });
  }

  const btnDownloadSales = document.getElementById("btnDownloadCleanedSales");
  if (btnDownloadSales) {
    btnDownloadSales.addEventListener("click", () => {
      window.location.href = "/api/download-cleaned";
      showToast("Downloading cleaned sales report...", "success");
    });
  }

  // Export Finance Detailed Report button
  const btnExportFin = document.getElementById("btnExportFinanceReport");
  if (btnExportFin) {
    btnExportFin.addEventListener("click", () => {
      const statusEl = document.getElementById("financeReportStatus");
      const dlRow    = document.getElementById("financeReportDownloadRow");

      // Show loading state
      btnExportFin.disabled = true;
      btnExportFin.innerHTML = '<i data-lucide="loader"></i> Generating...';
      lucide.createIcons();

      statusEl.style.display = "block";
      statusEl.style.background = "rgba(39,174,96,0.08)";
      statusEl.style.color = "#27ae60";
      statusEl.style.border = "1px solid #27ae60";
      statusEl.innerText = "⏳ Generating Finance Detailed Report (7 sheets)… please wait.";
      dlRow.style.display = "none";

      fetch("/api/export-finance-report", { method: "POST" })
        .then(r => r.json())
        .then(data => {
          btnExportFin.disabled = false;
          btnExportFin.innerHTML = '<i data-lucide="bar-chart-2"></i> Export Finance Report';
          lucide.createIcons();

          if (data.status === "success") {
            statusEl.style.background = "rgba(39,174,96,0.08)";
            statusEl.style.color = "#27ae60";
            statusEl.style.border = "1px solid #27ae60";
            statusEl.innerText = "✅ Finance Detailed Report generated successfully! Click below to download.";
            dlRow.style.display = "block";
            lucide.createIcons();
            showToast("Finance Detailed Report ready!", "success");
          } else {
            statusEl.style.background = "rgba(231,76,60,0.08)";
            statusEl.style.color = "#e74c3c";
            statusEl.style.border = "1px solid #e74c3c";
            statusEl.innerText = "❌ " + (data.message || "Failed to generate report.");
            showToast(data.message || "Failed to generate Finance Report", "error");
          }
        })
        .catch(err => {
          btnExportFin.disabled = false;
          btnExportFin.innerHTML = '<i data-lucide="bar-chart-2"></i> Export Finance Report';
          lucide.createIcons();
          statusEl.style.background = "rgba(231,76,60,0.08)";
          statusEl.style.color = "#e74c3c";
          statusEl.style.border = "1px solid #e74c3c";
          statusEl.innerText = "❌ Network error. Please try again.";
          console.error(err);
          showToast("Error contacting server", "error");
        });
    });
  }

  // Import directly from Excel Aggregation
  const btnImportExcel = document.getElementById("btnImportExcelToReview");
  if (btnImportExcel) {
    btnImportExcel.addEventListener("click", () => {
      btnImportExcel.disabled = true;
      const originalText = btnImportExcel.innerHTML;
      btnImportExcel.innerHTML = '<i data-lucide="loader"></i> Importing...';
      lucide.createIcons();
      
      fetch("/api/get-cleaned-aggregation?_=" + new Date().getTime())
        .then(res => res.json())
        .then(res => {
          btnImportExcel.disabled = false;
          btnImportExcel.innerHTML = originalText;
          lucide.createIcons();
          
          if (res.status === "success") {
            // Populate the cleaner summary data state with our backend calculation
            cleanedSummaryData = {
              sourcing: res.data.sourcing,
              dmi: res.data.dmi,
              qty: res.data.qty,
              val: res.data.val,
              products_qty: res.data.products_qty,
              products_val: res.data.products_val,
              brands_performance: res.data.brands_performance,
              branches: res.data.branches
            };
            
            // Trigger the existing application flow
            applyCleanedDataToActiveReview();
          } else {
            showToast(res.message || "Failed to aggregate Excel data", "error");
          }
        })
        .catch(err => {
          btnImportExcel.disabled = false;
          btnImportExcel.innerHTML = originalText;
          lucide.createIcons();
          showToast("Error fetching aggregated data: " + err.message, "error");
          console.error(err);
        });
    });
  }

  // Import directly from ClickHouse (live DB)
  const btnImportCH = document.getElementById('btnImportClickhouseToReview');
  if (btnImportCH) {
    btnImportCH.addEventListener('click', () => {
      if (!activeDate) { showToast('Please select a review date first.', 'warning'); return; }
      btnImportCH.disabled = true;
      const origHTML = btnImportCH.innerHTML;
      btnImportCH.innerHTML = '<span style="font-size:14px;">⚡</span> Loading from ClickHouse...';

      const timeframe = (document.getElementById('timeframeSelect') || {}).value || 'monthly';
      fetch(`/api/clickhouse/aggregate-review?date=${activeDate}&end_date=&timeframe=${timeframe}&_=${Date.now()}`)
        .then(res => res.json())
        .then(res => {
          btnImportCH.disabled = false;
          btnImportCH.innerHTML = origHTML;
          if (res.status === 'success') {
            cleanedSummaryData = {
              sourcing:          res.data.sourcing,
              dmi:               res.data.dmi,
              qty:               res.data.qty,
              val:               res.data.val,
              products_qty:      res.data.products_qty,
              products_val:      res.data.products_val,
              brands_performance:res.data.brands_performance,
              branches:          res.data.branches,
            };
            applyCleanedDataToActiveReview();
            showToast(`✅ Imported from ClickHouse (${res.date}, ${res.timeframe})`, 'success');
          } else {
            showToast(res.message || 'ClickHouse import failed', 'error');
          }
        })
        .catch(err => {
          btnImportCH.disabled = false;
          btnImportCH.innerHTML = origHTML;
          showToast('Error fetching from ClickHouse: ' + err.message, 'error');
        });
    });
  }

  // Action Buttons
  document.getElementById("btnRunCleaner").addEventListener("click", runCleaningProcess);
  document.getElementById("btnResetCleaner").addEventListener("click", resetCleanerState);
  document.getElementById("btnApplyCleanedAll").addEventListener("click", applyCleanedDataToActiveReview);
}

function recalculateCategoryMath(catData) {
  const cats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"];
  const fins_keys = ["aCASH", "BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow"];
  
  let qtyTotals = { totals: {}, grand_total: 0, paper_total: 0, card_total: 0 };
  fins_keys.forEach(k => qtyTotals.totals[k] = 0);
  
  cats.forEach(cat => {
    const wasDeducted = Boolean(catData.qty && catData.qty[cat] && catData.qty[cat]._emi_deducted);
    let prodsObj = catData.products_qty;
    if (catData.products && catData.products.qty) {
        prodsObj = catData.products.qty;
    }
    if (prodsObj && prodsObj[cat]) {
       const prodQty = prodsObj[cat];
       fins_keys.forEach(k => {
          let sum = 0;
          Object.keys(prodQty).forEach(p => sum += (prodQty[p][k] || 0));
          catData.qty[cat][k] = sum;
       });
       let sumLastWeek = 0;
       let sumApr = 0;
       Object.keys(prodQty).forEach(p => {
          sumLastWeek += (prodQty[p]["last_week_pct"] || 0);
          sumApr += (prodQty[p]["apr_closing_pct"] || 0);
       });
       const numProds = Object.keys(prodQty).length || 1;
       catData.qty[cat]["last_week_pct"] = sumLastWeek / numProds;
       catData.qty[cat]["apr_closing_pct"] = sumApr / numProds;
    }
    const info = catData.qty[cat];
    // Deduct Paytm, Benow, and Pinelab data count from aCASH
    if (wasDeducted) info._emi_deducted = true;
    const cardEmiCount = (info["PineLab"] || 0) + (info["PAYTM"] || 0) + (info["Benow"] || 0);
    if (prodsObj && prodsObj[cat]) {
      if (!info._emi_deducted) info["aCASH"] = Math.max(0, (info["aCASH"] || 0) - cardEmiCount);
      info["_emi_deducted"] = true;
    } else if (!info._emi_deducted) {
      if (!info._emi_deducted) info["aCASH"] = Math.max(0, (info["aCASH"] || 0) - cardEmiCount);
      info["_emi_deducted"] = true;
    }

    let row_tot = 0;
    let paper_tot = 0;
    let card_tot = 0;
    
    fins_keys.forEach((k, idx) => {
      const val = info[k] || 0;
      qtyTotals.totals[k] += val;
      row_tot += val;
      if (k !== "aCASH") {
        if (idx < 7) paper_tot += val;
        else card_tot += val;
      }
    });
    info.total = row_tot;
    info.paper_total = paper_tot;
    info.card_total = card_tot;
    info.fin_share = row_tot > 0 ? ((paper_tot + card_tot) / row_tot * 100) : 0;
    
    qtyTotals.grand_total += row_tot;
    qtyTotals.paper_total += paper_tot;
    qtyTotals.card_total += card_tot;
  });
  catData.qty_totals = qtyTotals;
  
  let valTotals = { totals: {}, grand_total: 0.0, paper_total: 0.0, card_total: 0.0 };
  fins_keys.forEach(k => valTotals.totals[k] = 0.0);
  
  cats.forEach(cat => {
    let prodsValObj = catData.products_val;
    if (catData.products && catData.products.value) {
        prodsValObj = catData.products.value;
    }
    if (prodsValObj && prodsValObj[cat]) {
       const prodVal = prodsValObj[cat];
       fins_keys.forEach(k => {
          let sum = 0.0;
          Object.keys(prodVal).forEach(p => sum += (parseFloat(prodVal[p][k]) || 0.0));
          catData.value[cat][k] = sum;
       });
       let sumLastWeek = 0;
       let sumApr = 0;
       Object.keys(prodVal).forEach(p => {
          sumLastWeek += (prodVal[p]["last_week_pct"] || 0);
          sumApr += (prodVal[p]["apr_closing_pct"] || 0);
       });
       const numProds = Object.keys(prodVal).length || 1;
       catData.value[cat]["last_week_pct"] = sumLastWeek / numProds;
       catData.value[cat]["apr_closing_pct"] = sumApr / numProds;
    }
    const info = catData.value[cat];
    let row_tot = 0.0;
    let paper_tot = 0.0;
    let card_tot = 0.0;
    
    fins_keys.forEach((k, idx) => {
      const val = parseFloat(info[k]) || 0.0;
      valTotals.totals[k] += val;
      row_tot += val;
      if (k !== "aCASH") {
        if (idx < 7) paper_tot += val;
        else card_tot += val;
      }
    });
    info.total = row_tot;
    info.paper_total = paper_tot;
    info.card_total = card_tot;
    info.fin_share = row_tot > 0 ? ((paper_tot + card_tot) / row_tot * 100) : 0;
    
    valTotals.grand_total += row_tot;
    valTotals.paper_total += paper_tot;
    valTotals.card_total += card_tot;
  });
  catData.val_totals = valTotals;
}


function uploadSalesReportToClean(file) {
  showToast("Uploading and cleaning sales report... please wait", "info");
  
  const formData = new FormData();
  formData.append("file", file);
  
  fetch("/api/clean-sales-report", {
    method: "POST",
    body: formData
  })
  .then(res => res.json())
  .then(data => {
    if (data.status === "success") {
      document.getElementById("salesReportResultText").innerText = `Successfully processed and cleaned ${data.count.toLocaleString("en-IN")} records. Ready for download.`;
      document.getElementById("salesReportResult").style.display = "block";
      showToast("Combined sales report cleaned successfully!", "success");
      // Also show the new import button for review by displaying the block containing both buttons
      document.getElementById("financeReportDownloadRow").style.display = "flex";
    } else {
      showToast(data.message || "Failed to clean sales report", "error");
    }
  })
  .catch(err => {
    console.error(err);
    showToast("Error processing file on backend", "error");
  });
}

function handleCleanerCSV(csvText) {
  try {
    const parsed = robustParseCSV(csvText);
    if (parsed.length < 2) {
      showToast("CSV file appears to be empty or has no columns", "error");
      return;
    }

    cleanerHeaders = parsed[0].map(h => h.trim());
    cleanerRawData = parsed.slice(1);

    // Populate drop downs
    populateMapDropdowns();

    // Auto detect columns
    autoDetectColumns();

    document.getElementById("cleanerConfigSection").style.display = "block";
    document.getElementById("cleanerPreviewSection").style.display = "none";
    showToast(`Loaded ${cleanerRawData.length} records. Please review column mappings.`, "info");
  } catch (err) {
    showToast("Error loading CSV file", "error");
    console.error(err);
  }
}

function robustParseCSV(text) {
  let p = '', r = [];
  let q = false;
  let row = [''];
  for (let i = 0; i < text.length; i++) {
    let c = text[i];
    let next = text[i+1];
    if (c === '"') {
      if (q && next === '"') { row[row.length - 1] += '"'; i++; }
      else { q = !q; }
    } else if (c === ',' && !q) {
      row.push('');
    } else if ((c === '\r' || c === '\n') && !q) {
      if (c === '\r' && next === '\n') { i++; }
      r.push(row);
      row = [''];
    } else {
      row[row.length - 1] += c;
    }
  }
  if (row.length > 1 || row[0] !== '') { r.push(row); }
  // filter out completely empty rows
  return r.filter(row => row.some(cell => cell.trim() !== ''));
}

function populateMapDropdowns() {
  const selects = ["map-col-fin", "map-col-branch", "map-col-cat", "map-col-status", "map-col-val", "map-col-brand"];
  
  selects.forEach(id => {
    const el = document.getElementById(id);
    el.innerHTML = "";
    
    // Add default blank option
    const def = document.createElement("option");
    def.value = "-1";
    def.innerText = "-- Select Column --";
    el.appendChild(def);

    cleanerHeaders.forEach((h, idx) => {
      const opt = document.createElement("option");
      opt.value = idx;
      opt.innerText = `${h} (Col ${idx + 1})`;
      el.appendChild(opt);
    });
  });
}

function autoDetectColumns() {
  cleanerHeaders.forEach((h, idx) => {
    const headerLower = h.toLowerCase();
    
    if (headerLower.includes("fin") || headerLower.includes("lender") || headerLower.includes("bank") || headerLower.includes("provider")) {
      document.getElementById("map-col-fin").value = idx;
    } else if (headerLower.includes("branch") || headerLower.includes("store") || headerLower.includes("location") || headerLower.includes("site")) {
      document.getElementById("map-col-branch").value = idx;
    } else if (headerLower.includes("cat") || headerLower.includes("product") || headerLower.includes("item") || headerLower.includes("category")) {
      document.getElementById("map-col-cat").value = idx;
    } else if (headerLower.includes("status") || headerLower.includes("stage") || headerLower.includes("state")) {
      document.getElementById("map-col-status").value = idx;
    } else if (headerLower.includes("value") || headerLower.includes("amount") || headerLower.includes("price") || headerLower.includes("amt")) {
      document.getElementById("map-col-val").value = idx;
    } else if (headerLower.includes("brand") || headerLower.includes("make") || headerLower.includes("manufacturer")) {
      document.getElementById("map-col-brand").value = idx;
    }
  });
}

function runCleaningProcess() {
  const finColIdx = parseInt(document.getElementById("map-col-fin").value);
  const branchColIdx = parseInt(document.getElementById("map-col-branch").value);
  const catColIdx = parseInt(document.getElementById("map-col-cat").value);
  const statusColIdx = parseInt(document.getElementById("map-col-status").value);
  const valColIdx = parseInt(document.getElementById("map-col-val").value);
  const brandColIdx = parseInt(document.getElementById("map-col-brand").value);

  if (finColIdx === -1) {
    showToast("Financier Column is required", "error");
    return;
  }

  // Reset Cleaned State
  cleanedSummaryData = {
    sourcing: {},
    dmi: { SAMSUNG: 0, VIVO: 0, OPPO: 0 },
    qty: {},
    val: {},
    branches: {}
  };

  // Initialize Financiers
  const standardFins = ["BAJAJ", "HDB", "IDFC", "PFL", "TVS", "DMI", "PineLab", "PAYTM", "Benow", "aCASH"];
  standardFins.forEach(f => {
    cleanedSummaryData.sourcing[f] = { sourcing: 0, pending: 0 };
  });
  
  // Initialize Category structures
  const standardCats = ["CD PRODUCTS", "LAPTOP", "MOBILE", "HVA", "SA", "OTHERS", "BANK EMI"];
  standardCats.forEach(cat => {
    cleanedSummaryData.qty[cat] = {};
    cleanedSummaryData.val[cat] = {};
    standardFins.forEach(f => {
      cleanedSummaryData.qty[cat][f] = 0;
      cleanedSummaryData.val[cat][f] = 0.0;
    });
  });

  // Initialize Branch lists dynamically from target branch list
  const targetBranches = Object.keys(reviewData.dmi_contest.branches);
  targetBranches.forEach(br => {
    cleanedSummaryData.branches[br] = { OPPO: 0, SAMSUNG: 0, VIVO: 0 };
  });

  let processedCount = 0;
  let skippedCount = 0;

  cleanerRawData.forEach(row => {
    // Get fields
    const rawFin = row[finColIdx];
    const stdFin = cleanFinancierName(rawFin);
    
    if (!stdFin) {
      skippedCount++;
      return; // Skip row if lender cannot be recognized
    }

    processedCount++;

    // 1. Accumulate Sourcing Counts
    const rawStatus = statusColIdx !== -1 ? row[statusColIdx] : "";
    const isPending = cleanSourcingStatus(rawStatus);

    cleanedSummaryData.sourcing[stdFin].sourcing++;
    if (isPending) {
      cleanedSummaryData.sourcing[stdFin].pending++;
    }

    // 2. Product Category Sourcing
    const rawCat = catColIdx !== -1 ? row[catColIdx] : "";
    const stdCat = matchProductCategory(rawCat);
    const rawValStr = valColIdx !== -1 ? row[valColIdx] : "0";
    const stdVal = parseFloat(rawValStr.replace(/[^0-9.]/g, "")) || 0.0;

    cleanedSummaryData.qty[stdCat][stdFin]++;
    // Sourcing values are typically entered in Rupees, convert to Crores for Category table
    cleanedSummaryData.val[stdCat][stdFin] += stdVal / 10000000.0; 

    // 3. DMI Contest Branch wise counts
    if (stdFin === "DMI") {
      const rawBranch = branchColIdx !== -1 ? row[branchColIdx] : "";
      const stdBranch = matchStandardBranch(rawBranch);
      
      const rawBrand = brandColIdx !== -1 ? row[brandColIdx] : "";
      const stdBrand = matchContestBrand(rawBrand);
      
      if (stdBranch && stdBrand) {
        cleanedSummaryData.branches[stdBranch][stdBrand]++;
        cleanedSummaryData.dmi[stdBrand]++;
      }
    }
  });

  // Render summaries
  renderCleanerPreview();

  document.getElementById("cleanerPreviewSection").style.display = "block";
  showToast(`Successfully processed ${processedCount} records. Gaps: ${skippedCount} skipped.`, "success");
}

function cleanFinancierName(rawVal) {
  if (!rawVal) return null;
  const name = rawVal.toUpperCase().trim();
  
  for (let key in FINANCIER_CLEAN_MAP) {
    if (name === key || name.includes(key) || key.includes(name)) {
      return FINANCIER_CLEAN_MAP[key];
    }
  }
  return null;
}

function cleanSourcingStatus(rawVal) {
  if (!rawVal) return false;
  const s = rawVal.toLowerCase().trim();
  const pendingKeywords = ["pending", "hold", "transit", "process", "submitted", "unpaid", "wait", "requested", "sourcing"];
  const successKeywords = ["disbursed", "paid", "success", "cleared", "completed", "done"];
  
  for (let kw of pendingKeywords) {
    if (s.includes(kw)) {
      let hasSuccess = false;
      for (let skw of successKeywords) {
        if (s.includes(skw)) hasSuccess = true;
      }
      return !hasSuccess;
    }
  }
  return false;
}

function matchProductCategory(rawVal) {
  if (!rawVal) return "OTHERS";
  const name = rawVal.toLowerCase().trim();
  if (name.includes("mobile") || name.includes("phone")) return "MOBILE";
  if (name.includes("laptop") || name.includes("notebook") || name.includes("computer")) return "LAPTOP";
  if (name.includes("hva") || name.includes("premium") || name.includes("led") || name.includes("tv")) return "HVA";
  if (name.includes("sa") || name.includes("accessory") || name.includes("charger") || name.includes("cable")) return "SA";
  if (name.includes("bank emi") || name.includes("card emi") || name.includes("credit emi")) return "BANK EMI";
  if (name.includes("cd") || name.includes("appliances") || name.includes("washing machine") || name.includes("refrigerator") || name.includes("fridge") || name.includes("ac")) return "CD PRODUCTS";
  return "OTHERS";
}

function matchStandardBranch(rawVal) {
  if (!rawVal) return null;
  let clean = rawVal.toUpperCase().trim().replace(/\s+/g, ' ');
  
  const targetBranches = Object.keys(reviewData.dmi_contest.branches);
  if (targetBranches.includes(clean)) return clean;
  
  for (let br of targetBranches) {
    if (clean.includes(br) || br.includes(clean)) {
      return br;
    }
  }
  return null;
}

function matchContestBrand(rawVal) {
  if (!rawVal) return null;
  const name = rawVal.toUpperCase().trim();
  if (name.includes("SAMSUNG")) return "SAMSUNG";
  if (name.includes("VIVO")) return "VIVO";
  if (name.includes("OPPO")) return "OPPO";
  return null;
}

function renderCleanerPreview() {
  // Sourcing preview table
  const srcBody = document.querySelector("#table-cleaner-sourcing tbody");
  srcBody.innerHTML = "";
  
  Object.keys(cleanedSummaryData.sourcing).forEach(fin => {
    const info = cleanedSummaryData.sourcing[fin];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fin}</td>
      <td class="num-align">${info.sourcing}</td>
      <td class="num-align">${info.pending}</td>
    `;
    srcBody.appendChild(tr);
  });

  // DMI brands preview
  const dmiBody = document.querySelector("#table-cleaner-dmi tbody");
  dmiBody.innerHTML = "";
  
  Object.keys(cleanedSummaryData.dmi).forEach(brand => {
    const count = cleanedSummaryData.dmi[brand];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${brand}</td>
      <td class="num-align">${count}</td>
    `;
    dmiBody.appendChild(tr);
  });
}

function applyCleanedDataToActiveReview() {
  // 1. Apply Sourcing counts
  const disb = reviewData.disbursement.financiers;
  const ft = reviewData.financier_targets;
  
  Object.keys(disb).forEach(fin => {
    if (cleanedSummaryData.sourcing[fin]) {
      disb[fin].sourcing = cleanedSummaryData.sourcing[fin].sourcing;
      disb[fin].pending = cleanedSummaryData.sourcing[fin].pending;
      if (ft[fin]) {
        ft[fin].achievement = cleanedSummaryData.sourcing[fin].sourcing;
      }
    }
  });

  // 2. Apply Category Volume and values
  reviewData.category_performance.qty = cleanedSummaryData.qty;
  reviewData.category_performance.value = cleanedSummaryData.val;

  if (cleanedSummaryData.products_qty && cleanedSummaryData.products_val) {
    reviewData.category_performance.products = {
      qty: cleanedSummaryData.products_qty,
      value: cleanedSummaryData.products_val
    };
  }
  
  if (cleanedSummaryData.brands_performance) {
    reviewData.brand_performance = cleanedSummaryData.brands_performance;
  }

  // 3. Apply DMI contest branches and summary counts
  if (cleanedSummaryData.branches) reviewData.dmi_contest.branches = cleanedSummaryData.branches;
  if (cleanedSummaryData.dmi && reviewData.dmi_contest && reviewData.dmi_contest.summary) {
    Object.keys(cleanedSummaryData.dmi).forEach(brand => {
      if (reviewData.dmi_contest.summary[brand]) {
        reviewData.dmi_contest.summary[brand].achievement = cleanedSummaryData.dmi[brand];
      }
    });
  }

  // Run Calculations and trigger table updates
  recalculateAll();
  populateBrandFilter();
  renderAllTables();
  updateDashboardKPIs();
  
  // Fetch the new timeframe-based dashboard data and KPIs
  const timeframe = document.getElementById("timeframeSelect") ? document.getElementById("timeframeSelect").value : "daily";
  loadDashboardData(activeDate, timeframe);
  
  // Switch to dashboard tab
  document.querySelector('.nav-item[data-tab="dashboard"]').click();
  showToast("Cleaned & Aggregated data applied to current review date!", "success");
}

function resetCleanerState() {
  cleanerRawData = [];
  cleanerHeaders = [];
  document.getElementById("cleanerConfigSection").style.display = "none";
  document.getElementById("cleanerPreviewSection").style.display = "none";
  showToast("Cleaner reset. Upload a new file.", "info");
}

/* ═══════════════════════════════════════════════════════════════════════
   Export Cleaned Data by Date Range
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  const btn    = document.getElementById('btnExportDateRange');
  const status = document.getElementById('exportDateRangeStatus');
  if (!btn) return;

  function setStatus(msg, type) {
    // type: 'info' | 'success' | 'error'
    const colors = {
      info:    { bg: 'rgba(37,99,235,0.12)',  border: 'rgba(59,130,246,0.35)',  color: '#93c5fd' },
      success: { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)', color: '#6ee7b7' },
      error:   { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)',  color: '#fca5a5' },
    };
    const s = colors[type] || colors.info;
    status.style.display      = 'block';
    status.style.background   = s.bg;
    status.style.border       = '1px solid ' + s.border;
    status.style.color        = s.color;
    status.innerHTML          = msg;
  }

  btn.addEventListener('click', function () {
    const startVal  = document.getElementById('exportStartDate').value;
    const endVal    = document.getElementById('exportEndDate').value;
    const sourceVal = document.getElementById('exportSource').value;

    if (!startVal || !endVal) {
      setStatus('⚠️ Please select both <strong>Start Date</strong> and <strong>End Date</strong>.', 'error');
      return;
    }
    if (startVal > endVal) {
      setStatus('⚠️ Start date must be <strong>before or equal</strong> to end date.', 'error');
      return;
    }

    // Show loading state
    btn.disabled   = true;
    btn.innerHTML  = '<i data-lucide="loader-circle" style="width:16px;height:16px;animation:spin 1s linear infinite;"></i> Generating…';
    if (typeof lucide !== 'undefined') lucide.createIcons();
    setStatus('⏳ Preparing your Excel export… this may take a moment for large date ranges.', 'info');

    const url = '/api/export-date-range?start_date=' + encodeURIComponent(startVal)
                + '&end_date='  + encodeURIComponent(endVal)
                + '&source='   + encodeURIComponent(sourceVal);

    // Use XHR to detect errors before triggering download
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.responseType = 'blob';

    xhr.onload = function () {
      btn.disabled  = false;
      btn.innerHTML = '<i data-lucide="file-down" style="width:16px;height:16px;"></i> Export Excel';
      if (typeof lucide !== 'undefined') lucide.createIcons();

      if (xhr.status === 200) {
        // Trigger download
        const blob     = new Blob([xhr.response], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        });
        const dlUrl    = URL.createObjectURL(blob);
        const a        = document.createElement('a');
        const filename = 'CleanedData_' + startVal.replace(/-/g, '')
                         + '_to_' + endVal.replace(/-/g, '') + '.xlsx';
        a.href         = dlUrl;
        a.download     = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(dlUrl);

        setStatus('✅ <strong>' + filename + '</strong> downloaded successfully!', 'success');
      } else {
        // Parse error JSON from blob
        const reader = new FileReader();
        reader.onload = function () {
          try {
            const err = JSON.parse(reader.result);
            setStatus('❌ Export failed: ' + (err.error || 'Unknown error'), 'error');
          } catch (e) {
            setStatus('❌ Export failed (HTTP ' + xhr.status + ')', 'error');
          }
        };
        reader.readAsText(xhr.response);
      }
    };

    xhr.onerror = function () {
      btn.disabled  = false;
      btn.innerHTML = '<i data-lucide="file-down" style="width:16px;height:16px;"></i> Export Excel';
      if (typeof lucide !== 'undefined') lucide.createIcons();
      setStatus('❌ Network error — could not reach the server.', 'error');
    };

    xhr.send();
  });
})();

// ============================================================================
// OCT & OCT Vol SHEETS TAB (Store-wise, TM, RBM, BDM Value & Volume Share)
// ============================================================================
let _octSheetsCacheData = null;
let _activeOctSubSheet = "oct"; // 'oct' (Value ₹) | 'oct_vol' (Volume Qty)

window.switchOctSubSheet = function(subSheet) {
  _activeOctSubSheet = subSheet === "oct_vol" ? "oct_vol" : "oct";
  const btnVal = document.getElementById("btn-oct-sheet-val");
  const btnVol = document.getElementById("btn-oct-sheet-vol");
  if (btnVal && btnVol) {
    if (_activeOctSubSheet === "oct") {
      btnVal.style.cssText = "padding:7px 18px; border-radius:6px; border:none; background:#ffffff; color:#0f7b3f; font-weight:800; font-size:13px; cursor:pointer; box-shadow:0 2px 6px rgba(0,0,0,0.12); display:flex; align-items:center; gap:6px; border-bottom:3px solid #0f7b3f;";
      btnVol.style.cssText = "padding:7px 18px; border-radius:6px; border:none; background:transparent; color:var(--text-muted); font-weight:600; font-size:13px; cursor:pointer; display:flex; align-items:center; gap:6px; border-bottom:3px solid transparent;";
    } else {
      btnVol.style.cssText = "padding:7px 18px; border-radius:6px; border:none; background:#ffffff; color:#0f7b3f; font-weight:800; font-size:13px; cursor:pointer; box-shadow:0 2px 6px rgba(0,0,0,0.12); display:flex; align-items:center; gap:6px; border-bottom:3px solid #0f7b3f;";
      btnVal.style.cssText = "padding:7px 18px; border-radius:6px; border:none; background:transparent; color:var(--text-muted); font-weight:600; font-size:13px; cursor:pointer; display:flex; align-items:center; gap:6px; border-bottom:3px solid transparent;";
    }
  }
  renderOctSheetsUI();
};

window.resetOctSheetFilters = function() {
  const sInput = document.getElementById("oct-store-search");
  const fTm = document.getElementById("oct-filter-tm");
  const fRbm = document.getElementById("oct-filter-rbm");
  const fBdm = document.getElementById("oct-filter-bdm");
  if (sInput) sInput.value = "";
  if (fTm) fTm.value = "ALL";
  if (fRbm) fRbm.value = "ALL";
  if (fBdm) fBdm.value = "ALL";
  renderOctSheetsUI();
};

window.filterOctSheetBy = function(field, val) {
  const elId = field === "tm" ? "oct-filter-tm" : field === "rbm" ? "oct-filter-rbm" : "oct-filter-bdm";
  const sel = document.getElementById(elId);
  if (!sel) return;
  const targetVal = String(val || "").toUpperCase();
  if (sel.value.toUpperCase() === targetVal) {
    sel.value = "ALL";
  } else {
    for (const opt of sel.options) {
      if (opt.value.toUpperCase() === targetVal) {
        sel.value = opt.value;
        break;
      }
    }
  }
  renderOctSheetsUI();
};

function _syncOctWindowModeUI(periodObj, date, timeframe) {
  const isOctMonthly = (timeframe === "monthly" && String(date || "").startsWith("2026-10"));
  const modeWrap = document.getElementById("oct-period-mode-wrap");
  const liveWrap = document.getElementById("oct-live-window-wrap");
  const liveBadge = document.getElementById("oct-live-window-badge");
  if (modeWrap) modeWrap.style.display = isOctMonthly ? "flex" : "none";
  if (liveWrap) liveWrap.style.display = isOctMonthly ? "none" : "flex";
  if (liveBadge && !isOctMonthly) {
    if (periodObj && periodObj.start_date && periodObj.end_date) {
      const fmtD = iso => {
        const dt = new Date(iso.slice(0, 10) + "T00:00:00");
        return isNaN(dt) ? iso : dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
      };
      liveBadge.textContent = `${fmtD(periodObj.start_date)} \u2013 ${fmtD(periodObj.end_date)}`;
    } else {
      liveBadge.textContent = `${timeframe.charAt(0).toUpperCase() + timeframe.slice(1)} (${date})`;
    }
  }
}

window.loadOctSheetsData = function(forceReload) {
  const date = activeDate || "2026-10-01";
  const timeframe = (document.getElementById("timeframeSelect") || {}).value || "monthly";
  const periodMode = (document.getElementById("oct-period-mode") || {}).value || "reference";
  const endDate = _endDate();
  const stateSel = ((document.getElementById("oct-filter-state") || document.getElementById("cat-filter-state") || {}).value || "");
  const url = `/api/oct-sheets-data?date=${encodeURIComponent(date)}&end_date=${encodeURIComponent(endDate)}&timeframe=${encodeURIComponent(timeframe)}&period_mode=${encodeURIComponent(periodMode)}&state=${encodeURIComponent(stateSel)}`;

  _syncOctWindowModeUI(null, date, timeframe);

  if (!forceReload && _octSheetsCacheData && _octSheetsCacheData._url === url) {
    _syncOctWindowModeUI(_octSheetsCacheData.period, date, timeframe);
    renderOctSheetsUI();
    return;
  }

  const tbody = document.getElementById("oct-stores-tbody");
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:30px; color:var(--text-muted);">Loading Store Fin Share data...</td></tr>`;
  }

  _cachedFetch(url)
    .then(data => {
      if (data.error) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:25px; color:var(--color-danger);">${data.error}</td></tr>`;
        return;
      }
      data._url = url;
      _octSheetsCacheData = data;
      _syncOctWindowModeUI(data.period, date, timeframe);
      populateOctFilterDropdowns(data);
      renderOctNotice(data);
      renderOctSheetsUI();
    })
    .catch(err => {
      console.error("Failed to load Store Fin Share data:", err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:25px; color:var(--color-danger);">Failed to load Store Fin Share data</td></tr>`;
    });
};

function populateOctFilterDropdowns(data) {
  if (!data || !data.oct) return;
  const tmSel = document.getElementById("oct-filter-tm");
  const rbmSel = document.getElementById("oct-filter-rbm");
  const bdmSel = document.getElementById("oct-filter-bdm");

  // Rebuild lists on every load (TMs / RBMs / BDMs come from the data of the selected period)
  [[tmSel, "All Fin BDMs"], [rbmSel, "All RBMs"], [bdmSel, "All BDMs"]].forEach(([sel, lbl]) => {
    if (!sel) return;
    sel.dataset.prev = sel.value;
    sel.innerHTML = `<option value="ALL">${lbl}</option>`;
  });
  if (tmSel && tmSel.options.length <= 1) {
    (data.oct.tms || []).forEach(t => {
      const o = document.createElement("option");
      o.value = t.tm;
      o.textContent = t.tm;
      tmSel.appendChild(o);
    });
  }
  if (rbmSel && rbmSel.options.length <= 1) {
    (data.oct.rbms || []).forEach(r => {
      const o = document.createElement("option");
      o.value = r.rbm;
      o.textContent = r.rbm;
      rbmSel.appendChild(o);
    });
  }
  if (bdmSel && bdmSel.options.length <= 1) {
    (data.oct.bdms || []).forEach(b => {
      const o = document.createElement("option");
      o.value = b.bdm;
      o.textContent = b.bdm;
      bdmSel.appendChild(o);
    });
  }
  [tmSel, rbmSel, bdmSel].forEach(sel => {
    if (sel && sel.dataset.prev && [...sel.options].some(o => o.value === sel.dataset.prev)) sel.value = sel.dataset.prev;
  });
}

// ---- Notices (benchmark / card upload status / targets) ----
function renderOctNotice(data) {
  const box = document.getElementById("oct-notice");
  if (!box || !data) return;
  const msgs = [];
  const b = data.benchmark || {};
  if (b.warning) msgs.push(b.warning);
  const cs = data.card_status || {};
  const missing = Object.keys(cs).filter(k => !cs[k].uploaded);
  if (missing.length) {
    msgs.push(`Card data not uploaded for this period: ${missing.join(", ")}. FIN is understated until those files are uploaded.`);
  }
  const t = data.targets || {};
  if (t.month && !t.set) {
    msgs.push(`No TM targets set for ${t.month}. Click "Set Targets" to enter them (Ach %, DRR and Trend need targets).`);
  }
  box.innerHTML = msgs.map(m => "&#9888; " + m).join("<br>");
  box.style.display = msgs.length ? "block" : "none";
}

// ---- TM targets editor ----
window.toggleOctTargetsPanel = function() {
  const panel = document.getElementById("oct-targets-panel");
  if (!panel || !_octSheetsCacheData) return;
  if (panel.style.display !== "none") { panel.style.display = "none"; return; }
  const month = (_octSheetsCacheData.targets || {}).month || "";
  const title = document.getElementById("oct-targets-title");
  if (title) title.textContent = `TM Targets \u2014 ${month}`;
  fetch(`/api/tm-targets?month=${encodeURIComponent(month)}`)
    .then(r => r.json())
    .then(cur => {
      const body = document.getElementById("oct-targets-body");
      body.innerHTML = "";
      (_octSheetsCacheData.oct.tms || []).forEach(t => {
        const c = (cur && cur[String(t.tm).toUpperCase()]) || {};
        const inp = (k) => `<input type="number" min="0" step="any" data-k="${k}" value="${c[k] || ""}" style="width:150px; text-align:right; padding:4px 6px;" />`;
        const tr = document.createElement("tr");
        tr.dataset.tm = t.tm;
        tr.innerHTML = `<td style="font-weight:600;">${t.tm}</td>
          <td class="num-align">${inp("value_target")}</td>
          <td class="num-align">${inp("card_slab_1")}</td>
          <td class="num-align">${inp("card_slab_2")}</td>`;
        body.appendChild(tr);
      });
      panel.style.display = "block";
    })
    .catch(() => showToast("Could not load targets", "error"));
};

window.saveOctTargets = function() {
  const month = (_octSheetsCacheData.targets || {}).month || "";
  const rows = [...document.querySelectorAll("#oct-targets-body tr")].map(tr => {
    const o = { tm: tr.dataset.tm };
    tr.querySelectorAll("input").forEach(i => { o[i.dataset.k] = parseFloat(i.value) || 0; });
    return o;
  });
  fetch("/api/tm-targets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ month, rows })
  })
    .then(r => r.json())
    .then(res => {
      if (res.error) { showToast("Save failed: " + res.error, "error"); return; }
      showToast("Targets saved", "success");
      _apiCache.clear();
      document.getElementById("oct-targets-panel").style.display = "none";
      loadOctSheetsData(true);
    })
    .catch(e => showToast("Save failed: " + e.message, "error"));
};

function _octValShareBg(pctInt) {
  if (pctInt >= 70) return "background:#10b981; color:#052e16; font-weight:700;";
  if (pctInt >= 65) return "background:#4ade80; color:#052e16; font-weight:700;";
  if (pctInt >= 60) return "background:#a3e635; color:#1a2e05; font-weight:700;";
  return "background:#bef264; color:#1a2e05; font-weight:700;";
}

function _octVolShareBg(pctInt) {
  if (pctInt >= 58) return "background:#38bdf8; color:#082f49; font-weight:700;";
  if (pctInt >= 50) return "background:#34d399; color:#052e16; font-weight:700;";
  if (pctInt >= 45) return "background:#a3e635; color:#1a2e05; font-weight:700;";
  return "background:#f87171; color:#450a0a; font-weight:700;";
}

window.renderOctSheetsUI = function() {
  if (!_octSheetsCacheData) return;
  const isVal = _activeOctSubSheet === "oct";
  const sheetObj = isVal ? _octSheetsCacheData.oct : _octSheetsCacheData.oct_vol;
  if (!sheetObj) return;

  const qSearch = ((document.getElementById("oct-store-search") || {}).value || "").trim().toUpperCase();
  const qTm = ((document.getElementById("oct-filter-tm") || {}).value || "ALL").toUpperCase();
  const qRbm = ((document.getElementById("oct-filter-rbm") || {}).value || "ALL").toUpperCase();
  const qBdm = ((document.getElementById("oct-filter-bdm") || {}).value || "ALL").toUpperCase();
  const showTargets = Boolean((document.getElementById("oct-tm-show-targets") || {}).checked);

  // Filter stores
  const allStores = sheetObj.stores || [];
  const filteredStores = allStores.filter(s => {
    if (qSearch && !String(s.store).toUpperCase().includes(qSearch)) return false;
    if (qTm !== "ALL" && String(s.fin_bdm).toUpperCase() !== qTm) return false;
    if (qRbm !== "ALL" && String(s.rbm).toUpperCase() !== qRbm) return false;
    if (qBdm !== "ALL" && String(s.bdm).toUpperCase() !== qBdm) return false;
    return true;
  });

  // Update KPI Strip
  const totRow = sheetObj.tm_total || { fin: 0, total: 0, fin_share: 0 };
  const northRow = sheetObj.tm_north || { fin_share: 0 };
  const southRow = sheetObj.tm_south || { fin_share: 0 };
  const octTot = (_octSheetsCacheData.oct && _octSheetsCacheData.oct.tm_total) || {};

  const kpiFin = document.getElementById("oct-kpi-fin");
  const kpiTot = document.getElementById("oct-kpi-tot");
  const kpiShare = document.getElementById("oct-kpi-share");
  const kpiNS = document.getElementById("oct-kpi-north-south");
  const kpiCard = document.getElementById("oct-kpi-card-ach");
  const kpiFinSub = document.getElementById("oct-kpi-fin-sub");
  const kpiTotSub = document.getElementById("oct-kpi-tot-sub");

  if (isVal) {
    if (kpiFin) kpiFin.textContent = "₹" + (totRow.fin / 10000000).toFixed(2) + " Cr";
    if (kpiFinSub) kpiFinSub.textContent = "Exact: ₹" + Math.round(totRow.fin).toLocaleString("en-IN");
    if (kpiTot) kpiTot.textContent = "₹" + (totRow.total / 10000000).toFixed(2) + " Cr";
    if (kpiTotSub) kpiTotSub.textContent = "Exact: ₹" + Math.round(totRow.total).toLocaleString("en-IN");
  } else {
    if (kpiFin) kpiFin.textContent = Number(totRow.fin || 0).toLocaleString("en-IN") + " Units";
    if (kpiFinSub) kpiFinSub.textContent = "Paper Finance + Card EMI Qty";
    if (kpiTot) kpiTot.textContent = Number(totRow.total || 0).toLocaleString("en-IN") + " Units";
    if (kpiTotSub) kpiTotSub.textContent = "Total ERP Sales Qty (" + (sheetObj.stores || []).length + " Stores)";
  }
  if (kpiShare) kpiShare.textContent = ((totRow.fin_share || 0) * 100).toFixed(2) + "%";
  if (kpiNS) {
    kpiNS.textContent = `N: ${((northRow.fin_share || 0) * 100).toFixed(1)}% · S: ${((southRow.fin_share || 0) * 100).toFixed(1)}%`;
  }
  if (kpiCard && octTot.card_ach !== undefined) {
    const cardCr = (octTot.card_ach / 10000000).toFixed(2);
    const cardPct = ((octTot.card_ach_pct || 0) * 100).toFixed(1);
    kpiCard.textContent = `₹${cardCr} Cr (${cardPct}%)`;
  }

  // 1. Render Store Table (Left Panel)
  const storeTitle = document.getElementById("oct-store-table-title");
  const storeBadge = document.getElementById("oct-store-count-badge");
  if (storeTitle) {
    storeTitle.innerHTML = isVal
      ? "Store Fin Share &mdash; Value (&#8377;)"
      : "Store Fin Share &mdash; Volume (Qty)";
  }
  if (storeBadge) {
    storeBadge.textContent = `${filteredStores.length} of ${allStores.length} Stores`;
  }

  const sThead = document.getElementById("oct-stores-thead");
  const sTbody = document.getElementById("oct-stores-tbody");
  const benchLabel = ((_octSheetsCacheData.benchmark || {}).label) || "Prev Qtr";
  if (sThead) {
    sThead.innerHTML = isVal
      ? `<tr>
          <th style="background:#fef08a; color:#1e293b; font-weight:800;">Stores</th>
          <th class="num-align">FIN</th>
          <th class="num-align" style="color:#4ade80;">Paper FIN</th>
          <th class="num-align" style="color:#a78bfa;">Card FIN</th>
          <th class="num-align">Total</th>
          <th class="num-align" style="background:#fef08a; color:#1e293b; font-weight:800;">FIN SHARE</th>
          <th class="num-align">${benchLabel}</th>
          <th class="num-align">DIF</th>
        </tr>`
      : `<tr>
          <th style="background:#fef08a; color:#1e293b; font-weight:800;">Stores</th>
          <th class="num-align">FIN</th>
          <th class="num-align" style="color:#4ade80;">Paper FIN</th>
          <th class="num-align" style="color:#a78bfa;">Card FIN</th>
          <th class="num-align">Total</th>
          <th class="num-align" style="background:#fef08a; color:#1e293b; font-weight:800;">FIN SHARE</th>
          <th class="num-align">${benchLabel}</th>
          <th class="num-align">DIF</th>
        </tr>`;
  }

  if (sTbody) {
    if (!filteredStores.length) {
      sTbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:25px; color:var(--text-muted);">No stores match current filters.</td></tr>`;
    } else {
      let rowsHtml = "";
      let sumFin = 0, sumTot = 0, sumPaper = 0, sumCard = 0;
      filteredStores.forEach(s => {
        sumFin += (s.fin || 0);
        sumTot += (s.total || 0);
        sumPaper += (s.paper_fin || 0);
        sumCard += (s.card_fin || 0);
        const sharePctInt = Math.round((s.fin_share || 0) * 100);
        const shareExact = ((s.fin_share || 0) * 100).toFixed(2) + "%";
        const shareStyle = isVal ? _octValShareBg(sharePctInt) : _octVolShareBg(sharePctInt);

        const benchVal = isVal ? s.amj : s.jas;
        let benchCell = `<td class="num-align" style="color:var(--text-muted);">#N/A</td>`;
        if (benchVal !== null && benchVal !== undefined) {
          const bPctInt = Math.round(benchVal * 100);
          const bStyle = isVal ? _octValShareBg(bPctInt) : _octVolShareBg(bPctInt);
          benchCell = `<td class="num-align" style="${bStyle} text-align:center;" title="${(benchVal * 100).toFixed(2)}%">${bPctInt}%</td>`;
        }

        let difCell = `<td class="num-align" style="color:var(--text-muted);">#N/A</td>`;
        if (s.dif !== null && s.dif !== undefined) {
          const difPct = (s.dif * 100);
          const isUp = difPct >= 0;
          const arrow = isUp ? "&#9650;" : "&#9660;";
          const difStyle = isUp
            ? "color:#16a34a; font-weight:700;"
            : "background:#f87171; color:#450a0a; font-weight:700;";
          difCell = `<td class="num-align" style="${difStyle}">${arrow}${Math.abs(difPct).toFixed(2)}%</td>`;
        }

        const finDisplay = Math.round(s.fin || 0).toString();
        const totDisplay = Math.round(s.total || 0).toString();
        const tip = isVal
          ? `Paper Fin: ₹${Math.round(s.paper_fin || 0).toLocaleString('en-IN')} | Card EMI: ₹${Math.round(s.card_fin || 0).toLocaleString('en-IN')}`
          : `Paper Fin: ${s.paper_fin || 0} | Card EMI: ${s.card_fin || 0}`;

        rowsHtml += `<tr title="${tip}">
          <td style="font-weight:600; white-space:nowrap;">${s.store}</td>
          <td class="num-align" style="font-variant-numeric:tabular-nums;">${finDisplay}</td>
          <td class="num-align" style="font-variant-numeric:tabular-nums; color:#16a34a;">${Math.round(s.paper_fin || 0)}</td>
          <td class="num-align" style="font-variant-numeric:tabular-nums; color:#7c3aed;">${Math.round(s.card_fin || 0)}</td>
          <td class="num-align" style="font-variant-numeric:tabular-nums;">${totDisplay}</td>
          <td class="num-align" style="${shareStyle} text-align:center;" title="${shareExact}">${sharePctInt}%</td>
          ${benchCell}
          ${difCell}
        </tr>`;
      });

      // Footer total row for filtered stores
      const totSharePct = sumTot > 0 ? ((sumFin / sumTot) * 100).toFixed(1) : "0.0";
      rowsHtml += `<tr class="total-row" style="font-weight:800;">
        <td>Total (${filteredStores.length} Stores)</td>
        <td class="num-align">${Math.round(sumFin).toLocaleString("en-IN")}</td>
        <td class="num-align" style="color:#16a34a;">${Math.round(sumPaper).toLocaleString("en-IN")}</td>
        <td class="num-align" style="color:#7c3aed;">${Math.round(sumCard).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(sumTot).toLocaleString("en-IN")}</td>
        <td class="num-align" style="text-align:center; color:#059669;">${totSharePct}%</td>
        <td colspan="2"></td>
      </tr>`;
      sTbody.innerHTML = rowsHtml;
    }
  }

  // 2. Render TM (Fin BDM) Summary Table (Right Top)
  const tmThead = document.getElementById("oct-tm-thead");
  const tmTbody = document.getElementById("oct-tm-tbody");
  if (tmThead && tmTbody) {
    if (isVal && showTargets) {
      tmThead.innerHTML = `<tr>
        <th>TM</th>
        <th class="num-align">FIN</th>
        <th class="num-align">Total</th>
        <th class="num-align" style="background:#fef08a; color:#1e293b;">FIN SHARE</th>
        <th class="num-align">Value Target</th>
        <th class="num-align">Ach %</th>
        <th class="num-align">Card Slab 2</th>
        <th class="num-align">Card Ach</th>
        <th class="num-align">Card Ach %</th>
      </tr>`;
    } else if (!isVal && showTargets) {
      tmThead.innerHTML = `<tr>
        <th>TM</th>
        <th class="num-align">FIN</th>
        <th class="num-align">Total</th>
        <th class="num-align" style="background:#fef08a; color:#1e293b;">FIN SHARE</th>
        <th class="num-align">Value Target</th>
        <th class="num-align">Card Slab 2</th>
        <th class="num-align">Card Ach</th>
      </tr>`;
    } else {
      tmThead.innerHTML = `<tr>
        <th>TM</th>
        <th class="num-align">FIN</th>
        <th class="num-align">Total</th>
        <th class="num-align" style="background:#fef08a; color:#1e293b;">FIN SHARE</th>
      </tr>`;
    }

    const tmRows = [...(sheetObj.tms || [])];
    const renderTmRow = (r, isSummary, highlightBg) => {
      const isSelected = qTm !== "ALL" && String(r.tm).toUpperCase() === qTm;
      const rowStyle = isSelected
        ? "background:rgba(37,99,235,0.18); font-weight:700; cursor:pointer;"
        : isSummary
          ? (highlightBg || "font-weight:800; background:rgba(15,23,42,0.06);")
          : "cursor:pointer;";
      const clickAttr = isSummary ? "" : `onclick="filterOctSheetBy('tm', '${r.tm}')"`;
      const shareInt = Math.round((r.fin_share || 0) * 100);
      const shareExact = ((r.fin_share || 0) * 100).toFixed(2) + "%";
      const shareStyle = isVal ? _octValShareBg(shareInt) : _octVolShareBg(shareInt);

      let cols = `<td>${r.tm}</td>
        <td class="num-align">${Math.round(r.fin || 0).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(r.total || 0).toLocaleString("en-IN")}</td>
        <td class="num-align" style="${shareStyle} text-align:center;" title="${shareExact}">${shareInt}%</td>`;

      if (isVal && showTargets) {
        const dash = "—";
        const fmtN = (v) => (v == null ? dash : Math.round(v).toLocaleString("en-IN"));
        const achPct = r.ach_pct != null ? Math.round(r.ach_pct * 100) + "%" : dash;
        const cAchPct = r.card_ach_pct != null ? Math.round(r.card_ach_pct * 100) + "%" : dash;
        cols += `
          <td class="num-align">${fmtN(r.value_target)}</td>
          <td class="num-align" style="font-weight:600; color:#2563eb;">${achPct}</td>
          <td class="num-align">${fmtN(r.card_slab_2)}</td>
          <td class="num-align">${Math.round(r.card_ach || 0).toLocaleString("en-IN")}</td>
          <td class="num-align" style="font-weight:700; color:${(r.card_ach_pct || 0) >= 1 ? '#16a34a' : '#d97706'};">${cAchPct}</td>`;
      } else if (!isVal && showTargets) {
        cols += `
          <td class="num-align">${r.value_target != null ? Math.round(r.value_target).toLocaleString("en-IN") : "—"}</td>
          <td class="num-align">${r.card_slab_2 ? Math.round(r.card_slab_2).toLocaleString("en-IN") : "—"}</td>
          <td class="num-align">${Math.round(r.card_ach || 0).toLocaleString("en-IN")}</td>`;
      }
      return `<tr style="${rowStyle}" ${clickAttr}>${cols}</tr>`;
    };

    let tmHtml = tmRows.map(r => renderTmRow(r, false)).join("");
    if (sheetObj.tm_total) {
      tmHtml += renderTmRow(sheetObj.tm_total, true, "font-weight:800; border-top:2px solid var(--card-border);");
    }
    if (sheetObj.tm_north) {
      tmHtml += renderTmRow(sheetObj.tm_north, true, "font-weight:700; background:rgba(16,185,129,0.10);");
    }
    if (sheetObj.tm_south) {
      tmHtml += renderTmRow(sheetObj.tm_south, true, "font-weight:700; background:rgba(59,130,246,0.08);");
    }
    tmTbody.innerHTML = tmHtml;
  }

  // 3. Render RBM Summary Table (Right Middle)
  const rbmTbody = document.getElementById("oct-rbm-tbody");
  if (rbmTbody) {
    const rbms = sheetObj.rbms || [];
    let rbmHtml = "";
    rbms.forEach(r => {
      const isSel = qRbm !== "ALL" && String(r.rbm).toUpperCase() === qRbm;
      const shareInt = Math.round((r.fin_share || 0) * 100);
      const shareStyle = isVal ? _octValShareBg(shareInt) : _octVolShareBg(shareInt);
      rbmHtml += `<tr style="cursor:pointer; ${isSel ? 'background:rgba(37,99,235,0.18); font-weight:700;' : ''}" onclick="filterOctSheetBy('rbm', '${r.rbm}')">
        <td style="font-weight:600;">${r.rbm}</td>
        <td class="num-align">${Math.round(r.fin || 0).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(r.total || 0).toLocaleString("en-IN")}</td>
        <td class="num-align" style="${shareStyle} text-align:center;" title="${((r.fin_share || 0) * 100).toFixed(2)}%">${shareInt}%</td>
      </tr>`;
    });
    if (sheetObj.tm_total) {
      const t = sheetObj.tm_total;
      const tInt = Math.round((t.fin_share || 0) * 100);
      rbmHtml += `<tr class="total-row" style="font-weight:800;">
        <td>Total</td>
        <td class="num-align">${Math.round(t.fin || 0).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(t.total || 0).toLocaleString("en-IN")}</td>
        <td class="num-align" style="text-align:center; color:#059669;">${tInt}%</td>
      </tr>`;
    }
    rbmTbody.innerHTML = rbmHtml;
  }

  // 4. Render BDM Summary Table (Right Bottom)
  const bdmTbody = document.getElementById("oct-bdm-tbody");
  if (bdmTbody) {
    const bdms = sheetObj.bdms || [];
    let bdmHtml = "";
    bdms.forEach(b => {
      const isSel = qBdm !== "ALL" && String(b.bdm).toUpperCase() === qBdm;
      const shareInt = Math.round((b.fin_share || 0) * 100);
      const shareStyle = isVal ? _octValShareBg(shareInt) : _octVolShareBg(shareInt);
      bdmHtml += `<tr style="cursor:pointer; ${isSel ? 'background:rgba(37,99,235,0.18); font-weight:700;' : ''}" onclick="filterOctSheetBy('bdm', '${b.bdm}')">
        <td style="font-weight:600;">${b.bdm}</td>
        <td class="num-align">${Math.round(b.fin || 0).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(b.total || 0).toLocaleString("en-IN")}</td>
        <td class="num-align" style="${shareStyle} text-align:center;" title="${((b.fin_share || 0) * 100).toFixed(2)}%">${shareInt}%</td>
      </tr>`;
    });
    if (sheetObj.tm_total) {
      const t = sheetObj.tm_total;
      const tInt = Math.round((t.fin_share || 0) * 100);
      bdmHtml += `<tr class="total-row" style="font-weight:800;">
        <td>Total</td>
        <td class="num-align">${Math.round(t.fin || 0).toLocaleString("en-IN")}</td>
        <td class="num-align">${Math.round(t.total || 0).toLocaleString("en-IN")}</td>
        <td class="num-align" style="text-align:center; color:#059669;">${tInt}%</td>
      </tr>`;
    }
    bdmTbody.innerHTML = bdmHtml;
  }
};

window.exportOctSheetsToExcel = function() {
  if (!_octSheetsCacheData || typeof XLSX === "undefined") {
    showToast("Data not loaded yet", "error");
    return;
  }
  try {
    const wb = XLSX.utils.book_new();

    // Sheet 1: OCT (Value)
    const oct = _octSheetsCacheData.oct;
    const octRows = [["Stores", "FIN", "Paper FIN", "Card FIN", "Total", "FIN SHARE", ((_octSheetsCacheData.benchmark || {}).label) || "Prev Qtr", "DIF"]];
    (oct.stores || []).forEach(s => {
      octRows.push([
        s.store,
        Math.round(s.fin || 0),
        Math.round(s.paper_fin || 0),
        Math.round(s.card_fin || 0),
        Math.round(s.total || 0),
        ((s.fin_share || 0) * 100).toFixed(2) + "%",
        s.amj !== null && s.amj !== undefined ? (s.amj * 100).toFixed(2) + "%" : "#N/A",
        s.dif !== null && s.dif !== undefined ? (s.dif * 100).toFixed(2) + "%" : "#N/A",
      ]);
    });
    octRows.push([]);
    octRows.push(["TM", "FIN", "Total", "FIN SHARE", "Value Target", "Ach %", "Card Slab 2", "Card Ach", "Card Ach %"]);
    [...(oct.tms || []), oct.tm_total, oct.tm_north, oct.tm_south].filter(Boolean).forEach(t => {
      octRows.push([
        t.tm,
        Math.round(t.fin || 0),
        Math.round(t.total || 0),
        ((t.fin_share || 0) * 100).toFixed(2) + "%",
        Math.round(t.value_target || 0),
        ((t.ach_pct || 0) * 100).toFixed(2) + "%",
        Math.round(t.card_slab_2 || 0),
        Math.round(t.card_ach || 0),
        ((t.card_ach_pct || 0) * 100).toFixed(2) + "%",
      ]);
    });
    octRows.push([]);
    octRows.push(["RBM", "FIN", "Total", "FIN SHARE"]);
    (oct.rbms || []).forEach(r => {
      octRows.push([r.rbm, Math.round(r.fin || 0), Math.round(r.total || 0), ((r.fin_share || 0) * 100).toFixed(2) + "%"]);
    });
    octRows.push([]);
    octRows.push(["BDM", "FIN", "Total", "FIN SHARE"]);
    (oct.bdms || []).forEach(b => {
      octRows.push([b.bdm, Math.round(b.fin || 0), Math.round(b.total || 0), ((b.fin_share || 0) * 100).toFixed(2) + "%"]);
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(octRows), "Value");

    // Sheet 2: OCT Vol (Volume)
    const vol = _octSheetsCacheData.oct_vol;
    const volRows = [["Stores", "FIN", "Paper FIN", "Card FIN", "Total", "FIN SHARE", ((_octSheetsCacheData.benchmark || {}).label) || "Prev Qtr", "DIF"]];
    (vol.stores || []).forEach(s => {
      volRows.push([
        s.store,
        s.fin || 0,
        s.paper_fin || 0,
        s.card_fin || 0,
        s.total || 0,
        ((s.fin_share || 0) * 100).toFixed(2) + "%",
        s.jas !== null && s.jas !== undefined ? (s.jas * 100).toFixed(2) + "%" : "#N/A",
        s.dif !== null && s.dif !== undefined ? (s.dif * 100).toFixed(2) + "%" : "#N/A",
      ]);
    });
    volRows.push([]);
    volRows.push(["TM", "FIN", "Total", "FIN SHARE"]);
    [...(vol.tms || []), vol.tm_total, vol.tm_north, vol.tm_south].filter(Boolean).forEach(t => {
      volRows.push([t.tm, t.fin || 0, t.total || 0, ((t.fin_share || 0) * 100).toFixed(2) + "%"]);
    });
    volRows.push([]);
    volRows.push(["RBM", "FIN", "Total", "FIN SHARE"]);
    (vol.rbms || []).forEach(r => {
      volRows.push([r.rbm, r.fin || 0, r.total || 0, ((r.fin_share || 0) * 100).toFixed(2) + "%"]);
    });
    volRows.push([]);
    volRows.push(["BDM", "FIN", "Total", "FIN SHARE"]);
    (vol.bdms || []).forEach(b => {
      volRows.push([b.bdm, b.fin || 0, b.total || 0, ((b.fin_share || 0) * 100).toFixed(2) + "%"]);
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(volRows), "Volume");

    XLSX.writeFile(wb, `Store_Fin_Share_${activeDate || "data"}.xlsx`);
    showToast("✅ Exported Store Fin Share to Excel", "success");
  } catch (e) {
    showToast("Export failed: " + e.message, "error");
  }
};
