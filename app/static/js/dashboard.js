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

  function refreshThemes() {
    return fetchJSON(`/api/themes?${buildParams()}`).then((data) => {
      const labels = data.themes.map((t) => t.theme);
      const positive = data.themes.map((t) => t.positive);
      const negative = data.themes.map((t) => t.negative);
      const neutral = data.themes.map((t) => t.neutral);

      if (charts.theme) {
        charts.theme.data.labels = labels;
        charts.theme.data.datasets[0].data = positive;
        charts.theme.data.datasets[1].data = negative;
        charts.theme.data.datasets[2].data = neutral;
        charts.theme.update();
        return;
      }

      const ctx = document.getElementById("theme-chart").getContext("2d");
      charts.theme = new Chart(ctx, {
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

  document.addEventListener("DOMContentLoaded", init);
})();
