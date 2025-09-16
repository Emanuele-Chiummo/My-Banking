(function(){
  function qs(s, r=document){return r.querySelector(s);}
  const DATA = JSON.parse(qs('#report-data')?.textContent || '{}');

  // --- Gauge score (usiamo un doughnut 0..100) ---
  const scoreEl = qs('#scoreGauge');
  if (scoreEl && window.Chart) {
    new Chart(scoreEl, {
      type: 'doughnut',
      data: { datasets: [{
        data: [DATA.score || 0, Math.max(0, 100 - (DATA.score || 0))],
        borderWidth: 0
      }]},
      options: {
        cutout: '70%',
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          title: { display: true, text: `Score: ${DATA.score || 0}` }
        }
      }
    });
  }

  // --- Entrate vs Uscite mensile (barre affiancate) ---
  const ieEl = qs('#incomeExpensesChart');
  if (ieEl && window.Chart) {
    const labels = (DATA.monthly || []).map(m => m.month);
    const inc = (DATA.monthly || []).map(m => m.income || 0);
    const exp = (DATA.monthly || []).map(m => m.expenses || 0);
    new Chart(ieEl, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: 'Entrate', data: inc, borderWidth: 0 },
          { label: 'Uscite', data: exp, borderWidth: 0 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { y: { beginAtZero: true } },
        plugins: { legend: { position: 'bottom' } }
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

  const chart = new Chart(catEl, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: vals, borderWidth: 0 }] },
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

})();
