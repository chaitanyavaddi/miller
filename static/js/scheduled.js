/* Scheduled modal: an editor that saves only on Save. Each row holds its state (title, rhythm and the
 * rhythm's detail) on itself; the shared pickers write into whichever row opened them; rows can be
 * added and removed. The footer counts what's changed, and Save sends every change at once — or, if
 * something's incomplete, says what and where instead. ✕ closes without saving. */
(function () {
  'use strict';

  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const ordinal = n => n + (n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
  const dateLabel = iso => { const [y, m, d] = iso.split('-').map(Number); return `${ordinal(d)} ${MONTHS[m - 1]}, ${y}`; };
  const clockLabel = hhmm => { const [h, m] = hhmm.split(':').map(Number); return `${h % 12 || 12}${m ? ':' + String(m).padStart(2, '0') : ''}${h < 12 ? 'am' : 'pm'}`; };
  const modal = () => document.querySelector('#overlay [data-scheduled]');
  const FIELDS = ['rhythm', 'onDate', 'weekdays', 'monthDay', 'atTime'];
  const DETAIL = { once: ['date', 'onDate'], monthly: ['monthday', 'monthDay'] };  /* the picker each rhythm needs next */

  /* — a row's state — */
  function state(row) {
    const d = row.dataset;
    return { title: row.querySelector('.scheduled-row__input').value.trim(), rhythm: d.rhythm, onDate: d.onDate, weekdays: d.weekdays, monthDay: d.monthDay, atTime: d.atTime || '' };
  }

  function remember(row) {  /* how it opened, to tell what changed */
    row._original = row.dataset.id ? state(row) : null;
  }

  function render(row) {
    const d = row.dataset;
    const set = (key, text, empty) => {
      const label = row.querySelector(`[data-label="${key}"]`);
      label.textContent = text;
      label.parentElement.classList.toggle('scheduled-chip--empty', empty);
    };
    set('rhythm', d.rhythm || 'when', !d.rhythm);
    set('date', d.onDate ? dateLabel(d.onDate) : 'date', !d.onDate);
    set('monthday', d.monthDay ? ordinal(Number(d.monthDay)) : 'day', !d.monthDay);
    set('time', d.atTime ? clockLabel(d.atTime) : 'time', !d.atTime);
    row.querySelectorAll('[data-detail]').forEach(el => { el.hidden = el.dataset.detail !== d.rhythm; });
    const days = (d.weekdays || '').split(',').filter(Boolean);
    row.querySelectorAll('[data-weekday]').forEach(b => {
      const on = days.includes(b.dataset.weekday);
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', String(on));
      b.disabled = on && days.length === 1;  /* keep at least one day */
    });
  }

  function changed(row) {
    if (!row._original) return true;
    const now = state(row);
    return now.title !== row._original.title || FIELDS.some(k => (now[k] || '') !== (row._original[k] || ''));
  }

  /* — the footer: how many changes are waiting for Save (never worded as if already done) — */
  function summarise() {
    const root = modal();
    if (!root) return;
    let pending = 0;
    root.querySelectorAll('[data-row]').forEach(row => {
      const gone = row.classList.contains('is-removed');
      row.classList.toggle('is-changed', !gone && !!row.dataset.id && changed(row));
      if (gone || !row.dataset.id || changed(row)) pending++;
    });
    if (root.matches('[data-move]')) return;  /* Move to…: one row, nothing to count */
    root.querySelector('[data-summary]').textContent =  /* empty until something changes */
      pending ? `${pending} unsaved change${pending === 1 ? '' : 's'}` : '';
    const any = !!root.querySelector('[data-row]:not(.is-removed)');
    root.querySelector('[data-empty]').hidden = any;
    root.querySelector('[data-head]').hidden = !any;
  }

  /* — the shared pickers — */
  let open = null;  /* { panel, row, trigger } */

  function closePanel() {
    if (!open) return false;
    open.panel.hidden = true;
    open.trigger.setAttribute('aria-expanded', 'false');
    open = null;
    return true;
  }

  function openPanel(trigger) {
    const row = trigger.closest('[data-row]');
    const panel = modal().querySelector(`[data-panel="${trigger.dataset.pop}"]`);
    const again = open && open.trigger === trigger;
    closePanel();
    if (again) return;
    const d = row.dataset;
    panel.querySelectorAll('[data-pick-rhythm]').forEach(b => b.classList.toggle('is-on', b.dataset.pickRhythm === d.rhythm));
    panel.querySelectorAll('[data-pick-date]').forEach(b => b.classList.toggle('is-on', b.dataset.pickDate === d.onDate));
    panel.querySelectorAll('[data-pick-month-day]').forEach(b => b.classList.toggle('is-on', b.dataset.pickMonthDay === d.monthDay));
    const [hh, mm] = (d.atTime || ':').split(':').map(Number);
    panel.querySelectorAll('[data-pick-hour]').forEach(b => b.classList.toggle('is-on', d.atTime && Number(b.dataset.pickHour) === (hh % 12 || 12)));
    panel.querySelectorAll('[data-pick-minute]').forEach(b => b.classList.toggle('is-on', d.atTime && Number(b.dataset.pickMinute) === mm));
    panel.querySelectorAll('[data-pick-meridiem]').forEach(b => b.classList.toggle('is-on', d.atTime && b.dataset.pickMeridiem === (hh < 12 ? 'am' : 'pm')));
    const panes = [...panel.querySelectorAll('[data-month-pane]')];
    if (panes.length) {  /* the row's month, else this one */
      const month = (d.onDate || '').slice(0, 7);
      const shown = (month && panes.find(p => p.querySelector(`[data-pick-date^="${month}"]`))) || panes[0];
      panes.forEach(p => { p.hidden = p !== shown; });
    }
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    open = { panel, row, trigger };
    const box = trigger.getBoundingClientRect();
    const below = box.bottom + 4 + panel.offsetHeight < window.innerHeight;
    panel.style.top = `${below ? box.bottom + 4 : Math.max(8, box.top - 4 - panel.offsetHeight)}px`;
    panel.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - panel.offsetWidth - 8))}px`;
  }

  /* Write a pick into the row that opened the picker; maybe go straight on to what it needs next. */
  function pick(update) {
    const row = open.row;
    update(row.dataset);
    render(row);
    closePanel();
    hideError();
    summarise();
    const next = DETAIL[row.dataset.rhythm];
    if (next && !row.dataset[next[1]]) openPanel(row.querySelector(`[data-pop="${next[0]}"]`));
    else if (modal().matches('[data-move]') && row.dataset.onDate && !row.dataset.atTime && next && next[0] === 'date') openPanel(row.querySelector('[data-pop="time"]'));  /* Remind or move: the day, then its time */
  }

  /* A time is built from three picks (hour, minute, am/pm); each writes the row at once, filling what's still missing. */
  function setClock(row, change) {
    const [h24, m] = (row.dataset.atTime || '').split(':').map(Number);
    let hour = row.dataset.atTime ? h24 % 12 || 12 : null, minute = row.dataset.atTime ? m : 0;
    let pm = row.dataset.atTime ? h24 >= 12 : null;
    ({ hour = hour, minute = minute, pm = pm } = change);
    if (hour === null) hour = 9;
    if (pm === null) pm = hour === 12 || hour <= 6;  /* 3 means the afternoon, 9 the morning */
    row.dataset.atTime = `${String((hour % 12) + (pm ? 12 : 0)).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
    render(row);
    hideError();
    summarise();
    openRefresh();
  }

  function openRefresh() {  /* keep the picker's highlights in step with the row */
    if (!open) return;
    const { panel, row } = open;
    const [hh, mm] = row.dataset.atTime.split(':').map(Number);
    panel.querySelectorAll('[data-pick-hour]').forEach(b => b.classList.toggle('is-on', Number(b.dataset.pickHour) === (hh % 12 || 12)));
    panel.querySelectorAll('[data-pick-minute]').forEach(b => b.classList.toggle('is-on', Number(b.dataset.pickMinute) === mm));
    panel.querySelectorAll('[data-pick-meridiem]').forEach(b => b.classList.toggle('is-on', b.dataset.pickMeridiem === (hh < 12 ? 'am' : 'pm')));
  }

  /* — what's missing before Save can go — */
  function problem(row) {
    const s = state(row);
    if (!s.title) return ['give it a title', () => row.querySelector('.scheduled-row__input').focus()];
    if (!s.rhythm) return ['pick once, weekly or monthly', () => openPanel(row.querySelector('[data-pop="rhythm"]'))];
    if (s.rhythm === 'once' && !s.onDate) return ['pick a date', () => openPanel(row.querySelector('[data-pop="date"]'))];
    if (s.rhythm === 'weekly' && !s.weekdays) return ['pick at least one day', () => row.querySelector('[data-weekday]').focus()];
    if (s.rhythm === 'monthly' && !s.monthDay) return ['pick a day of the month', () => openPanel(row.querySelector('[data-pop="monthday"]'))];
    return null;
  }

  function showError(row, text) {
    const root = modal();
    const error = root.querySelector('[data-error]');
    const name = state(row).title;
    error.textContent = name ? `“${name}”: ${text}` : text.charAt(0).toUpperCase() + text.slice(1);
    error.hidden = false;
    root.querySelectorAll('.has-problem').forEach(r => r.classList.remove('has-problem'));
    row.classList.add('has-problem');
  }

  function hideError() {
    const root = modal();
    if (!root) return;
    root.querySelector('[data-error]').hidden = true;
    root.querySelectorAll('.has-problem').forEach(r => r.classList.remove('has-problem'));
  }

  /* — Save: every changed row, as parallel lists; nothing at all if anything's incomplete — */
  document.addEventListener('submit', e => {
    const form = e.target.closest && e.target.closest('[data-save-form]');
    if (!form) return;
    const rows = [...modal().querySelectorAll('[data-row]')];
    const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    for (const row of rows) {
      if (row.classList.contains('is-removed') || !changed(row)) continue;
      const issue = problem(row);
      if (issue) {
        stop();
        closePanel();
        showError(row, issue[0]);
        issue[1]();
        return;
      }
    }
    const changes = rows.filter(row => row.classList.contains('is-removed') || changed(row));
    if (!changes.length) {  /* nothing to save: just close */
      stop();
      document.dispatchEvent(new Event('overlay-close'));
      return;
    }
    form.querySelectorAll('[data-change]').forEach(el => el.remove());
    changes.forEach(row => {
      const s = state(row);
      const values = {
        row_id: row.dataset.id || '', row_removed: row.classList.contains('is-removed') ? '1' : '',
        row_title: s.title, row_rhythm: s.rhythm || '', row_on_date: s.onDate || '', row_weekdays: s.weekdays || '', row_month_day: s.monthDay || '', row_at_time: s.atTime || '',
      };
      Object.entries(values).forEach(([name, value]) => {
        const input = document.createElement('input');
        Object.assign(input, { type: 'hidden', name, value });
        input.dataset.change = '';
        form.append(input);
      });
    });
  }, true);

  /* — clicks — */
  document.addEventListener('click', e => {
    const root = modal();
    if (!root || !root.contains(e.target)) return;
    const t = e.target.closest('button');
    if (open && !e.target.closest('.popover') && !(t && t === open.trigger)) closePanel();
    if (!t) return;
    const d = t.dataset;
    if (d.pop) { openPanel(t); return; }
    if (d.monthStep) {
      const pane = t.closest('[data-month-pane]');
      const next = Number(d.monthStep) < 0 ? pane.previousElementSibling : pane.nextElementSibling;
      if (next && next.matches('[data-month-pane]')) { pane.hidden = true; next.hidden = false; }
      return;
    }
    if (d.pickRhythm) { const v = d.pickRhythm; pick(ds => { ds.rhythm = v; }); return; }
    if (d.pickDate) { const v = d.pickDate; pick(ds => { ds.onDate = v; }); return; }
    if (d.pickHour) { setClock(open.row, { hour: Number(d.pickHour) }); return; }
    if (d.pickMinute) { const row = open.row; setClock(row, { minute: Number(d.pickMinute) }); closePanel(); return; }
    if (d.pickMeridiem) { setClock(open.row, { pm: d.pickMeridiem === 'pm' }); return; }
    if (d.pickNoTime !== undefined) { const row = open.row; row.dataset.atTime = ''; render(row); closePanel(); hideError(); summarise(); return; }
    if (d.pickMonthDay) { const v = d.pickMonthDay; pick(ds => { ds.monthDay = v; }); return; }
    if (d.weekday) {
      const row = t.closest('[data-row]');
      const days = new Set((row.dataset.weekdays || '').split(',').filter(Boolean));
      if (days.has(d.weekday)) days.delete(d.weekday); else days.add(d.weekday);
      row.dataset.weekdays = [...days].sort().join(',');
      render(row);
      hideError();
      summarise();
      return;
    }
    if (d.remove !== undefined) {
      const row = t.closest('[data-row]');
      if (!row.dataset.id) row.remove();             /* a new one never saved: just gone */
      else {                                          /* an existing one: marked until Save; click again to keep it */
        row.classList.toggle('is-removed');
        t.title = t.ariaLabel = row.classList.contains('is-removed') ? 'Keep' : 'Remove';
      }
      hideError();
      summarise();
      return;
    }
    if (d.add !== undefined) {
      const row = root.querySelector('[data-row-template]').content.firstElementChild.cloneNode(true);
      remember(row);
      root.querySelector('[data-rows]').append(row);
      render(row);
      summarise();
      row.querySelector('.scheduled-row__input').focus();
    }
  });

  document.addEventListener('input', e => {
    if (e.target.closest && e.target.closest('#overlay [data-row]')) { hideError(); summarise(); }
  });

  /* Enter in a title: on to its rhythm, if it has none yet. Never a save. */
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const input = e.target.closest && e.target.closest('#overlay .scheduled-row__input');
    if (!input) return;
    e.preventDefault();
    const row = input.closest('[data-row]');
    if (!row.dataset.rhythm) openPanel(row.querySelector('[data-pop="rhythm"]'));
  });

  /* Esc closes a picker, or puts back a title being renamed — never the modal: that's ✕ or Save. */
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !modal()) return;
    e.stopImmediatePropagation();
    if (closePanel()) return;
    const title = e.target.closest && e.target.closest('.scheduled-row__input');
    const row = title && title.closest('[data-row]');
    if (row && row._original) {
      title.value = row._original.title;
      title.blur();
      summarise();
    }
  }, true);

  /* A fresh modal: note how each row opened. */
  function arm() {
    const root = modal();
    if (!root || root._armed) return;
    root._armed = true;
    open = null;
    root.querySelectorAll('[data-row]').forEach(row => { remember(row); render(row); });
    summarise();
    if (root.matches('[data-move]')) openPanel(root.querySelector('[data-pop="date"]'));  /* Move to…: straight to the date */
  }

  document.addEventListener('htmx:after:swap', arm);
  arm();
})();
