"use strict";

// The ParishSoft refresh schedule editor (#632; admin-portal spec, "Live
// checks and saving"). The rules are applied in one place, the server: this
// script only adds, removes and renumbers rows, fills presets and the "Edit
// as text" lists, shows each row's fields for its shape, and posts the
// editor's unsaved rows to the page's read-only check as typing pauses (or
// at once when a choice changes). It has no copy of the rules, coverage or
// spacing. The check answers with the parts of the editor to put in place:
// the line beside Save (the only live region, changed only when its words
// change, which also says the problems of the schedule as a whole), the
// problems at each row, the summary and the seven-day list. Nothing it draws
// sits above the rows, and each row keeps room for a line of messages.
// Every change this script makes goes through steady, which scrolls by how
// much the changed parts above the pointer (else the control being edited)
// grew or shrank, so nothing moves under the pointer (#736). Each row's
// controls are described by its messages and marked invalid while it has
// problems. The words this script writes come from the editor's
// data-*-text attributes, translated by the template.
// Only the latest check's answer is shown. While a changed schedule has
// problems, Save is unavailable; when a check fails, the earlier answer
// stays, marked as not checked, and Save stays available (saving checks
// everything again).
(() => {
  const root = document.querySelector("[data-refresh-schedule]");
  const form = root && root.closest("form");
  if (!root || !form) return;
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
  const presets = JSON.parse(document.getElementById("refresh-schedule-presets")?.textContent || "[]");
  const SCOPES = ["rules", "skips"];
  const PAUSE = 500; // As the time entry waits before it reads a typed time.
  const rowsOf = (scope) => root.querySelector(`[data-schedule-rows="${scope}"]`);
  const rowList = (scope) => [...rowsOf(scope).querySelectorAll(":scope > [data-schedule-row]")];
  const management = (scope, name) => form.querySelector(`input[name="${scope}-${name}"]`);
  // A translated wording with {name} placeholders, filled in.
  const words = (name, values) => root.dataset[name].replace(/\{(\w+)\}/g,
    (match, key) => (key in values ? String(values[key]) : match));
  // The parts a check replaces. The line beside Save, the summary and the
  // seven-day list sit outside the editor, below Save.
  const part = (name) => document.querySelector(`[data-schedule-part="${name}"]`);
  const fields = window.ParishTimeFields;

  // Every name the editor posts: the rows, their counts and the switch.
  const schedulePart = (name) => /^(rules|skips)-/.test(name) || name === "skip_around_family_emails";
  const scheduleData = () => {
    const data = new FormData(form);
    [...data.keys()].forEach((name) => {
      if (name !== "csrfmiddlewaretoken" && !schedulePart(name)) data.delete(name);
    });
    return data;
  };
  // The rows as posted, without the token or the row counts (which only
  // follow the rows), to tell whether anything changed since a preset.
  const state = () => new URLSearchParams([...scheduleData()].filter(([name]) =>
    name !== "csrfmiddlewaretoken" && !/_FORMS$/.test(name))).toString();

  // Rows keep contiguous indexes, as the server's formsets read them: every
  // name, id and reference inside a row follows its position.
  const renumber = (scope) => {
    const pattern = new RegExp(`(^|[^a-z])${scope}-(?:\\d+|__prefix__)(?=-|_|$)`, "g");
    const rows = rowList(scope);
    rows.forEach((row, index) => {
      [row, ...row.querySelectorAll("[name], [id], [for], [aria-describedby], [aria-controls]")]
        .forEach((node) => {
          ["name", "id", "for", "aria-describedby", "aria-controls"].forEach((attribute) => {
            const value = node.getAttribute(attribute);
            if (value) node.setAttribute(attribute, value.replace(pattern, `$1${scope}-${index}`));
          });
        });
      row.querySelector("[data-row-number]").textContent = String(index + 1);
    });
    management(scope, "TOTAL_FORMS").value = String(rows.length);
    management(scope, "INITIAL_FORMS").value = "0";
    const maximum = Number(management(scope, "MAX_NUM_FORMS").value);
    root.querySelector(`[data-row-add="${scope}"]`).disabled = rows.length >= maximum;
  };

  // A row shows only the fields its shape uses ("every" or "at" for a rule,
  // "at" or "range" for a skip); the others are hidden and disabled, so they
  // are not sent and their time readings do not hold Save.
  const applyShape = (row) => {
    const shape = row.querySelector('select[name$="-shape"]')?.value;
    row.querySelectorAll("[data-row-shape]").forEach((wrapper) => {
      const shown = wrapper.dataset.rowShape === shape;
      wrapper.hidden = !shown;
      wrapper.querySelectorAll("input, select").forEach((control) => { control.disabled = !shown; });
    });
  };

  // Save is unavailable while a changed schedule has problems (the check's
  // data-blocking). Real disabled, as the other Save gates use; this gate
  // only re-enables what it disabled, and leaves the time entry's own mark.
  let blocking = root.dataset.blocking === "true";
  const submits = () => [...form.elements].filter((node) =>
    (node instanceof HTMLButtonElement || node instanceof HTMLInputElement) && node.type === "submit");
  const gate = () => {
    submits().forEach((node) => {
      if (blocking) {
        node.disabled = true;
        node.setAttribute("data-schedule-gated", "");
      } else if (node.hasAttribute("data-schedule-gated")) {
        node.removeAttribute("data-schedule-gated");
        node.disabled = node.hasAttribute("data-time-gated");
      }
    });
  };

  // The browser's own zone, when it is not the schedule's (the current
  // campaign's, else the parish's): say so once and add each preview time
  // in the browser's clock.
  const scheduleZone = root.dataset.scheduleZone;
  const browserZone = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; }
  })();
  const otherZone = browserZone && browserZone !== "Etc/Unknown" && browserZone !== scheduleZone;
  const browserClock = otherZone ? new Intl.DateTimeFormat("en-GB",
    {hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: browserZone}) : null;
  const browserTimes = (scope) => {
    if (!browserClock) return;
    scope.querySelectorAll("[data-browser-time]").forEach((node) => {
      node.textContent = ` ${words("browserTimeText", {time: browserClock.format(new Date(node.dataset.browserTime))})}`;
    });
  };
  // Drawn as the page loads, before anything is pointed at or focused, so
  // not through steady (below).
  if (otherZone) {
    const note = root.querySelector("[data-browser-zone-note]");
    note.textContent = words("browserZoneText", {zone: browserZone});
    note.hidden = false;
    browserTimes(part("preview"));
  }

  // A row's problems belong to its controls: each is described by the row's
  // messages while there are any, and marked invalid while there are
  // problems. The time entry sets aria-invalid on its own fields as it reads
  // them (and Admin forms clear marks on blur), so marks are applied again
  // after those, at the window; a field the time entry refuses keeps its own
  // mark when the row has no other problem.
  const markRow = (row) => {
    const messages = row.querySelector("[data-row-messages]");
    const said = messages.textContent.trim() !== "";
    const wrong = Boolean(messages.querySelector(".errorlist li"));
    row.querySelectorAll(".schedule-row-fields input, .schedule-row-fields select").forEach((control) => {
      const ids = (control.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && id !== messages.id);
      if (said) ids.push(messages.id);
      if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
      else control.removeAttribute("aria-describedby");
      if (wrong) {
        control.setAttribute("aria-invalid", "true");
        control.setAttribute("data-schedule-invalid", "");
      } else if (control.hasAttribute("data-schedule-invalid")) {
        control.removeAttribute("data-schedule-invalid");
        control.setAttribute("aria-invalid", String(!control.disabled && !control.validity.valid));
      }
    });
  };
  const markRows = () => SCOPES.forEach((scope) => rowList(scope).forEach(markRow));

  // The last pointer position on the screen, so a change can keep what is
  // under the pointer in place (#736). A touch or pen lifts away when it
  // ends or is taken over by a pan (pointercancel), and a mouse that leaves
  // the window points at nothing.
  let pointer = null;
  const track = (event) => { pointer = {x: event.clientX, y: event.clientY}; };
  document.addEventListener("pointermove", track, {passive: true});
  document.addEventListener("pointerdown", track, {passive: true});
  document.addEventListener("pointerup", (event) => {
    if (event.pointerType !== "mouse") pointer = null;
  });
  document.addEventListener("pointercancel", () => { pointer = null; });
  document.documentElement.addEventListener("pointerleave", () => { pointer = null; });
  // The browser's own scroll anchoring stays on for the page, so a note the
  // header's polls show above main moves nothing. The regions steady (below)
  // manages opt out of it (ui-v1.css), so it does not add a second
  // adjustment for their changes.

  // Whether ``node`` or what holds it stays put as the page scrolls.
  const pinned = (node) => {
    for (; node; node = node.parentElement) {
      if (/^(sticky|fixed)$/.test(getComputedStyle(node).position)) return true;
    }
    return false;
  };
  // The height on the screen whose content must not move: the pointer's y,
  // unless the pointer is over something sticky or fixed (such as the Admin
  // sidebar), which does not move with the page; else ``fallback``'s top,
  // else the focused control's top; null with none of them. The pointer's x
  // does not matter: what moves is the content at its height.
  const referenceY = (fallback) => {
    if (pointer && !pinned(document.elementFromPoint(pointer.x, pointer.y))) return pointer.y;
    const focused = document.activeElement;
    const node = fallback || (focused && focused !== document.body ? focused : null);
    return node && !pinned(node) ? node.getBoundingClientRect().top : null;
  };
  // Whether ``node`` is in the page and takes up room.
  const drawn = (node) => node.isConnected && node.getClientRects().length > 0;
  // The chain of nodes from ``region`` down to the content at the height
  // ``y``: at each level, the child that spans y. Over a gap between
  // children (a row's margin, say), the nearest child below y ends the
  // chain, as what the gap stays beside.
  const spanning = (region, y) => {
    const chain = [region];
    for (;;) {
      const children = [...chain[chain.length - 1].children].filter(drawn);
      const boxes = children.map((node) => node.getBoundingClientRect());
      const at = boxes.findIndex((box) => box.top <= y && y < box.bottom);
      if (at >= 0) {
        chain.push(children[at]);
        continue;
      }
      let below = -1;
      boxes.forEach((box, index) => {
        if (box.top >= y && (below < 0 || box.top < boxes[below].top)) below = index;
      });
      if (below >= 0) chain.push(children[below]);
      return chain;
    }
  };
  const indexOf = (node) => [...node.parentElement.children].indexOf(node);
  // Where ``chain``'s content is after a change in ``region``: the deepest
  // node of the chain still drawn inside the region, followed by identity,
  // so rows removed or added around it do not matter; returned with its
  // depth in the chain. Only when none of it is left (the region's content
  // was replaced, as a redraw of the seven-day list does, or the row itself
  // was removed) is it followed by child position, level by level, as deep
  // as that is still drawn.
  const follow = (region, chain, path) => {
    for (let depth = chain.length - 1; depth > 0; depth -= 1) {
      if (region.contains(chain[depth]) && drawn(chain[depth])) return [chain[depth], depth];
    }
    let node = region;
    let depth = 0;
    while (depth < path.length && node.children[path[depth]] && drawn(node.children[path[depth]])) {
      node = node.children[path[depth]];
      depth += 1;
    }
    return [node, depth];
  };

  // Every change this script makes to the page goes through here, so it
  // never moves what is under the pointer (#736). ``regions`` are the
  // elements ``change`` may resize (each a block that contains its own
  // margins, ui-v1.css); nothing else may change size. Measured before and
  // after the change, against the reference height y (referenceY):
  // - a region wholly above y (its bottom at or above y) moves everything
  //   at y by its change in height;
  // - in the region that spans y, the content at y moves by however far it
  //   moved within the region (follow); where nothing remains, the region's
  //   top is what stays;
  // - regions below y move nothing at y.
  // The page then scrolls by the sum. WebKit scrolls by whole pixels and
  // drops a fraction, so there the place is rounded, and the fraction left
  // over is owed to the next change at the same reference height, so a
  // run of changes (a reading, then the check's answer) does not add up
  // to more than half a pixel. At the top or bottom of the page it can
  // scroll only as far as the page goes. A change made while another is
  // being steadied (a time reading redrawn as a new row is wired) is part
  // of that one.
  let steadying = false;
  let owed = {y: null, by: 0};
  const steady = (regions, change, fallback) => {
    if (steadying) {
      change();
      return;
    }
    const y = referenceY(fallback);
    if (y !== owed.y) owed = {y, by: 0};
    const before = regions.map((region) => region.getClientRects().length && region.getBoundingClientRect());
    const at = y === null ? -1 : before.findIndex((box) => box && box.top <= y && y < box.bottom);
    const chain = at < 0 ? [] : spanning(regions[at], y);
    const offsets = chain.map((node) => node.getBoundingClientRect().top - before[at].top);
    const path = chain.slice(1).map(indexOf);
    const start = window.scrollY;
    steadying = true;
    try {
      change();
    } finally {
      steadying = false;
    }
    if (y === null) return;
    let shift = 0;
    regions.forEach((region, index) => {
      const box = region.getBoundingClientRect();
      // A region not drawn before (hidden) took no room: it is above y if
      // it now starts above y.
      const was = before[index] || {top: box.top, bottom: box.top, height: 0};
      if (was.bottom <= y) shift += box.height - was.height;
      if (index !== at) return;
      const [node, depth] = follow(region, chain, path);
      shift += node.getBoundingClientRect().top - box.top - offsets[depth];
    });
    // Scrolled to the place, not by the shift, in case the change itself
    // scrolled the page (a shorter page near its end, or the browser's
    // scroll anchoring for something outside these regions).
    const target = start + shift + owed.by;
    if (Math.abs(window.scrollY - target) > 0.25) window.scrollTo(window.scrollX, target);
    if (Math.abs(window.scrollY - target) > 0.5) window.scrollTo(window.scrollX, Math.round(target));
    owed.by = target - window.scrollY;
  };
  // Move focus to ``node`` without scrolling what is under the pointer
  // (#736). With no pointer (keyboard only), a control off the screen is
  // brought just into view.
  const focusOn = (node) => {
    node.focus({preventScroll: true});
    if (pointer) return;
    const box = node.getBoundingClientRect();
    if (box.top < 0 || box.bottom > window.innerHeight) node.scrollIntoView({block: "nearest"});
  };
  // A time field's reading line (ui-v1.js) can grow by a line or two on a
  // narrow screen: its changes in this editor are steadied too.
  fields?.steadyReadings?.((field, change) => {
    const line = root.contains(field) && field.closest(".schedule-row-fields");
    if (line) steady([line], change);
    else change();
  });

  // The check. One request at a time: a newer one aborts the older, and an
  // answer is shown only if it belongs to the latest request.
  // The "Not checked" line above the seven-day list: its words show while
  // the latest check failed.
  const staleLine = document.querySelector("[data-schedule-stale]");
  const stale = staleLine.querySelector(":scope > span");
  // What a check's answer may resize: the Save line (the status is a flex
  // item, so the line around it is what takes up room), the summary, the
  // "Not checked" line, the seven-day list and each row's messages.
  const checkRegions = () => [part("status").parentElement, part("summary"), staleLine, part("preview"),
    ...SCOPES.flatMap((scope) => rowList(scope).map((row) => row.querySelector("[data-row-messages]")))];
  let timer = 0;
  let latest = 0;
  let controller = null;
  const replaceIfChanged = (target, source) => {
    if (target && source && target.innerHTML !== source.innerHTML) target.innerHTML = source.innerHTML;
  };
  const textLists = () => [...root.querySelectorAll("[data-schedule-text]")];
  let textEdited = false;
  const show = (answer) => {
    const parsed = new DOMParser().parseFromString(answer, "text/html");
    const check = parsed.querySelector("[data-schedule-check]");
    if (!check) throw new Error("No schedule check in the answer.");
    // The line beside Save changes only when its words change, so a screen
    // reader hears it once per change.
    steady(checkRegions(), () => {
      draw(check);
      stale.hidden = true;
    });
    const text = check.querySelector("[data-schedule-text]");
    if (!textEdited && text && text.hasAttribute("data-full")) {
      textLists().forEach((area) => { area.value = text.dataset[area.dataset.scheduleText]; });
    }
    blocking = check.dataset.blocking === "true";
    gate();
  };
  // Put a check's answer in place: the line, the summary, the preview and
  // each row's messages.
  const draw = (check) => {
    const line = part("status");
    const fresh = check.querySelector('[data-schedule-part="status"]');
    if (line && fresh && line.textContent.trim() !== fresh.textContent.trim()) line.innerHTML = fresh.innerHTML;
    replaceIfChanged(part("summary"), check.querySelector('[data-schedule-part="summary"]'));
    // The days a reader opened stay open.
    const preview = part("preview");
    const opened = [...preview.querySelectorAll("details")].map((day) => day.open);
    replaceIfChanged(preview, check.querySelector('[data-schedule-part="preview"]'));
    preview.querySelectorAll("details").forEach((day, index) => {
      if (index < opened.length) day.open = opened[index];
    });
    browserTimes(preview);
    SCOPES.forEach((scope) => rowList(scope).forEach((row, index) => {
      const messages = row.querySelector("[data-row-messages]");
      const answerRow = check.querySelector(`[data-check-row="${scope}-${index}"]`);
      const html = answerRow ? answerRow.innerHTML : "";
      if (messages.innerHTML !== html) messages.innerHTML = html;
    }));
    markRows();
  };
  const run = async (mine) => {
    timer = 0;
    controller = new AbortController();
    gate();
    try {
      const response = await fetch(root.dataset.checkUrl, {
        method: "POST",
        body: new URLSearchParams(scheduleData()),
        credentials: "same-origin",
        headers: {"X-CSRFToken": csrf ? csrf.value : ""},
        signal: controller.signal,
      });
      const answer = await response.text();
      if (mine !== latest) return;
      if (!response.ok) throw new Error(`Check answered ${response.status}.`);
      show(answer);
    } catch (error) {
      if (mine !== latest || error.name === "AbortError") return;
      // Keep the earlier answer, say it is out of date, and let Save work.
      steady([staleLine], () => { stale.hidden = false; });
      blocking = false;
      gate();
    }
  };
  // Any change makes every earlier answer out of date (its rows may have
  // been renumbered since), so it is dropped even if it arrives first.
  const schedule = (delay) => {
    window.clearTimeout(timer);
    latest += 1;
    if (controller) controller.abort();
    const mine = latest;
    timer = window.setTimeout(() => run(mine), delay);
  };

  // Adding and removing rows. Each row's Remove is drawn by the server (and
  // in the row template), so every row, saved or new, can be removed.
  const wireRow = (row) => {
    row.querySelector('select[name$="-shape"]')?.addEventListener("change",
      () => steady([row.querySelector(".schedule-row-fields")], () => applyShape(row)));
    row.querySelector("[data-row-remove]").addEventListener("click", () => {
      const scope = row.dataset.scheduleRow;
      steady([rowsOf(scope)], () => {
        if (fields) fields.cancel(row);
        row.remove();
        renumber(scope);
      });
      focusOn(root.querySelector(`[data-row-add="${scope}"]`));
      schedule(0);
    });
    applyShape(row);
  };
  const addRow = (scope, values) => {
    const template = root.querySelector(`[data-row-template="${scope}"]`);
    const row = template.content.firstElementChild.cloneNode(true);
    rowsOf(scope).append(row);
    renumber(scope);
    Object.entries(values || {}).forEach(([name, value]) => {
      const control = row.querySelector(`[name$="-${name}"]`);
      if (control) control.value = String(value);
    });
    wireRow(row);
    if (fields) fields.wire(row);
    return row;
  };
  const clearRows = (scope) => {
    rowList(scope).forEach((row) => {
      if (fields) fields.cancel(row);
      row.remove();
    });
    renumber(scope);
  };
  SCOPES.forEach((scope) => {
    rowList(scope).forEach(wireRow);
    root.querySelector(`[data-row-add="${scope}"]`).addEventListener("click", () => {
      // A blank row says nothing yet: it is checked once it is first
      // edited, so "Enter a time" does not greet the reader.
      let row = null;
      steady([rowsOf(scope)], () => { row = addRow(scope); });
      const first = row.querySelector("select, input");
      if (first) focusOn(first);
    });
  });

  // Presets replace the rows (and clear the skips); the switch is left as
  // it is. With unsaved changes, the page asks first, in place.
  let baseline = state();
  const confirm = root.querySelector("[data-preset-confirm]");
  let chosen = null;
  const ask = (shown) => steady([confirm], () => { confirm.hidden = !shown; });
  const usePreset = (key) => {
    const preset = presets.find((item) => item.key === key);
    if (!preset) return;
    steady(SCOPES.map(rowsOf), () => {
      clearRows("rules");
      clearRows("skips");
      preset.rules.forEach((values) => addRow("rules", values));
    });
    baseline = state();
    textEdited = false;
    schedule(0);
  };
  root.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state() === baseline) {
        ask(false);
        usePreset(button.dataset.preset);
        return;
      }
      chosen = button;
      ask(true);
      focusOn(confirm.querySelector("[data-preset-replace]"));
    });
  });
  confirm.querySelector("[data-preset-replace]").addEventListener("click", () => {
    ask(false);
    if (chosen) {
      usePreset(chosen.dataset.preset);
      focusOn(chosen);
    }
  });
  confirm.querySelector("[data-preset-keep]").addEventListener("click", () => {
    ask(false);
    if (chosen) focusOn(chosen);
  });

  // "Edit as text": the two lists replace the rules with one "at" rule per
  // time, and the skips with none, once the reader chooses Use these lists.
  // Until then, each check refills them from the rows.
  const textError = root.querySelector("[data-schedule-text-error]");
  const sayTextError = (message) => steady([textError], () => { textError.textContent = message; });
  textLists().forEach((area) => area.addEventListener("input", () => { textEdited = true; }));
  const apply = root.querySelector("[data-schedule-text-apply]");
  apply.addEventListener("click", () => {
    const read = {};
    for (const area of textLists()) {
      const result = window.ParishTimeEntry.parseTimes(area.value, 0, "");
      if (result.error) {
        sayTextError(result.message);
        focusOn(area);
        return;
      }
      read[area.dataset.scheduleText] = [...new Set(result.values.map(window.ParishTimeEntry.canonicalTime))].sort();
    }
    // More times than rules a schedule can have: say so, change nothing.
    const count = read.full.length + read.quick.length;
    const most = Number(management("rules", "MAX_NUM_FORMS").value);
    if (count > most) {
      sayTextError(words("tooManyTimesText", {count}));
      focusOn(textLists()[0]);
      return;
    }
    // The rows above change in number: with no pointer (a touch, which may
    // not focus the button), the button is what stays where it is.
    steady([...SCOPES.map(rowsOf), textError], () => {
      textError.textContent = "";
      clearRows("rules");
      clearRows("skips");
      read.full.forEach((at) => addRow("rules", {kind: "full", shape: "at", at}));
      read.quick.forEach((at) => addRow("rules", {kind: "quick", shape: "at", at}));
    }, apply);
    textEdited = false;
    schedule(0);
  });

  // Typing waits for a pause; a choice (a select, the switch) checks at once.
  // A row change also brings the text lists back in step with the rows.
  root.addEventListener("input", (event) => {
    if (event.target.matches("[data-schedule-text]")) return;
    textEdited = false;
    schedule(PAUSE);
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-schedule-text]")) return;
    textEdited = false;
    schedule(event.target.matches("input[type=text]") ? PAUSE : 0);
  });
  // The time entry re-enables Save as soon as an entry reads again (on
  // input, and on focusout at the document); keep this gate's verdict until
  // the next check answers by gating after it, at the window.
  window.addEventListener("input", () => { gate(); markRows(); });
  window.addEventListener("focusout", () => { gate(); markRows(); });
  // The back/forward cache can restore rows and values without events.
  window.addEventListener("pageshow", (event) => {
    SCOPES.forEach((scope) => rowList(scope).forEach(applyShape));
    if (event.persisted) schedule(0);
  });
  markRows();
  gate();
})();
