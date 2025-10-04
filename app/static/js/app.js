
  // Year
  (function(){
    const y = document.querySelectorAll('[data-year]');
    const yr = String(new Date().getFullYear());
    y.forEach(n => n.textContent = yr);
  })();

  // Active link helper (only if your macro doesn't set .active/aria-current)
  (function(){
    const here = location.pathname.replace(/\/+$/,'');
    document.querySelectorAll('#sidebar a.sidebar-link, #mobileSidebar a.sidebar-link')
      .forEach(a => {
        const href = (a.getAttribute('href')||'').replace(/\/+$/,'');
        if (href && href === here) {
          a.classList.add('active');
          a.setAttribute('aria-current','page');
        }
      });
  })();

// Category selection and delete handling
// --------------------------------------------------
// Assumes category buttons have .category-item-btn class and data-id, data-name attributes
// Assumes delete buttons have .btn-del class and are inside a <form> with optional data-name attribute
document.addEventListener('click', (e) => {
  // Prevent delete clicks from toggling accordion
  if (e.target.closest('.btn-del')) {
    e.stopPropagation();
    // optional confirm inline:
    const f = e.target.closest('form');
    if (f && !confirm('Delete ' + (f.dataset.name || 'this') + '?')) {
      e.preventDefault();
    }
    return;
  }

  // Handle subcategory selection without reload
  const btn = e.target.closest('.category-item-btn');
  if (!btn) return;

  // visual active state
  document.querySelectorAll('.category-item-btn.active')
    .forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  const id = btn.dataset.id, name = btn.dataset.name;

  // Update URL query (no reload)
  const url = new URL(window.location);
  url.searchParams.set('category_id', id);
  history.replaceState(null, '', url);

  // Emit an event your page can listen for to refresh data
  document.dispatchEvent(new CustomEvent('category:selected', {
    detail: { id, name }
  }));

  // If you already load the right pane via Fetch/Ajax, trigger it here.
  // fetchAndRenderPayments(id)  // your function
});

