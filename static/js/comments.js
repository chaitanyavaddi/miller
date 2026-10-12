/* The comment composer: many lines (it grows with what's typed or said; Enter sends, Shift+Enter starts a
 * new line; past a few lines it scrolls, its top fading); a draft kept per todo in this browser, so switching
 * away and back finds it again (gone once sent); and files picked for a comment, listed until sent. */
(function () {
  'use strict';

  const MAX_HEIGHT = 120;  /* five 20px lines and the padding; past it, it scrolls */

  function fit(box) {
    box.style.height = 'auto';
    box.style.height = Math.min(box.scrollHeight, MAX_HEIGHT) + 'px';
    fade(box);
  }

  function fade(box) {
    box.classList.toggle('is-scrolled', box.scrollTop > 0);
  }

  const isComposer = el => el && el.id === 'comment-input' && el.tagName === 'TEXTAREA';

  /* — drafts: per todo, in this browser — */
  const draftKey = id => `miller-draft:${id}`;
  function readDraft(id) { try { return localStorage.getItem(draftKey(id)) || ''; } catch (_) { return ''; } }
  function writeDraft(id, text) {
    try { if (text.trim()) localStorage.setItem(draftKey(id), text); else localStorage.removeItem(draftKey(id)); } catch (_) { /* private mode: no drafts */ }
  }
  function restore() {
    const box = document.getElementById('comment-input');
    if (!isComposer(box)) return;
    const draft = readDraft(box.dataset.todoId);
    if (draft && !box.value) box.value = draft;
    fit(box);
  }

  /* A voice note that came back after the user moved on: onto that todo's draft (recorder.js calls this). */
  window.millerDraftAppend = function (id, text) {
    const box = document.getElementById('comment-input');
    if (isComposer(box) && box.dataset.todoId === String(id)) { window.millerComposerInsert(text); return; }
    const draft = readDraft(id);
    writeDraft(id, draft ? `${draft}${/\s$/.test(draft) ? '' : ' '}${text}` : text);
  };

  document.addEventListener('input', e => { if (isComposer(e.target)) { fit(e.target); writeDraft(e.target.dataset.todoId, e.target.value); } });
  document.addEventListener('submit', e => {
    const box = e.target.querySelector && e.target.querySelector('#comment-input');
    if (isComposer(box) && box.value.trim()) writeDraft(box.dataset.todoId, '');  /* sent: the draft goes */
  }, true);
  document.addEventListener('scroll', e => { if (isComposer(e.target)) fade(e.target); }, true);
  document.addEventListener('keydown', e => {
    if (!isComposer(e.target) || e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();  /* Enter sends; Shift+Enter is a new line */
    if (e.target.value.trim()) e.target.form.requestSubmit();
  });
  document.addEventListener('htmx:after:swap', restore);
  restore();

  /* Words said into the mic land where the cursor is, ready to edit before sending (recorder.js calls this). */
  window.millerComposerInsert = function (text) {
    const box = document.getElementById('comment-input');
    if (!box || !text) return;
    const start = box.selectionStart ?? box.value.length, end = box.selectionEnd ?? box.value.length;
    const before = box.value.slice(0, start), after = box.value.slice(end);
    const gap = before && !/\s$/.test(before) ? ' ' : '';
    box.value = before + gap + text + after;
    const caret = (before + gap + text).length;
    box.focus();
    box.setSelectionRange(caret, caret);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    box.scrollTop = box.scrollHeight;
  };

  function formatSize(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function pendingFile(file) {
    const row = document.createElement('span');
    row.className = 'file-link file-link--pending';
    row.innerHTML = '<span class="file-link__name"></span><span class="file-link__size"></span>';
    row.querySelector('.file-link__name').textContent = file.name;
    row.querySelector('.file-link__size').textContent = formatSize(file.size);
    return row;
  }

  document.addEventListener('change', e => {
    if (e.target.id !== 'files') return;
    const files = [...e.target.files];
    document.getElementById('pending').replaceChildren(...files.map(pendingFile));
    document.getElementById('composer').classList.toggle('composer--has-files', files.length > 0);
  });
})();
