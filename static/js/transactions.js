// transactions.js — filtri (collapse) + paginazione 20/pg
(function () {
  const $  = (sel) => document.querySelector(sel);

  const tbody = $("#txTbody");
  const empty = $("#txEmpty");
  const dot   = $("#txFilterDot");
  const range = $("#txRangeLabel");
  const btnPrev = $("#txPrev");
  const btnNext = $("#txNext");

  // Controlli filtri (come già in pagina)
  const controls = {
    q: $("#txSearch"),
    type: $("#txType"),
    account: $("#txAccount"),
    from: $("#txFrom"),
    to: $("#txTo"),
    sort: $("#txSort"),
    order: $("#txOrder"),
    apply: $("#txApply"),
    reset: $("#txReset"),
  };

  const LIMIT = 10;   // fisso
  let page = 1;       // pagina corrente (1-based)
  let hasMore = false;

  function fmtMoney(v) {
    if (typeof v !== "number") v = parseFloat(v || 0);
    const sign = v < 0 ? "-" : "";
    const abs = Math.abs(v).toFixed(2).replace(".", ",");
    return `${sign}${abs} €`;
  }

  function escapeHtml(s) {
    return (s || "").toString().replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[m]));
  }

  function rowHTML(t) {
    const cls = t.type === "CREDIT" ? "text-success" : "text-danger";
    return `
      <tr>
        <td>${t.date}</td>
        <td>${escapeHtml(t.description)}</td>
        <td>${escapeHtml(t.category)}</td>
        <td>${escapeHtml(t.account_name || t.account_id)}</td>
        <td class="text-end"><span class="${cls}">${fmtMoney(t.amount)}</span></td>
      </tr>`;
  }

  function hasActiveFilters() {
    const def = { q: "", type: "", account: "", from: "", to: "", sort: "date", order: "desc" };
    return (
      (controls.q.value.trim() !== def.q) ||
      (controls.type.value !== def.type) ||
      (controls.account.value !== def.account) ||
      (controls.from.value !== def.from) ||
      (controls.to.value !== def.to) ||
      (controls.sort.value !== def.sort) ||
      (controls.order.value !== def.order)
    );
  }

  function updateDot() {
    if (!dot) return;
    dot.classList.toggle("d-none", !hasActiveFilters());
  }

  function updatePager(countOnPage, totalKnown = null) {
    // Range visualizzato
    const start = countOnPage === 0 ? 0 : ( (page - 1) * LIMIT + 1 );
    const end   = (page - 1) * LIMIT + countOnPage;
    if (range) {
      range.textContent = countOnPage === 0
        ? "Nessun risultato"
        : `Mostrando ${start}–${end}${totalKnown ? ` su ${totalKnown}` : ""}`;
    }
    // Bottoni
    btnPrev.disabled = (page <= 1);
    btnNext.disabled = !hasMore;
  }

  async function fetchTx() {
    const params = new URLSearchParams();
    const q = controls.q.value.trim();
    if (q) params.set("q", q);
    if (controls.type.value) params.set("type", controls.type.value);
    if (controls.account.value) params.set("account_id", controls.account.value);
    if (controls.from.value) params.set("date_from", controls.from.value);
    if (controls.to.value) params.set("date_to", controls.to.value);
    if (controls.sort.value) params.set("sort", controls.sort.value);
    if (controls.order.value) params.set("order", controls.order.value);

    // paginazione: chiediamo LIMIT+1 per capire se c'è la prossima pagina
    const offset = (page - 1) * LIMIT;
    params.set("limit", String(LIMIT + 1));
    params.set("offset", String(offset));

    const res = await fetch(`/api/transactions?${params.toString()}`, { credentials: "same-origin" });
    const data = await res.json();

    // hasMore se riceviamo più di LIMIT record
    hasMore = Array.isArray(data) && data.length > LIMIT;
    const items = hasMore ? data.slice(0, LIMIT) : data;

    if (!Array.isArray(items) || items.length === 0) {
      tbody.innerHTML = "";
      empty.classList.remove("d-none");
      updatePager(0);
      updateDot();
      return;
    }

    empty.classList.add("d-none");
    tbody.innerHTML = items.map(rowHTML).join("");
    updatePager(items.length);
    updateDot();
  }

  // Azioni paginazione
  btnPrev?.addEventListener("click", (e) => {
    e.preventDefault();
    if (page > 1) {
      page--;
      fetchTx();
    }
  });
  btnNext?.addEventListener("click", (e) => {
    e.preventDefault();
    if (hasMore) {
      page++;
      fetchTx();
    }
  });

  // Applica/Reset filtri — reset pagina a 1
  controls.apply?.addEventListener("click", (e) => {
    e.preventDefault();
    page = 1;
    fetchTx();
  });
  controls.reset?.addEventListener("click", (e) => {
    e.preventDefault();
    controls.q.value = "";
    controls.type.value = "";
    controls.account.value = "";
    controls.from.value = "";
    controls.to.value = "";
    controls.sort.value = "date";
    controls.order.value = "desc";
    page = 1;
    fetchTx();
  });
  controls.q?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      page = 1;
      controls.apply?.click();
    }
  });

  // Primo load
  document.addEventListener("DOMContentLoaded", () => {
    page = 1;
    fetchTx();
  });
})();
