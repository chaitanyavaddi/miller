/* Voice mode. Turn it on (the mic in the top bar, the button at the foot on a phone, or V) and the page is run by speaking: the add-todo box and
 * the composer go away, the microphone stays open, and each thing you say, once you pause, is sent as a
 * command: add, rename, close, or write an update on a todo. Miller answers in writing, never aloud; if it
 * needs to ask something, just answer. The mic again (or Esc, or saying you're done) turns it off and lets go of the microphone.
 *
 * Only the final words are shown: they come back from the server once you've paused, spelled by the same
 * model that acts on them, so nothing is shown and then corrected.
 *
 * What the server sends (todos.js hands the stream's events to the page as DOM events):
 *   voice-heard  {turn, text}                              the words it made out
 *   voice-result {turn, reply, outcome, undo}              what happened, or a question back
 *   todos-changed                                          the list changed (todos.js reloads it)
 * The command's own response carries the same result, for a page whose stream is down. */
(function () {
  'use strict';

  /* What the browser can record: webm or ogg in Chrome, Firefox and Android, mp4 in Safari (iPhones). */
  const MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
  const pickMime = () => (window.MediaRecorder && MediaRecorder.isTypeSupported && MIMES.find(m => MediaRecorder.isTypeSupported(m))) || '';
  const MIME = pickMime();
  const BITRATE = 32000;
  const TICK_MS = 80;
  const PAUSE_MS = 1100;        /* quiet this long after speaking: that was the whole command */
  const MIN_VOICED_MS = 350;    /* less speech than this is a cough or a door, not a command */
  const IDLE_CYCLE_MS = 6000;   /* a stretch with no speech is dropped and started afresh, in a quiet moment */
  const MAX_MS = 40000;
  const WAIT_MS = 25000;        /* give up waiting for an answer after this */
  const STORE = 'miller-voice';

  const bar = document.getElementById('voice');
  if (!bar) return;
  const heard = document.getElementById('voice-heard');
  const reply = document.getElementById('voice-reply');
  const hint = document.getElementById('voice-hint');
  const undoButton = document.getElementById('voice-undo');
  const meta = bar.querySelector('.voice__meta');
  const dock = document.getElementById('voice-dock');                 /* the button at the foot (phones) */
  const mic = document.querySelector('.topbar__voice');               /* the one in the top bar (everywhere else) */
  const buttons = [mic, dock].filter(Boolean);
  const arc = bar.querySelector('.voice__arc');
  const stage = bar.querySelector('.voice__stage');
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');

  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const isTyping = el => !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
  const supported = () => !!(MIME && navigator.mediaDevices);

  let on = false;
  let state = 'listening';      /* listening | thinking (a command is with the server) */
  let seg = null;               /* the stretch being recorded */
  let turn = null;              /* the command in flight: {id, finished} */
  let session = '';             /* one run of voice mode is one conversation: its id goes with every command, and the server remembers the rest */
  let limited = false;          /* a guest who has used their free voice time */
  let stream = null, audio = null, analyser = null, samples = null, ticker = null, waitTimer = null;
  let level = 0, floor = 0.01;  /* the mic's level now, smoothed; and the room's background level */
  let undoId = null;
  const live = () => !!document.getElementById('live');
  /* what a live stream would have said: the list changed, and which thread to open and rows to light (todos.js) */
  const announce = data => document.dispatchEvent(new CustomEvent('todos-changed', { detail: { data: JSON.stringify(data) } }));   /* a member's page has a live stream; a guest's does not */

  /* ———————————————————————— the bar's words ———————————————————————— */

  let left = Number(bar.dataset.percent) >= 80 ? bar.dataset.left : '';   /* what's left of the budget, said once it's running low */

  /* The quiet line under the words says nothing while all is well: the mic's ring and the arch already show it is
     listening, or working. It has a word only for what needs one (a limit, a blocked mic), and Undo. */
  function setState(next, text) {
    state = next;
    bar.dataset.state = next;
    hint.textContent = text || (next === 'listening' && left ? `${left} left` : '');
    syncMeta();
  }

  function syncMeta() { meta.hidden = !hint.textContent && undoButton.hidden; }

  function write(el, text) {
    el.textContent = text || '';
    el.classList.remove('is-new');
    if (text && !calm.matches) { void el.offsetWidth; el.classList.add('is-new'); }
    if (typeof sizeArc === 'function') requestAnimationFrame(sizeArc);   /* the arch stands round whatever is written */
  }

  const quoted = text => (text ? `“${text.trim()}”` : '');

  function setUndo(id) {
    undoId = id || null;
    undoButton.hidden = !undoId;
    syncMeta();
  }

  /* The bar is as tall as its words; the page keeps that much room clear at the foot (voice.css reads --voice-h). */
  if (window.ResizeObserver) {
    new ResizeObserver(() => document.documentElement.style.setProperty('--voice-h', `${Math.round(bar.getBoundingClientRect().height)}px`)).observe(bar);
  }

  /* The arch of light stands round the words: wide and tall enough that they sit in its clear middle, so a longer
     answer makes a bigger arch (it eases there, voice.css). The words' own width, not the box they could fill. */
  const textWidth = el => {
    if (!el.textContent) return 0;
    const range = document.createRange();
    range.selectNodeContents(el);
    return range.getBoundingClientRect().width;
  };

  function sizeArc() {
    const box = stage.getBoundingClientRect();
    if (!box.width) return;
    /* The answer sets the size; what was said, small and quiet, counts for less. 44: the gap down to the mic. */
    const w = Math.max(textWidth(reply), textWidth(heard) * 0.75, 240), h = box.height + 44;
    bar.style.setProperty('--arc-w', `${Math.round(Math.min(w * 2, innerWidth * 1.4))}px`);
    bar.style.setProperty('--arc-h', `${Math.round(Math.max(h * 1.9, 190))}px`);
  }
  if (window.ResizeObserver) new ResizeObserver(sizeArc).observe(stage);
  window.addEventListener('resize', () => { if (on) sizeArc(); });

  /* ———————————————————————— turning voice mode on and off ———————————————————————— */

  async function setOn(value) {
    if (value === on) return;
    on = value;
    document.body.classList.toggle('voice-on', on);
    bar.hidden = !on;
    buttons.forEach(b => { b.setAttribute('aria-pressed', on ? 'true' : 'false'); b.setAttribute('aria-label', on ? 'Stop voice mode' : 'Voice mode'); });
    try { localStorage.setItem(STORE, on ? '1' : '0'); } catch (_) { /* a convenience only */ }
    if (on) {
      if (isTyping(document.activeElement)) document.activeElement.blur();
      write(heard, ''); write(reply, ''); setUndo(null); turn = null;
      session = (window.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)).slice(0, 36);
      limited = bar.dataset.limited === '1';
      setState('listening');
      sizeArc();
      if (limited) { stopForLimit(bar.dataset.limitMessage || 'You’ve reached your voice limit.'); return; }
      if (!supported()) { write(reply, 'Voice needs a browser that can record audio: Chrome, Edge, Firefox or Safari.'); setState('listening', 'Not supported'); return; }
      if (!(await openMic()) && on) { write(reply, 'Miller needs the microphone. Allow it in the browser, then turn voice on again.'); setState('listening', 'Microphone blocked'); return; }
      if (on) { beginSegment(); ticker = setInterval(tick, TICK_MS); }
    } else {
      clearInterval(ticker); clearTimeout(waitTimer); ticker = null;
      endSegment(false);
      releaseMic();
      turn = null;
      /* The update box sizes itself to what's in it (comments.js), and could not while it was hidden: have it measure again. */
      requestAnimationFrame(() => document.querySelectorAll('#comment-input').forEach(box => box.dispatchEvent(new Event('input', { bubbles: true }))));
    }
  }

  document.addEventListener('click', e => {
    const control = e.target.closest('[data-action="voice-toggle"]');
    if (control) { e.preventDefault(); control.blur(); setOn(!on); }
  });

  /* ———————————————————————— the microphone ———————————————————————— */

  async function openMic() {
    if (stream && stream.active) return true;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (_) { return false; }
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
    analyser = audio.createAnalyser();
    analyser.fftSize = 512;
    samples = new Uint8Array(analyser.fftSize);
    audio.createMediaStreamSource(stream).connect(analyser);
    return true;
  }

  function releaseMic() {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null; analyser = null;
  }

  function readLevel() {
    if (!analyser) return 0;
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (let i = 0; i < samples.length; i++) { const v = (samples[i] - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / samples.length);
  }

  /* ———————————————————————— listening, continuously ———————————————————————— */

  /* The mic stays open. A recording runs, and when you've spoken and then paused it is sent and a fresh one
     starts; one with no speech in it is dropped and restarted at a quiet moment. */

  function beginSegment() {
    if (!on || seg || !stream || !stream.active) return;
    const recorder = new MediaRecorder(stream, { mimeType: MIME, audioBitsPerSecond: BITRATE });
    const mine = { recorder, chunks: [], started: Date.now(), lastVoice: Date.now(), voiced: 0, send: false };
    recorder.ondataavailable = e => { if (e.data && e.data.size) mine.chunks.push(e.data); };
    recorder.onstop = () => {
      if (seg === mine) seg = null;
      if (mine.send && mine.voiced >= MIN_VOICED_MS && mine.chunks.length && on) send(new Blob(mine.chunks, { type: MIME }));
      else if (on && state === 'listening') beginSegment();
    };
    seg = mine;
    recorder.start();
  }

  function endSegment(send) {
    if (!seg) return;
    seg.send = send;
    if (seg.recorder.state !== 'inactive') seg.recorder.stop();
  }

  /* A browser keeps audio asleep until the person touches the page (a reload with voice mode remembered, say):
     the first tap or key wakes it, and until then the bar says so instead of sitting there deaf. */
  let sleeping = false;
  const awaken = () => { if (audio && audio.state === 'suspended') audio.resume().catch(() => {}); };
  ['pointerdown', 'keydown', 'touchstart'].forEach(name => document.addEventListener(name, awaken, { passive: true, capture: true }));

  let lastTick = Date.now();

  function tick() {
    const now = Date.now(), elapsed = Math.min(now - lastTick, 1000);   /* real time since the last look: a throttled page looks less often */
    lastTick = now;
    const asleep = !!audio && audio.state === 'suspended';
    if (asleep !== sleeping) { sleeping = asleep; if (state === 'listening') setState('listening', asleep ? 'Tap anywhere to start' : ''); }
    if (asleep) { awaken(); return; }
    level += (readLevel() - level) * 0.5;
    feedLevel();
    if (!seg || state !== 'listening') return;
    const threshold = Math.max(0.03, floor * 2.5);
    if (level > threshold) { seg.voiced += elapsed; seg.lastVoice = now; }
    else floor += (Math.min(level, 0.1) - floor) * 0.02;   /* learn the room's own noise */
    const spoke = seg.voiced >= MIN_VOICED_MS;
    const quiet = now - seg.lastVoice;
    if (spoke && quiet > PAUSE_MS) endSegment(true);
    else if (!spoke && quiet > 700 && now - seg.started > IDLE_CYCLE_MS) endSegment(false);
    else if (now - seg.started > MAX_MS) endSegment(true);
  }

  /* ———————————————————————— sending a command, hearing back ———————————————————————— */

  async function send(blob) {
    const command = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), finished: false };
    turn = command;
    setState('thinking');
    clearTimeout(waitTimer);
    waitTimer = setTimeout(() => onResult({ turn: command.id, reply: 'That took too long. Try again.', outcome: 'failed', undo: null }), WAIT_MS);
    const headers = { 'X-CSRFToken': csrf(), 'Content-Type': 'application/octet-stream', 'X-Voice-Turn': command.id, 'X-Voice-Session': session, 'X-Voice-Mime': MIME.split(';')[0] };
    const openThread = (document.querySelector('#board-state [name=t]') || {}).value;   /* the thread on screen: what "this one" means */
    if (openThread) headers['X-Voice-Open'] = openThread;
    try {
      const response = await fetch(bar.dataset.commandUrl, { method: 'POST', body: blob, headers });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'failed');
      result.turn = result.turn || command.id;
      onResult(result);   /* a no-op if the stream already delivered it */
    } catch (_) {
      onResult({ turn: command.id, reply: 'Couldn’t reach Miller. Try again.', outcome: 'failed', undo: null });
    }
  }

  function onHeard(data) {
    if (!turn || data.turn !== turn.id) return;
    write(heard, quoted(data.text));
    write(reply, ''); setUndo(null);
  }

  function onResult(result) {
    if (!turn || result.turn !== turn.id || turn.finished) return;
    turn.finished = true;
    clearTimeout(waitTimer);
    if (result.heard && result.outcome !== 'nothing' && !heard.textContent) write(heard, quoted(result.heard));   /* a page with no live stream hears its words here */
    if (result.reply) write(reply, result.reply.split(' · ').join('\n'));   /* one change to a line; nothing heard says nothing */
    setUndo(result.undo);
    if (result.undo) { bar.classList.remove('is-done'); void bar.offsetWidth; bar.classList.add('is-done'); }   /* the light swells once: it landed */
    if (result.undo && !live()) announce({ show: result.show, touched: result.touched, read: result.read });   /* no live stream to say so: refresh the list ourselves */
    showUsage(result.usage);
    if (result.outcome === 'limit') { limited = true; stopForLimit(result.reply); return; }
    if (result.go) navigate(result.go);   /* open a thread or a panel, or say goodbye */
    setState('listening');
    if (on) beginSegment();
  }

  /* Settings shows what voice has cost; here, only a word when it's running low. */
  function showUsage(usage) {
    if (!usage) return;
    left = usage.percent >= 80 && !usage.full ? usage.left : '';
    if (state === 'listening' && !sleeping && !/limit|blocked|supported|Tap/i.test(hint.textContent)) setState('listening');
  }

  /* — going somewhere: a todo's thread, one of the panels, or out of voice mode — */
  const openPanel = url => { if (window.htmx && url) htmx.ajax('GET', url, { target: '#overlay', swap: 'innerHTML' }); };

  function openTodo(id) {
    const main = document.querySelector(`#rows .todo-row[data-id="${id}"] .todo-row__main`);
    if (main) {   /* the same as clicking it: its thread loads, the row becomes the selected one */
      document.dispatchEvent(new Event('overlay-close'));
      main.click();
      const row = main.closest('.todo-row');
      row.scrollIntoView({ block: 'nearest', behavior: calm.matches ? 'auto' : 'smooth' });
      if (!calm.matches) row.animate([{ backgroundColor: 'rgba(38, 37, 30, 0.06)' }, { backgroundColor: 'transparent' }], { duration: 900, easing: 'ease-out' });
      return;
    }
    const selected = document.querySelector('#board-state [name=t]');   /* not on the page yet: reload with it selected */
    if (selected) selected.value = id;
    document.body.dispatchEvent(new Event('board-changed'));
  }

  function navigate(go) {
    if (go.to === 'end') { setTimeout(() => { if (on) setOn(false); }, 1500); return; }   /* the goodbye stays on screen a moment */
    if (go.to === 'close_panel') document.dispatchEvent(new Event('overlay-close'));
    else if (go.to === 'all_todos') openPanel(bar.dataset.reviewUrl);
    else if (go.to === 'settings') openPanel(bar.dataset.settingsUrl);
    else if (go.to === 'usage') openPanel(`${bar.dataset.settingsUrl}?open=usage`);
    else if (go.to === 'todo' && go.id) openTodo(go.id);
  }

  /* A person out of their voice budget: say so, and let go of the microphone. */
  function stopForLimit(message) {
    clearInterval(ticker); clearTimeout(waitTimer); ticker = null;
    endSegment(false);
    releaseMic();
    write(heard, ''); write(reply, message);
    setState('listening', 'Voice limit reached');
  }

  const detail = e => { try { return JSON.parse(e.detail.data); } catch (_) { return null; } };
  document.addEventListener('voice-heard', e => { const d = detail(e); if (d) onHeard(d); });
  document.addEventListener('voice-result', e => { const d = detail(e); if (d) onResult(d); });

  undoButton.addEventListener('click', async () => {
    if (!undoId) return;
    const id = undoId;
    setUndo(null);
    try {
      const response = await fetch(bar.dataset.undoUrl.replace('/0/', `/${id}/`), { method: 'POST', headers: { 'X-CSRFToken': csrf() } });
      const undone = await response.json();
      write(reply, undone.reply || 'Undone.');
      if (!live()) announce({ touched: undone.touched });
    } catch (_) { write(reply, 'Couldn’t undo that.'); }
  });

  /* ———————————————————————— keys ———————————————————————— */

  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
    if ((e.key === 'v' || e.key === 'V') && !isTyping(document.activeElement) && buttons.length) {
      e.preventDefault(); setOn(!on); return;
    }
    if (on && e.key === 'Escape') setOn(false);
  });

  /* ———————————————————————— the level ———————————————————————— */

  /* The light follows the voice: tick() hands the level to the arch, the window's hairline and the mic's ring
     (voice.css reads --voice-level). Only when it has moved enough to see. */
  let shownLevel = -1;
  function feedLevel() {
    const value = calm.matches ? 0 : Math.min(1, level * 8);
    if (Math.abs(value - shownLevel) < 0.03) return;
    shownLevel = value;
    const text = value.toFixed(2);
    bar.style.setProperty('--voice-level', text);
    buttons.forEach(b => b.style.setProperty('--voice-level', text));
  }

  /* ———————————————————————— start where it was left ———————————————————————— */

  /* Back on after a reload only if the browser already allows the microphone: no surprise permission prompt. */
  (async function restore() {
    try {
      if (localStorage.getItem(STORE) !== '1' || !supported() || !navigator.permissions) return;
      const permission = await navigator.permissions.query({ name: 'microphone' });
      if (permission.state === 'granted') setOn(true);
    } catch (_) { /* off */ }
  })();
})();
