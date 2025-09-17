(function(){
  function qs(s, r=document){return r.querySelector(s);}
  const DATA = JSON.parse(qs('#report-data')?.textContent || '{}');
  const rootStyles = getComputedStyle(document.documentElement);
  const COLOR_PRIMARY = rootStyles.getPropertyValue('--color-primary').trim() || '#4353ff';
  const COLOR_ACCENT = '#8b5cf6';
  const COLOR_MUTED = 'rgba(148, 163, 184, 0.45)';
  const COLOR_SUCCESS = rootStyles.getPropertyValue('--color-success').trim() || '#16a34a';
  const COLOR_DANGER = rootStyles.getPropertyValue('--color-danger').trim() || '#dc2626';
  const MONTHLY = Array.isArray(DATA.monthly) ? DATA.monthly : [];

  // --- Gauge score (usiamo un doughnut 0..100) ---
  const scoreEl = qs('#scoreGauge');
  if (scoreEl && window.Chart) {
    new Chart(scoreEl, {
      type: 'doughnut',
      data: { datasets: [{
        data: [DATA.score || 0, Math.max(0, 100 - (DATA.score || 0))],
        backgroundColor: [COLOR_SUCCESS, 'rgba(255,255,255,0.28)'],
        borderWidth: 0
      }]},
      options: {
        cutout: '70%',
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          title: {
            display: true,
            text: `Score: ${DATA.score || 0}`,
            color: '#f8fafc',
            font: { size: 16, weight: '600' }
          }
        }
      }
    });
  }

  // --- Entrate vs Uscite mensile (barre affiancate) ---
  const ieEl = qs('#incomeExpensesChart');
  if (ieEl && window.Chart) {
    const labels = MONTHLY.map(m => m.month);
    const inc = MONTHLY.map(m => m.income || 0);
    const exp = MONTHLY.map(m => m.expenses || 0);
    if (labels.length === 0) labels.push('N/A');
    new Chart(ieEl, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Entrate',
            data: inc,
            backgroundColor: COLOR_PRIMARY,
            borderRadius: 6,
            borderSkipped: false
          },
          {
            label: 'Uscite',
            data: exp,
            backgroundColor: COLOR_ACCENT,
            borderRadius: 6,
            borderSkipped: false
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: COLOR_MUTED, drawBorder: false },
            ticks: { color: '#475569' }
          },
          x: {
            grid: { display: false },
            ticks: { color: '#475569' }
          }
        },
        plugins: { legend: { position: 'bottom', labels: { color: '#1f2937' } } }
      }
    });
  }

  // --- Trend cumulato entrate/uscite ---
  const incomeTrendEl = qs('#incomeTrendChart');
  if (incomeTrendEl && window.Chart) {
    const labels = MONTHLY.map(m => m.month);
    const cumIncome = [];
    const cumExpenses = [];
    MONTHLY.reduce((acc, m, idx) => {
      const nextIncome = acc.income + (m.income || 0);
      const nextExpense = acc.expense + (m.expenses || 0);
      cumIncome[idx] = nextIncome;
      cumExpenses[idx] = nextExpense;
      return { income: nextIncome, expense: nextExpense };
    }, { income: 0, expense: 0 });

    if (labels.length === 0) labels.push('N/A');
    if (cumIncome.length === 0) {
      cumIncome.push(0);
      cumExpenses.push(0);
    }

    new Chart(incomeTrendEl, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Entrate cumulate',
            data: cumIncome,
            borderColor: COLOR_SUCCESS,
            backgroundColor: 'rgba(22, 163, 74, 0.12)',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5
          },
          {
            label: 'Uscite cumulate',
            data: cumExpenses,
            borderColor: COLOR_ACCENT,
            backgroundColor: 'rgba(139, 92, 246, 0.12)',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5
          },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { color: '#1f2937' } },
          tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(2)} €` } }
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: COLOR_MUTED, drawBorder: false },
            ticks: { color: '#475569', callback: value => `${value} €` }
          },
          x: {
            grid: { display: false },
            ticks: { color: '#475569' }
          }
        }
      }
    });
  }

  // --- Donut categorie + legenda + lista importi (compatti) ---
(function(){
  const catEl = document.querySelector('#categoryDonut');
  const legend = document.querySelector('#cat-legend');
  const list   = document.querySelector('#cat-list');
  if (!catEl || !window.Chart) return;

  const labels = (DATA.categories || []).map(c => c.category);
  const vals   = (DATA.categories || []).map(c => c.amount || 0);

  const palette = ['#4353ff','#8b5cf6','#22c55e','#f97316','#0ea5e9','#f43f5e','#14b8a6','#a855f7'];
  const chart = new Chart(catEl, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: vals, borderWidth: 0, backgroundColor: palette }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '68%',              // più sottile
      plugins: { legend: { display: false } } // legend custom
    }
  });

  // Legenda colorata (usa i colori reali del dataset)
  if (legend) {
    // forziamo un layout prima di leggere gli stili
    requestAnimationFrame(() => {
      const meta = chart.getDatasetMeta(0);
      labels.forEach((lbl, i) => {
        const color = meta.controller.getStyle(i).backgroundColor;
        const li = document.createElement('li');
        const dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.background = Array.isArray(color) ? color[i] : color;

        const txt = document.createElement('span');
        txt.textContent = lbl;

        li.appendChild(dot);
        li.appendChild(txt);
        legend.appendChild(li);
      });
    });
  }

  // Lista importi (opzionale – puoi rimuoverla se non ti serve)
  if (list) {
    labels.forEach((lbl, i) => {
      const li = document.createElement('li');
      li.className = 'list-group-item d-flex justify-content-between align-items-center';
      li.innerHTML = `<span>${lbl}</span><span class="fw-semibold">${(vals[i] || 0).toFixed(2)} €</span>`;
      list.appendChild(li);
    });
  }
})();

  // --- Cash flow mensile + saldo cumulato ---
  const cashflowEl = qs('#cashflowChart');
  if (cashflowEl && window.Chart) {
    const labels = MONTHLY.map(m => m.month);
    const net = MONTHLY.map(m => (m.income || 0) - (m.expenses || 0));
    const cumulative = [];
    net.reduce((acc, value, idx) => {
      const next = acc + value;
      cumulative[idx] = next;
      return next;
    }, 0);

    if (labels.length === 0) labels.push('N/A');
    if (net.length === 0) net.push(0);
    if (cumulative.length === 0) cumulative.push(0);

    const barColors = net.map(v => v >= 0 ? 'rgba(22, 163, 74, 0.68)' : 'rgba(220, 38, 38, 0.68)');

    new Chart(cashflowEl, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Cash flow mensile',
            data: net,
            backgroundColor: barColors,
            borderRadius: 6,
            borderSkipped: false,
            order: 2,
          },
          {
            type: 'line',
            label: 'Saldo cumulato',
            data: cumulative,
            borderColor: COLOR_PRIMARY,
            backgroundColor: COLOR_PRIMARY,
            borderWidth: 2,
            tension: 0.35,
            fill: false,
            pointRadius: 3,
            pointHoverRadius: 5,
            order: 1,
            yAxisID: 'y1',
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { color: '#1f2937' } },
          tooltip: {
            callbacks: {
              label: ctx => {
                const val = ctx.parsed.y;
                const suffix = ctx.datasetIndex === 0 ? '€' : '€ cumulato';
                return ` ${val.toFixed(2)} ${suffix}`;
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: COLOR_MUTED, drawBorder: false },
            ticks: { color: '#475569', callback: value => `${value} €` }
          },
          y1: {
            beginAtZero: true,
            position: 'right',
            grid: { display: false },
            ticks: { color: '#475569', callback: value => `${value} €` }
          },
          x: {
            grid: { display: false },
            ticks: { color: '#475569' }
          }
        }
      }
    });
  }

  // --- Tasso di risparmio ---
  const savingsRateEl = qs('#savingsRateChart');
  if (savingsRateEl && window.Chart) {
    const labels = MONTHLY.map(m => m.month);
    const rates = MONTHLY.map(m => {
      const inc = m.income || 0;
      const exp = m.expenses || 0;
      if (inc <= 0) return 0;
      return ((inc - exp) / inc) * 100;
    });

    if (labels.length === 0) labels.push('N/A');
    if (rates.length === 0) rates.push(0);

    new Chart(savingsRateEl, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Risparmio %',
          data: rates,
          borderColor: COLOR_PRIMARY,
          backgroundColor: COLOR_PRIMARY,
          fill: false,
          borderWidth: 3,
          tension: 0.35,
          pointRadius: 4,
          pointBackgroundColor: COLOR_PRIMARY,
          pointHoverRadius: 5
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(1)}%` } }
        },
        scales: {
          y: {
            suggestedMin: -50,
            suggestedMax: 100,
            grid: { color: COLOR_MUTED, drawBorder: false },
            ticks: { color: '#475569', callback: value => `${value}%` }
          },
          x: {
            grid: { display: false },
            ticks: { color: '#475569' }
          }
        }
      }
    });
  }

})();
