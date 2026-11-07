/**
 * drill_down.js  —  Category Performance Drill-Down + View Toggle
 * ───────────────────────────────────────────────────────────────
 *  Level 0  →  All Categories  (CD PRODUCTS, LAPTOP…)
 *  Level 1  →  Products inside a Category
 *  Level 2  →  Brands inside a Product   (via /api/brand-data)
 *
 *  View modes: 'qty' (Volume) | 'value' (Value in Cr)
 *  Loaded AFTER main.js; hooks via window._renderCategoryPerfDrillDown.
 */
(function () {
  'use strict';

  /* ── State ──────────────────────────────────────────────────── */
  window._catDrillState = { level: 0, category: null, product: null };
  window._catViewMode   = 'qty';   // 'qty' | 'value'
  window._useDrillDown  = true;

  var COLS  = ["aCASH","BAJAJ","HDB","IDFC","PFL","TVS","DMI","PineLab","PAYTM","Benow"];
  var PAPER = ["BAJAJ","HDB","IDFC","PFL","TVS","DMI"];
  var CARD  = ["PineLab","PAYTM","Benow"];

  /* ── Format helper ──────────────────────────────────────────── */
  function _f(v, type) {
    if (type === 'percent')  return (v != null && !isNaN(v)) ? Number(v).toFixed(1) + '%' : '-';
    if (typeof formatNumber === 'function') return formatNumber(v, type);
    if (type === 'decimal')  return v != null ? parseFloat(v).toFixed(2)       : '-';
    if (type === 'currency') return v != null ? '\u20B9' + Math.round(v).toLocaleString() : '-';
    return v != null ? String(v) : '-';
  }

  /**
   * _fv(v) — smart value formatter for "Value (in Cr)" mode
   *   >= 1 Cr  → "X.XX Cr"   (e.g. 17.22 Cr)
   *   < 1 Cr   → "XX.X L"    (e.g. 0.92 Cr → 92.0 L)
   *   0        → "0"
   */
  function _fv(v) {
    var n = parseFloat(v) || 0;
    if (n === 0) return '0';
    var abs = Math.abs(n);
    if (abs >= 1) {
      return n.toFixed(2) + '\u00A0Cr';       // non-breaking space + Cr
    }
    var lakhs = n * 100;
    var absL  = Math.abs(lakhs);
    if (absL >= 10)  return lakhs.toFixed(1) + '\u00A0L';
    if (absL >= 1)   return lakhs.toFixed(2) + '\u00A0L';
    return lakhs.toFixed(2) + '\u00A0L';
  }

  /* ── Card meta updater (title, badge, toggle buttons) ───────── */
  function _updateCardMeta() {
    var isQty = window._catViewMode === 'qty';
    var h2    = document.getElementById('cat-perf-title');
    var badge = document.getElementById('cat-perf-badge');
    if (h2)    h2.textContent    = isQty
      ? 'Product Category Performance \u2014 Volume (Qty)'
      : 'Product Category Performance \u2014 Value (in Cr)';
    if (badge) badge.textContent = isQty ? 'Quantity Sourcing Grid' : 'Value Sourcing Grid';
    var qBtn = document.getElementById('cat-toggle-qty');
    var vBtn = document.getElementById('cat-toggle-val');
    if (qBtn) qBtn.classList.toggle('active', isQty);
    if (vBtn) vBtn.classList.toggle('active', !isQty);
  }

  /* ── Public: switch view mode ───────────────────────────────── */
  window.setCatViewMode = function (mode) {
    window._catViewMode = (mode === 'value') ? 'value' : 'qty';
    _updateCardMeta();
    if (window._catDrillState.level === 2) {
      _fetchBrandDrill(window._catDrillState.category, window._catDrillState.product);
    } else {
      window._renderCategoryPerfDrillDown();
    }
  };

  /* ── Public: navigate to a drill level ─────────────────────── */
  window.catDrillTo = function (level, category, product) {
    window._catDrillState.level    = level    || 0;
    window._catDrillState.category = category || null;
    window._catDrillState.product  = product  || null;
    if (level === 2 && category && product) {
      _updateBreadcrumb();
      _fetchBrandDrill(category, product);
    } else {
      window._renderCategoryPerfDrillDown();
    }
  };

  /* ── Breadcrumb renderer ────────────────────────────────────── */
  function _updateBreadcrumb() {
    var nav = document.getElementById('cat-drill-breadcrumb');
    if (!nav) return;
    var level    = window._catDrillState.level;
    var category = window._catDrillState.category;
    var product  = window._catDrillState.product;
    var homeIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>';
    var sep  = '<span class="crumb-sep">\u203A</span>';
    var html = '';
    if (level === 0) {
      html = '<button class="crumb crumb-active">' + homeIcon + ' All Categories</button>';
    } else if (level === 1) {
      html = '<button class="crumb" onclick="catDrillTo(0)">' + homeIcon + ' All</button>' + sep +
             '<button class="crumb crumb-active"><span class="crumb-dot"></span>' + category + '</button>';
    } else {
      var esc = (category || '').replace(/'/g, "\\'");
      html = '<button class="crumb" onclick="catDrillTo(0)">' + homeIcon + ' All</button>' + sep +
             '<button class="crumb" onclick="catDrillTo(1,\'' + esc + '\')">' + category + '</button>' + sep +
             '<button class="crumb crumb-active"><span class="crumb-dot"></span>' + product + '</button>';
    }
    nav.innerHTML = html;
    var backBtn = document.getElementById('cat-perf-back-btn');
    if (backBtn) {
      backBtn.style.display = level > 0 ? 'inline-flex' : 'none';
    }
  }

  window.catDrillBack = function() {
    var lvl = window._catDrillState.level;
    if (lvl > 0) {
      catDrillTo(lvl - 1, window._catDrillState.category);
    }
  };

  /* ── Update column-0 header label ──────────────────────────── */
  function _setColHeader(label) {
    var el = document.getElementById('cat-qty-col0');
    if (el) el.textContent = label;
  }

  /* ── Grand-total + share rows ───────────────────────────────── */
  function _appendTotals(body, colTotals, isQty) {
    var gtTot   = COLS.reduce( function(s,c){ return s + colTotals[c]; }, 0);
    var gtPaper = PAPER.reduce(function(s,c){ return s + colTotals[c]; }, 0);
    var gtCard  = CARD.reduce( function(s,c){ return s + colTotals[c]; }, 0);
    var gtFin   = gtTot > 0 ? (gtPaper + gtCard) / gtTot * 100 : 0;
    var fmt     = function(v){ return isQty ? Math.round(v).toLocaleString() : _fv(v); };

    /* Grand Total row */
    var trGT = document.createElement('tr'); trGT.className = 'total-row';
    var td = '<td>Grand Total</td>';
    COLS.forEach(function(c){ td += '<td class="num-align">' + fmt(colTotals[c]) + '</td>'; });
    td += '<td class="num-align">' + fmt(gtTot)            + '</td>' +
          '<td class="num-align">' + _f(gtFin, 'percent')  + '</td>' +
          '<td class="num-align">' + fmt(gtPaper)           + '</td>' +
          '<td class="num-align">' + fmt(gtCard)            + '</td>' +
          '<td class="num-align">-</td><td class="num-align">-</td>';
    trGT.innerHTML = td;
    body.appendChild(trGT);

    /* Share row */
    var trSh = document.createElement('tr'); trSh.className = 'share-row';
    var td2  = '<td style="font-weight:600;color:var(--text-muted)">Share</td>';
    COLS.forEach(function(c){
      var sh = gtTot > 0 ? colTotals[c] / gtTot * 100 : 0;
      td2 += '<td class="num-align">' + _f(sh, 'percent') + '</td>';
    });
    td2 += '<td class="num-align">-</td><td class="num-align">-</td>' +
           '<td class="num-align">' + _f(gtTot > 0 ? gtPaper / gtTot * 100 : 0, 'percent') + '</td>' +
           '<td class="num-align">' + _f(gtTot > 0 ? gtCard  / gtTot * 100 : 0, 'percent') + '</td>' +
           '<td class="num-align">-</td><td class="num-align">-</td>';
    trSh.innerHTML = td2;
    body.appendChild(trSh);
  }

  /* ── Build a single data row ────────────────────────────────── */
  function _buildDataRow(key, info, isQty, clickFn, bankEmi) {
    var tr = document.createElement('tr');
    tr.setAttribute('data-category', key);
    if (clickFn) {
      tr.classList.add('drillable-row');
      tr.addEventListener('click', clickFn);
    } else {
      tr.classList.add('drill-leaf-row');
    }

    var total = COLS.reduce( function(s,c){ return s + (isQty ? (info[c] || 0) : (parseFloat(info[c]) || 0)); }, 0);
    var paper = PAPER.reduce(function(s,c){ return s + (isQty ? (info[c] || 0) : (parseFloat(info[c]) || 0)); }, 0);
    var card  = CARD.reduce( function(s,c){ return s + (isQty ? (info[c] || 0) : (parseFloat(info[c]) || 0)); }, 0);
    var fin   = total > 0 ? (paper + card) / total * 100 : 0;
    var fmt = function(v){ return isQty ? v.toLocaleString() : _fv(v); };

    var tds = '<td class="drill-cell' + (clickFn ? '' : ' drill-leaf') + '">' + key + '</td>';
    COLS.forEach(function(c){
      var v = isQty ? (info[c] || 0) : (parseFloat(info[c]) || 0);
      /* BANK EMI has no aCASH sales: leave the aCASH cell blank. The card units are still deducted
         from the aCASH Grand Total (colTotals keep the underlying value). */
      tds += '<td class="num-align">' + ((bankEmi && c === 'aCASH') ? '' : fmt(v)) + '</td>';
    });
    tds += '<td class="num-align" style="font-weight:700">' + (bankEmi ? '' : fmt(total)) + '</td>' +
           '<td class="num-align" style="font-weight:600">' + (bankEmi ? '-' : _f(fin,   'percent')) + '</td>' +
           '<td class="num-align" style="font-weight:600">' + fmt(paper)              + '</td>' +
           '<td class="num-align" style="font-weight:600">' + fmt(card)               + '</td>' +
           '<td class="num-align">' + (info.last_week_pct   != null ? _f(info.last_week_pct,   'percent') : '-') + '</td>' +
           '<td class="num-align">' + (info.apr_closing_pct != null ? _f(info.apr_closing_pct, 'percent') : '-') + '</td>';
    tr.innerHTML = tds;
    return tr;
  }

  /* ── Main renderer (levels 0 & 1) ──────────────────────────── */
  window._renderCategoryPerfDrillDown = function () {
    _updateBreadcrumb();
    _updateCardMeta();

    var CATS     = ["CD PRODUCTS","LAPTOP","MOBILE","HVA","SA","OTHERS","BANK EMI"];
    var level    = window._catDrillState.level;
    var drillCat = window._catDrillState.category;
    var isQty    = window._catViewMode === 'qty';
    var lblMap   = { 0: 'Category', 1: 'Product', 2: 'Brand' };
    _setColHeader(lblMap[level] || 'Category');

    if (level === 2) { _fetchBrandDrill(drillCat, window._catDrillState.product); return; } /* brand view: refetch so the current filters apply */

    var cp       = (typeof reviewData !== 'undefined' && reviewData.category_performance) || {};
    var rowKeys, getData;

    if (level === 0) {
      rowKeys = CATS;
      getData = isQty
        ? function(k){ return (cp.qty   && cp.qty[k])   || {}; }
        : function(k){ return (cp.value && cp.value[k]) || {}; };
    } else {
      /* Level 1: products within selected category */
      var srcKey = isQty ? 'products_qty' : 'products_val';
      var src    = (cp[srcKey] && cp[srcKey][drillCat]) || {};
      rowKeys    = Object.keys(src).sort();
      getData    = function(k){ return src[k] || {}; };

      if (!rowKeys.length) {
        /* fallback to root if no product data */
        window._catDrillState.level = 0;
        window._renderCategoryPerfDrillDown();
        return;
      }
    }

    var body = document.querySelector('#table-cat-qty tbody');
    if (!body) return;
    body.innerHTML = '';

    var colTotals = {};
    COLS.forEach(function(c){ colTotals[c] = 0; });

    rowKeys.forEach(function(key) {
      var info    = getData(key);
      if (level === 0 && isQty && !info._emi_deducted) {
        var cardCount = (info["PineLab"] || 0) + (info["PAYTM"] || 0) + (info["Benow"] || 0);
        info["aCASH"] = Math.max(0, (info["aCASH"] || 0) - cardCount);
        info._emi_deducted = true;
      }
      var lvlSnap = level;

      var clickFn = function() {
        catDrillTo(
          lvlSnap + 1,
          lvlSnap === 0 ? key     : drillCat,
          lvlSnap === 0 ? null    : key
        );
      };

      body.appendChild(_buildDataRow(key, info, isQty, clickFn,
        (level === 0 ? key === 'BANK EMI' : drillCat === 'BANK EMI')));
      COLS.forEach(function(c){
        colTotals[c] += isQty ? (info[c] || 0) : (parseFloat(info[c]) || 0);
      });
    });

    _appendTotals(body, colTotals, isQty);

    /* ── Extra rows at level 0 only ─────────────────────────── */
    if (level === 0) {
      var sif = cp.sif || null;
      var asp = cp.asp || null;

      /* Share in Fin Value row */
      if (sif) {
        var trSif = document.createElement('tr');
        trSif.style.background = 'rgba(255,255,255,0.01)';
        var siftd = '<td style="font-weight:600;color:var(--text-muted)">Share in Fin Value</td>' +
                    '<td class="num-align">-</td>';
        COLS.slice(1).forEach(function(c){
          siftd += '<td class="num-align">' + _f(sif[c], 'percent') + '</td>';
        });
        siftd += '<td class="num-align">-</td><td class="num-align">-</td>' +
                 '<td class="num-align">' + _f(sif.paper_total, 'percent') + '</td>' +
                 '<td class="num-align">' + _f(sif.card_total,  'percent') + '</td>' +
                 '<td class="num-align">-</td><td class="num-align">-</td>';
        trSif.innerHTML = siftd;
        body.appendChild(trSif);
      }

      /* ASP row — shown only in Value mode */
      if (asp && !isQty) {
        var trAsp = document.createElement('tr');
        trAsp.style.background = 'rgba(255,255,255,0.01)';
        var asptd = '<td style="font-weight:600;color:var(--text-muted)">ASP</td>' +
                    '<td class="num-align">-</td>';
        COLS.slice(1).forEach(function(c){
          asptd += '<td class="num-align">' + _f(asp[c], 'currency') + '</td>';
        });
        asptd += '<td class="num-align">-</td><td class="num-align">-</td>' +
                 '<td class="num-align">-</td><td class="num-align">-</td>' +
                 '<td class="num-align">-</td><td class="num-align">-</td>';
        trAsp.innerHTML = asptd;
        body.appendChild(trAsp);
      }
    }
  };

  /* ── Level 2: fetch brands from /api/brand-data ─────────────── */
  /* Current hierarchy dropdown filters as a query string (state, rbm, fin_bdm, bdm, store, staff) */
  function _brandFilterQS() {
    var ids = { state: 'cat-filter-state', district: 'cat-filter-district', rbm: 'cat-filter-rbm', fin_bdm: 'cat-filter-fin-bdm',
                bdm: 'cat-filter-bdm', store: 'cat-filter-store', staff: 'cat-filter-staff' };
    var qs = '';
    Object.keys(ids).forEach(function (k) {
      var el = document.getElementById(ids[k]);
      if (el && el.value) qs += '&' + k + '=' + encodeURIComponent(el.value);
    });
    return qs;
  }

  function _fetchBrandDrill(category, product) {
    var body  = document.querySelector('#table-cat-qty tbody');
    var isQty = window._catViewMode === 'qty';
    _setColHeader('Brand');
    _updateCardMeta();

    if (body) {
      body.innerHTML =
        '<tr><td colspan="17" style="text-align:center;padding:28px;color:var(--text-muted)">' +
        '<div class="spinner-ring" style="margin:0 auto 12px"></div>' +
        'Loading brands for <strong style="color:var(--text-primary)">' + product + '</strong>\u2026</td></tr>';
    }

    var tf  = (document.getElementById('timeframeSelect') || {}).value || 'monthly';
    var ad  = typeof activeDate !== 'undefined' ? activeDate : '';
    var ed  = typeof _endDate   === 'function'  ? _endDate() : '';
    var src = typeof _src       === 'function'  ? _src()     : '';
    var url = '/api/brand-data?date=' + ad + '&end_date=' + ed + '&timeframe=' + tf +
              '&category=' + encodeURIComponent(category) +
              '&product='  + encodeURIComponent(product)  + _brandFilterQS() + src;

    var fetcher = typeof _cachedFetch === 'function'
      ? _cachedFetch
      : function(u){ return fetch(u).then(function(r){ return r.json(); }); };

    fetcher(url).then(function(data) {
      if (!body) return;
      body.innerHTML = '';

      var brands = data.brands || [];
      if (!brands.length) {
        body.innerHTML =
          '<tr><td colspan="17" style="text-align:center;padding:32px;color:var(--text-muted)">' +
          'No brand data found for "' + product + '"</td></tr>';
        return;
      }

      var colTotals    = {}; COLS.forEach(function(c){ colTotals[c]    = 0; });
      var valTotals    = {}; COLS.forEach(function(c){ valTotals[c]    = 0; });
      var gtVal = 0;

      brands.forEach(function(b) {
        var bQty   = COLS.reduce( function(s,c){ return s + (b[c] || 0); }, 0);
        var bPaper = PAPER.reduce(function(s,c){ return s + (b[c] || 0); }, 0);
        var bCard  = CARD.reduce( function(s,c){ return s + (b[c] || 0); }, 0);
        var bFin   = bQty > 0 ? (bPaper + bCard) / bQty * 100 : 0;
        if (!bQty && !b.value) return;

        COLS.forEach(function(c){ colTotals[c] += (b[c] || 0); });
        COLS.forEach(function(c){ valTotals[c] += (parseFloat(b[c + '_val']) || 0); });
        gtVal += (b.value || 0);

        var tr  = document.createElement('tr');
        tr.classList.add('drill-leaf-row');
        var tds = '<td class="drill-cell drill-leaf">' + b.brand + '</td>';

        if (isQty) {
          COLS.forEach(function(c){
            tds += '<td class="num-align">' + (b[c] || 0).toLocaleString() + '</td>';
          });
          tds += '<td class="num-align" style="font-weight:700">' + bQty.toLocaleString()  + '</td>' +
                 '<td class="num-align">'                          + _f(bFin, 'percent')    + '</td>' +
                 '<td class="num-align">'                          + bPaper.toLocaleString() + '</td>' +
                 '<td class="num-align">'                          + bCard.toLocaleString()  + '</td>' +
                 '<td class="num-align">-</td><td class="num-align">-</td>';
        } else {
          /* Value mode: show per-financier value breakdown from _val fields */
          var bValTotal = COLS.reduce( function(s,c){ return s + (parseFloat(b[c+'_val'])||0); }, 0);
          var bValPaper = PAPER.reduce(function(s,c){ return s + (parseFloat(b[c+'_val'])||0); }, 0);
          var bValCard  = CARD.reduce( function(s,c){ return s + (parseFloat(b[c+'_val'])||0); }, 0);
          var bValFin   = bValTotal > 0 ? (bValPaper + bValCard) / bValTotal * 100 : 0;
          COLS.forEach(function(c){
            var v = parseFloat(b[c + '_val']) || 0;
            tds += '<td class="num-align">' + (v > 0 ? _fv(v) : '0') + '</td>';
          });
          tds += '<td class="num-align" style="font-weight:700">' + _fv(b.value || 0)       + '</td>' +
                 '<td class="num-align">'                          + _f(bValFin, 'percent')  + '</td>' +
                 '<td class="num-align">'                          + _fv(bValPaper)          + '</td>' +
                 '<td class="num-align">'                          + _fv(bValCard)           + '</td>' +
                 '<td class="num-align">-</td><td class="num-align">-</td>';
        }
        tr.innerHTML = tds;
        body.appendChild(tr);
      });

      /* Grand total row for brand level */
      var trGT = document.createElement('tr'); trGT.className = 'total-row';
      var qg   = '<td>Grand Total</td>';

      if (isQty) {
        var gtQ  = COLS.reduce( function(s,c){ return s + colTotals[c]; }, 0);
        var gtQP = PAPER.reduce(function(s,c){ return s + colTotals[c]; }, 0);
        var gtQC = CARD.reduce( function(s,c){ return s + colTotals[c]; }, 0);
        var gtQF = gtQ > 0 ? (gtQP + gtQC) / gtQ * 100 : 0;
        COLS.forEach(function(c){ qg += '<td class="num-align">' + colTotals[c].toLocaleString() + '</td>'; });
        qg += '<td class="num-align">' + gtQ.toLocaleString()  + '</td>' +
              '<td class="num-align">' + _f(gtQF, 'percent')   + '</td>' +
              '<td class="num-align">' + gtQP.toLocaleString() + '</td>' +
              '<td class="num-align">' + gtQC.toLocaleString() + '</td>' +
              '<td class="num-align">-</td><td class="num-align">-</td>';
      } else {
        /* Value mode grand total */
        var gtVP = PAPER.reduce(function(s,c){ return s + valTotals[c]; }, 0);
        var gtVC = CARD.reduce( function(s,c){ return s + valTotals[c]; }, 0);
        var gtVF = gtVal > 0 ? (gtVP + gtVC) / gtVal * 100 : 0;
        COLS.forEach(function(c){ qg += '<td class="num-align">' + (valTotals[c] > 0 ? _fv(valTotals[c]) : '0') + '</td>'; });
        qg += '<td class="num-align">' + _fv(gtVal) + '</td>' +
              '<td class="num-align">' + _f(gtVF, 'percent') + '</td>' +
              '<td class="num-align">' + _fv(gtVP) + '</td>' +
              '<td class="num-align">' + _fv(gtVC) + '</td>' +
              '<td class="num-align">-</td><td class="num-align">-</td>';
      }
      trGT.innerHTML = qg;
      body.appendChild(trGT);

    }).catch(function(err) {
      console.error('[DrillDown] Brand fetch error:', err);
      if (body) {
        body.innerHTML =
          '<tr><td colspan="17" style="text-align:center;padding:24px;color:var(--color-danger)">' +
          'Failed to load brand data. Please try again.</td></tr>';
      }
    });
  }

  /* ── Reset drill state when new data loads ──────────────────── */
  document.addEventListener('reviewDataLoaded', function () {
    if (window._catDrillState && window._catDrillState.level !== 0) {
      window._catDrillState = { level: 0, category: null, product: null };
    }
  });

  console.log('[DrillDown] Category drill-down + view toggle loaded \u2713');
})();
