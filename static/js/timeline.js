/* All todos' tabs (Past, Scheduled). A tab loads the first time it's opened and is added beside the
 * other; after that, switching only brings the kept one to the front (the last child of #overlay shows),
 * so unsaved Scheduled edits survive a look at Past. ✕ closes both. */
(function () {
  'use strict';

  document.addEventListener('click', e => {
    const tab = e.target.closest && e.target.closest('[data-timeline-tab]');
    if (!tab) return;
    const overlay = document.getElementById('overlay');
    const kept = overlay.querySelector(`:scope > [data-timeline-pane="${tab.dataset.timelineTab}"]`);
    if (!kept) return;  /* not loaded yet: htmx fetches it and adds it at the end */
    e.preventDefault();
    e.stopImmediatePropagation();
    if (kept !== overlay.lastElementChild) overlay.append(kept);
  }, true);

  /* Past's rail of days: the picked one in view (today, at the right end, at first); the arrows step a week. */
  function centre() {
    const days = document.querySelector('#overlay [data-rail-days]');
    if (!days || days._placed) return;
    days._placed = true;
    const on = days.querySelector('.is-on');
    days.style.scrollBehavior = 'auto';
    days.scrollLeft = on ? on.offsetLeft - days.offsetLeft - (days.clientWidth - on.offsetWidth) / 2 : days.scrollWidth;
    days.style.scrollBehavior = '';
  }

  document.addEventListener('click', e => {
    const step = e.target.closest && e.target.closest('[data-rail-step]');
    if (!step) return;
    const days = step.closest('[data-rail]').querySelector('[data-rail-days]');
    days.scrollBy({ left: Number(step.dataset.railStep) * 7 * 28 });
  });

  document.addEventListener('htmx:after:swap', centre);
  centre();
})();
