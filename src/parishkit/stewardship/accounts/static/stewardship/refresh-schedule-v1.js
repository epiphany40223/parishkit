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
// sits above the rows, and the control being edited is kept where it was on
// the screen, so a check never moves it under the pointer (#736). Each row's
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

  // Keep the focused control (the one being edited, or Add after a Remove)
  // where it is on the screen while ``change`` redraws messages above it.
  const keepInPlace = (change) => {
    const anchor = document.activeElement;
    const watched = anchor && anchor !== document.body && form.contains(anchor);
    const before = watched ? anchor.getBoundingClientRect().top : 0;
    change();
    if (!watched || !anchor.isConnected) return;
    const shift = anchor.getBoundingClientRect().top - before;
    if (shift) window.scrollBy(0, shift);
  };

  // The check. One request at a time: a newer one aborts the older, and an
  // answer is shown only if it belongs to the latest request.
  const stale = document.querySelector("[data-schedule-stale] > span");
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
    keepInPlace(() => draw(check));
    const text = check.querySelector("[data-schedule-text]");
    if (!textEdited && text && text.hasAttribute("data-full")) {
      textLists().forEach((area) => { area.value = text.dataset[area.dataset.scheduleText]; });
    }
    blocking = check.dataset.blocking === "true";
    stale.hidden = true;
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
      stale.hidden = false;
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
    row.querySelector('select[name$="-shape"]')?.addEventListener("change", () => applyShape(row));
    row.querySelector("[data-row-remove]").addEventListener("click", () => {
      const scope = row.dataset.scheduleRow;
      if (fields) fields.cancel(row);
      row.remove();
      renumber(scope);
      root.querySelector(`[data-row-add="${scope}"]`).focus();
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
      const row = addRow(scope);
      row.querySelector("select, input")?.focus();
    });
  });

  // Presets replace the rows (and clear the skips); the switch is left as
  // it is. With unsaved changes, the page asks first, in place.
  let baseline = state();
  const confirm = root.querySelector("[data-preset-confirm]");
  let chosen = null;
  const usePreset = (key) => {
    const preset = presets.find((item) => item.key === key);
    if (!preset) return;
    clearRows("rules");
    clearRows("skips");
    preset.rules.forEach((values) => addRow("rules", values));
    baseline = state();
    textEdited = false;
    schedule(0);
  };
  root.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state() === baseline) {
        confirm.hidden = true;
        usePreset(button.dataset.preset);
        return;
      }
      chosen = button;
      confirm.hidden = false;
      confirm.querySelector("[data-preset-replace]").focus();
    });
  });
  confirm.querySelector("[data-preset-replace]").addEventListener("click", () => {
    confirm.hidden = true;
    if (chosen) {
      usePreset(chosen.dataset.preset);
      chosen.focus();
    }
  });
  confirm.querySelector("[data-preset-keep]").addEventListener("click", () => {
    confirm.hidden = true;
    if (chosen) chosen.focus();
  });

  // "Edit as text": the two lists replace the rules with one "at" rule per
  // time, and the skips with none, once the reader chooses Use these lists.
  // Until then, each check refills them from the rows.
  const textError = root.querySelector("[data-schedule-text-error]");
  textLists().forEach((area) => area.addEventListener("input", () => { textEdited = true; }));
  root.querySelector("[data-schedule-text-apply]").addEventListener("click", () => {
    const read = {};
    for (const area of textLists()) {
      const result = window.ParishTimeEntry.parseTimes(area.value, 0, "");
      if (result.error) {
        textError.textContent = result.message;
        area.focus();
        return;
      }
      read[area.dataset.scheduleText] = [...new Set(result.values.map(window.ParishTimeEntry.canonicalTime))].sort();
    }
    // More times than rules a schedule can have: say so, change nothing.
    const count = read.full.length + read.quick.length;
    const most = Number(management("rules", "MAX_NUM_FORMS").value);
    if (count > most) {
      textError.textContent = words("tooManyTimesText", {count});
      textLists()[0].focus();
      return;
    }
    textError.textContent = "";
    clearRows("rules");
    clearRows("skips");
    read.full.forEach((at) => addRow("rules", {kind: "full", shape: "at", at}));
    read.quick.forEach((at) => addRow("rules", {kind: "quick", shape: "at", at}));
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
