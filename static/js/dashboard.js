(function () {
  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  // ---- Visibilità importi (toggle) ----
  const toggleBtn = qs('#toggle-visibility');
  let masked = false;
  function setMasked(state) {
    masked = state;
    qsa('.money').forEach(el => el.classList.toggle('masked', masked));
    if (toggleBtn) {
      toggleBtn.setAttribute('aria-pressed', String(masked));
      toggleBtn.querySelector('.vis-icon').textContent = masked ? '🙈' : '👁️';
    }
  }
  if (toggleBtn) toggleBtn.addEventListener('click', () => setMasked(!masked));

  // ---- Dati ----
  const dataScript = qs('#dash-data');
  let DASH = { account: { balance: 0, currency: 'EUR' }, piggies: [], transactions: [] };
  try { DASH = JSON.parse(dataScript?.textContent || '{}'); } catch {}

  // ---- Sparkline conto ----
  const sparkEl = qs('#balanceSparkline');
  if (sparkEl && window.Chart) {
    const tx = (DASH.transactions || []).slice().reverse().map(t => Number(t.amount || 0));
    const base = Number(DASH.account?.balance || 0);
    const series = [base];
    tx.forEach(a => series.push(series[series.length - 1] - a));
    const labels = series.map((_, i) => i);
    new Chart(sparkEl, {
      type: 'line',
      data: { labels, datasets: [{ data: series, tension: .35, pointRadius: 0, borderWidth: 2, fill: true }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: { x: { display: false }, y: { display: false } }
      }
    });
  }

  // ---- Progress salvadanai (no Jinja) ----
  qsa('.piggy-progress').forEach(wrap => {
    const current = parseFloat(wrap.dataset.current || '0');
    const target  = parseFloat(wrap.dataset.target || '0');
    const bar = wrap.querySelector('.progress-bar');
    if (!bar) return;
    if (!target || target <= 0) { wrap.style.display = 'none'; return; }
    let perc = Math.floor((current / target) * 100);
    perc = Math.max(0, Math.min(100, perc));
    bar.style.width = perc + '%';
    bar.setAttribute('aria-valuenow', String(perc));
  });

  // ---- Asset allocation: barra 100% orizzontale ----
  let assetChart;
  function buildAssetData() {
    const accountValue = Math.max(0, Number(DASH.account?.balance || 0));
    const piggies = DASH.piggies || [];
    const labels = ['Conto', ...piggies.map(p => p.name)];
    const values = [accountValue, ...piggies.map(p => Math.max(0, Number(p.current || 0)))];
    const total = values.reduce((a, b) => a + b, 0);
    const perc = total > 0 ? values.map(v => (v / total) * 100) : values.map(() => 0);
    return { labels, values: perc };
  }

  function renderAssetStacked() {
    const ctx = qs('#assetChart');
    if (!ctx || !window.Chart) return;
    const { labels, values } = buildAssetData();
    const data = {
      labels: ['Allocazione'],
      datasets: labels.map((lbl, i) => ({ label: lbl, data: [values[i]], borderWidth: 0 }))
    };
    if (assetChart) assetChart.destroy();
    assetChart = new Chart(ctx, {
      type: 'bar',
      data,
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, min: 0, max: 100, ticks: { callback: v => v + '%' }, grid: { display: false } },
          y: { stacked: true, grid: { display: false } }
        },
        plugins: {
          legend: { position: 'bottom' },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.x.toFixed(1)}%` } }
        }
      }
    });
  }

  function hideAssetChart() {
    const wrap = qs('#assetChartWrap');
    const empty = qs('#assetEmpty');
    if (assetChart) { assetChart.destroy(); assetChart = null; }
    if (wrap) wrap.classList.add('d-none');
    if (empty) empty.classList.remove('d-none');
  }
  function showAssetChart() {
    const wrap = qs('#assetChartWrap');
    const empty = qs('#assetEmpty');
    if (wrap) wrap.classList.remove('d-none');
    if (empty) empty.classList.add('d-none');
    renderAssetStacked();
  }

  const btnStacked = qs('#btnAssetStacked');
  const btnNone = qs('#btnAssetNone');
  if (btnStacked && btnNone) {
    btnStacked.addEventListener('click', () => {
      btnStacked.classList.add('active'); btnNone.classList.remove('active'); showAssetChart();
    });
    btnNone.addEventListener('click', () => {
      btnNone.classList.add('active'); btnStacked.classList.remove('active'); hideAssetChart();
    });
  }
  // Render iniziale
  showAssetChart();

  // ---- Modali UX ----
  const transferModal = qs('#modalTransfer');
  if (transferModal) {
    transferModal.addEventListener('show.bs.modal', (ev) => {
      const btn = ev.relatedTarget; if (!btn) return;
      const piggyId = btn.getAttribute('data-piggy') || '';
      const direction = btn.getAttribute('data-direction') || 'TO_PIGGY';
      const current = parseFloat(btn.getAttribute('data-current') || '0');
      qs('#transferPiggyId').value = piggyId;
      qs('#transferDirection').value = direction;
      const amountInput = qs('#transferAmount');
      amountInput.removeAttribute('max');
      if (direction === 'FROM_PIGGY') amountInput.setAttribute('max', String(current.toFixed(2)));
      amountInput.value = '';
      transferModal.querySelector('.modal-title').textContent =
        (direction === 'TO_PIGGY' ? 'Aggiungi al salvadanaio' : 'Rimuovi dal salvadanaio');
    });
  }

  const delModal = qs('#modalDeletePiggy');
  if (delModal) {
    delModal.addEventListener('show.bs.modal', (ev) => {
      const btn = ev.relatedTarget; if (!btn) return;
      const piggyId = btn.getAttribute('data-piggy') || '';
      const current = parseFloat(btn.getAttribute('data-current') || '0');
      qs('#deletePiggyId').value = piggyId;
      qs('#deletePiggyBalance').textContent = current.toFixed(2) + ' €';
    });
  }
})();
