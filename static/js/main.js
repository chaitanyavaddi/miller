/* Page-wide behaviour: htmx setup, the account menu, the overlay.
 * htmx 4: events are colon-separated (htmx:config:request) and the request is on event.detail.ctx. */
(function () {
  'use strict';

  const SAFE_METHODS = /^(GET|HEAD|OPTIONS|TRACE)$/i;

  /* Read from the page, not document.cookie: on localhost another app's csrftoken can sit beside ours. */
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  document.addEventListener('htmx:config:request', e => {
    const request = e.detail && e.detail.ctx && e.detail.ctx.request;
    if (!request || SAFE_METHODS.test(request.method || 'GET')) return;
    request.headers = request.headers || {};
    request.headers['X-CSRFToken'] = csrfToken();
  });

  document.addEventListener('htmx:response:error', e => {
    const ctx = (e.detail && e.detail.ctx) || {};
    console.error('htmx request failed', ctx.status);
  });

  function toggleMenu(open) {
    const menu = document.getElementById('account-menu');
    if (menu) menu.classList.toggle('account-menu--open', open);
  }

  function closeOverlay() {
    const overlay = document.getElementById('overlay');
    if (overlay) overlay.replaceChildren();
  }

  document.addEventListener('click', e => {
    if (!e.target.closest('.topbar__actions')) toggleMenu(false);
    const action = e.target.closest('[data-action]');
    if (!action) return;
    if (action.dataset.action === 'menu-toggle') toggleMenu();
    if (action.dataset.action === 'menu-close') toggleMenu(false);
    if (action.dataset.action === 'overlay-close') closeOverlay();
  });

  /* — toasts leave on their own after a few seconds, and wait while the pointer is on them — */
  const TOAST_MS = 6000;

  function armToast(toast) {
    if (toast._init) return;
    toast._init = true;
    let timer;
    const leave = () => {
      toast.classList.add('toast__item--leaving');
      setTimeout(() => toast.remove(), 200);
    };
    const start = () => { timer = setTimeout(leave, TOAST_MS); };
    toast.addEventListener('mouseenter', () => clearTimeout(timer));
    toast.addEventListener('mouseleave', start);
    start();
  }

  new MutationObserver(() => document.querySelectorAll('.toast__item').forEach(armToast))
    .observe(document.documentElement, { childList: true, subtree: true });

  /* — first visit: tell the server the browser's time zone, so the user's days start at their
   *   midnight. Only while none is saved; one the user picks in settings is never overridden. — */
  const detect = document.getElementById('timezone-detect');
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (detect && zone) {
    fetch(detect.dataset.url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      body: new URLSearchParams({ timezone: zone, detected: '1' }),
    }).then(response => {
      if (response.status === 200) document.body.dispatchEvent(new Event('board-changed'));
    }).catch(() => {});
  }

  /* Settings re-renders whole on every save: the sections that were open stay open. Remembered
   * only while the overlay shows settings; closing it forgets. */
  const overlayEl = document.getElementById('overlay');
  let openGroups = null;
  if (overlayEl) {
    overlayEl.addEventListener('toggle', e => {
      if (!e.target.matches('.settings__group')) return;
      openGroups = [...overlayEl.querySelectorAll('.settings__group[open]')].map(g => g.dataset.group);
    }, true);
    new MutationObserver(() => {
      const groups = overlayEl.querySelectorAll('.settings__group');
      if (!groups.length) { openGroups = null; return; }
      if (openGroups) groups.forEach(g => { g.open = openGroups.includes(g.dataset.group); });
    }).observe(overlayEl, { childList: true });
  }

  /* Sent by the server (HX-Trigger) or by another script once the overlay has done its job. */
  document.addEventListener('overlay-close', closeOverlay);

  document.addEventListener('keydown', e => {
    const overlay = document.getElementById('overlay');
    if (e.key !== 'Escape') return;
    if (document.querySelector('.account-menu--open')) toggleMenu(false);
    else if (overlay && overlay.firstElementChild) closeOverlay();
  });
})();
