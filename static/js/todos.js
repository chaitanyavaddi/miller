/* The todo list: search mode, selecting a row, drag to reorder. */
(function () {
  'use strict';

  const list = () => document.getElementById('list');
  const rows = () => document.getElementById('rows');
  /* Done rows close out the list but are not part of its order: nothing below moves or numbers them. */
  const ACTIVE = '.todo-row:not(.todo-row--done)';
  const isActive = el => !!el && el.matches(ACTIVE);
  const firstDone = () => document.querySelector('#rows .todo-row--done');
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
    const current = currentRow();
    const next = current
      ? (direction < 0 ? current.previousElementSibling : current.nextElementSibling)
      : document.querySelector(`#rows ${ACTIVE}`);
    if (!isActive(next)) return;
    next.querySelector('.todo-row__main').click();
    next.scrollIntoView({ block: 'nearest' });
  }

  function move(direction) {
    const current = currentRow();
    if (!current || isSearching()) return;
    const neighbour = direction < 0 ? current.previousElementSibling : current.nextElementSibling;
    if (!isActive(neighbour)) return;
    rows().insertBefore(current, direction < 0 ? neighbour : neighbour.nextElementSibling);
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

  /* — drag to reorder: grab a row by its grip, drop it, it is saved. Not while searching:
   *   a filtered list is not the whole order. — */
  let dragging = null;

  function clearDropMarks() {
    document.querySelectorAll('.todo-row--drop-before').forEach(r => r.classList.remove('todo-row--drop-before'));
    if (rows()) rows().classList.remove('todo-list__rows--drop-end');
  }

  function renumber() {
    document.querySelectorAll(`#rows ${ACTIVE} .todo-row__pos`).forEach((pos, i) => { pos.textContent = String(i + 1).padStart(2, '0'); });
  }

  /* The row a drop lands in front of; anything at or below the done rows means "end of the active ones". */
  function rowBefore(e) {
    const row = e.target.closest('.todo-row');
    if (!isActive(row)) return firstDone();
    const box = row.getBoundingClientRect();
    return e.clientY > box.top + box.height / 2 ? row.nextElementSibling : row;
  }

  function saveOrder() {
    const form = document.getElementById('reorder-form');
    form.elements.ids.value = [...document.querySelectorAll(`#rows ${ACTIVE}`)].map(r => r.dataset.id).join(',');
    form.dispatchEvent(new Event('reorder', { bubbles: true }));
  }

  document.addEventListener('dragstart', e => {
    const grip = e.target.closest && e.target.closest('.todo-row__grip');
    if (!grip) return;
    if (isSearching()) { e.preventDefault(); return; }
    const row = grip.closest('.todo-row');
    dragging = row;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', row.dataset.id);
    e.dataTransfer.setDragImage(row, 0, 18);
    setTimeout(() => row.classList.add('todo-row--dragging'), 0);
  });

  document.addEventListener('dragover', e => {
    if (!dragging || !e.target.closest('#rows')) return;
    e.preventDefault();
    clearDropMarks();
    const before = rowBefore(e);
    if (before && before !== dragging) before.classList.add('todo-row--drop-before');
    else if (!before) rows().classList.add('todo-list__rows--drop-end');
  });

  document.addEventListener('drop', e => {
    if (!dragging || !e.target.closest('#rows')) return;
    e.preventDefault();
    const before = rowBefore(e);
    const moved = before !== dragging && before !== dragging.nextElementSibling;
    if (moved) rows().insertBefore(dragging, before);
    clearDropMarks();
    renumber();
    /* The placed todo becomes the selection. A move saves the order and gets its thread back
     * in the same response, so the "#n today" it shows is always the new position. */
    if (moved) {
      select(dragging);
      saveOrder();
    } else {
      dragging.querySelector('.todo-row__main').click();
    }
  });

  document.addEventListener('dragend', () => {
    if (dragging) dragging.classList.remove('todo-row--dragging');
    dragging = null;
    clearDropMarks();
  });

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
