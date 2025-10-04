document.addEventListener('DOMContentLoaded', () => {
  const track = document.querySelector('.slider-track');
  if (!track) return;

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

  track.querySelectorAll('.acct-card').forEach(card => {
    card.setAttribute('draggable', 'true');
    card.addEventListener('dragstart', e => {
      card.classList.add('dragging');
      track.classList.add('drag-target');
      e.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', async () => {
      card.classList.remove('dragging');
      track.classList.remove('drag-target');
      await saveOrder();
    });
  });

  track.addEventListener('dragover', e => {
    e.preventDefault();
    const dragging = track.querySelector('.dragging');
    if (!dragging) return;
    const after = getCardAfter(track, e.clientX);
    if (after == null) track.appendChild(dragging);
    else track.insertBefore(dragging, after);
  });

  function getCardAfter(container, x) {
    const cards = [...container.querySelectorAll('.acct-card:not(.dragging)')];
    return cards.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = x - (box.left + box.width / 2);
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  }

  async function saveOrder() {
    const ids = [...track.querySelectorAll('.acct-card')].map(c => parseInt(c.dataset.id, 10));
    try {
      const res = await fetch(window.REORDER_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ order: ids })
      });
      if (!res.ok) console.error('reorder failed', res.status);
    } catch (e) { console.error('reorder save error', e); }
  }
});
