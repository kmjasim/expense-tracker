// static/js/fix-modals.js
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.modal').forEach(m => {
    if (m.parentElement !== document.body) {
      document.body.appendChild(m);
    }
  });
});
