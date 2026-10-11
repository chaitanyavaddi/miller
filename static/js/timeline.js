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
})();
