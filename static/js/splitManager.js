(function () {
  const q = (sel) => document.querySelector(sel);

  const els = {
    groupSelect: q('#splitGroupSelect'),
    addMemberBtn: q('#splitAddMemberBtn'),
    deleteGroupBtn: q('#splitDeleteGroupBtn'),
    membersEmpty: q('#splitMembersEmpty'),
    membersWrap: q('#splitMembersTableWrap'),
    membersBody: q('#splitMembersTableBody'),
    amountInput: q('#splitAmount'),
    messageInput: q('#splitMessage'),
    sendBtn: q('#splitSendBtn'),
    requestBtn: q('#splitRequestBtn'),
    summary: q('#splitSummary'),
    groupForm: q('#splitGroupForm'),
    groupName: q('#splitGroupName'),
    memberForm: q('#splitMemberForm'),
    memberSelect: q('#splitMemberContact'),
    toastStack: q('#p2pToastStack'),
  };

  if (!els.groupSelect) {
    return;
  }

  const state = {
    groups: [],
    contacts: [],
    selectedGroupId: null,
  };

  const groupModal = (() => {
    const el = document.getElementById('modalSplitGroup');
    return el ? new bootstrap.Modal(el) : null;
  })();
  const memberModal = (() => {
    const el = document.getElementById('modalSplitMember');
    return el ? new bootstrap.Modal(el) : null;
  })();

  function showToast(message, ok = true) {
    if (!els.toastStack) return;
    const toast = document.createElement('div');
    toast.className = 'toast align-items-center text-bg-' + (ok ? 'success' : 'danger') + ' border-0';
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Chiudi"></button>
      </div>`;
    els.toastStack.appendChild(toast);
    const instance = new bootstrap.Toast(toast, { delay: 3500 });
    instance.show();
    toast.addEventListener('hidden.bs.toast', () => toast.remove());
  }

  async function apiFetch(url, options = {}) {
    const opts = Object.assign({ credentials: 'same-origin' }, options);
    const headers = Object.assign({}, opts.headers);
    if (opts.body && !(opts.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    if (Object.keys(headers).length) {
      opts.headers = headers;
    }
    const res = await fetch(url, opts);
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      data = null;
    }
    if (!res.ok) {
      const err = new Error((data && data.message) || 'Errore inatteso');
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function currentGroup() {
    if (!state.selectedGroupId) return null;
    return state.groups.find((g) => g.group_id === state.selectedGroupId) || null;
  }

  function computeShares(amount, count) {
    if (!count || amount <= 0) return [];
    const centsTotal = Math.round(amount * 100);
    const base = Math.floor(centsTotal / count);
    const remainder = centsTotal - base * count;
    const shares = new Array(count).fill(base);
    for (let i = 0; i < remainder; i += 1) {
      shares[i] += 1;
    }
    return shares.map((c) => c / 100);
  }

  function renderSummary() {
    if (!els.summary) return;
    const group = currentGroup();
    if (!group || !group.members.length) {
      els.summary.textContent = '';
      return;
    }
    const amount = parseFloat(els.amountInput.value || '0');
    if (!amount || amount <= 0) {
      els.summary.textContent = `${group.members.length} persone`;
      return;
    }
    const shares = computeShares(amount, group.members.length);
    const min = Math.min(...shares);
    const max = Math.max(...shares);
    const same = Math.abs(max - min) < 0.01;
    const message = same
      ? `${group.members.length} persone • ${max.toFixed(2)}€ a testa`
      : `${group.members.length} persone • quote ${min.toFixed(2)}€ – ${max.toFixed(2)}€`;
    els.summary.textContent = message;
  }

  function renderMembers() {
    const group = currentGroup();
    const tbody = els.membersBody;
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!group || !group.members.length) {
      els.membersWrap && els.membersWrap.classList.add('d-none');
      els.membersEmpty && els.membersEmpty.classList.remove('d-none');
      renderSummary();
      return;
    }
    const amount = parseFloat(els.amountInput.value || '0');
    const shares = computeShares(amount > 0 ? amount : 0, group.members.length);
    group.members.forEach((member, idx) => {
      const tr = document.createElement('tr');
      const quote = shares[idx];
      tr.innerHTML = `
        <td>${member.display_name}</td>
        <td class="text-end">${shareLabel(quote)}</td>
        <td class="text-end">
          <button class="btn btn-outline-danger btn-sm" type="button" data-member-id="${member.member_id}">
            <i class="bi bi-x-lg"></i><span class="visually-hidden">Rimuovi</span>
          </button>
        </td>`;
      tbody.appendChild(tr);
    });
    els.membersWrap && els.membersWrap.classList.remove('d-none');
    els.membersEmpty && els.membersEmpty.classList.add('d-none');
    renderSummary();
  }

  function shareLabel(value) {
    if (!value && value !== 0) return '—';
    return `${value.toFixed(2)} €`;
  }

  function renderGroupOptions(selectId = null) {
    const select = els.groupSelect;
    if (!select) return;
    const groups = state.groups;
    if (!groups.length) {
      select.innerHTML = '<option value="">Nessun gruppo disponibile</option>';
      select.value = '';
      select.disabled = true;
      state.selectedGroupId = null;
      return;
    }
    select.disabled = false;
    select.innerHTML = groups.map((g) => `<option value="${g.group_id}">${g.name}</option>`).join('');
    const targetId = selectId || state.selectedGroupId || groups[0].group_id;
    state.selectedGroupId = targetId;
    select.value = targetId;
  }

  function updateControls() {
    const group = currentGroup();
    const members = group ? group.members.length : 0;
    const amount = parseFloat(els.amountInput.value || '0');

    if (els.addMemberBtn) {
      const hasGroup = Boolean(group);
      els.addMemberBtn.disabled = !hasGroup || !availableContacts().length;
    }
    if (els.deleteGroupBtn) {
      els.deleteGroupBtn.disabled = !group;
    }

    const canSplit = Boolean(group && members >= 1 && amount > 0);
    if (els.sendBtn) {
      const accountSelect = q('#p2pForm select[name="from_account_id"]');
      els.sendBtn.disabled = !canSplit || !accountSelect || !accountSelect.value;
    }
    if (els.requestBtn) {
      els.requestBtn.disabled = !canSplit;
    }
  }

  function availableContacts() {
    const group = currentGroup();
    const used = new Set(group ? group.members.map((m) => m.contact_id) : []);
    return state.contacts.filter((c) => c.target_user_id && c.target_account_id && !used.has(c.contact_id));
  }

  function renderMemberOptions() {
    if (!els.memberSelect) return;
    const options = availableContacts();
    const submitBtn = els.memberForm ? els.memberForm.querySelector('button[type="submit"]') : null;
    if (!options.length) {
      els.memberSelect.innerHTML = '<option value="">Nessun contatto disponibile</option>';
      els.memberSelect.disabled = true;
      submitBtn && submitBtn.setAttribute('disabled', 'disabled');
      return;
    }
    els.memberSelect.disabled = false;
    submitBtn && submitBtn.removeAttribute('disabled');
    els.memberSelect.innerHTML = options.map((c) => `<option value="${c.contact_id}">${c.display_name}</option>`).join('');
  }

  async function loadGroups(selectId = null) {
    try {
      const data = await apiFetch('/api/p2p/groups');
      state.groups = Array.isArray(data.groups) ? data.groups : [];
      renderGroupOptions(selectId);
      renderMembers();
      renderMemberOptions();
      updateControls();
    } catch (err) {
      showToast(err.message || 'Errore nel caricamento dei gruppi', false);
    }
  }

  async function loadContacts() {
    try {
      const data = await apiFetch('/api/contacts');
      state.contacts = Array.isArray(data) ? data : [];
      renderMemberOptions();
      updateControls();
    } catch (err) {
      showToast('Impossibile caricare i contatti', false);
    }
  }

  async function handleCreateGroup(event) {
    event.preventDefault();
    if (!els.groupName) return;
    const name = (els.groupName.value || '').trim();
    if (!name) {
      showToast('Inserisci un nome per il gruppo', false);
      return;
    }
    try {
      const res = await apiFetch('/api/p2p/groups', {
        method: 'POST',
        body: { name },
      });
      groupModal && groupModal.hide();
      els.groupForm && els.groupForm.reset();
      await loadGroups(res.group ? res.group.group_id : null);
      showToast('Gruppo creato con successo');
    } catch (err) {
      showToast(err.message || 'Errore durante la creazione del gruppo', false);
    }
  }

  async function handleAddMember(event) {
    event.preventDefault();
    const group = currentGroup();
    if (!group || !els.memberSelect) return;
    const contactId = els.memberSelect.value;
    if (!contactId) {
      showToast('Seleziona un contatto valido', false);
      return;
    }
    try {
      await apiFetch(`/api/p2p/groups/${group.group_id}/members`, {
        method: 'POST',
        body: { contact_id: contactId },
      });
      memberModal && memberModal.hide();
      els.memberForm && els.memberForm.reset();
      await loadGroups(group.group_id);
      showToast('Persona aggiunta al gruppo');
    } catch (err) {
      showToast(err.message || 'Errore durante l\'aggiunta', false);
    }
  }

  async function handleRemoveMember(memberId) {
    const group = currentGroup();
    if (!group) return;
    try {
      await apiFetch(`/api/p2p/groups/${group.group_id}/members/${memberId}`, { method: 'DELETE' });
      await loadGroups(group.group_id);
      showToast('Partecipante rimosso');
    } catch (err) {
      showToast(err.message || 'Errore durante la rimozione', false);
    }
  }

  async function handleDeleteGroup() {
    const group = currentGroup();
    if (!group) return;
    if (!confirm(`Eliminare il gruppo "${group.name}"?`)) return;
    try {
      await apiFetch(`/api/p2p/groups/${group.group_id}`, { method: 'DELETE' });
      state.selectedGroupId = null;
      await loadGroups();
      showToast('Gruppo eliminato');
    } catch (err) {
      showToast(err.message || 'Errore durante la cancellazione', false);
    }
  }

  function toggleProcessing(isLoading) {
    const buttons = [els.sendBtn, els.requestBtn, els.addMemberBtn, els.deleteGroupBtn];
    buttons.forEach((btn) => {
      if (!btn) return;
      if (isLoading) {
        btn.setAttribute('disabled', 'disabled');
      } else {
        btn.removeAttribute('disabled');
      }
    });
    updateControls();
  }

  async function performSplit(mode) {
    const group = currentGroup();
    if (!group || !group.members.length) {
      showToast('Seleziona un gruppo con almeno un partecipante', false);
      return;
    }
    const amount = parseFloat(els.amountInput.value || '0');
    if (!amount || amount <= 0) {
      showToast('Inserisci un importo valido', false);
      return;
    }
    const payload = {
      amount: amount.toFixed(2),
      mode,
    };
    const note = (els.messageInput.value || '').trim();
    if (note) payload.message = note;
    if (mode === 'send') {
      const accountSelect = q('#p2pForm select[name="from_account_id"]');
      if (!accountSelect || !accountSelect.value) {
        showToast('Scegli il conto mittente dal modulo P2P', false);
        return;
      }
      payload.from_account_id = accountSelect.value;
    }
    try {
      toggleProcessing(true);
      const res = await apiFetch(`/api/p2p/groups/${group.group_id}/split`, {
        method: 'POST',
        body: payload,
      });
      const count = Array.isArray(res.results) ? res.results.length : group.members.length;
      if (mode === 'send') {
        showToast(`Quote inviate a ${count} persone.`);
      } else {
        showToast(`Richieste inviate a ${count} persone.`);
      }
    } catch (err) {
      showToast(err.message || 'Operazione non riuscita', false);
    } finally {
      toggleProcessing(false);
    }
  }

  function handleGroupChange(event) {
    state.selectedGroupId = event.target.value || null;
    renderMemberOptions();
    renderMembers();
    updateControls();
  }

  function handleAmountInput() {
    renderMembers();
    updateControls();
  }

  function bindEvents() {
    els.groupSelect && els.groupSelect.addEventListener('change', handleGroupChange);
    els.amountInput && els.amountInput.addEventListener('input', handleAmountInput);
    if (els.sendBtn) {
      els.sendBtn.addEventListener('click', () => performSplit('send'));
    }
    if (els.requestBtn) {
      els.requestBtn.addEventListener('click', () => performSplit('request'));
    }
    if (els.groupForm) {
      els.groupForm.addEventListener('submit', handleCreateGroup);
    }
    if (els.memberForm) {
      els.memberForm.addEventListener('submit', handleAddMember);
    }
    if (els.deleteGroupBtn) {
      els.deleteGroupBtn.addEventListener('click', handleDeleteGroup);
    }
    if (els.membersBody) {
      els.membersBody.addEventListener('click', (ev) => {
        const btn = ev.target.closest('[data-member-id]');
        if (!btn) return;
        handleRemoveMember(btn.getAttribute('data-member-id'));
      });
    }
    const memberModalEl = document.getElementById('modalSplitMember');
    if (memberModalEl) {
      memberModalEl.addEventListener('show.bs.modal', renderMemberOptions);
    }
  }

  async function init() {
    bindEvents();
    await Promise.all([loadContacts(), loadGroups()]);
    updateControls();
  }

  init();
})();
