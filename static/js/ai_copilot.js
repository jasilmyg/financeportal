/* ═══════════════════════════════════════════════════════════════════
   myG FINANCE PORTAL — INBUILT AI AGENT, STUDIO & DASHBOARD EDITOR
   Handles:
     - Context-aware Floating AI Copilot Drawer (Ctrl+K)
     - Full-screen AI Analyst & Studio Tab (Chat / Brief / Studio / Memory & Settings)
     - Live SSE Streaming + Inline Chart.js + Sortable/Downloadable Tables
     - Tier 1 Live UI Control Bridge -> main.js
     - Tier 2 Persistent Custom Widgets, Thresholds & Targets (ClickHouse + Undo)
     - Tier 3 Optional Verified Source-Code Patch Preview & Approval
   ═══════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const state = {
    conversationId: "",
    isStreaming: false,
    status: null,
    dashboardConfig: null,
    charts: new Map(), // canvasId -> Chart instance
    lastUserQuestion: "",
  };

  // ── Utilities ───────────────────────────────────────────────────────
  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(msg, type) {
    if (typeof window.showToast === "function") {
      window.showToast(msg, type || "info");
    } else {
      console.log("[myG AI]", msg);
    }
  }

  function renderMarkdown(md) {
    let html = esc(md || "");
    // Code blocks
    html = html.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Headings
    html = html.replace(/^####\s+(.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
    // Bold & Italic
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    // Blockquotes
    html = html.replace(/^&gt;\s?(.+)$/gm, "<blockquote style='border-left:3px solid #e85c14;padding-left:10px;margin:6px 0;opacity:0.9;'>$1</blockquote>");
    // Lists
    html = html.replace(/^(?:- |\* )(.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>[\s\S]*?<\/li>)(?!\n<li>)/g, "<ul>$1</ul>");
    // Line breaks
    html = html.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
    html = html.replace(/<\/h3><br>/g, "</h3>").replace(/<\/h4><br>/g, "</h4>").replace(/<\/ul><br>/g, "</ul>");
    return html;
  }

  // ── Portal Context Reader ───────────────────────────────────────────
  function getPortalContext() {
    if (window._mygPortalBridge && typeof window._mygPortalBridge.getContext === "function") {
      return window._mygPortalBridge.getContext();
    }
    const activeNav = document.querySelector(".nav-item.active");
    const activeTab = activeNav ? activeNav.getAttribute("data-tab") : "dashboard";
    const dateEl = document.getElementById("reviewDateSelect");
    const endEl = document.getElementById("endDateSelect");
    const tfEl = document.getElementById("timeframeSelect");
    const oct2View = document.getElementById("oct2-view-by");
    const oct2Cmp = document.getElementById("oct2-compare-mode");
    const oct2Split = document.getElementById("oct2-fin-split");
    return {
      active_tab: activeTab || "dashboard",
      date: (dateEl && dateEl.value) || new Date().toISOString().slice(0, 10),
      end_date: (endEl && endEl.value) || "",
      timeframe: (tfEl && tfEl.value) || "monthly",
      state: "KERALA",
      source: "clickhouse",
      oct2_view_by: oct2View ? oct2View.value : "store",
      oct2_compare: oct2Cmp ? oct2Cmp.value : "none",
      oct2_fin_split: oct2Split ? oct2Split.value : "total",
    };
  }

  function updateContextStrip() {
    const ctx = getPortalContext();
    document.querySelectorAll(".ai-ctx-summary").forEach((el) => {
      const pLabel = ctx.timeframe === "custom" && ctx.end_date
        ? `${ctx.date} → ${ctx.end_date}`
        : `${ctx.date || "—"} (${ctx.timeframe})`;
      el.innerHTML =
        `<span class="ai-ctx-chip">Tab: ${esc(ctx.active_tab)}</span>` +
        `<span class="ai-ctx-chip">Period: ${esc(pLabel)}</span>` +
        `<span class="ai-ctx-chip">State: ${esc(ctx.state || "KERALA")}</span>`;
    });
  }

  // ── Tier 1: Live UI Action Dispatcher ───────────────────────────────
  function executeUIActions(actions) {
    if (!Array.isArray(actions) || !actions.length) return;
    actions.forEach((act) => {
      if (!act || !act.type) return;
      if (act.type === "switch_tab" && act.tab) {
        const nav = document.querySelector(`.nav-item[data-tab="${act.tab}"]`);
        if (nav) nav.click();
      } else if (act.type === "set_period") {
        if (window._mygPortalBridge && typeof window._mygPortalBridge.setPeriod === "function") {
          window._mygPortalBridge.setPeriod(act.timeframe, act.date, act.end_date);
        } else {
          const tfEl = document.getElementById("timeframeSelect");
          const dEl = document.getElementById("reviewDateSelect");
          const eEl = document.getElementById("endDateSelect");
          if (dEl && act.date) dEl.value = act.date;
          if (eEl && act.end_date) eEl.value = act.end_date;
          if (tfEl && act.timeframe) {
            tfEl.value = act.timeframe;
            tfEl.dispatchEvent(new Event("change", { bubbles: true }));
          } else if (dEl) {
            dEl.dispatchEvent(new Event("change", { bubbles: true }));
          }
        }
      } else if (act.type === "set_oct2_controls") {
        const map = [
          ["oct2-view-by", act.view_by],
          ["oct2-compare-mode", act.compare],
          ["oct2-fin-split", act.fin_split],
          ["oct2-store-search", act.search],
        ];
        map.forEach(([id, val]) => {
          const el = document.getElementById(id);
          if (el && val !== undefined && val !== null && val !== "") {
            el.value = val;
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.dispatchEvent(new Event("input", { bubbles: true }));
          }
        });
      } else if (act.type === "highlight_rows") {
        const thresh = Number(act.threshold_pct || 50);
        const op = act.operator || "lt";
        document.querySelectorAll("tr.ai-highlighted-row").forEach((tr) => tr.classList.remove("ai-highlighted-row"));
        let count = 0;
        document.querySelectorAll(".tab-page.active table tbody tr").forEach((tr) => {
          const cells = Array.from(tr.querySelectorAll("td"));
          for (const td of cells) {
            const m = /^\s*(\d+(?:\.\d+)?)\s*%\s*$/.exec(td.textContent || "");
            if (m) {
              const v = parseFloat(m[1]);
              if ((op === "lt" && v < thresh) || (op === "gt" && v > thresh)) {
                tr.classList.add("ai-highlighted-row");
                count++;
                break;
              }
            }
          }
        });
        toast(`Highlighted ${count} row(s) (${op === "lt" ? "<" : ">"} ${thresh}%)`, "info");
      } else if (act.type === "trigger_export") {
        const btn = document.getElementById(act.format === "pptx" ? "btnExportPptx" : "btnExportExcel");
        if (btn) btn.click();
      } else if (act.type === "refresh_tab") {
        if (typeof window.refreshCurrentTab === "function") window.refreshCurrentTab();
      }
    });
    setTimeout(updateContextStrip, 250);
  }

  // ── Inline Chart & Table Renderers ──────────────────────────────────
  function renderChartInto(canvasId, chartSpec) {
    if (typeof Chart === "undefined" || !chartSpec || !chartSpec.labels) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const prev = state.charts.get(canvasId);
    if (prev) { try { prev.destroy(); } catch (_) {} }

    const palette = ["#e85c14", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4", "#f43f5e", "#84cc16"];
    const datasets = (chartSpec.datasets || []).map((ds, idx) => ({
      label: ds.label || "Value",
      data: ds.data || [],
      backgroundColor: chartSpec.type === "doughnut" || chartSpec.type === "pie"
        ? palette
        : palette[idx % palette.length] + "cc",
      borderColor: palette[idx % palette.length],
      borderWidth: 1.5,
      borderRadius: 6,
      tension: 0.3,
    }));

    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    const tickColor = isLight ? "#475569" : "#94a3b8";
    const gridColor = isLight ? "rgba(15,23,42,0.08)" : "rgba(255,255,255,0.07)";

    const chart = new Chart(canvas.getContext("2d"), {
      type: chartSpec.type || "bar",
      data: { labels: chartSpec.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: tickColor, font: { size: 11 } } },
        },
        scales: chartSpec.type === "doughnut" || chartSpec.type === "pie" ? {} : {
          x: { ticks: { color: tickColor, font: { size: 10 } }, grid: { display: false } },
          y: { ticks: { color: tickColor, font: { size: 10 } }, grid: { color: gridColor } },
        },
      },
    });
    state.charts.set(canvasId, chart);
  }

  function buildTableHtml(tbl) {
    if (!tbl || !Array.isArray(tbl.columns)) return "";
    const expBtns = tbl.export_token
      ? `<div style="display:flex;gap:6px;">
           <a class="ai-icon-btn" style="text-decoration:none;font-size:11px;" href="/api/ai/export/${esc(tbl.export_token)}?format=xlsx" target="_blank">⬇ Excel</a>
           <a class="ai-icon-btn" style="text-decoration:none;font-size:11px;" href="/api/ai/export/${esc(tbl.export_token)}?format=csv" target="_blank">⬇ CSV</a>
         </div>`
      : "";
    const thead = `<tr>${tbl.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
    const tbody = (tbl.rows || []).map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("");
    return `
      <div class="ai-inline-card">
        <div class="ai-inline-card-hdr">
          <span>${esc(tbl.title || "Result Table")}</span>
          ${expBtns}
        </div>
        <div class="ai-inline-table-wrap">
          <table class="ai-inline-table">
            <thead>${thead}</thead>
            <tbody>${tbody}</tbody>
          </table>
        </div>
      </div>`;
  }

  function buildConfirmCardHtml(act) {
    const diffText = act.diff && act.diff.unified_diff
      ? act.diff.unified_diff
      : JSON.stringify(act.diff || {}, null, 2);
    const pinHtml = act.requires_pin
      ? `<input type="password" class="ai-pin-input" placeholder="Admin PIN..." data-pin-for="${esc(act.action_id)}">`
      : "";
    return `
      <div class="ai-confirm-card" data-action-card="${esc(act.action_id)}">
        <h4>🛠 Edit Preview — ${esc(act.title || act.edit_type)}</h4>
        <div style="font-size:12px;opacity:0.9;">${esc(act.summary || "")}</div>
        <div class="ai-confirm-diff">${esc(diffText)}</div>
        <div class="ai-confirm-actions">
          ${pinHtml}
          <button class="ai-send-btn" style="padding:6px 12px;font-size:12px;" onclick="window.MyGAI.confirmAction('${esc(act.action_id)}')">✓ Approve & Apply</button>
          <button class="ai-icon-btn" onclick="this.closest('.ai-confirm-card').remove()">✕ Cancel</button>
        </div>
      </div>`;
  }

  // ── Core Streaming Chat Send ────────────────────────────────────────
  async function sendChat(message, containerPrefix, forceDeep) {
    const text = String(message || "").trim();
    if (!text || state.isStreaming) return;
    state.isStreaming = true;
    state.lastUserQuestion = text;

    const msgBox = document.getElementById(`${containerPrefix}-messages`);
    const sendBtn = document.getElementById(`${containerPrefix}-send`);
    if (sendBtn) sendBtn.disabled = true;

    // Render User Bubble
    const uDiv = document.createElement("div");
    uDiv.className = "ai-msg user";
    uDiv.textContent = text;
    msgBox.appendChild(uDiv);

    // Render Assistant Placeholder Bubble
    const aDiv = document.createElement("div");
    aDiv.className = "ai-msg assistant";
    const stepsEl = document.createElement("div");
    stepsEl.className = "ai-steps-box";
    const bodyEl = document.createElement("div");
    bodyEl.innerHTML = `<span style="opacity:0.65;">Analyzing live ClickHouse data…</span>`;
    const extrasEl = document.createElement("div");
    const footEl = document.createElement("div");
    footEl.className = "ai-msg-footer";
    footEl.style.display = "none";

    aDiv.appendChild(stepsEl);
    aDiv.appendChild(bodyEl);
    aDiv.appendChild(extrasEl);
    aDiv.appendChild(footEl);
    msgBox.appendChild(aDiv);
    msgBox.scrollTop = msgBox.scrollHeight;

    const stepMap = new Map();
    let lastSql = "";

    try {
      const resp = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          conversation_id: state.conversationId,
          context: getPortalContext(),
          deep: !!forceDeep,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const chunk of parts) {
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let ev = null;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }

          if (ev.type === "meta" && ev.conversation_id) {
            state.conversationId = ev.conversation_id;
          } else if (ev.type === "status") {
            const pill = document.createElement("span");
            pill.className = "ai-step-pill";
            pill.textContent = ev.text || "Working...";
            stepsEl.appendChild(pill);
          } else if (ev.type === "tool_start") {
            const pill = document.createElement("span");
            pill.className = "ai-step-pill";
            pill.textContent = `⚡ ${ev.label || ev.tool}`;
            stepsEl.appendChild(pill);
            stepMap.set(ev.tool, pill);
          } else if (ev.type === "tool_done") {
            const pill = stepMap.get(ev.tool);
            if (pill) {
              pill.classList.add("done");
              pill.textContent = `✓ ${ev.tool}${ev.summary ? " (" + ev.summary + ")" : ""}`;
            }
          } else if (ev.type === "chart" && ev.chart) {
            const cid = `ai-chart-${Date.now()}-${Math.floor(Math.random() * 9999)}`;
            const wrap = document.createElement("div");
            wrap.className = "ai-inline-card";
            wrap.innerHTML = `<div class="ai-inline-card-hdr"><span>${esc(ev.chart.title || "Chart")}</span></div><div class="ai-inline-chart-wrap"><canvas id="${cid}"></canvas></div>`;
            extrasEl.appendChild(wrap);
            setTimeout(() => renderChartInto(cid, ev.chart), 40);
          } else if (ev.type === "table" && ev.table) {
            const wrap = document.createElement("div");
            wrap.innerHTML = buildTableHtml(ev.table);
            extrasEl.appendChild(wrap);
          } else if (ev.type === "ui_actions" && ev.actions) {
            executeUIActions(ev.actions);
          } else if (ev.type === "pending_action" && ev.action) {
            const wrap = document.createElement("div");
            wrap.innerHTML = buildConfirmCardHtml(ev.action);
            extrasEl.appendChild(wrap);
          } else if (ev.type === "answer") {
            lastSql = ev.sql || "";
            bodyEl.innerHTML = renderMarkdown(ev.text || "");
            const metaLabel = ev.mode === "llm"
              ? `${esc(ev.provider || "LLM")} · ${esc(ev.model || "")}`
              : "Built-in Fast Analytics";
            footEl.style.display = "flex";
            footEl.innerHTML = `
              <span>${metaLabel}${lastSql ? " · SQL verified" : ""}</span>
              <div class="ai-fb-btns">
                <button class="ai-fb-btn" onclick="window.MyGAI.sendFeedback(this, 'up', ${JSON.stringify(lastSql)})" title="Helpful (teaches AI)">👍 Helpful</button>
                <button class="ai-fb-btn" onclick="window.MyGAI.sendFeedback(this, 'down', '')" title="Report issue">👎</button>
              </div>`;
          } else if (ev.type === "error") {
            bodyEl.innerHTML = `<span style="color:#f43f5e;">Error: ${esc(ev.error)}</span>`;
          }
          msgBox.scrollTop = msgBox.scrollHeight;
        }
      }
      loadConversationsList();
    } catch (err) {
      bodyEl.innerHTML = `<span style="color:#f43f5e;">Request failed: ${esc(err.message || err)}</span>`;
    } finally {
      state.isStreaming = false;
      if (sendBtn) sendBtn.disabled = false;
      msgBox.scrollTop = msgBox.scrollHeight;
    }
  }

  // ── Confirm Pending Tier-2 / Tier-3 Action ──────────────────────────
  async function confirmAction(actionId) {
    const card = document.querySelector(`[data-action-card="${actionId}"]`);
    const pinInput = document.querySelector(`[data-pin-for="${actionId}"]`);
    const pin = pinInput ? pinInput.value : "";

    try {
      const r = await fetch("/api/ai/confirm-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: actionId, admin_pin: pin }),
      });
      const data = await r.json();
      if (!r.ok || data.status !== "success") {
        toast(data.error || "Edit failed", "error");
        return;
      }
      if (card) {
        card.innerHTML = `<div style="color:#10b981;font-weight:700;font-size:13px;">✓ ${esc(data.message)}</div>`;
      }
      toast(data.message || "Dashboard updated ✓", "success");
      if (data.reload_ui) {
        await loadAndRenderDashboardConfig();
      }
      if (data.reload_targets && typeof window.refreshCurrentTab === "function") {
        window.refreshCurrentTab();
      }
      if (data.reload_page) {
        setTimeout(() => location.reload(), 900);
      }
    } catch (e) {
      toast(`Failed to apply edit: ${e.message}`, "error");
    }
  }

  // ── Tier-2 Persistent Dashboard Overlay & Custom Widgets ────────────
  async function loadAndRenderDashboardConfig() {
    const ctx = getPortalContext();
    const qs = new URLSearchParams({
      date: ctx.date || "",
      timeframe: ctx.timeframe || "monthly",
      end_date: ctx.end_date || "",
      state: ctx.state || "KERALA",
      evaluate: "1",
    });
    try {
      const r = await fetch(`/api/ai/dashboard-config?${qs.toString()}`);
      if (!r.ok) return;
      const data = await r.json();
      state.dashboardConfig = data;
      renderPinnedWidgetsAcrossTabs(data.evaluated_widgets || []);
      applyLayoutOverlays(data.config || {});
      renderStudioWidgetsPanel(data);
    } catch (e) {
      console.warn("[myG AI] dashboard-config load skipped:", e);
    }
  }

  function renderPinnedWidgetsAcrossTabs(widgets) {
    // Clear previous hosts
    document.querySelectorAll(".ai-pinned-widgets-host").forEach((el) => el.remove());
    if (!widgets.length) return;

    const byTab = {};
    widgets.forEach((w) => {
      const t = w.tab || "dashboard";
      (byTab[t] = byTab[t] || []).push(w);
    });

    Object.entries(byTab).forEach(([tabId, list]) => {
      const tabEl = document.getElementById(`tab-${tabId}`);
      if (!tabEl) return;
      const host = document.createElement("div");
      host.className = "ai-pinned-widgets-host";

      list.forEach((w) => {
        const card = document.createElement("div");
        card.className = "ai-pinned-widget";
        let inner = "";
        if (w.error) {
          inner = `<div style="color:#f43f5e;font-size:12px;">${esc(w.error)}</div>`;
        } else if (w.widget_type === "kpi" && w.value !== undefined) {
          inner = `<div class="ai-stat-val">${esc(w.value)}</div><div class="ai-stat-sub">${esc(w.subvalue || w.subtitle || "")}</div>`;
        } else if (w.widget_type === "chart" && w.chart) {
          const cid = `pinned-chart-${w.id}-${Date.now()}`;
          inner = `<div style="height:210px;position:relative;"><canvas id="${cid}"></canvas></div>`;
          setTimeout(() => renderChartInto(cid, w.chart), 60);
        } else if (w.table) {
          inner = buildTableHtml(w.table);
        } else {
          inner = `<div style="opacity:0.6;font-size:12px;">No data for active period.</div>`;
        }
        card.innerHTML = `
          <div class="ai-pinned-widget-hdr">
            <div class="ai-pinned-widget-title">
              <span>${esc(w.title || "Custom Widget")}</span>
              <span class="ai-pinned-tag">AI Studio</span>
            </div>
            <button class="ai-icon-btn" title="Remove widget" onclick="window.MyGAI.deleteWidget('${esc(w.id)}')">✕</button>
          </div>
          ${inner}`;
        host.appendChild(card);
      });
      tabEl.prepend(host);
    });
  }

  function applyLayoutOverlays(cfg) {
    // Hidden sections
    const hidden = new Set(cfg.hidden_sections || []);
    document.querySelectorAll("[data-ai-section]").forEach((el) => {
      const sid = el.getAttribute("data-ai-section");
      el.style.display = hidden.has(sid) ? "none" : "";
    });
    // Custom titles
    Object.entries(cfg.custom_titles || {}).forEach(([selKey, title]) => {
      const el = document.getElementById(selKey);
      if (el) el.textContent = title;
    });
  }

  // ── Deep Insights & Executive Morning Brief Tab ─────────────────────
  async function loadExecutiveBrief() {
    const out = document.getElementById("ai-brief-output");
    if (!out) return;
    out.innerHTML = `<div class="ai-stat-card" style="text-align:center;padding:30px;">⏳ Running 7-axis ClickHouse diagnostic across Stores, TMs, Zones, Categories, Financiers & ERP Hygiene…</div>`;

    const ctx = getPortalContext();
    try {
      const r = await fetch("/api/ai/brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ctx),
      });
      const data = await r.json();
      if (!r.ok || data.status !== "success") {
        out.innerHTML = `<div class="ai-stat-card" style="color:#f43f5e;">Failed: ${esc(data.error || "No data")}</div>`;
        return;
      }

      const vb = data.variance_bridge || {};
      const audit = data.erp_hygiene_audit || {};
      const alerts = data.alerts || [];
      const drops = data.biggest_store_drops || [];
      const gains = data.biggest_store_gains || [];

      const chartId = `ai-brief-tm-chart-${Date.now()}`;
      out.innerHTML = `
        <div class="ai-kpi-grid">
          <div class="ai-stat-card">
            <div class="ai-stat-label">Total Finance</div>
            <div class="ai-stat-val">₹${vb.cur_fin_cr} Cr</div>
            <div class="ai-stat-sub">Paper ₹${vb.cur_paper_cr} Cr · Card ₹${vb.cur_card_cr} Cr</div>
          </div>
          <div class="ai-stat-card">
            <div class="ai-stat-label">Fin Share (Value)</div>
            <div class="ai-stat-val">${vb.cur_fin_share_pct}%</div>
            <div class="ai-stat-sub">vs ${esc(data.compare_label)}: <strong style="color:${vb.delta_share_pp >= 0 ? "#10b981" : "#f43f5e"}">${vb.delta_share_pp >= 0 ? "+" : ""}${vb.delta_share_pp} pp</strong></div>
          </div>
          <div class="ai-stat-card">
            <div class="ai-stat-label">Variance Bridge</div>
            <div class="ai-stat-val" style="color:${vb.delta_fin_cr >= 0 ? "#10b981" : "#f43f5e"}">${vb.delta_fin_cr >= 0 ? "+" : ""}₹${vb.delta_fin_cr} Cr</div>
            <div class="ai-stat-sub">Vol: ${vb.sales_volume_effect_cr >= 0 ? "+" : ""}${vb.sales_volume_effect_cr} Cr · Conv: ${vb.fin_share_conversion_effect_cr >= 0 ? "+" : ""}${vb.fin_share_conversion_effect_cr} Cr</div>
          </div>
          <div class="ai-stat-card">
            <div class="ai-stat-label">North vs South</div>
            <div class="ai-stat-val">${vb.north_fin_share_pct}% · ${vb.south_fin_share_pct}%</div>
            <div class="ai-stat-sub">Top-8 Recovery Upside: <strong style="color:#10b981">+₹${data.total_top8_recovery_cr} Cr</strong></div>
          </div>
          <div class="ai-stat-card">
            <div class="ai-stat-label">ERP Hygiene Audit</div>
            <div class="ai-stat-val">${audit.mismatch_rows || 0}</div>
            <div class="ai-stat-sub">DO Mismatches (₹${audit.mismatch_lakhs || 0} L) · ${audit.rescued_rows || 0} Rescued</div>
          </div>
        </div>

        ${alerts.length ? `<div class="ai-alert-list">${alerts.map((a) => `<div class="ai-alert-item">⚠️ ${esc(a)}</div>`).join("")}</div>` : ""}

        <div class="ai-two-col">
          <div class="ai-inline-card">
            <div class="ai-inline-card-hdr"><span>Fin BDM (TM) Fin Share %</span></div>
            <div class="ai-inline-chart-wrap" style="height:250px;"><canvas id="${chartId}"></canvas></div>
          </div>
          <div>${buildTableHtml(data.table)}</div>
        </div>

        <div class="ai-two-col" style="margin-top:14px;">
          <div>${buildTableHtml({
            title: "Steepest Store Fin Share Drops vs Prior Period",
            columns: ["Store", "TM", "Cur %", "Prev %", "Diff", "Sales Cr"],
            rows: drops.map((x) => [x.store, x.fin_bdm, `${x.fin_share_pct}%`, `${x.prev_share_pct}%`, `${x.diff_pp > 0 ? "+" : ""}${x.diff_pp}pp`, x.tot_cr]),
          })}</div>
          <div>${buildTableHtml({
            title: "Top Store Fin Share Gainers vs Prior Period",
            columns: ["Store", "TM", "Cur %", "Prev %", "Diff", "Sales Cr"],
            rows: gains.map((x) => [x.store, x.fin_bdm, `${x.fin_share_pct}%`, `${x.prev_share_pct}%`, `${x.diff_pp > 0 ? "+" : ""}${x.diff_pp}pp`, x.tot_cr]),
          })}</div>
        </div>
      `;
      if (data.chart) setTimeout(() => renderChartInto(chartId, data.chart), 50);
    } catch (e) {
      out.innerHTML = `<div class="ai-stat-card" style="color:#f43f5e;">Brief failed: ${esc(e.message)}</div>`;
    }
  }

  // ── Dashboard Studio Panel (Tier 2 UI Manager) ──────────────────────
  function renderStudioWidgetsPanel(data) {
    const listEl = document.getElementById("ai-studio-widgets-list");
    const histEl = document.getElementById("ai-studio-history-list");
    const undoBtn = document.getElementById("btn-ai-studio-undo");
    if (undoBtn) undoBtn.disabled = !data.can_undo;
    if (!listEl) return;

    const widgets = (data.config && data.config.widgets) || [];
    if (!widgets.length) {
      listEl.innerHTML = `<div style="opacity:0.65;font-size:13px;padding:12px;">No custom widgets pinned yet. Create one below or ask the AI: <em>"Pin a Bottom 10 Stores table to Dashboard"</em>.</div>`;
    } else {
      listEl.innerHTML = widgets.map((w) => `
        <div class="ai-conv-item" style="margin-bottom:6px;border-color:var(--border-soft);">
          <div>
            <strong>${esc(w.title)}</strong>
            <div style="font-size:11px;opacity:0.7;">Tab: <code>${esc(w.tab)}</code> · Type: <code>${esc(w.widget_type)}</code> · Source: <code>${esc(w.metric_source)}</code></div>
          </div>
          <button class="ai-icon-btn" onclick="window.MyGAI.deleteWidget('${esc(w.id)}')">🗑 Remove</button>
        </div>
      `).join("");
    }

    if (histEl) {
      const hist = data.history || [];
      histEl.innerHTML = hist.length
        ? hist.map((h) => `<div style="font-size:12px;padding:6px 0;border-bottom:1px solid var(--border-soft);"><strong>v${h.version}</strong> — ${esc(h.change_summary)} <span style="opacity:0.55;">(${esc(h.updated_at)})</span></div>`).join("")
        : `<div style="font-size:12px;opacity:0.6;">No edit history yet.</div>`;
    }
  }

  async function createWidgetFromStudio() {
    const title = (document.getElementById("studio-w-title") || {}).value || "";
    const tab = (document.getElementById("studio-w-tab") || {}).value || "dashboard";
    const widget_type = (document.getElementById("studio-w-type") || {}).value || "chart";
    const metric_source = (document.getElementById("studio-w-source") || {}).value || "financier";
    const sql = (document.getElementById("studio-w-sql") || {}).value || "";
    const pin = (document.getElementById("studio-admin-pin") || {}).value || "";
    if (!title.trim()) { toast("Enter a widget title", "error"); return; }

    const r = await fetch("/api/ai/dashboard-config/widget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ widget: { title, tab, widget_type, metric_source, sql }, admin_pin: pin }),
    });
    const d = await r.json();
    if (!r.ok || d.status !== "success") {
      toast(d.error || "Could not create widget", "error");
      return;
    }
    toast(`Pinned "${title}" to ${tab} ✓`, "success");
    await loadAndRenderDashboardConfig();
  }

  async function deleteWidget(wid) {
    const pin = (document.getElementById("studio-admin-pin") || {}).value || "";
    const r = await fetch(`/api/ai/dashboard-config/widget/${encodeURIComponent(wid)}?admin_pin=${encodeURIComponent(pin)}`, { method: "DELETE" });
    const d = await r.json();
    if (!r.ok) { toast(d.error || "Delete failed (check Admin PIN)", "error"); return; }
    toast("Widget removed (Undo available)", "info");
    await loadAndRenderDashboardConfig();
  }

  async function undoDashboardEdit() {
    const pin = (document.getElementById("studio-admin-pin") || {}).value || "";
    const r = await fetch("/api/ai/dashboard-config/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_pin: pin }),
    });
    const d = await r.json();
    if (!r.ok) { toast(d.error || "Undo failed", "error"); return; }
    toast("Reverted to previous dashboard layout ✓", "success");
    await loadAndRenderDashboardConfig();
  }

  // ── Memory, Conversations, Feedback & Settings ──────────────────────
  async function loadMemoryPanel() {
    const listEl = document.getElementById("ai-memory-list");
    const gqEl = document.getElementById("ai-golden-list");
    if (!listEl) return;
    try {
      const r = await fetch("/api/ai/memory");
      const d = await r.json();
      const mems = d.memories || [];
      listEl.innerHTML = mems.length
        ? mems.map((m) => `
          <div class="ai-conv-item" style="margin-bottom:6px;border-color:var(--border-soft);align-items:flex-start;">
            <div>
              <div><strong>${esc(m.key_topic)}</strong> <span class="ai-pinned-tag">${esc(m.category)}</span></div>
              <div style="font-size:12px;opacity:0.85;margin-top:2px;">${esc(m.content)}</div>
            </div>
            <button class="ai-icon-btn" onclick="window.MyGAI.deleteMemory('${esc(m.id)}')">✕</button>
          </div>`).join("")
        : `<div style="opacity:0.6;font-size:12.5px;">No learned memories yet. Teach the AI in chat ("Remember that Vypin Future was closed Oct 5–12") or add a rule below.</div>`;

      if (gqEl) {
        const gqs = d.golden_queries || [];
        gqEl.innerHTML = gqs.length
          ? gqs.map((g) => `<div style="padding:8px;border:1px solid var(--border-soft);border-radius:8px;margin-bottom:6px;font-size:12px;"><strong>${esc(g.question_pattern)}</strong><pre style="margin:4px 0 0;font-size:11px;white-space:pre-wrap;">${esc(g.sql_query)}</pre></div>`).join("")
          : `<div style="opacity:0.6;font-size:12px;">Click 👍 on any SQL answer to save it into the Golden Query Library.</div>`;
      }
    } catch (_) {}
  }

  async function addMemoryFromUI() {
    const topic = (document.getElementById("mem-topic") || {}).value || "";
    const content = (document.getElementById("mem-content") || {}).value || "";
    const category = (document.getElementById("mem-cat") || {}).value || "business_rule";
    if (!topic.trim() || !content.trim()) { toast("Enter topic and rule content", "error"); return; }
    const r = await fetch("/api/ai/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_topic: topic, content, category }),
    });
    if (r.ok) {
      document.getElementById("mem-topic").value = "";
      document.getElementById("mem-content").value = "";
      toast("Memory saved to ClickHouse ✓", "success");
      loadMemoryPanel();
    }
  }

  async function deleteMemory(mid) {
    await fetch(`/api/ai/memory/${encodeURIComponent(mid)}`, { method: "DELETE" });
    toast("Memory deleted", "info");
    loadMemoryPanel();
  }

  async function loadConversationsList() {
    const listEl = document.getElementById("ai-conv-list");
    if (!listEl) return;
    try {
      const r = await fetch("/api/ai/conversations");
      const d = await r.json();
      const convs = d.conversations || [];
      listEl.innerHTML = convs.length
        ? convs.map((c) => `
          <div class="ai-conv-item ${c.conv_id === state.conversationId ? "active" : ""}" onclick="window.MyGAI.openConversation('${esc(c.conv_id)}')">
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(c.title)}</span>
            <button class="ai-icon-btn" style="padding:2px 6px;" onclick="event.stopPropagation();window.MyGAI.deleteConversation('${esc(c.conv_id)}')">✕</button>
          </div>`).join("")
        : `<div style="opacity:0.55;font-size:12px;">No saved chats yet.</div>`;
    } catch (_) {}
  }

  async function openConversation(cid) {
    const r = await fetch(`/api/ai/conversations/${encodeURIComponent(cid)}`);
    if (!r.ok) return;
    const conv = await r.json();
    state.conversationId = conv.conv_id;
    ["drawer", "studio"].forEach((prefix) => {
      const box = document.getElementById(`${prefix}-messages`);
      if (!box) return;
      box.innerHTML = "";
      (conv.messages || []).forEach((m) => {
        const div = document.createElement("div");
        div.className = `ai-msg ${m.role === "user" ? "user" : "assistant"}`;
        div.innerHTML = m.role === "user" ? esc(m.content) : renderMarkdown(m.content);
        if (m.tables && m.tables.length) {
          m.tables.forEach((t) => { div.innerHTML += buildTableHtml(t); });
        }
        box.appendChild(div);
      });
      box.scrollTop = box.scrollHeight;
    });
    loadConversationsList();
  }

  async function deleteConversation(cid) {
    await fetch(`/api/ai/conversations/${encodeURIComponent(cid)}`, { method: "DELETE" });
    if (state.conversationId === cid) newChat();
    loadConversationsList();
  }

  function newChat() {
    state.conversationId = "";
    ["drawer", "studio"].forEach((prefix) => {
      const box = document.getElementById(`${prefix}-messages`);
      if (box) {
        box.innerHTML = `
          <div class="ai-msg assistant">
            <strong>myG Finance AI Ready</strong> — Ask any question on live ClickHouse data, run a 7-axis deep analysis, control tabs & filters, pin custom KPI widgets, or teach me business rules ("Remember that...").
          </div>`;
      }
    });
    loadConversationsList();
  }

  async function sendFeedback(btn, rating, sql) {
    btn.classList.add("voted");
    await fetch("/api/ai/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, question: state.lastUserQuestion, sql }),
    });
    toast(rating === "up" ? "Thanks! Pattern saved to AI learning." : "Feedback recorded.", "success");
    loadMemoryPanel();
  }

  async function loadAIStatus() {
    try {
      const r = await fetch("/api/ai/status");
      const d = await r.json();
      state.status = d;
      document.querySelectorAll(".ai-mode-pill").forEach((el) => {
        if (d.mode === "llm") {
          el.className = "ai-mode-pill";
          el.textContent = `● ${d.provider.toUpperCase()} (${d.fast_model})`;
        } else {
          el.className = "ai-mode-pill builtin";
          el.textContent = "● Built-in Fast Analytics (Add API Key for Full LLM)";
        }
      });
      const provEl = document.getElementById("ai-set-provider");
      const fastEl = document.getElementById("ai-set-fast");
      const deepEl = document.getElementById("ai-set-deep");
      const baseEl = document.getElementById("ai-set-baseurl");
      const pinSt = document.getElementById("ai-pin-status");
      if (provEl) provEl.value = d.provider || "gemini";
      if (fastEl) fastEl.value = d.fast_model || "";
      if (deepEl) deepEl.value = d.deep_model || "";
      if (baseEl) baseEl.value = d.base_url || "";
      if (pinSt) pinSt.textContent = d.pin_enabled ? "🔒 Admin PIN Lock ACTIVE" : "🔓 Admin PIN Lock OFF (Preview + Confirm still required)";
    } catch (_) {}
  }

  async function saveAISettings() {
    const provider = (document.getElementById("ai-set-provider") || {}).value || "gemini";
    const apiKey = (document.getElementById("ai-set-apikey") || {}).value || "";
    const fast_model = (document.getElementById("ai-set-fast") || {}).value || "";
    const deep_model = (document.getElementById("ai-set-deep") || {}).value || "";
    const base_url = (document.getElementById("ai-set-baseurl") || {}).value || "";
    const admin_pin = (document.getElementById("ai-set-newpin") || {}).value || "";
    const current_admin_pin = (document.getElementById("ai-set-curpin") || {}).value || "";

    const body = { provider, fast_model, deep_model, base_url, current_admin_pin };
    if (apiKey.trim()) body[`${provider}_api_key`] = apiKey.trim();
    if (admin_pin.trim()) body.admin_pin = admin_pin.trim();

    const r = await fetch("/api/ai/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) { toast(d.error || "Settings save failed", "error"); return; }
    if (document.getElementById("ai-set-apikey")) document.getElementById("ai-set-apikey").value = "";
    if (document.getElementById("ai-set-newpin")) document.getElementById("ai-set-newpin").value = "";
    toast("AI Settings saved in ClickHouse ✓", "success");
    await loadAIStatus();
  }

  async function testAIProvider() {
    toast("Testing LLM connection...", "info");
    const r = await fetch("/api/ai/test-provider", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      toast(`Connected to ${d.provider} (${d.model}) ✓`, "success");
    } else {
      toast(`Connection failed: ${d.error}`, "error");
    }
  }

  // ── Drawer Open / Close / Subtabs ───────────────────────────────────
  function openDrawer() {
    updateContextStrip();
    document.getElementById("aiDrawerBackdrop")?.classList.add("open");
    document.getElementById("aiDrawer")?.classList.add("open");
    setTimeout(() => document.getElementById("drawer-input")?.focus(), 180);
  }
  function closeDrawer() {
    document.getElementById("aiDrawerBackdrop")?.classList.remove("open");
    document.getElementById("aiDrawer")?.classList.remove("open");
  }
  function toggleDrawerWide() {
    document.getElementById("aiDrawer")?.classList.toggle("wide");
  }
  function switchStudioSubtab(sub) {
    document.querySelectorAll(".ai-subtab-btn").forEach((b) => b.classList.toggle("active", b.dataset.sub === sub));
    document.querySelectorAll(".ai-subview").forEach((v) => v.classList.toggle("active", v.id === `ai-sub-${sub}`));
    if (sub === "brief") loadExecutiveBrief();
    if (sub === "studio") loadAndRenderDashboardConfig();
    if (sub === "memory") { loadMemoryPanel(); loadAIStatus(); }
  }

  function wireComposer(prefix) {
    const inp = document.getElementById(`${prefix}-input`);
    const btn = document.getElementById(`${prefix}-send`);
    const deepChk = document.getElementById(`${prefix}-deep`);
    if (!inp || !btn) return;
    const go = () => {
      const v = inp.value.trim();
      if (!v) return;
      inp.value = "";
      sendChat(v, prefix, deepChk ? deepChk.checked : false);
    };
    btn.addEventListener("click", go);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        go();
      }
    });
  }

  // ── Boot & Public API ───────────────────────────────────────────────
  function init() {
    wireComposer("drawer");
    wireComposer("studio");

    document.getElementById("btnOpenAICopilot")?.addEventListener("click", openDrawer);
    document.getElementById("aiDrawerBackdrop")?.addEventListener("click", closeDrawer);

    // Quick prompt chips
    document.querySelectorAll(".ai-chip[data-prompt]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const prefix = chip.dataset.target || "drawer";
        const deep = chip.dataset.deep === "1";
        sendChat(chip.dataset.prompt, prefix, deep);
      });
    });

    // Global shortcut Ctrl+K / Cmd+K
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const dr = document.getElementById("aiDrawer");
        if (dr && dr.classList.contains("open")) closeDrawer();
        else openDrawer();
      } else if (e.key === "Escape") {
        closeDrawer();
      }
    });

    // Show SQL box only when metric_source === 'sql'
    const srcSel = document.getElementById("studio-w-source");
    const sqlRow = document.getElementById("studio-w-sql-row");
    if (srcSel && sqlRow) {
      const syncSql = () => { sqlRow.style.display = srcSel.value === "sql" ? "flex" : "none"; };
      srcSel.addEventListener("change", syncSql);
      syncSql();
    }

    // Re-update context strip whenever nav/period changes
    document.querySelectorAll(".nav-item").forEach((n) => {
      n.addEventListener("click", () => {
        setTimeout(() => {
          updateContextStrip();
          if (n.getAttribute("data-tab") === "ai-studio") {
            loadAIStatus();
            loadConversationsList();
          }
        }, 120);
      });
    });

    newChat();
    loadAIStatus();
    setTimeout(() => {
      updateContextStrip();
      loadAndRenderDashboardConfig();
    }, 1500);
  }

  window.MyGAI = {
    openDrawer,
    closeDrawer,
    toggleDrawerWide,
    switchStudioSubtab,
    sendChat,
    newChat,
    confirmAction,
    loadExecutiveBrief,
    loadAndRenderDashboardConfig,
    createWidgetFromStudio,
    deleteWidget,
    undoDashboardEdit,
    addMemoryFromUI,
    deleteMemory,
    openConversation,
    deleteConversation,
    sendFeedback,
    saveAISettings,
    testAIProvider,
    onPeriodChanged: () => {
      updateContextStrip();
      loadAndRenderDashboardConfig();
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
