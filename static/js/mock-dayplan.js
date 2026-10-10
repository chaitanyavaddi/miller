/* Standalone day-plan mock. Not wired into the product board.
   Three row kinds: todos (checkbox, ordered, draggable), meetings (calendar, read-only),
   reminders (bell + time, day-anchored). Day sticks keep order; unfinished todos roll over. */
(() => {
  const root = document.getElementById("dayplan-mock");
  if (!root) return;

  const days = [
    { id: "oct9", label: "oct 9", today: true },
    { id: "oct10", label: "oct 10" },
    { id: "oct11", label: "oct 11" },
    { id: "later", label: "later", collapsed: true },
  ];

  const gripIcon = `<svg class="mock__grip" width="10" height="14" viewBox="0 0 10 14" fill="currentColor" aria-hidden="true"><circle cx="3" cy="3" r="1.1"/><circle cx="7" cy="3" r="1.1"/><circle cx="3" cy="7" r="1.1"/><circle cx="7" cy="7" r="1.1"/><circle cx="3" cy="11" r="1.1"/><circle cx="7" cy="11" r="1.1"/></svg>`;

  /**
   * todo — flexible, ordered, checkbox; dayId is planning.
   * meeting — calendar-fixed; doesn't drag.
   * reminder — time-fixed nudge; doesn't drag; lands on a day by when it fires.
   */
  let items = [
    { id: "t1", kind: "todo", title: "Say hello and schedule a call", meta: "0d", dayId: "oct9", done: false },
    { id: "t2", kind: "todo", title: "Check flights from Dubai", meta: "0d", dayId: "oct9", done: false },
    { id: "r1", kind: "reminder", title: "Pick up dry cleaning", time: "11:30a", dayId: "oct9" },
    { id: "m1", kind: "meeting", title: "Design sync", time: "2:00p", dayId: "oct9", soon: true },
    { id: "t3", kind: "todo", title: "Draft the Q4 hire plan", meta: "3d", dayId: "oct9", done: false },
    { id: "m2", kind: "meeting", title: "Vendor check-in", time: "4:30p", dayId: "oct9" },
    { id: "t4", kind: "todo", title: "Ship the onboarding flow", meta: "1d", dayId: "oct10", done: false },
    { id: "r2", kind: "reminder", title: "Call the bank", time: "3:00p", dayId: "oct10" },
    { id: "t5", kind: "todo", title: "Reply to the design notes", meta: "5h", dayId: "oct10", done: false },
    { id: "t6", kind: "todo", title: "Cut waitlist copy from homepage", meta: "0d", dayId: "oct10", done: false },
    { id: "r3", kind: "reminder", title: "Submit expense report", time: "9:00a", dayId: "oct11" },
    { id: "t7", kind: "todo", title: "Review vendor security packet", meta: "2d", dayId: "oct11", done: false },
    { id: "m3", kind: "meeting", title: "Hiring panel", time: "10:00a", dayId: "oct11" },
    { id: "t8", kind: "todo", title: "Write standup summary", meta: "0d", dayId: "oct11", done: false },
    { id: "t9", kind: "todo", title: "Close the vendor review", meta: "4d", dayId: "later", done: false },
    { id: "t10", kind: "todo", title: "Sketch AI composer commands", meta: "0d", dayId: "later", done: false },
  ];

  let selectedId = "t1";
  let todoDragId = null;
  let stickDrag = null;
  let laterOpen = false;

  const planEl = root.querySelector("[data-plan]");
  const threadEl = root.querySelector("[data-thread]");

  function dayIndex(dayId) {
    return days.findIndex((d) => d.id === dayId);
  }

  function insertIntoDay(item, dayId, beforeId) {
    items = items.filter((i) => i.id !== item.id);
    item.dayId = dayId;

    if (beforeId) {
      const idx = items.findIndex((i) => i.id === beforeId);
      items.splice(idx === -1 ? items.length : idx, 0, item);
      return;
    }

    let insertAt = items.length;
    let found = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].dayId === dayId) {
        found = true;
        insertAt = i + 1;
      } else if (found) break;
    }
    if (!found) {
      const dIdx = dayIndex(dayId);
      for (let i = 0; i < items.length; i++) {
        if (dayIndex(items[i].dayId) > dIdx) {
          insertAt = i;
          break;
        }
      }
    }
    items.splice(insertAt, 0, item);
  }

  function insertAtStartOfDay(item, dayId) {
    const first = items.find((i) => i.dayId === dayId);
    insertIntoDay(item, dayId, first ? first.id : null);
  }

  function checkboxHtml(done) {
    return `<button type="button" class="mock__check${done ? " mock__check--on" : ""}" data-check aria-label="${done ? "Mark not done" : "Mark done"}">
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="1.5" y="1.5" width="11" height="11" rx="2"></rect>
        ${done ? '<path d="M3.5 7l2.5 2.5 4.5-5"></path>' : ""}
      </svg>
    </button>`;
  }

  function renderRow(item) {
    const current = item.id === selectedId ? " mock__row--current" : "";

    if (item.kind === "meeting") {
      const soon = item.soon ? " mock__row--soon" : "";
      return `<div class="mock__row mock__row--meeting${soon}${current}" data-id="${item.id}" data-kind="meeting" title="From calendar · read only">
        <div class="mock__row-body">
          <span class="mock__row-pos" aria-hidden="true"></span>
          <span class="mock__row-title">${item.title}</span>
          <span class="mock__row-meta mock__row-meta--time">${item.time}</span>
        </div>
        <span class="mock__row-trail"></span>
      </div>`;
    }

    if (item.kind === "reminder") {
      return `<div class="mock__row mock__row--reminder${current}" data-id="${item.id}" data-kind="reminder" title="Reminder · fires at this time">
        <div class="mock__row-body">
          <span class="mock__row-pos" aria-hidden="true"></span>
          <span class="mock__row-title">${item.title}</span>
          <span class="mock__row-meta mock__row-meta--time">${item.time}</span>
        </div>
        <span class="mock__row-trail"></span>
      </div>`;
    }

    const done = item.done ? " mock__row--done" : "";
    return `<div class="mock__row mock__row--todo${done}${current}" data-id="${item.id}" data-kind="todo" draggable="${item.done ? "false" : "true"}">
      <div class="mock__row-body">
        <span class="mock__row-pos mock__row-pos--check">${checkboxHtml(item.done)}</span>
        <span class="mock__row-title">${item.title}</span>
        <span class="mock__row-meta">${item.meta}</span>
      </div>
      <span class="mock__row-trail"></span>
    </div>`;
  }

  function render() {
    const activeStick = stickDrag?.dayId;

    planEl.innerHTML = days
      .map((day) => {
        const dayItems = items.filter((i) => i.dayId === day.id);
        const openTodos = dayItems.filter((i) => i.kind === "todo" && !i.done).length;

        if (day.collapsed && !laterOpen) {
          return `<section class="mock__day mock__day--later" data-day="${day.id}">
            <button type="button" class="mock__later" data-later-toggle>
              <span class="mock__later-label">${day.label}</span>
              <span class="mock__later-rail"></span>
              <span class="mock__later-count">${openTodos} todo${openTodos === 1 ? "" : "s"}</span>
            </button>
          </section>`;
        }

        const rows = dayItems.map(renderRow).join("");
        const empty = dayItems.length ? "" : `<div class="mock__empty">Drop a todo here</div>`;
        const canDrag = !day.today && !day.collapsed;
        const stickClass = [
          "mock__stick",
          day.today ? "mock__stick--today" : "",
          canDrag ? "" : "mock__stick--static",
          activeStick === day.id ? "mock__stick--dragging" : "",
        ]
          .filter(Boolean)
          .join(" ");

        const collapseBtn =
          day.collapsed && laterOpen
            ? `<button type="button" class="mock__later-hide" data-later-toggle>collapse</button>`
            : "";
        const handle = canDrag
          ? `<span class="mock__stick-handle" aria-hidden="true">${gripIcon}</span>`
          : `<span class="mock__stick-handle mock__stick-handle--dot" aria-hidden="true"></span>`;
        const title = canDrag ? "Drag to move day boundary" : day.label;

        return `<section class="mock__day" data-day="${day.id}">
          <button type="button" class="${stickClass}" data-stick="${day.id}" title="${title}" aria-label="${canDrag ? `Drag ${day.label} boundary` : day.label}">
            <span class="mock__stick-day">${day.label}</span>
            <span class="mock__stick-rail"></span>
            ${handle}
          </button>
          ${collapseBtn}
          <div class="mock__items" data-drop="${day.id}">${rows}${empty}</div>
        </section>`;
      })
      .join("");

    const selected = items.find((i) => i.id === selectedId) || items.find((i) => i.kind === "todo");
    const day = days.find((d) => d.id === selected?.dayId);
    if (selected && threadEl) {
      if (selected.kind === "meeting") {
        threadEl.innerHTML = `
          <div class="mock__crumb"><b>meeting</b> · ${day?.label || ""} · read only</div>
          <h2 class="mock__thread-title">${selected.title}</h2>
          <div class="mock__thread-body">
            <p>Anchored at <strong>${selected.time}</strong> from the calendar. Todos flow around it — it doesn’t drag.</p>
          </div>`;
      } else if (selected.kind === "reminder") {
        threadEl.innerHTML = `
          <div class="mock__crumb"><b>reminder</b> · ${day?.label || ""}</div>
          <h2 class="mock__thread-title">${selected.title}</h2>
          <div class="mock__thread-body">
            <p>Fires at <strong>${selected.time}</strong>. Time-anchored like a meeting, but it’s yours — not from the calendar. Doesn’t drag; todos flow around it.</p>
          </div>`;
      } else {
        threadEl.innerHTML = `
          <div class="mock__crumb"><b>todo</b> · ${day?.label || ""}${selected.done ? " · done" : ""}</div>
          <h2 class="mock__thread-title">${selected.title}</h2>
          <div class="mock__thread-body">
            <p>Tick the box to finish. Drag between days, or drag a day boundary — unfinished todos cross into the next day at the top. Meetings and reminders stay put.</p>
          </div>`;
      }
    }
  }

  planEl.addEventListener("click", (e) => {
    if (e.target.closest("[data-later-toggle]")) {
      laterOpen = !laterOpen;
      render();
      return;
    }
    const check = e.target.closest("[data-check]");
    if (check) {
      e.preventDefault();
      e.stopPropagation();
      const row = check.closest(".mock__row--todo");
      const item = items.find((i) => i.id === row?.dataset.id && i.kind === "todo");
      if (!item) return;
      item.done = !item.done;
      selectedId = item.id;
      render();
      return;
    }
    const row = e.target.closest(".mock__row");
    if (!row) return;
    selectedId = row.dataset.id;
    render();
  });

  planEl.addEventListener("dragstart", (e) => {
    const row = e.target.closest(".mock__row--todo");
    if (!row || row.classList.contains("mock__row--done")) {
      e.preventDefault();
      return;
    }
    todoDragId = row.dataset.id;
    row.classList.add("mock__row--dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", todoDragId);
  });

  planEl.addEventListener("dragend", () => {
    todoDragId = null;
    planEl.querySelectorAll(".mock__row--dragging, .mock__items--over, .mock__row--drop-before").forEach((el) => {
      el.classList.remove("mock__row--dragging", "mock__items--over", "mock__row--drop-before");
    });
  });

  planEl.addEventListener("dragover", (e) => {
    if (!todoDragId) return;
    const zone = e.target.closest("[data-drop]");
    if (!zone) return;
    e.preventDefault();
    planEl.querySelectorAll(".mock__items--over").forEach((el) => el.classList.remove("mock__items--over"));
    planEl.querySelectorAll(".mock__row--drop-before").forEach((el) => el.classList.remove("mock__row--drop-before"));
    zone.classList.add("mock__items--over");
    const row = e.target.closest(".mock__row");
    if (row && row.dataset.id !== todoDragId) {
      const rect = row.getBoundingClientRect();
      if (e.clientY < rect.top + rect.height / 2) row.classList.add("mock__row--drop-before");
    }
  });

  planEl.addEventListener("drop", (e) => {
    const zone = e.target.closest("[data-drop]");
    if (!zone || !todoDragId) return;
    e.preventDefault();
    const item = items.find((i) => i.id === todoDragId && i.kind === "todo");
    if (!item) return;
    const before = zone.querySelector(".mock__row--drop-before");
    insertIntoDay(item, zone.dataset.drop, before?.dataset.id);
    selectedId = item.id;
    todoDragId = null;
    render();
  });

  function onStickMove(e) {
    if (!stickDrag || e.pointerId !== stickDrag.pointerId) return;
    stickDrag.accum += e.clientY - stickDrag.lastY;
    stickDrag.lastY = e.clientY;
    const step = 34;
    let changed = false;
    while (stickDrag.accum >= step) {
      stickDrag.accum -= step;
      if (pushTodoToPrev(stickDrag.dayIdx)) changed = true;
      else break;
    }
    while (stickDrag.accum <= -step) {
      stickDrag.accum += step;
      if (slideTodoFromPrev(stickDrag.dayIdx)) changed = true;
      else break;
    }
    if (changed) render();
  }

  function onStickUp(e) {
    if (!stickDrag || e.pointerId !== stickDrag.pointerId) return;
    document.removeEventListener("pointermove", onStickMove);
    document.removeEventListener("pointerup", onStickUp);
    document.removeEventListener("pointercancel", onStickUp);
    stickDrag = null;
    render();
  }

  planEl.addEventListener("pointerdown", (e) => {
    const stick = e.target.closest("[data-stick]");
    if (!stick || e.button !== 0) return;
    const dayId = stick.dataset.stick;
    const dIdx = dayIndex(dayId);
    if (dIdx <= 0 || days[dIdx].collapsed) return;
    e.preventDefault();
    stickDrag = {
      dayId,
      dayIdx: dIdx,
      lastY: e.clientY,
      accum: 0,
      pointerId: e.pointerId,
    };
    stick.classList.add("mock__stick--dragging");
    document.addEventListener("pointermove", onStickMove);
    document.addEventListener("pointerup", onStickUp);
    document.addEventListener("pointercancel", onStickUp);
  });

  function slideTodoFromPrev(dIdx) {
    if (dIdx <= 0) return false;
    const prevId = days[dIdx - 1].id;
    const dayId = days[dIdx].id;
    const prevTodos = items.filter((i) => i.dayId === prevId && i.kind === "todo" && !i.done);
    if (!prevTodos.length) return false;
    insertAtStartOfDay(prevTodos[prevTodos.length - 1], dayId);
    return true;
  }

  function pushTodoToPrev(dIdx) {
    if (dIdx <= 0) return false;
    const prevId = days[dIdx - 1].id;
    const dayId = days[dIdx].id;
    const moving = items.find((i) => i.dayId === dayId && i.kind === "todo" && !i.done);
    if (!moving) return false;
    insertIntoDay(moving, prevId, null);
    return true;
  }

  render();
})();
