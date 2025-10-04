(function () {
  const yearSelect = document.getElementById('yearSelect');
  const balanceEl  = document.getElementById('totalBalance');
  const canvas     = document.getElementById('cashflowChart');
  if (!yearSelect || !balanceEl || !canvas || !window.Chart) return;

  const ctx = canvas.getContext('2d');
  let chart;
  async function load(year) {
    const url = new URL('/api/cashflow', window.location.origin);
    url.searchParams.set('year', year);
    url.searchParams.set('currency', "{{ currency }}");  // server passes initial page currency
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const data = await res.json();

    // store currency symbol from API
    const sym = (data.currency === "BDT") ? "৳" : "₩";

    // Update Total Balance text
    balanceEl.textContent = data.total_balance_fmt || '';

    const labels   = data.labels || [];
    const income   = data.income || [];
    const expenses = data.expenses || [];
    const net      = data.net || [];

    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Income',  data: income,  tension: 0.3 },
          { label: 'Expenses',data: expenses, tension: 0.3 },
          { label: 'Net',     data: net,     tension: 0.3 },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        interaction: { mode: 'index', intersect: false },
        scales: { y: { beginAtZero: true } },
        plugins: {
          legend: { position: 'top' },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                const val = ctx.parsed.y;
                const asInt = Math.trunc(val);
                const num = (val !== asInt) ? val.toLocaleString() : asInt.toLocaleString();
                return ` ${ctx.dataset.label}: ${sym}${num}`;
              }
            }
          }
        }
      }
    });
  }


  // initial
  load(yearSelect.value);

  // on change
  yearSelect.addEventListener('change', () => load(yearSelect.value));
})();


(function () {
  const ySel = document.getElementById('breakdownYear');
  const mSel = document.getElementById('breakdownMonth');
  const totalEl = document.getElementById('expTotal');
  const changeEl = document.getElementById('expChange');
  const listEl = document.getElementById('expList');
  const canvas = document.getElementById('expenseBreakdownChart');
  if (!ySel || !mSel || !totalEl || !changeEl || !listEl || !canvas || !window.Chart) return;

  const ctx = canvas.getContext('2d');
  let chart;

  async function load() {
    const year = ySel.value;
    const month = mSel.value; // "01".."12"
    const url = new URL('/api/expense_breakdown', window.location.origin);
    url.searchParams.set('year', year);
    url.searchParams.set('month', month);
    url.searchParams.set('currency', "{{ currency }}");

    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const data = await res.json();

    // --- Total & change
    totalEl.textContent = data.total_fmt || '';
    if (data.change_pct == null) {
      changeEl.textContent = '—';
      changeEl.className = 'badge rounded-pill bg-secondary-subtle text-secondary';
    } else {
      const up = !!data.change_up;
      const pct = Math.abs(data.change_pct).toFixed(2);
      changeEl.textContent = (up ? '↑ ' : '↓ ') + pct + ' %';
      changeEl.className = 'badge rounded-pill ' + (up ? 'bg-danger-subtle text-danger' : 'bg-success-subtle text-success');
      // ↑ More expense than last month is "bad" → red; less → green
    }

    // --- Chart (doughnut)
    const labels = data.labels || [];
    const values = data.values || [];
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: 'doughnut',
      data: { labels, datasets: [{ data: values }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '55%',
        plugins: {
          legend: { display: false},
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const label = ctx.label || '';
                const val = ctx.parsed;
                const total = values.reduce((a,b)=>a+b, 0) || 1;
                const pct = ((val / total) * 100).toFixed(1);
                const sym = (data.currency === 'BDT') ? '৳' : '₩';
                const num = Number.isInteger(val) ? val.toLocaleString() : val.toLocaleString();
                return ` ${label}: ${sym}${num} (${pct}%)`;
              }
            }
          }
        }
      }
    });

    // --- List (progress bars)
  listEl.innerHTML = '';
  const items = data.items || [];

  // --- group by parent category ---
  const grouped = {};
  for (const it of items) {
    // assume each item has `parent` and `amount`
    const parent = it.parent || it.name; // fallback if no parent given
    if (!grouped[parent]) {
      grouped[parent] = { name: parent, amount: 0 };
    }
    grouped[parent].amount += (it.amount || 0);
  }

  // turn grouped object into array
  const parents = Object.values(grouped);
  const grand = parents.reduce((a, b) => a + (b.amount || 0), 0) || 1;

  // --- render rows only for parents ---
  for (const it of parents) {
    const sym = (data.currency === 'BDT') ? '৳' : '₩';
    const amountStr = sym + (Number.isInteger(it.amount) ? it.amount.toLocaleString() : it.amount.toLocaleString());

    // percent share based on total
    const pct = (it.amount / grand) * 100;

    const row = document.createElement('div');
    row.className = 'mb-2';
    row.innerHTML = `
      <div class="d-flex justify-content-between small">
        <span class="text-truncate pe-2">${it.name}</span>
        <span class="fw-semibold">${amountStr}</span>
      </div>
      <div class="progress" style="height: 6px;">
        <div class="progress-bar" role="progressbar" style="width: ${pct.toFixed(1)}%;" 
            aria-valuenow="${pct.toFixed(1)}" aria-valuemin="0" aria-valuemax="100"></div>
      </div>
    `;
    listEl.appendChild(row);
  }

  }

  // initial + listeners
  load();
  ySel.addEventListener('change', load);
  mSel.addEventListener('change', load);
})();


(function () {
  const root = document.getElementById('financeScoreSection');
  if (!root) return;

  const apiUrl = root.dataset.api || '/api/finance-score';  // 👈 read from HTML

  const yearEl  = root.querySelector('#fsYear');
  const monthEl = root.querySelector('#fsMonth');
  const labelEl = root.querySelector('[data-role="fs-label"]');
  const pctEl   = root.querySelector('[data-role="fs-score"]');
  const barEl   = root.querySelector('#financeScoreBar');

function paint(d) {
  // text
  labelEl.textContent = d.label;       // "Excellent" / "Good" / "Fair" / "Poor"
  pctEl.textContent   = `${d.score}%`; // "92%" etc.

  // width
  barEl.style.width = `${d.score}%`;

  // color by score
  barEl.classList.remove('excellent','good','fair','poor');
  if (d.score >= 85)      barEl.classList.add('excellent');
  else if (d.score >= 70) barEl.classList.add('good');
  else if (d.score >= 50) barEl.classList.add('fair');
  else                    barEl.classList.add('poor');

  // (Optional) also tint the percent text to match the bucket:
  pctEl.classList.remove('text-success','text-teal','text-warning','text-danger');
  if (d.score >= 85)      pctEl.classList.add('text-success');
  else if (d.score >= 70) pctEl.classList.add('text-teal');     // add a .text-teal class in CSS if not using Bootstrap 5.3+
  else if (d.score >= 50) pctEl.classList.add('text-warning');
  else                    pctEl.classList.add('text-danger');

  // (Optional) accessibility hint
  barEl.setAttribute('aria-valuenow', d.score);
  barEl.setAttribute('aria-label', `Finance score ${d.score} percent (${d.label})`);
}

  async function loadScore(paramsObj = {}) {
    const params = new URLSearchParams(paramsObj);
    try {
      const res = await fetch(`${apiUrl}?${params.toString()}`, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      paint(data);
    } catch (e) {
      console.error('[finance-score] fetch failed:', e);
    }
  }

  function onChange() {
    loadScore({ year: yearEl.value, month: monthEl.value });
  }

  yearEl && yearEl.addEventListener('change', onChange);
  monthEl && monthEl.addEventListener('change', onChange);

  // initial fetch for current month (service defaults to today if no params)
  document.addEventListener('DOMContentLoaded', () => loadScore());
})();



(function () {
  const CARDS = window.CARDS || [];  // 👈 server injects this variable in the HTML
  const KRW = new Intl.NumberFormat(undefined, { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
  const fmt = (n) => KRW.format(Math.max(0, Math.round(n || 0)));
  const utilClass = (p) => p >= 70 ? "bad" : (p >= 30 ? "warn" : "good");
  const el = (h) => { const w = document.createElement('div'); w.innerHTML = h.trim(); return w.firstElementChild; };

  function paintTotals(cards) {
    const totalLimit  = cards.reduce((s, c) => s + (c.limit||0), 0);
    const totalUsed   = cards.reduce((s, c) => s + (c.used||0), 0);
    const totalRemain = Math.max(0, totalLimit - totalUsed);
    const utilPct     = totalLimit > 0 ? Math.round(totalUsed / totalLimit * 100) : 0;

    document.getElementById('totLimit').textContent  = fmt(totalLimit);
    document.getElementById('totUsed').textContent   = fmt(totalUsed);
    document.getElementById('totRemain').textContent = fmt(totalRemain);

    const bar = document.getElementById('totProgress');
    bar.classList.remove('good','warn','bad');
    bar.classList.add(utilClass(utilPct));
    bar.style.width = utilPct + "%";
    document.getElementById('totUtilPct').textContent = utilPct + "% used";
  }

  function buildCardRows(cards) {
    const rows = document.getElementById('cardRows');
    rows.innerHTML = "";

    cards.forEach(c => {
      const limit = c.limit || 0;
      const used  = c.used  || 0;
      const remain = Math.max(0, limit - used);
      const pct = limit > 0 ? Math.round(used / limit * 100) : 0;

      const row = el(`
        <div class="list-group-item card-row">
          <!-- Row 1: Brand / Name -->
          <div class="d-flex align-items-center gap-3 mb-2 brand">
            <img src="${c.brandLogo}" alt="${c.name}" class="brand-logo rounded">
            <div class="title text-truncate" title="${c.name}">${c.name}</div>
            <div class="ms-auto small text-muted">${pct}% used</div>
          </div>

          <!-- Row 2: Limit -->
          <div class="kv d-flex justify-content-between mb-1">
            <div class="label">Credit Limit</div>
            <div class="val currency-wrap">${fmt(limit)}</div>
          </div>

          <!-- Row 3: Used -->
          <div class="kv d-flex justify-content-between mb-1">
            <div class="label">Used</div>
            <div class="val currency-wrap">${fmt(used)}</div>
          </div>

          <!-- Row 4: Remaining -->
          <div class="kv d-flex justify-content-between mb-2">
            <div class="label">Remaining</div>
            <div class="val currency-wrap">${fmt(remain)}</div>
          </div>

          <!-- Row 5: Progress bar -->
          <div class="progress">
            <div class="progress-bar ${utilClass(pct)}" role="progressbar"
                 style="width:${pct}%;" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
          </div>
        </div>
      `);
      rows.appendChild(row);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    paintTotals(CARDS);
    buildCardRows(CARDS);
  });
})();

