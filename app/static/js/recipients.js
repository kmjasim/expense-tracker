/* static/js/recipients.js */
(function () {
  // ---------------------------------------------------------------------------
  // Guard: run once
  // ---------------------------------------------------------------------------
  if (window.__recipientsInit) return;
  window.__recipientsInit = true;

  // ---------------------------------------------------------------------------
  // DOM handles
  // ---------------------------------------------------------------------------
  const list     = document.getElementById('recipientList');
  const search   = document.getElementById('recipientSearch');
  const addForm  = document.getElementById('recipientForm');
  const editBtn  = document.getElementById('editRecipientBtn');
  const delBtn   = document.getElementById('deleteRecipientBtn');
  const editForm = document.getElementById('recipientEditForm');

  // Convenience: safe Modal hide
  function hideModalById(id) {
    try {
      const el = document.getElementById(id);
      if (!el) return;
      const inst = (window.bootstrap && bootstrap.Modal) ? bootstrap.Modal.getInstance(el) : null;
      if (inst && typeof inst.hide === 'function') inst.hide();
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Config JSON (create/update/delete URLs)
  // ---------------------------------------------------------------------------
  function readConfig() {
    try {
      const el = document.getElementById('recipients-config');
      return el ? JSON.parse(el.textContent) : {};
    } catch (e) {
      console.warn('[Recipients] Bad config JSON', e);
      return {};
    }
  }
  const CFG = readConfig();

  // ---------------------------------------------------------------------------
  // URL helper: replace a "/0" segment with real id, flex patterns supported
  // ---------------------------------------------------------------------------
  function urlFromZeroFlexible(tpl, id) {
    const u = new URL(tpl, location.origin);
    const enc = encodeURIComponent(id);
    u.pathname = u.pathname
      .replace(/\/0\/delete$/, `/${enc}/delete`)   // /recipients/0/delete -> /recipients/{id}/delete
      .replace(/\/delete\/0$/, `/delete/${enc}`)   // /recipients/delete/0 -> /recipients/delete/{id}
      .replace(/\/0(?=\/|$)/, `/${enc}`);          // fallback: first /0 segment
    return u.toString();
  }
  // Back-compat alias (your previous code used urlFromZero)
  const urlFromZero = urlFromZeroFlexible;

  // ---------------------------------------------------------------------------
  // Error helper
  // ---------------------------------------------------------------------------
  function showServerError(res, data, fallback) {
    const msg = (data && (data.error || data.message)) || fallback || 'Request failed';
    alert(msg);
    console.error('[Recipients] Server error', { url: res?.url, status: res?.status, data });
  }

  // ---------------------------------------------------------------------------
  // CSRF helpers
  // ---------------------------------------------------------------------------
  const getCsrf = () =>
    (document.querySelector('meta[name="csrf-token"]')?.content
    || document.querySelector('input[name="csrf_token"]')?.value
    || null);

  const withCsrfHeaders = (h = {}) => {
    const t = getCsrf();
    if (t) h['X-CSRFToken'] = t;
    h['X-Requested-With'] = 'XMLHttpRequest';
    return h;
  };

  const appendCsrf = (fd) => {
    const t = getCsrf();
    if (t && !fd.has('csrf_token')) fd.append('csrf_token', t);
    return fd;
  };

  // ---------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------
  function dispatchChange(el) {
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function applySelection(id, name) {
    // Update <select name="recipient_id"> across the page
    document.querySelectorAll('select[name="recipient_id"]').forEach(sel => {
      let opt = sel.querySelector(`option[value="${id}"]`);
      if (!opt && id) {
        opt = document.createElement('option');
        opt.value = id;
        opt.textContent = name || ('Recipient #' + id);
        sel.appendChild(opt);
      }
      sel.value = id || '';
      dispatchChange(sel);
    });

    // Update hidden inputs
    document.querySelectorAll('input[name="recipient_id"]').forEach(inp => {
      inp.value = id || '';
      dispatchChange(inp);
    });

    // If no id selected, allow typing a free name into recipient_name
    document.querySelectorAll('input[name="recipient_name"]').forEach(inp => {
      inp.value = id ? '' : (name || '');
      dispatchChange(inp);
    });

    // Any display chips
    document.querySelectorAll('.recipient-display').forEach(el => {
      el.textContent = name || '';
    });
  }

  // Ensure one is active on initial load (optional)
  (function ensureInitialActive() {
    if (!list) return;
    const active = list.querySelector('.recipient-item.active');
    const first  = list.querySelector('.recipient-item');
    if (!active && first) {
      first.classList.add('active');
    }
    const pick = list.querySelector('.recipient-item.active');
    if (pick) applySelection(pick.dataset.recipientId || '', pick.dataset.recipientName || '');
  })();

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------
  list?.addEventListener('click', (e) => {
    const btn = e.target.closest('.recipient-item');
    if (!btn || !list.contains(btn)) return;
    list.querySelectorAll('.recipient-item').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applySelection(btn.dataset.recipientId || '', btn.dataset.recipientName || '');
  });

  // ---------------------------------------------------------------------------
  // Search filter
  // ---------------------------------------------------------------------------
  const filterRecipients = (q) => {
    const qn = (q || '').trim().toLowerCase();
    list?.querySelectorAll('.recipient-item').forEach(btn => {
      const name = (btn.dataset.recipientName || '').toLowerCase();
      const acct = (btn.dataset.recipientAcct || '').toLowerCase();
      const show = !qn || name.includes(qn) || acct.includes(qn);
      btn.style.display = show ? '' : 'none';
    });
  };
  search?.addEventListener('input', (e) => filterRecipients(e.target.value));

  // ---------------------------------------------------------------------------
  // Anti-flicker forms (prevent Enter submit, use reportValidity)
  // ---------------------------------------------------------------------------
  function hardenFormAgainstFlicker(form) {
    if (!form) return;
    form.setAttribute('novalidate', 'novalidate'); // use reportValidity manually
    form.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.target.matches('textarea')) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }
  hardenFormAgainstFlicker(addForm);
  hardenFormAgainstFlicker(editForm);

  // ---------------------------------------------------------------------------
  // ADD
  // ---------------------------------------------------------------------------
  addForm?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();

    if (!addForm.checkValidity()) {
      addForm.reportValidity();
      return;
    }

    const submitter = addForm.querySelector('[type="submit"]');
    submitter?.setAttribute('disabled', 'disabled');

    try {
      const action = CFG.createUrl || addForm.action;
      const fd = appendCsrf(new FormData(addForm));

      const res = await fetch(action, {
        method: 'POST',
        headers: withCsrfHeaders(),
        body: fd
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok) {
        showServerError(res, data, `Add failed (${res.status})`);
        return;
      }

      // Build new list button
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'recipient-item active';
      btn.dataset.recipientId   = String(data.id);
      btn.dataset.recipientName = data.name || '';
      btn.dataset.recipientAcct = data.acct || '';
      btn.innerHTML = `
        <div class="w-100 text-start">
          <div class="fw-semibold small">${data.name || ''}</div>
          ${data.acct ? `<div class="text-muted tiny">${data.acct}</div>` : ''}
        </div>
      `;

      // Activate this, deactivate others
      list?.querySelectorAll('.recipient-item').forEach(b => b.classList.remove('active'));
      list?.prepend(btn);

      applySelection(String(data.id), data.name || '');

      hideModalById('recipientModal');
      addForm.reset();
    } catch (err) {
      alert(err?.message || String(err));
      console.error('[Recipients][ADD] exception', err);
    } finally {
      submitter?.removeAttribute('disabled');
    }
  }, { capture: true });

  // ---------------------------------------------------------------------------
  // EDIT (prefill)
  // ---------------------------------------------------------------------------
  editBtn?.addEventListener('click', () => {
    const active = list?.querySelector('.recipient-item.active');
    if (!active) {
      alert('Select a recipient first.');
      return;
    }

    const id = active.dataset.recipientId;
    const action = urlFromZero(CFG.updateUrlZero, id);
    if (editForm) editForm.action = action;

    // Fill fields
    const get = (id) => document.getElementById(id);

    const type = (active.dataset.recipientType || 'person').replace(/^self_$/, 'self');

    get('recEditId').value      = id || '';
    get('recEditName').value    = active.dataset.recipientName || '';
    get('recEditCountry').value = active.dataset.recipientCountry || '';
    get('recEditService').value = active.dataset.recipientService || '';
    get('recEditAcct').value    = active.dataset.recipientAcct || '';
    get('recEditNotes').value   = active.dataset.recipientNotes || '';
    get('recEditFav').checked   = (active.dataset.recipientFav === '1');

    // Selects
    const typeSel = get('recEditType');
    if (typeSel) typeSel.value = type;

    const methodSel = get('recEditMethod');
    if (methodSel) methodSel.value = active.dataset.recipientMethod || '';
  });

  // ---------------------------------------------------------------------------
  // EDIT (submit)
  // ---------------------------------------------------------------------------
  editForm?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();

    if (!editForm.checkValidity()) {
      editForm.reportValidity();
      return;
    }

    const submitter = editForm.querySelector('[type="submit"]');
    submitter?.setAttribute('disabled', 'disabled');

    try {
      const fd = appendCsrf(new FormData(editForm));
      const res = await fetch(editForm.action, {
        method: 'POST',
        headers: withCsrfHeaders(),
        body: fd
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok) {
        showServerError(res, data, `Update failed (${res.status})`);
        return;
      }

      // Update the active button text and dataset
      const active = list?.querySelector('.recipient-item.active');
      if (active) {
        active.dataset.recipientId    = String(data.id);
        active.dataset.recipientName  = data.name || '';
        active.dataset.recipientAcct  = data.acct || '';

        const nameEl = active.querySelector('.fw-semibold');
        if (nameEl) nameEl.textContent = data.name || '';

        const acctLine = active.querySelector('.text-muted.tiny');
        if (acctLine) {
          if (data.acct) {
            acctLine.textContent = data.acct;
          } else {
            acctLine.remove();
          }
        } else if (data.acct) {
          active.querySelector('.w-100')
            ?.insertAdjacentHTML('beforeend', `<div class="text-muted tiny">${data.acct}</div>`);
        }
      }

      applySelection(String(data.id), data.name || '');
      hideModalById('recipientEditModal');
    } catch (err) {
      alert(err?.message || String(err));
      console.error('[Recipients][EDIT] exception', err);
    } finally {
      submitter?.removeAttribute('disabled');
    }
  }, { capture: true });

  // ---------------------------------------------------------------------------
  // DELETE
  // ---------------------------------------------------------------------------
  delBtn?.addEventListener('click', async () => {
    const active = list?.querySelector('.recipient-item.active');
    if (!active) {
      alert('Select a recipient first.');
      return;
    }

    const id = active.dataset.recipientId;
    if (!id) {
      alert('Invalid recipient id.');
      return;
    }

    if (!confirm('Delete this recipient? This cannot be undone.')) return;

    const url = urlFromZeroFlexible(CFG.deleteUrlZero, id);
    delBtn.setAttribute('disabled', 'disabled');

    try {
      const fd = appendCsrf(new FormData());
      const res = await fetch(url, {
        method: 'POST',
        headers: withCsrfHeaders(),
        body: fd
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data?.ok) {
        showServerError(res, data, `Delete failed (${res.status})`);
        return;
      }

      // Remove and focus next
      const next = active.nextElementSibling || active.previousElementSibling;
      active.remove();
      if (next && next.classList?.contains('recipient-item')) {
        next.classList.add('active');
        applySelection(next.dataset.recipientId || '', next.dataset.recipientName || '');
      } else {
        applySelection('', '');
      }
    } catch (err) {
      alert(err?.message || String(err));
      console.error('[Recipients][DELETE] exception', err);
    } finally {
      delBtn.removeAttribute('disabled');
    }
  });

})();
