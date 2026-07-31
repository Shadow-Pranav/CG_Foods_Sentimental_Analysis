(function () {
  "use strict";

  const COLORS = {
    positive: "#4B6B43",
    negative: "#A8122B",
    neutral: "#91733F",
    gold: "#C79A3E",
    text: "#221E1A",
    textMuted: "#6B6255",
    border: "#DBD2C0",
  };

  const EVENT_LINE_COLOR = {
    price_hike: COLORS.negative,
    quality_controversy: COLORS.negative,
    litigation: COLORS.gold,
    competitor_launch: COLORS.gold,
    market_expansion: COLORS.gold,
  };

  if (window.Chart) {
    Chart.defaults.font.family = "'Source Sans 3', -apple-system, sans-serif";
    Chart.defaults.font.size = 11.5;
    Chart.defaults.color = COLORS.textMuted;
    Chart.defaults.borderColor = COLORS.border;
  }

  const state = {
    platforms: new Set(["youtube", "twitter", "facebook"]),
    sentiments: new Set(["positive", "negative", "neutral"]),
    regions: new Set(["nepal", "india", "unknown"]),
    startDate: null,
    endDate: null,
    q: "",
    page: 1,
    pageSize: 25,
  };

  let charts = { timeline: null, theme: null, byPlatform: {}, byRegion: {} };
  let latestTimelineEvents = [];
  let eventSignificance = {}; // event_id -> { significant, p_value }

  function buildParams(extra) {
    const p = new URLSearchParams();
    p.set("platform", Array.from(state.platforms).join(","));
    p.set("sentiment", Array.from(state.sentiments).join(","));
    p.set("region", Array.from(state.regions).join(","));
    if (state.startDate) p.set("start_date", state.startDate);
    if (state.endDate) p.set("end_date", state.endDate);
    if (state.q) p.set("q", state.q);
    if (extra) {
      Object.keys(extra).forEach((k) => p.set(k, extra[k]));
    }
    return p;
  }

  function fetchJSON(url) {
    return fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${url} -> ${r.status}`);
      return r.json();
    });
  }

  // -------------------------------------------------------------- init

  function init() {
    document.querySelectorAll(".filter-platform").forEach((el) => {
      el.addEventListener("change", () => {
        syncSetFromCheckboxes(".filter-platform", state.platforms);
        state.page = 1;
        refreshAll();
      });
    });
    document.querySelectorAll(".filter-sentiment").forEach((el) => {
      el.addEventListener("change", () => {
        syncSetFromCheckboxes(".filter-sentiment", state.sentiments);
        state.page = 1;
        refreshAll();
      });
    });
    document.querySelectorAll(".filter-region").forEach((el) => {
      el.addEventListener("change", () => {
        syncSetFromCheckboxes(".filter-region", state.regions);
        state.page = 1;
        refreshAll();
      });
    });

    let searchTimer = null;
    document.getElementById("keyword-search").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.q = e.target.value.trim();
        state.page = 1;
        refreshAll();
      }, 300);
    });

    document.getElementById("start-date").addEventListener("change", (e) => {
      state.startDate = e.target.value || null;
      state.page = 1;
      refreshAll();
    });
    document.getElementById("end-date").addEventListener("change", (e) => {
      state.endDate = e.target.value || null;
      state.page = 1;
      refreshAll();
    });

    document.getElementById("reset-range").addEventListener("click", () => {
      const startEl = document.getElementById("start-date");
      const endEl = document.getElementById("end-date");
      state.startDate = startEl.dataset.min;
      state.endDate = endEl.dataset.max;
      startEl.value = state.startDate;
      endEl.value = state.endDate;
      state.page = 1;
      refreshAll();
    });

    document.getElementById("clear-filters").addEventListener("click", () => {
      document.querySelectorAll(".filter-platform").forEach((el) => (el.checked = true));
      document.querySelectorAll(".filter-sentiment").forEach((el) => (el.checked = true));
      document.querySelectorAll(".filter-region").forEach((el) => (el.checked = true));
      document.getElementById("keyword-search").value = "";
      state.platforms = new Set(["youtube", "twitter", "facebook"]);
      state.sentiments = new Set(["positive", "negative", "neutral"]);
      state.regions = new Set(["nepal", "india", "unknown"]);
      state.q = "";
      document.getElementById("reset-range").click();
    });

    document.getElementById("page-prev").addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        refreshComments();
      }
    });
    document.getElementById("page-next").addEventListener("click", () => {
      state.page += 1;
      refreshComments();
    });

    fetchJSON("/api/meta").then((meta) => {
      const startEl = document.getElementById("start-date");
      const endEl = document.getElementById("end-date");
      startEl.dataset.min = meta.min_date;
      endEl.dataset.max = meta.max_date;
      startEl.value = meta.min_date;
      endEl.value = meta.max_date;
      state.startDate = meta.min_date;
      state.endDate = meta.max_date;
      refreshAll();
      refreshRegionComparison(); // unfiltered/static, so only needs loading once
      refreshEventAnalysis(); // ditto
      refreshWordcloud(); // ditto -- analyze.py's precomputed term frequency, not filter-aware
    });
  }

  function refreshEventAnalysis() {
    return fetchJSON("/api/event-analysis").then((data) => {
      eventSignificance = {};
      data.events.forEach((e) => {
        eventSignificance[e.event_id] = { significant: e.significant, p_value: e.p_value };
      });
      if (charts.timeline) charts.timeline.update(); // redraw markers with badges now available
    });
  }

  function syncSetFromCheckboxes(selector, targetSet) {
    targetSet.clear();
    document.querySelectorAll(selector).forEach((el) => {
      if (el.checked) targetSet.add(el.value);
    });
  }

  // --------------------------------------------------------- data refresh

  function refreshAll() {
    Promise.all([
      refreshSummary(),
      refreshTimeline(),
      refreshByPlatform(),
      refreshByRegion(),
      refreshThemes(),
      refreshCompetitors(),
      refreshComments(),
    ]).catch((err) => console.error("Dashboard refresh failed:", err));
  }

  function refreshSummary() {
    return fetchJSON(`/api/summary?${buildParams()}`).then((data) => {
      document.getElementById("kpi-total").textContent = data.total_comments.toLocaleString();
      document.getElementById("kpi-positive").textContent = data.total_comments
        ? `${data.percentages.positive}%`
        : "—";
      document.getElementById("kpi-negative").textContent = data.total_comments
        ? `${data.percentages.negative}%`
        : "—";
      document.getElementById("kpi-neutral").textContent = data.total_comments
        ? `${data.percentages.neutral}%`
        : "—";
      document.getElementById("kpi-theme").textContent = data.most_discussed_theme
        ? `${data.most_discussed_theme.theme} (${data.most_discussed_theme.count})`
        : "—";

      const ew = data.engagement_weighted;
      const weightedLabel = (pct) => (ew ? `engagement-wtd ${pct}%` : "—");
      document.getElementById("kpi-positive-weighted").textContent = weightedLabel(ew && ew.percentages.positive);
      document.getElementById("kpi-negative-weighted").textContent = weightedLabel(ew && ew.percentages.negative);
      document.getElementById("kpi-neutral-weighted").textContent = weightedLabel(ew && ew.percentages.neutral);
    });
  }

  function monthLabel(period) {
    const [y, m] = period.split("-");
    const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${names[parseInt(m, 10) - 1]} ${y.slice(2)}`;
  }

  function refreshTimeline() {
    return fetchJSON(`/api/timeline?${buildParams()}`).then((data) => {
      latestTimelineEvents = data.events || [];
      const labels = data.points.map((p) => monthLabel(p.period));
      const periods = data.points.map((p) => p.period);

      const datasets = [
        { label: "Positive", data: data.points.map((p) => p.positive), borderColor: COLORS.positive, backgroundColor: COLORS.positive, tension: 0.25, pointRadius: 2 },
        { label: "Negative", data: data.points.map((p) => p.negative), borderColor: COLORS.negative, backgroundColor: COLORS.negative, tension: 0.25, pointRadius: 2 },
        { label: "Neutral", data: data.points.map((p) => p.neutral), borderColor: COLORS.neutral, backgroundColor: COLORS.neutral, tension: 0.25, pointRadius: 2 },
      ];

      if (charts.timeline) {
        charts.timeline.data.labels = labels;
        charts.timeline.data.datasets.forEach((ds, i) => (ds.data = datasets[i].data));
        charts.timeline.options.plugins.eventMarkers.periods = periods;
        charts.timeline.update();
        return;
      }

      const ctx = document.getElementById("timeline-chart").getContext("2d");
      charts.timeline = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: COLORS.border } },
          },
          plugins: {
            legend: { position: "top", align: "end", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
            tooltip: { backgroundColor: COLORS.text, titleColor: "#F6F1E9", bodyColor: "#F6F1E9" },
            eventMarkers: { periods },
          },
        },
        plugins: [eventMarkersPlugin],
      });
    });
  }

  const eventMarkersPlugin = {
    id: "eventMarkers",
    afterDraw(chart, args, opts) {
      const periods = opts.periods;
      if (!periods || !periods.length || !latestTimelineEvents.length) return;
      const { ctx, chartArea, scales } = chart;
      const xScale = scales.x;

      ctx.save();
      latestTimelineEvents.forEach((evt) => {
        const [y, m, d] = evt.date.split("-").map(Number);
        const period = `${y}-${String(m).padStart(2, "0")}`;
        const idx = periods.indexOf(period);
        if (idx === -1) return;

        const daysInMonth = new Date(y, m, 0).getDate();
        const dayFrac = (d - 1) / daysInMonth;

        const x0 = xScale.getPixelForValue(idx);
        let x;
        if (idx + 1 < periods.length) {
          const x1 = xScale.getPixelForValue(idx + 1);
          x = x0 + (x1 - x0) * dayFrac;
        } else {
          x = x0;
        }

        const sig = eventSignificance[evt.id];
        const isSignificant = sig && sig.significant;

        const color = EVENT_LINE_COLOR[evt.category] || COLORS.gold;
        ctx.strokeStyle = color;
        ctx.lineWidth = isSignificant ? 1.75 : 1;
        ctx.setLineDash(isSignificant ? [] : [3, 3]);
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);

        const labelText = isSignificant ? `${evt.label} *` : evt.label;
        ctx.save();
        ctx.translate(x + 9, chartArea.top + 4);
        ctx.rotate(Math.PI / 2);
        ctx.fillStyle = color;
        ctx.font = isSignificant
          ? "bold 10px 'Source Sans 3', sans-serif"
          : "10px 'Source Sans 3', sans-serif";
        ctx.textBaseline = "middle";
        ctx.fillText(labelText, 0, 0);
        ctx.restore();
      });
      ctx.restore();
    },
  };

  function refreshByPlatform() {
    return fetchJSON(`/api/by-platform?${buildParams()}`).then((data) => {
      data.platforms.forEach((p) => {
        document.getElementById(`n-${p.platform}`).textContent = `n=${p.total.toLocaleString()}`;
        renderDonut(charts.byPlatform, p.platform, `chart-platform-${p.platform}`, p.counts);
      });
    });
  }

  function refreshByRegion() {
    return fetchJSON(`/api/by-region?${buildParams()}`).then((data) => {
      data.regions.forEach((r) => {
        document.getElementById(`n-region-${r.region}`).textContent = `n=${r.total.toLocaleString()}`;
        renderDonut(charts.byRegion, r.region, `chart-region-${r.region}`, r.counts);
      });
    });
  }

  function renderDonut(store, key, elId, counts) {
    const el = document.getElementById(elId);
    if (!el) return;
    const data = [counts.positive, counts.negative, counts.neutral];

    if (store[key]) {
      store[key].data.datasets[0].data = data;
      store[key].update();
      return;
    }

    store[key] = new Chart(el.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: ["Positive", "Negative", "Neutral"],
        datasets: [{ data, backgroundColor: [COLORS.positive, COLORS.negative, COLORS.neutral], borderColor: "#FBF8F2", borderWidth: 2 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: COLORS.text, titleColor: "#F6F1E9", bodyColor: "#F6F1E9" },
        },
      },
    });
  }

  function refreshRegionComparison() {
    return fetchJSON("/api/region-comparison").then((data) => {
      const tbody = document.getElementById("region-comparison-tbody");
      tbody.innerHTML = "";
      data.events.forEach((evt) => {
        const nepal = evt.regions.nepal;
        const india = evt.regions.india;
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHTML(evt.label)}</td>
          <td class="col-date">${evt.date}</td>
          <td class="col-engagement">${nepal.total}</td>
          <td class="col-engagement">${nepal.negative_pct !== null ? nepal.negative_pct + "%" : "—"}</td>
          <td class="col-engagement">${india.total}</td>
          <td class="col-engagement">${india.negative_pct !== null ? india.negative_pct + "%" : "—"}</td>
        `;
        tbody.appendChild(tr);
      });
    });
  }

  const TERM_CHART_TOP_N = 12;

  function refreshWordcloud() {
    return fetchJSON("/api/wordcloud").then((data) => {
      renderTermChart("positive", data.positive);
      renderTermChart("negative", data.negative);
      renderTermChart("neutral", data.neutral);
    });
  }

  function renderTermChart(sentiment, terms) {
    const elId = `chart-terms-${sentiment}`;
    const el = document.getElementById(elId);
    if (!el) return;

    // Already sorted desc by pipeline/analyze.py; take the top N and
    // reverse so the highest-frequency term renders at the top of the
    // horizontal bar chart (Chart.js draws category axis bottom-up).
    const top = terms.slice(0, TERM_CHART_TOP_N).reverse();
    const labels = top.map((t) => t.term);
    const counts = top.map((t) => t.count);
    const color = COLORS[sentiment];

    if (charts[`terms_${sentiment}`]) {
      charts[`terms_${sentiment}`].data.labels = labels;
      charts[`terms_${sentiment}`].data.datasets[0].data = counts;
      charts[`terms_${sentiment}`].update();
      return;
    }

    charts[`terms_${sentiment}`] = new Chart(el.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [{ data: counts, backgroundColor: color, barThickness: 12 }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: COLORS.border } },
          y: { grid: { display: false } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: COLORS.text, titleColor: "#F6F1E9", bodyColor: "#F6F1E9" },
        },
      },
    });
  }

  function refreshThemes() {
    return fetchJSON(`/api/themes?${buildParams()}`).then((data) => {
      renderSentimentBarChart(
        "theme",
        "theme-chart",
        data.themes.map((t) => t.theme),
        data.themes
      );
    });
  }

  function refreshCompetitors() {
    return fetchJSON(`/api/competitors?${buildParams()}`).then((data) => {
      renderSentimentBarChart(
        "competitor",
        "competitor-chart",
        data.competitors.map((c) => c.display_name),
        data.competitors.map((c) => c.counts)
      );
    });
  }

  // Shared grouped-bar renderer for both the theme-frequency and
  // competitor-mentions charts -- same shape (category axis + positive/
  // negative/neutral series), just a different data source.
  function renderSentimentBarChart(key, elId, labels, rowsWithCounts) {
    const positive = rowsWithCounts.map((r) => r.positive);
    const negative = rowsWithCounts.map((r) => r.negative);
    const neutral = rowsWithCounts.map((r) => r.neutral);

    if (charts[key]) {
      charts[key].data.labels = labels;
      charts[key].data.datasets[0].data = positive;
      charts[key].data.datasets[1].data = negative;
      charts[key].data.datasets[2].data = neutral;
      charts[key].update();
      return;
    }

    const ctx = document.getElementById(elId).getContext("2d");
    charts[key] = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Positive", data: positive, backgroundColor: COLORS.positive },
          { label: "Negative", data: negative, backgroundColor: COLORS.negative },
          { label: "Neutral", data: neutral, backgroundColor: COLORS.neutral },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: COLORS.border } },
        },
        plugins: {
          legend: { position: "top", align: "end", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
          tooltip: { backgroundColor: COLORS.text, titleColor: "#F6F1E9", bodyColor: "#F6F1E9" },
        },
      },
    });
  }

  function escapeHTML(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function refreshComments() {
    return fetchJSON(`/api/comments?${buildParams({ page: state.page, page_size: state.pageSize })}`).then((data) => {
      const tbody = document.getElementById("comments-tbody");
      const emptyState = document.getElementById("empty-state");
      tbody.innerHTML = "";

      if (data.comments.length === 0) {
        emptyState.hidden = false;
      } else {
        emptyState.hidden = true;
        data.comments.forEach((c) => {
          const tr = document.createElement("tr");
          const date = c.timestamp.slice(0, 10);
          const themeTags = c.themes.map((t) => `<span class="theme-tag">${escapeHTML(t)}</span>`).join("");
          tr.innerHTML = `
            <td class="col-date">${date}</td>
            <td class="col-platform">${escapeHTML(c.platform)}</td>
            <td class="col-region">${escapeHTML(c.region || "unknown")}</td>
            <td class="col-sentiment"><span class="sentiment-label sentiment-${c.final_label}">${escapeHTML(c.final_label || "")}</span></td>
            <td class="col-themes">${themeTags}</td>
            <td class="col-text comment-text" title="${escapeHTML(c.text_raw)}">${escapeHTML(c.text_raw)}</td>
            <td class="col-engagement">${c.engagement}</td>
          `;
          tbody.appendChild(tr);
        });
      }

      document.getElementById("table-count-sub").textContent = `${data.total.toLocaleString()} comment${data.total === 1 ? "" : "s"} match current filters`;
      document.getElementById("page-info").textContent = `Page ${data.page} of ${data.total_pages}`;
      document.getElementById("page-prev").disabled = data.page <= 1;
      document.getElementById("page-next").disabled = data.page >= data.total_pages;
    });
  }

  // ------------------------------------------------------------------ chat

  let chatMessageCounter = 0;

  function initChat() {
    const form = document.getElementById("chat-form");
    if (!form) return;

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = document.getElementById("chat-input");
      const question = input.value.trim();
      if (!question) return;

      appendChatMessage("user", question);
      input.value = "";
      input.disabled = true;
      const pendingId = appendChatMessage("assistant", "Thinking...");

      fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      })
        .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
        .then(({ ok, data }) => renderChatResponse(pendingId, ok, data))
        .catch(() => renderChatResponse(pendingId, false, { detail: "Network error reaching /api/chat." }))
        .finally(() => {
          input.disabled = false;
          input.focus();
        });
    });
  }

  function renderChatResponse(messageId, ok, data) {
    const el = document.getElementById(messageId);
    if (!el) return;
    const textEl = el.querySelector(".chat-text");

    if (!ok) {
      textEl.textContent = data.detail || "Something went wrong.";
      el.classList.add("chat-error");
      return;
    }

    textEl.textContent = data.answer || "(no answer returned)";
    if (data.tool_calls && data.tool_calls.length) {
      const wrap = document.createElement("div");
      wrap.className = "chat-tool-calls";
      data.tool_calls.forEach((tc) => {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = `${tc.tool}(${JSON.stringify(tc.input)})`;
        details.appendChild(summary);
        details.insertAdjacentHTML("beforeend", renderToolOutputTable(tc.output));
        wrap.appendChild(details);
      });
      el.appendChild(wrap);
    }
    document.getElementById("chat-messages").scrollTop = document.getElementById("chat-messages").scrollHeight;
  }

  function renderToolOutputTable(output) {
    if (!output || typeof output !== "object") return "";

    const arrayKey = Object.keys(output).find(
      (k) => Array.isArray(output[k]) && output[k].length && typeof output[k][0] === "object"
    );

    if (!arrayKey) {
      const rows = Object.entries(output).filter(([, v]) => typeof v !== "object" || v === null);
      if (!rows.length) return "";
      const body = rows
        .map(([k, v]) => `<tr><td>${escapeHTML(k)}</td><td>${escapeHTML(String(v))}</td></tr>`)
        .join("");
      return `<table class="chat-tool-table"><tbody>${body}</tbody></table>`;
    }

    const items = output[arrayKey].slice(0, 8);
    const cols = Object.keys(items[0]).filter((c) => typeof items[0][c] !== "object");
    const header = `<tr>${cols.map((c) => `<th>${escapeHTML(c)}</th>`).join("")}</tr>`;
    const body = items
      .map((row) => `<tr>${cols.map((c) => `<td>${escapeHTML(String(row[c] ?? ""))}</td>`).join("")}</tr>`)
      .join("");
    return `<table class="chat-tool-table"><thead>${header}</thead><tbody>${body}</tbody></table>`;
  }

  function appendChatMessage(role, text) {
    const container = document.getElementById("chat-messages");
    const placeholder = container.querySelector(".chat-placeholder");
    if (placeholder) placeholder.remove();

    const id = `chat-msg-${chatMessageCounter++}`;
    const div = document.createElement("div");
    div.id = id;
    div.className = `chat-message chat-message-${role}`;
    div.innerHTML = `<span class="chat-role">${role === "user" ? "You" : "Assistant"}</span><span class="chat-text">${escapeHTML(text)}</span>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return id;
  }

  document.addEventListener("DOMContentLoaded", () => {
    init();
    initChat();
  });
})();
