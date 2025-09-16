(function () {
  const form = document.getElementById('p2pForm');
  const contactSelect = document.getElementById('contactSelect');
  const hiddenName = document.getElementById('contactDisplayName');
  const tableBody = document.getElementById('p2pTableBody');

  if (!form) return;

  // Imposta il nome alla selezione corrente
  function syncContactName() {
    if (!contactSelect || !hiddenName) return;
    const opt = contactSelect.options[contactSelect.selectedIndex];
    hiddenName.value = opt ? opt.text.trim() : '';
  }
  contactSelect && contactSelect.addEventListener('change', syncContactName);
  syncContactName();

  function showToast(msg, ok = true) {
    const stack = document.getElementById('p2pToastStack');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = 'toast align-items-center text-bg-' + (ok ? 'success' : 'danger') + ' border-0';
    el.setAttribute('role', 'alert');
    el.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${msg}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto"
                data-bs-dismiss="toast" aria-label="Chiudi"></button>
      </div>`;
    stack.appendChild(el);
    const t = new bootstrap.Toast(el, { delay: 3000 });
    t.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    syncContactName();

    const endpoint = form.getAttribute('action') || '/api/p2p/send';
    const payload = {
      from_account_id: form.from_account_id.value,
      contact_id: form.contact_id.value,
      amount: form.amount.value,
      message: form.message.value,
      contact_display_name: hiddenName ? hiddenName.value : undefined
    };

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const js = await res.json();
      if (!res.ok) {
        showToast('❌ ' + (js.message || 'Errore durante l’invio'), false);
        return;
      }

      // OK
      const toName = js.to_name || hiddenName.value || 'destinatario';
      const amt = Number(js.amount || payload.amount).toFixed(2);
      showToast(`✅ Inviati € ${amt} a ${toName}`, true);
      form.reset();
      syncContactName();

      // Aggiorna tabella: riga "DEBIT" (uscita) per il mittente
      if (tableBody) {
        const today = new Date();
        const d = today.toISOString().slice(0, 10);
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${d}</td>
          <td>P2P a ${toName}${payload.message ? ' — ' + payload.message : ''}</td>
          <td class="text-end"><span class="text-danger">-${amt} €</span></td>`;
        tableBody.prepend(tr);
      }
    } catch (e) {
      showToast('❌ Errore di rete', false);
    }
  });
})();
