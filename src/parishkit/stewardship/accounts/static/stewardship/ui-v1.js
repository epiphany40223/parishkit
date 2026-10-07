"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  // The Admin sidebar menu is open in the markup so it works without
  // JavaScript. Collapse it on narrow screens, where it sits above the page
  // content, and keep it open on wide screens, where its toggle is hidden.
  const adminMenu = document.querySelector("[data-admin-menu]");
  if (adminMenu && typeof window.matchMedia === "function") {
    const wide = window.matchMedia("(min-width: 60rem)");
    const syncMenu = () => { adminMenu.open = wide.matches; };
    syncMenu();
    // Older Safari only offers the deprecated addListener on media queries.
    if (typeof wide.addEventListener === "function") wide.addEventListener("change", syncMenu);
    else if (typeof wide.addListener === "function") wide.addListener(syncMenu);
  }

  // "About this page" panels ({% aboutpage %} in templatetags/stewardship.py)
  // start closed so the page's data comes first (#227). Each browser
  // remembers, per page type, whether an Admin opened one. Only an "open"
  // marker is stored, and only after an Admin opens a panel: closed is the
  // default, so closing removes the marker. Earlier versions stored a
  // "closed" marker instead (panels then started open); that value now means
  // the default and is cleared. Storage can be unavailable (private windows,
  // blocked site data); the panel then just starts closed.
  document.querySelectorAll("details[data-about-page]").forEach((panel) => {
    const key = `pk-about-page:${panel.dataset.aboutPage}`;
    try {
      const stored = window.localStorage.getItem(key);
      if (stored === "open") panel.open = true;
      else if (stored !== null) window.localStorage.removeItem(key);
    } catch (error) { /* Keep the panel closed. */ }
    panel.addEventListener("toggle", () => {
      try {
        if (panel.open) window.localStorage.setItem(key, "open");
        else window.localStorage.removeItem(key);
      } catch (error) { /* Nothing to remember without storage. */ }
    });
  });

  // The LOCAL step-up (components/reauthenticate.html, #613). There is no
  // Google in LOCAL: the Administrator mints a sign-in link on the laptop
  // and opens it, often in a new tab, so the page that asked records its
  // return path (never an answer or credential) for local-sign-in-v1.js to
  // send as the sign-in's "next". The key "pk-local-step-up" and the
  // ten-minute limit pair with STEP_UP_KEY and STEP_UP_SECONDS in
  // local-sign-in-v1.js; change them together.
  document.querySelectorAll("[data-local-step-up]").forEach((node) => {
    try {
      window.localStorage.setItem("pk-local-step-up", JSON.stringify({
        next: node.dataset.localStepUp, at: Date.now(),
      }));
    } catch (error) { /* The sign-in then returns to the home page. */ }
  });

  // Hosted files (#346). A Copy button copies its read-only field; without
  // script (or the clipboard API) the button stays hidden and the field can
  // be selected by hand. Choosing a file fills an empty placeholder-name
  // field from the file's base name, as the server would derive it (the
  // server still derives and validates it when the field is left blank).
  // The buttons sit in table rows, so a re-sorted table wires them again
  // (enhanceTable below).
  const wireCopyButtons = (root) => {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") return;
    root.querySelectorAll("button[data-copy]").forEach((button) => {
      const field = document.getElementById(button.dataset.copy);
      if (!field) return;
      button.hidden = false;
      const label = button.textContent;
      button.addEventListener("click", () => {
        navigator.clipboard.writeText(field.value).then(
          () => { button.textContent = "Copied"; setTimeout(() => { button.textContent = label; }, 2000); },
          () => { field.select(); },
        );
      });
    });
  };
  const slugSource = document.querySelector("input[data-slug-from]");
  const slugTarget = document.querySelector("input[data-slug-to]");
  if (slugSource && slugTarget) {
    let derived = "";
    slugSource.addEventListener("change", () => {
      const file = slugSource.files && slugSource.files[0];
      if (!file || (slugTarget.value && slugTarget.value !== derived)) return;
      const stem = file.name.replace(/\.[^.]*$/, "");
      let slug = stem.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      if (slug.length > 64) slug = slug.slice(0, 64).replace(/-[^-]*$/, "") || slug.slice(0, 64);
      derived = slug.replace(/^-+|-+$/g, "") || "file";
      slugTarget.value = derived;
    });
  }

  // The parish's chosen date format (date-format-v1.js, loaded first).
  if (window.ParishDates) window.ParishDates.localize(document);

  // A download's timezone choice offers the browser's own zone, chosen by
  // default; without script the choice stays UTC. Also run for markup a table
  // swap brings in (enhanceTable below).
  //
  // A form that takes a date and time typed in local time (#558) sends the
  // browser's zone in a hidden input[data-browser-zone] for the server to
  // convert to UTC, and reveals its [data-browser-zone-note] naming the zone.
  // When the browser reports no zone (or ICU's "Etc/Unknown") the field stays
  // empty and the note hidden, and the [data-browser-zone-field] controls are
  // marked invalid with the input's data-zone-missing-hint while they hold a
  // value, so the data-require-complete gate below keeps Save (or Apply)
  // unavailable and says why; they are then described by that hint instead
  // of the hidden note. An empty field is left alone: optional dates (the
  // System logs filters) need no zone, and a required one (a contact
  // attempt's date) is invalid while empty anyway, with the same hint. The
  // server's own catalog check refuses any other zone it does not know.
  // A [data-zone-dependent] field that only narrows what is shown (the
  // critical-events banner's From day for System logs) is instead disabled,
  // so it is not sent: the form still works, only less narrowly.
  const zoneValidity = (field) => {
    field.setCustomValidity(field.value ? field.dataset.zoneHint || "" : "");
  };
  const wireBrowserTimezone = (root) => {
    let zone = "";
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { /* no Intl */ }
    root.querySelectorAll("input[data-browser-zone]").forEach((input) => {
      const scope = input.form || root;
      const known = Boolean(zone) && zone !== "Etc/Unknown";
      const hint = known ? "" : input.dataset.zoneMissingHint || "";
      const note = scope.querySelector("[data-browser-zone-note]");
      const gateHint = scope.querySelector("[data-complete-hint]");
      input.value = known ? zone : "";
      scope.querySelectorAll("[data-browser-zone-name]").forEach((node) => {
        node.textContent = input.value;
      });
      if (note) note.hidden = !known;
      scope.querySelectorAll("[data-zone-dependent]").forEach((field) => {
        field.disabled = !known;
      });
      scope.querySelectorAll("[data-browser-zone-field]").forEach((field) => {
        if (hint) {
          field.dataset.missingHint = hint;
          field.dataset.zoneHint = hint;
        } else {
          delete field.dataset.missingHint;
          delete field.dataset.zoneHint;
        }
        zoneValidity(field);
        // The zone note (or, without a zone, the Save hint) describes the
        // field; any other description, such as a refused contact time's
        // inline error (#592), is kept after it.
        const described = known ? note : gateHint;
        if (described && described.id) {
          const others = (field.getAttribute("aria-describedby") || "").split(/\s+/)
            .filter((id) => id && id !== note?.id && id !== gateHint?.id);
          field.setAttribute("aria-describedby", [described.id, ...others].join(" "));
        }
      });
    });
    if (!zone || zone === "UTC") return;
    root.querySelectorAll("select[data-browser-timezone]").forEach((select) => {
      select.append(new Option(`${zone} (this browser)`, zone, true, true));
    });
  };
  wireBrowserTimezone(document);
  // Re-check a zone field as its value changes. Capturing on the document
  // runs before a form's own input and change listeners (the complete gate),
  // and covers fields a table swap brings in. A value the browser restores
  // without an event (the back/forward cache) is re-checked on pageshow, and
  // this listener is added before the gate's own.
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    if (event.target instanceof Element && event.target.matches("[data-browser-zone-field]")) {
      zoneValidity(event.target);
    }
  }, true));
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("[data-browser-zone-field]").forEach(zoneValidity);
  });

  // An error summary's links move focus to their field. Wired for the page
  // and again for any summary a refused in-place save swaps in (#562).
  const wireSummaryLinks = (root) => {
    root.querySelectorAll('[data-error-summary] a[href^="#"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = document.getElementById(link.hash.slice(1));
        if (target) { event.preventDefault(); target.focus(); }
      });
    });
  };
  document.querySelector("[data-error-summary]")?.focus();
  // Field errors (#592). A field in error is marked aria-invalid="true" and
  // described by its message, a .errorlist beside it: the server draws
  // both (a refused follow-up, or any Django form, whose 5.2 markup is the
  // same), and the browser's own checks mark a field on blur. The rule for
  // every Admin form is that a mark clears as soon as the error does:
  //   - a field the browser marked clears as soon as its value is valid;
  //   - a field the server marked clears, with its message, on the first
  //     edit (input or change), since the browser cannot re-check the
  //     server's rule; the server checks again on save. A message shared by
  //     several fields (a date and a time) clears them all, and the error
  //     summary loses the item that links to them (the whole box once it is
  //     empty).
  // Leaving a field without editing it keeps a server mark: the error is
  // neither fixed nor edited. data-field-error marks a field whose mark
  // comes from a message (the server's, or a live check such as the
  // follow-up contact time's) rather than from the browser's validity.
  const errorMessages = (node) => (node?.getAttribute("aria-describedby") || "").split(/\s+/)
    .map((id) => id && document.getElementById(id))
    .filter((message) => message && message.matches(".errorlist"));
  const clearFieldError = (field) => {
    // A choice group Django renders as a fieldset (use_fieldset widgets)
    // marks each input but describes the fieldset, so its message is found
    // there, and every input in it clears together. No Admin form uses one
    // yet; this keeps the rule true when one does.
    const group = errorMessages(field).length ? null : field.closest("fieldset");
    const messages = errorMessages(group || field);
    const fields = new Set([field]);
    if (group && messages.length) {
      fields.add(group);
      group.querySelectorAll("[data-field-error]").forEach((node) => fields.add(node));
    }
    messages.forEach((message) => {
      document.querySelectorAll("[aria-describedby]").forEach((other) => {
        if (other.getAttribute("aria-describedby").split(/\s+/).includes(message.id)) {
          fields.add(other);
        }
      });
    });
    fields.forEach((node) => {
      node.removeAttribute("data-field-error");
      // A live check's flag goes with its mark (data-show-when hiding it).
      delete node.dataset.clientError;
      node.setAttribute("aria-invalid", "false");
      const ids = (node.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && !messages.some((message) => message.id === id));
      if (ids.length) node.setAttribute("aria-describedby", ids.join(" "));
      else node.removeAttribute("aria-describedby");
      // The summary item that links to this field goes too, and the
      // summary with it once it lists nothing.
      if (!node.id) return;
      document.querySelectorAll(`[data-error-summary] a[href="#${CSS.escape(node.id)}"]`)
        .forEach((link) => link.closest("li")?.remove());
    });
    document.querySelectorAll("[data-error-summary]").forEach((summary) => {
      if (!summary.querySelector("li")) summary.remove();
    });
    // A message element a live check may reuse stays, hidden and empty.
    messages.forEach((message) => {
      message.hidden = true;
      message.querySelectorAll("li").forEach((item) => { item.textContent = ""; });
      delete message.dataset.source;
    });
  };
  // Fields this script marked from the browser's own check on blur. Only
  // those are cleared when valid again, so a mark another script set is
  // never taken off here. (The Family form's inputs are built by
  // family-v1.js after this runs, so they are never wired here at all;
  // that script manages their marks.)
  const blurMarked = new WeakSet();
  const wireValidity = (root) => {
    root.querySelectorAll("input, select, textarea").forEach((field) => {
      if (field.getAttribute("aria-invalid") === "true") field.setAttribute("data-field-error", "");
      const edited = () => {
        if (field.hasAttribute("data-field-error")) clearFieldError(field);
        else if (blurMarked.has(field) && field.validity.valid) {
          field.setAttribute("aria-invalid", "false");
          blurMarked.delete(field);
        }
      };
      field.addEventListener("input", edited);
      field.addEventListener("change", edited);
      field.addEventListener("blur", () => {
        if (!field.willValidate) return;
        const invalid = field.hasAttribute("data-field-error") || !field.validity.valid;
        field.setAttribute("aria-invalid", String(invalid));
        if (invalid && !field.hasAttribute("data-field-error")) blurMarked.add(field);
      });
    });
  };

  // Time of day entry (#631). A field marked data-time-entry="time" (one
  // time) or "list" (several) takes a time in any common form: 2:00, 2am,
  // 0200, 2:30 PM, 14h30, noon. These are the rules of the server's
  // parishkit.stewardship.time_entry, which stays the authority; the shared
  // table tests/stewardship/fixtures/time_entry_cases.json pins both, so
  // keep the messages and rules here in step with it. A bare hour is never
  // guessed as afternoon: the reading always shows both clocks, as in
  // "Reads as 07:00 (7:00 AM)". As the Admin types, a readable entry's
  // reading shows at once; a refusal waits for a pause or for focus to
  // leave, so a half-typed "2:" does not flash red. Screen readers hear the
  // reading or refusal once typing pauses. A server error under the field is
  // removed on the first edit; on blur a readable entry is rewritten in its
  // canonical form; and the form's submit buttons stay unavailable while any
  // shown entry cannot be read. The fields read wall-clock times only; which
  // zone they are in is the page's own rule (stated in each field's help).
  const TIME_MESSAGES = {
    empty: () => "Enter a time, for example 2:00 PM or 14:00.",
    unreadable: (p) => `“${p.entry}” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400.`,
    hour: (p) => `“${p.entry}” has no hour ${p.hour}: hours run from 0 to 23.`,
    minute: (p) => `“${p.entry}” has no minute ${p.minute}: minutes run from 00 to 59.`,
    twelve_hour: (p) => `“${p.entry}”: with AM or PM the hour runs from 1 to 12.`,
    seconds: (p) => `“${p.entry}” has seconds: these times are to the minute, so leave the seconds out.`,
    too_many: (p) => `Enter at most ${p.count} different times.`,
  };
  const timeError = (code, params = {}) => ({error: code, message: TIME_MESSAGES[code](params)});
  // White space spelled out, the server's time_entry.WHITESPACE (which is
  // JavaScript's \s); ASCII digits only, as on the server.
  const TIME_SPACE = String.raw`[ \t\n\v\f\r\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]`;
  const TIME_SPACES = new RegExp(`${TIME_SPACE}+`);
  const TIME_SUFFIX = String.raw`(?:${TIME_SPACE}*([ap])\.?(?:m\.?)?)?`;
  const TIME_CLOCK = new RegExp(
    String.raw`^([0-9]{1,2})(?:([:.h])([0-9]{2})(?::([0-9]{2}))?)?${TIME_SUFFIX}$`, "i");
  const TIME_RUN = new RegExp(String.raw`^([0-9]{3,4})${TIME_SUFFIX}$`, "i");
  // A list's entries are separated by commas, semicolons or new lines, or by
  // spaces; a suffix standing alone after a space ("2 pm") belongs to the
  // entry before it, but not across a comma, semicolon or new line ("2, pm").
  const TIME_BREAKS = /[,;\n\r\u2028\u2029]+/;
  const TIME_LONE_SUFFIX = /^[ap]\.?(?:m\.?)?$/i;
  const TIME_WORDS = new Map([["noon", 12], ["midnight", 0]]);
  const pad2 = (number) => String(number).padStart(2, "0");
  const timeWords = (text) => String(text ?? "").split(TIME_SPACES).filter(Boolean);
  // A read time is {hour, minute, second}; its canonical text is "HH:MM",
  // or "HH:MM:SS" for a kept saved time with seconds.
  const canonicalTime = (value) => `${pad2(value.hour)}:${pad2(value.minute)}${
    value.second ? `:${pad2(value.second)}` : ""}`;
  const twelveHourTime = (value) => `${value.hour % 12 || 12}:${pad2(value.minute)}${
    value.second ? `:${pad2(value.second)}` : ""} ${value.hour >= 12 ? "PM" : "AM"}`;
  const timeReading = (value) => `${canonicalTime(value)} (${twelveHourTime(value)})`;
  // One entry: {value} or {error, message}. ``kept`` is the canonical text
  // of a saved time with seconds that may be typed back unchanged.
  const parseTime = (text, kept = "") => {
    const entry = timeWords(text).join(" ");
    if (!entry) return timeError("empty");
    if (TIME_WORDS.has(entry.toLowerCase())) {
      return {value: {hour: TIME_WORDS.get(entry.toLowerCase()), minute: 0, second: 0}};
    }
    let hour, minute, second, half, seconds = false;
    let match = TIME_CLOCK.exec(entry);
    if (match) {
      [hour, minute, second] = [match[1], match[3] || 0, match[4] || 0].map(Number);
      seconds = match[4] !== undefined;
      half = match[5];
      // Seconds belong to the 24-hour colon form only: "2:30:00 pm",
      // "14.30:00" and "14h30:00" are refused.
      if (seconds && (half || match[2] !== ":")) return timeError("unreadable", {entry});
    } else if ((match = TIME_RUN.exec(entry))) {
      [hour, minute, second] = [match[1].slice(0, -2), match[1].slice(-2), 0].map(Number);
      half = match[2];
    } else {
      return timeError("unreadable", {entry});
    }
    if (half) {
      if (hour < 1 || hour > 12) return timeError("twelve_hour", {entry});
      hour = hour % 12 + (half.toLowerCase() === "p" ? 12 : 0);
    }
    if (hour > 23) return timeError("hour", {entry, hour});
    if (minute > 59) return timeError("minute", {entry, minute: pad2(minute)});
    if (second > 59) return timeError("unreadable", {entry});
    const value = {hour, minute, second};
    if (second && canonicalTime(value) !== kept) return timeError("seconds", {entry});
    return {value};
  };
  const splitTimes = (text) => {
    const items = [];
    String(text ?? "").split(TIME_BREAKS).forEach((chunk) => {
      let joinable = false;
      timeWords(chunk).forEach((part) => {
        if (joinable && TIME_LONE_SUFFIX.test(part)) items[items.length - 1] += ` ${part}`;
        else items.push(part);
        joinable = true;
      });
    });
    return items;
  };
  // A list: {values, duplicates, blank} (values in the order typed,
  // duplicates as sorted canonical texts), or the first refusal. ``max``
  // bounds the number of different times. A list with no entries (blank, or
  // only separators such as ",") reads as ``blank``, the list a blank field
  // stands for, with ``blank`` true.
  const parseTimes = (text, max = 0, blank = "") => {
    const items = splitTimes(text);
    if (!items.length && blank) return {...parseTimes(blank, max), blank: true};
    const values = [];
    for (const item of items) {
      const result = parseTime(item);
      if (result.error) return result;
      values.push(result.value);
    }
    const texts = values.map(canonicalTime);
    const distinct = [...new Set(texts)].sort();
    if (max && distinct.length > max) return timeError("too_many", {count: max});
    return {values, duplicates: distinct.filter((text) =>
      texts.indexOf(text) !== texts.lastIndexOf(text)), blank: false};
  };
  // Exposed read-only for the browser tests that run the shared fixture.
  window.ParishTimeEntry = Object.freeze({parseTime, parseTimes, timeReading, canonicalTime});

  // Per field: its visible reading line, its visually hidden live region and
  // the timer that waits for a pause in typing.
  const timeParts = new WeakMap();
  const timeGateHints = new WeakMap();
  let timeGateCount = 0;
  const TIME_PAUSE = 500;
  const readTimeEntry = (field) => (field.dataset.timeEntry === "list"
    ? parseTimes(field.value, Number(field.dataset.timeMax) || 0, field.dataset.timeBlank || "")
    : parseTime(field.value, field.dataset.timeKept || ""));
  // What a field's entry reads as: {message, invalid}. A blank single time
  // has no reading (``required`` is the server's); a blank list reads as
  // its data-time-blank, when it has one.
  const readTimeField = (field) => {
    const list = field.dataset.timeEntry === "list";
    if (!list && !timeWords(field.value).length) return {message: "", invalid: false};
    const result = readTimeEntry(field);
    if (result.error) return {message: result.message, invalid: true};
    if (!list) return {message: `Reads as ${timeReading(result.value)}`, invalid: false};
    if (!result.values.length) return {message: "", invalid: false};
    const read = `${result.blank ? "Blank reads as" : "Reads as"} ${
      result.values.map(timeReading).join(", ")}`;
    const repeated = result.duplicates.length
      ? `; ${result.duplicates.join(", ")} ${result.duplicates.length > 1 ? "are" : "is"
      } listed more than once and saved once` : "";
    return {message: read + repeated, invalid: false};
  };
  // Fields with a pending pause timer, so a removed row's or a replaced
  // region's timers can be cleared (cancelTimeChecks).
  const timePending = new Set();
  const cancelTimeCheck = (field) => {
    const parts = timeParts.get(field);
    if (parts) window.clearTimeout(parts.timer);
    timePending.delete(field);
  };
  // Clear the timers of every time field inside ``root``, or, with no root,
  // of every field no longer in the page.
  const cancelTimeChecks = (root) => {
    if (root) root.querySelectorAll("input[data-time-entry]").forEach(cancelTimeCheck);
    else [...timePending].filter((field) => !field.isConnected).forEach(cancelTimeCheck);
  };
  // Show a field's reading or refusal and set its validity, which the Save
  // gates read at once. ``typing`` (an input event on this field) defers a
  // refusal, and the Save hint that explains it, until typing pauses, so a
  // half-typed "2:" is not flashed red; aria-invalid follows what is shown.
  // ``quiet`` keeps the line empty while a server error under the field says
  // the same. Only this field's own typing (after the pause) or blur
  // ``announce``s, through the live region, and only a changed message:
  // loading, pageshow and other controls' changes update the line silently.
  const checkTimeField = (field, {typing = false, announce = false, quiet = false} = {}) => {
    const parts = timeParts.get(field);
    if (!parts) return;
    const {message, invalid} = readTimeField(field);
    field.setCustomValidity(invalid ? message : "");
    cancelTimeCheck(field);
    const shown = quiet ? "" : message;
    const show = (speak) => {
      parts.reading.textContent = shown;
      parts.reading.classList.toggle("is-error", invalid && !quiet);
      field.setAttribute("aria-invalid", String(invalid || quiet));
      if (speak && shown !== parts.spoken) parts.live.textContent = shown;
      parts.spoken = shown;
    };
    if (!typing) {
      show(announce);
      return;
    }
    if (invalid) {
      // A half-typed entry: no stale reading and no red until the pause.
      parts.reading.textContent = "";
      parts.reading.classList.remove("is-error");
      field.setAttribute("aria-invalid", "false");
    } else {
      parts.reading.textContent = shown;
      parts.reading.classList.remove("is-error");
      field.setAttribute("aria-invalid", String(quiet));
    }
    timePending.add(field);
    parts.timer = window.setTimeout(() => {
      timePending.delete(field);
      if (!field.isConnected) return;
      show(true);
      gateTimes(field.form);
    }, TIME_PAUSE);
  };
  // The server's own error under a field (Django's ul.errorlist, id
  // "<field id>_error") is removed on the first edit; the live check then
  // speaks for the field.
  const serverTimeError = (field) => field.id && document.getElementById(`${field.id}_error`);
  const clearServerTimeError = (field) => {
    const error = serverTimeError(field);
    if (!error) return;
    error.remove();
    const described = (field.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((id) => id && id !== error.id);
    field.setAttribute("aria-describedby", described.join(" "));
    field.setAttribute("aria-invalid", "false");
  };
  const isSubmit = (node) => (node instanceof HTMLButtonElement
    || node instanceof HTMLInputElement) && node.type === "submit";
  // Keep a form's submit buttons unavailable while a shown time entry cannot
  // be read, with a hint after them saying why. As with the complete gate,
  // real disabled is used and only buttons this gate disabled are re-enabled.
  // A data-require-complete form is left to that gate: the entry's custom
  // validity is set in a capture-phase listener (below), before the gate's
  // own input listener runs, so it counts the current keystroke.
  // ``defer`` (typing) disables at once but leaves a hidden hint hidden
  // until the pause timer calls again, as the refusal it explains waits.
  const gateTimes = (form, {defer = false} = {}) => {
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-require-complete")) return;
    const fields = [...form.querySelectorAll("input[data-time-entry]")];
    if (!fields.length && !timeGateHints.has(form)) return;
    const unread = fields.some((field) =>
      !field.disabled && !field.closest("[hidden]") && !field.validity.valid);
    // A formnovalidate button (a cancel or back action) stays usable.
    const buttons = [...form.elements].filter((node) => isSubmit(node) && !node.formNoValidate);
    let hint = timeGateHints.get(form);
    const explain = unread && (!defer || Boolean(hint && !hint.hidden));
    if (!hint && explain && buttons.length) {
      hint = document.createElement("p");
      hint.className = "help";
      timeGateCount += 1;
      hint.id = `time-gate-hint-${timeGateCount}`;
      hint.textContent = "Fix the time that can't be read to continue.";
      const last = buttons[buttons.length - 1];
      (last.parentElement === form ? last : last.parentElement).after(hint);
      timeGateHints.set(form, hint);
    }
    if (hint) hint.hidden = !explain;
    buttons.forEach((node) => {
      const described = (node.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && id !== hint?.id);
      if (unread && !node.disabled) {
        node.disabled = true;
        node.setAttribute("data-time-gated", "");
      } else if (!unread && node.hasAttribute("data-time-gated")) {
        node.disabled = false;
        node.removeAttribute("data-time-gated");
      }
      if (explain && hint) described.push(hint.id);
      if (described.length) node.setAttribute("aria-describedby", described.join(" "));
      else node.removeAttribute("aria-describedby");
    });
  };
  // Give each new time field its reading line (which describes the field)
  // and live region, and check it: for the page, a swapped-in region, or a
  // cloned schedule row.
  const wireTimeEntry = (root) => {
    // A swap replaced some fields: their pending timers have nothing to do.
    cancelTimeChecks();
    const forms = new Set();
    root.querySelectorAll("input[data-time-entry]").forEach((field) => {
      if (timeParts.has(field)) return;
      const reading = document.createElement("p");
      reading.className = "time-reading";
      // Always rendered, even empty, so its first message is announced.
      const live = document.createElement("span");
      live.className = "visually-hidden";
      live.setAttribute("aria-live", "polite");
      if (field.id) {
        reading.id = `${field.id}_reading`;
        field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"),
          reading.id].filter(Boolean).join(" "));
      }
      field.after(reading, live);
      timeParts.set(field, {reading, live, timer: 0, spoken: ""});
      checkTimeField(field, {quiet: Boolean(serverTimeError(field))});
      forms.add(field.form);
    });
    forms.forEach(gateTimes);
  };
  // Capture phase: the entry's validity is current before any form's own
  // input listener (the complete gate) runs.
  document.addEventListener("input", (event) => {
    const field = event.target;
    if (!(field instanceof HTMLInputElement) || !timeParts.has(field)) return;
    clearServerTimeError(field);
    checkTimeField(field, {typing: true});
    gateTimes(field.form, {defer: true});
  }, true);
  // As focus leaves, a refusal (and the Save hint) shows at once and is
  // announced if it changed, and a readable entry is
  // rewritten in its canonical form: "2pm" becomes "14:00", a list "2am,2 pm"
  // becomes "02:00, 14:00" (repeats stay, so the reading still reports them;
  // the server saves each time once). A blank entry stays blank.
  document.addEventListener("focusout", (event) => {
    const field = event.target;
    if (!(field instanceof HTMLInputElement) || !timeParts.has(field)) return;
    const quiet = Boolean(serverTimeError(field));
    const result = readTimeEntry(field);
    const blank = field.dataset.timeEntry === "list" ? !splitTimes(field.value).length
      : !timeWords(field.value).length;
    if (!result.error && !blank) {
      field.value = result.values ? result.values.map(canonicalTime).join(", ")
        : canonicalTime(result.value);
    }
    checkTimeField(field, {quiet, announce: true});
    gateTimes(field.form);
  });
  // Another control can show, hide, enable or clear a time field without an
  // event on it (a schedule's mail type, the refresh frequency), and the
  // back/forward cache can restore values silently: check again then.
  const recheckTimes = (form) => {
    if (!(form instanceof HTMLFormElement)) return;
    form.querySelectorAll("input[data-time-entry]").forEach((field) =>
      checkTimeField(field, {quiet: Boolean(serverTimeError(field))}));
    gateTimes(form);
  };
  document.addEventListener("change", (event) => {
    if (event.target instanceof Element) recheckTimes(event.target.closest("form"));
  });
  window.addEventListener("pageshow", () => {
    new Set([...document.querySelectorAll("input[data-time-entry]")].map((field) =>
      field.form)).forEach(recheckTimes);
  });

  // Shared Admin tables (web/tables.py, table-navigator.html and
  // table-selection.html). Row selection is per page: Select all and the
  // header checkbox choose every enabled row checkbox shown, and bulk action
  // buttons stay disabled until something is chosen. The server still
  // validates every submitted selection.
  const wireSelection = (scope) => {
    const rows = () => [...scope.querySelectorAll("input[data-select-row]")]
      .filter((node) => !node.disabled);
    const header = scope.querySelector("input[data-select-all]");
    const button = scope.querySelector("[data-select-all-button]");
    const count = scope.querySelector("[data-selected-count]");
    const actions = scope.querySelectorAll("[data-bulk-action]");
    const update = () => {
      const all = rows();
      const chosen = all.filter((node) => node.checked).length;
      const every = chosen > 0 && chosen === all.length;
      if (header) {
        header.checked = every;
        header.indeterminate = chosen > 0 && !every;
      }
      if (button) {
        button.hidden = !all.length;
        button.textContent = every ? "Clear selection" : "Select all";
      }
      if (count) count.textContent = chosen ? `${chosen} selected` : "";
      actions.forEach((node) => { node.disabled = !chosen; });
    };
    const choose = (checked) => {
      rows().forEach((node) => { node.checked = checked; });
      update();
    };
    header?.addEventListener("change", () => choose(header.checked));
    button?.addEventListener("click", () => {
      const all = rows();
      choose(!(all.length && all.every((node) => node.checked)));
    });
    scope.addEventListener("change", (event) => {
      if (event.target instanceof HTMLInputElement
          && event.target.matches("[data-select-row]")) update();
    });
    update();
  };
  // A new rows-per-page choice applies at once and starts again at page 1.
  // The submission has no submitter, so the select is noted for the in-place
  // handler below, which puts focus back on its replacement.
  const pendingControls = new WeakMap(); // form → control that submitted it
  const wirePageSize = (root) => {
    root.querySelectorAll("select[data-page-size]").forEach((select) => {
      select.addEventListener("change", () => {
        const page = select.form?.querySelector("[data-page-number]");
        if (page) page.value = "1";
        if (select.form) pendingControls.set(select.form, select);
        select.form?.requestSubmit();
      });
    });
  };
  // Everything above that binds to elements inside a table, so a table
  // swapped in by the in-place re-sort below behaves like one the page
  // loaded with.
  const enhanceTable = (root) => {
    wireCopyButtons(root);
    wireSummaryLinks(root);
    wireValidity(root);
    root.querySelectorAll("[data-select-table]").forEach(wireSelection);
    wirePageSize(root);
    wireTimeEntry(root);
    if (root === document) return;
    wireBrowserTimezone(root);
    if (window.ParishDates) window.ParishDates.localize(root);
    // A swapped-in region can carry charts (the response dashboard); the
    // chart engine draws them as it drew the page's own (chart-v1.js).
    if (window.ParishCharts) window.ParishCharts.render(root);
  };
  enhanceTable(document);

  // In-place controls (#478, #484, #519): an Admin control changes the page
  // where the reader is, without a reload or a jump to the top.
  //
  // A page marks each part a control can change as a region: an element
  // with a stable id and data-in-place-region, or data-table-region for a
  // shared Admin table (its navigators and rows; the id is the TablePage's
  // anchor). The id is also the control's URL fragment, so the ordinary
  // load (without script, or when anything below goes wrong) lands on the
  // region rather than at the top of the page.
  //
  // Activating a control fetches exactly the request the control would have
  // made (a link's GET, a form's query or CSRF POST body), finds the regions
  // in the fetched page and swaps them in, so the reader keeps their scroll
  // position and focus. The server sees an ordinary page request and no
  // special response path exists. The controls are:
  // - a shared table's sort headings, navigator links and forms, and the
  //   page's filter form (form#table-filters, #484), unless that form is
  //   itself a form[data-in-place] because its options reshape far more of
  //   the page than a table (the participation report's options, which
  //   refresh its statistics, chart and export panels, #519);
  // - a[data-in-place]: a link that shows another view of this page (the
  //   response dashboard's mode and grain, a list's Refresh). Its value, if
  //   any, is a stable key that finds the link again in the fresh page, for
  //   focus and to announce the view now shown ("By day");
  // - form[data-in-place]: a form whose answer is this page again, such as a
  //   POST whose server redirects back here (Post/Redirect/Get). A POST
  //   saves a change unless the form is marked data-in-place-read (System
  //   logs' Show related entries, which filters the list in a POST body):
  //   a read may be cancelled by a newer choice and, with no answer at all,
  //   falls back to the ordinary submission, as a table's POST does. A form
  //   marked data-in-place-filters sets the page's filters from outside
  //   form#table-filters, so the filter form then shows the filters the
  //   fresh page applied (syncFilters).
  // A data-in-place link or form names the region it changes by its URL's
  // fragment, or else by the region it sits in. Its data-in-place-message
  // ("List refreshed.") is announced before the region's row count.
  // A page script can follow an a[data-in-place] link on the reader's behalf
  // (the participation report applying this browser's time zone as it
  // loads, report-v1.js) by marking the link data-in-place-quiet. The regions
  // are swapped, but focus is not moved and nothing is announced, since the
  // reader did not act, and the address (or, should the fetch fail, the
  // ordinary load) keeps the page's own fragment, not the region's.
  const keepHash = (url) => {
    const address = new URL(url, document.baseURI);
    address.hash = window.location.hash;
    return address.href;
  };
  // Every answer refreshes every region on the page, unless the link is
  // marked data-in-place-only: then only the region it names is swapped (a
  // history pager nested in a panel whose form holds unsaved typing). A
  // link whose key is gone from the fresh page (Older history on the last
  // page) hands focus to the link its data-in-place-fallback key names. A
  // form marked data-in-place-anywhere changes a region every Admin page
  // draws (the critical-problems banner): whichever same-origin page
  // answers it, only that region is taken from the answer, and the address
  // stays.
  const REGIONS = "[data-in-place-region][id], [data-table-region][id]";
  const isRegion = (node) => Boolean(node && node.matches(REGIONS));
  // A checkbox that submits its own in-place form when it changes (#621).
  const SUBMIT_ON_CHANGE = "form[data-in-place] input[type=checkbox][data-submit-on-change]";
  const isBox = (node) => node instanceof HTMLInputElement && node.matches(SUBMIT_ON_CHANGE);
  // A form's submit buttons, including a <button> with no type (a submit
  // button by default).
  const SUBMIT = "button:not([type]), button[type=submit], input[type=submit]";
  // A form's first submit button, including one outside it that names it
  // with form="…" (form.elements lists those; querySelector would not).
  const firstSubmit = (form) => [...form.elements].find((node) => node.matches(SUBMIT));
  // Only this site's own pages are fetched and swapped in; any other address
  // keeps its ordinary meaning.
  const sameOrigin = (url) => new URL(url, document.baseURI).origin === window.location.origin;
  const tableStatus = document.createElement("div");
  tableStatus.className = "visually-hidden";
  tableStatus.setAttribute("role", "status");
  (document.querySelector("main") || document.body).append(tableStatus);
  // Text waiting for the 50 ms announcement timer, or null.
  let announcing = null;
  const announce = (text) => {
    // A second message within the window joins the first instead of
    // replacing it ("Session revoked." then "Ended sessions shown.").
    if (announcing !== null) {
      // Text already waiting is not said twice ("Still saving…").
      if (!announcing.includes(text)) announcing = `${announcing} ${text}`;
      return;
    }
    announcing = text;
    // Clearing first makes a repeated message (the same count after a
    // re-sort) read again.
    tableStatus.textContent = "";
    window.setTimeout(() => {
      tableStatus.textContent = announcing;
      announcing = null;
    }, 50);
  };
  // One request in flight for the whole page: every response rewrites every
  // region, so a newer choice anywhere supersedes an older one, and an older
  // response can never overwrite a newer swap.
  let tableRequest = null; // the AbortController of the request in flight
  // The region the latest request marked aria-busy. An aborted request
  // leaves the mark only when that newer request owns the same region.
  let busyRegion = null;
  // A form[data-in-place] POST saves a change, so it is never aborted: while
  // it is in flight every other in-place control (a heading, Next, a view
  // link, the filters) is ignored, as a repeated submission is. Aborting it
  // would leave the change saved but unreported and the page out of date.
  let saving = null; // the AbortController of a saving POST in flight
  // The table region a table control belongs to, or null when the control
  // is not inside one (a page that keeps full loads) or the region has no
  // id to match by; either way the control keeps its ordinary meaning.
  const tableRegion = (node) => {
    const region = node.closest("[data-table-region]");
    return region && region.id ? region : null;
  };
  // The region a data-in-place link or form changes: the one the fragment
  // of its own href or action attribute names, else the one it sits in. The
  // attribute is read as written, so an empty action (this page) never
  // borrows the fragment of the address the page happens to show.
  const targetRegion = (control, written) => {
    const hash = (written || "").split("#")[1];
    const named = hash ? document.getElementById(hash) : null;
    if (isRegion(named)) return named;
    const around = control.closest("[data-in-place-region], [data-table-region]");
    return isRegion(around) ? around : null;
  };
  const withFragment = (url, id) => {
    const target = new URL(url, document.baseURI);
    target.hash = id;
    return target.href;
  };
  // Where focus goes after the swap. owner is the a[data-in-place] link or
  // form[data-in-place] that opted the control into the generic mechanism,
  // or null for a table control. The click and submit handlers decide it,
  // never the control's ancestors: a form="…" button can sit outside its
  // form, and a table's sort heading or navigator can sit inside a
  // form[data-in-place] (a selection form around its table) and still be a
  // table control. A data-in-place control, or any control outside the
  // region it changed, keeps focus while it is still in the document (a
  // filter button); one that was replaced (it sat in a region, or in a
  // data-table-sync node) is found again by its id, then by its owner's
  // data-in-place key (then its data-in-place-fallback key), a form's key
  // standing for its first button. A table control returns to the same
  // heading (by column) or the matching control of the same navigator. Previous and Next become plain text on the first
  // or last page, so each falls back to the other. Anything still missing,
  // or disabled, falls back to the region's first heading, else the region
  // itself (refreshRegions).
  const focusAfter = (region, control, owner) => {
    if (!region.contains(control) || owner) {
      const keys = [owner?.dataset.inPlace, owner?.dataset.inPlaceFallback].filter(Boolean);
      return () => {
        if (control.isConnected) return control;
        const again = control.id && document.getElementById(control.id);
        if (again) return again;
        const found = keys.map((key) => document.querySelector(`[data-in-place="${CSS.escape(key)}"]`))
          .find(Boolean);
        return found instanceof HTMLFormElement ? firstSubmit(found) : found;
      };
    }
    const heading = control.closest("th[data-sort-column]");
    if (heading) {
      const column = CSS.escape(heading.dataset.sortColumn);
      return (fresh) => fresh.querySelector(`th[data-sort-column="${column}"] .sort-link`);
    }
    const navs = [...region.querySelectorAll(".table-nav")];
    const index = navs.indexOf(control.closest(".table-nav"));
    const choices = control.matches("[data-table-previous]") ? ["[data-table-previous]", "[data-table-next]"]
      : control.matches("[data-table-next]") ? ["[data-table-next]", "[data-table-previous]"]
      : control.matches("[data-page-size]") ? ["[data-page-size]"]
      : control.matches("[data-page-number]") ? ["[data-page-number]"]
      : [".table-nav-form [type=submit]"];
    return (fresh) => {
      const nav = fresh.querySelectorAll(".table-nav")[index];
      return choices.map((selector) => nav?.querySelector(selector)).find(Boolean);
    };
  };
  // What the live region says (owner as for focusAfter). A data-in-place
  // control says its message
  // ("List refreshed.") and the rows now shown, or names the view now shown
  // (its fresh link's text, "By day"). A table control says the sorted
  // column and its direction after a heading, otherwise the navigator's own
  // "Showing 26–50 of 120 · Page 2 of 5". A filter changes every table on
  // the page, so each table's count is read, named by its navigator when
  // there is more than one ("Member pages: …").
  const squeeze = (text) => (text || "").replace(/\s+/g, " ").trim();
  const tableCount = (region) => squeeze(region.querySelector(".table-count")?.textContent);
  const describeTable = (fresh, control, owner) => {
    if (owner) {
      // A submit-on-change box says what its state now shows ("Ended
      // sessions shown.").
      if (isBox(control)) {
        // What the page now shows, which the box's tick may not match yet.
        const shown = control.dataset.applied === "true";
        const said = control.getAttribute(shown ? "data-message-on" : "data-message-off");
        if (said) return said;
      }
      const message = owner.getAttribute("data-in-place-message");
      if (message) return [message, tableCount(fresh)].filter(Boolean).join(" ");
      // A form without a message names what was done by its button's text
      // ("Save"), never by the whole form's text.
      if (owner instanceof HTMLFormElement) {
        return squeeze(control instanceof HTMLInputElement ? control.value : control.textContent)
          || tableCount(fresh);
      }
      const key = owner.dataset.inPlace;
      const view = key && document.querySelector(`[data-in-place="${CSS.escape(key)}"]`);
      return view ? squeeze(view.textContent) : tableCount(fresh);
    }
    if (!control.closest("[data-table-region]")) {
      const regions = [...document.querySelectorAll("[data-table-region][id]")];
      if (regions.length < 2) return tableCount(fresh);
      return regions.map((region) => {
        const label = region.querySelector(".table-nav")?.getAttribute("aria-label");
        return label ? `${label}: ${tableCount(region)}` : tableCount(region);
      }).join(". ");
    }
    const column = control.closest("th[data-sort-column]")?.dataset.sortColumn;
    if (column === undefined) return tableCount(fresh);
    const heading = fresh.querySelector(`th[data-sort-column="${CSS.escape(column)}"]`);
    const label = [...(heading?.querySelector(".sort-link")?.childNodes || [])]
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent).join("").trim();
    const state = heading?.getAttribute("aria-sort");
    return state ? `Sorted by ${label}, ${state}` : `Sorted by ${label}`;
  };
  // Enhance freshly swapped-in content as the page's own was enhanced, then
  // tell any other script (a bubbling parishkit:swap event on the fresh
  // root), so it can bind to the new elements without a load-order tie to
  // this file.
  const enhanceSwapped = (fresh) => {
    enhanceTable(fresh);
    fresh.dispatchEvent(new CustomEvent("parishkit:swap", {bubbles: true}));
  };
  // Swap one region for its fresh copy. Selections are restored on the copy
  // before it enters the document, so wireSelection's first update already
  // counts them and no change event fires for a tick the reader did not make.
  const swapRegion = (region, fresh) => {
    const chosen = new Set([...region.querySelectorAll("input[data-select-row]:checked")]
      .map((box) => box.value));
    fresh.querySelectorAll("input[data-select-row]").forEach((box) => {
      if (chosen.has(box.value)) box.checked = true;
    });
    // Charts keep their space until the fresh ones are drawn (chart-v1.js).
    if (window.ParishCharts) window.ParishCharts.hold(region, fresh);
    region.replaceWith(fresh);
    enhanceSwapped(fresh);
  };
  // Controls and text outside the regions that follow a table's page, size,
  // sort or filters (or any in-place refresh) are marked data-table-sync
  // with a stable id. A form keeps its element, and so every script bound to
  // it and every visible choice (an export's format or timezone, a packet's
  // history tick, the filters just typed), and takes from the fetched page
  // only its hidden fields, which carry the table state: a filter form's
  // size and sort, an export's query and one-time request key. A filter can
  // add or drop a hidden field (an export carries only the filters in use),
  // so the hidden fields are matched by name and position, updated, removed
  // or added. A non-form node (a refresh link, a count or summary, a list of
  // choices drawn from the rows) is replaced whole and enhanced again; its
  // ticks are lost, as a full load would lose them. A browser-zone field is
  // left alone: it holds this browser's zone, not table state, and a fetched
  // page renders only the zone its request carried (none after a re-sort of
  // a page no filter was applied to), which must not blank it (#558).
  const syncHidden = (form, fresh) => {
    const hidden = (root) => [
      ...root.querySelectorAll('input[type="hidden"][name]:not([data-browser-zone])'),
    ];
    const wanted = new Map(); // name → the fresh fields of that name, in order
    hidden(fresh).forEach((field) => {
      const name = field.getAttribute("name");
      wanted.set(name, [...(wanted.get(name) || []), field]);
    });
    hidden(form).forEach((field) => {
      const source = wanted.get(field.getAttribute("name"))?.shift();
      if (source) field.value = source.value;
      else field.remove();
    });
    form.prepend(...[...wanted.values()].flat().map((field) => document.importNode(field)));
  };
  // After a control that sets the filters from outside the filter form
  // (data-in-place-filters), the filter form must show what the fresh page
  // applied, or the next Apply, sort or page would quietly undo it. Its
  // visible fields take the fresh page's values (syncHidden already took the
  // hidden ones), a details[id] in it opens or closes as the fresh page
  // draws it ("Filter by identifier" opens when one is set), and the Apply
  // gate checks again. Only then: any other swap keeps filters the reader
  // has typed but not applied. The browser-zone field holds this browser's
  // zone and is left alone (see syncHidden).
  const syncFilters = (parsed) => {
    const form = document.getElementById("table-filters");
    const fresh = parsed.getElementById("table-filters");
    if (!(form instanceof HTMLFormElement) || !(fresh instanceof HTMLFormElement)) return;
    const copies = [...fresh.elements];
    [...form.elements].forEach((field) => {
      if (!field.name || field.type === "hidden" || field.matches("[data-browser-zone]")) return;
      const copy = copies.find((node) => node.name === field.name);
      if (field.type === "checkbox") field.checked = Boolean(copy?.checked);
      else if (copy && "value" in field) field.value = copy.value;
    });
    form.querySelectorAll("details[id]").forEach((node) => {
      node.open = Boolean(fresh.querySelector(`#${CSS.escape(node.id)}`)?.open);
    });
    form.dispatchEvent(new Event("change"));
  };
  const syncControls = (parsed) => {
    document.querySelectorAll("[data-table-sync][id]").forEach((node) => {
      const fresh = parsed.getElementById(node.id);
      if (!fresh) return;
      if (node instanceof HTMLFormElement) {
        syncHidden(node, fresh);
        // The state a submit-on-change box's form now shows (see
        // reconcileBoxes); the box keeps the reader's own tick.
        fresh.querySelectorAll("[data-submit-on-change][data-applied][id]").forEach((copy) => {
          const box = node.querySelector(`#${CSS.escape(copy.id)}`);
          if (box) box.dataset.applied = copy.dataset.applied;
        });
        return;
      }
      node.replaceWith(fresh);
      enhanceSwapped(fresh);
    });
  };
  // A saving POST that got no answer at all may or may not have reached the
  // server, so it is not sent again; an alert at the top of the form says so
  // instead, until the next attempt.
  const UNREACHABLE = "The server could not be reached, so this page was not updated. "
    + "Your change may or may not have been saved: reload the page to check before trying again.";
  const clearUnreachable = (form) => form?.querySelector("[data-in-place-error]")?.remove();
  const showUnreachable = (form) => {
    clearUnreachable(form);
    const note = document.createElement("p");
    note.className = "notice notice-error";
    note.setAttribute("role", "alert");
    note.setAttribute("data-in-place-error", "");
    note.textContent = UNREACHABLE;
    form.prepend(note);
  };
  // What the live region says for a refusal: its summary's messages, each
  // read as a sentence (focus on the summary already reads its heading), or
  // this when it has no summary.
  const NOT_ACCEPTED = "The server did not accept this. Please check the form and try again.";
  const sentences = (nodes) => nodes.filter(Boolean).map((node) => squeeze(node.textContent))
    .filter(Boolean).map((line) => (/[.!?:]$/.test(line) ? line : `${line}.`)).join(" ");
  // A refusal's error summary is shown where the reader is. The shared
  // summary (components/errors.html) is drawn by base.html at the top of
  // <main>, outside every region, so when the fresh region does not already
  // hold one, the summary drawn outside it is moved to the top of that
  // region. This page's own summaries outside the regions (an earlier
  // refusal of an ordinary form) are removed first: the refusal replaces
  // them, and no second error-title id appears. A successful swap leaves
  // them alone. Returns the summary the fresh region now holds, or null.
  const placeSummary = (parsed, fresh) => {
    document.querySelectorAll("[data-error-summary]").forEach((node) => {
      if (!node.closest(REGIONS)) node.remove();
    });
    const inside = fresh.querySelector("[data-error-summary]");
    if (inside) return inside;
    const loose = [...parsed.querySelectorAll("[data-error-summary]")]
      .find((node) => !node.closest(REGIONS));
    if (loose) fresh.prepend(loose);
    return loose || null;
  };
  // Show an answer as the whole page, as a native submission would have shown
  // it. document.open keeps this window: it erases the old page's event
  // listeners but not its timers, which would keep running beside the new
  // page's own (session-v1.js's one-second tick, the header polls) and act on
  // detached elements (#562). So the old page is first told it is going away,
  // with a pagehide that is not persisted: the pollers that listen for it
  // stop, and a reply already in flight can neither re-arm their timers nor
  // act. Then every timer id in this window up to a fresh one is cleared
  // (clearTimeout clears an interval too), which also stops timers no script
  // exposes a way to stop. That relies on browsers numbering timers in
  // sequence, as Chromium, WebKit and Firefox do.
  const showAsReturned = (text) => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: false}));
    const newest = window.setTimeout(() => {}, 0);
    for (let id = 1; id <= newest; id += 1) window.clearTimeout(id);
    document.open();
    document.write(text);
    document.close();
  };
  // Load the page a control leads to and bring this page up to date from it.
  // Success: every region present in both pages is swapped (the one the
  // control changes and any other on the page, so they never disagree), the
  // sync controls take their values, the address bar follows a GET choice
  // or a followed redirect, and focus and the live region report the result.
  //
  // A POST that got an answer is never sent again: that would repeat it (a
  // second access-audit row for a report read, a second change for a save).
  // So a successful answer that is not a page with this region (a sign-in
  // page after the session ended), or for a data-in-place control a page at
  // another path or origin, is shown by loading its address only when a
  // redirect led there (that address is a GET, safe to repeat); a POST's own
  // answer is shown as returned. A refused POST (an error answer, or a page
  // with an error summary) that carries this region is swapped in like a
  // success, its summary focused and announced, and the address left alone;
  // one without the region is shown as returned (#562). A GET is
  // safe to repeat, so a GET's error answer, or a fetch with no answer at
  // all, falls back to the control's ordinary navigation. A read-only table
  // POST with no answer falls back to the ordinary submission too; a saving
  // POST is never re-sent at all, and says the server could not be reached
  // instead (see above). A newer choice aborts an older request, unless that
  // one is a saving POST.
  //
  // options.owner is the data-in-place link or form behind the control (null
  // for a table control; see focusAfter); options.form is the
  // form a saving data-in-place POST came from (a form="…" submitter can sit
  // outside it).
  const refreshTable = async (region, control, url, init, fallback, options = {}) => {
    if (saving) {
      // The control was ignored; say why rather than doing nothing visibly.
      announce("Still saving…");
      // A box applies itself once the save settles (reconcileBoxes), and
      // says so beside it too.
      if (isBox(control)) {
        heldBoxes.add(control);
        showHeld(control);
      }
      return;
    }
    if (isBox(control)) sentState.set(control, String(control.checked));
    tableRequest?.abort();
    const controller = new AbortController();
    tableRequest = controller;
    const form = options.form || null;
    if (form) {
      saving = controller;
      clearUnreachable(form);
    }
    try {
      await refreshRegions(region, control, url, init, fallback, options, controller, form);
    } finally {
      if (saving === controller) saving = null;
      // Nothing is in flight once the latest request settles (an aborted
      // one leaves the newer request in place).
      if (tableRequest === controller) tableRequest = null;
      // After the submit handler has released its form (see settle).
      window.setTimeout(reconcileBoxes, 0);
    }
  };
  // A box that submits its form when it changes (data-submit-on-change,
  // #621) must not be left disagreeing with the page. Its data-applied
  // attribute is the state the page was rendered for, taken from every
  // fetched page (syncControls). A box is resubmitted only when its own
  // change never got an answer of its own: it changed again while its
  // request ran (the repeat was ignored), a newer request overtook its
  // request, or a save held it back (heldBoxes). Never after its request
  // fell back to a full load or got a page shown as returned (the page is
  // leaving, or the server cannot answer: resending would loop), and
  // never the same state again after that state failed (failedState),
  // until the reader changes the box or a request succeeds.
  const heldBoxes = new WeakSet();
  const sentState = new WeakMap(); // box → "true"/"false" last sent
  const failedState = new WeakMap(); // box → the state whose request failed
  let leaving = false; // set when a fallback hands the page to the browser
  const showHeld = (box) => {
    if (box.form.querySelector("[data-held-note]")) return;
    const note = document.createElement("p");
    note.className = "helptext";
    note.setAttribute("data-held-note", "");
    note.textContent = "Still saving… this applies when the save finishes.";
    (box.closest("label") || box).after(note);
  };
  const clearHeld = (box) => box.form?.querySelector("[data-held-note]")?.remove();
  // A fallback that did not actually leave (the full load got no answer
  // either, as WebKit shows offline) would leave a held box stuck with its
  // note; the reader's next interaction shows the page is still here.
  ["pointerdown", "keydown"].forEach((type) => document.addEventListener(type, () => {
    if (!leaving) return;
    leaving = false;
    window.setTimeout(reconcileBoxes, 0);
  }, true));
  const reconcileBoxes = () => {
    if (saving || tableRequest || leaving) return;
    document.querySelectorAll(SUBMIT_ON_CHANGE).forEach((box) => {
      if (!heldBoxes.has(box) || inFlight.has(box.form)) return;
      heldBoxes.delete(box);
      clearHeld(box);
      const wanted = String(box.checked);
      if (!box.hasAttribute("data-applied") || wanted === box.dataset.applied
          || failedState.get(box) === wanted) return;
      pendingControls.set(box.form, box);
      box.form.requestSubmit();
    });
  };
  // Set while the reader is leaving the page (see refreshRegions). A
  // navigation that never leaves (a download, a cancelled prompt) clears it
  // again after a moment, since this page then keeps running.
  let unloading = false;
  window.addEventListener("beforeunload", () => {
    unloading = true;
    window.setTimeout(() => { unloading = false; }, 2000);
  });
  // A page brought back from the back/forward cache is running again.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) unloading = false;
  });
  const refreshRegions = async (region, control, url, init, fallback, options, controller, form) => {
    const owner = options.owner || null;
    // Every way out below that hands the page to the browser (a full load,
    // a page shown as returned) marks it leaving, so no box resubmits, and
    // marks a box's state as failed.
    const leave = (act) => {
      leaving = true;
      if (isBox(control)) failedState.set(control, sentState.get(control));
      act();
    };
    const focus = focusAfter(region, control, owner);
    const id = region.id;
    region.setAttribute("aria-busy", "true");
    busyRegion = region;
    let response, text;
    try {
      response = await fetch(url, {
        ...init,
        signal: controller.signal,
        credentials: "same-origin",
        headers: {"X-Requested-With": "fetch"},
      });
      text = await response.text();
    } catch (error) {
      // No answer at all (a network error): the ordinary navigation, or for
      // a saving POST the note that says so. Only a newer request aborts
      // one, and that request has already marked its own region busy
      // (busyRegion): when it is this same region the mark is left to it,
      // otherwise this region is cleared, since nothing else will.
      const aborted = error.name === "AbortError";
      if (!aborted || busyRegion !== region) region.removeAttribute("aria-busy");
      if (aborted) {
        // Overtaken by a newer request: the box applies itself afterwards.
        if (isBox(control)) heldBoxes.add(control);
        return;
      }
      // A read the browser cut off because the reader is leaving the page
      // (some engines report that as a network error, not an abort) must not
      // fall back: the fallback's own navigation would hijack theirs. A
      // saving POST's note is still shown: beforeunload also fires for a
      // download link, and that page stays.
      if (unloading && !form) return;
      if (form) showUnreachable(form);
      else leave(fallback);
      return;
    }
    const parsed = new DOMParser().parseFromString(text, "text/html");
    const fresh = parsed.getElementById(id);
    const answered = new URL(response.url);
    // An error answer (a refused filter's 400, a denial, an unavailable
    // report) or a page that renders its own error summary is a refusal. A
    // GET is safe to repeat, so a refused GET takes the ordinary navigation.
    // A POST is never sent again (the report views audit every POST, and a
    // saving POST would repeat its change): a refusal that is this page again
    // (a form re-rendered with its errors and the values the reader sent) is
    // swapped in like a success, so the reader keeps their place (#562); any
    // other refusal is shown whole, as a native submission would show it.
    const refused = !response.ok || Boolean(parsed.querySelector("[data-error-summary]"));
    if (refused && init.method !== "POST") {
      region.removeAttribute("aria-busy");
      leave(fallback);
      return;
    }
    // A redirect chose the answer's address: a refusal that was redirected
    // to another page (the sign-in page) is that page, not this one.
    const otherOrigin = answered.origin !== window.location.origin;
    const foreign = otherOrigin || (response.redirected && answered.pathname !== window.location.pathname);
    // A form marked data-in-place-anywhere changes a region every Admin page
    // draws (the critical-problems banner), so any same-origin page that
    // answers it will do: its server may redirect to Home from any page. An
    // error page draws that region too, but a refusal is not an answer
    // about it, so the error page is shown whole, with its own explanation.
    const anywhere = Boolean(owner?.hasAttribute("data-in-place-anywhere"));
    if (refused && (foreign || anywhere || !isRegion(fresh))) {
      leave(() => showAsReturned(text));
      return;
    }
    const elsewhere = otherOrigin
      || (owner && !anywhere && answered.pathname !== window.location.pathname);
    if (!refused && (elsewhere || !isRegion(fresh))) {
      if (response.redirected || init.method !== "POST") {
        leave(() => window.location.assign(withFragment(response.url, id)));
      } else {
        leave(() => showAsReturned(text));
      }
      return;
    }
    const summary = refused ? placeSummary(parsed, fresh) : null;
    // The server answered with this page: nothing is leaving any more.
    leaving = false;
    if (isBox(control)) failedState.delete(control);
    // An answer from anywhere is another page: only the named region is
    // taken from it, never its other regions, sync nodes or address.
    const only = anywhere || Boolean(owner?.hasAttribute("data-in-place-only"));
    document.querySelectorAll(REGIONS).forEach((other) => {
      if (only && other !== region) return;
      const copy = parsed.getElementById(other.id);
      if (isRegion(copy)) swapRegion(other, copy);
    });
    if (!anywhere) syncControls(parsed);
    if (owner?.hasAttribute("data-in-place-filters")) syncFilters(parsed);
    // A GET choice belongs in the address bar, so reload, bookmarks and
    // returning to the page keep it; a POST table's private filters never
    // reach a URL. A server redirect chose the address itself (a saving
    // POST's Post/Redirect/Get answer, so a reload repeats only the GET).
    // An answer from anywhere leaves the address this page's.
    if (!anywhere && response.redirected) {
      window.history.replaceState(window.history.state, "", withFragment(response.url, id));
    } else if (!anywhere && init.method !== "POST") {
      window.history.replaceState(window.history.state, "", options.quiet ? keepHash(url) : url);
    }
    if (options.quiet && !refused) return;
    // A refusal's summary (now in the region) takes focus, as it does on an
    // ordinary load; without one the control does, as after a success. A
    // control that is gone (a save that closed a follow-up request leaves no
    // form) or disabled cannot take focus, so the region's first heading
    // does, which names what changed; failing that, the region itself. A
    // region left empty (an acknowledged banner, the last security event)
    // has nothing to focus or say, so the page's own heading takes focus.
    const swapped = document.getElementById(id);
    let target = summary || focus(swapped);
    if (!target || target.disabled) {
      target = swapped.querySelector("h1, h2, h3, h4, h5, h6")
        || (squeeze(swapped.textContent) ? swapped : document.querySelector("main h1") || swapped);
      target.setAttribute("tabindex", "-1");
    }
    target.focus({preventScroll: true});
    // Focus without scrolling kept the reader's place; if the control itself
    // is just outside the viewport, bring it (and nothing more) into view.
    target.scrollIntoView({block: "nearest"});
    if (!refused) announce(describeTable(document.getElementById(id), control, owner));
    else if (summary) announce(sentences([...summary.querySelectorAll("li")]));
    else announce(NOT_ACCEPTED);
  };
  // Links: a GET table's sort headings and navigator Previous and Next, and
  // every a[data-in-place]. A modified click (new tab, new window) keeps its
  // ordinary meaning.
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey || !(event.target instanceof Element)) return;
    const link = event.target.closest("a.sort-link, .table-nav a[href], a[data-in-place][href]");
    // A link that opens elsewhere (another window, a download, another
    // site) keeps its ordinary meaning.
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")
        || !sameOrigin(link.href)) return;
    // Only a link marked data-in-place itself is generic; a sort heading or
    // navigator link stays a table control even inside a form[data-in-place].
    const owner = link.matches("[data-in-place]") ? link : null;
    const region = owner ? targetRegion(link, link.getAttribute("href")) : tableRegion(link);
    if (!region) return;
    event.preventDefault();
    const quiet = Boolean(owner?.hasAttribute("data-in-place-quiet"));
    const target = quiet ? keepHash(link.href) : link.href;
    refreshTable(region, link, link.href, {method: "GET"}, () => window.location.assign(target),
      {owner, quiet});
  });
  // Forms: a POST table's headings, Previous and Next, every table's
  // page-number form, the page's filter form (#484), whose new filters
  // refresh every table region, the summaries and counts marked
  // data-table-sync, and the export form's hidden query, and every
  // form[data-in-place]. Capture phase, so this runs before the busy-state
  // handler below, which leaves a submission script already took over
  // alone. The browser has already checked the form's own constraints (a
  // pattern, a date) before this event fires.
  // Forms whose in-place request is still in flight. The busy-state handler
  // below never sees an in-place submission (it is defaultPrevented), so
  // repeats (a double click, Enter pressed twice) are ignored here instead,
  // and the submitter is marked aria-disabled until the request settles.
  const inFlight = new WeakSet();
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
    let region, control;
    let owner = null; // the form[data-in-place] behind the submission, if any
    // Attributes, not properties: a hidden field named "action" or "method"
    // (a report's own filter) would shadow the form's property of that name.
    // A submitter's formaction or formmethod overrides the form's, as in a
    // native submission.
    const written = event.submitter?.getAttribute("formaction") ?? form.getAttribute("action");
    const method = (event.submitter?.getAttribute("formmethod")
      ?? form.getAttribute("method") ?? "get").toLowerCase();
    if (form.matches(".sort-form, .table-nav form")) {
      region = tableRegion(form);
      // A rows-per-page change submits its form from script, with no
      // submitter; wirePageSize noted the select. Failing both, the Go button.
      control = event.submitter || pendingControls.get(form) || form;
      pendingControls.delete(form);
    } else if (form.matches("form#table-filters[data-table-sync]:not([data-in-place])")) {
      // The first table on the page stands for them all: every region in
      // the response is swapped, and focus stays on the filter button.
      region = document.querySelector("[data-table-region][id]");
      control = event.submitter || form.querySelector("[type=submit]") || form;
    } else if (form.matches("form[data-in-place]")) {
      region = targetRegion(form, written);
      // A box that submits its form when it changes (see below) was noted
      // as the control, so focus stays on it.
      control = event.submitter || pendingControls.get(form) || firstSubmit(form) || form;
      pendingControls.delete(form);
      // The submitter's own form owns it, even when the button sits outside
      // that form (form="…"); the event's target is that same form.
      owner = event.submitter?.form || form;
    }
    if (!region || !sameOrigin(written || "")) return;
    event.preventDefault();
    if (inFlight.has(form)) {
      // A box changed again while its request runs applies itself after.
      if (isBox(control)) heldBoxes.add(control);
      return;
    }
    // The request is built before the form is marked in flight, so nothing
    // can fail between the two and leave the form locked.
    const action = new URL(written || "", document.baseURI);
    const fields = new FormData(form, event.submitter || undefined);
    const save = Boolean(owner) && method === "post" && !owner.hasAttribute("data-in-place-read");
    let init, load;
    if (method === "post") {
      // The same body the browser would send, submitter included: multipart
      // for a file upload, urlencoded otherwise.
      const enctype = (event.submitter?.getAttribute("formenctype")
        ?? form.getAttribute("enctype") ?? "").toLowerCase();
      const body = enctype === "multipart/form-data" ? fields : new URLSearchParams(fields);
      init = {method: "POST", body};
      // A read-only table POST that got no answer is submitted natively; a
      // saving POST never is (refreshTable shows a note instead).
      load = () => HTMLFormElement.prototype.submit.call(form);
    } else {
      action.search = new URLSearchParams(fields).toString();
      init = {method: "GET"};
      // A data-in-place GET falls back to the very URL it fetched, which
      // keeps a submitter's formaction (a native re-submission would not).
      load = owner ? () => window.location.assign(action.href)
        : () => HTMLFormElement.prototype.submit.call(form);
    }
    inFlight.add(form);
    const submitter = event.submitter;
    submitter?.setAttribute("aria-disabled", "true");
    // A save shows its button busy, as an ordinary submission does.
    if (save && !saving) submitter?.classList.add("is-busy");
    const settle = () => {
      inFlight.delete(form);
      submitter?.removeAttribute("aria-disabled");
      submitter?.classList.remove("is-busy");
      reconcileBoxes();
    };
    refreshTable(region, control, action.href, init, load, {owner, form: save ? form : null})
      .finally(settle);
  }, true);
  // A checkbox marked data-submit-on-change applies at once (Automation
  // access's "Include ended sessions", #621): it submits its own
  // form[data-in-place], which the handler above sends in place, and is
  // noted as the control so focus stays on it. The Admin portal requires
  // script, so such a form has no Apply button.
  document.addEventListener("change", (event) => {
    const box = event.target;
    if (!isBox(box)) return;
    // The reader's own change may retry a state that failed before.
    failedState.delete(box);
    pendingControls.set(box.form, box);
    box.form.requestSubmit();
  });

  // Ordinary form submissions: show at once that the click registered, and
  // ignore repeats (double clicks, Enter pressed twice) until the browser
  // navigates. The listener is on document, so it runs after each form's own
  // handlers; a submit that page script already took over (defaultPrevented:
  // fetch-driven forms, cancelled confirmations) is left alone. Buttons get
  // aria-disabled, never disabled: a disabled submitter's name/value would be
  // dropped from the request (action=start, page=2, ...). A download or a new
  // tab leaves this page in place, so the form is released after a while, and
  // also when the browser shows this page again from its back/forward cache.
  const busyStatus = document.createElement("span");
  busyStatus.className = "visually-hidden";
  busyStatus.setAttribute("role", "status");
  (document.querySelector("main") || document.body).append(busyStatus);
  const submitting = new Map();
  const submitControls = (form) => [...form.elements].filter((node) =>
    (node instanceof HTMLButtonElement || node instanceof HTMLInputElement)
    && node.type === "submit");
  // Forms marked data-submit-waits (export queueing) can legitimately wait
  // on the server for a minute or two, e.g. behind a ParishSoft refresh's
  // final step. They stay busy much longer and say so visibly, instead of
  // silently re-enabling after ten seconds, which looked like nothing happened.
  const waitNotes = new Map();
  const slowTimers = new Map();
  const release = (form) => {
    window.clearTimeout(submitting.get(form));
    submitting.delete(form);
    // A form released early (page restored from the back/forward cache) must
    // not later show a stale "Still working" note.
    window.clearTimeout(slowTimers.get(form));
    slowTimers.delete(form);
    form.removeAttribute("aria-busy");
    submitControls(form).forEach((node) => {
      node.classList.remove("is-busy");
      node.removeAttribute("aria-disabled");
    });
    if (!submitting.size) busyStatus.textContent = "";
  };
  const waitNote = (form, submitter, text) => {
    let note = waitNotes.get(form);
    if (!note) {
      note = document.createElement("p");
      note.className = "help submit-wait";
      note.setAttribute("role", "status");
      (submitter || form).insertAdjacentElement("afterend", note);
      waitNotes.set(form, note);
    }
    note.textContent = text;
  };
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (submitting.has(form)) { event.preventDefault(); return; }
    if (event.defaultPrevented || form.hasAttribute("data-submit-repeatable")) return;
    form.setAttribute("aria-busy", "true");
    submitControls(form).forEach((node) => node.setAttribute("aria-disabled", "true"));
    event.submitter?.classList.add("is-busy");
    busyStatus.textContent = "Working…";
    if (!form.hasAttribute("data-submit-waits")) {
      submitting.set(form, window.setTimeout(() => release(form), 10000));
      return;
    }
    const submitter = event.submitter;
    slowTimers.set(form, window.setTimeout(() => waitNote(form, submitter,
      "Still working… If a ParishSoft refresh is finishing, this can take up to two minutes. Keep this page open."), 5000));
    submitting.set(form, window.setTimeout(() => {
      release(form);
      waitNote(form, submitter,
        "No response from the server yet. Your export may still have been queued; check Background work, or try again.");
    }, 180000));
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    [...submitting.keys()].forEach(release);
    waitNotes.forEach((note) => note.remove());
    waitNotes.clear();
    // A page restored from the back/forward cache still carries the one-time
    // request_key it was rendered with. Reusing it for a different export
    // (another format) would be refused as an already-bound request, so give
    // each restored form a fresh key; a genuine retry of the same submission
    // still reuses the key it was sent with.
    document.querySelectorAll('input[type="hidden"][name="request_key"]').forEach((input) => {
      if (window.crypto?.randomUUID) input.value = window.crypto.randomUUID();
    });
  });

  // Required acknowledgments: a form's submit buttons stay disabled (muted by
  // button:disabled) until every visible [data-acknowledgment] checkbox that
  // belongs to it is checked. A hidden one (a setup test's "may have arrived"
  // prompt before any doubt) does not count. This is progressive enhancement
  // only: without JavaScript the buttons stay enabled and the server refuses a
  // missing acknowledgment exactly as before. Real disabled is used, unlike
  // the busy state above, because no submission should start at all; only
  // buttons disabled here (data-acknowledgment-gated) are ever re-enabled, so
  // a button the server or another script disabled (a pending test, setup
  // that is not ready) stays disabled. formnovalidate buttons are never gated.
  const gateAcknowledgments = (form) => {
    if (!form) return;
    const boxes = [...form.elements].filter((node) => node instanceof HTMLInputElement
      && node.type === "checkbox" && node.hasAttribute("data-acknowledgment"));
    if (!boxes.length) return;
    const blocked = boxes.some((box) => !box.checked && !box.closest("[hidden]"));
    submitControls(form).forEach((node) => {
      if (node.formNoValidate) return;
      if (blocked && !node.disabled) {
        node.disabled = true;
        node.setAttribute("data-acknowledgment-gated", "");
      } else if (!blocked && node.hasAttribute("data-acknowledgment-gated")) {
        node.disabled = false;
        node.removeAttribute("data-acknowledgment-gated");
      }
    });
  };
  document.querySelectorAll("form").forEach((form) => {
    gateAcknowledgments(form);
    form.addEventListener("change", () => gateAcknowledgments(form));
  });
  // Back/forward navigation can restore checkbox state without a change event.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form").forEach(gateAcknowledgments);
  });

  // Readiness status is a passive GET, never the source-load idle-renewal
  // exception. No message content, key or answer is retained by this poller.
  document.querySelectorAll("[data-setup-mail]").forEach((panel) => {
    const button = panel.querySelector("[data-mail-send]");
    const warning = panel.querySelector("[data-mail-status-error]");
    const uncertain = panel.querySelector("[data-mail-uncertain]");
    const acknowledgement = uncertain?.querySelector("input");
    // Until this revision's test is accepted the send button is the page's
    // primary action and Continue waits hidden (setup-test-actions.html).
    const onward = panel.querySelector("[data-mail-continue]");
    const states = new Set([
      "queued", "submitting", "accepted", "not_sent", "delivery_unknown", "cancelled"
    ]);
    if (!button || !warning || !uncertain || !acknowledgement) return;
    acknowledgement.required = !uncertain.hidden;
    let pending = panel.dataset.mailPending === "true";
    let inFlight = false;
    let stopped = false;
    let activeRequest = null;
    async function refresh() {
      if (!pending || inFlight || stopped || document.hidden) return;
      inFlight = true;
      const controller = new AbortController();
      activeRequest = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panel.dataset.mailStatusUrl, {
          method: "GET", credentials: "same-origin", cache: "no-store",
          headers: {"Accept": "application/json"}, signal: controller.signal
        });
        if (!response.ok) throw new Error("status unavailable");
        const data = await response.json();
        if (!Number.isSafeInteger(data.revision) ||
            String(data.revision) !== panel.dataset.mailRevision ||
            typeof data.pending !== "boolean" || typeof data.unknown !== "boolean" ||
            !Array.isArray(data.items) || data.items.length > 25) {
          throw new Error("status changed");
        }
        const nodes = new Map([...panel.querySelectorAll("[data-mail-id]")]
          .map((node) => [node.dataset.mailId, node.querySelector("[data-mail-state]")]));
        for (const item of data.items) {
          if (!states.has(item.state) || typeof item.label !== "string" ||
              item.label.length > 512 || !nodes.get(item.id)) {
            throw new Error("status changed");
          }
        }
        for (const item of data.items) nodes.get(item.id).textContent = item.label;
        if (onward?.hidden && data.items.some((item) =>
          item.current === true && item.state === "accepted")) {
          onward.hidden = false;
          button.hidden = true;
        }
        pending = data.pending;
        // This poller owns the send button's pending state; take it back from
        // the acknowledgment gate, then let the gate apply the new prompt.
        button.removeAttribute("data-acknowledgment-gated");
        button.disabled = pending;
        uncertain.hidden = !data.unknown;
        acknowledgement.required = data.unknown;
        gateAcknowledgments(acknowledgement.form);
      } catch {
        stopped = true;
        button.removeAttribute("data-acknowledgment-gated");
        button.disabled = true;
        warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        activeRequest = null;
        inFlight = false;
      }
    }
    const timer = window.setInterval(refresh, 5000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearInterval(timer);
      activeRequest?.abort();
    });
    refresh();
  });

  // "Finishing setup" polls a passive GET status (it never renews the setup
  // login) every 15 seconds while visible. The server returns only a short
  // signature of what the page shows: a change reloads the page, and
  // completion reveals the Continue link instead of leaving the page.
  document.querySelectorAll("[data-finishing]").forEach((panel) => {
    const done = document.querySelector("[data-finishing-done]");
    const warning = panel.querySelector("[data-finishing-unavailable]");
    let stopped = false;
    let inFlight = false;
    let timer = null;
    let controller = null; // the status request in flight, aborted on pagehide
    async function refresh() {
      if (stopped || inFlight || document.hidden) return;
      inFlight = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panel.dataset.finishingUrl, {
          method: "GET", credentials: "same-origin", cache: "no-store",
          headers: {"Accept": "application/json"}, signal: controller.signal
        });
        if (!response.ok) throw new Error("status unavailable");
        const data = await response.json();
        // A reply that arrives after pagehide must not reload or reveal.
        if (stopped) return;
        if (typeof data.signature !== "string" || typeof data.overall !== "string") {
          throw new Error("status changed");
        }
        warning.hidden = true;
        if (data.overall === "completed") {
          stopped = true;
          if (done) done.hidden = false;
          panel.hidden = true;
        } else if (data.signature !== panel.dataset.finishingSignature) {
          stopped = true;
          window.location.reload();
        }
      } catch {
        if (!stopped) warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        inFlight = false;
        controller = null;
        if (!stopped) timer = window.setTimeout(refresh, 15000);
      }
    }
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !inFlight) {
        window.clearTimeout(timer);
        refresh();
      }
    });
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearTimeout(timer);
      controller?.abort();
    });
    timer = window.setTimeout(refresh, 15000);
  });

  // Only the exact source-progress page posts this renewal exception. The
  // server checks the original login, task/source leases and five-minute limit;
  // visibility and local deadlines merely stop unnecessary browser requests.
  document.querySelectorAll("[data-setup-progress]").forEach((panel) => {
    const form = panel.querySelector("form");
    const warning = panel.querySelector("[data-progress-unavailable]");
    const deadlines = [...panel.querySelectorAll("[data-progress-deadline]")];
    const number = new Intl.NumberFormat("en-US");
    const localTime = {format: (date) => window.ParishDates ?
      window.ParishDates.instant(date) : date.toISOString()};
    let active = panel.dataset.progressActive === "true";
    let closed = false, pending = false, timer = null, controller = null;
    // Anchor server instants to monotonic elapsed time, not the browser's wall
    // clock. Clock skew or a later system-clock correction must not stop renewal.
    let serverAt = Date.parse(panel.dataset.progressNow), observedAt = performance.now();
    // Liveness display: elapsed time and the worker's last report tick locally
    // between checks, so a download whose totals are not yet known (0 of 0)
    // still visibly progresses. These are display-only; nothing is posted.
    const texts = Object.fromEntries([...panel.querySelectorAll("[data-progress-text]")]
      .map(node => [node.dataset.progressText, node.textContent]));
    const summary = panel.querySelector("[data-progress-summary]");
    const bar = panel.querySelector("[data-progress-bar]");
    const elapsed = panel.querySelector("[data-progress-elapsed]");
    const quiet = panel.querySelector("[data-progress-quiet]");
    const checked = panel.querySelector("[data-progress-checked]");
    const done = document.querySelector("[data-progress-done]");
    const failed = document.querySelector("[data-progress-failed]");
    const records = panel.querySelector("[data-load-records]");
    const phaseList = panel.querySelector(".setup-load-phases");
    const collectionList = panel.querySelector("[data-load-collections]");
    const phaseOrder = ["fetching", "staging", "validating"];
    // Fill a server-rendered wording template such as "{count} loaded".
    const fill = (template, values) => template.replace(/\{(\w+)\}/g,
      (match, name) => (name in values ? number.format(values[name]) : match));
    const mark = (node, state, text) => {
      node.classList.remove("setup-load-done", "setup-load-active", "setup-load-waiting");
      node.classList.add(`setup-load-${state}`);
      const status = node.querySelector("[data-load-status]");
      if (status && status.textContent !== text) status.textContent = text;
    };
    // Mirrors setup_progress_views.phases() and collections().
    const showLoad = (data, key) => {
      const index = phaseOrder.indexOf(data.phase);
      phaseList?.querySelectorAll("[data-load-phase]").forEach((node) => {
        const position = phaseOrder.indexOf(node.dataset.loadPhase);
        const state = key === "done" || position < index ? "done"
          : position === index && data.active ? "active" : "waiting";
        mark(node, state, phaseList.dataset[`status${state[0].toUpperCase()}${state.slice(1)}`]);
      });
      if (collectionList) {
        const text = collectionList.dataset;
        const fetching = data.phase === "fetching" && data.active;
        let waiting = false;
        data.collections.forEach((item) => {
          const node = collectionList.querySelector(`[data-collection="${item.key}"]`);
          let state = "waiting";
          if (item.done) state = "done";
          else if (fetching && !waiting) { state = "active"; waiting = true; }
          const rosters = item.key === "ministry_roster" && item.expected !== null;
          mark(node, state, rosters && state !== "waiting" ? fill(text.textRosters, item)
            : state === "done" ? fill(text.textDone, item)
            : state === "active" ? text.textActive : text.textWaiting);
        });
      }
      if (records) records.hidden = !["staging", "validating"].includes(data.phase);
    };
    const validCollections = (items) => Array.isArray(items) && items.length <= 20
      && items.every((item) => item && typeof item.key === "string" && /^[a-z_]{1,40}$/.test(item.key)
        && collectionList?.querySelector(`[data-collection="${item.key}"]`)
        && Number.isSafeInteger(item.count) && item.count >= 0 && typeof item.done === "boolean"
        && [item.finished, item.expected].every((value) => value === null
          || (Number.isSafeInteger(value) && value >= 0)));
    let startedAt = Date.parse(panel.dataset.progressStarted || "");
    let heartbeatAt = Date.parse(panel.dataset.progressHeartbeat || "");
    const duration = (milliseconds) => {
      const seconds = Math.max(0, Math.floor(milliseconds / 1000));
      return seconds < 60 ? `${seconds} seconds`
        : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
    };
    // Mirrors the server's summary() choice of plain-language status text.
    const statusKey = (data) => {
      if (data.setup_state === "expired"
          || ["failed", "cancelled", "abandoned"].includes(data.task_state)) return "failed";
      if (data.task_state === "succeeded" || data.setup_state === "collecting") return "done";
      if (["queued", "retry_wait"].includes(data.task_state)) return data.task_state;
      return data.phase in texts ? data.phase : "working";
    };
    const tick = () => {
      const now = serverAt + performance.now() - observedAt;
      if (elapsed && Number.isFinite(startedAt) && active) elapsed.textContent = duration(now - startedAt);
      if (quiet && Number.isFinite(heartbeatAt) && active) quiet.textContent = `${duration(now - heartbeatAt)} ago`;
    };
    const ticker = window.setInterval(tick, 1000);
    const live = () => {
      const until = Math.min(...deadlines.map(node => Date.parse(node.dateTime)));
      const now = serverAt + performance.now() - observedAt;
      return !closed && active && Number.isFinite(until) && Number.isFinite(now) && now < until;
    };
    const schedule = () => {
      window.clearTimeout(timer);
      if (live()) timer = window.setTimeout(refresh, 15000);
    };
    async function refresh() {
      if (!live() || pending) return;
      if (document.hidden) { schedule(); return; }
      pending = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(panel.dataset.progressUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"Content-Type": "application/x-www-form-urlencoded"},
          body: new URLSearchParams(new FormData(form)), signal: controller.signal
        });
        if (!response.ok) throw new Error("unavailable");
        const data = await response.json();
        if (data.task_id !== panel.dataset.progressTask || typeof data.active !== "boolean"
            || !Number.isFinite(Date.parse(data.server_now))
            || !Number.isSafeInteger(data.current) || !Number.isSafeInteger(data.total)
            || data.current < 0 || data.total < data.current
            || !["queued", "running", "retry_wait", "abandoned", "succeeded", "failed", "cancelled"].includes(data.task_state)
            || !["collecting", "loading", "frozen", "completed", "expired"].includes(data.setup_state)
            || typeof data.phase !== "string" || !validCollections(data.collections)
            || deadlines.some(node => !Number.isFinite(Date.parse(data[node.dataset.progressDeadline])))) {
          throw new Error("unavailable");
        }
        if (closed || document.hidden) return;
        serverAt = Date.parse(data.server_now);
        observedAt = performance.now();
        active = data.active;
        panel.querySelector("[data-task-state]").textContent = data.task_state;
        panel.querySelector("[data-setup-state]").textContent = data.setup_state;
        panel.querySelector("[data-task-phase]").textContent = data.phase;
        const percentage = data.total ? Math.round(data.current * 100 / data.total) : 0;
        panel.querySelector("[data-task-counts]").textContent =
          `${number.format(data.current)} of ${number.format(data.total)} (${percentage}%)`;
        // Unknown totals show an indeterminate bar rather than a stuck 0%.
        if (bar && data.total) { bar.max = data.total; bar.value = data.current; }
        else if (bar) bar.removeAttribute("value");
        if (typeof data.started_at === "string" && Number.isFinite(Date.parse(data.started_at))) {
          startedAt = Date.parse(data.started_at);
        }
        if (typeof data.heartbeat_at === "string" && Number.isFinite(Date.parse(data.heartbeat_at))) {
          heartbeatAt = Date.parse(data.heartbeat_at);
        }
        const key = statusKey(data);
        if (summary && texts[key] && summary.textContent !== texts[key]) summary.textContent = texts[key];
        showLoad(data, key);
        if (done) done.hidden = key !== "done";
        if (failed) failed.hidden = key !== "failed";
        if (checked) {
          checked.dateTime = data.server_now;
          checked.textContent = localTime.format(new Date(serverAt));
        }
        tick();
        deadlines.forEach(node => {
          node.dateTime = data[node.dataset.progressDeadline];
          node.textContent = localTime.format(new Date(node.dateTime));
        });
        warning.hidden = true;
      } catch (_) {
        if (!closed && !document.hidden) warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        pending = false;
        controller = null;
        schedule();
      }
    }
    form.addEventListener("submit", event => {
      // A stopped automatic poller must not disable the normal manual form.
      if (live()) { event.preventDefault(); refresh(); }
    });
    window.addEventListener("pagehide", () => {
      closed = true;
      window.clearTimeout(timer);
      window.clearInterval(ticker);
      if (controller) controller.abort();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && controller) controller.abort();
      if (!document.hidden) refresh();
    });
    tick();
    refresh();
  });

  // A control's value for the rules below (data-show-when and
  // data-required-when). A checkbox counts as its value only while it is
  // ticked and is empty otherwise, as a submission would send it:
  // "followed_up!=yes" means "not ticked". A radio group already reads as
  // its ticked button's value (RadioNodeList.value).
  const ruleValue = (control) => (control instanceof HTMLInputElement
    && control.type === "checkbox"
    ? (control.checked ? control.value : "") : control.value);

  // Complete-before-submit (#553): a form marked data-require-complete keeps
  // its submit buttons disabled until every control that is currently shown
  // and required is valid, and says why in its [data-complete-hint] element
  // (which the buttons name with aria-describedby). Conditional requirements
  // are applied only here, never in the markup, so without JavaScript the
  // buttons stay enabled and the server's own validation answers as before:
  //   - data-required-when-shown="name …" on a data-show-when group (or an
  //     empty value on a field) makes those controls required while shown;
  //   - data-required-when="name=value" makes a field required while the
  //     form's control "name" has that value (notes for the outcome Other);
  //   - data-require-one on a group of checkboxes needs at least one ticked
  //     (the System logs Show choices, #601): while none is, each box is
  //     marked invalid with the group's data-missing-hint.
  // The hint is the first missing control's data-missing-hint, or that of
  // the nearest element around it. As with acknowledgments below, real
  // disabled is used and only buttons this gate disabled are re-enabled.
  const requiredWhen = (form) => {
    form.querySelectorAll("[data-required-when]").forEach((node) => {
      const [name, value] = node.dataset.requiredWhen.split("=");
      const control = form.elements.namedItem(name);
      // A hidden (disabled) control's leftover value does not count.
      node.required = Boolean(control) && !control.disabled && ruleValue(control) === value;
    });
  };
  const requireOne = (form) => {
    form.querySelectorAll("[data-require-one]").forEach((group) => {
      const boxes = [...group.querySelectorAll("input[type=checkbox]")];
      const none = !boxes.some((box) => box.checked);
      // The group's own data-missing-hint is the message (and the custom
      // validity a box needs to count as invalid); the page supplies it.
      const hint = none ? group.dataset.missingHint : "";
      boxes.forEach((box) => box.setCustomValidity(hint));
    });
  };
  const gateComplete = (form) => {
    if (!form || !form.hasAttribute("data-require-complete")) return;
    requiredWhen(form);
    requireOne(form);
    // A required text field holding only spaces is still empty: the server
    // trims it (notes for the outcome Other).
    const blank = (node) => node.required && node.matches("textarea, input[type=text]")
      && !node.value.trim();
    // A live check (the follow-up contact time) marks a field it found in
    // error with data-client-error, whose text is the hint.
    const missing = [...form.elements].find((node) => node.willValidate
      && !node.closest("[hidden]")
      && (node.hasAttribute("data-client-error") || !node.validity.valid || blank(node)));
    const hint = form.querySelector("[data-complete-hint]");
    if (hint) {
      hint.hidden = !missing;
      hint.textContent = missing
        ? (missing.dataset.clientError
          || missing.closest("[data-missing-hint]")?.dataset.missingHint
          || "Fill in the required fields to save.")
        : "";
    }
    submitControls(form).forEach((node) => {
      if (node.formNoValidate) return;
      if (missing && !node.disabled) {
        node.disabled = true;
        node.setAttribute("data-complete-gated", "");
      } else if (!missing && node.hasAttribute("data-complete-gated")) {
        node.disabled = false;
        node.removeAttribute("data-complete-gated");
      }
    });
  };

  // A field marked data-show-when="name=value" is shown only while the form's
  // control called "name" has that value, e.g. the daily refresh time only
  // for the once-a-day frequency; "name!=value" shows it for every other
  // value, e.g. a contact attempt's date only once a channel is chosen, or
  // the confirmation to reopen information follow-up only once "Follow-up
  // completed" is unticked. On a field the mark hides its enclosing div; on a
  // div (a group of fields, as in Ministry follow-up) it hides that div and
  // every control inside it. Hidden fields are disabled so they are not
  // sent; without JavaScript every field simply stays visible, and the
  // server ignores what does not apply. A browser can restore form values
  // without a change event (the back/forward cache, or autofill after load),
  // so every rule is applied again on pageshow, not only at load.
  //
  // Both this and the complete-before-submit gate are wired for the page and
  // again for content an in-place swap brings in (a follow-up form saved or
  // refused in place, #519), which arrives as fresh elements with no
  // listeners. The lists the pageshow re-run uses drop what a swap removed.
  // within() includes root itself, which a swapped sync node can be.
  const within = (root, selector) => [
    ...(root instanceof Element && root.matches(selector) ? [root] : []),
    ...root.querySelectorAll(selector),
  ];
  let showWhenUpdates = []; // {node, update} for each data-show-when mark
  let completeForms = []; // every form[data-require-complete] wired so far
  const wireShowWhen = (root) => {
    within(root, "[data-show-when]").forEach((node) => {
      const rule = node.dataset.showWhen;
      const negated = rule.includes("!=");
      const [name, value] = rule.split(negated ? "!=" : "=");
      const field = node.matches("input, select, textarea");
      const form = field ? node.form : node.closest("form");
      const control = form && form.elements.namedItem(name);
      const wrapper = field ? node.closest("div") : node;
      if (!control || !wrapper) return;
      const controls = field ? [node] : [...node.querySelectorAll("input, select, textarea")];
      const listed = node.dataset.requiredWhenShown;
      const required = listed === undefined ? []
        : listed === "" ? [node]
          : listed.split(/\s+/).map((item) => form.elements.namedItem(item)).filter(Boolean);
      const update = () => {
        // A control outside a swapped region outlives the marks it served.
        if (!node.isConnected) return;
        const shown = (ruleValue(control) === value) !== negated;
        wrapper.hidden = !shown;
        // A hidden field is not sent, so an error marked on it no longer
        // applies: it clears, with its message and summary item.
        if (!shown) {
          // Clearing one field also clears the fields sharing its message,
          // so each is checked again as it comes up.
          controls.forEach((item) => {
            if (item.hasAttribute("data-field-error")) clearFieldError(item);
          });
        }
        controls.forEach((item) => { item.disabled = !shown; });
        required.forEach((item) => { item.required = shown; });
        gateComplete(form);
      };
      control.addEventListener("change", update);
      showWhenUpdates.push({node, update});
      update();
    });
  };
  const wireComplete = (root) => {
    within(root, "form[data-require-complete]").forEach((form) => {
      gateComplete(form);
      form.addEventListener("input", () => gateComplete(form));
      form.addEventListener("change", () => gateComplete(form));
      // A partly typed date ("09/__/____") is invalid (badInput) but fires no
      // input or change event, so the gate also checks as focus leaves a field.
      form.addEventListener("focusout", () => gateComplete(form));
      completeForms.push(form);
    });
  };
  // A contact attempt cannot be in the future (#592). The browser checks
  // what it can at once; the server still checks every save and its
  // refusal is shown as before. The date input is marked
  // data-not-future="<time input id>", with the inline message element
  // (data-not-future-error) and the server's own words
  // (data-not-future-message). Its picker stops at today in this browser's
  // time zone: set at load, after a swap, on pageshow, and on focus, so a
  // page left open past midnight moves on.
  //
  // While the date (with no time yet: a later day) or the date and time
  // (in this browser's zone) are in the future, both fields are marked in
  // error beside the message, and the Save gate holds Save with the message
  // as its hint. The message element is the one a server refusal fills, so
  // one message shows at a time:
  //   - An edit (input or change of Date or Time) owns the element: a
  //     future time shows the check's message there, and a time no longer
  //     in the future clears it, including the server's own "in the
  //     future" refusal, which the reader has now changed.
  //   - Any other run (wiring at load or after a swap, pageshow, leaving a
  //     field, another change on the form, the timer below) never touches a
  //     server message: it only adds the marks for a future time, and
  //     clears only the check's own message. The server's refusal exists
  //     for exactly the case where this browser's clock disagrees with it
  //     (a wrong clock, a stale tab), so the browser must not overrule it
  //     unasked.
  // A time can pass while the page is open: while the check holds Save it
  // runs again once a minute, so Save comes back once the time is no longer
  // in the future.
  const localToday = () => {
    const now = new Date();
    const pad = (number) => String(number).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  };
  let notFutureChecks = []; // {date, check} for each wired date input
  const wireNotFuture = (root) => {
    within(root, "input[data-not-future]").forEach((date) => {
      const time = document.getElementById(date.dataset.notFuture);
      const errorId = date.dataset.notFutureError;
      const message = date.dataset.notFutureMessage;
      const fields = [date, time].filter(Boolean);
      let timer = null; // the once-a-minute re-check while Save is held
      // Written only when the day changes. An earlier version also ran the
      // check on Save's pointerdown, and re-setting max there, even to the
      // same value, made Chromium drop that click; the attribute is not
      // rewritten needlessly in case anything else runs the check mid-press.
      const limit = () => {
        const today = localToday();
        if (date.max !== today) date.max = today;
      };
      const describe = (node) => {
        const ids = (node.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter((id) => id && id !== errorId);
        node.setAttribute("aria-describedby", [...ids, errorId].join(" "));
      };
      const check = (edited = false) => {
        if (!date.isConnected) {
          window.clearInterval(timer);
          return;
        }
        limit();
        const error = document.getElementById(errorId);
        const source = error && !error.hidden ? error.dataset.source : null;
        // A hidden contact group's fields are disabled and not sent.
        const future = !date.disabled && Boolean(date.value) && (time && time.value
          ? new Date(`${date.value}T${time.value}`).getTime() > Date.now()
          : date.value > localToday());
        if (future) {
          fields.forEach((node) => {
            node.setAttribute("aria-invalid", "true");
            node.setAttribute("data-field-error", "");
            describe(node);
          });
          date.dataset.clientError = message;
          // The check's message replaces a server one only on an edit.
          if (error && (edited || !source || source === "client")) {
            error.hidden = false;
            error.dataset.source = "client";
            const item = error.querySelector("li") || error.appendChild(document.createElement("li"));
            item.textContent = message;
          }
          if (!timer) timer = window.setInterval(() => check(), 60000);
        } else {
          delete date.dataset.clientError;
          window.clearInterval(timer);
          timer = null;
          // Defensive: on an edit, the field-error listener (wireValidity,
          // wired before this) has already cleared a server mark, so the
          // second case is not reached today. It keeps this check right on
          // its own if that wiring order ever changes.
          if (source === "client" || (edited && source === "contact_future")) {
            clearFieldError(date);
          }
        }
        gateComplete(date.form);
      };
      fields.forEach((node) => {
        node.addEventListener("input", () => check(true));
        node.addEventListener("change", () => check(true));
        node.addEventListener("blur", () => check());
      });
      date.addEventListener("focus", limit);
      // Any other change on the form runs the check again (not as an edit):
      // choosing a contact channel shows Date and Time again, with values
      // that may still be in the future.
      date.form?.addEventListener("change", () => check());
      notFutureChecks.push({date, check});
      check();
    });
  };
  const wireConditional = (root) => {
    showWhenUpdates = showWhenUpdates.filter(({node}) => node.isConnected);
    completeForms = completeForms.filter((form) => form.isConnected);
    notFutureChecks = notFutureChecks.filter(({date}) => date.isConnected);
    wireShowWhen(root);
    wireComplete(root);
    wireNotFuture(root);
  };
  wireConditional(document);
  document.addEventListener("parishkit:swap", (event) => wireConditional(event.target));
  window.addEventListener("pageshow", () => {
    showWhenUpdates.forEach(({update}) => update());
    completeForms.forEach(gateComplete);
    notFutureChecks.forEach(({check}) => check());
  });

  // Optional modules remain ordinary accessible fieldsets without JavaScript.
  // Hidden fields are disabled, not silently copied into submitted data. The
  // server independently rejects stray data for every disabled module.
  document.querySelectorAll("[data-campaign-form]").forEach((form) => {
    form.querySelectorAll("[data-campaign-module]").forEach((group) => {
      const toggle = document.getElementById(group.dataset.campaignModule);
      if (!toggle) return;
      let initialized = toggle.checked || !form.hasAttribute("data-new-campaign");
      function update() {
        group.hidden = !toggle.checked;
        group.disabled = !toggle.checked;
        if (toggle.checked && !initialized) {
          const ministries = group.querySelector('select[name="ministry_duids"]');
          if (ministries) Array.from(ministries.options).forEach((item) => {
            item.selected = true;
          });
          initialized = true;
        }
      }
      toggle.addEventListener("change", update);
      update();
    });
  });

  // A financial period is one year: entering its start fills an empty end
  // with the day before the first anniversary (Feb 29 anniversaries fall on
  // Feb 28, as the server's rule does). Dates stay YYYY-MM-DD text and the
  // arithmetic uses UTC, so no browser time zone can shift the day. An end the
  // person typed is never replaced; one filled here follows later start edits
  // (typing a year passes through values like 0002-01-01). The server still
  // checks every period.
  const periodEnd = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    const [year, month, day] = match.slice(1).map(Number);
    const end = new Date(0);
    end.setUTCFullYear(year + 1, month - 1, month === 2 && day === 29 ? 28 : day);
    end.setUTCDate(end.getUTCDate() - 1);
    if (!Number.isFinite(end.getTime()) || end.getUTCFullYear() > 9999) return null;
    return [String(end.getUTCFullYear()).padStart(4, "0"),
      String(end.getUTCMonth() + 1).padStart(2, "0"),
      String(end.getUTCDate()).padStart(2, "0")].join("-");
  };
  document.querySelectorAll("input[data-fills-end]").forEach((start) => {
    const end = start.form?.elements[start.dataset.fillsEnd];
    if (!(end instanceof HTMLInputElement)) return;
    const note = document.createElement("span");
    note.className = "help";
    note.setAttribute("role", "status");
    end.after(note);
    const fill = () => {
      const value = periodEnd(start.value);
      if (!value || (end.value && end.value !== end.dataset.autofilled)) return;
      if (end.value === value) return;
      end.value = value;
      end.dataset.autofilled = value;
      note.textContent = "End date filled in; change it if needed.";
      // Let dependent checks (the overlap confirmation) see the new end.
      end.dispatchEvent(new Event("input", {bubbles: true}));
    };
    start.addEventListener("input", fill);
    start.addEventListener("change", fill);
    end.addEventListener("input", (event) => {
      if (event.isTrusted) note.textContent = "";
    });
  });

  // The overlap confirmation is needed only while the financial period and
  // the campaign share at least one day (campaign_forms.overlaps). Hidden, it
  // is also unchecked so a stale confirmation is never submitted. The server
  // renders it visible whenever it is needed, so this is only a convenience.
  document.querySelectorAll("[data-overlap-confirmation]").forEach((group) => {
    const form = group.closest("form");
    const box = group.querySelector('input[type="checkbox"]');
    if (!form || !box) return;
    const read = (key) => {
      const name = group.dataset[`${key}Name`];
      const value = name ? form.elements[name]?.value : group.dataset[`${key}Value`];
      return /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value : null;
    };
    const update = () => {
      const [start, end, periodStart, periodEnd] =
        ["campaignStart", "campaignEnd", "periodStart", "periodEnd"].map(read);
      const needed = Boolean(start && end && periodStart && periodEnd
        && periodStart <= end && periodEnd >= start);
      group.hidden = !needed;
      if (!needed) box.checked = false;
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });

  // Mail schedule rows: show only the fields the chosen mail type uses (the
  // row's data-schedule-fields is schedule_forms.FIELDS), and offer only
  // emails of that type. A field hidden here is also cleared, so a stale date
  // or weekday is never submitted. When the page first loads, a field the
  // server reported an error on stays visible with its value, so the message
  // can be read and acted on. Without this script every field shows and the
  // server still explains any value that does not apply.
  const scheduleRow = (row) => {
    let rules;
    try { rules = JSON.parse(row.dataset.scheduleFields); } catch { return; }
    const kind = row.querySelector('[data-schedule-field="kind"] select');
    const template = row.querySelector('[data-schedule-field="template_version"] select');
    if (!kind || !template || !rules) return;
    const governed = new Set(Object.values(rules).flat());
    const emails = [...template.options];
    const update = (firstLoad) => {
      const wanted = rules[kind.value] || [];
      row.querySelectorAll("[data-schedule-field]").forEach((group) => {
        if (!governed.has(group.dataset.scheduleField)) return;
        const show = wanted.includes(group.dataset.scheduleField)
          || (firstLoad && group.querySelector(".errorlist") !== null);
        group.hidden = !show;
        if (!show) group.querySelectorAll("input, select").forEach((control) => {
          control.value = "";
        });
      });
      const selected = template.value;
      template.replaceChildren(...emails.filter((option) =>
        !option.value || !option.dataset.kind || option.dataset.kind === kind.value));
      template.value = [...template.options].some((option) => option.value === selected)
        ? selected : "";
    };
    kind.addEventListener("change", () => update(false));
    update(true);
  };
  document.querySelectorAll("[data-schedule-row]").forEach(scheduleRow);

  // "Add another schedule" clones the formset's empty form (rendered in a
  // <template> with __prefix__ names) as the next index and raises
  // TOTAL_FORMS, so several new schedules save in one submission and the
  // server validates them all together as before. Rows added here can be
  // removed again before saving; later added rows are renumbered so the
  // indexes stay contiguous. Without this script the button stays hidden and
  // each save offers one blank row.
  document.querySelectorAll("[data-schedule-template]").forEach((template) => {
    const form = template.closest("form");
    const total = form?.querySelector('input[name="schedules-TOTAL_FORMS"]');
    const maximum = Number(form?.querySelector('input[name="schedules-MAX_NUM_FORMS"]')?.value);
    const rows = form?.querySelector("[data-schedule-rows]");
    const addRow = form?.querySelector("[data-schedule-add-row]");
    const add = addRow?.querySelector("[data-schedule-add]");
    const status = form?.querySelector("[data-schedule-status]");
    if (!total || !rows || !add || !status) return;
    const first = Number(total.value); // The server's rows keep their indexes.
    const added = [];
    const renumber = (row, index) => {
      // aria-controls: field toggletips point at their bubble's id.
      row.querySelectorAll("[name], [id], [for], [aria-describedby], [aria-controls]").forEach((node) => {
        ["name", "id", "for", "aria-describedby", "aria-controls"].forEach((attribute) => {
          const value = node.getAttribute(attribute);
          if (value) node.setAttribute(attribute,
            value.replace(/schedules-(?:\d+|__prefix__)-/g, `schedules-${index}-`));
        });
      });
      row.querySelector("[data-schedule-number]").textContent =
        (index + 1).toLocaleString("en-US");
    };
    const refresh = () => {
      added.forEach((row, offset) => renumber(row, first + offset));
      total.value = String(first + added.length);
      add.disabled = Number.isFinite(maximum) && first + added.length >= maximum;
    };
    add.addEventListener("click", () => {
      const row = template.content.firstElementChild.cloneNode(true);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button-secondary";
      remove.textContent = "Remove this new schedule";
      remove.addEventListener("click", () => {
        added.splice(added.indexOf(row), 1);
        cancelTimeChecks(row);
        row.remove();
        refresh();
        gateTimes(form);
        status.textContent = "New schedule removed.";
        add.focus();
      });
      row.append(remove);
      rows.append(row);
      added.push(row);
      refresh();
      scheduleRow(row);
      wireTimeEntry(row);
      status.textContent = `Schedule ${row.querySelector("[data-schedule-number]")
        .textContent} added. Choose its mail type.`;
      row.querySelector('[data-schedule-field="kind"] select')?.focus();
    });
    addRow.hidden = false;
  });

  // Plain multi-select lists: say how many items are chosen, since a long list
  // (a parish may have hundreds of Ministries) hides most of its selection.
  document.querySelectorAll("select[multiple]").forEach((select) => {
    const count = document.createElement("p");
    count.className = "help";
    count.setAttribute("aria-live", "polite");
    select.after(count);
    const show = () => {
      const chosen = select.selectedOptions.length;
      count.textContent = `${chosen.toLocaleString("en-US")} of ${
        select.options.length.toLocaleString("en-US")} selected`;
    };
    // Also on any form change: turning on a module preselects its options
    // without a change event on the list itself.
    (select.form || select).addEventListener("change", show);
    show();
  });

  // Editable regions write one <div> per line in Chrome/WebKit (and a bare
  // first line), <b>/<i> for bold/italic, and trailing <br> placeholders.
  // The server allowlist has no <div>, so without this the lines would reach
  // the sanitizer as one run-on paragraph. Normalize a detached copy (never
  // the live editor, whose caret would jump) into <p> paragraphs: loose
  // top-level text becomes a paragraph, two or more <br> in a row start a new
  // paragraph, and blank lines are dropped (paragraph spacing replaces them).
  // The server applies the same div-to-paragraph rule to pasted or older
  // markup; this only makes the stored source match what the editor showed.
  const inlineContent = (node) => node.nodeType === Node.TEXT_NODE
    || (node.nodeType === Node.ELEMENT_NODE
      && !["P", "H2", "H3", "UL", "OL", "BLOCKQUOTE", "DIV"].includes(node.nodeName));
  const blank = (nodes) => nodes.every((node) => node.nodeName === "BR"
    || (node.nodeType === Node.TEXT_NODE && !node.data.replace(/ /g, " ").trim()));
  const retag = (node, tag) => {
    const replacement = document.createElement(tag);
    replacement.append(...node.childNodes);
    node.replaceWith(replacement);
    return replacement;
  };
  const paragraphs = (nodes) => {
    // Split one line run at each group of 2+ <br>, trimming edge breaks.
    const result = [];
    let current = [];
    let breaks = [];
    const close = () => {
      if (!blank(current)) {
        const paragraph = document.createElement("p");
        paragraph.append(...current);
        result.push(paragraph);
      }
      current = [];
    };
    for (const node of nodes) {
      if (node.nodeName === "BR") { breaks.push(node); continue; }
      if (node.nodeType === Node.TEXT_NODE && !node.data.trim() && breaks.length) continue;
      if (breaks.length >= 2) close();
      else if (breaks.length && current.length) current.push(...breaks);
      breaks = [];
      current.push(node);
    }
    close();
    return result;
  };
  const normalizedSource = (editor) => {
    const copy = editor.cloneNode(true);
    copy.querySelectorAll("b").forEach((node) => retag(node, "strong"));
    copy.querySelectorAll("i").forEach((node) => retag(node, "em"));
    // Innermost first, so a line <div> inside a wrapper <div> is seen first.
    [...copy.querySelectorAll("div")].reverse().forEach((node) => {
      if ([...node.children].some((child) => !inlineContent(child))) {
        node.replaceWith(...node.childNodes);
      } else {
        retag(node, "p");
      }
    });
    // A <p> holding a block (a list inserted mid-paragraph) is split around it.
    const flow = (nodes) => {
      const blocks = [];
      let run = [];
      for (const node of nodes) {
        if (inlineContent(node)) { run.push(node); continue; }
        blocks.push(...paragraphs(run));
        run = [];
        if (node.nodeName === "P") blocks.push(...flow([...node.childNodes]));
        else blocks.push(node);
      }
      blocks.push(...paragraphs(run));
      return blocks;
    };
    copy.replaceChildren(...flow([...copy.childNodes]));
    return copy.innerHTML;
  };

  // Redraw the visual pane from the server's sanitizer while the HTML source
  // is edited. The pane stays visible but dims and stops accepting edits until
  // the newest request answers (a stale pane edited now would overwrite the
  // source). The one response also carries the generated plain text, relayed
  // to the plain-text preview as "stewardship:source-preview", and a
  // plain-language list of removed markup, shown as text (never as markup).
  function liveSource(form, visual, editor, source) {
    const url = visual.dataset.previewUrl;
    const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
    const updating = visual.querySelector("[data-visual-updating]");
    const unavailable = visual.querySelector("[data-visual-unavailable]");
    const removedNotice = visual.querySelector("[data-visual-removed]");
    const removedList = visual.querySelector("[data-visual-removed-list]");
    if (!url || !csrf || !updating || !unavailable || !removedNotice || !removedList) {
      // Without the live preview, fall back to the safe old behavior.
      source.addEventListener("input", () => { visual.hidden = true; });
      return;
    }
    let timer = null;
    let controller = null;
    const tools = [...form.querySelectorAll("[data-content-tag]")];
    // A stale pane accepts neither typing nor formatting: either would call
    // sync() and overwrite newer source with the stale pane's content.
    const busy = (value) => {
      visual.setAttribute("aria-busy", String(value));
      editor.contentEditable = value ? "false" : "true";
      tools.forEach((button) => { button.disabled = value; });
      updating.hidden = !value;
    };
    const refresh = async () => {
      controller?.abort();
      controller = new AbortController();
      const request = controller;
      const posted = source.value;
      // A response applies only while it still describes the current source:
      // newer typing aborts this request and schedules another.
      const current = () => controller === request && source.value === posted;
      try {
        const response = await fetch(url, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"},
          body: new URLSearchParams({html: posted}), signal: request.signal
        });
        if (!response.ok) throw new Error("preview unavailable");
        const data = await response.json();
        if (typeof data.html !== "string" || typeof data.text !== "string"
            || !Array.isArray(data.removed)) throw new Error("preview unavailable");
        if (!current()) return;
        // Server-sanitized markup only: the same allowlist the stored content
        // passed, so this is as safe as the page's initial render.
        editor.innerHTML = data.html;
        removedList.textContent = data.removed.filter((item) => typeof item === "string").join("; ");
        removedNotice.hidden = !data.removed.length;
        unavailable.hidden = true;
        busy(false);
        form.dispatchEvent(new CustomEvent("stewardship:source-preview", {detail: {text: data.text}}));
      } catch (error) {
        if (error.name === "AbortError" || !current()) return;
        // Keep the pane read-only: its content no longer matches the source.
        updating.hidden = true;
        unavailable.hidden = false;
        form.dispatchEvent(new CustomEvent("stewardship:source-preview", {detail: {text: null}}));
      }
    };
    source.addEventListener("input", () => {
      busy(true);
      unavailable.hidden = true;
      // Invalidate any request in flight now, not when the next one starts.
      controller?.abort();
      controller = null;
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 800);
    });
    // The visual editor marks its container as live for the plain-text panel.
    visual.dataset.livePreview = "true";
  }

  // The visual editor only ever shows server-sanitized markup. It starts from
  // the stored (sanitized) content; while the Admin edits the HTML source, the
  // source is posted to the server's sanitizer after a pause and the pane is
  // redrawn from the sanitized result, never from the raw source. Paste/drop
  // into the pane are plain text.
  document.querySelectorAll("[data-content-form]").forEach((form) => {
    const visual = form.querySelector("[data-visual-content]");
    const editor = form.querySelector("[data-content-editor]");
    const source = form.querySelector('textarea[name="html"]');
    if (!visual || !editor || !source) return;
    visual.hidden = false;
    form.querySelector("[data-html-source]").open = false;
    // Enter starts a <p> rather than a <div> where the browser supports it.
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch { /* optional */ }
    const sync = () => {
      source.value = normalizedSource(editor);
      // Programmatic value changes fire no input event; tell the plain-text
      // preview below that the HTML changed.
      form.dispatchEvent(new Event("stewardship:html-changed"));
    };
    editor.addEventListener("input", sync);
    // Shift+Enter is a line break (<br>) inside the paragraph everywhere.
    // Chrome, Firefox and Linux WebKit do that natively, but macOS WebKit
    // (and presumably Safari) maps Shift+Return to the same "insert newline"
    // command as Return and starts a new paragraph, so ask for the line break
    // explicitly (#544). An input method's commit keydown (isComposing, or
    // keyCode 229 in Safari) belongs to the IME, and the browser's own
    // behaviour stays whenever the command is unsupported or fails.
    editor.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !event.shiftKey
          || event.isComposing || event.keyCode === 229
          || event.altKey || event.ctrlKey || event.metaKey) return;
      if (document.execCommand("insertLineBreak")) event.preventDefault();
    });
    liveSource(form, visual, editor, source);
    const selectedRange = () => {
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) return null;
      const range = selection.getRangeAt(0);
      return editor.contains(range.commonAncestorContainer) ? range : null;
    };
    form.querySelectorAll("[data-content-tag]").forEach((button) => {
      let saved = null;
      button.addEventListener("pointerdown", (event) => {
        saved = selectedRange();
        event.preventDefault(); // Keep the selected text when clicking a tool.
      });
      button.addEventListener("click", () => {
        if (editor.getAttribute("contenteditable") === "false") return;
        const tag = button.dataset.contentTag;
        if (!["strong", "em", "p", "h2", "ul"].includes(tag)) return;
        const range = saved || selectedRange();
        saved = null;
        if (!range || !editor.contains(range.commonAncestorContainer)) return;
        const node = document.createElement(tag);
        const target = tag === "ul" ? node.appendChild(document.createElement("li")) : node;
        target.appendChild(range.extractContents());
        if (!target.hasChildNodes()) target.appendChild(document.createElement("br"));
        range.insertNode(node);
        // A new block never nests inside the paragraph or heading it was made
        // in: split that line around it and drop any half left empty.
        const host = node.parentElement;
        if (tag !== "strong" && tag !== "em" && host !== editor
            && host.parentElement === editor && /^(P|H2|H3|DIV)$/.test(host.nodeName)) {
          const tail = host.cloneNode(false);
          while (node.nextSibling) tail.append(node.nextSibling);
          host.after(node);
          node.after(tail);
          [host, tail].forEach((part) => { if (blank([...part.childNodes])) part.remove(); });
        }
        const selection = window.getSelection();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
        editor.focus();
        sync();
      });
    });
    editor.addEventListener("paste", (event) => {
      event.preventDefault();
      if (editor.getAttribute("contenteditable") === "false") return;
      const range = selectedRange();
      if (!range || !event.clipboardData) return;
      range.deleteContents();
      // Keep the pasted text's lines: each line break becomes a <br> (a blank
      // line, two of them, becomes a paragraph break when the source is
      // normalized). Only text nodes and <br> are created, never parsed markup.
      const fragment = document.createDocumentFragment();
      event.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n").split("\n")
        .forEach((line, index) => {
          if (index) fragment.append(document.createElement("br"));
          if (line) fragment.append(document.createTextNode(line));
        });
      const last = fragment.lastChild;
      if (!last) return;
      range.insertNode(fragment);
      range.setStartAfter(last);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      sync();
    });
    editor.addEventListener("drop", (event) => { event.preventDefault(); });
    editor.addEventListener("click", (event) => {
      if (event.target.closest("a")) event.preventDefault();
    });
  });

  // "Generate plain text from HTML": while it is checked the plain-text field
  // is read-only and shows the server's own generated text for the current
  // HTML, so the Admin sees exactly what a save stores. "Edit plain text" (or
  // unchecking the box) keeps that text and makes it editable; typed text is
  // never replaced unless the Admin checks the box again. A checked box
  // submits no plain text (the field is disabled just for the submission), so
  // a slightly stale preview cannot conflict with the server's generation;
  // without this script the server refuses typed text that would be dropped.
  document.querySelectorAll("[data-plain-text]").forEach((panel) => {
    const form = panel.closest("form");
    const box = panel.querySelector('input[name="generate_text"]');
    const text = panel.querySelector('textarea[name="text"]');
    const source = form?.querySelector('textarea[name="html"]');
    const note = panel.querySelector("[data-generated-note]");
    const unavailable = panel.querySelector("[data-generated-unavailable]");
    const csrf = form?.querySelector('input[name="csrfmiddlewaretoken"]');
    if (!form || !box || !text || !source || !note || !unavailable || !csrf) return;
    let timer = null;
    let controller = null;
    const refresh = async () => {
      if (!box.checked) return;
      controller?.abort();
      controller = new AbortController();
      const request = controller;
      // Like the visual preview, a response applies only to the source it
      // was generated from, so an older answer never overwrites a newer one.
      const posted = source.value;
      try {
        const response = await fetch(panel.dataset.plainTextUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"},
          body: new URLSearchParams({html: posted}), signal: request.signal
        });
        if (!response.ok) throw new Error("preview unavailable");
        const data = await response.json();
        if (typeof data.text !== "string") throw new Error("preview unavailable");
        if (box.checked && controller === request && source.value === posted) {
          text.value = data.text;
          unavailable.hidden = true;
        }
      } catch (error) {
        if (error.name !== "AbortError") unavailable.hidden = !box.checked;
      }
    };
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 400);
    };
    const show = () => {
      text.readOnly = box.checked;
      note.hidden = !box.checked;
      if (!box.checked) unavailable.hidden = true;
    };
    const edit = () => {
      box.checked = false;
      show();
      text.focus();
    };
    box.addEventListener("change", () => { show(); if (box.checked) refresh(); });
    panel.querySelector("[data-edit-plain-text]")?.addEventListener("click", edit);
    // Typing into the read-only generated text means "let me edit it".
    text.addEventListener("keydown", (event) => {
      if (!box.checked || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key.length === 1 || ["Backspace", "Delete", "Enter"].includes(event.key)) {
        edit();
      }
    });
    // With a live visual editor, source edits reach the server once, through
    // the visual preview's request, which relays the generated text here.
    if (form.querySelector('[data-visual-content][data-live-preview="true"]')) {
      form.addEventListener("stewardship:source-preview", (event) => {
        if (!box.checked) return;
        // The shared answer is newer than any plain-text request in flight.
        controller?.abort();
        if (typeof event.detail?.text === "string") {
          text.value = event.detail.text;
          unavailable.hidden = true;
        } else {
          unavailable.hidden = false;
        }
      });
    } else {
      source.addEventListener("input", schedule);
    }
    form.addEventListener("stewardship:html-changed", schedule);
    form.addEventListener("submit", () => { if (box.checked) text.disabled = true; });
    window.addEventListener("pageshow", () => { text.disabled = false; });
    show();
    refresh();
  });

  // Header counters stop polling for good once the Admin session has ended
  // (401/403): repeating a refused request every 30 seconds only fills the
  // logs, and the inactivity dialog (session-v1.js) tells the Admin to sign in.
  const headerPolls = [];
  function stopHeaderPolls() {
    while (headerPolls.length) window.clearInterval(headerPolls.pop());
  }
  function signedOut(response) {
    return response.status === 401 || response.status === 403;
  }

  // Presence is observational: these requests never count as user activity.
  // Timers skip hidden tabs and never overlap requests or catch up missed ticks.
  const presenceIndicator = document.querySelector("[data-presence-indicator]");
  let presencePending = false;
  async function refreshPresence() {
    if (document.hidden || presencePending || !presenceIndicator) return;
    presencePending = true;
    const unavailable = document.querySelector("[data-presence-unavailable]");
    try {
      const response = await fetch("/admin/presence?format=count", {
        credentials: "same-origin", cache: "no-store"
      });
      if (signedOut(response)) { stopHeaderPolls(); throw new Error("Signed out"); }
      if (!response.ok) throw new Error("Presence unavailable");
      const result = await response.json();
      if (!Number.isSafeInteger(result.count) || result.count < 0) throw new Error("Invalid count");
      presenceIndicator.querySelector("[data-presence-count]").textContent = result.count.toLocaleString("en-US");
      if (unavailable) unavailable.hidden = true;
    } catch {
      if (unavailable) unavailable.hidden = false;
    } finally { presencePending = false; }
  }
  if (presenceIndicator) {
    refreshPresence();
    headerPolls.push(window.setInterval(refreshPresence, 30000));
  }

  const backgroundIndicator = document.querySelector("[data-background-indicator]");
  let backgroundPending = false;
  async function refreshBackground() {
    if (document.hidden || backgroundPending || !backgroundIndicator) return;
    backgroundPending = true;
    const unavailable = document.querySelector("[data-background-unavailable]");
    try {
      const response = await fetch("/admin/background/counts", {
        credentials: "same-origin", cache: "no-store"
      });
      if (signedOut(response)) { stopHeaderPolls(); throw new Error("Signed out"); }
      if (!response.ok) throw new Error("Background work unavailable");
      const result = await response.json();
      const values = ["queued", "running", "retry_wait", "abandoned", "active"].map(
        (key) => result.counts[key]);
      if (values.some((value) => !Number.isSafeInteger(value) || value < 0)) throw new Error("Invalid counts");
      const total = values.slice(0, 4).reduce((sum, value) => sum + value, 0);
      if (!Number.isSafeInteger(total)) throw new Error("Invalid total");
      const deliveryWarning = document.querySelector("[data-delivery-warning]");
      if (deliveryWarning && (!Number.isSafeInteger(result.delivery_unknown) || result.delivery_unknown < 0)) throw new Error("Invalid delivery count");
      backgroundIndicator.querySelector("[data-background-total]").textContent = total.toLocaleString("en-US");
      backgroundIndicator.querySelector("[data-background-running]").textContent = values[4].toLocaleString("en-US");
      if (deliveryWarning) {
        const count = deliveryWarning.querySelector("[data-delivery-unknown]");
        const formatted = result.delivery_unknown.toLocaleString("en-US");
        if (count.textContent !== formatted) count.textContent = formatted;
        const hidden = result.delivery_unknown === 0;
        if (deliveryWarning.hidden !== hidden) deliveryWarning.hidden = hidden;
      }
      if (unavailable) unavailable.hidden = true;
    } catch {
      if (unavailable) unavailable.hidden = false;
    } finally { backgroundPending = false; }
  }
  if (backgroundIndicator) headerPolls.push(window.setInterval(refreshBackground, 30000));

  // Admin pages use the shared inactivity dialog in session-v1.js instead.
  const session = document.querySelector("[data-family-session]");
  if (!session) return;
  const warning = document.getElementById("session-warning");
  const expired = document.getElementById("session-expired");
  const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  const offset = Date.parse(session.dataset.serverNow) - Date.now();
  let deadline = Date.parse(session.dataset.idleDeadline);
  const absolute = Date.parse(session.dataset.absoluteDeadline);
  let dirty = false;
  let pending = false;
  let lastAttempt = Date.now();
  let expiryAnnounced = false;
  let familyFinished = false;
  document.addEventListener("stewardship:family-finished", () => {
    if (!session.hasAttribute("data-family-session")) return;
    familyFinished = true;
    warning.hidden = expired.hidden = true;
  });
  if (![offset, deadline, absolute].every(Number.isFinite)) return;

  let familyPresencePending = false;
  async function familyPresence() {
    if (familyFinished || !session.hasAttribute("data-family-session") || document.hidden ||
        familyPresencePending || !csrf ||
        Date.now() + offset >= Math.min(deadline, absolute)) return;
    familyPresencePending = true;
    try {
      await fetch("/family/presence", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf.value, "Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({section: session.dataset.presenceSection || "welcome"})
      });
    } catch {
      // Presence failure neither renews the session nor replays any form values.
    } finally { familyPresencePending = false; }
  }
  if (session.hasAttribute("data-family-session")) {
    familyPresence();
    window.setInterval(familyPresence, 30000);
  }

  // Polling, focus and visibility do not imply activity. The empty server-side
  // keepalive is explicitly an untrusted claim, capped there too at five minutes.
  ["input", "keydown", "pointerdown"].forEach((event) => {
    document.addEventListener(event, () => { dirty = true; }, {passive: true});
  });
  async function tick() {
    if (familyFinished) return;
    const now = Date.now() + offset;
    const remaining = Math.min(deadline, absolute) - now;
    // Once the Family form has ended the session (data-expired), keep the
    // notice shown even if the local timer still has time left.
    const ended = expired.hasAttribute("data-expired");
    warning.hidden = ended || remaining > 300000 || remaining <= 0;
    expired.hidden = !ended && remaining > 0;
    if (remaining <= 0 && !expiryAnnounced && session.hasAttribute("data-family-session")) {
      expiryAnnounced = true;
      document.dispatchEvent(new Event("stewardship:family-expired"));
    }
    if (!session.hasAttribute("data-family-session") || remaining <= 0 ||
        !dirty || pending || document.hidden || !csrf ||
        Date.now() - lastAttempt < 300000) return;
    pending = true;
    dirty = false;
    lastAttempt = Date.now();
    try {
      const response = await fetch("/family/keepalive", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"}
      });
      if (response.ok) {
        const result = await response.json();
        const next = Date.parse(result.idle_deadline);
        if (!Number.isFinite(next)) throw new Error("Invalid activity response");
        deadline = Math.min(next, absolute);
      } else {
        dirty = true;
      }
    } catch {
      // Retry the unconsumed claim at the next allowed five-minute interval.
      // Failure never extends the local deadline or bypasses server admission.
      dirty = true;
    } finally { pending = false; }
  }
  tick();
  window.setInterval(tick, 15000);
})();

// Toggletips, shared by the Family and Admin pages: a small "i" button beside
// a label that shows or hides one short help bubble on click, tap, Enter or
// Space. Never on hover: touch screens have none, and hover bubbles vanish
// while someone is reading them. Markup (components/toggletip.html renders it
// on the server; window.StewardshipToggletip.create(text, labelId) builds it
// in page scripts):
//   <span class="toggletip">
//     <button type="button" class="toggletip-button" aria-expanded="false"
//       aria-controls="ID" aria-label="More information"
//       aria-describedby="LABEL-ID">i</button>
//     <span class="toggletip-bubble" id="ID" hidden>Help text</span>
//   </span>
// The bubble follows its button in the document, so screen readers reach it
// next. The button's name is generic on purpose: naming it after its label
// ("More about Phone") would make it a second match for that label's text,
// both for voice control and for tests that find fields by label. Its
// description (the label, when given an id) says which field it explains. One document-level listener serves every toggletip, including ones
// page scripts add later. Only one bubble is open at a time; Escape or a
// click elsewhere closes it. Without JavaScript the bubble stays hidden and
// the button does nothing, so keep anything essential in visible text.
(() => {
  let count = 0;
  function close(tip, returnFocus = false) {
    tip.classList.remove("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "false");
    tip.querySelector(".toggletip-bubble").hidden = true;
    if (returnFocus) tip.querySelector(".toggletip-button").focus();
  }
  function open(tip) {
    document.querySelectorAll(".toggletip-open").forEach((other) => close(other));
    const bubble = tip.querySelector(".toggletip-bubble");
    tip.classList.add("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "true");
    bubble.hidden = false;
    // Keep the bubble on screen: a button near the right edge of a phone
    // would otherwise push it past the viewport (and scroll the page).
    bubble.style.insetInlineStart = "";
    const edge = 8, box = bubble.getBoundingClientRect();
    const overflow = box.right - (document.documentElement.clientWidth - edge);
    if (overflow > 0) {
      // Shift left by the overflow, but never past the viewport's left edge.
      bubble.style.insetInlineStart = -Math.max(0, Math.min(overflow, box.left - edge)) + "px";
    }
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".toggletip-button");
    const openTip = document.querySelector(".toggletip-open");
    if (button) {
      const tip = button.closest(".toggletip");
      if (tip === openTip) close(tip);
      else open(tip);
    } else if (openTip && !openTip.contains(event.target)) close(openTip);
  });
  document.addEventListener("keydown", (event) => {
    const openTip = document.querySelector(".toggletip-open");
    if (event.key === "Escape" && openTip) close(openTip, openTip.contains(document.activeElement));
  });
  window.StewardshipToggletip = {
    create(text, labelId = "") {
      // Build the markup above for script-rendered pages (the Family form).
      const tip = document.createElement("span");
      tip.className = "toggletip";
      const button = document.createElement("button");
      const id = "toggletip-" + (++count);
      Object.entries({type: "button", class: "toggletip-button", "aria-expanded": "false",
        "aria-controls": id, "aria-label": "More information"}).forEach(
        ([key, value]) => button.setAttribute(key, value));
      if (labelId) button.setAttribute("aria-describedby", labelId);
      button.textContent = "i";
      const bubble = document.createElement("span");
      bubble.className = "toggletip-bubble";
      bubble.id = id;
      bubble.hidden = true;
      bubble.textContent = text;
      tip.append(button, bubble);
      return tip;
    }
  };
})();
