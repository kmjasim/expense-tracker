// static/js/slider_nav.js
(function () {
  const ready = (fn) => (document.readyState !== 'loading')
    ? fn()
    : document.addEventListener('DOMContentLoaded', fn);

  ready(() => {
    const slider = document.querySelector('.account-slider');
    if (!slider) return;

    const track = slider.querySelector('.slider-track');
    const prev  = slider.querySelector('.slider-nav.prev');
    const next  = slider.querySelector('.slider-nav.next');
    if (!track || !prev || !next) return;

    // Measure one "step": card width + gap
    function stepSize() {
      const firstCard = track.querySelector('.acct-card');
      if (!firstCard) return 250; // fallback
      const cardRect = firstCard.getBoundingClientRect();
      const styles = window.getComputedStyle(track);
      const gap = parseFloat(styles.columnGap || styles.gap || 0);
      return Math.round(cardRect.width + gap);
    }

    // Smooth scroll by +/- one step
    function scrollByStep(dir) {
      track.scrollBy({ left: dir * stepSize(), behavior: 'smooth' });
    }

    // Enable/disable arrows based on scroll position
    function updateArrows() {
      const max = track.scrollWidth - track.clientWidth - 1; // -1 to avoid float jitter
      const atStart = track.scrollLeft <= 0;
      const atEnd   = track.scrollLeft >= max;

      prev.toggleAttribute('disabled', atStart);
      prev.setAttribute('aria-disabled', String(atStart));

      next.toggleAttribute('disabled', atEnd);
      next.setAttribute('aria-disabled', String(atEnd));
    }

    // Clicks
    prev.addEventListener('click', () => scrollByStep(-1));
    next.addEventListener('click', () => scrollByStep(1));

    // Update on these events
    ['scroll', 'resize'].forEach(evt => {
      const target = evt === 'scroll' ? track : window;
      target.addEventListener(evt, () => {
        // throttle with rAF
        if (updateArrows._tick) return;
        updateArrows._tick = requestAnimationFrame(() => {
          updateArrows._tick = null;
          updateArrows();
        });
      }, { passive: true });
    });

    // Initial state
    updateArrows();
  });
})();
