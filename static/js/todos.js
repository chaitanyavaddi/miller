/* The todo list: search mode, selecting a row, drag to reorder. */
(function () {
  'use strict';

  const list = () => document.getElementById('list');
  const rows = () => document.getElementById('rows');
  /* Done rows close out the list but are not part of its order: nothing below moves or numbers them.
   * Timed rows (today's meetings) sort by their time after the user's own order, and stay put. */
  const ACTIVE = '.todo-row:not(.todo-row--done)';
  const ORDERED = '.todo-row:not(.todo-row--done)';  /* todos and meetings alike */
  const isOrdered = el => !!el && el.matches(ORDERED);
  const isSearching = () => !!list() && list().classList.contains('todo-list--searching');

  /* — search — */
  function openSearch() {
    if (!list()) return;
    list().classList.add('todo-list--searching');
    document.getElementById('q').focus();
  }

  function closeSearch() {
    const query = document.getElementById('q');
    if (query && query.value) {
      query.value = '';
      query.dispatchEvent(new Event('search', { bubbles: true }));
    }
    if (list()) list().classList.remove('todo-list--searching');
  }

  /* — selection: the server sends the thread; the row highlight and the `t` every list action
   *   carries are kept here, so closing or adding never changes what is being read. — */
  function select(row) {
    document.querySelectorAll('.todo-row--current').forEach(r => r.classList.remove('todo-row--current'));
    row.classList.add('todo-row--current');
    const selected = document.querySelector('#board-state [name=t]');
    if (selected) selected.value = row.dataset.id;
  }

  document.addEventListener('click', e => {
    const action = e.target.closest('[data-action]');
    if (action && action.dataset.action === 'search-open') openSearch();
    if (action && action.dataset.action === 'search-close') closeSearch();
    if (isSearching() && !e.target.closest('#list')) closeSearch();  /* clicking away puts the search back to its icon */
    const main = e.target.closest('.todo-row__main');
    if (main) select(main.closest('.todo-row'));
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const title = e.target.closest && e.target.closest('.title-form__input');
    if (title) {
      title.value = title.dataset.original;
      title.blur();
      return;
    }
    if (isSearching()) closeSearch();
    const newTitle = document.getElementById('new-title');
    if (document.activeElement === newTitle) newTitle.value = '';
  });

  /* — keyboard: ↑/↓ move through the list, Shift+↑/↓ move the selected todo, N adds one, / searches.
   *   Arrows stay with the list while the comment box is empty, so after picking a todo
   *   (which focuses its comment box) you can keep walking without leaving it. — */
  function isTyping(el) {
    return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
  }

  function arrowsBelongToList(el) {
    return !isTyping(el) || (el.id === 'comment-input' && !el.value);
  }

  function currentRow() {
    return document.querySelector('#rows .todo-row--current');
  }

  function step(direction) {
    const all = [...document.querySelectorAll(`#rows ${ACTIVE}`)];
    const current = currentRow();
    const next = current ? all[all.indexOf(current) + direction] : all[0];
    if (!next) return;
    next.querySelector('.todo-row__main').click();
    next.scrollIntoView({ block: 'nearest' });
  }

  /* Shift+↑/↓ swaps the selected todo (or meeting) with its neighbour in the user's order. */
  function move(direction) {
    const current = currentRow();
    if (!current || isSearching() || !isOrdered(current)) return;
    const neighbour = direction < 0 ? current.previousElementSibling : current.nextElementSibling;
    if (!isOrdered(neighbour)) return;
    snap(current, () => rows().insertBefore(current, direction < 0 ? neighbour : neighbour.nextElementSibling));
    renumber();
    saveOrder();
    current.scrollIntoView({ block: 'nearest' });
  }

  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey || !list()) return;
    const active = document.activeElement;
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && arrowsBelongToList(active)) {
      e.preventDefault();
      const direction = e.key === 'ArrowUp' ? -1 : 1;
      if (e.shiftKey) move(direction);
      else step(direction);
    }
    if ((e.key === 'n' || e.key === 'N') && !e.shiftKey && !isTyping(active)) {
      e.preventDefault();
      document.getElementById('new-title').focus();
    }
    if (e.key === '/' && !isTyping(active)) {
      e.preventDefault();
      openSearch();
    }
  });

  /* — snapping: every row slides from where it was to where it lands (FLIP), and the placed todo
   *   settles with a brief tint. Skipped for people who ask for less motion. — */
  /* — phones: one pane at a time. The list is home; a todo opens on its thread (`mobile-thread` on <body>, see
   *   mobile.css), and "‹ Todos" comes back to the list where it was left. — */
  const phone = window.matchMedia('(max-width: 760px)');
  let listScroll = 0;

  function showThread(on) {
    if (!phone.matches || document.body.classList.contains('mobile-thread') === on) return;
    if (on) listScroll = window.scrollY;
    document.body.classList.toggle('mobile-thread', on);
    window.scrollTo(0, on ? 0 : listScroll);
  }

  document.addEventListener('click', e => {
    if (!phone.matches) return;
    if (e.target.closest('.todo-row__main')) showThread(true);
    else if (e.target.closest('[data-action="thread-back"]')) showThread(false);
    /* Done or deleted from the thread: back to the list, which is where that todo has just gone from. */
    else if (e.target.closest('.thread [hx-post*="/close/"], .thread [hx-post*="/delete/"]')) showThread(false);
  });
  /* The browser's own Back, from a thread, goes to the list. */
  window.addEventListener('popstate', () => { if (document.body.classList.contains('mobile-thread')) showThread(false); });

  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  const SNAP = { duration: 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' };

  function snap(placed, change) {
    if (calm.matches) { change(); return; }
    const tracked = [...document.querySelectorAll('#rows .todo-row')];
    const before = new Map(tracked.map(el => [el, el.getBoundingClientRect().top]));
    change();
    before.forEach((top, el) => {
      const shift = top - el.getBoundingClientRect().top;
      if (Math.abs(shift) >= 1) el.animate([{ transform: `translateY(${shift}px)` }, { transform: 'none' }], SNAP);
    });
    placed.animate([{ backgroundColor: 'rgba(38, 37, 30, 0.06)' }, { backgroundColor: 'transparent' }], { duration: 700, easing: 'ease-out' });
  }

  /* — drag: hold a todo or a meeting and drop it anywhere in the user's order; a drop saves at once.
   *   Not while searching: a filtered list is not the whole order. — */
  let dragging = null;
  let mark = null;  /* the row the drop line sits above, or the list it ends */

  function clearDropMarks() {
    document.querySelectorAll('.todo-row--drop-before').forEach(r => r.classList.remove('todo-row--drop-before'));
    if (rows()) rows().classList.remove('todo-list__rows--drop-end');
    mark = null;
  }

  /* dragover fires many times a second: only move the line when its place changes, so it fades in once. */
  function markDrop(el, mod) {
    if (mark === el) return;
    clearDropMarks();
    if (!el) return;
    el.classList.add(mod);
    mark = el;
  }

  function renumber() {
    /* Every row counts toward the numbers, done ones too, and shows its number, so closing one moves no one's. */
    document.querySelectorAll(`#rows ${ACTIVE}`).forEach((row, i) => {
      row.querySelector('.todo-row__pos').textContent = String(i + 1).padStart(2, '0');
    });
  }

  /* The row a drop lands in front of. Over a row of the done list, or below everything, it lands at
   * the end of the user's order: in front of the first row that isn't part of it (null: the very end). */
  function rowBefore(e) {
    const row = e.target.closest('.todo-row');
    if (isOrdered(row)) {
      const box = row.getBoundingClientRect();
      return e.clientY > box.top + box.height / 2 ? row.nextElementSibling : row;
    }
    return document.querySelector('#rows .todo-row--done');
  }

  function saveOrder() {
    const form = document.getElementById('reorder-form');
    form.elements.ids.value = [...document.querySelectorAll(`#rows ${ORDERED}`)].map(r => r.dataset.id).join(',');
    form.dispatchEvent(new Event('reorder', { bubbles: true }));
  }

  document.addEventListener('dragstart', e => {
    const row = e.target.closest && e.target.closest(`#rows ${ACTIVE}`);
    if (!row) return;
    if (isSearching()) { e.preventDefault(); return; }
    dragging = row;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', row.dataset.id);
    e.dataTransfer.setDragImage(row, 0, 18);
    setTimeout(() => row.classList.add('todo-row--dragging'), 0);  /* after the drag image is taken */
  });

  document.addEventListener('dragover', e => {
    if (!dragging || !isOrdered(dragging) || !e.target.closest('#rows')) return;
    e.preventDefault();
    const before = rowBefore(e);
    if (before === dragging || before === dragging.nextElementSibling) markDrop(null);
    else if (before) markDrop(before, 'todo-row--drop-before');
    else markDrop(rows(), 'todo-list__rows--drop-end');
  });

  document.addEventListener('drop', e => {
    if (!dragging || !isOrdered(dragging) || !e.target.closest('#rows')) return;
    e.preventDefault();
    const before = rowBefore(e);
    const moved = before !== dragging && before !== dragging.nextElementSibling;
    clearDropMarks();
    if (moved) snap(dragging, () => {
      dragging.classList.remove('todo-row--dragging');
      rows().insertBefore(dragging, before);
    });
    renumber();
    /* The placed todo becomes the selection. The move saves and gets its thread back in the same
     * response, so the "#n" it shows is always the new position. */
    select(dragging);
    if (moved) saveOrder();
  });

  document.addEventListener('dragend', () => {
    if (dragging) dragging.classList.remove('todo-row--dragging');
    dragging = null;
    clearDropMarks();
  });

  /* — notice: a quiet word in the bottom-right corner, gone after a few seconds — */
  let noticeTimer = null;

  function notify(text) {
    const notice = document.getElementById('notice');
    if (!notice) return;
    notice.textContent = text;
    notice.classList.add('notice--shown');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => notice.classList.remove('notice--shown'), 3500);
  }

  /* — midnight: a new day begins, so the board reloads; whatever Scheduled has for today lands on
   *   it. Also on coming back to a tab that slept through it. — */
  let boardDay = new Date().toDateString();

  function refreshIfNewDay() {
    if (!list() || new Date().toDateString() === boardDay) return;
    boardDay = new Date().toDateString();
    document.body.dispatchEvent(new Event('board-changed'));
  }

  function refreshAtMidnight() {
    const now = new Date();
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
    setTimeout(() => { refreshIfNewDay(); refreshAtMidnight(); }, midnight - now);
  }

  refreshAtMidnight();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshIfNewDay(); });

  /* — live meetings: the server says "meetings-changed" over the page's event stream the moment a calendar
   *   push (or a meeting's end) changes them, and the board reloads. Never mid-typing, mid-drag or with a
   *   modal open: it waits until that's over. As a net (the stream pauses while the tab is in the
   *   background), the board also compares its meetings' stamp on coming back, and every few minutes. — */
  const stampUrl = document.getElementById('board-stamp')?.dataset.url;
  let meetingStamp = null;

  async function checkMeetings() {
    if (!stampUrl || !list() || document.hidden) return;
    let stamp;
    try {
      const response = await fetch(stampUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      stamp = (await response.json()).stamp;
    } catch (_) { return; }
    if (meetingStamp === null) { meetingStamp = stamp; return; }
    if (stamp === meetingStamp) return;
    if (busy()) return;
    meetingStamp = stamp;
    reloadBoard();
  }

  /* Typing means words in a field: an empty composer that merely holds the cursor doesn't hold the board back. */
  const typing = el => isTyping(el) && (el.isContentEditable || !!el.value);
  const busy = () => typing(document.activeElement) || document.querySelector('.todo-row--dragging') || document.getElementById('overlay')?.children.length;

  let pendingLive = false;
  function liveChanged() {
    if (busy()) { pendingLive = true; return; }  /* refresh once whatever's going on is over */
    pendingLive = false;
    meetingStamp = null;  /* the next stamp check takes the new state as its starting point */
    reloadBoard();
  }

  /* A change that says what it did (a voice command): which todo's thread to open, which rows to light.
   * The board reloads as for any live change, but remembers where every row stood, so afterwards each
   * one slides from there to where it landed, as it does when you drag, and the thread opens on the note. */
  let hint = null;
  const rowTops = () => new Map([...document.querySelectorAll('#rows .todo-row')].map(el => [el.dataset.id, el.getBoundingClientRect().top]));

  function todosChanged(e) {
    let data = null;
    try { data = JSON.parse(e.detail && e.detail.data); } catch (_) { /* a plain "something changed" */ }
    if (data && typeof data === 'object') {
      hint = { show: data.show || (hint && hint.show) || null, touched: [...new Set([...(hint ? hint.touched : []), ...(data.touched || [])])] };
      /* On a phone, show where it happened: a note, in its thread; anything else (a move, a todo added or closed), in the list. */
      if (data.read) showThread(true);
      else if ((data.touched || []).length) showThread(false);
      const selected = document.querySelector('#board-state [name=t]');
      if (hint.show && selected) selected.value = hint.show;  /* the reload brings back that todo's thread */
    }
    liveChanged();
  }

  function settle(before, play) {
    const rowsNow = [...document.querySelectorAll('#rows .todo-row')];
    if (!calm.matches) {
      rowsNow.forEach(el => {
        const was = before.get(el.dataset.id);
        if (was === undefined) { el.animate([{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], SNAP); return; }
        const shift = was - el.getBoundingClientRect().top;
        /* a longer trip takes a little longer, so the eye can follow it */
        if (Math.abs(shift) >= 1) el.animate([{ transform: `translateY(${shift}px)` }, { transform: 'none' }], { ...SNAP, duration: Math.min(560, SNAP.duration + Math.abs(shift) * 0.4) });
      });
      (play.touched || []).forEach(id => {
        const el = document.querySelector(`#rows .todo-row[data-id="${id}"]`);
        if (el) el.animate([{ backgroundColor: 'rgba(38, 37, 30, 0.06)' }, { backgroundColor: 'transparent' }], { duration: 700, easing: 'ease-out' });
      });
    }
    if (!play.show) return;
    const thread = document.querySelector('.thread__comments');
    if (!thread) return;
    thread.scrollTop = thread.scrollHeight;  /* the newest note is at the foot */
    const last = thread.querySelector('.comment:last-child');
    if (last) last.scrollIntoView({ block: 'nearest', behavior: calm.matches ? 'auto' : 'smooth' });  /* and the page, where the thread sits below the list */
    const stamp = last && last.querySelector('time') && Date.parse(last.querySelector('time').getAttribute('datetime'));
    if (last && !calm.matches && stamp && Date.now() - stamp < 20000) {
      last.animate([{ backgroundColor: 'rgba(38, 37, 30, 0.06)' }, { backgroundColor: 'transparent' }], { duration: 1200, easing: 'ease-out' });
    }
  }

  function reloadBoard() {
    if (document.activeElement?.id === 'comment-input') {  /* the cursor was in the composer: it's back there after */
      document.addEventListener('htmx:after:swap', () => document.getElementById('comment-input')?.focus(), { once: true });
    }
    const play = hint;
    hint = null;
    if (play) {
      const before = rowTops();
      const run = () => settle(before, play);
      document.addEventListener('htmx:after:swap', run, { once: true });
      setTimeout(() => document.removeEventListener('htmx:after:swap', run), 4000);  /* the reload never came: don't catch a later swap */
    }
    document.body.dispatchEvent(new Event('board-changed'));
  }
  document.addEventListener('meetings-changed', liveChanged, true);
  document.addEventListener('todos-changed', todosChanged, true);  /* a voice command changed the list */
  setInterval(() => { if (pendingLive) liveChanged(); }, 1500);

  checkMeetings();
  setInterval(checkMeetings, 5 * 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkMeetings(); });

  /* — empty list: no message, just the composer, ready to type into. On load and whenever the
   *   last open todo goes; never while searching or typing somewhere else. — */
  function focusComposerIfEmpty() {
    if (!rows() || isSearching() || rows().querySelector(ACTIVE) || isTyping(document.activeElement)) return;
    const input = document.getElementById('new-title');
    if (input) input.focus();
  }

  focusComposerIfEmpty();
  if (rows()) new MutationObserver(focusComposerIfEmpty).observe(rows(), { childList: true });
})();
