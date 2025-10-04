/* static/js/transfers.js */
(function(){
  // ---------------- Tabs ----------------
  document.querySelectorAll('.tab-btn').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('show'));
      document.querySelector('#tab-' + tab)?.classList.add('show');
    });
  });

  // ---------------- Rails (arrows) ----------------
  function getRail(group){ return document.getElementById('rail-' + group); }
  function scrollRail(group, dir){
    const rail = getRail(group);
    if(!rail) return;
    const card = rail.querySelector('.account-card');
    const step = card ? (card.getBoundingClientRect().width + 10) : 200;
    rail.scrollBy({left: (dir==='next'? step : -step), behavior:'smooth'});
  }
  document.querySelectorAll('.rail-btn').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const group = btn.dataset.rail;
      const dir = btn.classList.contains('next') ? 'next' : 'prev';
      scrollRail(group, dir);
    });
  });

  // ---------------- Helpers ----------------
  function selectCard(card, options){
    const group = card.dataset.railGroup;
    const all = document.querySelectorAll('.account-card[data-rail-group="'+group+'"]');
    all.forEach(c=>c.classList.remove('selected'));
    card.classList.add('selected');

    if(options?.hiddenId){
      const hid = document.getElementById(options.hiddenId);
      if (hid) hid.value = card.dataset.accountId || '';
    }
    if(options?.hiddenCurrency && card.dataset.currency){
      const hc = document.getElementById(options.hiddenCurrency);
      if (hc) hc.value = card.dataset.currency;
    }
    if(typeof options?.onSelect === 'function'){
      options.onSelect(card);
    }
  }

  function filterRailByCurrency(railId, allowedCurrency){
    const rail = document.getElementById(railId);
    if(!rail) return;
    const allow = allowedCurrency ? allowedCurrency.toUpperCase() : null;
    rail.querySelectorAll('.account-card').forEach(card=>{
      const cur = (card.dataset.currency || '').toUpperCase();
      const show = allow ? (cur === allow) : true;
      card.classList.toggle('d-none', !show);
      if(!show) card.classList.remove('selected');
    });
  }

  function showAllCardsInRail(railId){
    const rail = document.getElementById(railId);
    if(!rail) return;
    rail.querySelectorAll('.account-card').forEach(card=>{
      card.classList.remove('d-none');
    });
  }

  // Disable same account in LOCAL "to"
  function applyLocalToDisables(){
    const fromId = (document.getElementById('local-from-id')?.value) || '';
    const toCards = document.querySelectorAll('.account-card[data-rail-group="local-to"]');
    toCards.forEach(c=>{
      if(c.dataset.accountId === fromId){ c.classList.add('disabled'); }
      else { c.classList.remove('disabled'); }
    });
  }

  // LOCAL: "to" shows same currency as "from"
  function applyLocalCurrencyFilter(fromCard){
    const cur = (fromCard?.dataset.currency || '').toUpperCase();
    if(!cur){ showAllCardsInRail('rail-local-to'); return; }
    filterRailByCurrency('rail-local-to', cur);
  }

  // INTL: "to" shows opposite currency of "from"
  function applyIntlToCurrencyFilter(fromCard){
    const cur = (fromCard?.dataset.currency || '').toUpperCase();
    if(!cur){ showAllCardsInRail('rail-intl-to'); return; }
    const opposite = cur === 'KRW' ? 'BDT' : (cur === 'BDT' ? 'KRW' : null);
    if(opposite){ filterRailByCurrency('rail-intl-to', opposite); }
    else { showAllCardsInRail('rail-intl-to'); }
  }

  // Recipient controls — keep visible, only disable/enable
  function setRecipientDisabled(scopePrefix, disabled){
    // scopePrefix: '' (intl) or 'local-'
    const wrap = document.getElementById(scopePrefix + 'recipient-wrap');
    if (wrap) {
      wrap.classList.toggle('opacity-75', !!disabled);    // subtle visual cue
      wrap.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    }
    const idInput   = document.getElementById(scopePrefix + 'recipient_id');
    const nameInput = document.getElementById(scopePrefix + 'recipient_name');
    const nodes = [idInput, nameInput].filter(Boolean);
    nodes.forEach(n=>{
      n.disabled = !!disabled;
      n.classList.toggle('is-disabled', !!disabled);
    });
  }

  // ---------------- Wire up rails ----------------
  const map = {
    'local-from': {
      hiddenId: 'local-from-id',
      hiddenCurrency: 'local-currency',
      onSelect: (card)=>{
        applyLocalToDisables();
        applyLocalCurrencyFilter(card);
      }
    },
    'local-to'  : { hiddenId: 'local-to-id' },
    'intl-from' : {
      hiddenId: 'intl-from-id',
      onSelect: (card)=>{
        applyIntlToCurrencyFilter(card);
      }
    },
    'intl-to'   : { hiddenId: 'intl-to-id' }
  };

  Object.keys(map).forEach(group=>{
    const rail = document.getElementById('rail-' + group);
    if(!rail) return;
    rail.addEventListener('click', (e)=>{
      const card = e.target.closest('.account-card');
      if(!card || card.classList.contains('disabled')) return;
      selectCard(card, map[group]);
    });
  });

  // ---------------- Self toggles ----------------
  // International: keep recipient visible; just disable it when self is checked
  const selfChk = document.getElementById('chk-self');
  const selfWrap = document.getElementById('self-bdt-wrap');
  const onIntlSelfChange = ()=>{
    if(!selfChk) return;
    if(selfChk.checked){
      selfWrap?.classList.remove('d-none');     // show BDT picker
      setRecipientDisabled('', true);           // disable intl recipient inputs (keep visible)
      // Ensure BDT cards are visible and preselect first BDT
      filterRailByCurrency('rail-intl-to', 'BDT');
      const firstBDT = document.querySelector('#rail-intl-to .account-card:not(.d-none):not(.disabled)');
      if (firstBDT) selectCard(firstBDT, map['intl-to']);
    } else {
      selfWrap?.classList.add('d-none');        // hide BDT picker when not self
      setRecipientDisabled('', false);          // enable intl recipient inputs
      const hidden = document.getElementById('intl-to-id');
      if(hidden) hidden.value = '';
      document.querySelectorAll('#rail-intl-to .account-card').forEach(c=>c.classList.remove('selected'));
    }
  };
  if(selfChk){
    selfChk.addEventListener('change', onIntlSelfChange);
    onIntlSelfChange(); // init to reflect current checkbox state
  }

  // Local/domestic: keep recipient visible; just disable when self is checked
  const localSelfChk = document.getElementById('local-chk-self');
  const onLocalSelfChange = ()=>{
    if(!localSelfChk) return;
    setRecipientDisabled('local-', !!localSelfChk.checked);
  };
  if(localSelfChk){
    localSelfChk.addEventListener('change', onLocalSelfChange);
    onLocalSelfChange(); // init
  }

  // ---------------- Optional preselect ----------------
  ['local-from','intl-from'].forEach(group=>{
    const first = document.querySelector('#rail-'+group+' .account-card:not(.d-none):not(.disabled)');
    if(first){ selectCard(first, map[group]); }
  });
})();
