(() => {
  // -------- DOM --------
  const yearSel   = document.getElementById('filterYear');
  const monthSel  = document.getElementById('filterMonth');
  const kpiAmount = document.getElementById('kpiTotalSent');
  const kpiPeriod = document.getElementById('kpiPeriod');

  const lineCtx = document.getElementById('totalSentYearChart')?.getContext('2d');
  const barCtx  = document.getElementById('totalSentByRecipientChart')?.getContext('2d');

  if (!yearSel || !monthSel || !kpiAmount || !kpiPeriod || !lineCtx || !barCtx) {
    console.warn('[send-overview] Missing required DOM elements.');
    return;
  }

  // -------- Helpers --------
  const MONTHS = [
    'January','February','March','April','May','June',
    'July','August','September','October','November','December'
  ];

  const now   = new Date();
  const curY  = now.getFullYear();
  const curM  = now.getMonth() + 1; // 1-12

  function pad(n){ return n.toString().padStart(2,'0'); }
  function fmtNumber(n){ return n.toLocaleString(undefined); }
  function monthName(m){ return MONTHS[(m-1)]; }

  // Fill year select (e.g., last 6 years)
  function populateYears() {
    const start = curY - 5;
    yearSel.innerHTML = '';
    for (let y = curY; y >= start; y--) {
      const opt = document.createElement('option');
      opt.value = y;
      opt.textContent = y;
      yearSel.appendChild(opt);
    }
    yearSel.value = curY;
  }

  // Fill month select + "All"
  function populateMonths() {
    monthSel.innerHTML = '';
    // All months (empty value)
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'All Months';
    monthSel.appendChild(all);

    MONTHS.forEach((name, idx) => {
      const opt = document.createElement('option');
      opt.value = (idx + 1); // 1..12
      opt.textContent = name;
      monthSel.appendChild(opt);
    });

    // Default to current month
    monthSel.value = String(curM);
  }

  // -------- Charts (keep references so we can destroy before re-render) --------
  let lineChart = null;
  let barChart  = null;

  function buildLineChart(dataByMonth) {
    // generate data points for all 12 months (fill missing with 0)
    const labels = MONTHS;
    const data = [];
    for (let m = 1; m <= 12; m++) data.push(Number(dataByMonth[m] || 0));

    if (lineChart) lineChart.destroy();
    lineChart = new Chart(lineCtx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Total Sent',
          data,
          tension: 0.35,
          pointRadius: 3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ticks: { callback: v => v.toLocaleString() } }
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: ctx => `৳${fmtNumber(ctx.parsed.y)}`
            }
          },
          legend: { display: true }
        }
      }
    });
  }

  function buildBarChart(items) {
    // items: [{name, total}]
    const labels = items.map(i => i.name || '—');
    const data   = items.map(i => Number(i.total || 0));

    if (barChart) barChart.destroy();
    barChart = new Chart(barCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Total Sent',
          data
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ticks: { callback: v => v.toLocaleString() } }
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: ctx => `৳${fmtNumber(ctx.parsed.y)}`
            }
          },
          legend: { display: true }
        }
      }
    });
  }

  // -------- Fetch + Paint --------
  async function load() {
    const year  = Number(yearSel.value);
    const month = monthSel.value ? Number(monthSel.value) : null;

    const params = new URLSearchParams({ year: String(year) });
    if (month) params.set('month', String(month));

    const url = `/api/send_overview?${params.toString()}`;
    let payload;
    try {
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
    } catch (err) {
      console.error('[send-overview] fetch failed:', err);
      return;
    }

    // KPI
    const total = Number(payload.total_sent || 0);
    kpiAmount.textContent = fmtNumber(total);
    if (payload.month) {
      kpiPeriod.textContent = `${monthName(payload.month)}, ${payload.year}`;
    } else {
      kpiPeriod.textContent = `${payload.year}`;
    }

    // Charts
    buildLineChart(payload.monthly || {});
    buildBarChart(payload.recipients || []);
  }

  // -------- Events --------
  yearSel.addEventListener('change', load);
  monthSel.addEventListener('change', load);

  // -------- Init --------
  populateYears();
  populateMonths();
  load();
})();