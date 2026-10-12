/* Recorder, in two shapes.
 * - Voice (a todo's composer mic): say it; the audio goes up in one piece when you stop, comes back as
 *   words, and lands in the composer to edit before sending. Nothing is kept.
 * - Call (a meeting that's on, from its thread or the start-of-meeting nudge): the mic, plus the call's tab
 *   audio if shared, each cut into 10-second chunks and uploaded in order. Chunks wait in IndexedDB until
 *   the server has them, so a dropped connection, a crash or a closed tab loses nothing already recorded;
 *   the next visit sends what's left. A new part starts every hour (and after a gap), so every part plays
 *   on its own. Its state shows in the meeting's thread (a dot on its row from anywhere else); once
 *   stopped, the thread refreshes when the transcript is in.
 * The bottom-left corner is only for a word now and then, and the nudge. */
(function () {
  'use strict';

  const CHUNK_MS = 10000;
  const PART_MS = 60 * 60 * 1000;
  const VOICE_MAX_MS = 60 * 1000;    /* a voice note is a minute at most, for now */
  const LEVEL = '<span class="level" aria-hidden="true"><i></i><i></i><i></i></span>';  /* listening, in the text's colour */
  const BITRATE = 32000;            /* speech: ~14 MB an hour per track */
  const MIME = 'audio/webm;codecs=opus';
  const RETRY_MS = [2000, 5000, 10000, 30000];
  const WATCH_MS = 5 * 60 * 1000;   /* how long to watch for a call's transcript before leaving it to a reload */

  const supported = () => !!(window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(MIME) && navigator.mediaDevices);
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const corner = () => document.getElementById('recorder');
  const isTyping = el => !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
  const wait = ms => new Promise(r => setTimeout(r, ms));

  function clock(ms) {
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const pad = n => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  /* ———————————————————————— voice: said into a composer ———————————————————————— */

  let voice = null;    /* { url, recorder, stream, chunks, started, ticker, limit } */
  let MIC_ICON = '';   /* the mic's own icon, put back after listening */

  function micButton() {
    return voice && document.querySelector(`[data-voice][data-voice-url="${voice.url}"]`);
  }

  function showMic(state) {
    const button = micButton();
    if (!button) return;
    button.classList.toggle('is-listening', state === 'listening');
    button.classList.toggle('is-writing', state === 'writing');
    if (state === 'listening') { button.innerHTML = LEVEL + clock(Date.now() - voice.started); button.title = 'Stop'; }
    else if (state === 'writing') button.textContent = 'writing…';
  }

  async function listen(button) {
    if (voice) { if (voice.recorder.state !== 'inactive') voice.recorder.stop(); return; }  /* a second click stops */
    if (call) { say('A call is being recorded.'); return; }
    if (!supported()) { say('Voice works in Chrome, Edge or Firefox.'); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (_) { say('Miller needs the microphone. Allow it in the browser and try again.'); return; }
    MIC_ICON = MIC_ICON || button.innerHTML;
    const recorder = new MediaRecorder(stream, { mimeType: MIME, audioBitsPerSecond: BITRATE });
    voice = { url: button.dataset.voiceUrl, todo: button.dataset.todoId, recorder, stream, chunks: [], started: Date.now() };
    recorder.ondataavailable = e => { if (e.data && e.data.size) voice.chunks.push(e.data); };
    recorder.onstop = () => transcribe();
    recorder.start();
    voice.ticker = setInterval(() => showMic('listening'), 1000);
    voice.limit = setTimeout(() => recorder.state !== 'inactive' && recorder.stop(), VOICE_MAX_MS);
    showMic('listening');
  }

  async function transcribe() {
    const done = voice;
    clearInterval(done.ticker);
    clearTimeout(done.limit);
    done.stream.getTracks().forEach(t => t.stop());
    showMic('writing');
    let text = '', problem = '';
    try {
      const response = await fetch(done.url, { method: 'POST', body: new Blob(done.chunks, { type: MIME }), headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/octet-stream' } });
      const result = await response.json();
      if (response.ok) text = result.text; else problem = result.error;
    } catch (_) { problem = "Couldn't reach Miller. Check the connection and try again."; }
    const here = micButton();  /* still on the todo it was said for? */
    voice = null;
    document.querySelectorAll('[data-voice]').forEach(b => {
      b.classList.remove('is-listening', 'is-writing');
      if (MIC_ICON) b.innerHTML = MIC_ICON;
      b.title = 'Say it';
    });
    if (problem) { say(problem); return; }
    if (!text) { say("Didn't catch any words."); return; }
    if (here && window.millerComposerInsert) window.millerComposerInsert(text);
    else if (window.millerDraftAppend) { window.millerDraftAppend(done.todo, text); say("Added to that todo's draft."); }  /* you'd moved on */
  }

  /* ———————————————————————— call: a meeting while it's on ———————————————————————— */

  let call = null;  /* { id, todo, chunkUrl, stopUrl, statusUrl, title, started, tracks: {mic, call}, share, ticker, stopping, note } */

  /* — chunks waiting for the server, kept in IndexedDB — */
  const DB = 'miller-recorder', STORE = 'chunks';
  let dbPromise = null;
  function db() {
    if (!window.indexedDB) return Promise.reject(new Error('no IndexedDB'));
    dbPromise = dbPromise || new Promise((resolve, reject) => {
      const open = indexedDB.open(DB, 1);
      open.onupgradeneeded = () => open.result.createObjectStore(STORE, { keyPath: 'key' });
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
    return dbPromise;
  }
  function store(mode, act) {
    return db().then(d => new Promise((resolve, reject) => {
      const tx = d.transaction(STORE, mode);
      const request = act(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request && request.result);
      tx.onerror = () => reject(tx.error);
    })).catch(() => undefined);
  }
  const keep = chunk => store('readwrite', s => s.put(chunk));
  const forget = key => store('readwrite', s => s.delete(key));
  const leftovers = () => store('readonly', s => s.getAll());

  /* — the upload queue: one chunk at a time, in the order they were cut — */
  const queue = [];
  let sending = false, failures = 0, isOffline = false;

  function enqueue(chunk) {
    queue.push(chunk);
    keep(chunk);
    pump();
  }

  async function pump() {
    if (sending) return;
    sending = true;
    while (queue.length) {
      const chunk = queue[0];
      let response;
      try {
        const url = `${chunk.url}?track=${chunk.track}&part=${chunk.part}&seq=${chunk.seq}&started=${chunk.started}`;
        response = await fetch(url, { method: 'POST', body: chunk.blob, headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/octet-stream' } });
      } catch (_) { response = null; }
      if (response && (response.ok || [403, 404, 409, 410].includes(response.status))) {
        failures = 0;
        offline(false);
        queue.shift();
        forget(chunk.key);
        if (response.status === 409) gapped(chunk);
        if (response.status === 410) closed(chunk.recording);
        continue;
      }
      offline(true);  /* no connection, or the server stumbled: wait and send the same chunk again */
      await wait(RETRY_MS[Math.min(failures++, RETRY_MS.length - 1)]);
    }
    sending = false;
  }

  /* The server missed an earlier chunk of this part: the rest of the part can't play, so the track starts a new one. */
  function gapped(chunk) {
    for (let i = queue.length - 1; i >= 0; i--) {
      const c = queue[i];
      if (c.recording === chunk.recording && c.track === chunk.track && c.part === chunk.part) { forget(c.key); queue.splice(i, 1); }
    }
    const track = call && call.id === chunk.recording && call.tracks[chunk.track];
    if (track && track.part === chunk.part) restart(track);
  }

  /* The server wrapped this recording up (it went quiet too long): nothing more can go to it. */
  function closed(recordingId) {
    for (let i = queue.length - 1; i >= 0; i--) if (queue[i].recording === recordingId) { forget(queue[i].key); queue.splice(i, 1); }
    if (call && call.id === recordingId) finishCall('This recording was closed. Start a new one to go on.');
  }

  function offline(on) {
    if (isOffline === on) return;
    isOffline = on;
    renderCall();
  }

  /* — a track: one stream, recorded in parts — */
  function begin(track) {
    const recorder = new MediaRecorder(track.stream, { mimeType: MIME, audioBitsPerSecond: BITRATE });
    const part = { index: track.part, seq: 0, started: Date.now(), recording: call.id, url: call.chunkUrl };  /* its own, should the call end first */
    track.recorder = recorder;
    track.started = part.started;
    recorder.ondataavailable = e => {
      if (!e.data || !e.data.size) return;
      const seq = part.seq++;
      enqueue({ key: `${part.recording}:${track.name}:${part.index}:${seq}`, recording: part.recording, url: part.url,
                track: track.name, part: part.index, seq, started: part.started, blob: e.data });
    };
    track.stopped = new Promise(resolve => { recorder.onstop = resolve; });
    recorder.start(CHUNK_MS);
  }

  function restart(track) {
    if (!track.recorder || track.recorder.state === 'inactive' || !track.live) return;
    const old = track.recorder;
    old.onstop = () => { track.part += 1; begin(track); };
    old.stop();
  }

  function track(name, stream, part) {
    const t = { name, stream, part, live: true, recorder: null, started: 0, stopped: Promise.resolve() };
    begin(t);
    return t;
  }

  /* — start: the call's tab first (only right after a click can it be asked), then the mic — */
  async function startCall(button) {
    if (call) return;
    if (voice) { say('Finish the voice note first.'); return; }
    if (!supported()) { say('Recording works in Chrome, Edge or Firefox.'); return; }
    let share = null, callStream = null, micStream;
    if (navigator.mediaDevices.getDisplayMedia) {
      try {
        share = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true, systemAudio: 'include', selfBrowserSurface: 'exclude', surfaceSwitching: 'exclude' });
        if (share.getAudioTracks().length) callStream = new MediaStream(share.getAudioTracks());
        else { share.getTracks().forEach(t => t.stop()); share = null; }
      } catch (_) { share = null; }  /* not shared: the mic alone */
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (_) {
      if (share) share.getTracks().forEach(t => t.stop());
      say('Miller needs the microphone to record. Allow it in the browser and try again.');
      return;
    }
    let begun, refusal = '';
    try {
      const response = await fetch(button.dataset.recordUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf() } });
      if (response.status === 409) refusal = (await response.json()).error;  /* the meeting isn't on */
      if (!response.ok) throw new Error(String(response.status));
      begun = await response.json();
    } catch (_) {
      [micStream, share].forEach(s => s && s.getTracks().forEach(t => t.stop()));
      say(refusal || "Couldn't start recording. Check the connection and try again.");
      return;
    }
    hideNudge();
    call = { id: begun.id, todo: button.dataset.recordTodo, chunkUrl: begun.chunk_url, stopUrl: begun.stop_url, statusUrl: begun.status_url,
             title: button.dataset.recordTitle, started: Date.now(), share, tracks: {}, stopping: false,
             note: callStream ? '' : 'Your mic only: no call audio was shared.' };
    call.tracks.mic = track('mic', micStream, begun.next_parts.mic);
    if (callStream) {
      call.tracks.call = track('call', callStream, begun.next_parts.call);
      callStream.getAudioTracks()[0].addEventListener('ended', () => callAudioEnded());  /* "Stop sharing" in the browser's bar */
    }
    call.ticker = setInterval(tick, 1000);
    syncCall();
  }

  function callAudioEnded() {
    const t = call && call.tracks.call;
    if (!t || !t.live) return;
    t.live = false;
    if (t.recorder.state !== 'inactive') t.recorder.stop();
    call.note = 'Call audio stopped: recording your mic only.';
    renderCall();
  }

  /* — every second: the clock, and a new part on the hour — */
  function tick() {
    if (!call) return;
    Object.values(call.tracks).forEach(t => { if (t.live && Date.now() - t.started >= PART_MS) restart(t); });
    renderCall();
  }

  /* — stop: the last chunks out, then tell the server — */
  async function stopCall() {
    if (!call || call.stopping) return;
    call.stopping = true;
    call.note = 'Saving…';
    renderCall();
    const tracks = Object.values(call.tracks);
    tracks.forEach(t => { t.live = false; if (t.recorder.state !== 'inactive') t.recorder.stop(); });
    await Promise.all(tracks.map(t => t.stopped));
    [...tracks.map(t => t.stream), call.share].forEach(s => s && s.getTracks().forEach(t => t.stop()));
    const id = call.id, stopUrl = call.stopUrl, statusUrl = call.statusUrl;
    while (queue.some(c => c.recording === id)) await wait(500);
    try { await fetch(stopUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf() } }); } catch (_) { /* it wraps up on its own after a while */ }
    finishCall('Transcribing. The transcript lands here.');
    watch(statusUrl);
  }

  function finishCall(message) {
    const todo = call && call.todo;
    if (call) {
      clearInterval(call.ticker);
      Object.values(call.tracks).forEach(t => { t.live = false; if (t.recorder && t.recorder.state !== 'inactive') t.recorder.stop(); t.stream.getTracks().forEach(x => x.stop()); });
      if (call.share) call.share.getTracks().forEach(t => t.stop());
    }
    call = null;
    syncCall();
    const slot = todo && document.querySelector(`[data-call-slot="${todo}"]`);
    if (slot) { slot.hidden = false; slot.textContent = message; }
    else say(message);
  }

  /* — once stopped: ask now and then how it's getting on, and refresh the thread when the transcript is in
   *   (never mid-typing: it waits). Short-lived: it gives up after a while and a reload shows the rest. — */
  async function watch(statusUrl) {
    const until = Date.now() + WATCH_MS;
    while (Date.now() < until) {
      await wait(3000);
      let status = '';
      try { status = (await (await fetch(statusUrl, { headers: { Accept: 'application/json' } })).json()).status; } catch (_) { continue; }
      if (status !== 'ready' && status !== 'failed') continue;
      while (isTyping(document.activeElement) && Date.now() < until) await wait(1000);
      document.body.dispatchEvent(new Event('board-changed'));
      return;
    }
  }

  /* — the call's state, in its meeting's thread (and a dot on its row) — */
  function renderCall() {
    if (!call) return;
    const slot = document.querySelector(`[data-call-slot="${call.todo}"]`);
    if (!slot) return;  /* another thread is open: the meeting's row carries a dot */
    slot.hidden = false;
    slot.innerHTML = '';
    const line = document.createElement('span');
    line.className = 'recorder__line';
    line.innerHTML = LEVEL;
    line.append(`Recording · ${clock(Date.now() - call.started)}`);
    slot.append(line);
    if (!call.stopping) {
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.className = 'text-btn text-btn--small recorder__stop';
      stop.dataset.recordStop = '';
      stop.textContent = 'Stop';
      slot.append(stop);
    }
    const detail = isOffline ? 'Offline · saving here, sends when back' : call.note;
    if (detail) {
      const small = document.createElement('span');
      small.className = 'recorder__note';
      small.textContent = detail;
      slot.append(small);
    }
  }

  /* After any change to the page: the thread's Record call hides while recording, the row gets its dot. */
  function syncCall() {
    document.querySelectorAll('[data-record]').forEach(b => b.classList.toggle('is-recording', !!call));
    document.querySelectorAll('.todo-row.is-recording').forEach(r => { r.classList.remove('is-recording'); r.querySelectorAll('.todo-row__when .level').forEach(l => l.remove()); });
    if (call) {
      const row = document.querySelector(`#rows .todo-row[data-id="${call.todo}"]`);
      const when = row && row.querySelector('.todo-row__when');
      if (row) row.classList.add('is-recording');
      if (when) when.insertAdjacentHTML('afterbegin', LEVEL);
    }
    renderCall();
  }

  /* ———————————————————————— the corner: a word now and then, and the nudge ———————————————————————— */

  let sayTimer = null;

  function say(message) {
    const el = corner();
    if (!el) return;
    delete el.dataset.nudge;
    el.hidden = false;
    el.innerHTML = '';
    const small = document.createElement('span');
    small.className = 'recorder__note';
    small.textContent = message;
    el.append(small);
    clearTimeout(sayTimer);
    sayTimer = setTimeout(() => { el.hidden = true; }, 6000);
  }

  /* When a meeting on today's list starts, offer to record its call (timed in this tab: no server asks). The
   * times come with the board; they're read again whenever it refreshes. */
  let nudgeTimers = [];
  const nudged = new Set();

  function scheduleNudges() {
    nudgeTimers.forEach(clearTimeout);
    nudgeTimers = [];
    document.querySelectorAll('[data-meeting-nudges] [data-nudge-start]').forEach(el => {
      const until = Date.parse(el.dataset.nudgeStart) - Date.now();
      if (until < 0 || until > 24 * 3600 * 1000 || nudged.has(el.dataset.nudgeUrl)) return;
      nudgeTimers.push(setTimeout(() => nudge(el.dataset), until));
    });
  }

  function nudge(meeting) {
    nudged.add(meeting.nudgeUrl);
    if (call) return;
    const el = corner();
    if (!el) return;
    clearTimeout(sayTimer);
    el.hidden = false;
    el.innerHTML = '';
    el.dataset.nudge = '';
    const line = document.createElement('span');
    line.className = 'recorder__line';
    line.textContent = `${meeting.nudgeTitle} started`;
    const record = document.createElement('button');
    record.type = 'button';
    record.className = 'text-btn text-btn--small recorder__stop';
    Object.assign(record.dataset, { record: '', recordUrl: meeting.nudgeUrl, recordTitle: meeting.nudgeTitle, recordTodo: meeting.nudgeTodo });
    record.textContent = 'Record call';
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'text-btn text-btn--small recorder__dismiss';
    dismiss.dataset.nudgeDismiss = '';
    dismiss.textContent = 'Not now';
    el.append(line, record, dismiss);
    sayTimer = setTimeout(hideNudge, 10 * 60000);  /* left alone, it goes after ten minutes */
  }

  function hideNudge() {
    const el = corner();
    if (el && 'nudge' in el.dataset) { el.hidden = true; delete el.dataset.nudge; }
  }

  /* ———————————————————————— wiring ———————————————————————— */

  document.addEventListener('click', e => {
    const mic = e.target.closest('[data-voice]');
    if (mic) { if (!mic.classList.contains('is-writing')) listen(mic); return; }
    const record = e.target.closest('[data-record]');
    if (record) { startCall(record); return; }
    if (e.target.closest('[data-record-stop]')) stopCall();
    if (e.target.closest('[data-nudge-dismiss]')) hideNudge();
  });
  document.addEventListener('htmx:after:swap', () => { syncCall(); scheduleNudges(); if (voice) showMic('listening'); });
  window.addEventListener('beforeunload', e => {
    if (call || voice) { e.preventDefault(); e.returnValue = ''; }  /* closing ends the recording: ask first */
  });
  window.addEventListener('online', () => pump());
  scheduleNudges();

  /* Chunks a previous visit couldn't send (offline, or the tab closed): send them now. */
  leftovers().then(chunks => {
    (chunks || []).sort((a, b) => a.recording - b.recording || (a.track < b.track ? -1 : a.track > b.track ? 1 : 0) || a.part - b.part || a.seq - b.seq);
    (chunks || []).forEach(c => queue.push(c));
    if (queue.length) pump();
  });
})();
