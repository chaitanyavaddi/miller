/* The todo list: search mode, selecting a row, moving todos within and between days. */
(function () {
  'use strict';

  const list = () => document.getElementById('list');
  const rows = () => document.getElementById('rows');
  /* Done rows close out the list but are not part of its order: nothing below moves or numbers them. */
  const ACTIVE = '.todo-row:not(.todo-row--done)';
  const isActive = el => !!el && el.matches(ACTIVE);
  const activeRows = () => [...document.querySelectorAll(`#rows ${ACTIVE}`)];
  const shown = row => row.offsetParent !== null;  /* not folded away in "later" */
  const dayOf = row => row.closest('.day');
  const isMeeting = row => row.dataset.kind === 'meeting';
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

  /* From the review modal: show an open todo in the board. */
  function showTodo(id) {
    const row = document.querySelector(`.todo-row[data-id="${id}"]`);
    if (row) row.querySelector('.todo-row__main').click();
    document.dispatchEvent(new Event('overlay-close'));
  }

  document.addEventListener('click', e => {
    const action = e.target.closest('[data-action]');
    if (action && action.dataset.action === 'search-open') openSearch();
    if (action && action.dataset.action === 'search-close') closeSearch();
    if (action && action.dataset.action === 'select-todo') showTodo(action.dataset.todoId);
    if (action && action.dataset.action === 'later-toggle') toggleLater(dayOf(action));
    if (action && action.dataset.action === 'reorder-start') startReorder();
    if (action && action.dataset.action === 'reorder-save') saveReorder();
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
    if (closeRemindMenus()) return;
    if (isReordering()) { cancelReorder(); return; }
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
    const rows = activeRows().filter(shown);
    const current = currentRow();
    const next = current ? rows[rows.indexOf(current) + direction] : rows[0];
    if (!next) return;
    next.querySelector('.todo-row__main').click();
    next.scrollIntoView({ block: 'nearest' });
  }

  /* — snapping: every row and day label slides from where it was to where it lands (FLIP), and the
   *   placed todo settles with a brief tint. Skipped for people who ask for less motion. — */
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  const SNAP = { duration: 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' };

  function snap(placed, change) {
    if (calm.matches) { change(); return; }
    const tracked = [...document.querySelectorAll('#rows .todo-row, #rows .day__head, #todo-form')];
    const before = new Map(tracked.map(el => [el, el.getBoundingClientRect().top]));
    change();
    before.forEach((top, el) => {
      if (!el.isConnected || !el.offsetParent) return;
      const shift = top - el.getBoundingClientRect().top;
      if (Math.abs(shift) < 1) return;
      el.animate([{ transform: `translateY(${shift}px)` }, { transform: 'none' }], SNAP);
    });
    if (placed) placed.animate([{ backgroundColor: 'rgba(38, 37, 30, 0.06)' }, { backgroundColor: 'transparent' }], { duration: 700, easing: 'ease-out' });
  }

  /* Shift+↑/↓: swap with the neighbour, or cross into the next day (landing at its near end).
   * A meeting only moves among its own day's rows. */
  function move(direction) {
    const current = currentRow();
    if (!current || isSearching()) return;
    const rows = activeRows().filter(shown);
    const neighbour = rows[rows.indexOf(current) + direction];
    if (!neighbour) return;
    const crossing = dayOf(neighbour) !== dayOf(current);
    if (crossing && isMeeting(current)) return;
    snap(current, () => {
      if (crossing === (direction > 0)) neighbour.before(current);
      else neighbour.after(current);
    });
    renumber();
    if (isReordering()) reordered = true;
    else saveOrder();
    current.scrollIntoView({ block: 'nearest' });
  }

  /* — reorder mode (the pencil): the next few days and later appear as empty drop
   *   targets. Moves stay on screen; the tick saves them all at once, Esc puts everything back. — */
  let reordered = false;
  const isReordering = () => !!list() && list().classList.contains('todo-list--reordering');

  function startReorder() {
    if (!list()) return;
    closeSearch();
    reordered = false;
    list().classList.add('todo-list--reordering');
  }

  function saveReorder() {
    list().classList.remove('todo-list--reordering');
    if (reordered) saveOrder();
    reordered = false;
  }

  function cancelReorder() {
    list().classList.remove('todo-list--reordering');
    if (reordered) document.body.dispatchEvent(new Event('board-changed'));  /* the server still has the old order */
    reordered = false;
  }

  /* — later: everything past the coming week, folded to a count until asked for — */
  function toggleLater(day, open) {
    if (!day) return;
    const isOpen = day.classList.toggle('day--open', open);
    const toggle = day.querySelector('[data-action="later-toggle"]');
    toggle.textContent = isOpen ? 'hide' : 'show';
    toggle.setAttribute('aria-expanded', String(isOpen));
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

  /* — drag: hold a todo and drop it anywhere in a day; a todo can go to any day (the empty ones
   *   appear while it's being dragged), a meeting only within its own. A drop saves at once,
   *   except in reorder mode, where the tick saves. Not while searching: a filtered list is not
   *   the whole order. — */
  let dragging = null;

  let mark = null;  /* the row the drop line sits above, or the day it closes */

  function clearDropMarks() {
    document.querySelectorAll('.todo-row--drop-before').forEach(r => r.classList.remove('todo-row--drop-before'));
    document.querySelectorAll('#rows .day--drop-end').forEach(d => d.classList.remove('day--drop-end'));
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
    activeRows().forEach((row, i) => {
      row.querySelector('.todo-row__pos').textContent = String(i + 1).padStart(2, '0');
      row.dataset.day = dayOf(row).dataset.day;
    });
    const later = document.querySelector('#rows .day--later');
    if (later) {
      const count = later.querySelectorAll('.todo-row:not(.todo-row--meeting)').length;
      later.querySelector('[data-later-count]').textContent = `${count} todo${count === 1 ? '' : 's'}`;
    }
  }

  function canDropIn(day) {
    return !!day && (!isMeeting(dragging) || day.dataset.day === dragging.dataset.day);
  }

  /* The row a drop lands in front of within the day; null means "the end of that day". */
  function rowBefore(e, day) {
    const row = e.target.closest('.todo-row');
    if (!isActive(row) || !day.contains(row)) return null;
    const box = row.getBoundingClientRect();
    return e.clientY > box.top + box.height / 2 ? row.nextElementSibling : row;
  }

  function saveOrder() {
    const form = document.getElementById('reorder-form');
    const rows = activeRows();
    form.elements.ids.value = rows.map(r => r.dataset.id).join(',');
    form.elements.days.value = rows.map(r => dayOf(r).dataset.day).join(',');
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
    /* After the drag image is taken: the empty days appearing moves things under the pointer. */
    setTimeout(() => {
      row.classList.add('todo-row--dragging');
      if (!isMeeting(row)) list().classList.add('todo-list--dragging');
    }, 0);
  });

  document.addEventListener('dragover', e => {
    const day = e.target.closest && e.target.closest('#rows .day');
    if (!dragging || !canDropIn(day)) return;
    e.preventDefault();
    const before = rowBefore(e, day);
    if (before === dragging) markDrop(null);
    else if (before) markDrop(before, 'todo-row--drop-before');
    else markDrop(day, 'day--drop-end');
  });

  document.addEventListener('drop', e => {
    const day = e.target.closest && e.target.closest('#rows .day');
    if (!dragging || !canDropIn(day)) return;
    e.preventDefault();
    const before = rowBefore(e, day);
    const [parent, next] = [dragging.parentNode, dragging.nextElementSibling];
    snap(dragging, () => {
      dragging.classList.remove('todo-row--dragging');
      if (before !== dragging) day.querySelector('.day__rows').insertBefore(dragging, before);
      if (!isReordering()) list().classList.remove('todo-list--dragging');  /* the empty days fold away as it lands */
    });
    const moved = dragging.parentNode !== parent || dragging.nextElementSibling !== next;
    clearDropMarks();
    renumber();
    if (day.classList.contains('day--later')) toggleLater(day, true);  /* keep what was just placed in sight */
    /* The placed todo becomes the selection. Outside reorder mode the move saves now and its thread
     * comes back in the same response, so the "#n" it shows is the new position. */
    select(dragging);
    if (moved && isReordering()) reordered = true;
    else if (moved) saveOrder();
  });

  document.addEventListener('dragend', () => {
    if (dragging) dragging.classList.remove('todo-row--dragging');
    if (list()) list().classList.remove('todo-list--dragging');
    dragging = null;
    clearDropMarks();
  });

  /* — reminders: the composer's bell (for the todo being written) and the thread's (for the one being
   *   read) open the same presets. The composer keeps the choice until the todo is added; the
   *   thread saves it straight away. "Pick a time…" reveals the one native field, only when asked. — */
  function remindScope(el) {
    return el.closest('[data-remind-scope]') || document.getElementById(el.dataset.remindFor || '');
  }

  function closeRemindMenus(except) {
    let closed = false;
    document.querySelectorAll('.remind-menu').forEach(menu => {
      if (menu === except || menu.hidden) return;
      menu.hidden = true;
      menu.querySelector('.remind-menu__pick').hidden = true;
      closed = true;
    });
    document.querySelectorAll('[data-action="remind-open"][aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    return closed;
  }

  /* Fixed to the viewport, under the opener (above it when there's no room), so a scrolling list can't clip it. */
  function openRemindMenu(opener) {
    const scope = remindScope(opener);
    const menu = scope && scope.querySelector('.remind-menu');
    if (!menu) return;
    const opening = menu.hidden;
    closeRemindMenus();
    if (!opening) return;
    menu.hidden = false;
    opener.setAttribute('aria-expanded', 'true');
    const box = opener.getBoundingClientRect();
    const below = box.bottom + 6 + menu.offsetHeight < window.innerHeight;
    menu.style.top = `${below ? box.bottom + 6 : box.top - 6 - menu.offsetHeight}px`;
    menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.querySelector('.remind-menu__item').focus();
  }

  function setReminder(scope, preset, at, label) {
    scope.querySelector('[name=remind_preset]').value = preset;
    scope.querySelector('[name=remind_at]').value = at;
    closeRemindMenus();
    if (scope.hasAttribute('data-remind-submit')) {
      scope.requestSubmit();
      return;
    }
    const chip = scope.querySelector('[data-remind-chip]');
    chip.hidden = !(preset || at);
    chip.querySelector('[data-remind-chip-label]').textContent = label;
    scope.querySelector('[name=title]').focus();
  }

  function pickedLabel(value) {
    const at = new Date(value);
    return isNaN(at) ? value : at.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-action="remind-open"]');
    if (opener) { openRemindMenu(opener); return; }
    const preset = e.target.closest('[data-remind-preset]');
    if (preset) { setReminder(remindScope(preset), preset.dataset.remindPreset, '', preset.dataset.remindLabel); return; }
    const pick = e.target.closest('[data-action="remind-pick"]');
    if (pick) {
      const field = pick.parentNode.querySelector('.remind-menu__pick');
      field.hidden = false;
      field.focus();
      if (field.showPicker) try { field.showPicker(); } catch (_) { /* not allowed here; typing works */ }
      return;
    }
    const clear = e.target.closest('[data-action="remind-clear"]');
    if (clear) { setReminder(remindScope(clear), '', '', ''); return; }
    if (!e.target.closest('.remind-menu')) closeRemindMenus();
  });

  document.addEventListener('change', e => {
    if (!e.target.matches || !e.target.matches('.remind-menu__pick') || !e.target.value) return;
    setReminder(remindScope(e.target), '', e.target.value, pickedLabel(e.target.value));
  });

  /* — empty list: no message, just the composer, ready to type into. On load and whenever the
   *   last open todo goes; never while searching or typing somewhere else. — */
  function focusComposerIfEmpty() {
    if (!rows() || isSearching() || rows().querySelector(ACTIVE) || isTyping(document.activeElement)) return;
    const input = document.getElementById('new-title');
    if (input) input.focus();
  }

  /* — midnight: a new day begins, so the board reloads and yesterday's unfinished todos roll into
   *   today. Also on coming back to a tab that slept through it. — */
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

  focusComposerIfEmpty();
  if (rows()) new MutationObserver(focusComposerIfEmpty).observe(rows(), { childList: true });
})();
