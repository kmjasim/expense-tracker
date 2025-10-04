// static/js/budget.js
(function () {
  "use strict";

  // ---- Helpers ----
  function $(sel) { return document.querySelector(sel); }

  // KRW formatter (fall back if Intl isn’t available)
  const formatKRW = (n) => {
    try {
      return new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'KRW', maximumFractionDigits: 0 }).format(n || 0);
    } catch {
      const x = Math.round(n || 0).toLocaleString();
      return `₩${x}`;
    }
  };

  // Guard: data must be present
  const DATA = window.__BUDGET_DATA__ || null;
  if (!DATA) {
    console.warn("[budget] __BUDGET_DATA__ missing; charts not rendered.");
    return;
  }

  // ---- Bar: Spending by Parent Category (this month) ----
  const barEl = $("#barByCategory");
  if (barEl && Array.isArray(DATA.bar_chart?.labels)) {
    const labels = DATA.bar_chart.labels;
    const values = (DATA.bar_chart.values || []).map(v => Number(v) || 0);

    // If all zeros, avoid drawing an empty chart
    const allZero = values.every(v => v === 0);

    const ctx = barEl.getContext("2d");
    new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Spent",
          data: values,
          // Keep default colors; Chart.js will pick them.
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `${formatKRW(item.parsed.y)}`
            }
          },
          title: {
            display: allZero,
            text: allZero ? "No spending recorded for this month." : ""
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              callback: (v) => formatKRW(v)
            }
          },
          x: {
            ticks: { autoSkip: true, maxRotation: 0 }
          }
        }
      }
    });
  } else {
    console.info("[budget] #barByCategory not found or no data.");
  }

  // ---- Line: Yearly Budget vs Spent (optional) ----
  // To enable, set in your route:
  // window.__BUDGET_LINE__ = { labels: ["01","02",...,"12"], budget: [...], spent: [...] }
  const LINE = window.__BUDGET_LINE__ || null;
  const lineEl = $("#lineYear");
  if (lineEl && LINE && Array.isArray(LINE.labels)) {
    const ctx = lineEl.getContext("2d");
    new Chart(ctx, {
      type: "line",
      data: {
        labels: LINE.labels,
        datasets: [
          { label: "Budget", data: (LINE.budget || []).map(n => Number(n) || 0), tension: 0.35 },
          { label: "Spent",  data: (LINE.spent  || []).map(n => Number(n) || 0), tension: 0.35 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${formatKRW(item.parsed.y)}`
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: { callback: (v) => formatKRW(v) }
          }
        }
      }
    });
  } else if (lineEl) {
    // If canvas exists but no data supplied, show a friendly message
    const wrap = lineEl.closest(".card-body");
    if (wrap) {
      const note = document.createElement("div");
      note.className = "text-muted small";
      note.textContent = "Yearly chart not available yet.";
      wrap.appendChild(note);
    }
  }

  // ---- (Optional) Improve accordion caret UX on the table ----
  // If you used ▸ as the toggle, turn it to ▾ when open.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-bs-toggle="collapse"]');
    if (!btn) return;
    const targetSel = btn.getAttribute("data-bs-target");
    if (!targetSel) return;
    const el = document.querySelector(targetSel);
    if (!el) return;

    // Wait for collapse transition end to reflect correct state
    const onDone = () => {
      const open = el.classList.contains("show");
      btn.textContent = open ? "▾" : "▸";
      el.removeEventListener("shown.bs.collapse", onDone);
      el.removeEventListener("hidden.bs.collapse", onDone);
    };
    el.addEventListener("shown.bs.collapse", onDone);
    el.addEventListener("hidden.bs.collapse", onDone);
  });

})();
