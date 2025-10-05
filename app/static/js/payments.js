
(function () {
  const $ = (sel) => document.querySelector(sel);

  // ----- Account card highlight + symbol (optional; safe if not present) -----
  document.querySelectorAll('.acct-card').forEach(card => {
    card.addEventListener('click', () => {
      const radio = card.querySelector('.acct-radio');
      if (radio) radio.checked = true;
      document.querySelectorAll('.acct-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');

      const symEl = document.getElementById('amtSymbol');
      const sym = card.dataset?.symbol || '';
      if (symEl && sym) symEl.textContent = sym;
    });
  });

  // ----- Categories & Subcategories -----
  const catSel = $('#selectCategory');
  const subSel = $('#selectSubcategory');
  const hidden = $('#category_id');

  const CAT_DATA = Array.isArray(window.CAT_DATA) ? window.CAT_DATA : [];
  // Ensure numeric or null, not the string "null"
  const parseMaybeNum = (v) => (v === null || v === undefined || v === 'null') ? null : Number(v);
  let selParentId = parseMaybeNum(window.SELECTED_PARENT_ID);
  let selChildId  = parseMaybeNum(window.SELECTED_CHILD_ID);

  function fillCategories() {
    if (!catSel) return;
    const opts = ['<option value="">— Select a category —</option>'];
    for (const p of CAT_DATA) {
      const selected = (selParentId !== null && Number(p.id) === selParentId) ? ' selected' : '';
      opts.push(`<option value="${p.id}"${selected}>${p.name ?? ''}</option>`);
    }
    catSel.innerHTML = opts.join('');
    fillSubcategories();
  }

  function fillSubcategories() {
    if (!subSel || !catSel) return;
    const parentId = Number(catSel.value || 0);
    const parent = CAT_DATA.find(p => Number(p.id) === parentId);
    const children = Array.isArray(parent?.children) ? parent.children : [];

    const opts = ['<option value="">— Optional subcategory —</option>'];
    for (const ch of children) {
      const selected = (selChildId !== null && Number(ch.id) === selChildId) ? ' selected' : '';
      opts.push(`<option value="${ch.id}"${selected}>${ch.name ?? ''}</option>`);
    }
    subSel.innerHTML = opts.join('');

    // hidden category_id = sub if chosen, else parent
    if (hidden) hidden.value = subSel.value || catSel.value || '';
  }

  if (catSel && subSel) {
    catSel.addEventListener('change', () => { selChildId = null; fillSubcategories(); });
    subSel.addEventListener('change', () => { if (hidden) hidden.value = subSel.value || catSel.value || ''; });
    fillCategories();
  }
})();

// --- Edit Account modal prefill ---
(function () {
  const $ = (id) => document.getElementById(id);

  function getSelectedCard() {
    const checked = document.querySelector('.acct-radio:checked');
    if (checked) return checked.closest('.acct-card');
    // fallback: first card if none selected
    return document.querySelector('.acct-card');
  }

  function setSelectValue(selectEl, raw) {
    if (!selectEl) return;
    if (raw == null) return;
    const want = String(raw).trim();
    // exact match
    const exact = [...selectEl.options].find(o => o.value === want);
    if (exact) { selectEl.value = exact.value; return; }
    // try normalized matches
    const upper = want.toUpperCase();
    const lower = want.toLowerCase();
    const u = [...selectEl.options].find(o => o.value.toUpperCase?.() === upper);
    if (u) { selectEl.value = u.value; return; }
    const l = [...selectEl.options].find(o => o.value.toLowerCase?.() === lower);
    if (l) { selectEl.value = l.value; return; }
    // else leave as-is
  }

  function prefillEditFrom(card) {
    if (!card) return;
    const id   = card.dataset.id || '';
    const name = card.dataset.name || '';
    const curr = card.dataset.currency || 'KRW';       // "KRW" / "BDT"
    const type = card.dataset.type || 'bank';          // "bank"/"credit"/"cash"/"mobile_wallet"
    const init = card.dataset.initial ?? '';           // string like "0.00"
    const act  = (card.dataset.active === '1' || card.dataset.active === 'true');

    $('accEditId')?.setAttribute('value', id);
    const nameEl = $('accEditName');
    if (nameEl) nameEl.value = name;

    const initEl = $('accEditInitial');
    if (initEl) initEl.value = init;

    const activeEl = $('accEditActive');
    if (activeEl) activeEl.checked = !!act;

    setSelectValue($('accEditCurrency'), curr);
    setSelectValue($('accEditType'),     type);
  }

  // Click "Edit" button -> prefill from selected (or first) card
  const editBtn = document.getElementById('editAccountBtn');
  if (editBtn) {
    editBtn.addEventListener('click', () => {
      prefillEditFrom(getSelectedCard());
    });
  }

  // Also prefill when the modal actually opens (covers keyboard shortcuts, etc.)
  const editModal = document.getElementById('accountEditModal');
  if (editModal) {
    editModal.addEventListener('show.bs.modal', () => {
      prefillEditFrom(getSelectedCard());
    });
  }
})();
// --- Set Credit Limit modal prefill & button state ---
(function () {
  const setLimitBtn     = document.getElementById('setLimitBtn');
  const limitAccountId  = document.getElementById('limitAccountId');
  const limitAccountName= document.getElementById('limitAccountName');
  const limitCurrencySym= document.getElementById('limitCurrencySym');
  const limitValue      = document.getElementById('limitValue');

  function getActiveCard() {
    return document.querySelector('.acct-card.active');
  }

  function refreshLimitButtonState() {
    const card = getActiveCard();
    if (!card) { setLimitBtn.disabled = true; return; }
    const type = card.dataset.type; // 'bank' | 'credit' | ...
    setLimitBtn.disabled = (type !== 'credit');
  }

  // Update button state when selection changes
  document.addEventListener('click', (e) => {
    if (e.target.closest('.acct-card')) {
      // Let your existing selection code run first, then:
      setTimeout(refreshLimitButtonState, 0);
    }
  });

  // When opening the modal, prefill
  const setLimitModal = document.getElementById('setLimitModal');
  setLimitModal.addEventListener('show.bs.modal', () => {
    const card = getActiveCard();
    if (!card) return;

    limitAccountId.value   = card.dataset.id;
    limitAccountName.value = card.dataset.name || 'Selected account';
    limitCurrencySym.textContent = card.dataset.symbol || '';

    // Use the current available limit if we have it; fallback to 0
    // If you want to surface current value: add data-limit="{{ a.credit_limit or 0 }}"
    const avail = card.dataset.limit ? Number(card.dataset.limit) : 0;
    limitValue.value = avail.toFixed(2);
  });

  // On first load
  refreshLimitButtonState();
})();

// --- Settle Credit Card modal prefill & button state ---
(function () {
  const settleBtn = document.getElementById('settleBtn');
  const modal = document.getElementById('settleModal');

  const el = {
    cardId: document.getElementById('settleCardId'),
    cardName: document.getElementById('settleCardName'),
    pending: document.getElementById('settlePending'),
    available: document.getElementById('settleAvailable'),
    sym: document.getElementById('settleSym'),
    amount: document.getElementById('settleAmount'),
    full: document.getElementById('settleFullSwitch'),
    from: document.getElementById('settleFromAccount'),
    hint: document.getElementById('settleCurrencyHint'),
  };

  function money(n) {
    const v = Number(n || 0);
    return v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }

  function activeCard() {
    return document.querySelector('.acct-card.active');
  }

  function refreshButtons() {
    const card = activeCard();
    if (!card) { settleBtn.disabled = true; return; }
    settleBtn.disabled = (card.dataset.type !== 'credit');
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('.acct-card')) {
      setTimeout(refreshButtons, 0);
    }
  });

  modal.addEventListener('show.bs.modal', () => {
    const card = activeCard();
    if (!card) return;
    const id   = card.dataset.id;
    const name = card.dataset.name || '';
    const sym  = card.dataset.symbol || '';
    const cur  = card.dataset.currency || '';
    const pending = Number(card.dataset.pending || 0);
    const avail   = Number(card.dataset.limit || 0);

    el.cardId.value = id;
    el.cardName.value = name;
    el.pending.textContent = sym + money(pending);
    el.available.textContent = sym + money(avail);
    el.sym.textContent = sym;
    el.hint.textContent = 'Currency: ' + cur;

    // default: full settlement
    el.full.checked = true;
    el.amount.value = pending.toFixed(2);

    // filter "pay from" accounts by currency
    [...el.from.options].forEach(opt => {
      if (!opt.value) return;
      opt.hidden = (opt.dataset.currency !== cur);
    });
    el.from.value = ''; // force user to pick
  });

  // toggle full/manual
  el.full?.addEventListener('change', () => {
    const card = activeCard();
    const pending = Number(card?.dataset.pending || 0);
    if (el.full.checked) {
      el.amount.value = pending.toFixed(2);
      el.amount.readOnly = true;
    } else {
      el.amount.readOnly = false;
      el.amount.focus();
    }
  });

  // initial
  refreshButtons();
})();

// --- Set Account Balance modal prefill & button state ---
(function () {
  const setBalanceBtn = document.getElementById('setBalanceBtn');

  function activeCard() {
    return document.querySelector('.acct-card.active');
  }
  function refreshButtons() {
    const card = activeCard();
    if (!card) { setBalanceBtn.disabled = true; return; }
    setBalanceBtn.disabled = (card.dataset.type === 'credit'); // only non-credit
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('.acct-card')) {
      setTimeout(refreshButtons, 0);
    }
  });

  // Prefill modal
  const sbModal = document.getElementById('setBalanceModal');
  sbModal.addEventListener('show.bs.modal', () => {
    const card = activeCard();
    if (!card) return;

    const id   = card.dataset.id;
    const name = card.dataset.name || '';
    const sym  = card.dataset.symbol || '';
    const cur  = card.dataset.currency || '';
    const shown= Number(card.dataset.balance || 0);

    document.getElementById('sbAccountId').value = id;
    document.getElementById('sbAccountName').value = name;
    document.getElementById('sbCurrent').textContent = sym + shown.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    document.getElementById('sbCurrency').textContent = cur;
    document.getElementById('sbSym').textContent = sym;
    document.getElementById('sbTarget').value = shown.toFixed(2);
  });

  // Init
  refreshButtons();
})();

// --- Create Account modal: adjust amount field based on type ---

document.addEventListener('DOMContentLoaded', () => {
  const createModal = document.getElementById('accountNewModal');
  if (!createModal) return;

  createModal.addEventListener('show.bs.modal', (evt) => {
    const root     = evt.target;
    const typeSel  = root.querySelector('#acctTypeCreate');
    const amtLabel = root.querySelector('[data-role="amt-label"]');
    const amtInput = root.querySelector('[data-role="amt-input"]');
    const amtHelp  = root.querySelector('[data-role="amt-help"]');

    if (!typeSel || !amtLabel || !amtInput || !amtHelp) return;

    const updateField = () => {
      const t = (typeSel.value || '').toLowerCase();
      if (t === 'credit') {
        amtLabel.textContent   = 'Credit limit';
        amtInput.name          = 'credit_limit';
        amtInput.placeholder   = 'e.g., 3,000,000.00';
        amtHelp.textContent    = 'Set the available credit limit for this card.';
      } else {
        amtLabel.textContent   = 'Initial balance';
        amtInput.name          = 'initial_balance';
        amtInput.placeholder   = '0.00';
        amtHelp.textContent    = 'Opening balance for this account.';
      }
    };

    updateField();
    typeSel.addEventListener('change', updateField);
  });
});


document.addEventListener('DOMContentLoaded', () => {
  const editModal = document.getElementById('accountEditModal');
  const editBtn   = document.getElementById('editAccountBtn'); // optional

  // Your existing function to get active card, adapt if needed
  function getCard(el) { return el.closest('.acct-card'); }

  document.addEventListener('click', (e) => {
    const card = getCard(e.target);
    if (!card) return;

    // If inactive, stop selection and open Edit modal
    if (card.classList.contains('is-inactive') || card.dataset.active === '0') {
      e.preventDefault();

      // Prefill edit modal fields
      document.getElementById('accEditId').value = card.dataset.id;
      document.getElementById('accEditName').value = card.dataset.name || '';
      document.getElementById('accEditCurrency').value = card.dataset.currency || 'KRW';
      document.getElementById('accEditType').value = card.dataset.type || 'bank';
      // set Active checkbox to false
      const chk = document.getElementById('accEditActive');
      if (chk) chk.checked = false;

      // show the modal
      const bsModal = new bootstrap.Modal(editModal);
      bsModal.show();

      return; // don't toggle selection
    }

    // else let your existing selection logic run (possibly via other code)
  });
});

document.addEventListener('DOMContentLoaded', () => {
  function money(n) {
    const v = Number(n || 0);
    return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  document.querySelectorAll('.acct-card[data-type="credit"]').forEach(card => {
    const avail   = Number(card.dataset.limit ?? 0);    // available
    const pending = Number(card.dataset.pending ?? 0);  // pending (absolute)
    const total   = avail + pending;                    // inferred total capacity
    const usedPct = total > 0 ? (pending / total) * 100 : 0;

    // Progress width
    const fill = card.querySelector('.limit-bar-fill');
    if (fill) fill.style.width = Math.min(100, Math.max(0, usedPct)).toFixed(2) + '%';

    // Color thresholds
    card.classList.remove('ok', 'warn', 'danger');
    if (usedPct >= 80)      card.classList.add('danger');
    else if (usedPct >= 50) card.classList.add('warn');
    else                    card.classList.add('ok');

    // Labels
    const sym = card.dataset.symbol || '';
    const availEl = card.querySelector('[data-role="avail"]');
    const usedEl  = card.querySelector('[data-role="used"]');
    if (availEl) availEl.textContent = sym + money(avail);
    if (usedEl)  usedEl.textContent  = `${usedPct.toFixed(0)}% used`;
    
    // ARIA
    const bar = card.querySelector('.limit-bar');
    if (bar) {
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', String(total > 0 ? 100 : 0));
      bar.setAttribute('aria-valuenow', usedPct.toFixed(0));
    }
  });
});

document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("toggle-inactive-btn");
  var track = document.getElementById("accounts-track");
  if (!btn || !track) return;

  btn.addEventListener("click", function () {
    var showing = btn.getAttribute("data-show") === "1";
    if (showing) {
      track.classList.remove("show-inactive");
      btn.textContent = "Show inactive";
      btn.setAttribute("data-show", "0");
      btn.setAttribute("aria-pressed", "false");
      // ensure one visible radio stays checked
      var visChecked = track.querySelector(".acct-card:not(.is-inactive) input.acct-radio:checked");
      if (!visChecked) {
        var firstVis = track.querySelector(".acct-card:not(.is-inactive) input.acct-radio:not([disabled])");
        if (firstVis) firstVis.checked = true;
      }
    } else {
      track.classList.add("show-inactive");
      btn.textContent = "Hide inactive";
      btn.setAttribute("data-show", "1");
      btn.setAttribute("aria-pressed", "true");
    }
  });
});