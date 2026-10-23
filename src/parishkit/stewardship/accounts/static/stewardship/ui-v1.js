"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  // The Admin sidebar menu is open in the markup. Collapse it on narrow
  // screens, where it sits above the page content, and keep it open on wide
  // screens, where its toggle is hidden.
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
  // Delegated, so an Upload form an in-place answer brings in (#519) fills
  // its name too. The name the field was last filled with is kept on the
  // field (data-derived), so a name the reader typed is never replaced.
  document.addEventListener("change", (event) => {
    const source = event.target;
    if (!(source instanceof HTMLInputElement) || !source.matches("input[data-slug-from]")) return;
    const target = source.form?.querySelector("input[data-slug-to]");
    const file = source.files && source.files[0];
    if (!target || !file || (target.value && target.value !== target.dataset.derived)) return;
    const stem = file.name.replace(/\.[^.]*$/, "");
    let slug = stem.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    if (slug.length > 64) slug = slug.slice(0, 64).replace(/-[^-]*$/, "") || slug.slice(0, 64);
    target.dataset.derived = slug.replace(/^-+|-+$/g, "") || "file";
    target.value = target.dataset.derived;
  });

  // The parish's chosen date format (date-format-v1.js, loaded first).
  if (window.ParishDates) window.ParishDates.localize(document);

  // A download's timezone choice offers the browser's own zone, chosen by
  // default; the markup selects UTC, which stays when the browser's zone is
  // not offered. Also run for markup a table swap brings in (enhanceTable
  // below).
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
      // A note can be a toggletip (Sent mail's From, #1562): it shows its "i"
      // the same way, and the fields are described by its bubble, never by
      // the button's "More information".
      const noteNode = scope.querySelector("[data-browser-zone-note]");
      const note = noteNode?.querySelector(".toggletip-bubble") || noteNode;
      const gateHint = scope.querySelector("[data-complete-hint]");
      input.value = known ? zone : "";
      scope.querySelectorAll("[data-browser-zone-name]").forEach((node) => {
        node.textContent = input.value;
      });
      if (noteNode) noteNode.hidden = !known;
      scope.querySelectorAll("[data-zone-dependent]").forEach((field) => {
        field.disabled = !known;
      });
      scope.querySelectorAll("[data-browser-zone-field]").forEach((field) => {
        if (hint) {
          field.dataset.missingHint = hint;
          field.dataset.zoneHint = hint;
        } else {
          // An end date keeps its own order hint (#563) once the zone is known.
          if (field.dataset.orderHint) field.dataset.missingHint = field.dataset.orderHint;
          else delete field.dataset.missingHint;
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
    // A followed link's days are in the zone of the computer it was made on
    // (System logs, #536); its [data-link-zone] note shows only when that is
    // not this browser's zone.
    root.querySelectorAll("[data-link-zone]").forEach((note) => {
      note.hidden = note.dataset.linkZone === zone;
    });
    // A fresh time zone menu marked data-browser-default (Create the
    // campaign, the setup Parish step, #1330) starts on this browser's zone
    // when it offers it; otherwise it keeps the server's choice (the parish
    // zone, or a blank required entry), never the list's first zone. An
    // engine may report ICU's older name for a zone the list offers by its
    // IANA name (Chromium says America/Louisville for
    // America/Kentucky/Louisville), so an option this engine resolves to the
    // reported name matches too. A browser reporting UTC (or one of its
    // aliases) is treated like one reporting no zone: a server, VM or
    // privacy setting reports it, rarely a parish's own clock.
    const resolved = (name) => {
      try { return new Intl.DateTimeFormat("en-US", {timeZone: name}).resolvedOptions().timeZone; } catch (_) { return ""; }
    };
    const unknownZone = !zone || zone === "Etc/Unknown"
      || /^(Etc\/)?(UTC|UCT|Universal|Zulu)$/.test(zone);
    root.querySelectorAll("select[data-browser-default]").forEach((select) => {
      if (select.disabled || unknownZone) return;
      const values = Array.from(select.options, (option) => option.value).filter(Boolean);
      const match = values.includes(zone) ? zone : values.find((value) => resolved(value) === zone);
      if (match) select.value = match;
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
  // A field with a shared format rule (data-field-rule; the server's
  // web/field_rules.py gives it the same pattern and words) also says its
  // refusal at the field when left: in its reserved error line ("<id>_error",
  // file-size-field.html), which keeps its height while empty so the message
  // coming or going moves nothing, and is a polite live region (#1577). The
  // words are the rule's data-missing-hint, or data-empty-hint for an empty
  // required field. The message follows the value while the mark stands and
  // goes once the value is valid. A server's message in the same line is
  // cleared on the first edit, as for any field.
  const ruleLine = (field) => field.hasAttribute("data-field-rule") && field.id
    && document.getElementById(`${field.id}_error`);
  const sayRule = (field, invalid) => {
    const line = ruleLine(field);
    if (!line) return;
    // A page check's own refusal (a short label another option has,
    // data-distinct) says its own words, not the format rule's.
    const message = !invalid ? ""
      : (field.validity.valueMissing && field.dataset.emptyHint)
        || (field.hasAttribute("data-distinct") && field.validity.customError
          && field.validationMessage)
        || field.dataset.missingHint || field.validationMessage;
    // Unchanged words stay untouched: rewriting the live region on every
    // keystroke would make a screen reader read them again.
    if (line.hidden === !message && line.textContent === message) return;
    line.hidden = !message;
    line.replaceChildren(Object.assign(document.createElement("li"), {textContent: message}));
    const ids = (field.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((id) => id && id !== line.id);
    if (message) ids.push(line.id);
    if (ids.length) field.setAttribute("aria-describedby", ids.join(" "));
    else field.removeAttribute("aria-describedby");
  };
  const wireValidity = (root) => {
    root.querySelectorAll("input, select, textarea").forEach((field) => {
      if (field.getAttribute("aria-invalid") === "true") field.setAttribute("data-field-error", "");
      const edited = () => {
        if (field.hasAttribute("data-field-error")) clearFieldError(field);
        else if (blurMarked.has(field) && field.validity.valid) {
          field.setAttribute("aria-invalid", "false");
          blurMarked.delete(field);
          sayRule(field, false);
        } else if (blurMarked.has(field)) sayRule(field, true);
      };
      field.addEventListener("input", edited);
      field.addEventListener("change", edited);
      field.addEventListener("blur", () => {
        if (!field.willValidate) return;
        const invalid = field.hasAttribute("data-field-error") || !field.validity.valid;
        field.setAttribute("aria-invalid", String(invalid));
        if (invalid && !field.hasAttribute("data-field-error")) {
          blurMarked.add(field);
          sayRule(field, true);
        }
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
  // The complete gate's hint for an entry that cannot be read (#2082):
  // short enough for one line at 320 px, as the refusal itself shows at the
  // field.
  const TIME_HINT = "Fix the time above.";
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
  // The page script's one weekday source (#1129), matching the server's
  // wall_clock.WEEKDAYS: the day names Monday first, and mondayFirst, which
  // turns a JavaScript day number (getDay or getUTCDay: Sunday 0) into that
  // index (Monday 0, as Python's date.weekday() and every stored schedule
  // count). The test_wall_clock guard keeps every other script from holding
  // its own list of day names.
  const WEEKDAY_NAMES = Object.freeze(["Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday"]);
  const mondayFirst = (jsDay) => (jsDay + 6) % 7;
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

  // The controls this code reads: a text box, or a textarea for a list that
  // takes one time per line (the refresh schedule's Edit as text, #1117).
  // Every Admin time of day is one of these, so every page shows the same
  // reading (tests/stewardship/test_time_entry_guard.py keeps it so).
  const TIME_FIELDS = ":is(input, textarea)[data-time-entry]";
  // Per field: its visible reading line, its visually hidden live region and
  // the timer that waits for a pause in typing.
  const timeParts = new WeakMap();
  const timeGateHints = new WeakMap();
  let timeGateCount = 0;
  const TIME_PAUSE = 500;
  // A list reading names each time up to this many; a longer list is
  // summarised (readTimeField).
  const TIME_LIST_SHOWN = 8;
  // A field out of view: hidden, or inside a closed <details> such as the
  // refresh schedule's Edit as text.
  const outOfView = (field) => Boolean(field.closest("[hidden], details:not([open])"));
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
    // A long list (Edit as text can hold dozens of times, #1117) reads as
    // its count and range, so the line stays a line or two and does not
    // push the page about as the Admin types.
    const sorted = [...result.values].sort((a, b) =>
      canonicalTime(a).localeCompare(canonicalTime(b)));
    const distinct = new Set(sorted.map(canonicalTime)).size;
    const times = distinct > TIME_LIST_SHOWN
      ? `${distinct} times, from ${timeReading(sorted[0])} to ${timeReading(sorted.at(-1))}`
      : result.values.map(timeReading).join(", ");
    const read = `${result.blank ? "Blank reads as" : "Reads as"} ${times}`;
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
    if (root) root.querySelectorAll(TIME_FIELDS).forEach(cancelTimeCheck);
    else [...timePending].filter((field) => !field.isConnected).forEach(cancelTimeCheck);
  };
  // Every change to a reading line goes through this, which a page that
  // keeps its layout steady itself can replace (ParishTimeFields, below):
  // a reading can grow by a line or two on a narrow screen. By default the
  // change is simply made.
  let aroundReading = (field, change) => change();
  // Show a field's reading or refusal and set its validity, which the Save
  // gates read at once. ``typing`` (an input event on this field) defers a
  // refusal, and the Save hint that explains it, until typing pauses, so a
  // half-typed "2:" is not flashed red; aria-invalid follows what is shown.
  // ``quiet`` keeps the line empty while a server error under the field says
  // the same. Only this field's own typing (after the pause) or blur
  // ``announce``s, through the live region, and only a changed message:
  // loading, pageshow and other controls' changes update the line silently.
  //
  // A field can also carry a mark from a message beside it rather than from
  // its own reading (data-field-error: a server refusal in a shared message
  // element, or the follow-up contact time's not-in-the-future check, #398).
  // That mark keeps aria-invalid true whatever the reading. While the
  // entry is unreadable a server mark's message already says so, so
  // the reading line stays quiet. A time field that also takes the browser's
  // zone (data-browser-zone-field) keeps the zone's validity once its entry
  // reads, since both set the same custom validity.
  const checkTimeField = (field, {typing = false, announce = false, quiet = false} = {}) => {
    const parts = timeParts.get(field);
    if (!parts) return;
    const {message, invalid} = readTimeField(field);
    // Out of view, the refusal still shows in its line but sets no custom
    // validity: the browser would otherwise refuse the submit ("not
    // focusable") with nothing the Admin can see. It is set again when the
    // field comes back into view (the toggle listener, recheckTimes).
    field.setCustomValidity(invalid && !outOfView(field) ? message : "");
    if (!invalid && field.matches("[data-browser-zone-field]")) zoneValidity(field);
    cancelTimeCheck(field);
    const marked = () => field.hasAttribute("data-field-error");
    // A server mark has its own message beside the field; the not-in-the-
    // future check's mark (value "client") does not, so a time it cannot
    // read still shows its own reading line.
    const silent = () => quiet
      || (invalid && marked() && field.getAttribute("data-field-error") !== "client");
    const show = (speak) => aroundReading(field, () => {
      const shown = silent() ? "" : message;
      parts.reading.textContent = shown;
      parts.reading.classList.toggle("is-error", invalid && !silent());
      field.setAttribute("aria-invalid", String(invalid || silent() || marked()));
      if (speak && shown !== parts.spoken) parts.live.textContent = shown;
      parts.spoken = shown;
    });
    if (!typing) {
      show(announce);
      return;
    }
    // A half-typed entry: no stale reading and no red until the pause.
    aroundReading(field, () => {
      parts.reading.textContent = invalid || quiet ? "" : message;
      parts.reading.classList.remove("is-error");
      field.setAttribute("aria-invalid", String((!invalid && quiet) || marked()));
    });
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
  // A page that reserves a time field's line keeps the list out of it until
  // there is an error (report-email-entry.html): a list here always reads as
  // the server's refusal.
  // A list already cleared (hidden by clearFieldError, when the other field
  // sharing its message was edited) is no refusal any more.
  const serverTimeError = (field) => {
    const error = field.id && document.getElementById(`${field.id}_error`);
    return error && !error.hidden ? error : null;
  };
  const clearServerTimeError = (field) => {
    const error = serverTimeError(field);
    if (!error) return;
    // A refusal shared with another field (a one-time send's date and time)
    // clears from both, as any shared server message does (wireValidity),
    // before its list goes.
    if (field.hasAttribute("data-field-error")) clearFieldError(field);
    error.remove();
    const described = (field.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((id) => id && id !== error.id);
    field.setAttribute("aria-describedby", described.join(" "));
    field.setAttribute("aria-invalid", "false");
  };
  const isSubmit = (node) => (node instanceof HTMLButtonElement
    || node instanceof HTMLInputElement) && node.type === "submit";
  // Several gates can hold one submit button (the time entry, acknowledgment
  // and complete gates below, and the change gate, #921). Each marks a button
  // it holds with its own attribute, and a button is enabled again only once
  // no gate holds it. A button that is disabled without any gate's mark (the
  // server drew it disabled, or another script disabled it: a pending test)
  // is never taken over, so no gate ever enables it.
  //
  // Firefox brings a script-set disabled back when the page is reloaded,
  // without the gate's mark, which would leave a held button disabled for
  // good. Two defences: a held button is marked autocomplete="off", which
  // tells Firefox not to keep its state, and the server marks every submit
  // button it draws disabled with data-server-disabled (the {% button %}
  // tag), so a disabled submit button without that mark when this script
  // starts (before any script could have disabled it) came back from an
  // earlier visit and is enabled again, as the server drew it.
  // live-status-v1.js holds a kept preview's button for good once the
  // status no longer shows what it reviews (data-live-stale-gated, #530),
  // and an export's Download once its window ends (data-download-ended-gated,
  // #1398).
  // refresh-schedule-v1.js holds the ParishSoft Save while its schedule has
  // problems (data-schedule-gated), alongside the change gate (#1330).
  // The mail schedule pages hold theirs while a send date falls outside
  // the campaign (data-window-gated, #1787), and New scheduled email while
  // its Repeat gives no date to add (data-repeat-gated, #1915). The
  // date-change review holds Review changes while its New schedule pane
  // holds part of a schedule (data-pane-gated, #1944).
  const GATE_MARKS = [
    "data-time-gated", "data-acknowledgment-gated", "data-complete-gated", "data-change-gated",
    "data-live-stale-gated", "data-schedule-gated", "data-download-ended-gated",
    "data-save-spent-gated", "data-window-gated", "data-repeat-gated", "data-pane-gated",
  ];
  const gated = (node) => GATE_MARKS.some((mark) => node.hasAttribute(mark));
  document.querySelectorAll("button[disabled], input[disabled]").forEach((node) => {
    if (isSubmit(node) && !node.hasAttribute("data-server-disabled")) node.disabled = false;
  });
  const holdButton = (node, mark) => {
    if (node.disabled && !gated(node)) return;
    node.setAttribute("autocomplete", "off");
    node.disabled = true;
    node.setAttribute(mark, "");
  };
  const releaseButton = (node, mark) => {
    if (!node.hasAttribute(mark)) return;
    node.removeAttribute(mark);
    node.disabled = gated(node);
  };
  // Add (``on``) or take away the id ``id`` in ``node``'s aria-describedby.
  const describeBy = (node, id, on) => {
    const ids = (node.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((other) => other && other !== id);
    if (on) ids.push(id);
    if (ids.length) node.setAttribute("aria-describedby", ids.join(" "));
    else node.removeAttribute("aria-describedby");
  };
  // Hold ``form``'s submit buttons (not a formnovalidate cancel) with the
  // gate ``mark`` while ``reason`` is set, each named by a visually hidden
  // line holding those words. The words themselves show where the reader
  // is (a field's error line, a summary line), so the hidden line, made
  // the first time a reason comes, after the last button, never moves
  // anything. ``hints`` keeps each form's line; ``suffix`` ends its id.
  // Used by the send-date window check (#1787) and New scheduled email's
  // Repeat (#1915).
  const holdFormButtons = (form, {mark, hints, suffix}, reason) => {
    const buttons = [...form.elements].filter((node) => isSubmit(node) && !node.formNoValidate);
    let hint = hints.get(form);
    if (!hint && reason && buttons.length) {
      hint = document.createElement("span");
      hint.className = "visually-hidden";
      hint.id = `${form.id || "schedule"}-${suffix}`;
      const last = buttons[buttons.length - 1];
      (last.parentElement === form ? last : last.parentElement).after(hint);
      hints.set(form, hint);
    }
    if (hint) hint.textContent = reason;
    buttons.forEach((node) => {
      if (hint) describeBy(node, hint.id, Boolean(reason));
      if (reason) holdButton(node, mark);
      else releaseButton(node, mark);
    });
  };
  // Keep a form's submit buttons unavailable while a shown time entry cannot
  // be read, with a hint after them saying why. As with the complete gate,
  // real disabled is used (holdButton and releaseButton, above).
  // A data-require-complete form is left to that gate: the entry's custom
  // validity is set in a capture-phase listener (below), before the gate's
  // own input listener runs, so it counts the current keystroke.
  // ``defer`` (typing) disables at once but leaves a hidden hint hidden
  // until the pause timer calls again, as the refusal it explains waits.
  const gateTimes = (form, {defer = false} = {}) => {
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-require-complete")) return;
    const fields = [...form.querySelectorAll(TIME_FIELDS)];
    if (!fields.length && !timeGateHints.has(form)) return;
    // A field out of view holds nothing: the Admin could not see why.
    const unread = fields.some((field) => !field.disabled
      && !outOfView(field) && !field.validity.valid);
    // A formnovalidate button (a cancel or back action) stays usable.
    const buttons = [...form.elements].filter((node) => isSubmit(node) && !node.formNoValidate);
    let hint = timeGateHints.get(form);
    const explain = unread && (!defer || Boolean(hint && !hint.hidden));
    if (!hint && explain && buttons.length) {
      // Always visually hidden (never toggled), so it never shifts layout.
      hint = document.createElement("span");
      hint.className = "visually-hidden";
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
      if (unread) holdButton(node, "data-time-gated");
      else releaseButton(node, "data-time-gated");
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
    root.querySelectorAll(TIME_FIELDS).forEach((field) => {
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
  // A page script that builds rows of time fields itself (the refresh
  // schedule editor, refresh-schedule-v1.js) wires each new row, and clears
  // a removed row's timers, through these. steadyReadings(hook) has each
  // later reading change made as hook(field, change), so that page can keep
  // what is under the pointer in place as a reading grows or shrinks.
  // recheck(fields) reads those fields again silently, after the page
  // script set their values itself (the Edit as text lists, refilled from
  // each check's answer). Only those: a field elsewhere that the Admin is
  // still typing in keeps waiting for its pause.
  window.ParishTimeFields = Object.freeze({
    wire: wireTimeEntry,
    cancel: cancelTimeChecks,
    recheck: (fields) => {
      const forms = new Set();
      fields.forEach((field) => {
        checkTimeField(field, {quiet: Boolean(serverTimeError(field))});
        forms.add(field.form);
      });
      forms.forEach((form) => gateTimes(form, {defer: timePending.size > 0}));
    },
    steadyReadings: (hook) => { aroundReading = hook; },
  });
  // Capture phase: the entry's validity is current before any form's own
  // input listener (the complete gate) runs.
  document.addEventListener("input", (event) => {
    const field = event.target;
    if (!timeParts.has(field)) return;
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
    if (!timeParts.has(field)) return;
    const quiet = Boolean(serverTimeError(field));
    const result = readTimeEntry(field);
    const blank = field.dataset.timeEntry === "list" ? !splitTimes(field.value).length
      : !timeWords(field.value).length;
    if (!result.error && !blank) {
      // A list typed one time per line (a textarea) stays one per line.
      const joiner = /[\n\r]/.test(field.value) ? "\n" : ", ";
      field.value = result.values ? result.values.map(canonicalTime).join(joiner)
        : canonicalTime(result.value);
    }
    checkTimeField(field, {quiet, announce: true});
    gateTimes(field.form);
    // The rewrite fires no input event: check again whether the form still
    // differs from its saved values ("6am" back to the saved "06:00").
    gateChange(field.form);
  });
  // Another control can show, hide, enable or clear a time field without an
  // event on it (a schedule's mail type, the refresh frequency), and the
  // back/forward cache can restore values silently: check again then.
  const recheckTimes = (form) => {
    if (!(form instanceof HTMLFormElement)) return;
    form.querySelectorAll(TIME_FIELDS).forEach((field) =>
      checkTimeField(field, {quiet: Boolean(serverTimeError(field))}));
    gateTimes(form);
  };
  document.addEventListener("change", (event) => {
    if (event.target instanceof Element) recheckTimes(event.target.closest("form"));
  });
  window.addEventListener("pageshow", () => {
    new Set([...document.querySelectorAll(TIME_FIELDS)].map((field) =>
      field.form)).forEach(recheckTimes);
  });
  // Opening or closing a <details> shows or hides the fields in it, which
  // decides whether they hold Save (gateTimes). "toggle" does not bubble.
  document.addEventListener("toggle", (event) => {
    if (event.target instanceof HTMLDetailsElement
      && event.target.querySelector(TIME_FIELDS)) recheckTimes(event.target.closest("form"));
  }, true);

  // Shared Admin tables (web/tables.py, table-navigator.html and
  // table-selection.html). Row selection is per page: Select all and the
  // header checkbox choose every enabled row checkbox shown, and bulk action
  // buttons stay disabled until something is chosen. While nothing is, the
  // reason line table-selection.html puts below the buttons
  // (data-selection-hint) shows the table's data-missing-hint, or a generic
  // line, as visible text, and the buttons name it in aria-describedby
  // (Administrator decision B2, 2026-10-10; #879). The line never leaves the
  // layout: once a row is ticked its text is only made invisible
  // (data-cleared), so nothing below or beside it moves (#563, #736). The
  // back/forward cache can restore ticks without a change event, so every
  // table's state is worked out again on pageshow. The server still
  // validates every submitted selection.
  let selectionUpdates = []; // {scope, update} for each wired table
  let selectionHints = 0; // numbers each hint's id
  const wireSelection = (scope) => {
    const rows = () => [...scope.querySelectorAll("input[data-select-row]")]
      .filter((node) => !node.disabled);
    const header = scope.querySelector("input[data-select-all]");
    const button = scope.querySelector("[data-select-all-button]");
    const count = scope.querySelector("[data-selected-count]");
    const actions = [...scope.querySelectorAll("[data-bulk-action]")];
    const hint = actions.length ? scope.querySelector("[data-selection-hint]") : null;
    if (hint && !hint.id) {
      // An id the include was given (hint_id) is kept, so a button the
      // server disabled can name the line too.
      selectionHints += 1;
      hint.id = `selection-hint-${selectionHints}`;
    }
    const missingHint = scope.dataset.missingHint || "Select at least one row first.";
    // A table that gives an empty-table reason (the include's empty_hint)
    // keeps it visible in the line while the table has no rows at all.
    const emptyHint = hint?.dataset.emptyHint || "";
    // Rows can be shown with none of them selectable (every file in use,
    // no open request): their selection cells are empty. The line then says
    // why nothing can be ticked, from the table's data-none-hint; without
    // one it stays blank, as for an empty table (#1332).
    const noneHint = scope.dataset.noneHint || "";
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
      // A bulk button for one kind of row (its data-confirm-kind, matching
      // the rows'; the Ministry follow-up queue's outcomes, #1131) is
      // available only while every ticked row is of its kind. While one is
      // unavailable for that reason, the line says why: the table's
      // data-kind-hint-<kind> when the ticked rows are all of one kind, or
      // its data-kind-hint-mixed when they mix kinds. A row whose box lists
      // several kinds (space-separated: the Census changes decisions a
      // row's Review offers, #528) fits each of them; a table whose rows
      // do so gives one data-kind-hint for every case.
      const ticked = all.filter((node) => node.checked);
      const kinds = new Set(ticked.map((node) => node.dataset.confirmKind || ""));
      const fits = (node) => chosen > 0 && (!node.dataset.confirmKind
        || ticked.every((row) => (row.dataset.confirmKind || "").split(/\s+/)
          .includes(node.dataset.confirmKind)));
      const kindHint = !chosen || actions.every(fits) ? ""
        : scope.dataset.kindHint
          || scope.getAttribute(`data-kind-hint-${kinds.size > 1 ? "mixed" : [...kinds][0]}`) || "";
      // With no rows at all the table itself says it is empty, unless the
      // table gave an empty-table reason, which then stays in the line.
      const none = !all.length && Boolean(noneHint)
        && Boolean(scope.querySelector("tbody td.select-col"));
      const empty = !all.length && !none && Boolean(emptyHint);
      const explain = Boolean(hint)
        && (Boolean(kindHint) || (!chosen && (all.length > 0 || empty || none)));
      if (hint) {
        const text = kindHint || (none ? noneHint : empty ? emptyHint : missingHint);
        if (hint.textContent !== text) hint.textContent = text;
      }
      hint?.toggleAttribute("data-cleared", !explain);
      actions.forEach((node) => {
        node.disabled = !fits(node);
        const described = (node.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter((id) => id && id !== hint?.id);
        if (explain && node.disabled) described.push(hint.id);
        if (described.length) node.setAttribute("aria-describedby", described.join(" "));
        else node.removeAttribute("aria-describedby");
      });
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
    // A table an in-place swap replaced no longer needs its re-check.
    selectionUpdates = selectionUpdates.filter((item) => item.scope.isConnected);
    selectionUpdates.push({scope, update});
    update();
  };
  window.addEventListener("pageshow", () => {
    selectionUpdates.forEach(({scope, update}) => {
      if (scope.isConnected) update();
    });
  });
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
  // New and Edit scheduled email (#878) show and take send times in this
  // browser's time zone (#558), as every Admin time is. A schedule keeps
  // campaign-local values, which the server converts from what is posted
  // with the browser's zone (input[data-browser-zone], filled above). An
  // unbound Edit page carries the schedule's moment as data-schedule-local
  // (a UTC instant; a digest's next send), and its date, time and weekday
  // are filled from it here, in this browser's zone, before the time entry
  // first reads the time. A refused page has none: its fields hold what was
  // typed. The filled values are also made the fields' defaults: they are
  // the saved schedule as this browser shows it, which the change gate
  // (data-require-change, below) compares the form with, so Review changes
  // stays unavailable until the reader changes one (#750). Without a zone
  // Review changes stays unavailable and [data-zone-missing] says why.
  // The date-change review's schedules (form[data-schedule-zoned]) work the
  // same way, row by row: each unbound saved row carries its own
  // data-schedule-local, and Review changes waits for a zone.
  // localParts: a moment (ms) as this browser's {date, time, weekday}, the
  // time canonical as the time entry writes it and Monday 0.
  const localParts = (moment) => {
    const at = new Date(moment);
    return {
      date: `${String(at.getFullYear()).padStart(4, "0")}-${pad2(at.getMonth() + 1)}-${
        pad2(at.getDate())}`,
      time: canonicalTime({hour: at.getHours(), minute: at.getMinutes(),
        second: at.getSeconds()}),
      weekday: mondayFirst(at.getDay()),
    };
  };
  // localMoment: the moment (ms) a date ("YYYY-MM-DD") and canonical time
  // stand for in this browser's zone, by the server's one daylight-saving
  // rule (ui-conventions#schedules, resolve_local): a time that occurs twice
  // is the first, and a time skipped when clocks spring forward is the first
  // moment after the gap, so 2:30 AM reads as 3:00 AM. Date alone reads a
  // skipped time with the offset before the change (3:30 AM), so a moment
  // that does not show the typed wall time is searched back to the gap's end.
  const localMoment = (day, clock) => {
    const [year, month, date] = day.split("-").map(Number);
    const [hour, minute, second = 0] = clock.split(":").map(Number);
    const wall = Date.UTC(year, month - 1, date, hour, minute, second);
    // The wall time a moment shows in this browser, counted as if it were
    // UTC, so two wall times compare as numbers.
    const shown = (moment) => {
      const at = new Date(moment);
      return Date.UTC(at.getFullYear(), at.getMonth(), at.getDate(), at.getHours(),
        at.getMinutes(), at.getSeconds(), at.getMilliseconds());
    };
    let high = new Date(year, month - 1, date, hour, minute, second).getTime();
    if (shown(high) === wall) return high;
    // Skipped: ``high`` shows a later wall time. Two days earlier shows an
    // earlier one even after a whole skipped day; the first millisecond
    // showing ``wall`` or later is the end of the gap.
    let low = high - 2 * 86400000;
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2);
      if (shown(middle) < wall) low = middle;
      else high = middle;
    }
    return high;
  };
  // Shared with the browser tests, which pin it to the server's reading.
  window.ParishLocalTime = Object.freeze({moment: localMoment});
  // Without a browser zone, a schedule form's send times cannot be read:
  // say so and hold its buttons. Run for the page and again for a redrawn
  // date-change review (#1944, parishkit:swap below).
  const gateScheduleZones = (root) => root.querySelectorAll(
    "form[data-schedule-entry], form[data-schedule-zoned]").forEach((form) => {
    const zone = form.querySelector("input[data-browser-zone]");
    if (zone && !zone.value) {
      const missing = form.querySelector("[data-zone-missing]");
      if (missing) missing.hidden = false;
      // form.elements, not the form's descendants: inside the campaign
      // wizard New and Edit's button is the action row's Save and go back,
      // a submit of this form drawn after it (#1404 PR 4b).
      [...form.elements].filter(isSubmit).forEach((button) => {
        button.disabled = true;
        if (missing?.id) {
          button.setAttribute("aria-describedby", [button.getAttribute("aria-describedby"),
            missing.id].filter(Boolean).join(" "));
        }
      });
    }
  });
  gateScheduleZones(document);
  // Fill one schedule's fields (New and Edit's form, or one date-change
  // review row) from its data-schedule-local moment, in this browser's
  // zone. Run for the page and again for a redrawn date-change review.
  const localizeSchedules = (root) => root.querySelectorAll(
    "form[data-schedule-entry][data-schedule-local], "
    + "[data-schedule-row][data-schedule-local]").forEach((scope) => {
    const moment = Date.parse(scope.dataset.scheduleLocal || "");
    if (Number.isNaN(moment)) return;
    const parts = localParts(moment);
    const field = (name) => scope.querySelector(
      `[data-schedule-field="${name}"] input, [data-schedule-field="${name}"] select`);
    // Set a field's value and its default (a select's default option) alike.
    const settle = (node, value) => {
      if (node instanceof HTMLSelectElement) {
        [...node.options].forEach((option) => { option.defaultSelected = option.value === value; });
      } else {
        node.defaultValue = value;
      }
      node.value = value;
    };
    // Only fields the saved schedule uses hold a value to convert.
    if (field("date")?.value) settle(field("date"), parts.date);
    if (field("weekday")?.value) settle(field("weekday"), String(parts.weekday));
    const time = field("time");
    if (time) {
      settle(time, parts.time);
      // A legacy time with seconds may be posted back unchanged (#631).
      if (time.dataset.timeKept) time.dataset.timeKept = parts.time;
    }
  });
  localizeSchedules(document);

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
  // load (when anything below goes wrong) lands on the region rather than
  // at the top of the page.
  //
  // Activating a control fetches exactly the request the control would have
  // made (a link's GET, a form's query or CSRF POST body), finds the regions
  // in the fetched page and swaps them in, so the reader keeps their scroll
  // position and focus. The server sees an ordinary page request and no
  // special response path exists. The controls are:
  // - a shared table's sort headings, navigator links and forms, and the
  //   page's filter form (form#table-filters, #484), unless that form is
  //   itself a form[data-in-place] because its options reshape far more of
  //   the page than a table;
  // - a[data-in-place]: a link that shows another view of this page (the
  //   Response dashboard's mode and Chart population pairs, #1535, #1537,
  //   whose links sit in the region they refresh, so each refresh redraws
  //   them with the current sort, size and zone; a page's periodic
  //   re-read). Its value, if any, is a stable key that finds the link
  //   again in the fresh page, for focus and to announce the view now shown
  //   ("Testing rehearsal");
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
  // ("Saved.") is announced before the region's row count.
  // A submit button marked data-in-place-moves (a follow-up item's Save and
  // next, #534) may be answered by a redirect to another page that draws the
  // same region (the next item): that page's regions are swapped in and the
  // address becomes its own, focus moves to the region's first heading (the
  // item now shown), and the button's data-in-place-message is announced
  // with that heading. On the last item, whose answer is the queue, the
  // button is marked data-in-place-native instead, so the browser submits
  // it natively and loads the queue once.
  // A page script can follow an a[data-in-place] link on the reader's behalf
  // (the Response dashboard applying this browser's time zone as it
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
  // history pager nested in a panel whose form holds unsaved typing), plus
  // any regions whose ids the attribute's value lists (a Mail delivery
  // resolution also redraws the email's state and history). A
  // link whose key is gone from the fresh page (Older history on the last
  // page) hands focus to the link its data-in-place-fallback key names. A
  // form marked data-in-place-anywhere changes a region every Admin page
  // draws (the critical-problems banner): whichever same-origin page
  // answers it, only that region is taken from the answer, and the address
  // stays. An element of the swapped region marked data-in-place-focus (a
  // review's heading) takes focus after a successful answer
  // instead of the control, since that is what the reader acts on next.
  // A region marked data-in-place-own is replaced only by an answer to a
  // control that names it (or lists it in data-in-place-only), never by
  // another control's answer or a redraw: Hosted files' Upload panel keeps a
  // chosen file and a typed name across a sort, a page or a deletion, which
  // a fresh copy would discard (#519).
  // A page whose filter form is marked data-in-place-views (Sent mail,
  // #1562) keeps, in each history entry, how to draw the view it shows: the
  // POST body of the read that drew it (never its CSRF token), or none
  // for a view drawn from its address or by a save. A form marked data-in-place-push (a
  // row's Recipient) adds a history entry for its answer instead of
  // replacing the address, and Back or Forward redraws that entry's view in
  // place (the popstate handler below), so a private filter that never
  // reaches a URL still has a Back. A reload draws the page from its
  // address, so it shows that view, its filter fields with it.
  const REGIONS = "[data-in-place-region][id], [data-table-region][id]";
  const VIEWS_FORM = "form#table-filters[data-in-place-views]";
  const ownedElsewhere = (other, region) => other !== region
    && other.hasAttribute("data-in-place-own");
  // The control whose answer is being swapped in, while refreshRegions swaps
  // it (synchronously), so a parishkit:swap listener can tell the reader's
  // own table choice (a sort heading, Previous or Next, the filters) from
  // any other answer, such as the quiet refresh after Apply. Null otherwise.
  let swappingFor = null;
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
  // ("Saved.") and the rows now shown, or names the view now shown
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
      // A form whose answer says itself what happened (a saved change or a
      // refusal, #528) names the element of the fresh page that says it;
      // that text is announced instead of a fixed message.
      const from = owner.getAttribute("data-in-place-message-from");
      const told = from && squeeze(document.getElementById(from)?.textContent || "");
      if (told) return told;
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
  // A page script that redraws a part of its page itself (New and Edit
  // report email's live Next sends table) enhances it the same way.
  window.ParishRegions = Object.freeze({enhance: enhanceSwapped});
  // The min-height (a CSS length, or "" for none) that keeps `element` tall
  // enough that the page's foot stays at or below the viewport's bottom edge
  // however its content shrinks. A reader scrolled to the foot of the page
  // would otherwise see the browser pull everything above it down under the
  // pointer (#736); a reader higher up needs less, often nothing, so a long
  // review followed by a short status leaves no gap below them. The height
  // is measured and kept unrounded (offsetHeight can round down, and rounding
  // up would move the page by a pixel), and never exceeds the element's
  // present height. The element is a flow root
  // (ui-v1.css), so its children's margins count in its height. A copy lives
  // in live-status-v1.js, which does not depend on this file.
  const heldHeight = (element) => {
    const root = document.documentElement;
    const height = element.getBoundingClientRect().height;
    const below = window.scrollY + root.clientHeight - (root.scrollHeight - height);
    const need = Math.min(height, below);
    return need > 0 ? `${need}px` : "";
  };
  // A pane selector (Campaign comparison's pledge distribution year, #1221)
  // shows the pane of its group whose data-pane is its value and hides the
  // others (data-shown, ui-v1.css), with no request: every year's pane is
  // already on the page, in one grid cell, so nothing moves.
  const showPane = (select) => {
    select.closest("[data-pane-group]")?.querySelectorAll("[data-pane]").forEach((pane) => {
      pane.toggleAttribute("data-shown", pane.dataset.pane === select.value);
    });
  };
  document.addEventListener("change", (event) => {
    if (event.target instanceof HTMLSelectElement && event.target.matches("select[data-pane-select]")) {
      showPane(event.target);
    }
  });
  // Swap one region for its fresh copy. Selections are restored on the copy
  // before it enters the document, so wireSelection's first update already
  // counts them and no change event fires for a tick the reader did not make.
  const swapRegion = (region, fresh) => {
    const chosen = new Set([...region.querySelectorAll("input[data-select-row]:checked")]
      .map((box) => box.value));
    fresh.querySelectorAll("input[data-select-row]").forEach((box) => {
      if (chosen.has(box.value)) box.checked = true;
    });
    // A disclosure marked data-keep-open (Campaign comparison's data notes,
    // #1161) stays open in the fresh copy when
    // the reader had opened it, so changing the day does not fold away what
    // they were reading.
    region.querySelectorAll("details[data-keep-open][id][open]").forEach((node) => {
      const copy = fresh.querySelector(`details[data-keep-open]#${CSS.escape(node.id)}`);
      if (copy) copy.open = true;
    });
    // A pane selector keeps the reader's choice in the fresh copy, which
    // shows that pane before it enters the document, so Compare on day
    // does not switch the pledge distribution back to this campaign (#1221).
    region.querySelectorAll("select[data-pane-select][id]").forEach((node) => {
      const copy = fresh.querySelector(`select[data-pane-select]#${CSS.escape(node.id)}`);
      if (!copy || ![...copy.options].some((option) => option.value === node.value)) return;
      copy.value = node.value;
      showPane(copy);
    });
    // Charts keep their space until the fresh ones are drawn (chart-v1.js).
    if (window.ParishCharts) window.ParishCharts.hold(region, fresh);
    // A data-keep-height region (a review or status region, such as Restore
    // review's or Past campaigns') keeps the height the reader's place needs
    // (heldHeight, above) (#736). One whose fresh copy is empty holds
    // nothing: every in-place answer redraws every region, so a review
    // region that another control's answer empties would otherwise keep a
    // blank gap until the page reloads (#973).
    const empty = !fresh.firstElementChild && !fresh.textContent.trim();
    if (region.hasAttribute("data-keep-height")) {
      fresh.style.minHeight = empty ? "" : heldHeight(region);
    }
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
  // zone and is left alone (see syncHidden). A field whose value this
  // changes gets its own (non-bubbling) change event, so a rule listening
  // on it runs again: the System logs level boxes come back when a
  // cross-link's answer clears the Activity choice (data-enabled-when,
  // #953); the form's own change event below covers the form-wide gates.
  const syncFilters = (parsed) => {
    const form = document.getElementById("table-filters");
    const fresh = parsed.getElementById("table-filters");
    if (!(form instanceof HTMLFormElement) || !(fresh instanceof HTMLFormElement)) return;
    const copies = [...fresh.elements];
    [...form.elements].forEach((field) => {
      if (!field.name || field.type === "hidden" || field.matches("[data-browser-zone]")) return;
      const copy = copies.find((node) => node.name === field.name);
      const before = field.type === "checkbox" ? field.checked : field.value;
      if (field.type === "checkbox") field.checked = Boolean(copy?.checked);
      else if (copy && "value" in field) field.value = copy.value;
      const after = field.type === "checkbox" ? field.checked : field.value;
      if (after !== before) field.dispatchEvent(new Event("change"));
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
      // A file field keeps its element, and so the file chosen in it, and
      // takes only the fresh page's size limit: Hosted files' room in the
      // library, which a deletion gives back (#1252). Its file is then
      // checked against the new limit, and its form's gate answers again.
      if (node instanceof HTMLInputElement && node.type === "file") {
        ["data-max-bytes", "data-max-bytes-message"].forEach((name) => {
          if (fresh.hasAttribute(name)) node.setAttribute(name, fresh.getAttribute(name));
          else node.removeAttribute(name);
        });
        if (node.form) {
          recheckSizes(node.form);
          gateComplete(node.form);
        }
        return;
      }
      // A select keeps its element, and so the reader's choice and focus,
      // and takes only the fresh page's wording of each choice: Census
      // changes' Status choices carry counts that follow every recorded
      // action (#1682).
      if (node instanceof HTMLSelectElement) {
        [...node.options].forEach((option) => {
          const copy = [...fresh.querySelectorAll("option")].find((other) => other.value === option.value);
          if (copy) option.textContent = copy.textContent;
        });
        return;
      }
      if (node instanceof HTMLFormElement) {
        syncHidden(node, fresh);
        // A menu whose choices carry counts (the review worklist's Stage,
        // #1684) takes the fresh page's choice labels, so its counts follow
        // a decision or an Apply; the reader's own choice is kept.
        fresh.querySelectorAll("select[data-sync-options][id]").forEach((copy) => {
          const menu = node.querySelector(`#${CSS.escape(copy.id)}`);
          if (!menu) return;
          [...copy.options].forEach((option, index) => {
            const shown = menu.options[index];
            if (shown?.value === option.value) shown.textContent = option.textContent;
          });
        });
        // The state a submit-on-change box's form now shows (see
        // reconcileBoxes); the box keeps the reader's own tick.
        fresh.querySelectorAll("[data-submit-on-change][data-applied][id]").forEach((copy) => {
          const box = node.querySelector(`#${CSS.escape(copy.id)}`);
          if (box) box.dataset.applied = copy.dataset.applied;
        });
        return;
      }
      // Charts in it (the Financial stewardship summary, #720) keep their
      // space until the fresh ones are drawn, so nothing below them moves.
      if (window.ParishCharts) window.ParishCharts.hold(node, fresh);
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
  // A successful swap can remove the fields an earlier refusal's summary
  // (outside every region) links to: Backup encryption key's Start over
  // after a wrong code (#750). Such an item names nothing any more, so it
  // goes, and the summary with it once it lists nothing; a summary item
  // whose field is still shown stays until that field is edited.
  const dropOrphanedSummaries = () => {
    document.querySelectorAll("[data-error-summary]").forEach((summary) => {
      if (summary.closest(REGIONS)) return;
      summary.querySelectorAll('li a[href^="#"]').forEach((link) => {
        if (!document.getElementById(link.hash.slice(1))) link.closest("li")?.remove();
      });
      if (!summary.querySelector("li")) summary.remove();
    });
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
  // A refused in-place POST answered as JSON (a typed error: a user-facing
  // refusal, a stale page, a denial, an outage) is the server's answer to a
  // script, never a page: shown whole it would be raw JSON (#519). It is
  // shown in place instead, as one alert line after the control's buttons,
  // so nothing above it moves: the refusal's message and fix in plain words
  // (refusalText, with the confirm dialogs' wording for its status when it
  // has none) and its same-origin link when it has one. A later answer to
  // the same form replaces the line (a new element, so it is announced
  // again) or, when it is not such a refusal, removes it. Keyed by the form
  // (or the control, outside any form) so a submitter outside its form
  // (form="…") still finds its own line.
  //
  // A typed refusal whose errors name a field of that form (FieldError's
  // field_id, such as Census changes' Edit value, #528) also marks the field
  // as a server field error is marked (#592): aria-invalid="true" and
  // described by the line. As the server's rule cannot be checked again
  // here, the mark and the line clear on the field's first edit (input or
  // change), and whenever the line goes.
  const refusalNotes = new WeakMap();
  const refusedFields = new WeakMap(); // key → [{field, clear}]
  const unmarkRefused = (key) => {
    (refusedFields.get(key) || []).forEach(({field, clear}) => {
      field.removeEventListener("input", clear);
      field.removeEventListener("change", clear);
      field.removeAttribute("data-refused");
      field.setAttribute("aria-invalid", "false");
      const ids = (field.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && id !== refusalNotes.get(key)?.id);
      if (ids.length) field.setAttribute("aria-describedby", ids.join(" "));
      else field.removeAttribute("aria-describedby");
    });
    refusedFields.delete(key);
  };
  const clearRefusal = (key) => {
    unmarkRefused(key);
    refusalNotes.get(key)?.remove();
    refusalNotes.delete(key);
  };
  const markRefused = (key, note, text) => {
    if (!(key instanceof HTMLFormElement)) return;
    let errors = [];
    try { errors = JSON.parse(text)?.errors || []; } catch { return; }
    const fields = errors.map((error) => error?.field_id
      && key.elements.namedItem(error.field_id))
      .filter((field) => field instanceof HTMLElement && field.matches("input, select, textarea"));
    if (!fields.length) return;
    note.id = `${key.id || "form"}-refusal`;
    const marked = [];
    fields.forEach((field) => {
      const clear = () => clearRefusal(key);
      field.setAttribute("aria-invalid", "true");
      field.setAttribute("data-refused", "");
      const ids = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      field.setAttribute("aria-describedby", [note.id, ...ids.filter((id) => id !== note.id)].join(" "));
      field.addEventListener("input", clear);
      field.addEventListener("change", clear);
      marked.push({field, clear});
    });
    refusedFields.set(key, marked);
  };
  // A JSON refusal's same-origin link ({url, label}, such as Outgoing mail
  // for held send work), or null when it has none.
  const refusalLink = (text) => {
    let link = null;
    try { link = JSON.parse(text)?.refusal?.link; } catch { /* not JSON: no link */ }
    return link?.url && link.label && sameOrigin(link.url) ? link : null;
  };
  const showRefusal = (key, control, status, text) => {
    unmarkRefused(key);
    const note = document.createElement("p");
    note.className = "notice notice-error";
    note.setAttribute("role", "alert");
    note.setAttribute("data-in-place-refusal", "");
    note.textContent = refusalText(status, text);
    const link = refusalLink(text);
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.textContent = link.label;
      note.append(" ", anchor);
    }
    const old = refusalNotes.get(key);
    if (old?.isConnected) {
      old.replaceWith(note);
    } else if (control instanceof HTMLFormElement || !(control instanceof Element)) {
      key.append(note);
    } else {
      (control.closest(".actions, p") || control).after(note);
    }
    refusalNotes.set(key, note);
    markRefused(key, note, text);
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
  // outside it). options.filters makes the filter form show the answer's
  // filters (syncFilters), as a data-in-place-filters owner does;
  // options.popped marks a Back or Forward redraw (see VIEWS_FORM), whose
  // history entry is left as it is. options.cleared marks Clear filters'
  // answer (#1651), the page's default filters, which the form then shows.
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
  // How to find the focused element again after a quiet refresh replaces
  // the region it is in: its id, else its nearest ancestor with an id (the
  // region itself at most) and its position among that ancestor's elements
  // of the same tag, link target and text. The position matters because a
  // paged table draws its navigator twice (two Next links alike) and every
  // row has its own Technical details summary: the first match would move
  // focus to another copy (#1586 review). Null when focus is not in an
  // in-place region.
  const heldFocus = () => {
    const node = document.activeElement;
    if (!(node instanceof Element) || !node.closest(REGIONS)) return null;
    if (node.id) return {id: node.id};
    const anchor = node.parentElement?.closest("[id]");
    if (!anchor) return null;
    const held = {anchor: anchor.id, tag: node.tagName, href: node.getAttribute("href"),
      text: node.textContent};
    held.index = heldMatches(anchor, held).indexOf(node);
    return held;
  };
  // The elements under ``anchor`` that look like the held one (see heldFocus).
  const heldMatches = (anchor, held) => [...anchor.querySelectorAll(held.tag)]
    .filter((node) => node.getAttribute("href") === held.href && node.textContent === held.text);
  // Focus the fresh element matching ``held`` without scrolling, unless
  // focus already moved somewhere still on the page. An element that cannot
  // be found again at the same place is not replaced by another copy.
  const refocusHeld = (held) => {
    if (!held || (document.activeElement && document.activeElement !== document.body)) return;
    const anchor = held.id ? null : document.getElementById(held.anchor);
    const match = held.id ? document.getElementById(held.id)
      : anchor && heldMatches(anchor, held)[held.index];
    match?.focus({preventScroll: true});
  };
  const refreshRegions = async (region, control, url, init, fallback, options, controller, form) => {
    const owner = options.owner || null;
    // A periodic re-read (options.background, #1586) never hands the page
    // to the browser: the reader did not act, so a failed read is only
    // reported back (with the answer, or null when there was none) and the
    // page stays as it is.
    const background = options.background || null;
    // Every way out below that hands the page to the browser (a full load,
    // a page shown as returned) marks it leaving, so no box resubmits, and
    // marks a box's state as failed.
    const leave = (act) => {
      if (background) {
        region.removeAttribute("aria-busy");
        background.failed(response || null);
        return;
      }
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
        headers: {...init.headers, "X-Requested-With": "fetch"},
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
      if (unloading && !form && !background) return;
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
    // A refused POST answered as JSON is shown in place beside its control
    // (showRefusal), never written as the page; the page and focus stay.
    // Any other answer removes such a line an earlier answer left.
    const noteKey = owner || (control instanceof Element && control.closest("form")) || control;
    if (refused && /^application\/json\b/i.test(response.headers.get("Content-Type") || "")) {
      region.removeAttribute("aria-busy");
      if (isBox(control)) failedState.set(control, sentState.get(control));
      showRefusal(noteKey, control, response.status, text);
      // Focus is on the control, as after a swapped refusal with no
      // summary; a click does not focus a button in every engine (Safari).
      if (control instanceof HTMLElement && !(control instanceof HTMLFormElement)) {
        control.focus({preventScroll: true});
      }
      return;
    }
    clearRefusal(noteKey);
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
    // A data-in-place-moves submitter's redirect to another page with this
    // region is that region's next content, not a page elsewhere.
    const moved = !refused && !otherOrigin && response.redirected
      && control instanceof Element && control.hasAttribute("data-in-place-moves")
      && answered.pathname !== window.location.pathname && isRegion(fresh);
    const elsewhere = otherOrigin
      || (owner && !anywhere && !moved && answered.pathname !== window.location.pathname);
    if (!refused && (elsewhere || !isRegion(fresh))) {
      if (response.redirected || init.method !== "POST") {
        // The region's fragment goes along only when that page draws the
        // region too; elsewhere it would name nothing (Campaign settings'
        // dates-only Review leads to Dates and mail schedules, #532).
        const target = isRegion(fresh) ? withFragment(response.url, id) : response.url;
        leave(() => window.location.assign(target));
      } else {
        leave(() => showAsReturned(text));
      }
      return;
    }
    const summary = refused ? placeSummary(parsed, fresh) : null;
    // The answer's own address (a[data-page-address], #536), read before the
    // swaps below move its node out of the parsed answer.
    const address = parsed.querySelector("a[data-page-address][href]")?.getAttribute("href");
    // The server answered with this page: nothing is leaving any more.
    leaving = false;
    if (isBox(control)) failedState.delete(control);
    // An answer from anywhere is another page: only the named region is
    // taken from it, never its other regions, sync nodes or address.
    const only = anywhere || Boolean(owner?.hasAttribute("data-in-place-only"));
    const alsoSwapped = anywhere ? []
      : (owner?.getAttribute("data-in-place-only") || "").split(/\s+/).filter(Boolean);
    // A quiet refresh happens without the reader acting (a status that
    // settled, #869, #1354), so it must not take their focus away: a focused
    // element in a region it replaces is found again in the fresh copy.
    const held = options.quiet ? heldFocus() : null;
    swappingFor = control instanceof Element ? control : null;
    try {
      document.querySelectorAll(REGIONS).forEach((other) => {
        const copy = parsed.getElementById(other.id);
        if (only && other !== region && !alsoSwapped.includes(other.id)) return;
        if (ownedElsewhere(other, region) && !alsoSwapped.includes(other.id)) return;
        if (isRegion(copy)) swapRegion(other, copy);
      });
    } finally {
      swappingFor = null;
    }
    if (!refused) dropOrphanedSummaries();
    if (!anywhere) syncControls(parsed);
    // Clear filters (#1651) asked the server for the unfiltered view, so the
    // form takes that answer's values, as after a cross-link that sets them.
    // A Back or Forward view (options.filters, #1562) sets them too.
    if (owner?.hasAttribute("data-in-place-filters") || options.filters || options.cleared) syncFilters(parsed);
    if (!anywhere) noteFiltersShown(control, owner, Boolean(options.cleared));
    // A report's filters refused at a field (#1332) are drawn again with
    // that field marked, but the filter form keeps its element and the
    // values typed (syncControls), so the marks and messages are copied
    // onto it, as a refused Review's are; its message lines are reserved,
    // so nothing moves. The summary is already in the region
    // (placeSummary) and takes focus below, straight from the control.
    // An answer that is not a refusal clears every server mark it does
    // not repeat: the reader fixed the range at its other end.
    const filterForm = document.getElementById("table-filters");
    const freshFilters = parsed.getElementById("table-filters");
    if (!anywhere && filterForm instanceof HTMLFormElement && freshFilters) {
      if (refused) showFieldErrors(filterForm, parsed, {focus: false});
      else {
        filterForm.querySelectorAll('[data-field-error=""][id]').forEach((node) => {
          const twin = freshFilters.querySelector(`#${CSS.escape(node.id)}`);
          if (node.hasAttribute("data-field-error") && twin?.getAttribute("aria-invalid") !== "true") {
            clearFieldError(node);
          }
        });
      }
    }
    // Likewise a refused POST from an in-place form that no swap replaced
    // (Off-site backups' Test access, whose folder link the view refuses at
    // its field, #1330; its form is in no region, or in one only its own
    // control redraws): the form keeps its element and the link typed, so
    // the answer's marks and messages are copied onto it. A form a swap
    // replaced is no longer connected, and its fresh copy has them already.
    if (refused && !anywhere && owner instanceof HTMLFormElement && owner !== filterForm
      && owner.isConnected && owner.id && parsed.getElementById(owner.id)) {
      showFieldErrors(owner, parsed, {focus: false});
    }
    // Every swap and the synced hidden version are in place: a Review that
    // was applied makes its values the saved ones (the change gate, #921).
    gateChanges();
    // A GET choice belongs in the address bar, so reload, bookmarks and
    // returning to the page keep it; a POST table's private filters never
    // reach a URL. A server redirect chose the address itself (a saving
    // POST's Post/Redirect/Get answer, so a reload repeats only the GET).
    // An answer from anywhere leaves the address this page's. A POST answer
    // that draws its own address (a[data-page-address], System logs' link
    // to its filters, #536) puts that in the address bar: the server built
    // it from only the filters a link may carry, never an identifier.
    // On a page that keeps its views in history (data-in-place-views, see
    // REGIONS) the entry also records this view, unless this answer is
    // itself a Back or Forward redraw, whose entry already holds it.
    const views = !anywhere && !refused && !options.popped
      && Boolean(document.querySelector(VIEWS_FORM));
    const setAddress = (href) => {
      if (!views) {
        window.history.replaceState(window.history.state, "", href);
        return;
      }
      // Only a read's body is kept, as only a read is safe to send again:
      // the filter form or a table control (no owner) or a form marked
      // data-in-place-read (a row's Recipient). A save on the same page
      // (Sent mail's bulk Preview and Confirm) keeps none, so Back or
      // Forward to its entry draws the address by GET (#1571).
      const read = !owner || owner.hasAttribute("data-in-place-read");
      const body = read && init.method === "POST" && init.body instanceof URLSearchParams
        ? [...init.body].filter(([name]) => name !== "csrfmiddlewaretoken") : null;
      const state = {...(window.history.state || {}), inPlaceView: body};
      if (owner?.hasAttribute("data-in-place-push")) window.history.pushState(state, "", href);
      else window.history.replaceState(state, "", href);
    };
    if (!anywhere && response.redirected) {
      // A form may name one-time answer parameters (data-in-place-forget,
      // space-separated) that say what just happened, such as a notice; they
      // are dropped from the address, so a reload does not repeat them.
      const address = new URL(withFragment(response.url, id), document.baseURI);
      (owner?.getAttribute("data-in-place-forget") || "").split(/\s+/).filter(Boolean)
        .forEach((name) => address.searchParams.delete(name));
      setAddress(address.href);
    } else if (!anywhere && init.method !== "POST") {
      setAddress(options.quiet ? keepHash(url) : url);
    } else if (!anywhere && !refused && address) {
      const href = new URL(address, window.location.href);
      if (href.origin === window.location.origin && href.pathname === window.location.pathname) {
        setAddress(keepHash(href.href));
      }
    } else if (!anywhere && !refused) {
      // A follow-up queue names its remembered view by an opaque token
      // (#534), never its filters: a reload then shows the view now applied.
      const view = parsed.querySelector("[data-queue-address]")?.getAttribute("data-queue-address");
      if (view && sameOrigin(view)) window.history.replaceState(window.history.state, "", keepHash(view));
    }
    if (options.quiet && !refused) {
      refocusHeld(held);
      background?.ok();
      return;
    }
    // A refusal's summary (now in the region) takes focus, as it does on an
    // ordinary load; without one the control does, as after a success. A
    // control that is gone (a save that closed a follow-up request leaves no
    // form) or disabled cannot take focus, so the region's first heading
    // does, which names what changed; failing that, the region itself. A
    // region left empty (an acknowledged banner, the last security event)
    // has nothing to focus or say, so the page's own heading takes focus.
    const swapped = document.getElementById(id);
    if (moved) {
      // Another item is shown: its heading takes focus and is announced.
      const heading = swapped.querySelector("h1, h2, h3, h4, h5, h6") || swapped;
      heading.setAttribute("tabindex", "-1");
      heading.focus({preventScroll: true});
      heading.scrollIntoView({block: "nearest"});
      announce([control.getAttribute("data-in-place-message"), squeeze(heading.textContent)]
        .filter(Boolean).join(" "));
      return;
    }
    let target = summary || (!refused && swapped.querySelector("[data-in-place-focus]"))
      || focus(swapped);
    if (!target || target.disabled) {
      target = swapped.querySelector("h1, h2, h3, h4, h5, h6")
        || (squeeze(swapped.textContent) ? swapped : document.querySelector("main h1") || swapped);
      target.setAttribute("tabindex", "-1");
    }
    target.focus({preventScroll: true});
    // Focus without scrolling kept the reader's place; if the control itself
    // is just outside the viewport, bring it (and nothing more) into view.
    target.scrollIntoView({block: "nearest"});
    // A form marked data-in-place-spoken (Load ParishSoft data's button,
    // #1404 PR 5) says nothing on success: the region it changes reads out
    // its own new state through its live-status announcer, and a second
    // message in another live region at the same moment could cut it off.
    if (!refused) {
      if (!owner?.hasAttribute("data-in-place-spoken")) {
        const told = describeTable(document.getElementById(id), control, owner);
        announce(options.cleared ? ["Filters cleared.", told].filter(Boolean).join(" ") : told);
      }
    }
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
    // A settled status region's follow-up link (live-status-v1.js) and a
    // shown-again re-read link (data-reread-on-show, below) are only ever
    // followed by script, so their read is automatic and sends
    // PASSIVE_READ: it never extends the sign-in (UX conventions, "Passive
    // reads only", #1985). A link the reader clicks never carries it.
    const automatic = Boolean(owner?.matches("[data-live-follow], [data-reread-on-show]"));
    const init = automatic ? {method: "GET", headers: PASSIVE_READ} : {method: "GET"};
    refreshTable(region, link, link.href, init, () => window.location.assign(target),
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
  // Forms edited since their last in-place submission, by id, with the
  // element that was edited (see the in-place review below). A request that
  // starts clears its form's entry there, at the moment it is marked in
  // flight: a repeated submission (Enter pressed again while a Review runs)
  // is ignored above that point, so it never forgets an edit the running
  // request does not include.
  const editedSince = new Map();
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
    // Clear filters (#1651): the filter form sent without its filters (see
    // clearedFields). An unavailable one does nothing; admin-menu-v1.js
    // already ignores its click, and this covers any other way to submit it.
    const clearing = Boolean(event.submitter?.matches("form#table-filters [data-clear-filters]"));
    if (clearing && event.submitter.hasAttribute("data-clear-unavailable")) {
      event.preventDefault();
      return;
    }
    // A page whose filter form has no table to refresh still clears, with
    // an ordinary load of the unfiltered view; a native submission would
    // send the very filters being cleared.
    if (clearing && !region) {
      event.preventDefault();
      loadCleared(form, written, method);
      return;
    }
    if (!region || !sameOrigin(written || "")) return;
    // A form that also opens the review dialog (Off-site backups' settings,
    // #750) is in place only for a button marked data-review-skip (Test
    // access): its Save is the dialog's Review (below), never an in-place save.
    if (form.matches("form[data-review-open]") && !event.submitter?.hasAttribute("data-review-skip")) return;
    // A submitter marked data-in-place-native submits natively: Save and
    // next on the last follow-up item, whose answer is the queue (#534),
    // loads it once rather than fetching it and then loading it again.
    if (event.submitter?.hasAttribute("data-in-place-native")) return;
    event.preventDefault();
    if (inFlight.has(form)) {
      // A box changed again while its request runs applies itself after.
      if (isBox(control)) heldBoxes.add(control);
      return;
    }
    // The request is built before the form is marked in flight, so nothing
    // can fail between the two and leave the form locked.
    const action = new URL(written || "", document.baseURI);
    const fields = clearing ? clearedFields(form) : new FormData(form, event.submitter || undefined);
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
      load = clearing ? () => loadCleared(form, written, method)
        : () => HTMLFormElement.prototype.submit.call(form);
    } else {
      action.search = new URLSearchParams(fields).toString();
      init = {method: "GET"};
      // A data-in-place GET falls back to the very URL it fetched, which
      // keeps a submitter's formaction (a native re-submission would not).
      load = owner || clearing ? () => window.location.assign(action.href)
        : () => HTMLFormElement.prototype.submit.call(form);
    }
    inFlight.add(form);
    editedSince.delete(form.id);
    noteSent(form); // the change gate's record of what was sent (#921)
    const submitter = event.submitter;
    submitter?.setAttribute("aria-disabled", "true");
    // In flight, not unavailable (ui-v1.css): the busy pointer, not greyed.
    submitter?.setAttribute("aria-busy", "true");
    // A save shows its button busy, as an ordinary submission does.
    if (save && !saving) submitter?.classList.add("is-busy");
    const settle = () => {
      inFlight.delete(form);
      submitter?.removeAttribute("aria-disabled");
      submitter?.removeAttribute("aria-busy");
      submitter?.classList.remove("is-busy");
      // Clear filters' own state outlives the busy mark just removed.
      gateClear(form);
      reconcileBoxes();
    };
    refreshTable(region, control, action.href, init, load,
      {owner, form: save ? form : null, cleared: clearing})
      .finally(settle);
  }, true);
  // Back or Forward within a page that keeps its views in history
  // (VIEWS_FORM, #1562) redraws the entry's view in place: its recorded
  // POST body with the filter form's current CSRF token, or else its
  // address by GET. Quietly, as the reader did not choose a control here:
  // focus stays where it is. The filter form shows the view's filters
  // (options.filters). Should no answer come, the page reloads from its
  // address, which draws that address's view.
  window.addEventListener("popstate", (event) => {
    const filters = document.querySelector(VIEWS_FORM);
    const region = document.querySelector("[data-table-region][id]");
    if (!filters || !region) return;
    const body = event.state?.inPlaceView;
    const reload = () => window.location.reload();
    const options = {quiet: true, filters: true, popped: true};
    // A body naming an action is a save (a bulk Preview or Confirm), never
    // re-sent even if one was recorded: the address is drawn instead.
    if (Array.isArray(body) && !body.some(([name]) => name === "action")) {
      const fields = new URLSearchParams(body);
      const token = filters.querySelector("input[name=csrfmiddlewaretoken]")?.value;
      if (token) fields.set("csrfmiddlewaretoken", token);
      const action = new URL(filters.getAttribute("action") || "", document.baseURI);
      refreshTable(region, filters, action.href, {method: "POST", body: fields}, reload, options);
    } else {
      refreshTable(region, filters, window.location.href, {method: "GET"}, reload, options);
    }
  });
  // A load (a reload, or a return the back/forward cache did not keep)
  // draws the page from its address, so its entry forgets any view a POST
  // drew there: Back and Forward then agree with what this load shows.
  if (window.history.state?.inPlaceView && document.querySelector(VIEWS_FORM)) {
    window.history.replaceState({...window.history.state, inPlaceView: null}, "");
  }
  // Tabs (ui-conventions "Tabs", #1712; components/tabs.html): a tablist of
  // in-place links (role="tab", data-in-place, data-in-place-only) above
  // one panel per tab, each panel a region of its own (data-in-place-own).
  // The server draws only the chosen tab's panel (data-tab-loaded). The
  // first time another tab is chosen, its panel is fetched from the tab's
  // address and swapped in alone through refreshTable, so the request,
  // fallback, focus and announcement follow the in-place mechanism's rules;
  // a drawn panel stays until the next load, so choosing its tab again needs
  // no request. The chosen tab is the address's bare ?tab=. Choosing a tab
  // adds a history entry (a tab is navigation, not a view choice, whose
  // address is replaced), and Back or Forward shows the tab the address
  // names, quietly. Activation is manual (a click, Enter or Space), so
  // arrowing past a tab never loads its panel.
  const TAB_ROW = "[data-tabs] > [role=tablist]";
  const tabRowOf = (node) => node.closest(TAB_ROW);
  const tabsIn = (row) => [...row.querySelectorAll(":scope > [role=tab]")];
  const tabUnavailable = (tab) => tab.getAttribute("aria-disabled") === "true";
  const tabPanel = (tab) => document.getElementById(tab.getAttribute("aria-controls") || "");
  // Mark `tab` chosen in its row: aria-selected, the roving tabindex (only
  // the chosen tab is in the Tab order) and the one panel shown. The panel
  // area first keeps the height the reader's place needs (heldHeight), so a
  // shorter panel, or an empty one while it loads, never pulls the page and
  // the tab row down under the pointer (#736).
  const showTab = (row, tab) => {
    const area = row.closest("[data-tabs]").querySelector(".tabs-panels");
    if (area) area.style.minHeight = heldHeight(area);
    tabsIn(row).forEach((other) => {
      const chosen = other === tab;
      other.setAttribute("aria-selected", String(chosen));
      if (!tabUnavailable(other)) other.tabIndex = chosen ? 0 : -1;
      const panel = tabPanel(other);
      if (panel) panel.hidden = !chosen;
    });
  };
  // Choose `tab`: show it, put its address (without the panel's fragment,
  // so a reload does not scroll to it) in a new history entry, and fetch its
  // panel unless it is already drawn. `popped` is a Back or Forward: the
  // entry is already there, and the switch is quiet (focus stays where it
  // is and nothing is announced). An unavailable tab does nothing.
  const chooseTab = (tab, popped = false) => {
    const row = tabRowOf(tab);
    const panel = tabPanel(tab);
    if (!row || !panel || tabUnavailable(tab)) return;
    if (tab.getAttribute("aria-selected") === "true") {
      if (!popped) tab.focus({preventScroll: true});
      return;
    }
    // A save in flight holds every in-place control, as refreshTable does.
    if (saving) {
      announce("Still saving…");
      return;
    }
    const address = new URL(tab.href);
    address.hash = "";
    if (!popped) window.history.pushState(null, "", address.href);
    showTab(row, tab);
    if (panel.hasAttribute("data-tab-loaded")) {
      // Another tab's panel still loading is overtaken, so its answer can
      // neither show that panel nor take focus. Its region keeps no busy
      // mark: the aborted request leaves it to a newer request, and there
      // is none. Any other read in flight (a control inside a panel) is
      // left to finish, as its region is that panel's own.
      if (tableRequest && busyRegion?.matches("[role=tabpanel]:not([data-tab-loaded])")) {
        tableRequest.abort();
        busyRegion.removeAttribute("aria-busy");
      }
      // A click does not focus a link in every engine (Safari).
      if (!popped) tab.focus({preventScroll: true});
      return;
    }
    // With no usable answer the page is loaded the ordinary way. The address
    // already names the tab, and assigning it with the panel's fragment
    // would only scroll to the empty panel, so the fallback reloads.
    refreshTable(panel, tab, address.href, {method: "GET"}, () => window.location.reload(),
      {owner: tab, quiet: popped});
  };
  // A plain click on a tab, or Enter on it (a link's click), chooses it.
  // Capture phase, so the generic a[data-in-place] handler (above, which
  // listens in the bubble phase) never sees
  // it; a modified click (a new tab or window) keeps its ordinary meaning.
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey || !(event.target instanceof Element)) return;
    const tab = event.target.closest("[role=tab]");
    if (!tab || !tabRowOf(tab)) return;
    event.preventDefault();
    chooseTab(tab);
  }, true);
  // The tab row's keys (WAI-ARIA tabs, manual activation): Left and Right
  // move focus to the previous and next tab, wrapping at the ends, Home and
  // End to the first and last, and Space chooses the focused tab without
  // scrolling the page. An unavailable tab is reached too, so its tip
  // (admin-menu-v1.js) says why it cannot be opened. Moving focus scrolls
  // only the row's own box when the tab is out of its view.
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey
        || !(event.target instanceof Element)) return;
    const tab = event.target.closest("[role=tab]");
    const row = tab && tabRowOf(tab);
    if (!row) return;
    if (event.key === " ") {
      event.preventDefault();
      chooseTab(tab);
      return;
    }
    const tabs = tabsIn(row);
    const at = tabs.indexOf(tab);
    const step = {ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1}[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const next = tabs[(step + tabs.length) % tabs.length];
    next.focus({preventScroll: true});
    next.scrollIntoView({block: "nearest", inline: "nearest"});
  });
  // Back or Forward to an entry of this page shows the tab its address
  // names (the row's default when it names none), in place.
  window.addEventListener("popstate", () => {
    const row = document.querySelector(TAB_ROW);
    if (!row) return;
    const key = new URL(window.location.href).searchParams.get("tab");
    const tabs = tabsIn(row).filter((tab) => !tabUnavailable(tab));
    const tab = tabs.find((each) => each.dataset.tab === key)
      || tabs.find((each) => each.dataset.tab === row.dataset.tabsDefault) || tabs[0];
    if (tab) chooseTab(tab, true);
  });
  // Clear filters (#1651, UX conventions "Clear filters"): the button
  // beside a filter form's apply button (components/filter-actions.html)
  // puts every filter back to the page's default in place. The server owns
  // the defaults, so Clear sends the form without its filters and the form
  // then takes the answer's values (syncFilters, above), never blank fields.
  //
  // What is sent: the form's hidden fields (the CSRF token, the browser's
  // zone, the table's sort and size, a view choice such as Production or
  // Testing), except those marked data-filter, which carry a filter (System
  // logs' submitted-form mark, a one-send view), and every visible control
  // marked data-view: a view choice that switches the data shown (the
  // directory's Responses shown, Production or Testing) is a view, not a
  // filter, so Clear keeps it. No other visible field is sent, so neither
  // is a private filter. An unticked box or radio sends nothing, as in a
  // native submission.
  const keptField = (field) => field.name && !field.disabled
    && (field.hasAttribute("data-view")
      || (field instanceof HTMLInputElement && field.type === "hidden" && !field.hasAttribute("data-filter")));
  const clearedFields = (form) => {
    const fields = new FormData();
    [...form.elements].forEach((field) => {
      if (keptField(field) && !((field.type === "checkbox" || field.type === "radio") && !field.checked)) {
        fields.append(field.name, field.value);
      }
    });
    return fields;
  };
  // The ordinary load of the unfiltered view, when it cannot be shown in
  // place: the address with only the kept fields, or for a POST form the
  // same fields posted from a detached form (the filter form itself would
  // send its filters).
  const loadCleared = (form, written, method) => {
    const action = new URL(written || "", document.baseURI);
    const fields = clearedFields(form);
    if (method !== "post") {
      action.search = new URLSearchParams(fields).toString();
      window.location.assign(action.href);
      return;
    }
    const sent = document.createElement("form");
    sent.method = "post";
    sent.action = action.href;
    sent.hidden = true;
    fields.forEach((value, name) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      sent.append(input);
    });
    document.body.append(sent);
    HTMLFormElement.prototype.submit.call(sent);
  };
  // A filter form's filters as one comparable string: each visible field
  // (ticked or not for a box, else its value) and each data-filter hidden
  // field, in order; a data-view choice is not a filter. ``rendered``
  // reads what the server drew (the default* properties) rather than what
  // the browser now shows, which a reload may have restored from an earlier
  // visit.
  const filterValues = (form, {rendered = false} = {}) => JSON.stringify([...form.elements]
    .filter((field) => field.name && !isSubmit(field) && !field.matches("[data-browser-zone], [data-view]")
      && (field.type !== "hidden" || field.hasAttribute("data-filter")))
    .map((field) => {
      if (field.type === "checkbox" || field.type === "radio") {
        return [field.name, rendered ? field.defaultChecked : field.checked];
      }
      if (rendered && field instanceof HTMLSelectElement) {
        const options = [...field.options];
        return [field.name, (options.find((option) => option.defaultSelected) || options[0])?.value ?? ""];
      }
      return [field.name, rendered && "defaultValue" in field ? field.defaultValue : field.value];
    }));
  // Whether this page was opened with no filter in its address, so what the
  // server drew is the page's default. Only the form's kept hidden fields
  // (sort, size, zone, a view choice) and a table's page number (its size
  // field's prefix + "page", web/tables.py) are not filters. A POST
  // report's filters never reach the address, and its plain address shows
  // its default view.
  const openedUnfiltered = (form) => {
    const kept = new Set([...clearedFields(form).keys()]);
    [...kept].filter((name) => name.endsWith("size"))
      .forEach((name) => kept.add(`${name.slice(0, -"size".length)}page`));
    return [...new URLSearchParams(window.location.search).keys()].every((name) => kept.has(name));
  };
  // Per filter form: ``applied``, the filters of the list shown, and
  // ``defaults``, the page's default filters, or null while unknown (a
  // page opened already filtered, until a Clear shows them).
  const filterState = new WeakMap();
  // Clear filters is unavailable while it has nothing to do: the defaults
  // are known, the list shown uses them and every field shows them. It is
  // then aria-disabled (so it keeps focus and its place) and described by
  // its tip, which admin-menu-v1.js shows on hover, focus or tap; while
  // available it names no tip, so the tip never shows. A request in flight
  // keeps its own aria-disabled mark until it settles (settle, above).
  const gateClear = (form) => {
    const button = form instanceof HTMLFormElement && form.querySelector("[data-clear-filters]");
    const state = filterState.get(form);
    if (!button || !state) return;
    const idle = state.defaults !== null && state.applied === state.defaults
      && filterValues(form) === state.defaults;
    const tip = button.parentElement.querySelector(".admin-tip[id]");
    button.toggleAttribute("data-clear-unavailable", idle);
    if (idle) {
      button.setAttribute("aria-disabled", "true");
      if (tip) button.setAttribute("aria-describedby", tip.id);
      return;
    }
    if (!inFlight.has(form)) button.removeAttribute("aria-disabled");
    button.removeAttribute("aria-describedby");
    tip?.classList.remove("is-shown", "is-tapped");
  };
  // After an in-place answer: a filter submission (Apply, Clear, or a
  // control that sets the filters from outside the form, such as a System
  // logs cross-link) changed the list's filters to what the form now shows;
  // a Clear's answer is the page's default. Sorting or paging changes no
  // filter, but every answer checks Clear filters again.
  const noteFiltersShown = (control, owner, cleared) => {
    const form = document.getElementById("table-filters");
    const state = filterState.get(form);
    if (!state) return;
    const fromForm = control === form || (control instanceof Element && control.closest("form") === form);
    if (fromForm || cleared || owner?.hasAttribute("data-in-place-filters")) {
      state.applied = filterValues(form);
      if (cleared) state.defaults = state.applied;
    }
    gateClear(form);
  };
  {
    const form = document.getElementById("table-filters");
    if (form instanceof HTMLFormElement && form.querySelector("[data-clear-filters]")) {
      const rendered = filterValues(form, {rendered: true});
      filterState.set(form, {applied: rendered, defaults: openedUnfiltered(form) ? rendered : null});
      gateClear(form);
      // Capture phase: a script's own change events (syncFilters) do not
      // bubble, and every edit, typed or chosen, counts at once.
      ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
        if (event.target === form || event.target?.form === form) gateClear(form);
      }, true));
      // Values a back/forward visit restores are checked again.
      window.addEventListener("pageshow", () => gateClear(form));
    }
  }
  // Where a filter form's Apply filters and Clear filters sit
  // (ui-conventions "Clear filters", #1776): the first of three steps that
  // fits, to take the least vertical space above the table. 1 "beside":
  // side by side on the fields' last row; 2 "stacked": Clear under Apply on
  // that row; 3 "row": side by side on a row of their own. A step fits when
  // the fields' last row keeps its place and height and the pair stays
  // inside it, both buttons on one line for step 1. CSS cannot tell
  // whether a stacked pair would make the row taller, nor place the pair in
  // a Filters panel's grid by that test, so the pair is measured: first on
  // its own row, which shows the fields' last row without it (every field
  // before the pair whose box overlaps the last one's line), then in each
  // step in turn. Only the pair's data-filter-place (and, in a grid, its
  // data-filter-wide) changes, before the browser paints, so nothing is
  // seen to move.
  const FILTER_PAIR = "form#table-filters .filter-actions";
  const shown = (node) => node.getClientRects().length > 0;
  const placeFilterPair = (pair) => {
    // A closed Filters panel draws nothing to measure; the panel's toggle
    // (below) places the pair when it opens.
    if (!shown(pair)) return;
    // The fields before the pair, in its own box (the form, a filter bar
    // or Logs' row): a hint, note or message line is not a field.
    const fields = [];
    for (let node = pair.previousElementSibling; node; node = node.previousElementSibling) {
      if (shown(node) && !node.matches("p, [data-complete-hint], .field-error-line, .zone-note")) fields.push(node);
    }
    pair.dataset.filterPlace = "row";
    pair.removeAttribute("data-filter-wide");
    if (!fields.length) return;
    // The fields' last row: the last field's line, widened to every field
    // whose box shares it (a taller field beside it sets the row's height).
    const band = () => {
      const last = fields[0].getBoundingClientRect();
      const row = fields.map((node) => node.getBoundingClientRect())
        .filter((box) => box.top < last.bottom - 1 && box.bottom > last.top + 1);
      return {top: Math.min(...row.map((box) => box.top)), bottom: Math.max(...row.map((box) => box.bottom))};
    };
    const alone = band();
    const fits = (oneLine) => {
      const now = band();
      const box = pair.getBoundingClientRect();
      const [apply, clear] = pair.querySelectorAll(":scope > button, :scope > .filter-clear");
      return Math.abs(now.top - alone.top) < 1 && Math.abs(now.bottom - alone.bottom) < 1
        && box.top >= alone.top - 1 && box.bottom <= alone.bottom + 1
        && pair.scrollWidth <= pair.clientWidth + 1
        && (!oneLine || !apply || !clear
          || Math.abs(apply.getBoundingClientRect().top - clear.getBoundingClientRect().top) < 1);
    };
    // In a Filters panel's grid, side by side may need two of the last
    // row's empty cells: one cell (14rem and up) holds both buttons only
    // from about 288 px, as at 1280 px.
    pair.dataset.filterPlace = "beside";
    if (fits(true)) return;
    const grid = getComputedStyle(pair.parentElement);
    if (grid.display === "grid" && grid.gridTemplateColumns.trim().split(/\s+/).length > 1) {
      pair.setAttribute("data-filter-wide", "");
      if (fits(true)) return;
      pair.removeAttribute("data-filter-wide");
    }
    pair.dataset.filterPlace = "stacked";
    if (fits(false)) return;
    pair.dataset.filterPlace = "row";
  };
  {
    // Each pair is placed at load, once web fonts have loaded (their widths
    // decide what fits), when the window's width changes, when a Filters
    // panel opens (a closed one draws nothing to measure) and after an
    // in-place swap that redraws a form. Window resize and the panel's
    // toggle, not a ResizeObserver on the form: the placement changes the
    // form's height, and an observer that changes layout under itself makes
    // WebKit report a "ResizeObserver loop" error, which tests treat as a
    // script error. Resizes are taken once a frame.
    const placeAll = (root = document) => {
      const pairs = root.matches?.(FILTER_PAIR) ? [root] : [...root.querySelectorAll(FILTER_PAIR)];
      pairs.forEach(placeFilterPair);
    };
    placeAll();
    document.fonts?.ready.then(() => placeAll());
    let width = window.innerWidth;
    let pending = false;
    window.addEventListener("resize", () => {
      if (pending || window.innerWidth === width) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        width = window.innerWidth;
        placeAll();
      });
    });
    // toggle does not bubble, so it is caught on the way down.
    document.addEventListener("toggle", (event) => {
      if (event.target instanceof HTMLDetailsElement && event.target.open) placeAll(event.target);
    }, true);
    document.addEventListener("parishkit:swap", (event) => {
      if (event.target instanceof Element) placeAll(event.target);
    });
  }
  // An in-place review (Sent mail's bulk review, #382; Past campaigns'
  // import, #1018) shows what its form's last Review would change, in an
  // element marked data-review-of="<form id>", with Apply. Editing that form again makes
  // the review out of date, and Apply would still apply the reviewed values,
  // so the review is withdrawn at once: its Apply is removed, the rest is
  // greyed out (data-review-stale), and its data-review-note line says to
  // review again. The review stays in place, and its region keeps the
  // height the reader's place needs (heldHeight), so a reader scrolled down
  // to the review at the foot of the page never sees the form pulled under
  // the pointer (#736). The server's signed preview stays the authority for
  // what is applied. An edit made while Review is still in flight is not in
  // the review that answer brings, so that review is withdrawn as soon as
  // it arrives. Forms are noted by id (editedSince, above). A form that is
  // its own region is replaced by an answer that draws it (a table's
  // selection form, or the quiet refresh once a change is applied). Edits
  // typed into it since it was sent are then gone, so a line says they were
  // not kept: at the top of the region its action names, else after it. A
  // review dialog's Review clears the mark (prepareReview), since what was
  // typed is in that review and the form cannot change while it is shown.
  const withdraw = (review) => {
    // Hold the review region (else the review) at what the reader's place
    // needs, which also lets go of any hold an earlier swap no longer needs.
    const keep = review.closest("[data-keep-height]") || review;
    keep.style.minHeight = heldHeight(keep);
    review.removeAttribute("data-review-of");
    review.setAttribute("data-review-stale", "");
    review.querySelectorAll("form").forEach((apply) => apply.remove());
    let note = review.querySelector("[data-review-note]");
    if (!note) {
      note = document.createElement("p");
      note.setAttribute("data-review-note", "");
      review.append(note);
    }
    note.className = "notice";
    note.setAttribute("role", "status");
    // A review may say this in its own words (data-stale-note, Outgoing
    // mail's bulk preview, #382).
    note.textContent = review.dataset.staleNote
      || "You changed the settings after reviewing them. "
      + "Choose Review changes again to see what will change.";
  };
  const withdrawReview = (event) => {
    const form = event.target instanceof Element ? event.target.closest("form") : null;
    if (!form?.id) return;
    editedSince.set(form.id, form);
    document.querySelectorAll(`[data-review-of="${CSS.escape(form.id)}"]`).forEach(withdraw);
  };
  document.addEventListener("input", withdrawReview);
  document.addEventListener("change", withdrawReview);
  // A table's Select all / Clear selection button ticks its rows from
  // script, which fires no change event, so it withdraws a review of the
  // row selection itself (#750).
  document.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("[data-select-all-button]")) {
      withdrawReview(event);
    }
  });
  document.addEventListener("parishkit:swap", (event) => {
    if (!(event.target instanceof Element)) return;
    // Regions are swapped in page order, so a form's own region (above its
    // review region) is swapped first and its old element has left the page.
    within(event.target, "form[id]").forEach((fresh) => {
      const edited = editedSince.get(fresh.id);
      if (!edited || edited.isConnected) return;
      editedSince.delete(fresh.id);
      // A selection table (Hosted files, Schedule settings) is
      // redrawn with the ticks of every row it still shows (swapRegion), and
      // its selection is per page. When the reader re-sorts, re-pages or
      // re-filters it, a tick on a row no longer shown goes as they asked,
      // so nothing needs saying (#750). Any other answer (the quiet refresh
      // once a change is applied) says so only if a ticked row is gone.
      if (fresh.matches("[data-select-table]")) {
        if (swappingFor?.closest("[data-table-region], form#table-filters")) return;
        const shown = new Set([...fresh.querySelectorAll("input[data-select-row]")]
          .map((box) => box.value));
        const ticked = [...edited.querySelectorAll("input[data-select-row]:checked")];
        if (ticked.every((box) => shown.has(box.value))) return;
      }
      // The line goes at the top of the region the form's action names (its
      // review region, below it), never above the form: a line added there
      // would move the form under the reader's caret (#736). That region may
      // be swapped later in this same answer, so the line is added once the
      // swaps are done (they run synchronously, before any microtask).
      const target = (fresh.getAttribute("action") || "").split("#")[1];
      queueMicrotask(() => {
        const region = target ? document.getElementById(target) : null;
        document.querySelectorAll("[data-edits-lost]").forEach((old) => old.remove());
        const note = document.createElement("p");
        note.className = "notice";
        note.setAttribute("role", "note");
        note.setAttribute("data-edits-lost", "");
        note.textContent = "Changes you typed while this page was updating were not kept. "
          + "Check the form before you continue.";
        if (region) region.prepend(note);
        else fresh.after(note);
      });
    });
    within(event.target, "[data-review-of]").forEach((review) => {
      const id = review.getAttribute("data-review-of");
      const edited = editedSince.get(id);
      if (!edited) return;
      editedSince.delete(id);
      withdraw(review);
    });
  });
  // Review only what changed (#921): a settings form marked
  // data-require-change="<version field>" keeps its submit buttons (Review
  // changes) disabled while its enabled controls all hold the saved values,
  // with its [data-unchanged-hint] line after them saying why; restoring the
  // saved values disables Review again. The hint always keeps its space
  // (data-idle hides it with visibility), so it never moves the page (#736).
  // The server still answers an unchanged Review with its refusal.
  //
  // The saved values are the form's server-drawn defaults (defaultValue,
  // defaultChecked, defaultSelected), never its live values at load, which a
  // browser may have restored from an earlier visit. They are kept by form
  // id for each version the hidden version field names, so a form an answer
  // redraws at the same version keeps the saved values it had. When the
  // version moves on because this page's Apply was applied, and the answer
  // kept the very form element the Apply was sent from (a data-table-sync
  // form, which keeps the reader's typing and takes only the new version),
  // the saved values become those that Review sent. Any other new version,
  // such as a form redrawn after an applied change, takes the form's new
  // defaults. A control drawn with a proposal that is not saved yet (the
  // date-change review's proposed dates, #750) names its saved value in
  // data-saved-value, which stands in for its default. A control the page
  // script added inside a [data-change-blank] row (a blank added schedule)
  // that still holds its own default is no change. A control marked
  // data-change-ignore is no change, like a disabled one: Edit scheduled
  // email's "Edit this schedule's email" (#446) only shows the email's
  // fields, which then count, so ticking it without editing the email is no
  // change; and plain text generated from the HTML is not sent (the
  // plain-text panel marks it while generating), so text regenerated
  // differently from a saved email's is no change either. The mark can come
  // and go after the saved values are taken, so such a control keeps its
  // key and only its value stops counting. The gate runs on every edit,
  // after every in-place answer (see refresh) and on pageshow.
  const savedStates = new Map(); // form id → {version, state} it was saved at
  const reviewSent = new Map(); // form id → the state its last Review sent
  // form id → {form, state}: the form element when its Apply was sent, and
  // the state the Review behind that Apply sent.
  const reviewApplied = new Map();
  // The form's state as a Map of "name#n" (the nth control of that name) to
  // {value, blank, disabled, hidden}: its defaults or its live values; text
  // is trimmed as the server trims it. :disabled, not the disabled property,
  // so a control inside a disabled fieldset (which is not sent either)
  // counts as disabled.
  //
  // A hidden field counts only when it is marked data-change-setting: it
  // carries a saved setting the page shows only as words (ParishSoft's
  // stored quick updates, #1122) or a saved row's identity, so removing it,
  // or a script rewriting it, is a change (#1334 review). Every other hidden
  // field carries none and stays out: the CSRF token, the version field the
  // gate keys by, a formset's row counts, a one-time request key or signed
  // intent, the browser's zone, a table's filters, size and sort (a redraw
  // at other view settings is no change, #1334 re-review), and the fields a
  // script writes from the visible controls the reader changed (a repeat
  // rule's dates), which count themselves. Opt-in, so a new hidden field is
  // no change until it is marked. A hidden field's default is its live
  // value (the browser keeps the two as one), so its saved value is what
  // the gate read from the form element it was drawn in (savedState). A
  // marked hidden field a script adds is no change by itself either.
  const controlValue = (node, defaults) => {
    const saved = defaults ? node.dataset.savedValue : undefined;
    if (node.type === "checkbox" || node.type === "radio") {
      return String(defaults ? node.defaultChecked : node.checked);
    }
    if (node instanceof HTMLSelectElement) {
      if (saved !== undefined) return JSON.stringify([saved]);
      const options = [...node.options];
      let chosen = options.filter((option) => (defaults ? option.defaultSelected : option.selected));
      // A single select drawn with no option marked selected shows its first.
      if (defaults && !node.multiple && !chosen.length) chosen = options.slice(0, 1);
      return JSON.stringify(chosen.map((option) => option.value));
    }
    return (saved ?? (defaults ? node.defaultValue : node.value)).trim();
  };
  const formState = (form, defaults) => {
    const state = new Map();
    const seen = new Map();
    [...form.elements].forEach((node) => {
      if (!node.name || !(node instanceof HTMLInputElement || node instanceof HTMLSelectElement
        || node instanceof HTMLTextAreaElement)) return;
      if (["submit", "button", "reset", "image", "file"].includes(node.type)) return;
      const hidden = node.type === "hidden";
      if (hidden && !node.hasAttribute("data-change-setting")) return;
      // A presentation choice (the Basic / Advanced toggle and the Basic
      // panel that drives the Advanced fields, #1121) is not a change to
      // review: the configuration it writes is.
      if (node.closest("[data-not-a-change]")) return;
      const index = seen.get(node.name) || 0;
      seen.set(node.name, index + 1);
      const value = controlValue(node, defaults);
      const blank = !defaults && Boolean(node.closest("[data-change-blank]"))
        && value === controlValue(node, true);
      const disabled = node.matches(":disabled") || node.hasAttribute("data-change-ignore");
      state.set(`${node.name}#${index}`, {value, blank, disabled, hidden});
    });
    return state;
  };
  // Whether the form's enabled controls hold exactly the saved values, with
  // no control added or removed (a new or deleted option row; an added
  // blank row or hidden field, above, is no change). A control
  // that is disabled (a hidden module's field) is not sent, so its value
  // does not count; the control that hid it differs instead.
  // changedNames lists the names of the controls that differ (#1330: the
  // live Ministries must change alone, below in gateComplete).
  const changedNames = (form, saved) => {
    const now = formState(form, false);
    const keys = [...saved.keys()].filter((key) => !now.has(key));
    now.forEach(({value, blank, disabled, hidden}, key) => {
      if (!disabled && !(saved.has(key) ? saved.get(key).value === value : blank || hidden)) {
        keys.push(key);
      }
    });
    return new Set(keys.map((key) => key.replace(/#\d+$/, "")));
  };
  // Campaign images' slot upload forms in the campaign wizard
  // (data-wizard-upload) whose enabled file field holds a chosen image.
  const chosenUploads = () => [...document.querySelectorAll("form[data-wizard-upload]")]
    .filter((upload) => [...upload.querySelectorAll('input[type="file"]')]
      .some((node) => !node.matches(":disabled") && node.files?.length > 0));
  // A Campaign setup step's Continue (#1404, campaign-step-actions.html)
  // records the step kept as is, so it waits while the page holds a change:
  // a change-gated form that differs from its saved values. The page's own
  // Review stays the way on. Its hint after the row keeps its
  // line (data-idle hides it with visibility), and names the button only
  // while it is shown.
  // On a step that saves directly (Campaign settings, Share options, #1404
  // PR 4) a change does not hold Continue: the row's one primary switches
  // to Save and continue, the page form's own submit button in the same
  // grid cell, and back to Continue once the form holds the saved values
  // again. The one not offered is data-step-inactive (hidden with
  // visibility, so the cell keeps its width) and inert. Save and continue
  // is described by the form's complete hint, as the page's Save was.
  // On Campaign images (data-direct-uploads, #1404 PR 4c) the change is a
  // chosen image in any slot's upload form (chosenUploads, below).
  const gateStepContinue = () => {
    const form = document.querySelector("form[data-campaign-continue]");
    if (!form) return;
    const save = document.querySelector("[data-campaign-save]");
    const direct = save?.form;
    if (direct?.matches("[data-direct-save]")) {
      const changed = direct.hasAttribute("data-direct-uploads")
        ? chosenUploads().length > 0 : direct.hasAttribute("data-holds-change");
      form.toggleAttribute("data-step-inactive", changed);
      form.inert = changed;
      save.toggleAttribute("data-step-inactive", !changed);
      save.inert = !changed;
      const hint = direct.querySelector("[data-complete-hint][id]");
      if (hint) save.setAttribute("aria-describedby", hint.id);
      return;
    }
    const held = Boolean(document.querySelector("form[data-require-change][data-holds-change]"));
    const hint = document.getElementById("campaign-continue-hint");
    hint?.toggleAttribute("data-idle", !held);
    form.querySelectorAll("button[type=submit]").forEach((node) => {
      if (held) holdButton(node, "data-change-gated");
      else releaseButton(node, "data-change-gated");
      if (held && hint) node.setAttribute("aria-describedby", hint.id);
      else node.removeAttribute("aria-describedby");
    });
  };
  const matchesSaved = (form, saved) => !changedNames(form, saved).size;
  // The saved state of a data-require-change form at its current version.
  // A form element drawn anew at the same version (a table redrawn at other
  // view settings, #1334 re-review) takes its hidden settings from the new
  // element, which are what the server drew: a hidden field's default is
  // its live value, so the old element's could differ only by a script's
  // edit there, which the redraw replaced. Its visible controls keep the
  // cached saved values, so a redraw that keeps the reader's typing still
  // shows it as a change.
  const savedState = (form) => {
    const field = form.elements.namedItem(form.dataset.requireChange);
    const version = field instanceof HTMLInputElement ? field.value : "";
    let saved = savedStates.get(form.id);
    if (!saved || saved.version !== version) {
      const applied = saved && reviewApplied.get(form.id);
      const kept = applied?.form === form;
      saved = {version, form, state: kept ? applied.state : formState(form, true)};
      savedStates.set(form.id, saved);
      reviewApplied.delete(form.id);
    } else if (saved.form !== form) {
      const state = new Map([...saved.state].filter(([, entry]) => !entry.hidden));
      formState(form, true).forEach((entry, key) => entry.hidden && state.set(key, entry));
      saved = {version, form, state};
      savedStates.set(form.id, saved);
    }
    return saved.state;
  };
  const gateChange = (form) => {
    if (!(form instanceof HTMLFormElement) || !form.id || !form.hasAttribute("data-require-change")) {
      return;
    }
    const unchanged = matchesSaved(form, savedState(form));
    form.toggleAttribute("data-holds-change", !unchanged);
    gateStepContinue();
    const hint = form.querySelector("[data-unchanged-hint]");
    // The hint's id comes from its form's, so two gated forms on one page
    // never describe their buttons with the same line.
    if (hint) hint.id = `${form.id}-unchanged-hint`;
    hint?.toggleAttribute("data-idle", !unchanged);
    // isSubmit, not submitControls (defined below, after this first runs).
    // A button marked data-change-exempt acts on what is saved as well as
    // on an edit (Off-site backups' Test access, #1330), so it never waits.
    [...form.elements].filter(isSubmit).forEach((node) => {
      if (node.formNoValidate || node.hasAttribute("data-change-exempt")) return;
      if (unchanged) holdButton(node, "data-change-gated");
      else releaseButton(node, "data-change-gated");
      if (!hint?.id) return;
      const described = (node.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && id !== hint.id);
      if (unchanged) described.push(hint.id);
      if (described.length) node.setAttribute("aria-describedby", described.join(" "));
      else node.removeAttribute("aria-describedby");
    });
  };
  const gateChanges = () => {
    document.querySelectorAll("form[data-require-change]").forEach(gateChange);
    // A page without a change-gated form (Campaign images) still gates its
    // Continue, and a redraw that empties its file inputs releases it.
    gateStepContinue();
  };
  // Campaign setup's Back never saves (ui-conventions Wizards rule 6,
  // #1942): while the page holds a change, Back (or a sub-page's Return to
  // <step>, the same data-campaign-step-back link) and the stepper's step
  // links first ask in the shared confirmation dialog
  // (campaign-step-actions.html, #campaign-step-discard). A change is a
  // change-gated form that differs from its saved values (the gate's
  // data-holds-change), a chosen image (Campaign images), or a directly
  // saved form without a gate (an editor offering to save the default text
  // as it is) that differs from what it was drawn with. Discard and go back
  // follows the link, posting nothing; Stay, Escape or closing keeps the
  // typed values and returns focus to the link. An unchanged page, and a
  // click that opens a new tab or window (which discards nothing), are left
  // to the browser. Leave campaign setup and the Admin menu leave as always.
  const stepHoldsChange = () => chosenUploads().length > 0
    || [...document.querySelectorAll("form[data-require-change], form[data-direct-save]")]
      .some((form) => (form.hasAttribute("data-require-change")
        ? form.hasAttribute("data-holds-change")
        : changedNames(form, formState(form, true)).size > 0));
  const askBeforeDiscard = (dialog, link) => {
    wireDialog(dialog);
    if (!dialog.parishDiscard) {
      dialog.parishDiscard = {link, leaving: false};
      dialog.querySelector("[data-confirm-accept]")?.addEventListener("click", () => {
        const state = dialog.parishDiscard;
        state.leaving = true;
        dialog.close();
        window.location.assign(state.link.href);
      });
      dialog.addEventListener("close", () => {
        const state = dialog.parishDiscard;
        // Stay, Escape or closing: nothing changes, and focus goes back to
        // the link, for browsers that do not restore it themselves.
        if (!state.leaving && state.link.isConnected) state.link.focus();
      });
    }
    dialog.parishDiscard.link = link;
    dialog.parishDiscard.leaving = false;
    dialog.showModal();
    // The safe choice takes focus first.
    dialog.querySelector("[data-confirm-cancel]")?.focus();
  };
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
      || event.shiftKey || event.altKey || !(event.target instanceof Element)) return;
    const link = event.target.closest(
      "a[href][data-campaign-step-back], #campaign-stepper .setup-track a[href], #campaign-stepper .setup-steps a[href]");
    const dialog = document.getElementById("campaign-step-discard");
    if (!link || !(dialog instanceof HTMLDialogElement) || !stepHoldsChange()) return;
    event.preventDefault();
    askBeforeDiscard(dialog, link);
  });
  // Note what each Review sends, and which Review an Apply applies: an Apply
  // sits in the review of its form (data-review-of names it). The in-place
  // handler (above) calls this only once a request really starts, so a
  // repeated submission it ignores while one is in flight (Enter pressed
  // again after an edit) is never taken for what was sent.
  const noteSent = (form) => {
    if (form.id && form.hasAttribute("data-require-change")) {
      reviewSent.set(form.id, formState(form, false));
    }
    const of = form.closest("[data-review-of]")?.getAttribute("data-review-of");
    if (of && reviewSent.has(of)) {
      reviewApplied.set(of, {form: document.getElementById(of), state: reviewSent.get(of)});
    }
  };
  // Listening on the document, after each form's own listeners, keeps the
  // input that just happened in view; the other gates share the buttons'
  // bookkeeping, so the order does not decide what is enabled.
  // A removed new schedule row (schedules-changed) changes the form too.
  ["input", "change", "schedules-changed"].forEach((type) => document.addEventListener(type, (event) => {
    if (event.target instanceof Element) gateChange(event.target.closest("form"));
    if (event.target instanceof HTMLInputElement && event.target.type === "file") gateStepContinue();
  }));
  // The Pages and emails visual editor's toolbar and paste write the HTML
  // source with no input event; their stewardship:html-changed does not
  // bubble, so it is caught on the way down (#1330).
  document.addEventListener("stewardship:html-changed", (event) => {
    if (event.target instanceof HTMLFormElement) gateChange(event.target);
  }, true);
  // A page script that adds or removes a gated form's rows fires no input
  // event (ParishSoft's Remove quick updates and a rule's Remove, #1334
  // review), so the gate also runs, once per form, after the nodes inside
  // a gated form change. The gate itself only sets attributes, which this
  // does not watch, so it never wakes itself.
  new MutationObserver((records) => {
    new Set(records.map(({target}) => target instanceof Element
      && target.closest("form[data-require-change]"))).forEach((form) => form && gateChange(form));
  }).observe(document.body, {childList: true, subtree: true});
  window.addEventListener("pageshow", gateChanges);
  gateChanges();
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
  // Forms marked data-submit-waits (long server steps) can legitimately wait
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
      if (!form.contains(node)) node.removeAttribute("aria-busy");
    });
    if (!submitting.size) busyStatus.textContent = "";
  };
  const waitNote = (form, submitter, text) => {
    let note = waitNotes.get(form);
    if (!note) {
      note = document.createElement("p");
      note.className = "help submit-wait";
      note.setAttribute("role", "status");
      // An export row's submit never reaches here: exports download in
      // place (below, #1778), with their note in the row's own status line.
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
    submitControls(form).forEach((node) => {
      node.setAttribute("aria-disabled", "true");
      // A button tied in by form="…" sits outside the form, so the form's
      // aria-busy does not reach it; mark it busy itself (cleared on release).
      if (!form.contains(node)) node.setAttribute("aria-busy", "true");
    });
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

  // Every Admin export downloads in place (#1778; ui-conventions "Downloads
  // happen in place"): a submit from an export row (.export-actions) or a
  // form[data-export-in-place] (the campaign archive) is posted with fetch,
  // and the page never leaves. The answer is one of:
  // - the file itself (an in-memory export such as Limitations), saved at
  //   once;
  // - a redirect to the export's status address (a queued export), which is
  //   then read passively at the live-status pace (live-status-v1.js) until
  //   its [data-export-state] says ready; its Download form is then posted
  //   the same way and the file saved;
  // - a refusal, said in the row's reserved status line
  //   ([data-export-status]), in the server's own words where it gives some.
  // The clicked button is busy and the form's other buttons wait until the
  // file arrives or the export is refused; after five seconds the line says
  // the export is still being worked on (a queued export's own status
  // sentence, such as why it has not started, #340). The server is
  // unchanged: freezing, auditing, limits, windows and one-use download
  // grants are its own.
  const EXPORT_PACE = [2000, 2000, 3000, 4000, 6000, 8000, 10000];
  // A queued export is usually ready in seconds. Past this the page stops
  // watching, so a stuck export never holds the buttons for good.
  const EXPORT_GIVE_UP_MS = 30 * 60 * 1000;
  // How long one request may take before it counts as unanswered: the
  // start may wait behind a ParishSoft refresh's last step (up to two
  // minutes), a status read is a quick passive page, and a download carries
  // the file.
  const EXPORT_START_MS = 180000;
  const EXPORT_READ_MS = 30000;
  const EXPORT_DOWNLOAD_MS = 120000;
  // A ready file's Download answered busy (503) is posted again at the pace
  // this many times; trying the button again would start a new export.
  const EXPORT_DOWNLOAD_TRIES = 5;
  const EXPORT_TEXT = {
    waiting: "Still working… If a ParishSoft refresh is finishing, this can take up to two minutes. Keep this page open.",
    failed: "The file could not be created. Try again; if it fails again, check System logs or ask your system administrator.",
    gone: "This export ended before it was downloaded. Try again.",
    cancelled: "This export was cancelled. Try again.",
    signedOut: "Couldn't check on the file: your sign-in may have ended. Reload the page or sign in again.",
    slow: "The file is taking longer than usual. Try again later; if it keeps happening, ask your system administrator.",
    noAnswer: "No response from the server yet. Your export may still have been started; try again in a few minutes.",
    unreachable: "The server could not be reached. Check your connection, then try again.",
    empty: "No file came back. Reload the page, then try again.",
  };
  // The running exports, keyed by their form's id (the form itself when it
  // has none), each with the clicked button's {name, value}. An in-place
  // redraw may bring in a fresh copy of a running export's form (Campaign
  // comparison's Compare on day) or of its buttons (Ministry requests'
  // synced row), which must show the export busy and must not start a
  // second one beside it.
  const runningExports = new Map();
  const exportKey = (form) => form.id || form;
  // fetch() that gives up after ``ms`` (an AbortError, as a lost answer).
  const fetchWithin = (url, init, ms) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), ms);
    return fetch(url, {...init, credentials: "same-origin", signal: controller.signal})
      .finally(() => window.clearTimeout(timer));
  };
  // The file name a download answer gives (RFC 6266's filename* first, as
  // web/exports.py writes both), or a plain fallback.
  const downloadName = (header) => {
    const value = header || "";
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(value);
    if (encoded) {
      try { return decodeURIComponent(encoded[1]); } catch { /* fall through */ }
    }
    return /filename="([^"]+)"/i.exec(value)?.[1] || "export";
  };
  // Whether an answer is the file itself (web/exports.download_headers).
  const isDownload = (response) => response.ok
    && /^\s*attachment\b/i.test(response.headers.get("Content-Disposition") || "");
  // Whether an unanswered or failed read may be tried again: no answer, a
  // server or proxy failure, or too many requests. Any other refusal is
  // final.
  const transient = (response) => !response || response.status >= 500 || response.status === 429;
  // Save a download answer's body through a blob link with the server's
  // file name; the link is never part of the page.
  const saveDownload = async (response) => {
    const name = downloadName(response.headers.get("Content-Disposition"));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    // Long enough for the browser to have taken the file.
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  };
  // At most the first two sentences of ``text``: an error page's notice is
  // written for a whole page, and the row's line keeps room for two lines.
  const twoSentences = (text) => squeeze(text).split(/(?<=[.!?])\s+/).slice(0, 2).join(" ");
  // The sentence a refusal gives: a JSON refusal's message; a plain-text
  // answer's text; for the page drawn again (the campaign archive's 409),
  // the reason its copy of ``form`` names on a button; for an error page,
  // its notice's first paragraph; else the status's default.
  const exportRefusal = (response, text, form) => {
    const status = response.status;
    const type = response.headers.get("Content-Type") || "";
    try {
      if (JSON.parse(text)?.refusal) return refusalText(status, text);
    } catch { /* not JSON: a page or plain text */ }
    if (/^text\/plain\b/i.test(type)) return twoSentences(text) || refusalText(status, "");
    const parsed = new DOMParser().parseFromString(text || "", "text/html");
    const same = form?.id ? parsed.getElementById(form.id) : null;
    if (same) {
      const reason = [...same.querySelectorAll("button[aria-describedby]")]
        .flatMap((node) => node.getAttribute("aria-describedby").split(/\s+/))
        .map((id) => squeeze(parsed.getElementById(id)?.textContent)).find(Boolean);
      return reason || refusalText(status, "");
    }
    const node = parsed.querySelector("main [role=alert], main [role=status], main .notice-error");
    const sentence = node && (node.matches("p") ? node : node.querySelector("p") || node);
    return twoSentences(sentence?.textContent) || refusalText(status, "");
  };
  // The form an export runs for, as the page has it now: an in-place
  // redraw (a Compare-on-day change, a refreshed table) may have replaced
  // it with a fresh copy of the same id.
  const liveForm = (form) => (form.isConnected || !form.id ? form
    : document.getElementById(form.id) || form);
  // Say ``text`` in the export row's reserved line, in the error colour for
  // a refusal. An unchanged sentence is left alone, so a screen reader does
  // not hear it again at every status read.
  const sayExport = (form, text, error = false) => {
    const line = liveForm(form).querySelector("[data-export-status]");
    if (!line) return;
    if (line.textContent !== text) line.textContent = text;
    line.classList.toggle("error", Boolean(text) && error);
  };
  // A one-time request key is spent by each start, so the next click is a
  // new export (a reused key would be refused as an already-bound form).
  const renewRequestKey = (form) => {
    const input = liveForm(form).querySelector('input[type="hidden"][name="request_key"]');
    if (input && window.crypto?.randomUUID) input.value = window.crypto.randomUUID();
  };
  // The status page's own sentence for the row's line: a copy without the
  // parts hidden from screen readers (queue-wait.html's ticking "queued"
  // time, which would make every read a new announcement), its times in
  // this browser's zone as date-format-v1.js shows them, and at most two
  // sentences.
  const statusSentence = (node) => {
    if (!node) return "";
    const copy = node.cloneNode(true);
    copy.querySelectorAll("[aria-hidden=true]").forEach((part) => part.remove());
    window.ParishDates?.localize(copy);
    return twoSentences(copy.textContent);
  };
  // A queued export's status page answer: {state, pending, sentence,
  // download, token} from it, {retry} for a read worth repeating, or
  // {refusal} when it settled otherwise.
  const parseExport = (response, text) => {
    if (transient(response)) return {retry: true};
    if (response.status === 410) {
      // An ended export, or a campaign that is no longer current, whose
      // typed refusal says so.
      try {
        if (JSON.parse(text)?.refusal) return {refusal: refusalText(410, text)};
      } catch { /* the fixed "no longer available" page */ }
      return {refusal: EXPORT_TEXT.gone};
    }
    const parsed = new DOMParser().parseFromString(text, "text/html");
    const region = parsed.querySelector("[data-export-state]");
    // Signed out (a redirect to sign-in) or no longer allowed.
    if (!region) {
      return {refusal: response.ok ? EXPORT_TEXT.signedOut : exportRefusal(response, text, null)};
    }
    const download = region.querySelector("form[data-live-autosubmit]");
    return {
      state: region.getAttribute("data-export-state"),
      pending: region.hasAttribute("data-live-pending"),
      sentence: statusSentence(region.querySelector(".export-state")),
      download: download && new URL(download.getAttribute("action"), response.url).href,
      token: download?.querySelector('input[name="csrfmiddlewaretoken"]')?.value,
    };
  };
  // One passive read of the status address, as live-status-v1.js reads it.
  // An unanswered read is {retry}, as a server failure is.
  const readExport = async (url) => {
    try {
      const response = await fetchWithin(url, {cache: "no-store",
        headers: {Accept: "text/html"}}, EXPORT_READ_MS);
      return parseExport(response, await response.text());
    } catch {
      return {retry: true};
    }
  };
  // Post a ready file's Download and save the file. A busy answer (503) is
  // posted again at the pace; returns the refusal sentence, or "" once the
  // file is saved.
  const downloadExport = async (status) => {
    for (let attempt = 0; ; attempt += 1) {
      const body = new URLSearchParams();
      if (status.token) body.set("csrfmiddlewaretoken", status.token);
      let response;
      try {
        response = await fetchWithin(status.download, {method: "POST", body,
          headers: {"X-Requested-With": "fetch"}}, EXPORT_DOWNLOAD_MS);
      } catch {
        return EXPORT_TEXT.unreachable;
      }
      if (isDownload(response)) {
        await saveDownload(response);
        return "";
      }
      if (response.status === 410) return EXPORT_TEXT.gone;
      const text = await response.text();
      if (response.status !== 503 || attempt + 1 >= EXPORT_DOWNLOAD_TRIES) {
        return exportRefusal(response, text, null);
      }
      await pause(EXPORT_PACE[Math.min(attempt, EXPORT_PACE.length - 1)]);
    }
  };
  // Follow a queued export from its first reading until it is ready, then
  // download it. ``progress`` is the export's {slow, sentence}: the latest
  // status sentence is kept there, and said once the export is slow. A
  // read worth repeating (no answer, a server failure) is read again at
  // the pace, as live-status-v1.js does. Returns the refusal sentence, or
  // "" once the file is saved.
  const followExport = async (form, url, first, progress) => {
    const started = Date.now();
    let status = first;
    for (let attempt = 0; ; attempt += 1) {
      if (status.refusal) return status.refusal;
      if (!status.retry) {
        if (status.state === "ready" && status.download) return downloadExport(status);
        if (!status.pending) {
          return status.state === "cancelled" ? EXPORT_TEXT.cancelled : EXPORT_TEXT.failed;
        }
        progress.sentence = status.sentence;
        if (progress.slow) sayExport(form, status.sentence || EXPORT_TEXT.waiting);
      }
      if (Date.now() - started > EXPORT_GIVE_UP_MS) return EXPORT_TEXT.slow;
      await pause(EXPORT_PACE[Math.min(attempt, EXPORT_PACE.length - 1)]);
      // A hidden tab is not read; it is checked again once shown.
      while (document.visibilityState !== "visible") await pause(1000);
      status = await readExport(url);
    }
  };
  // Show ``form``'s export busy (or released): the form aria-busy, its
  // buttons waiting, the clicked one (by name and value) with its spinner.
  // One helper for an export's start, a redraw while it runs and its end.
  const showExportBusy = (form, clicked, busy) => {
    if (busy) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
    submitControls(form).forEach((control) => {
      const mine = Boolean(clicked) && control.name === clicked.name
        && control.value === clicked.value;
      control.classList.toggle("is-busy", busy && mine);
      if (busy) control.setAttribute("aria-disabled", "true");
      else control.removeAttribute("aria-disabled");
    });
  };
  // A redraw brought in fresh copies: show each running export busy again
  // on the form as the page has it now.
  document.addEventListener("parishkit:swap", () => {
    runningExports.forEach((clicked, key) => {
      const form = typeof key === "string" ? document.getElementById(key) : key;
      if (form instanceof HTMLFormElement && form.isConnected) showExportBusy(form, clicked, true);
    });
  });
  // Run one export for ``form`` from the clicked ``submitter``: mark the
  // form busy, post it, save the file or follow the queued export, and say
  // any refusal in the row's line. The buttons are released, on the form as
  // the page has it then, whatever happens.
  const exportInPlace = async (form, submitter) => {
    const clicked = submitter ? {name: submitter.name, value: submitter.value} : null;
    runningExports.set(exportKey(form), clicked);
    showExportBusy(form, clicked, true);
    busyStatus.textContent = "Working…";
    sayExport(form, "");
    // Five seconds in, the line says the export is still being worked on:
    // its latest status sentence once there is one.
    const progress = {slow: false, sentence: ""};
    const timer = window.setTimeout(() => {
      progress.slow = true;
      sayExport(form, progress.sentence || EXPORT_TEXT.waiting);
    }, 5000);
    const fields = new FormData(form, submitter || undefined);
    let refusal = "";
    try {
      const response = await fetchWithin(form.getAttribute("action") || "", {method: "POST",
        body: new URLSearchParams(fields), headers: {"X-Requested-With": "fetch"}},
      EXPORT_START_MS);
      if (isDownload(response)) {
        await saveDownload(response);
      } else if (response.ok && response.redirected) {
        // A queued export: the redirect's answer is the status address's
        // first read (or, signed out, the sign-in page: no export state).
        const first = parseExport(response, await response.text());
        refusal = await followExport(form, response.url, first, progress);
      } else {
        const text = await response.text();
        refusal = response.ok && !text ? EXPORT_TEXT.empty : exportRefusal(response, text, form);
      }
    } catch (error) {
      refusal = error?.name === "AbortError" ? EXPORT_TEXT.noAnswer : EXPORT_TEXT.unreachable;
    } finally {
      window.clearTimeout(timer);
      // Renewed whatever the answer: even a lost one may have reached the
      // server and bound the key.
      renewRequestKey(form);
      runningExports.delete(exportKey(form));
      [form, liveForm(form)].forEach((node) => showExportBusy(node, clicked, false));
    }
    sayExport(form, refusal, Boolean(refusal));
    busyStatus.textContent = refusal ? "" : "Download started.";
  };
  // Capture phase, so this runs before the busy-state handler above, which
  // leaves a submission script took over alone.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
    if (!form.hasAttribute("data-export-in-place") && !event.submitter?.closest(".export-actions")) return;
    if (!sameOrigin(form.getAttribute("action") || "")) return;
    event.preventDefault();
    // A repeat (a double click, Enter pressed twice) while it runs is ignored.
    if (runningExports.has(exportKey(form))) return;
    exportInPlace(form, event.submitter);
  }, true);

  // Required acknowledgments: a form's submit buttons stay disabled (muted by
  // button:disabled) until every visible [data-acknowledgment] checkbox that
  // belongs to it is checked. A hidden one (a setup test's "may have arrived"
  // prompt before any doubt) does not count. This is a convenience, not the
  // guard: the server refuses a missing acknowledgment on its own (the Admin
  // portal requires JavaScript, #565, but a crafted request still reaches the
  // server). Real disabled is used, unlike
  // the busy state above, because no submission should start at all; only
  // buttons disabled by a gate are ever re-enabled (holdButton and
  // releaseButton), so a button the server or another script disabled (a
  // pending test, setup that is not ready) stays disabled. formnovalidate
  // buttons are never gated.
  //
  // While this gate holds a button, a hint after it (the unticked box's
  // data-missing-hint, or a generic line) says why, named by the button's
  // aria-describedby (#563). It sits after the button, or after the row or
  // paragraph holding it, so it never moves the box just ticked or the
  // button. A hint only explains this gate: a button the server disabled
  // (setup not ready, a test pending) gets none. A form with the
  // data-require-complete gate is left to it; its required boxes count there
  // and its own hint says what is missing.
  const acknowledgmentHints = new WeakMap(); // form → its hint element
  let acknowledgmentHintCount = 0; // numbers each hint's id
  const acknowledgmentHint = (form, controls) => {
    if (acknowledgmentHints.has(form)) return acknowledgmentHints.get(form);
    if (!controls.length) return null;
    const last = controls[controls.length - 1];
    const hint = document.createElement("p");
    hint.className = "help";
    acknowledgmentHintCount += 1;
    hint.id = `acknowledgment-hint-${acknowledgmentHintCount}`;
    hint.setAttribute("data-acknowledgment-hint", "");
    hint.hidden = true;
    const holder = last.parentElement;
    (holder?.matches("p, .setup-actions") ? holder : last).after(hint);
    controls.forEach((node) => {
      const described = (node.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter(Boolean);
      node.setAttribute("aria-describedby", [...described, hint.id].join(" "));
    });
    acknowledgmentHints.set(form, hint);
    return hint;
  };
  const gateAcknowledgments = (form) => {
    if (!form || !form.isConnected || form.hasAttribute("data-require-complete")) return;
    const boxes = [...form.elements].filter((node) => node instanceof HTMLInputElement
      && node.type === "checkbox" && node.hasAttribute("data-acknowledgment"));
    if (!boxes.length) return;
    const unticked = boxes.find((box) => !box.checked && !box.closest("[hidden]"));
    const controls = submitControls(form).filter((node) => !node.formNoValidate);
    controls.forEach((node) => {
      if (unticked) holdButton(node, "data-acknowledgment-gated");
      else releaseButton(node, "data-acknowledgment-gated");
    });
    const holding = controls.some((node) => node.hasAttribute("data-acknowledgment-gated"));
    // No hint element is made until one is needed.
    const hint = holding || acknowledgmentHints.has(form)
      ? acknowledgmentHint(form, controls) : null;
    if (!hint) return;
    hint.hidden = !holding;
    hint.textContent = holding
      ? (unticked.closest("[data-missing-hint]")?.dataset.missingHint
        || "Tick the confirmation above to continue.")
      : "";
  };
  // Forms are wired at load and again when an in-place swap brings a form
  // in (fresh elements with no listeners); a swap inside a wired form only
  // needs that form checked again.
  const wiredAcknowledgments = new WeakSet();
  const wireAcknowledgments = (root) => {
    const forms = [...(root instanceof HTMLFormElement ? [root] : []),
      ...root.querySelectorAll("form")];
    forms.forEach((form) => {
      if (wiredAcknowledgments.has(form)) return;
      wiredAcknowledgments.add(form);
      form.addEventListener("change", () => gateAcknowledgments(form));
    });
    forms.forEach(gateAcknowledgments);
    if (root instanceof Element) gateAcknowledgments(root.closest("form"));
  };
  wireAcknowledgments(document);
  document.addEventListener("parishkit:swap", (event) => {
    if (event.target instanceof Element) wireAcknowledgments(event.target);
  });
  // Back/forward navigation can restore checkbox state without a change event.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form").forEach(gateAcknowledgments);
  });

  // The setup wizard's status pages (Test email, Test Slack, Load parish
  // data, Finishing setup) follow their work through live-status-v1.js
  // regions (#1354). Only the source-load idle renewal stays here: while the
  // exact source-progress page is visible and its load is active, a
  // CSRF-only POST renews the setup's idle deadline every 15 seconds. The
  // server checks the original login, task/source leases and five-minute
  // limit; visibility and local deadlines merely stop unnecessary requests.
  // The renewal draws no status (the live region does); it only keeps the
  // deadlines it stops at current and says when it could not renew.
  document.querySelectorAll("form[data-setup-renew]").forEach((form) => {
    const warning = document.querySelector("[data-progress-unavailable]");
    const deadlines = [...form.querySelectorAll("[data-progress-deadline]")];
    let active = form.dataset.progressActive === "true";
    let closed = false, pending = false, timer = null, controller = null;
    // Anchor server instants to monotonic elapsed time, not the browser's wall
    // clock. Clock skew or a later system-clock correction must not stop renewal.
    let serverAt = Date.parse(form.dataset.progressNow), observedAt = performance.now();
    const live = () => {
      const until = Math.min(...deadlines.map((node) => Date.parse(node.dateTime)));
      const now = serverAt + performance.now() - observedAt;
      return !closed && active && Number.isFinite(until) && Number.isFinite(now) && now < until;
    };
    const schedule = () => {
      window.clearTimeout(timer);
      if (live()) timer = window.setTimeout(renew, 15000);
    };
    async function renew() {
      if (!live() || pending) return;
      if (document.hidden) { schedule(); return; }
      pending = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(form.dataset.progressUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"Content-Type": "application/x-www-form-urlencoded"},
          body: new URLSearchParams(new FormData(form)), signal: controller.signal
        });
        if (!response.ok) throw new Error("unavailable");
        const data = await response.json();
        if (data.task_id !== form.dataset.progressTask || typeof data.active !== "boolean"
            || !Number.isFinite(Date.parse(data.server_now))
            || deadlines.some((node) => !Number.isFinite(Date.parse(data[node.dataset.progressDeadline])))) {
          throw new Error("unavailable");
        }
        if (closed) return;
        serverAt = Date.parse(data.server_now);
        observedAt = performance.now();
        active = data.active;
        deadlines.forEach((node) => { node.dateTime = data[node.dataset.progressDeadline]; });
        if (warning) warning.hidden = true;
      } catch (_) {
        // Tried again on the next turn while the load is still live.
        if (!closed && !document.hidden && warning) warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        pending = false;
        controller = null;
        schedule();
      }
    }
    // A page kept in the back/forward cache (persisted) renews again when
    // it is shown; one that goes away for good stops.
    // Either way the page is marked closed while it is away, so a renewal
    // aborted by leaving shows no "could not be reached" warning; showing
    // the page again reopens it.
    window.addEventListener("pagehide", () => {
      window.clearTimeout(timer);
      if (controller) controller.abort();
      closed = true;
    });
    window.addEventListener("pageshow", (event) => {
      closed = false;
      if (event.persisted) renew();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && controller) controller.abort();
      if (!document.hidden) renew();
    });
    renew();
  });

  // A settled Test email / Test Slack page restored from the back/forward
  // cache (#563) still shows what it showed when it was left, such as
  // Continue, although an earlier step's settings may have changed since.
  // Its hidden follow-up link, followed as live-status-v1.js follows it once
  // a test settles, re-reads this address's panel in place, so the panel is
  // drawn again from the server's current state; if a test is in flight
  // again, the fresh region is watched anew. A pending region is already
  // polled. Load parish data does the same with its hidden
  // a[data-reread-on-show] link (#1353), since its load may have been
  // discarded from another tab while the page was away; it also re-reads
  // when its tab is shown again, as its progress page's status does
  // (live-status-v1.js). Each re-read refreshes every in-place region on
  // the page, the step bar included.
  const reread = (selector) => {
    const follow = document.querySelector(selector);
    const id = follow?.getAttribute("href").replace(/^#/, "");
    const panel = id && document.getElementById(id);
    if (!panel) return;
    const here = new URL(window.location.href);
    here.hash = panel.id;
    follow.href = here.href;
    follow.click();
  };
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    reread('[data-live-status="setup-test"]:not([data-live-pending]) a[data-live-follow][data-in-place], '
      + "a[data-reread-on-show][data-in-place]");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reread("a[data-reread-on-show][data-in-place]");
  });

  // Pages that update themselves (UX conventions, #1586). A page whose state
  // changes without the reader's own action (Background work, Families on
  // the form now) carries one hidden a[data-reread-every="<ms>"][data-in-place]
  // link, outside its regions, whose fragment names the region to re-read.
  // Every <ms> (at least 5 s) while the tab is visible, the page's own
  // address (so the filters, sort, size and page shown) is re-read quietly
  // through the in-place mechanism: every region is swapped, focus is found
  // again, nothing is announced, the address keeps its fragment, and a view
  // that is passive never extends the sign-in. A read is skipped (and tried
  // at the next tick) while the reader is busy: another in-place request is
  // in flight, the reader is mid-edit in a region (see readerBusy), text
  // is selected or a dialog is open. Hidden tabs are not read; the page catches up at once when shown
  // again or brought back from the back/forward cache. A failed read never
  // navigates: after three in a row, a line after the link says the page is
  // not current, and the next read that works removes it; a refusal (signed
  // out, access removed, or an answer from another page) stops the reads
  // and says so. Every such read carries PASSIVE_READ, so a view whose own
  // load renews the sign-in (the Response dashboard) reads passively instead.
  //
  // A report whose own view is audited (the Response dashboard, Campaign
  // comparison) is not re-read on a timer: its figures status region
  // (live-status-v1.js) polls a passive status read, and once newer figures
  // are ready its hidden a[data-reread] follow-up link sends a
  // parishkit:reread event.
  // The page's own address is then re-read once in the same way (waiting
  // while the reader is busy, stopping at a refusal); the fresh status
  // region is watched anew. That read is audited, so a failed one is
  // retried only a bounded number of times: twice more, REREAD_MIN_MS
  // apart, and then, saying so, the status region is handed back to the
  // passive status poll (parishkit:live-watch) at FOLLOW_BACKOFF_MS, so
  // the page is read again only when the status still says newer figures
  // are ready. The next read that works clears the count.
  const REREAD_MIN_MS = 5000;
  const REREAD_FAILURES = 3;
  // The status poll's pace after the third failed follow-up read, then
  // after each later one.
  const FOLLOW_BACKOFF_MS = [30000, 60000];
  // The pace of a figures status retrying figures it could not read.
  const RETRY_PACE_MS = 60000;
  // Said when a read is refused, and when reads keep failing and will be
  // tried again; live-status-v1.js says the same (#1647).
  const UPDATES_REFUSED = "Couldn't check for updates. Reload the page or sign in again.";
  const UPDATES_RETRYING = "Couldn't check for updates — retrying.";
  // Sent with every automatic re-read (accounts/sessions.py passive_read),
  // and only with one: it also marks an audited report view as automatic
  // (#1620), so a read the reader asked for never carries it.
  const PASSIVE_READ = {"X-ParishKit-Read": "passive"};
  let rereadTimer = 0;
  let rereadFailures = 0;
  let rereadStopped = false;
  const rereadLink = () => document.querySelector("a[data-reread-every][data-in-place][href]");
  const rereadProblem = (link, text) => {
    // Say ``text`` on the line right after ``link`` (created on first
    // use), or hide the line when ``text`` is empty. A figures status's
    // follow-up link puts the line after its status region instead, so a
    // status poll that redraws the region keeps the line (see handBack).
    link = link?.closest("[data-live-status]") || link;
    if (!link) return;
    let line = link.parentElement?.querySelector(":scope > [data-reread-problem]") || null;
    if (!line && !text) return;
    if (!line) {
      line = document.createElement("p");
      line.className = "notice";
      line.setAttribute("role", "status");
      line.setAttribute("data-reread-problem", "");
      link.after(line);
    }
    line.textContent = text;
    line.hidden = !text;
  };
  // Input types the reader types into; focus in one means they may be
  // mid-entry, so a re-read waits (a select or button merely focused after
  // its choice was applied does not).
  const TEXT_ENTRY = new Set(["text", "search", "number", "date", "datetime-local", "time",
    "month", "week", "email", "url", "tel", "password"]);
  const isTextEntry = (node) => node instanceof HTMLTextAreaElement
    || (node instanceof HTMLInputElement && TEXT_ENTRY.has(node.type))
    || (node instanceof HTMLElement && node.isContentEditable);
  // Whether a control in an in-place region shows a change the page has not
  // applied: its value differs from the one the server drew (controlValue).
  // A pane selector (Campaign comparison's pledge distribution year) is a
  // presentation choice, applied at once and kept through a swap
  // (swapRegion), so it never holds a read.
  const unapplied = () => [...document.querySelectorAll(REGIONS)].some((region) =>
    [...region.querySelectorAll("input, select, textarea")].some((node) =>
      node.type !== "hidden" && !node.matches("select[data-pane-select]")
      && controlValue(node, true) !== controlValue(node, false)));
  const readerBusy = () => {
    // Whether a periodic re-read would get in the reader's way now: another
    // in-place request is in flight, the reader is mid-edit in a region the
    // re-read replaces (focus in a text-entry field there, or a control
    // there with an unapplied change), text is selected, or a dialog is
    // open. Controls outside the regions are never replaced, so they never
    // hold a read (UX conventions, "Pages that update themselves").
    const active = document.activeElement;
    const selection = window.getSelection ? window.getSelection() : null;
    return Boolean(tableRequest || saving
      || (active && active.closest(REGIONS) && isTextEntry(active))
      || unapplied()
      || (selection && !selection.isCollapsed)
      || document.querySelector("dialog[open]"));
  };
  const quietRead = (link, done) => {
    // Re-read the page's own address quietly and passively now, into the
    // region ``link``'s fragment names, and tell ``done`` how it went:
    // "ok", "failed", "refused", "busy" (the reader is busy: not read),
    // "aborted" (the reader's own choice overtook it), "hidden" (the tab is
    // hidden: not read) or "gone" (no such region).
    const id = link?.getAttribute("href").split("#")[1];
    const region = id && document.getElementById(id);
    if (!isRegion(region)) return done("gone");
    if (document.visibilityState !== "visible") return done("hidden");
    if (readerBusy()) return done("busy");
    const here = new URL(window.location.href);
    here.hash = region.id;
    // Whether this read reported back (ok or failed); an aborted read
    // reports nothing.
    let settled = false;
    const background = {
      ok: () => {
        settled = true;
        done("ok");
      },
      failed: (response) => {
        settled = true;
        // A refusal (an error status a retry cannot fix, or an answer that
        // is another page, such as the sign-in page) stops the reads.
        const elsewhere = response && response.redirected
          && new URL(response.url).pathname !== window.location.pathname;
        const refused = elsewhere || (response && [400, 401, 403, 404, 410].includes(response.status));
        done(refused ? "refused" : "failed");
      },
    };
    refreshTable(region, link, here.href, {method: "GET", headers: PASSIVE_READ}, () => {},
      {owner: link, quiet: true, background}).finally(() => {
      if (!settled) done("aborted");
    });
  };
  const scheduleReread = () => {
    // Arm the next re-read at the link's pace, unless the page has none,
    // reads were stopped by a refusal, or the tab is hidden.
    window.clearTimeout(rereadTimer);
    const link = rereadLink();
    const pace = Number(link?.getAttribute("data-reread-every"));
    if (!link || rereadStopped || !(pace > 0) || document.visibilityState !== "visible") return;
    rereadTimer = window.setTimeout(rereadNow, Math.max(pace, REREAD_MIN_MS));
  };
  const rereadNow = () => {
    // The periodic re-read: read now, or wait for the next tick while the
    // reader is busy.
    if (rereadStopped) return;
    const link = rereadLink();
    quietRead(link, (outcome) => {
      if (outcome === "refused") {
        rereadStopped = true;
        rereadProblem(link, UPDATES_REFUSED);
        return;
      }
      if (outcome === "ok") {
        rereadFailures = 0;
        rereadProblem(link, "");
      } else if (outcome === "failed") {
        rereadFailures += 1;
        if (rereadFailures >= REREAD_FAILURES) rereadProblem(link, UPDATES_RETRYING);
      }
      // A hidden tab catches up when shown (below); a page without the
      // region has nothing to read.
      if (outcome !== "hidden" && outcome !== "gone") scheduleReread();
    });
  };
  // The figures status's one re-read (see above): its link, until the read
  // works, the link is replaced (another in-place answer redrew the region)
  // or a read is refused.
  let follow = null;
  let followTimer = 0;
  // Follow-up reads failed in a row, across follow-ups, until one works.
  let followFailures = 0;
  const handBack = (link) => {
    // Too many follow-up reads failed: stop retrying the audited page and
    // have live-status-v1.js watch ``link``'s status region again, as a
    // pending one at the backoff pace. Its next answer that still says
    // newer figures are ready settles the region and asks for one more
    // follow-up (parishkit:reread); any other answer is adopted, and the
    // region is watched at the server's pace from then on.
    follow = null;
    const status = link.closest("[data-live-status]");
    if (!status) return;
    // A region retrying figures it could not read (data-live-retry) never
    // goes faster than its own 60 s (#1586).
    const backoff = FOLLOW_BACKOFF_MS[Math.min(followFailures - REREAD_FAILURES, FOLLOW_BACKOFF_MS.length - 1)];
    const pace = status.hasAttribute("data-live-retry") ? Math.max(backoff, RETRY_PACE_MS) : backoff;
    status.setAttribute("data-live-pending", "");
    status.setAttribute("data-live-interval", String(pace));
    status.dispatchEvent(new CustomEvent("parishkit:live-watch", {bubbles: true}));
  };
  const followNow = () => {
    // Re-read for the figures status now, or again in REREAD_MIN_MS.
    window.clearTimeout(followTimer);
    if (!follow?.isConnected) {
      follow = null;
      return;
    }
    const link = follow;
    quietRead(link, (outcome) => {
      if (link !== follow) return;
      if (outcome === "ok" || outcome === "gone") {
        if (outcome === "ok") followFailures = 0;
        follow = null;
        return;
      }
      if (outcome === "refused") {
        follow = null;
        rereadProblem(link, UPDATES_REFUSED);
        return;
      }
      if (outcome === "failed") {
        followFailures += 1;
        if (followFailures >= REREAD_FAILURES) {
          rereadProblem(link, UPDATES_RETRYING);
          handBack(link);
          return;
        }
      }
      // A busy reader or an aborted read was never audited; wait and try
      // again. A hidden tab catches up when shown (below).
      if (outcome !== "hidden") followTimer = window.setTimeout(followNow, REREAD_MIN_MS);
    });
  };
  document.addEventListener("parishkit:reread", (event) => {
    if (!(event.target instanceof HTMLAnchorElement)) return;
    follow = event.target;
    followNow();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      rereadNow();
      if (follow) followNow();
    } else {
      window.clearTimeout(rereadTimer);
      window.clearTimeout(followTimer);
    }
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    rereadNow();
    if (follow) followNow();
  });
  scheduleReread();

  // A control's value for the rules below (data-show-when and
  // data-required-when). A checkbox counts as its value only while it is
  // ticked and is empty otherwise, as a submission would send it:
  // "followed_up!=yes" means "not ticked". A radio group already reads as
  // its ticked button's value (RadioNodeList.value).
  const ruleValue = (control) => (control instanceof HTMLInputElement
    && control.type === "checkbox"
    ? (control.checked ? control.value : "") : control.value);

  // A value made only of what Python's str.strip() removes (str.isspace:
  // U+0085 and U+001C-U+001F are, U+FEFF is not; JavaScript's trim() and \s
  // differ on all three). The server decides "a key was pasted" with
  // str.strip() (integration_views), and the key field's own cleaning strips
  // the same way, so the page and the server always take the same path for
  // a pasted key (#750, #1568); the complete gate counts such a required
  // field as empty, as the server's required check does (#1577).
  // test_review_dialog checks this class against
  // str.isspace for every code point.
  const SERVER_SPACE = /^[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/;
  // Complete-before-submit (#553): a form marked data-require-complete keeps
  // its submit buttons disabled until every control that is currently shown
  // and required is valid, and says why in its [data-complete-hint] element
  // (which the buttons name with aria-describedby). Conditional requirements
  // are applied only here, never in the markup; the server's own validation
  // still answers any request that skips them:
  //   - data-required-when-shown="name …" on a data-show-when group (or an
  //     empty value on a field) makes those controls required while shown;
  //   - data-required-when="name=value" makes a field required while the
  //     form's control "name" has that value (notes for the outcome Other);
  //     "name!=value" while it has any other value (the ParishSoft key in
  //     setup, once the organization ID differs from the saved one);
  //   - data-require-one on a group of checkboxes needs at least one ticked
  //     (the System logs Show choices, #601), or on a group of file fields
  //     at least one file chosen (Parish logos' Advanced sizes, #1062):
  //     while none is, each is marked invalid with the group's
  //     data-missing-hint (and data-require-one-missing). A group inside a
  //     hidden element (an editor's hidden Advanced panel, which is still
  //     sent) asks for nothing, so the browser never refuses to send a form
  //     over a field the reader cannot see.
  // The hint is the first missing control's data-missing-hint, or that of
  // the nearest element around it. As with acknowledgments above, real
  // disabled is used (holdButton and releaseButton).
  //
  // A number field's rule compares numbers, as the server reads them, so
  // "04321" matches a saved 4321; an empty number field matches no number.
  // Every gate (show, enabled and required) compares this way.
  // While the rule's own control is missing or invalid (an emptied
  // organization ID), the rule waits: that control's hint comes first.
  const sameValue = (control, value) => (control instanceof HTMLInputElement
    && control.type === "number" && value !== ""
    ? control.value.trim() !== "" && Number(control.value) === Number(value)
    : ruleValue(control) === value);
  // A rule's value may list alternatives separated by "|": "activity=a|b"
  // holds while the control has either value, and "activity=|a" while it is
  // empty or a (the System logs level boxes, enabled for all activity and
  // for the Activity groups with operational entries, #953). A negated rule
  // "activity!=a|b" holds while the control has none of them.
  const anyValue = (control, values, same) => values.split("|")
    .some((value) => same(control, value));
  const requiredWhen = (form) => {
    form.querySelectorAll("[data-required-when]").forEach((node) => {
      const rule = node.dataset.requiredWhen;
      const negated = rule.includes("!=");
      const [name, value] = rule.split(negated ? "!=" : "=");
      const control = form.elements.namedItem(name);
      // A hidden (disabled) control's leftover value does not count. A radio
      // group (RadioNodeList) has no validity of its own.
      node.required = Boolean(control) && !control.disabled
        && control.validity?.valid !== false
        && anyValue(control, value, sameValue) !== negated;
      // A label with both words (data-required-label, data-optional-label)
      // says "required" only while the rule holds, so it follows the choice
      // in place (the review worklist's note, required only to reopen an
      // ignored item, #1681).
      const label = node.labels?.[0];
      if (label?.dataset.requiredLabel && label.dataset.optionalLabel) {
        label.textContent = node.required ? label.dataset.requiredLabel : label.dataset.optionalLabel;
      }
    });
  };
  const requireOne = (form) => {
    form.querySelectorAll("[data-require-one]").forEach((group) => {
      // A box hidden by data-show-when is disabled and not sent, so its
      // tick does not count (a type Cancel cannot take, #563).
      const all = [...group.querySelectorAll("input[type=checkbox], input[type=file]")];
      const boxes = all.filter((box) => !box.disabled);
      const chosen = (box) => (box.type === "file" ? Boolean(box.files?.length) : box.checked);
      const none = !group.closest("[hidden]") && !boxes.some(chosen);
      // The group's own data-missing-hint is the message (and the custom
      // validity a box needs to count as invalid); the page supplies it.
      const hint = none ? group.dataset.missingHint : "";
      all.forEach((box) => {
        const missing = Boolean(hint) && boxes.includes(box);
        // A file field's own refusal (its size, wireMaxBytes) is not this
        // gate's to clear: only a mark this gate made is taken back.
        if (missing || box.type !== "file" || box.hasAttribute("data-require-one-missing")) {
          box.setCustomValidity(missing ? hint : "");
        }
        box.toggleAttribute("data-require-one-missing", missing);
      });
    });
  };
  // A data-require-one group of checkboxes with a reserved error line
  // (<group id>_error, an .errorlist in a .field-error-line that keeps its
  // height) also says its rule at the group, live (#2009; UX conventions,
  // "Field errors and live checks"): once the reader has changed one of
  // its boxes, no box ticked shows the group's data-missing-hint (the
  // server's words) in the line, marks every box aria-invalid and has the
  // group described by the line, as the server draws its refusal; a tick,
  // or the group being hidden or disabled, takes the marks away. A fresh
  // group with none ticked is not an error yet (the gate's hint says what
  // is missing), as for a repeat rule's days (markRepeatRule). A refusal
  // the server drew is cleared by the box's own first edit
  // (wireValidity's clearFieldError, which runs before this listener on
  // the document), so this check never takes away words it did not write
  // (data-require-one-said). The error summary keeps only the server's
  // items. requireOne holds the buttons by the same rule.
  const requireOneReached = new WeakSet();
  const sayRequireOne = (group) => {
    const line = group.id && document.getElementById(`${group.id}_error`);
    if (!line || !line.matches(".errorlist")) return;
    const boxes = [...group.querySelectorAll("input[type=checkbox]")];
    const live = boxes.filter((box) => !box.disabled);
    const none = requireOneReached.has(group) && !group.closest("[hidden]")
      && live.length > 0 && !live.some((box) => box.checked);
    const message = none ? (group.dataset.missingHint || "") : "";
    if (!message) {
      if (!group.hasAttribute("data-require-one-said")) return;
      group.removeAttribute("data-require-one-said");
      boxes.forEach((box) => box.setAttribute("aria-invalid", "false"));
      describeBy(group, line.id, false);
      line.hidden = true;
      line.querySelectorAll("li").forEach((item) => { item.textContent = ""; });
      return;
    }
    group.setAttribute("data-require-one-said", "");
    boxes.forEach((box) => box.setAttribute("aria-invalid", String(!box.disabled)));
    describeBy(group, line.id, true);
    // Unchanged words stay untouched, so a screen reader hears them once.
    const item = line.querySelector("li") || line.appendChild(document.createElement("li"));
    if (line.hidden || item.textContent !== message) {
      line.hidden = false;
      item.textContent = message;
    }
  };
  // Every such group is checked again after any change (a box, or a
  // control that hides the group) and on pageshow. Changing or leaving
  // one of its boxes reaches the group, as leaving a field lets the
  // browser's own check mark it (wireValidity).
  const sayRequireOnes = () => document.querySelectorAll("[data-require-one][id]")
    .forEach(sayRequireOne);
  const reachRequireOne = (event) => {
    const box = event.target;
    const group = box instanceof HTMLInputElement && box.type === "checkbox"
      && box.closest("[data-require-one][id]");
    if (group) requireOneReached.add(group);
    sayRequireOnes();
  };
  document.addEventListener("change", reachRequireOne);
  document.addEventListener("focusout", reachRequireOne);
  window.addEventListener("pageshow", sayRequireOnes);
  // data-max-ticked="N" on a group of checkboxes allows at most N ticked
  // (the Ministry requests packet's Ministries, #1332). Past that each
  // ticked box is invalid with the group's data-max-ticked-hint, its
  // "{count}" the number ticked, which the gate below shows as its hint.
  // Only a mark this check made is cleared. The server refuses more too.
  const maxTicked = (form) => {
    form.querySelectorAll("[data-max-ticked]").forEach((group) => {
      const boxes = [...group.querySelectorAll("input[type=checkbox]")];
      const ticked = boxes.filter((box) => box.checked && !box.disabled);
      const over = !group.closest("[hidden]")
        && ticked.length > Number(group.dataset.maxTicked);
      const hint = (group.dataset.maxTickedHint || "").replace("{count}",
        ticked.length.toLocaleString("en-US"));
      boxes.forEach((box) => {
        const mark = over && ticked.includes(box);
        if (mark || box.hasAttribute("data-max-ticked-over")) box.setCustomValidity(mark ? hint : "");
        box.toggleAttribute("data-max-ticked-over", mark);
      });
    });
  };
  // Fields that must change on their own (data-change-alone="<name> ..." on
  // a data-require-change form, with its words in data-change-alone-hint):
  // a live campaign's Ministries, whose review is applied by itself, or a
  // draft's dates and time zone while it has mail schedules (#1330). The
  // names listed are one group: while a field of it and any field outside
  // it differ from the saved values, the group's first changed field
  // carries the hint as its data-client-error, so the gate below holds
  // Review with those words. The server still refuses such a change.
  const changeAlone = (form) => {
    const group = (form.dataset.changeAlone || "").split(/\s+/).filter(Boolean);
    const fields = group.map((name) => form.elements.namedItem(name))
      .filter((field) => field instanceof HTMLElement);
    if (!fields.length || !form.id || !form.hasAttribute("data-require-change")) return;
    const names = changedNames(form, savedState(form));
    const hint = form.dataset.changeAloneHint || "";
    const mixed = [...names].some((name) => !group.includes(name));
    const marked = mixed ? fields.find((field) => names.has(field.name)) : null;
    fields.forEach((field) => {
      if (field === marked) field.dataset.clientError = hint;
      else if (field.dataset.clientError === hint) delete field.dataset.clientError;
    });
  };
  // Whether a field still holds the value it was drawn with (a select: its
  // drawn choice), for data-require-edit below.
  const openedValue = (field) => (field instanceof HTMLSelectElement
    ? [...field.options].every((option) => option.selected === option.defaultSelected)
    : field.value === field.defaultValue);
  const gateComplete = (form) => {
    if (!form || !form.hasAttribute("data-require-complete")) return;
    requiredWhen(form);
    requireOne(form);
    maxTicked(form);
    changeAlone(form);
    // A required text field holding only spaces is still empty: the server
    // strips it (notes for the outcome Other, a Slack token being set up),
    // with Python's whitespace (SERVER_SPACE), which trim() does not match.
    const blank = (node) => node.required
      && node.matches("textarea, input[type=text], input[type=password]")
      && SERVER_SPACE.test(node.value);
    // A live check (the follow-up contact time) marks a field it found in
    // error with data-client-error, whose text is the hint.
    const invalid = [...form.elements].find((node) => node.willValidate
      && !node.closest("[hidden]")
      && (node.hasAttribute("data-client-error") || !node.validity.valid || blank(node)));
    // An editor marked data-require-edit (the review worklist's Edit, R38
    // finding 4) also holds Save until one of its fields differs from the
    // value it opened with, saying so in its data-unchanged-hint: saving
    // the value the item already carries forward records no edit, only a
    // plain Accept, which reads as a lost edit.
    const unchanged = invalid ? null : [...form.querySelectorAll("[data-require-edit]")]
      .find((group) => !group.closest("[hidden]") && [...group.querySelectorAll("[name]")]
        .filter((field) => field.willValidate).every(openedValue));
    const missing = invalid || unchanged?.querySelector("[name]") || null;
    const hint = form.querySelector("[data-complete-hint]");
    // An empty required field may say what to type (data-empty-hint) where
    // its data-missing-hint describes the format (a placeholder name, #563).
    const empty = missing && missing.dataset.emptyHint
      && (missing.validity.valueMissing || blank(missing));
    if (hint) {
      // A hint marked data-complete-hint="reserve" stays in the layout and
      // only empties, keeping its line's height (ui-v1.css), so it appearing
      // or going never moves what follows it (no layout shift: the System
      // logs table under its filter bar, #953). Any other hint hides.
      hint.hidden = !missing && hint.dataset.completeHint !== "reserve";
      hint.textContent = unchanged
        ? (unchanged.dataset.unchangedHint || "Change the value to save it.")
        : missing
        ? (missing.dataset.clientError
          // An unreadable time entry (#398) points at the field, whose
          // reading line already gives the whole refusal: repeating it here
          // could wrap onto more lines on a phone and move what follows the
          // hint as the Admin types (#2082). The fixed words keep the hint
          // to one line at 320 px. A time field's other refusal (its
          // browser zone, data-browser-zone-field) and a DUID list (#1577)
          // say why themselves.
          || (missing.matches("[data-time-entry]") && readTimeField(missing).invalid
            && TIME_HINT)
          || (missing.matches("[data-time-entry], [data-duid-list], [data-distinct]")
            && missing.validity.customError && missing.validationMessage)
          || (empty && missing.dataset.emptyHint)
          || (missing.hasAttribute("data-max-ticked-over") && missing.validationMessage)
          || missing.closest("[data-missing-hint]")?.dataset.missingHint
          || "Fill in the required fields to save.")
        : "";
    }
    submitControls(form).forEach((node) => {
      if (node.formNoValidate) return;
      if (missing) holdButton(node, "data-complete-gated");
      else releaseButton(node, "data-complete-gated");
    });
  };

  // A file field marked data-max-bytes (Past campaigns' archive, #1018)
  // counts as in error while the chosen file is larger than that: its
  // data-too-large text becomes the field's data-client-error, which the
  // complete-before-submit gate above holds the form's buttons with and
  // shows as its hint, so a file the server would refuse is never sent.
  // Capture phase, so the field is marked before the gate's own change
  // listener on the form runs. The server checks the size again.
  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file"
        || !input.hasAttribute("data-max-bytes")) return;
    const file = input.files?.[0];
    if (file && file.size > Number(input.dataset.maxBytes)) {
      input.setAttribute("data-client-error", input.dataset.tooLarge || "This file is too large.");
    } else {
      input.removeAttribute("data-client-error");
    }
  }, true);

  // A field marked data-show-when="name=value" is shown only while the form's
  // control called "name" has that value, e.g. the daily refresh time only
  // for the once-a-day frequency; "name!=value" shows it for every other
  // value, e.g. the confirmation to reopen information follow-up only once
  // "Follow-up completed" is unticked. On a field the mark hides its
  // enclosing div; on a div (a group of fields) it hides that div and every
  // control inside it. Hidden fields are disabled so they are not sent, and
  // the server ignores whatever does not apply in any case. A browser can
  // restore form values without a change event (the back/forward cache, or
  // autofill after load), so every rule is applied again on pageshow, not
  // only at load.
  //
  // data-enabled-when takes the same rule but never hides: the field stays
  // where it is, disabled (greyed and not sent) while the rule does not
  // hold, so choosing a value moves nothing on the page (no layout shift,
  // #953: the System logs level boxes while Sign-in activity is chosen; the
  // Ministry follow-up outcome and contact details, #1014).
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
    within(root, "[data-show-when], [data-enabled-when]").forEach((node) => {
      const hides = node.hasAttribute("data-show-when");
      const rule = hides ? node.dataset.showWhen : node.dataset.enabledWhen;
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
        const shown = anyValue(control, value, sameValue) !== negated;
        if (hides) wrapper.hidden = !shown;
        // A hidden field is not sent, so an error marked on it no longer
        // applies: it clears, with its message and summary item.
        if (!shown) {
          // Clearing one field also clears the fields sharing its message,
          // so each is checked again as it comes up.
          controls.forEach((item) => {
            if (item.hasAttribute("data-field-error")) clearFieldError(item);
          });
        }
        controls.forEach((item) => {
          // A group inside another (the census address editor's State
          // controls inside Add's "What changed" group, #1785) is sent only
          // while every group around it is shown: showing the outer group
          // never enables a control the inner rule hides, nor the inner
          // one a control inside a hidden outer group.
          item.disabled = !shown || showWhenUpdates.some((entry) => entry.wrapper !== wrapper
            && entry.wrapper?.hidden && entry.wrapper.contains(item));
          // A hidden file field's size refusal goes with it (#1062).
          if (!shown && item.dataset.sizeRule) {
            item.setCustomValidity("");
            delete item.dataset.sizeRule;
          }
        });
        required.forEach((item) => { item.required = shown; });
        if (form) recheckSizes(form);
        gateComplete(form);
      };
      // A radio group (RadioNodeList) is not an element: each of its
      // buttons is listened to instead.
      (control instanceof RadioNodeList ? [...control] : [control])
        .forEach((item) => {
          item.addEventListener("change", update);
          // A text control is followed as it is typed, not only on blur
          // (the Slack step's saved-token note follows the channel ID).
          item.addEventListener("input", update);
        });
      showWhenUpdates.push({node, update, wrapper: hides ? wrapper : null});
      update();
    });
  };
  // Question-led choices (the Mail message page's resolution question,
  // #1563). An element marked data-choice-panel="name=value" is the part of
  // a question shown while the radio button group "name" has that value
  // ("name=" while none is chosen yet). The group is found in the panel's
  // nearest [data-choice-scope], and is in no form, so choosing an answer
  // sends nothing: each answer is its own form, and only the chosen one is
  // ever visible. A panel not chosen is hidden by visibility (ui-v1.css,
  // [data-choice-hidden]) and made inert, never removed from the layout:
  // every panel of a question shares one reserved grid cell (.choice-slot),
  // so choosing, or changing, an answer never moves the button or what
  // follows (#736). Applied again on pageshow, for a browser that restores
  // a ticked radio button from history, and after an in-place swap.
  let choiceUpdates = []; // {node, update} for each data-choice-panel
  const wireChoices = (root) => {
    within(root, "[data-choice-panel]").forEach((panel) => {
      const [name, value] = panel.dataset.choicePanel.split("=");
      const scope = panel.closest("[data-choice-scope]") || document;
      const radios = [...scope.querySelectorAll("input[type=radio]")]
        .filter((radio) => radio.name === name);
      const update = () => {
        if (!panel.isConnected) return;
        const chosen = radios.find((radio) => radio.checked);
        const shown = value === "" ? !chosen : chosen?.value === value;
        panel.toggleAttribute("data-choice-hidden", !shown);
        panel.inert = !shown;
      };
      radios.forEach((radio) => radio.addEventListener("change", update));
      choiceUpdates.push({node: panel, update});
      update();
    });
  };
  // A field marked data-locked-when="name=value" (or "name!=value") stays
  // where it is but is locked while the rule holds: it shows its
  // data-locked-value, disabled, so locking moves nothing. The field's own
  // help (its toggletip) says why; a reason line under the field would make
  // it taller than its neighbours in a bottom-aligned filter bar. The first
  // use is the directory's Campaign mail can reach, which shows "By postal
  // mail only" while Include mailing columns is ticked (#951).
  // data-show-when would hide the field and drop the reader's value; this
  // shows the value that applies and keeps the reader's own: a disabled
  // field is not sent, so the hidden input marked data-locked-own="<field
  // id>" carries it while locked, and the field takes it back once the rule
  // stops holding. The server draws either state itself and applies the
  // rule on every request in any case. Listening on the form, not the
  // control, also catches a filter sync's synthetic form change.
  let lockedUpdates = []; // {node, update} for each data-locked-when mark
  const wireLockedWhen = (root) => {
    within(root, "[data-locked-when]").forEach((node) => {
      const rule = node.dataset.lockedWhen;
      const negated = rule.includes("!=");
      const [name, value] = rule.split(negated ? "!=" : "=");
      const form = node.form;
      const control = form && form.elements.namedItem(name);
      const own = form && form.querySelector(`[data-locked-own="${CSS.escape(node.id)}"]`);
      if (!control || !own) return;
      const update = () => {
        if (!node.isConnected) return;
        const locked = (ruleValue(control) === value) !== negated;
        if (locked && !node.disabled) {
          own.value = node.value;
          node.value = node.dataset.lockedValue;
        } else if (!locked && node.disabled) {
          node.value = own.value;
        }
        node.disabled = locked;
        own.disabled = !locked;
      };
      form.addEventListener("change", update);
      lockedUpdates.push({node, update});
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
  // File size (#563): data-max-bytes is wired by wireMaxBytes, below with
  // the Parish logos' size checks (#1062), which it shares.
  // A pattern for a textarea (#563), which has no pattern attribute of its
  // own: data-pattern="[^@]*" must match the whole value, or the field is
  // invalid with its data-missing-hint, so the browser marks it on blur and
  // a data-require-complete form holds its button with that hint. Checked
  // on input, at load, after a swap and on pageshow. Compiled with the "v"
  // flag, as the browser compiles an input's pattern, so a shared format
  // rule (web/field_rules.py, such as Setup's one address per line, #1577)
  // reads the same on a textarea as on a text box.
  let patternChecks = []; // {field, check} for each wired textarea
  const wirePattern = (root) => {
    within(root, "textarea[data-pattern]").forEach((field) => {
      const rule = new RegExp(`^(?:${field.dataset.pattern})$`, "v");
      const check = () => field.setCustomValidity(rule.test(field.value) ? ""
        : (field.closest("[data-missing-hint]")?.dataset.missingHint || "Check this field."));
      patternCheck.set(field, check);
      check();
      patternChecks.push({field, check});
    });
  };
  // A list of Family DUIDs (data-duid-list, Test with Families, #1577) is
  // read as the server's parse_family_duids reads it, with the rules it
  // sends in the attribute: the value split at any white space (Python's,
  // "space"), each entry a whole DUID ("token": digits, or digits grouped by
  // commas) of at most "longest" characters naming a number from 1 to
  // 2^63 - 1, at most "limit" of them, no number twice ("1,234" and
  // "01234" are one number). A list that breaks a rule is invalid with the
  // server's own words for it ("exact" or "distinct"), so the field is
  // marked as it is left and the form's gate holds Check with them. No
  // pattern can say "no number twice"; the shared case table
  // (fixtures/field_rule_cases.json) runs the same examples through both.
  const duidProblem = (value, rules) => {
    const entries = value.split(new RegExp(`[${rules.space}]+`, "v")).filter(Boolean);
    const token = new RegExp(`^(?:${rules.token})$`, "v");
    const numbers = [];
    for (const entry of entries) {
      if (entry.length > rules.longest || !token.test(entry)) return rules.exact;
      const number = BigInt(entry.replaceAll(",", ""));
      if (number < 1n || number >= 2n ** 63n) return rules.exact;
      numbers.push(number);
    }
    return numbers.length > rules.limit || new Set(numbers).size !== numbers.length
      ? rules.distinct : "";
  };
  const wireDuidList = (root) => {
    within(root, "textarea[data-duid-list]").forEach((field) => {
      let rules;
      try { rules = JSON.parse(field.dataset.duidList); } catch (_) { return; }
      const check = () => field.setCustomValidity(duidProblem(field.value, rules));
      patternCheck.set(field, check);
      check();
      patternChecks.push({field, check});
    });
  };
  // No two values alike (#1569, Share options' short labels): every text
  // box of a form marked data-distinct="<group>" is compared with the
  // others of its group as the Admin types, as the server compares them
  // (campaigns.configuration.short_label_key): around them Python's
  // whitespace, which the field strips (SERVER_SPACE), is left out, and
  // case is ignored by upper then lower case, the full Unicode mapping
  // both engines apply. A disabled box (its row's Delete is ticked) is not
  // sent, so it does not count. Every box whose value another box has is
  // invalid with its data-distinct-message, so the form's gate holds Save
  // with those words, and is marked at the field as the browser's own
  // checks are (wireValidity): in its reserved error line ("<id>_error",
  // the shared rule line, so nothing moves), aria-invalid and described by
  // it. A new mark waits for a pause in typing (TIME_PAUSE), so a label
  // being typed past another ("IRA" on the way to "IRAs") flashes nothing,
  // and shows at once when a box is left; a mark that no longer applies
  // goes at once, from every box it was on. A server refusal in the same
  // words on a box the Admin did not edit goes too once it no longer holds.
  const SERVER_STRIP = new RegExp(`^${SERVER_SPACE.source.slice(1, -1)}|${
    SERVER_SPACE.source.slice(1, -2)}+$`, "g");
  const distinctKey = (value) => value.replace(SERVER_STRIP, "").toUpperCase().toLowerCase();
  const distinctForms = new WeakMap(); // form -> its check
  let distinctChecks = []; // {form, check} for each wired form, for pageshow
  // Set each box's validity in ``form`` from the values now in it.
  const distinctValidity = (form) => {
    const groups = new Map(); // group -> key -> count
    const boxes = [...form.querySelectorAll("input[data-distinct]")];
    const keyOf = (box) => (box.disabled ? "" : distinctKey(box.value));
    boxes.forEach((box) => {
      const keys = groups.get(box.dataset.distinct) || new Map();
      const key = keyOf(box);
      if (key) keys.set(key, (keys.get(key) || 0) + 1);
      groups.set(box.dataset.distinct, keys);
    });
    boxes.forEach((box) => {
      const key = keyOf(box);
      const repeated = Boolean(key) && groups.get(box.dataset.distinct).get(key) > 1;
      box.setCustomValidity(repeated ? box.dataset.distinctMessage || "Use a different value." : "");
    });
    return boxes;
  };
  const wireDistinct = (root) => {
    const forms = new Set(within(root, "input[data-distinct]").map((box) => box.form));
    forms.forEach((form) => {
      if (!form || distinctForms.has(form)) return;
      let pause = null;
      // ``show`` marks a box newly found repeated; without it only marks
      // already shown follow the values (typing, before the pause).
      const check = (show) => {
        window.clearTimeout(pause);
        distinctValidity(form).forEach((box) => {
          const repeated = box.validity.customError;
          const message = box.dataset.distinctMessage;
          const line = ruleLine(box);
          if (!repeated && box.hasAttribute("data-field-error")
              && line?.textContent.trim() === message) {
            clearFieldError(box);
          } else if (blurMarked.has(box)) {
            const invalid = !box.validity.valid;
            box.setAttribute("aria-invalid", String(invalid));
            if (!invalid) blurMarked.delete(box);
            sayRule(box, invalid);
          } else if (repeated && show && !box.hasAttribute("data-field-error")) {
            box.setAttribute("aria-invalid", "true");
            blurMarked.add(box);
            sayRule(box, true);
          }
        });
        gateComplete(form);
      };
      // The box's own listeners (wireValidity) and the form's gate see this
      // input's verdict: validity is set first, in the capture phase.
      form.addEventListener("input", () => distinctValidity(form), true);
      form.addEventListener("change", () => distinctValidity(form), true);
      // Then the marks, once the box's own listeners have run. Ticking a
      // row's Delete disables its box (data-enabled-when, a change
      // listener on the box), which this runs after too.
      form.addEventListener("input", (event) => {
        check(false);
        if (event.target.matches?.("input[data-distinct]")) {
          pause = window.setTimeout(() => check(true), TIME_PAUSE);
        }
      });
      form.addEventListener("change", () => check(true));
      form.addEventListener("focusout", (event) => {
        if (event.target.matches?.("input[data-distinct]")) check(true);
      });
      distinctForms.set(form, check);
      distinctChecks.push({form, check});
      check(true);
    });
  };
  // Run as the page's first input listener (the document's capture phase),
  // so the field's own listeners (the field-error wiring, which clears a
  // mark once the value is valid) and the form's gate all see this input's
  // verdict, not the previous one's.
  const patternCheck = new WeakMap(); // textarea -> its check
  document.addEventListener("input", (event) => patternCheck.get(event.target)?.(), true);
  // The end of a financial period that starts on ``value`` (YYYY-MM-DD):
  // the day before its first anniversary, a Feb 29 anniversary falling on
  // Feb 28 (campaigns.intervals.financial_period_end). Dates stay text and
  // the arithmetic uses UTC, so no browser time zone can shift the day; null
  // for a start that is not a whole date or whose end is past year 9999.
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
  // Date and amount order (#563). A Through or To date marked
  // data-not-before="<start input id>" takes the start's value as its min,
  // so the browser itself finds an end before the start (rangeUnderflow):
  // the field is marked on blur, and a data-require-complete form holds its
  // button with the end's data-missing-hint. The server still checks the
  // order. Set at load, after a swap, on pageshow (before the gates), and
  // whenever the start changes.
  // A text amount (the Financial report's pledge range) has no min, so
  // the same order is a custom validity, set only while both values match
  // their own pattern (a malformed value says so itself).
  const checkNotBefore = (end) => {
    const start = document.getElementById(end.dataset.notBefore);
    if (!(start instanceof HTMLInputElement)) return;
    if (end.type !== "date") {
      const ordered = !start.value || !end.value || start.validity.patternMismatch
        || end.validity.patternMismatch || Number(end.value) >= Number(start.value);
      end.setCustomValidity(ordered ? "" : (end.dataset.missingHint || "Out of order."));
    } else {
      // A financial period's end (data-one-year) must be exactly the day
      // its start gives (#1577), as the server requires: that day is both
      // its min and its max, so any other day is out of range. Otherwise
      // the start is the min. Each is written only when it changes (#1419).
      // Rewriting min, even to the same value, makes Chromium rebuild the
      // date's fields; the end's own input event fires once its first year
      // digit makes a whole date, so a rewrite there dropped the year being
      // typed and the next digit cleared the field.
      const exact = end.hasAttribute("data-one-year") ? periodEnd(start.value) : null;
      const bound = (name, value) => {
        if (end.getAttribute(name) === (value || null)) return;
        if (value) end.setAttribute(name, value);
        else end.removeAttribute(name);
      };
      bound("min", exact || start.value);
      bound("max", exact);
    }
  };
  const wireNotBefore = (root) => within(root, "input[data-not-before]").forEach(checkNotBefore);
  // One listener for every page, in the capture phase, so the order is set
  // before the form's own listeners (the complete gate) see the same event,
  // and a start field kept across in-place swaps never gathers a listener
  // per swap: it finds its ends by id each time.
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    const field = event.target;
    if (!(field instanceof HTMLInputElement)) return;
    if (field.hasAttribute("data-not-before")) checkNotBefore(field);
    if (field.id) {
      document.querySelectorAll(`input[data-not-before="${CSS.escape(field.id)}"]`)
        .forEach(checkNotBefore);
    }
  }, true));
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
        // A greyed contact field is disabled and not sent. The
        // time is typed in any common form (#398), so it is read as the
        // server reads it; an entry that cannot be read yet counts as no
        // time (its own refusal holds Save).
        const clock = time && time.value ? parseTime(time.value) : null;
        const future = !date.disabled && Boolean(date.value) && (clock && clock.value
          ? localMoment(date.value, canonicalTime(clock.value)) > Date.now()
          : date.value > localToday());
        if (future) {
          fields.forEach((node) => {
            node.setAttribute("aria-invalid", "true");
            node.setAttribute("data-field-error", "client");
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
  // A field checked by the server as it is typed (#944): a live campaign's
  // end date, whose Review changes stays unavailable while the date cannot
  // be reviewed. It is a single-field check: the form is marked
  // data-live-check="<check URL>", names its one field in
  // data-live-check-field and the saved value in data-live-check-saved, and
  // holds that field's error line ([data-live-check-error], an .errorlist in
  // a reserved .field-error-line, so the message coming and going never
  // moves Review). The reserved line is a polite live region (role=status),
  // so a screen reader hears each answer. (The ParishSoft refresh schedule's
  // multi-row editor, #920, checks a whole formset with its own mechanism
  // for now.) As the value changes:
  //   - empty: the data-require-complete gate holds Review with the form's
  //     data-missing-hint;
  //   - the saved value: nothing is checked and no error is shown (nothing
  //     is wrong, there is just nothing to do); the form's change gate
  //     (data-require-change, #924) holds Review with its own hint;
  //   - otherwise any earlier error is taken away (it was about another
  //     value) and Review is held (data-checking-hint) while, after a short
  //     pause, the whole form is posted to the check, which saves nothing.
  //     Its answer ([data-live-check-answer]) either clears the field or
  //     marks it in error (data-field-error="client", aria-invalid,
  //     described by the line) with the answer's words, which may link to
  //     where to resolve it, and holds Review with its data-hint (the words
  //     without the link) as the hint.
  // Each change makes every earlier answer out of date, so a late one is
  // dropped. Review is offered only when the change is known to be
  // possible, so a check that cannot answer fails closed: Review stays
  // held, and the line and hint say so (data-signed-out-hint for a 401 or
  // 403, data-check-failed-hint otherwise). The field is not marked
  // invalid then, as nothing is known to be wrong with it. The next edit,
  // or showing the page again (pageshow), checks again.
  let liveChecks = []; // {form, check} for each wired form
  const wireLiveCheck = (root) => {
    within(root, "form[data-live-check]").forEach((form) => {
      const field = form.elements.namedItem(form.dataset.liveCheckField);
      const line = form.querySelector("[data-live-check-error]");
      if (!(field instanceof HTMLInputElement) || !line) return;
      let timer = null;
      let latest = 0;
      let controller = null;
      // ``hint`` holds Review with that line; an empty one releases it.
      const hold = (hint) => {
        if (hint) field.dataset.clientError = hint;
        else delete field.dataset.clientError;
        gateComplete(form);
      };
      const unmark = () => {
        if (field.hasAttribute("data-field-error")) clearFieldError(field);
      };
      // Show ``nodes`` in the line, described by the field; ``invalid``
      // marks the field in error (a refusal, not a check that failed).
      const mark = (nodes, invalid) => {
        line.hidden = false;
        const item = line.querySelector("li") || line.appendChild(document.createElement("li"));
        item.replaceChildren(...nodes);
        if (invalid) field.setAttribute("aria-invalid", "true");
        field.setAttribute("data-field-error", "client");
        const ids = (field.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter((id) => id && id !== line.id);
        field.setAttribute("aria-describedby", [...ids, line.id].join(" "));
      };
      // The check could not answer: say so at the field and hold Review.
      const failed = (status) => {
        const words = (status === 401 || status === 403)
          ? form.dataset.signedOutHint : form.dataset.checkFailedHint;
        unmark();
        mark([document.createTextNode(words)], false);
        hold(words);
      };
      const ask = async (mine) => {
        controller = new AbortController();
        let status = 0;
        try {
          const response = await fetch(form.dataset.liveCheck, {
            method: "POST",
            body: new URLSearchParams(new FormData(form)),
            credentials: "same-origin",
            signal: controller.signal,
          });
          status = response.status;
          const text = await response.text();
          if (mine !== latest) return;
          if (!response.ok) throw new Error(`Check answered ${response.status}.`);
          const answer = new DOMParser().parseFromString(text, "text/html")
            .querySelector("[data-live-check-answer]");
          if (!answer) throw new Error("Check gave no answer.");
          unmark();
          if (answer.dataset.blocking === "true") {
            mark([...answer.childNodes].map((node) => document.importNode(node, true)), true);
            hold(answer.dataset.hint || answer.textContent.trim());
          } else {
            hold("");
          }
        } catch (error) {
          if (mine !== latest || error.name === "AbortError") return;
          failed(status);
        }
      };
      const check = () => {
        if (!field.isConnected) return;
        window.clearTimeout(timer);
        latest += 1;
        if (controller) controller.abort();
        const value = field.value;
        // Any error shown was about an earlier value.
        unmark();
        if (!value || value === form.dataset.liveCheckSaved) {
          hold("");
          return;
        }
        hold(form.dataset.checkingHint);
        const mine = latest;
        timer = window.setTimeout(() => ask(mine), 250);
      };
      field.addEventListener("input", check);
      field.addEventListener("change", check);
      liveChecks.push({form, check});
      check();
    });
  };
  // A file field marked data-max-bytes refuses a file over that many bytes
  // as soon as it is chosen (Parish logos, #1062), in the server's own
  // words: data-max-bytes-message, whose "{size}" is the chosen file's size
  // written as the server writes it ("3.2 MB", "300 KB", rounded up). A form
  // marked data-max-total-bytes likewise refuses files that together exceed
  // it, at the field just chosen, with its data-max-total-message. The
  // message goes where the server's errors for the field go: its
  // "<id>_error" list, in a reserved line that keeps its height while empty
  // and is a polite live region, so it is announced and moves nothing
  // (file-size-field.html). The field is marked in error, and its custom
  // validity keeps the browser from sending the form until another file is
  // chosen. A field with no data-max-bytes-message (Past campaigns'
  // archive, #1018) uses its data-too-large text, or a plain refusal.
  // The reverse proxy refuses a request body over 6 MB before the page
  // could explain why, so this check is what keeps such a refusal on the
  // form; the server still checks every upload. The check runs after the
  // change's other listeners (setTimeout), since the shared field-error
  // wiring clears a mark on every edit.
  const fileSize = (bytes) => (bytes >= 1048576
    ? `${Math.ceil(bytes * 10 / 1048576) / 10} MB`
    : `${Math.ceil(bytes / 1024).toLocaleString("en-US")} KB`);
  // Each field's check (edited: whether it is the field just changed); a
  // change to any file field re-checks its form's others, so a total that
  // fits again releases the field that was refused for it.
  const sizeChecks = new WeakMap(); // form -> [check]
  const wireMaxBytes = (root) => {
    within(root, "input[type=file][data-max-bytes]").forEach((input) => {
      const errorId = `${input.id}_error`;
      const check = (edited) => {
        if (!input.isConnected) return;
        const form = input.form;
        // A hidden (disabled) field is not sent, so its file is not judged.
        const file = !input.disabled && input.files && input.files[0];
        const limit = form && Number(form.dataset.maxTotalBytes || 0);
        const total = limit
          ? [...form.querySelectorAll("input[type=file]")]
            .filter((node) => !node.disabled && node.files && node.files[0])
            .reduce((sum, node) => sum + node.files[0].size, 0)
          : 0;
        let message = "";
        let rule = "";
        if (file && file.size === 0 && input.dataset.emptyFileMessage) {
          // A 0-byte file, which the server's file field refuses too, in
          // its words (data-empty-file-message, #1577).
          message = input.dataset.emptyFileMessage;
          rule = "own";
        } else if (file && file.size > Number(input.dataset.maxBytes)) {
          // A field marked before #1062 (Past campaigns' archive, #1018)
          // words its refusal in data-too-large instead.
          message = (input.dataset.maxBytesMessage || input.dataset.tooLarge
            || "This file is too large.").replace("{size}", fileSize(file.size));
          rule = "own";
        } else if (file && limit && total > limit
          && (edited || input.dataset.sizeRule === "total")) {
          message = form.dataset.maxTotalMessage.replace("{size}", fileSize(total));
          rule = "total";
        }
        input.setCustomValidity(message);
        // A data-require-complete form holds its button with this as its
        // hint (Hosted files, Campaign images, #563).
        if (message) input.dataset.clientError = message;
        else delete input.dataset.clientError;
        let error = document.getElementById(errorId);
        // A field with no reserved error line of its own (only Parish logos'
        // file-size-field.html has one) is marked invalid and says why in
        // its form's hint; adding a line would move what follows it.
        if (!error) {
          if (message) input.dataset.sizeRule = rule;
          else delete input.dataset.sizeRule;
          input.setAttribute("aria-invalid", String(Boolean(message)));
          return;
        }
        if (!message) {
          if (input.dataset.sizeRule && input.hasAttribute("data-field-error")) {
            clearFieldError(input);
          }
          delete input.dataset.sizeRule;
          return;
        }
        input.dataset.sizeRule = rule;
        error.hidden = false;
        error.replaceChildren(Object.assign(document.createElement("li"), {textContent: message}));
        const ids = (input.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter((id) => id && id !== errorId);
        input.setAttribute("aria-describedby", [...ids, errorId].join(" "));
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("data-field-error", "client");
      };
      const form = input.form;
      if (!form) return;
      if (!sizeChecks.has(form)) sizeChecks.set(form, []);
      sizeChecks.get(form).push(check);
      input.addEventListener("change", () => window.setTimeout(() => {
        sizeChecks.get(form).forEach((other) => other(other === check));
        // The gate answered this change before the check ran: answer again
        // with the field's new data-client-error (or none).
        gateComplete(form);
      }, 0));
    });
  };
  // Check a form's file sizes again, none as the one just changed: a field
  // that a choice hid or showed (data-show-when) changes which files count,
  // and a field shown again must show any refusal its file still has rather
  // than be held silently invalid.
  const recheckSizes = (form) => {
    (sizeChecks.get(form) || []).forEach((check) => check(false));
  };
  const wireConditional = (root) => {
    wireMaxBytes(root);
    showWhenUpdates = showWhenUpdates.filter(({node}) => node.isConnected);
    choiceUpdates = choiceUpdates.filter(({node}) => node.isConnected);
    lockedUpdates = lockedUpdates.filter(({node}) => node.isConnected);
    completeForms = completeForms.filter((form) => form.isConnected);
    notFutureChecks = notFutureChecks.filter(({date}) => date.isConnected);
    patternChecks = patternChecks.filter(({field}) => field.isConnected);
    distinctChecks = distinctChecks.filter(({form}) => form.isConnected);
    liveChecks = liveChecks.filter(({form}) => form.isConnected);
    wireShowWhen(root);
    wireChoices(root);
    wireLockedWhen(root);
    wireNotBefore(root);
    wirePattern(root);
    wireDuidList(root);
    wireDistinct(root);
    wireComplete(root);
    wireNotFuture(root);
    wireLiveCheck(root);
  };
  wireConditional(document);
  document.addEventListener("parishkit:swap", (event) => {
    // A swapped region's old forms are gone: their live checks go too, so a
    // page restored from history never posts a check for a removed field.
    liveChecks = liveChecks.filter(({form}) => form.isConnected);
    wireConditional(event.target);
    // A swapped part of a gated form (Hosted files' Upload line, #563) brings
    // fresh buttons: the form's gate holds them again at once.
    const form = event.target.parentElement?.closest("form[data-require-complete]");
    if (form) gateComplete(form);
  });
  window.addEventListener("pageshow", () => {
    showWhenUpdates.forEach(({update}) => update());
    choiceUpdates.forEach(({update}) => update());
    lockedUpdates.forEach(({update}) => update());
    document.querySelectorAll("input[data-not-before]").forEach(checkNotBefore);
    patternChecks.forEach(({check}) => check());
    distinctChecks.forEach(({check}) => check(true));
    completeForms.forEach(gateComplete);
    notFutureChecks.forEach(({check}) => check());
    liveChecks = liveChecks.filter(({form}) => form.isConnected);
    liveChecks.forEach(({check}) => check());
  });

  // Optional modules are ordinary accessible fieldsets shown or hidden by
  // their toggle. Hidden fields are disabled, not silently copied into
  // submitted data. The
  // server independently rejects stray data for every disabled module. The
  // back/forward cache can restore a module's tick without a change event,
  // so every group is set again on pageshow (#563); otherwise a restored
  // Financial tick could leave its fields disabled and unsent.
  //
  // Campaign settings' form is redrawn in place once its change is applied
  // (#1119), so this wiring, the period end filling and the overlap
  // confirmation below all run again for each swapped region
  // (parishkit:swap), as well as at load. A pageshow re-run skips what a
  // swap removed.
  let campaignUpdates = []; // {node, update} for each wired group
  // Entries of groups a swap removed are dropped, never run.
  const pruneCampaignUpdates = () => {
    campaignUpdates = campaignUpdates.filter(({node}) => node.isConnected);
  };
  window.addEventListener("pageshow", () => {
    pruneCampaignUpdates();
    campaignUpdates.forEach(({update}) => update());
  });
  const wireCampaignModules = (root) => within(root, "[data-campaign-form]").forEach((form) => {
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
        // A form that adopts the complete-before-submit gate sees the
        // fields this shows or hides, also after a pageshow re-run.
        gateComplete(form);
      }
      toggle.addEventListener("change", update);
      campaignUpdates.push({node: group, update});
      update();
    });
  });
  wireCampaignModules(document);

  // A financial period is one year: entering its start fills an empty end
  // with the day before the first anniversary (periodEnd, above). An end
  // the person typed is never replaced; one filled here follows later start
  // edits (typing a year passes through values like 0002-01-01). The server
  // still checks every period.
  const wireFillsEnd = (root) => within(root, "input[data-fills-end]").forEach((start) => {
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
  wireFillsEnd(document);

  // The overlap confirmation is needed only while the financial period and
  // the campaign share at least one day (campaign_forms.overlaps). Hidden, it
  // is also unchecked so a stale confirmation is never submitted. The server
  // renders it visible whenever it is needed, so this is only a convenience.
  // Restored dates (the back/forward cache) fire no event: pageshow checks
  // again (#563).
  const wireOverlap = (root) => within(root, "[data-overlap-confirmation]").forEach((group) => {
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
      // Under the complete gate (Campaign settings, First campaign) a shown
      // confirmation must be ticked before Review (#563). Only there: the
      // schedule pages share this group without the gate, and a required
      // box would bring the browser's own popup to them instead.
      if (form.hasAttribute("data-require-complete")) box.required = needed;
      gateComplete(form); // As for campaign modules above.
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    campaignUpdates.push({node: group, update});
    update();
  });
  wireOverlap(document);
  document.addEventListener("parishkit:swap", (event) => {
    if (!(event.target instanceof Element)) return;
    pruneCampaignUpdates();
    wireCampaignModules(event.target);
    wireFillsEnd(event.target);
    wireOverlap(event.target);
  });

  // The chosen email, described under a schedule's email list (#446): the
  // start of its text, the saved schedules that send it, a warning when
  // editing it would change other schedules too, and (on Dates and mail
  // schedules) links to preview, test and edit it, which open in a new tab so
  // this page's unsaved changes stay. The option's data- attributes come
  // from schedule_forms.email_details; every value is set as text.
  const emailSummary = (row, template) => {
    const summary = row.querySelector("[data-email-summary]");
    if (!summary) return;
    // The list is described by its summary as well as its help text.
    const described = (template.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
    if (summary.id && !described.includes(summary.id)) {
      template.setAttribute("aria-describedby", [...described, summary.id].join(" "));
    }
    const option = template.selectedOptions[0];
    summary.replaceChildren();
    if (!option || !option.value) return;
    let users = [];
    try { users = JSON.parse(option.dataset.usedBy || "[]"); } catch { users = []; }
    const line = (text) => {
      const paragraph = document.createElement("p");
      paragraph.className = "help";
      paragraph.textContent = text;
      summary.append(paragraph);
      return paragraph;
    };
    if (option.dataset.excerpt) line(`Starts: ${option.dataset.excerpt}`);
    line(users.length ? `Sent by: ${users.join(", ")}` : "No saved schedule sends this email yet.");
    const own = row.dataset.scheduleLabel;
    if (users.some((user) => user !== own)) {
      line("Editing this email changes it for every schedule that sends it.");
    }
    const links = [["testUrl", "Preview and send a test"], ["editUrl", "Edit this email"]]
      .filter(([key]) => option.dataset[key]);
    if (links.length) {
      const paragraph = line("");
      links.forEach(([key, text], index) => {
        if (index) paragraph.append(" · ");
        const link = document.createElement("a");
        link.href = option.dataset[key];
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = `${text} (opens in a new tab)`;
        paragraph.append(link);
      });
    }
    // While the campaign admits no test mail, the server sends why in place
    // of the test address (#923), so the page never links a dead end.
    if (option.dataset.testNote) line(option.dataset.testNote);
  };

  // Action tables (#879; admin-portal spec, "Row actions and confirmation").
  // A control marked data-confirm-open="<dialog id>" (a row's Delete in
  // components/table-actions.html, or the bulk bar's button) opens that
  // components/confirm-dialog.html <dialog> as a modal, naming the row or the
  // count of ticked rows. Cancel, Escape or the dialog closing changes
  // nothing. Confirm posts the table form's fields (its CSRF token and any
  // base version) plus the chosen rows' values, with fetch and no page load.
  // The server answers with the configuration change's status page (or a
  // redirect back to this page when nothing is left to wait for), and this
  // follows that status until the change is applied, then redraws every
  // region from the table region's data-refresh-url (the page's own address,
  // sort included), closes the dialog and announces what was done. A
  // refusal, or a change that did not apply, is shown inside the dialog,
  // below its buttons, and nothing is redrawn. Focus stays in the dialog
  // while it is open (Tab wraps), and returns to the control that opened it,
  // or to the dialog's data-confirm-fallback element when that row is gone.
  // The server checks every chosen row itself.
  const CONFIRM_POLL_MS = 1000;
  const CONFIRM_WAIT_MS = 30000;
  // A refusal with no explanation of its own, by status. A bare 400 means
  // the page sent something the server did not expect (most often a page
  // left open while it changed), never an outage: retrying cannot help, a
  // reload can (#1400, #1401). A 404 or 410 means the item is gone; only an
  // outage (5xx, or no status here) keeps "try again in a moment".
  const CONFIRM_REFUSED = {
    400: "This was not done: the page sent something the server did not expect. Reload the page, then try again.",
    404: "This was not done: it no longer exists. Reload the page.",
    410: "This was not done: it no longer exists. Reload the page.",
    403: "This was not done: you are not allowed to do this now, or your sign-in has ended. Reload the page.",
    409: "This was not done: the page is out of date. Reload the page, then try again.",
  };
  const CONFIRM_UNAVAILABLE = "This was not done: the server can't do it right now. Try again in a moment.";
  const CONFIRM_UNREACHABLE = "The server could not be reached. Reload the page to check whether this was done.";
  const CONFIRM_STATUS_LOST = "The change was saved, but its status can no longer be checked: your sign-in ended or your account no longer has access. Reload the page to see the list.";
  const fillIn = (text, values) => String(text || "").replace(/\{(\w+)\}/g,
    (whole, key) => (key in values ? String(values[key]) : whole));
  const pause = (ms) => new Promise((resolve) => { window.setTimeout(resolve, ms); });
  const focusable = (root) => [...root.querySelectorAll(
    "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((node) => !node.disabled && !node.hidden && node.getClientRects().length);
  // The open dialog's request: {dialog, opener, form, chosen, values, busy,
  // done}, and for a review dialog (below) review: {apply, done} instead of
  // chosen and values.
  let confirming = null;
  const wiredDialogs = new WeakSet();
  const wireDialog = (dialog) => {
    if (wiredDialogs.has(dialog)) return;
    wiredDialogs.add(dialog);
    // Escape asks to cancel; not while the request is in flight, since the
    // change may already be under way. The keydown is refused too: Chromium
    // closes a dialog on a second Escape even when its cancel event was
    // refused, unless the key itself is. Should the dialog close anyway,
    // it opens again until the answer arrives.
    const busy = () => confirming?.dialog === dialog && confirming.busy;
    dialog.addEventListener("cancel", (event) => {
      if (busy()) event.preventDefault();
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && busy()) event.preventDefault();
    });
    dialog.addEventListener("close", () => {
      const state = confirming?.dialog === dialog ? confirming : null;
      if (state?.busy && !state.done) {
        dialog.showModal();
        return;
      }
      confirming = null;
      // A key's confirmation names the key's last four characters: they
      // go with the dialog however it closes (Apply, Cancel, Escape), so
      // the key stays only in its field and Apply's one request (#1568).
      if (state?.review?.key) dialog.querySelector("[data-review-body]")?.replaceChildren();
      if (!state || state.done) return;
      const back = state.opener.isConnected ? state.opener
        : document.getElementById(dialog.dataset.confirmFallback || "");
      back?.focus();
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const nodes = focusable(dialog);
      if (!nodes.length) return;
      const [first, last] = [nodes[0], nodes[nodes.length - 1]];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    dialog.querySelector("[data-confirm-cancel]")?.addEventListener("click", () => {
      if (!confirming?.busy) dialog.close();
    });
    // A review answer's typed field (data-review-require, the Confirm
    // Production dialog's "Type Production to confirm", #1585) is gated
    // while it is edited, and Enter in it is Apply, never a native submit of
    // the Apply form it names: Apply always posts from acceptReview. The
    // Enter that commits an input method's composition is not Apply.
    dialog.addEventListener("input", (event) => {
      if (event.target instanceof Element && event.target.matches("[data-review-require]")) gateReview(dialog);
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing || !(event.target instanceof Element)
        || !event.target.matches("[data-review-require]")) return;
      event.preventDefault();
      const accept = dialog.querySelector("[data-confirm-accept]");
      if (accept && !accept.disabled && !accept.hidden) accept.click();
    });
    dialog.querySelector("form[data-review-apply]")?.addEventListener("submit", (event) => {
      event.preventDefault();
    });
    dialog.querySelector("[data-confirm-accept]")?.addEventListener("click", (event) => {
      // A dialog with fields makes Confirm its form's submit button, so the
      // form's complete gate holds it (confirm-dialog.html fields_template);
      // it is still posted only from here, never natively.
      event.preventDefault();
      // An unavailable Apply (data-review-unavailable) does nothing.
      if (confirming?.dialog !== dialog || confirming.busy || confirming.unavailable) return;
      if (confirming.review) acceptReview(confirming);
      else acceptConfirm(confirming);
    });
  };
  // A bulk button may carry its own dialog text and post its own name and
  // value (confirm-dialog.html; the Ministry follow-up queue's bulk
  // outcomes, #1131). One limited to one kind of row is available only
  // while every ticked row is of that kind (wireSelection), so the dialog
  // acts on every ticked row. A row's control may carry its own dialog
  // text too, and post more of its row's fields than its own name and
  // value: data-confirm-with, a query string (Census changes' decision,
  // version and request key, #879), so one dialog serves every row.
  const openConfirm = (dialog, opener) => {
    const form = document.getElementById(dialog.dataset.confirmForm || "");
    if (!(form instanceof HTMLFormElement)) return;
    const bulk = opener.hasAttribute("data-bulk-action");
    // form.elements, not the form's descendants: a table whose headings are
    // forms of their own ties its row boxes to the form by their form
    // attribute.
    const chosen = bulk
      ? [...form.elements].filter((box) => box.matches("input[data-select-row]:checked")
        && !box.disabled)
        .map((box) => ({field: box.name, value: box.value, name: box.dataset.confirmName}))
      : [{field: opener.name, value: opener.value, name: opener.dataset.confirmName}];
    if (!chosen.length || chosen.some((item) => !item.field || !item.value)) return;
    // The button's own text, else the dialog's.
    const text = (key) => opener.dataset[key] || dialog.dataset[key] || "";
    wireDialog(dialog);
    const values = {count: chosen.length.toLocaleString("en-US"), name: chosen[0].name || ""};
    dialog.querySelector("[data-confirm-title]").textContent = fillIn(
      chosen.length === 1 ? text("titleOne") : text("titleMany"), values);
    // A caution for marked rows only (confirm-dialog.html caution_marked):
    // said while some chosen row's control carries data-confirm-caution.
    const caution = dialog.querySelector("[data-confirm-caution-marked]");
    if (caution) {
      caution.hidden = !(bulk
        ? [...form.elements].some((box) => box.matches("input[data-select-row]:checked")
          && !box.disabled && box.hasAttribute("data-confirm-caution"))
        : opener.hasAttribute("data-confirm-caution"));
    }
    const body = dialog.querySelector("[data-confirm-body]");
    if (body) {
      if (body.dataset.default === undefined) body.dataset.default = body.textContent;
      body.textContent = opener.dataset.confirmBody || body.dataset.default;
    }
    // A consequence the opener says on its own (confirm-dialog.html warning):
    // its own error notice, hidden while this opener carries none (#2041).
    const warning = dialog.querySelector("[data-confirm-warning]");
    if (warning) {
      warning.textContent = opener.dataset.confirmWarning || "";
      warning.hidden = !warning.textContent;
    }
    const error = dialog.querySelector("[data-confirm-error]");
    error.hidden = true;
    error.replaceChildren();
    dialog.querySelector("[data-confirm-status]").textContent = "";
    dialog.querySelectorAll("[data-confirm-accept], [data-confirm-cancel]").forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-disabled");
      button.removeAttribute("aria-busy");
      button.classList.remove("is-busy");
    });
    // Fields start again from their defaults each time, so an earlier
    // contact attempt is never recorded again unasked; each change event
    // lets the rules on them (data-enabled-when, the complete gate) follow.
    // Those events are not the reader's typing, so the form's "edited since"
    // mark is put back as it was: a region that redraws the dialog's form
    // (a Family hub tab, #2064) must not then say typed changes were lost.
    const edited = editedSince.get(form.id);
    dialog.querySelectorAll("[data-confirm-reset]").forEach((field) => {
      if (field instanceof HTMLSelectElement) {
        const first = [...field.options].find((option) => option.defaultSelected) || field.options[0];
        field.value = first ? first.value : "";
      } else {
        field.value = field.defaultValue;
      }
      field.dispatchEvent(new Event("change", {bubbles: true}));
    });
    if (edited) editedSince.set(form.id, edited);
    else editedSince.delete(form.id);
    // The review worklist's choice field (#1681): a nameless hidden input
    // marked data-confirm-choice, which a data-required-when rule on the
    // dialog's note reads, so one dialog asks for a note only when the
    // action needs one. A row's icon gives its data-confirm-choice
    // ("reopen" when it undoes a terminal step; an Ignored item's note is
    // optional, #1889); a bulk button's holds only while some ticked row's
    // box carries the same choice, so sending items back to review in bulk
    // asks for a reason only when it undoes a terminal step (review pipeline
    // spec, "Undoing a terminal stage"). The "no_note"
    // choice is an action that records no note (a Ministry roster tick, R39):
    // it holds for every ticked row, and it hides the note (data-note-field),
    // which data-confirm-reset has just emptied, so an empty one is posted.
    const want = opener.dataset.confirmChoice || "";
    const holds = !bulk || want === "no_note" || [...form.elements].some((box) =>
      box.matches("input[data-select-row]:checked") && box.dataset.confirmChoice === want);
    dialog.querySelectorAll("input[data-confirm-choice]").forEach((field) => {
      field.value = want && holds ? want : "";
    });
    dialog.querySelectorAll("[data-note-field]").forEach((field) => {
      field.hidden = want === "no_note";
    });
    if (form.hasAttribute("data-require-complete")) gateComplete(form);
    confirming = {
      dialog, opener, form, chosen, values, busy: false, done: false,
      doneOne: text("doneOne"), doneMany: text("doneMany"),
      extra: bulk ? (opener.name ? [[opener.name, opener.value]] : [])
        : [...new URLSearchParams(opener.dataset.confirmWith || "")],
    };
    dialog.showModal();
    // The safe choice takes focus first.
    dialog.querySelector("[data-confirm-cancel]")?.focus();
  };
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const opener = event.target.closest("[data-confirm-open]");
    if (!opener || opener.disabled) return;
    const dialog = document.getElementById(opener.dataset.confirmOpen);
    if (!(dialog instanceof HTMLDialogElement)) return;
    event.preventDefault();
    openConfirm(dialog, opener);
  });
  // Show why the action was not done, below the dialog's buttons; ``link``
  // is an optional same-origin {href, text} (a change's status page).
  // text is a sentence, or the nodes of a refusal the server drew
  // (refusalMarkup), shown as drawn.
  const refuseConfirm = (state, text, link) => {
    const error = state.dialog.querySelector("[data-confirm-error]");
    if (Array.isArray(text)) {
      error.replaceChildren(...text);
      // A drawn refusal can carry a control (a link), which must not be
      // read as part of the announcement: only its sentences are the
      // alert, so the line itself is no live region then.
      error.removeAttribute("role");
      error.querySelectorAll(":scope > p").forEach((line) => {
        line.setAttribute("role", "alert");
      });
    } else {
      // A sentence is the whole alert (a drawn refusal may have removed it).
      error.setAttribute("role", "alert");
      const line = document.createElement("p");
      line.textContent = text;
      error.replaceChildren(line);
    }
    if (link && sameOrigin(link.href)) {
      const anchor = document.createElement("a");
      anchor.href = link.href;
      anchor.textContent = link.text;
      error.append(anchor);
    }
    // A review dialog's body keeps its height while the refusal shows
    // (#1482): its height limit then rises by the refusal's room (ui-v1.css),
    // and the body, flexing, would otherwise take that room and move the
    // buttons. A max-height, so a window made smaller still shrinks it.
    const body = state.review && state.dialog.querySelector("[data-review-body]");
    if (body && error.hidden) body.style.maxHeight = `${body.getBoundingClientRect().height}px`;
    error.hidden = false;
    state.dialog.querySelector("[data-confirm-status]").textContent = "";
    const accept = state.dialog.querySelector("[data-confirm-accept]");
    accept.classList.remove("is-busy");
    accept.removeAttribute("aria-disabled");
    accept.removeAttribute("aria-busy");
    // The same request would be refused again; Cancel stays.
    accept.disabled = true;
    const cancel = state.dialog.querySelector("[data-confirm-cancel]");
    cancel.disabled = false;
    state.busy = false;
    cancel.focus();
  };
  // A refusal whose errors name fields in the dialog (field_id: the bulk
  // outcome's contact date and time, #1332, by id; or Census changes' Edit
  // value, #879, by its name in the dialog's form, since each row's field
  // has its own id) is shown at those fields instead: the message fills the
  // dialog's field-error line (its first .errorlist with an id) and each
  // field is marked and described by it.
  // Confirm stays unavailable, naming the line, until one of them is
  // edited: the edit clears the mark (wireValidity) and then lets the
  // form's complete gate decide again. Any change on the form counts,
  // since choosing no contact attempt greys Date and Time without an event
  // on them. False when nothing was marked.
  const refuseAtFields = (state, raw, text) => {
    let ids = [];
    try {
      ids = (JSON.parse(raw)?.errors || []).map((item) => item?.field_id).filter(Boolean);
    } catch { return false; }
    // A name two controls share (the census address editor's State: a
    // drop-down for the United States, free text otherwise, #1785) is a
    // RadioNodeList; the enabled one, which was sent, is marked.
    const fields = [...new Set(ids)].flatMap((id) => {
      const found = document.getElementById(id) || state.form.elements.namedItem(id);
      return found instanceof RadioNodeList ? [...found] : [found];
    })
      .filter((node) => node instanceof HTMLElement && state.dialog.contains(node)
        && node.matches("input, select, textarea") && !node.disabled);
    const list = state.dialog.querySelector(".errorlist[id]");
    if (!fields.length || !list) return false;
    const item = list.querySelector("li") || list.appendChild(document.createElement("li"));
    item.textContent = text;
    list.hidden = false;
    list.dataset.source = "server";
    const describe = (node) => node.setAttribute("aria-describedby", [...new Set([
      ...(node.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean), list.id])].join(" "));
    fields.forEach((node) => {
      node.setAttribute("aria-invalid", "true");
      node.setAttribute("data-field-error", "");
      describe(node);
    });
    state.dialog.querySelector("[data-confirm-error]").hidden = true;
    state.dialog.querySelector("[data-confirm-status]").textContent = "";
    const accept = state.dialog.querySelector("[data-confirm-accept]");
    accept.classList.remove("is-busy");
    accept.removeAttribute("aria-disabled");
    accept.removeAttribute("aria-busy");
    accept.disabled = true;
    describe(accept);
    state.dialog.querySelector("[data-confirm-cancel]").disabled = false;
    state.busy = false;
    // In the capture phase on the form, so the button stops naming the
    // line before wireValidity's clearFieldError (on the field) runs:
    // that would otherwise mark the button aria-invalid too.
    const done = new AbortController();
    const release = () => {
      done.abort();
      const kept = (accept.getAttribute("aria-describedby") || "").split(/\s+/)
        .filter((id) => id && id !== list.id);
      if (kept.length) accept.setAttribute("aria-describedby", kept.join(" "));
      else accept.removeAttribute("aria-describedby");
      if (confirming !== state) return;
      accept.disabled = false;
      if (state.form.hasAttribute("data-require-complete")) gateComplete(state.form);
    };
    ["input", "change"].forEach((type) =>
      state.form.addEventListener(type, release, {capture: true, signal: done.signal}));
    state.dialog.addEventListener("close", release, {signal: done.signal});
    fields[0].focus();
    return true;
  };
  // A refused answer's message: the server's own explanation when it gives
  // one (a JSON refusal), else a plain one for its status.
  const refusalText = (status, text) => {
    try {
      const refusal = JSON.parse(text)?.refusal;
      if (refusal?.message) return [refusal.message, refusal.fix].filter(Boolean).join(" ");
    } catch { /* not JSON: the plain message below */ }
    return CONFIRM_REFUSED[status] || CONFIRM_UNAVAILABLE;
  };
  // A refusal the server draws as markup, marked data-review-refusal: the
  // nodes to show, or null for any other answer. System health's actions
  // (#1356) answer so, for a refusal a sentence cannot carry.
  const refusalMarkup = (text) => {
    const node = new DOMParser().parseFromString(text, "text/html")
      .querySelector("[data-review-refusal]");
    return node ? [...node.childNodes].map((child) => document.importNode(child, true)) : null;
  };
  // A change's status page: {state, text} from its data-live-status region,
  // or null for any other page (the change needed no waiting).
  const changeStatus = (text) => {
    const parsed = new DOMParser().parseFromString(text, "text/html");
    const region = parsed.querySelector("[data-live-status]");
    if (!region) return null;
    return {
      state: region.getAttribute("data-live-state") || "",
      text: squeeze([...region.querySelectorAll("h2, p")].slice(0, 2)
        .map((node) => node.textContent).join(" ")),
    };
  };
  const readPage = async (url, init) => {
    const response = await fetch(url, {...init, credentials: "same-origin",
      headers: {...init?.headers, "X-Requested-With": "fetch"}});
    return {response, text: await response.text()};
  };
  // Post one confirmed change (url, body) from the open dialog and follow its
  // status until it is applied. The dialog is busy from the start: Cancel
  // and Escape do nothing until the answer. onRecorded runs once the server
  // has recorded the change. A review dialog's Apply shows no spinner (its
  // width would move Cancel beside it): its progress is the status line's.
  // Returns the POST's answer ({response, text}) once it is applied;
  // otherwise refuseConfirm has said why in the dialog, and it returns false.
  // Follow a recorded change's status (statusUrl, a passive read) from its
  // first reading, status, until it settles or CONFIRM_WAIT_MS passes.
  // Returns {status, outcome}: "settled" (applied, failed or cancelled, or
  // no status to follow), "waiting" (still pending at the deadline) or
  // "lost" (signed out or no longer allowed to read the status, such as
  // after a change that removed the reader's own access: waiting cannot
  // help, so it ends at once, #952). The review dialog's Apply and a
  // campaign wizard step's direct save (#1404 PR 4) both follow with it.
  const settleChange = async (statusUrl, status) => {
    const deadline = Date.now() + CONFIRM_WAIT_MS;
    while (status && !["applied", "failed", "cancelled"].includes(status.state)) {
      if (Date.now() >= deadline) return {status, outcome: "waiting"};
      await pause(CONFIRM_POLL_MS);
      let next;
      try {
        next = await readPage(statusUrl, {method: "GET"});
      } catch { continue; /* keep waiting until the deadline */ }
      if ([401, 403, 404].includes(next.response.status) || next.response.redirected) {
        return {status, outcome: "lost"};
      }
      if (next.response.ok) status = changeStatus(next.text) || status;
    }
    return {status, outcome: "settled"};
  };
  const followChange = async (state, url, body, onRecorded) => {
    const {dialog} = state;
    state.busy = true;
    const accept = dialog.querySelector("[data-confirm-accept]");
    accept.setAttribute("aria-disabled", "true");
    // In flight, not unavailable: the busy pointer without the spinner on a
    // review's Apply (ui-v1.css).
    accept.setAttribute("aria-busy", "true");
    if (!state.review) accept.classList.add("is-busy");
    dialog.querySelector("[data-confirm-cancel]").disabled = true;
    dialog.querySelector("[data-confirm-error]").hidden = true;
    dialog.querySelector("[data-confirm-status]").textContent = dialog.dataset.working || "Working…";
    let answer;
    try {
      answer = await readPage(url, {method: "POST", body});
    } catch {
      // It may have reached the server: never send it again.
      refuseConfirm(state, CONFIRM_UNREACHABLE);
      return false;
    }
    if (!answer.response.ok) {
      // A refusal the server draws (data-review-refusal, #1364) is shown as
      // drawn; any other is marked at its fields when it names them (z27),
      // else said in the dialog.
      const markup = refusalMarkup(answer.text);
      const text = refusalText(answer.response.status, answer.text);
      if (markup) refuseConfirm(state, markup);
      else if (!refuseAtFields(state, answer.text, text)) refuseConfirm(state, text);
      return false;
    }
    onRecorded?.();
    // Follow the change until it is applied (a passive status read). A
    // review dialog that leaves for the page Apply's answer redirected to
    // (data-review-redraw="leave": Start go-live, #1585) follows nothing:
    // that page follows its own work.
    const statusUrl = answer.response.url;
    const {status, outcome} = await settleChange(statusUrl,
      dialog.dataset.reviewRedraw === "leave" ? null : changeStatus(answer.text));
    if (outcome === "waiting") {
      refuseConfirm(state, "The change is saved but not applied yet, so the list is unchanged for now.",
        {href: statusUrl, text: "See the change's status"});
      return false;
    }
    if (outcome === "lost") {
      refuseConfirm(state, CONFIRM_STATUS_LOST);
      return false;
    }
    if (status && status.state !== "applied") {
      refuseConfirm(state, status.text || "The change was not applied. Nothing was changed.",
        {href: statusUrl, text: "See the change's status"});
      return false;
    }
    return answer;
  };
  // The page at address after an applied change, parsed, or null when it
  // could not be read: the change is applied, so an ordinary load of the
  // address shows it instead.
  const freshPage = async (address) => {
    try {
      const page = await readPage(address, {method: "GET"});
      if (!page.response.ok) throw new Error("refresh refused");
      return new DOMParser().parseFromString(page.text, "text/html");
    } catch {
      window.location.assign(address);
      return null;
    }
  };
  // Swap every region of this page that the fresh page also has, except one
  // only its own control replaces (data-in-place-own). ``own`` is the region
  // whose control's answer this is (a review dialog's form's region, #750),
  // which is swapped even when it is marked so.
  const redrawFrom = (fresh, own = null) => {
    document.querySelectorAll(REGIONS).forEach((other) => {
      if (ownedElsewhere(other, own)) return;
      const copy = fresh.getElementById(other.id);
      if (isRegion(copy)) swapRegion(other, copy);
    });
    syncControls(fresh);
    // A redrawn form takes its new saved values (and version) at once, so
    // its Review changes waits for the next real change (#1119).
    gateChanges();
  };
  const acceptConfirm = async (state) => {
    const {dialog, form, chosen} = state;
    // The form's own fields (CSRF, base version), never its row boxes: the
    // chosen rows are exactly the ones the dialog named, and the opener's
    // own fields (state.extra) are sent once, as it gives them.
    const fields = new Set([...chosen.map((item) => item.field),
      ...state.extra.map(([name]) => name)]);
    const body = new URLSearchParams();
    new FormData(form).forEach((value, name) => {
      if (!fields.has(name) && typeof value === "string") body.append(name, value);
    });
    chosen.forEach((item) => body.append(item.field, item.value));
    state.extra.forEach(([name, value]) => body.append(name, value));
    // The answer's page may carry a data-confirm-announce line (below).
    const answer = await followChange(state, form.action, body);
    if (!answer) return;
    // Applied: redraw every region from the table's own address. A dialog
    // whose form sits outside the table's region (its rows tied to it by
    // their form attribute) finds the region from the button that opened it.
    // A page whose view travels only in POST bodies (Census changes, #528)
    // has no address that shows it again, so its dialog posts to the page
    // itself with the view's fields, and the answer is that page, redrawn
    // from directly (data-confirm-answer-page).
    const region = form.closest(REGIONS) || state.opener.closest(REGIONS);
    // A row's control in a table whose rows have ids (Census changes, #879)
    // returns focus to that row's first action once it is redrawn, so the
    // reader stays where they were; when the row is gone, the fallback. An
    // item drawn on a line of its own rather than a table row (a Family
    // hub tab's New Member addition, #2064) is marked data-action-row.
    const row = state.opener.closest("tr[id], [data-action-row][id]")?.id;
    // A review worklist icon (data-row-action, #1681) returns focus to the
    // same action's icon in the redrawn row, now drawn selected, so the
    // reader hears the decision in force (data-focus-action names another
    // when the decision is drawn on it: an Edit's is its Accept); otherwise
    // to the row's first action, as above.
    const kind = state.opener.dataset.focusAction || state.opener.dataset.rowAction || "";
    const fresh = dialog.hasAttribute("data-confirm-answer-page")
      ? new DOMParser().parseFromString(answer.text, "text/html")
      : await freshPage(region?.dataset.refreshUrl || window.location.href);
    if (!fresh) return;
    state.done = true;
    dialog.close();
    const fallback = dialog.dataset.confirmFallback || "";
    redrawFrom(fresh);
    const redrawn = row ? document.getElementById(row) : null;
    const target = (state.opener.isConnected && state.opener)
      || (kind && redrawn?.querySelector(`[data-row-action="${CSS.escape(kind)}"]`))
      || redrawn?.querySelector("[data-confirm-open]:not(:disabled)")
      || document.getElementById(fallback) || document.querySelector("main h1");
    target?.focus({preventScroll: true});
    target?.scrollIntoView({block: "nearest"});
    // An action that turned out to change nothing says so on the page its
    // answer redirects to, in a hidden data-confirm-announce line (Signed-in
    // sessions: the session had already ended, #953). That line is said
    // instead of the dialog's own "done" words, which would claim a change.
    const outcome = new DOMParser().parseFromString(answer.text, "text/html")
      .querySelector("[data-confirm-announce]")?.textContent.trim();
    announce(outcome || fillIn(chosen.length === 1 ? state.doneOne : state.doneMany,
      state.values));
  };

  // The review dialog of Admin editors (UX conventions, "Review dialog",
  // #750): components/confirm-dialog.html with review=True, opened by a form
  // marked data-review-open="<dialog id>" (New and Edit user, Import
  // Ministry leaders, New and Edit scheduled email, Campaign settings, its
  // date change on Dates and mail schedules, Share options, Campaign images,
  // Parish logos, Parish settings, the integration settings pages and Create
  // the campaign's confirmation).
  //
  // Review posts the form, submitter included, without a page load. Until
  // the answer arrives the form is inert and aria-busy, and its Review
  // button shows it is working, so nothing can be edited meanwhile. Then:
  // - an accepted review (a fragment marked data-review-answer, whose
  //   data-review-done is the start of the status line) fills the dialog's
  //   body. Its hidden fields move into the dialog's Apply form, which posts
  //   them all: the signed preview (marked data-review-preview) and any
  //   other an editor needs (a version, an in-place page). The dialog opens
  //   as a modal, focus on its title (never on Apply). No preview (nobody
  //   left to import) means no Apply, unless the answer marks a reason
  //   data-review-unavailable: then Apply is shown greyed with that reason
  //   (Past campaigns' file already imported).
  // - a review refused for a field's value (status 400 drawing this same
  //   form) leaves the dialog shut: each field takes the answer's error line
  //   and aria-invalid mark, and the answer's error summary is shown above
  //   the page heading and focused, as on a page the server drew.
  // - any other refusal (a stale page, a refusal in the server's words, no
  //   answer at all) opens the dialog with the refusal below its buttons and
  //   no Apply. The form keeps the reader's values at their version.
  // Escape or Cancel closes the dialog, changing nothing, and focus returns
  // to Review. Apply is the Delete dialog's (followChange): from then on the
  // dialog cannot be dismissed until the answer, its progress shown in the
  // status line (no spinner on Apply, so Cancel never moves); once the
  // change is recorded Apply is hidden (its space kept) and focus moves to
  // the title; once applied the dialog closes, and either the page goes
  // to the dialog's data-review-return (a New or Edit page's table) or, with
  // none, every region is redrawn from the form's region's data-refresh-url
  // (or, for a dialog marked data-review-redraw="answer", from the address
  // Apply's answer ended on).
  // A form may name the region Apply redraws as its own in
  // data-review-region (Finish switching on an integration's status line
  // redraws the settings editor too, since the switch may change settings
  // the form shows, #1568).
  // Either way the page's reserved status line then says what was applied
  // and when, and takes focus. That line is the one the form that opened
  // the dialog names with its own data-review-outcome, else the dialog's:
  // a page whose panes share one dialog (Campaign images' slots) gives each
  // pane its own line, so the status shows next to the control that caused
  // it and the page never scrolls to a line elsewhere (UX conventions,
  // "Review dialog"). A line inside a redrawn region is written after the
  // redraw, so the redraw never wipes it. Across a page change that line's text waits in sessionStorage,
  // for that address only and for a minute: it is the review's short
  // sentence and a time, never an answer, a value or a credential.
  //
  // A form marked data-review-key="<field>" holds a secret (an
  // integration's new key, #1568; UX conventions, "Review dialog", its
  // recorded exception). While that field holds a value, Review posts the
  // form without it, with key_review=yes, so the server reviews everything
  // else and never receives the key at Review. Its answer, marked
  // data-review-key, is a confirmation with no signed preview: its
  // [data-review-key-tail] sentence takes the key's last four characters,
  // read here from the field ({tail}; the server never draws any part of a
  // key), and Apply posts the page's own form, key included, to its own
  // address (acceptKey). That answer is the page again: a refusal for a
  // value (400, drawing the form) closes the dialog and marks the field,
  // any other is shown in the dialog, and otherwise the dialog closes and
  // the page's regions are redrawn from it, the form's own included. The
  // key's slow check is then followed by the page's status line
  // (live-status-v1.js), not the dialog.
  //
  // The secret field a data-review-key form holds a value in, or null.
  const keyField = (form) => {
    const field = form.dataset.reviewKey && form.elements.namedItem(form.dataset.reviewKey);
    return field && "value" in field && !SERVER_SPACE.test(field.value) ? field : null;
  };
  const REVIEW_OUTCOME = "pk-review-outcome";
  const REVIEW_OUTCOME_MS = 60000;
  const REVIEW_UNREACHABLE = "The server could not be reached, so nothing was reviewed. Try again in a moment.";
  const reviewing = new WeakSet();
  const showOutcome = (id, text) => {
    const line = id ? document.getElementById(id) : null;
    if (!line) return false;
    line.textContent = text;
    // One line high: a line cut short is whole in its title.
    line.title = text;
    line.focus({preventScroll: true});
    line.scrollIntoView({block: "nearest"});
    return true;
  };
  // A kept line may also name the element focus moves to (focus: an id):
  // a campaign wizard sub-page's return focuses its item's row on Pages and
  // emails (#1404 PR 6b; UX conventions, "Wizards", rule 10), after the
  // line is written, so the line is still read as a status.
  try {
    const kept = JSON.parse(window.sessionStorage.getItem(REVIEW_OUTCOME) || "null");
    window.sessionStorage.removeItem(REVIEW_OUTCOME);
    if (kept && kept.path === window.location.pathname
      && Date.now() - kept.at < REVIEW_OUTCOME_MS && showOutcome(kept.id, kept.text)) {
      const row = typeof kept.focus === "string" && kept.focus
        ? document.getElementById(kept.focus) : null;
      if (row) {
        row.focus({preventScroll: true});
        row.scrollIntoView({block: "nearest"});
      }
    }
  } catch { /* No storage: the page shows no status line. */ }
  // The status line's time, in the parish style's clock and this browser's
  // time zone, through the shared formatter (date-format-v1.js, which every
  // page loads from base.html; the guard only keeps Apply working without it).
  const clockTime = () => (window.ParishDates ? window.ParishDates.time(new Date()) : "");
  // An address from an attribute, never the form's action property, which
  // a field named "action" shadows.
  const formUrl = (form) => new URL(form.getAttribute("action") || "", document.baseURI).href;
  // A form that names its pane's own reserved status line (Campaign images'
  // slots, data-review-outcome) shows a refused Review there (#1410). A
  // form inside a dialog of its own (the Campaign calendar's Move…, #1521)
  // names its refusal line with data-review-refused instead, so a refusal
  // stays in its dialog while Apply's status goes to the page's line.
  const paneLine = (form) => {
    const id = form.dataset.reviewRefused || form.dataset.reviewOutcome;
    return id ? document.getElementById(id) : null;
  };
  // A refusal written to a pane's line goes once it no longer applies: on
  // an edit of a form naming the line, when a Review from the pane is
  // accepted, or when the line takes another refusal. The pane's forms
  // share the line (Campaign images' upload and Remove this image), so
  // every field the line describes loses that description and its server
  // mark, whichever form cleared it.
  const clearPaneRefusal = (form) => {
    const line = paneLine(form);
    if (!line?.hasAttribute("data-refusal")) return;
    line.removeAttribute("data-refusal");
    line.textContent = "";
    line.removeAttribute("title");
    document.querySelectorAll("[aria-describedby]").forEach((node) => {
      const ids = node.getAttribute("aria-describedby").split(/\s+/).filter(Boolean);
      if (!ids.includes(line.id)) return;
      const kept = ids.filter((id) => id !== line.id);
      if (kept.length) node.setAttribute("aria-describedby", kept.join(" "));
      else node.removeAttribute("aria-describedby");
      node.removeAttribute("data-field-error");
      node.setAttribute("aria-invalid", "false");
    });
  };
  // Forms whose edits clear their pane's refusal: one listener each.
  const paneWatched = new WeakSet();
  // ``focus: false`` leaves focus to the caller when the answer has no
  // summary of its own (a refused in-place filter Apply, whose summary is
  // already in the swapped region).
  const showFieldErrors = (form, parsed, {focus = true} = {}) => {
    const fresh = parsed.getElementById(form.id);
    const line = paneLine(form);
    // A pane line's earlier refusal, and the marks it described, give way.
    if (line) clearPaneRefusal(form);
    // A list placed by an earlier answer (below) that this one does not
    // repeat is out of date: its field's mark and message clear.
    form.querySelectorAll(".errorlist[data-review-placed]").forEach((placed) => {
      if (fresh.querySelector(`#${CSS.escape(placed.id)}`)) return;
      const control = form.querySelector(`#${CSS.escape(placed.id.replace(/_error$/, ""))}`);
      if (control?.hasAttribute("data-field-error")) clearFieldError(control);
      placed.hidden = true;
    });
    // Each reserved error line takes the answer's messages. Its element
    // stays, since a page script may hold it (users-v2.js's Email line).
    // A form that reserves no line (Django draws a field's list only with
    // an error: New and Edit scheduled email's fields other than the send
    // date, whose line is reserved, #1787) gets the answer's list where
    // Django draws it, just before its control (<field id>_error; or before
    // the control's label, for a checkbox inside one).
    fresh.querySelectorAll(".errorlist[id]").forEach((list) => {
      let live = form.querySelector(`#${CSS.escape(list.id)}`);
      // A pane with its own line says it there instead: a list added
      // here would move the pane's button under the pointer (#1410).
      if (!live && line) return;
      if (!live) {
        const control = form.querySelector(`#${CSS.escape(list.id.replace(/_error$/, ""))}`);
        if (!control) return;
        live = document.importNode(list, true);
        live.setAttribute("data-review-placed", "");
        (control.closest("label") || control).before(live);
      }
      live.replaceChildren(...[...list.childNodes].map((node) => document.importNode(node, true)));
      live.hidden = list.hidden;
    });
    // Each control (and a choice group's fieldset) the answer marks is
    // marked and described the same way; the mark clears on the next edit
    // (wireValidity). A mark the answer does not repeat is left alone.
    // Buttons are left out: the gates keep their descriptions.
    [...form.elements].forEach((node) => {
      if (!node.id || isSubmit(node) || node instanceof HTMLButtonElement) return;
      const twin = fresh.querySelector(`#${CSS.escape(node.id)}`);
      if (!twin) return;
      const invalid = twin.getAttribute("aria-invalid") === "true";
      if (twin.hasAttribute("aria-describedby")) {
        // A pane's field is described by the pane's line, in place of the
        // list it does not draw.
        const ids = twin.getAttribute("aria-describedby").split(/\s+/)
          .filter((id) => id && (!line || form.querySelector(`#${CSS.escape(id)}`)));
        if (line && invalid) ids.push(line.id);
        node.setAttribute("aria-describedby", ids.join(" "));
      }
      if (invalid) {
        node.setAttribute("aria-invalid", "true");
        node.setAttribute("data-field-error", "");
      }
    });
    // A summary an earlier refusal placed inside the form's region (below)
    // goes too.
    document.querySelectorAll("[data-error-summary]").forEach((node) => {
      if (!node.closest(REGIONS) || node.hasAttribute("data-summary-placed")) node.remove();
    });
    const summary = parsed.querySelector("[data-error-summary]");
    if (line) {
      // The refusal is said in the pane's own line, where the reader is,
      // never in a summary above the heading: focusing that would scroll
      // the page to the top (#1410; UX conventions, "Review dialog").
      line.setAttribute("data-refusal", "");
      showOutcome(line.id, sentences([...(summary?.querySelectorAll("li") || [])]) || NOT_ACCEPTED);
      if (!paneWatched.has(form)) {
        paneWatched.add(form);
        // A file pick fires both; the second finds nothing to clear.
        ["input", "change"].forEach((type) => form.addEventListener(type, () => clearPaneRefusal(form)));
      }
      return;
    }
    if (!summary) {
      if (focus) form.querySelector("[aria-invalid=true]")?.focus();
      return;
    }
    const placed = document.importNode(summary, true);
    const heading = document.querySelector("main h1");
    // A long form whose Review changes sits far below the heading names the
    // element its summary follows (data-review-summary-after: Dates and mail
    // schedules, whose New schedule pane is at the end, R45), so the summary
    // shows, and takes focus, where the reader is, rather than jumping the
    // page to the top; nothing above the button moves.
    const after = form.dataset.reviewSummaryAfter
      && document.getElementById(form.dataset.reviewSummaryAfter);
    if (after) {
      placed.setAttribute("data-summary-placed", "");
      after.after(placed);
    } else if (heading) heading.before(placed);
    else form.before(placed);
    wireSummaryLinks(placed.parentElement);
    placed.focus();
  };
  // Hold a review dialog's Apply until every field of its answer marked
  // data-review-require is valid (prerequisite gates): the Confirm
  // Production dialog's typed word, whose pattern ignores spaces around it,
  // as the server does (#1585). While it holds, the reserved status line
  // under the buttons says what is missing (the field's data-missing-hint)
  // and describes Apply, so nothing moves when it clears. Not while Apply
  // runs or after a refusal, which keeps Apply unavailable itself.
  const gateReview = (dialog) => {
    const fields = [...dialog.querySelectorAll("[data-review-body] [data-review-require]")];
    const accept = dialog.querySelector("[data-confirm-accept]");
    const status = dialog.querySelector("[data-confirm-status]");
    if (!accept) return;
    if (!fields.length) {
      // Another answer in the same dialog: Apply no longer names the line.
      if (status.id && accept.getAttribute("aria-describedby") === status.id) {
        accept.removeAttribute("aria-describedby");
      }
      return;
    }
    if (confirming?.dialog !== dialog || confirming.busy
      || !dialog.querySelector("[data-confirm-error]").hidden) return;
    const missing = fields.find((field) => !field.checkValidity());
    accept.disabled = Boolean(missing);
    status.textContent = missing ? missing.dataset.missingHint || "" : "";
    if (missing && status.id) accept.setAttribute("aria-describedby", status.id);
    else accept.removeAttribute("aria-describedby");
  };
  const openReview = (dialog, form, opener, answer, refusal) => {
    wireDialog(dialog);
    const apply = dialog.querySelector("form[data-review-apply]");
    apply.querySelectorAll("[data-review-carried]").forEach((node) => node.remove());
    const carried = answer ? [...answer.querySelectorAll("input[type=hidden][name]")] : [];
    carried.forEach((field) => {
      const copy = document.importNode(field, true);
      copy.setAttribute("data-review-carried", "");
      apply.append(copy);
      field.remove();
    });
    const token = carried.find((field) => field.hasAttribute("data-review-preview"))?.value || "";
    const body = dialog.querySelector("[data-review-body]");
    body.replaceChildren(
      ...(answer ? [...answer.childNodes].map((node) => document.importNode(node, true)) : []));
    // The answer's <time data-local-instant> send times arrive as the
    // server's UTC instants; the page localized only what it loaded with,
    // so the review's own are shown in this browser's time zone here.
    if (window.ParishDates) window.ParishDates.localize(body);
    body.style.maxHeight = "";
    // An editor whose Review is a confirmation only for some changes (#1356:
    // New scheduled email, Import Ministry leaders) has its answer name the
    // confirmation's question (data-review-title) and the sentence that
    // describes the dialog (data-review-describedby). Any other answer, or a
    // refusal, gets the dialog's own title and description back.
    const title = dialog.querySelector("[data-review-title]");
    title.dataset.reviewDefault ??= title.textContent;
    title.textContent = answer?.getAttribute("data-review-title") || title.dataset.reviewDefault;
    dialog.dataset.reviewDescribed ??= dialog.getAttribute("aria-describedby") || "";
    const described = answer?.getAttribute("data-review-describedby") || dialog.dataset.reviewDescribed;
    if (described) dialog.setAttribute("aria-describedby", described);
    else dialog.removeAttribute("aria-describedby");
    const error = dialog.querySelector("[data-confirm-error]");
    error.hidden = true;
    error.replaceChildren();
    dialog.querySelector("[data-confirm-status]").textContent = "";
    dialog.querySelectorAll("[data-confirm-accept], [data-confirm-cancel]").forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-disabled");
      button.removeAttribute("data-recorded");
      button.classList.remove("is-busy");
    });
    // A key's confirmation (data-review-key) has no preview: its Apply
    // posts the form itself. Its sentence names the key by its last four
    // characters, from the field, as text.
    const key = Boolean(answer?.hasAttribute("data-review-key") && keyField(form));
    if (key) {
      const tail = keyField(form).value.trim().slice(-4);
      dialog.querySelectorAll("[data-review-body] [data-review-key-tail]").forEach((line) => {
        line.textContent = fillIn(line.dataset.reviewKeyTail, {tail});
      });
    }
    // An answer with nothing Apply could do (Past campaigns' file already
    // imported, #750; Administrator decision 12, campaign history spec)
    // marks its reason data-review-unavailable: Apply stays in view, greyed
    // (aria-disabled, so it keeps its place and the reader reaches it), and
    // the reason moves to the reserved status line, which describes Apply
    // (UX conventions, "Review dialog"; prerequisite gates). Its times were
    // localized with the body, above.
    const unavailable = body.querySelector("[data-review-unavailable]");
    const accept = dialog.querySelector("[data-confirm-accept]");
    accept.hidden = !token && !key && !unavailable;
    confirming = {dialog, opener, form, busy: false, done: false,
      unavailable: Boolean(unavailable),
      review: {apply, key, done: answer?.getAttribute("data-review-done") || "",
        // An answer may name where Apply's applied change leads and that
        // page's status line (Create the campaign opens the next Campaign
        // setup step, #1404), in place of the dialog's own.
        back: answer?.getAttribute("data-review-return") || "",
        backOutcome: answer?.getAttribute("data-review-return-outcome") || ""}};
    gateReview(dialog);
    if (unavailable) {
      const status = dialog.querySelector("[data-confirm-status]");
      status.replaceChildren(...unavailable.childNodes);
      unavailable.remove();
      accept.setAttribute("aria-disabled", "true");
      if (status.id) accept.setAttribute("aria-describedby", status.id);
    }
    dialog.showModal();
    if (refusal) refuseConfirm(confirming, refusal);
    // The title first, so the reader hears what is reviewed.
    dialog.querySelector("[data-review-title]").focus();
  };
  const prepareReview = async (form, dialog, submitter) => {
    reviewing.add(form);
    // What was typed is in this Review, and the form stays inert until its
    // answer and behind the modal dialog after it, so nothing typed can be
    // lost when Apply redraws the form's region: no "not kept" line then.
    editedSince.delete(form.id);
    const data = new FormData(form, submitter || undefined);
    // A secret is never part of a Review (data-review-key, #1568).
    if (keyField(form)) {
      data.delete(form.dataset.reviewKey);
      data.append("key_review", "yes");
    }
    // A form that sends a file (Campaign images' and Parish logos' uploads)
    // posts multipart, as it would natively; any other posts its fields
    // URL-encoded.
    const body = form.enctype === "multipart/form-data" ? data : new URLSearchParams(data);
    // A form marked data-csrf-header also sends its token in the
    // X-CSRFToken header: its server checks only the header, so that an
    // unauthorized upload is refused before its body is read (Past
    // campaigns' archive Review, #1018).
    const token = form.hasAttribute("data-csrf-header")
      && form.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    const headers = token ? {"X-CSRFToken": token} : {};
    const opener = submitter || firstSubmit(form) || form;
    form.setAttribute("aria-busy", "true");
    opener.setAttribute("aria-disabled", "true");
    opener.classList.add("is-busy");
    busyStatus.textContent = "Working…";
    form.inert = true;
    let answer = null;
    try {
      answer = await readPage(formUrl(form), {method: "POST", body, headers});
    } catch { /* no answer: said in the dialog below */ }
    form.inert = false;
    form.removeAttribute("aria-busy");
    opener.removeAttribute("aria-disabled");
    opener.classList.remove("is-busy");
    busyStatus.textContent = "";
    reviewing.delete(form);
    if (!answer) {
      openReview(dialog, form, opener, null, REVIEW_UNREACHABLE);
      return;
    }
    // A Review the server answers with another page ends on that page, for
    // a form marked data-review-redirects only: Campaign settings' change of
    // a draft's dates alone leads to Dates and mail schedules' combined
    // review (#1119). Any other editor's redirect is not a review.
    const answered = new URL(answer.response.url || formUrl(form), document.baseURI);
    if (form.hasAttribute("data-review-redirects")
      && answer.response.ok && answer.response.redirected && sameOrigin(answered.href)
      && answered.pathname !== new URL(formUrl(form)).pathname) {
      window.location.assign(answered.href);
      return;
    }
    const parsed = new DOMParser().parseFromString(answer.text, "text/html");
    const review = answer.response.ok && parsed.querySelector("[data-review-answer]");
    if (review) {
      // An earlier refusal no longer applies: its summary goes, and so do
      // the marks and messages it left on fields the reader did not edit
      // since (the server's, never a live check's, data-field-error="client",
      // which holds Review until it clears by itself). Reserved lines stay,
      // hidden and empty; a list showFieldErrors placed is hidden too.
      document.querySelectorAll("[data-error-summary]").forEach((node) => {
        if (!node.closest(REGIONS) || node.hasAttribute("data-summary-placed")) node.remove();
      });
      form.querySelectorAll('[data-field-error=""]').forEach((node) => {
        if (node.hasAttribute("data-field-error")) clearFieldError(node);
      });
      form.querySelectorAll(".errorlist[data-review-placed]").forEach((placed) => {
        placed.hidden = true;
      });
      clearPaneRefusal(form);
      openReview(dialog, form, opener, review, null);
    } else if (answer.response.status === 400 && form.id && parsed.getElementById(form.id)) {
      showFieldErrors(form, parsed);
      // The page's own script may show more of the refusal (the date-change
      // review opens the schedule editors the answer drew open).
      form.dispatchEvent(new CustomEvent("review-refused", {detail: parsed}));
    } else {
      openReview(dialog, form, opener, null, refusalMarkup(answer.text)
        || refusalText(answer.response.status, answer.text));
    }
  };
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)
      || !form.matches("form[data-review-open]")) return;
    // A submission that is no Review takes the form's other way: a button
    // marked data-review-skip (Off-site backups' Test access, which posts in
    // place). A pasted key is reviewed without it (data-review-key, above).
    if (event.submitter?.hasAttribute("data-review-skip")) return;
    // A submitter may name a dialog of its own (data-review-open on the
    // button): the report editor's Preview opens "Preview — not sent", not
    // the form's review (#2035).
    const dialog = document.getElementById(event.submitter?.dataset.reviewOpen
      || form.dataset.reviewOpen);
    if (!(dialog instanceof HTMLDialogElement) || !sameOrigin(formUrl(form))) return;
    event.preventDefault();
    // A repeat (Enter pressed twice) while a Review is prepared is ignored.
    if (reviewing.has(form)) return;
    prepareReview(form, dialog, event.submitter);
  }, true);
  // Two views of one thing in a dialog, a binary choice (UX conventions,
  // "Binary choices"; #2035): a report email Preview's Email | Plain text.
  // Each segment is a button naming its panel (data-view-show); choosing
  // one shows its panel and hides the other, in place, with no request, and
  // focus stays on the segment. The pair is drawn with the panels, so a new
  // answer starts at its first view.
  document.addEventListener("click", (event) => {
    const segment = event.target instanceof Element && event.target.closest("[data-view-show]");
    if (!segment) return;
    const pair = segment.closest("[data-view-pair]");
    if (!pair) return;
    pair.querySelectorAll("[data-view-show]").forEach((button) => {
      const chosen = button === segment;
      button.setAttribute("aria-pressed", String(chosen));
      const panel = document.getElementById(button.dataset.viewShow);
      if (panel) panel.hidden = !chosen;
    });
  });
  // The region a review's Apply redraws as its form's own: the one the form
  // names in data-review-region, else the one it is in.
  const reviewRegion = (form) => (form.dataset.reviewRegion
    && document.getElementById(form.dataset.reviewRegion)) || form.closest(REGIONS);
  // Apply of a key's confirmation (data-review-key, #1568): the page's own
  // form, key included, posted once to its own address without a page
  // load; the dialog cannot be dismissed until the answer. The key stays
  // only in the form's field and this one request.
  const acceptKey = async (state) => {
    const {dialog, form, review} = state;
    state.busy = true;
    const accept = dialog.querySelector("[data-confirm-accept]");
    accept.setAttribute("aria-disabled", "true");
    dialog.querySelector("[data-confirm-cancel]").disabled = true;
    dialog.querySelector("[data-confirm-error]").hidden = true;
    dialog.querySelector("[data-confirm-status]").textContent = dialog.dataset.working || "Working…";
    let answer;
    try {
      answer = await readPage(formUrl(form), {method: "POST", body: new URLSearchParams(new FormData(form))});
    } catch {
      // It may have reached the server: never send it again.
      refuseConfirm(state, CONFIRM_UNREACHABLE);
      return;
    }
    const parsed = new DOMParser().parseFromString(answer.text, "text/html");
    if (answer.response.status === 400 && form.id && parsed.getElementById(form.id)) {
      // Refused for a value: back to the form, which keeps what was typed.
      state.done = true;
      dialog.close();
      showFieldErrors(form, parsed);
      return;
    }
    if (!answer.response.ok || !sameOrigin(answer.response.url || formUrl(form))) {
      const markup = refusalMarkup(answer.text);
      refuseConfirm(state, markup || refusalText(answer.response.status, answer.text));
      return;
    }
    // Staged: the answer is the page, its status line saying the key is
    // being installed. Every region is redrawn from it, the form's own too.
    state.done = true;
    dialog.close();
    redrawFrom(parsed, reviewRegion(form));
    const outcome = form.dataset.reviewOutcome || dialog.dataset.reviewOutcome || "";
    if (!showOutcome(outcome, review.done)) announce(review.done);
  };
  const acceptReview = async (state) => {
    const {dialog, review} = state;
    if (review.key) {
      acceptKey(state);
      return;
    }
    const accept = dialog.querySelector("[data-confirm-accept]");
    const recorded = () => {
      // Recorded: Apply is done with, so it goes, keeping its space; the
      // status line says the rest.
      accept.setAttribute("data-recorded", "");
      dialog.querySelector("[data-review-title]").focus();
    };
    const body = new URLSearchParams(new FormData(review.apply));
    const answer = await followChange(state, formUrl(review.apply), body, recorded);
    if (!answer) return;
    dialog.querySelector("[data-confirm-status]").textContent = dialog.dataset.applied || "Applied.";
    const text = [review.done, fillIn(dialog.dataset.appliedAt || "Applied at {time}.",
      {time: clockTime()})].filter(Boolean).join(" ");
    // A dialog marked data-review-redraw="leave" (Start go-live, #1585)
    // ends on the page Apply's answer redirected to, opened as a page: its
    // address, not this one, is then the history entry a reload shows.
    if (dialog.dataset.reviewRedraw === "leave" && sameOrigin(answer.response.url)) {
      state.done = true;
      dialog.close();
      window.location.assign(answer.response.url);
      return;
    }
    const own = review.back && sameOrigin(review.back) ? review.back : "";
    const back = own || dialog.dataset.reviewReturn;
    const outcome = (own && review.backOutcome) || state.form.dataset.reviewOutcome || dialog.dataset.reviewOutcome || "";
    if (back) {
      const target = new URL(back, document.baseURI);
      try {
        window.sessionStorage.setItem(REVIEW_OUTCOME, JSON.stringify(
          {path: target.pathname, id: outcome, text, at: Date.now()}));
      } catch { /* No storage: Users shows no status line. */ }
      state.done = true;
      dialog.close();
      window.location.assign(target.href);
      return;
    }
    // A page whose own address can only be refused once the change is
    // applied (Create the campaign, data-review-redraw="answer") is redrawn
    // from the address Apply's answer ended on, the page naming its change,
    // which then becomes the page's address so a reload shows the same.
    // A dialog marked data-review-redraw="body" is redrawn from Apply's
    // answer itself, which holds the regions it changed: System health,
    // whose own address records a page view each time it is read (#1356).
    const region = reviewRegion(state.form);
    const fromAnswer = dialog.dataset.reviewRedraw === "answer"
      && sameOrigin(answer.response.url) ? answer.response.url : "";
    const fresh = dialog.dataset.reviewRedraw === "body"
      ? new DOMParser().parseFromString(answer.text, "text/html")
      : await freshPage(fromAnswer || region?.dataset.refreshUrl
        || window.location.href);
    if (!fresh) return;
    state.done = true;
    dialog.close();
    if (fromAnswer) window.history.replaceState(window.history.state, "", fromAnswer);
    // The Apply is the answer to the form's own Review, so the form's region
    // is redrawn even when only its own controls replace it (data-in-place-own:
    // an integration's settings, which Dismiss and Test access leave alone).
    redrawFrom(fresh, region);
    if (!showOutcome(outcome, text)) announce(text);
  };

  // A campaign wizard step's direct save (#1404 PR 4; UX conventions,
  // "Wizards", rules 4 and 5): a form marked data-direct-save (Campaign
  // settings, Share options, the sub-pages that save and Dates and mail
  // schedules' suggested schedules, #1404 PR 6, while the wizard
  // applies) is posted by the action row's Save and continue (a sub-page's
  // Save and go back) without a page load and without the review dialog. The form is inert and aria-busy meanwhile, and the
  // row's reserved status line (#campaign-step-status) says "Saving…".
  // - A refusal for the values (status 400 drawing this same form) marks
  //   the fields and shows the error summary, as a refused Review does
  //   (showFieldErrors); the form keeps what was typed.
  // - A Review that leads to another page (Campaign settings' dates-only
  //   change with schedules, Dates and mail schedules' combined review)
  //   opens it.
  // - Any other refusal (a stale page, held work, setup ended) is said in
  //   the status line, with nothing saved, and with the refusal's own link
  //   when it has one (Outgoing mail for held send work).
  // - A recorded change is followed in place (settleChange), the line
  //   saying it is being applied. Once applied, this page is read again for
  //   its action row's data-next-url (the next step not done yet, which a
  //   change of the modules can move) and that step opens, its stepper's
  //   status line saying what was saved and when (sessionStorage, as the
  //   review dialog's return does), and, when Financial stewardship was
  //   turned off, that the share options were removed (data-direct-removed).
  //   A sub-page's Save and go back (Pages and emails' editor, New and Edit
  //   scheduled email, #1404 PR 4b) opens its step again: its row's
  //   data-next-url is marked data-next-fixed, since the step's address
  //   never moves, so this page is not read again first (an email's
  //   revision page may no longer exist once a new revision is saved).
  // - A change that is not applied, or not yet, says so with a link to its
  //   status, and the page stays. Its request key is spent (a later save
  //   with it would be the same request), so the typed values are kept at
  //   the version they came from and only a new key is taken from the page.
  //   When the page cannot be read again (the sign-in ended), Save and
  //   continue is held (data-save-spent-gated) and the line says to reload.
  const SAVE_UNREACHABLE = "The server could not be reached, so nothing was saved. Try again in a moment.";
  // Campaign images' Save and continue (#1404 PR 4c) first stages each
  // chosen image at its slot's own upload address (data-wizard-upload),
  // one at a time, as an upload outside the wizard does; the answer is
  // that upload's review, of which only the staged bundle is read. Each
  // staged bundle becomes the save form's bundle_<slot> field, so a retry
  // after a refusal stages only what is not staged yet; changing a slot's
  // file drops its field (below). A refused image (status 400 drawing the
  // page) is marked at its field and said in its slot's line; any other
  // refusal is said in the row's line. An answer that is neither (the
  // sign-in page once the session ended) holds Save and continue until a
  // reload (``hold``), as a save whose status is lost does. Returns whether
  // every chosen image is staged; ``finish`` or ``hold`` ends the save with
  // a line otherwise.
  const stageUploads = async (form, finish, hold) => {
    for (const upload of chosenUploads()) {
      const name = `bundle_${upload.dataset.wizardUpload}`;
      if (form.elements.namedItem(name)) continue;
      let answer = null;
      try {
        answer = await readPage(formUrl(upload), {method: "POST", body: new FormData(upload)});
      } catch { /* no answer: said below */ }
      if (!answer) {
        finish(SAVE_UNREACHABLE);
        return false;
      }
      const parsed = new DOMParser().parseFromString(answer.text, "text/html");
      const bundle = answer.response.ok
        ? parsed.querySelector('input[type=hidden][name="bundle"]')?.value : "";
      if (!bundle) {
        if (answer.response.ok) {
          hold(`The image could not be uploaded. ${SAVE_RELOAD}`);
        } else if (answer.response.status === 400 && parsed.getElementById(upload.id)) {
          finish("");
          showFieldErrors(upload, parsed);
        } else {
          finish(refusalText(answer.response.status, answer.text), refusalLink(answer.text));
        }
        return false;
      }
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = name;
      field.value = bundle;
      form.append(field);
    }
    return true;
  };
  const SAVE_RELOAD = "Reload the page to make another change.";
  const SAVE_STATUS_LOST = "The change was saved, but its status can no longer be checked: your sign-in ended or your account no longer has access. " + SAVE_RELOAD;
  const saveStep = async (form, submitter) => {
    reviewing.add(form);
    const line = document.getElementById("campaign-step-status");
    // ``link`` is the change's status page, or a refusal's own link
    // ({url, label}: Outgoing mail when send work blocks the change).
    const say = (text, link) => {
      if (!line) return;
      line.replaceChildren(text);
      if (link) {
        const anchor = document.createElement("a");
        anchor.href = link.url || link;
        anchor.textContent = link.label || "See the change's status";
        line.append(" ", anchor);
      }
    };
    const opener = submitter || form;
    // Read before the save: the form's drawn (saved) state against the
    // posted one. Only Campaign settings carries the note.
    const financial = form.elements.namedItem("financial_enabled");
    const removes = financial instanceof HTMLInputElement && financial.defaultChecked
      && !financial.checked;
    // Campaign images (data-direct-uploads) stages from the slots' own
    // forms, so the whole slots region is held: a file changed, or an image
    // removed, while an upload is in flight would otherwise save an image
    // the page no longer shows, or post from a redrawn region.
    const busy = form.hasAttribute("data-direct-uploads")
      ? form.closest("[data-in-place-region]") || form : form;
    busy.setAttribute("aria-busy", "true");
    opener.setAttribute("aria-disabled", "true");
    opener.classList.add("is-busy");
    busy.inert = true;
    // The row's Continue (Continue without schedules for now beside Add
    // these schedules and continue) waits too, so the step is not kept as
    // is while its save is under way; each gets back its own state.
    const paused = [...document.querySelectorAll("form[data-campaign-continue]")]
      .map((node) => [node, node.inert]);
    paused.forEach(([node]) => { node.inert = true; });
    say("Saving…");
    const finish = (text, link) => {
      busy.inert = false;
      paused.forEach(([node, inert]) => { node.inert = inert; });
      busy.removeAttribute("aria-busy");
      opener.removeAttribute("aria-disabled");
      opener.classList.remove("is-busy");
      reviewing.delete(form);
      say(text, link);
    };
    // Hold Save and continue (or Add these schedules and continue, #1404
    // PR 6) for good (until a reload), saying why.
    const hold = (text, link) => {
      finish(text, link);
      document.querySelectorAll("[data-campaign-save], [data-campaign-add]")
        .forEach((node) => holdButton(node, "data-save-spent-gated"));
    };
    // The change is recorded but the page stays: keep what was typed (at
    // the version it came from) and take only a new request key from the
    // page read again, so a retry is a request of its own and, if the
    // version moved meanwhile, is refused as a stale page.
    const keep = async (text, link) => {
      let fresh = null;
      try {
        const page = await readPage(window.location.href, {method: "GET"});
        if (page.response.ok && !page.response.redirected) {
          fresh = new DOMParser().parseFromString(page.text, "text/html");
        }
      } catch { /* not readable: held below */ }
      const key = fresh?.getElementById(form.id)?.elements.namedItem("request_key");
      if (!key?.value || !form.elements.namedItem("request_key")) {
        hold(`${text} ${SAVE_RELOAD}`, link);
        return;
      }
      form.elements.namedItem("request_key").value = key.value;
      finish(text, link);
    };
    if (form.hasAttribute("data-direct-uploads") && !(await stageUploads(form, finish, hold))) return;
    // Read once any images are staged, so the save names them.
    const body = new URLSearchParams(new FormData(form, submitter || undefined));
    let answer = null;
    try {
      answer = await readPage(formUrl(form), {method: "POST", body});
    } catch { /* no answer: said below */ }
    if (!answer) {
      finish(SAVE_UNREACHABLE);
      return;
    }
    let status = answer.response.ok ? changeStatus(answer.text) : null;
    const answered = new URL(answer.response.url || formUrl(form), document.baseURI);
    if (!status && answer.response.ok && answer.response.redirected && sameOrigin(answered.href)
      && answered.pathname !== new URL(formUrl(form)).pathname) {
      window.location.assign(answered.href);
      return;
    }
    if (!status) {
      const parsed = new DOMParser().parseFromString(answer.text, "text/html");
      if (answer.response.status === 400 && form.id && parsed.getElementById(form.id)) {
        finish("");
        showFieldErrors(form, parsed);
        return;
      }
      finish(refusalText(answer.response.status, answer.text), refusalLink(answer.text));
      return;
    }
    say(status.text || "Applying your change…");
    const statusUrl = answer.response.url;
    const settled = await settleChange(statusUrl, status);
    status = settled.status;
    if (settled.outcome === "waiting") {
      await keep("Your change is saved but not applied yet.", statusUrl);
      return;
    }
    if (settled.outcome === "lost") {
      hold(SAVE_STATUS_LOST);
      return;
    }
    if (status.state !== "applied") {
      await keep(status.text || "The change was not applied. Nothing was changed.", statusUrl);
      return;
    }
    const text = [form.dataset.directSaved, fillIn(
      "Applied at {time}.", {time: clockTime()}), removes && form.dataset.directRemoved]
      .filter(Boolean).join(" ");
    const row = document.querySelector("[data-campaign-step-actions][data-next-fixed]");
    const fresh = row ? null : await freshPage(window.location.href);
    if (!row && !fresh) return;
    const next = (row || fresh.querySelector("[data-campaign-step-actions]"))?.dataset.nextUrl;
    if (next && sameOrigin(next)) {
      const target = new URL(next, document.baseURI);
      try {
        window.sessionStorage.setItem(REVIEW_OUTCOME, JSON.stringify(
          {path: target.pathname, id: "campaign-step-outcome", text,
            focus: form.dataset.directFocus, at: Date.now()}));
      } catch { /* No storage: the next step shows no status line. */ }
      window.location.assign(target.href);
      return;
    }
    // No later step to open (the wizard ended meanwhile): the page is
    // redrawn in place with what was applied. A sub-page always names its
    // step, so it has no fresh copy to redraw from: it is loaded again.
    if (!fresh) {
      window.location.reload();
      return;
    }
    finish("");
    redrawFrom(fresh);
    if (!showOutcome("campaign-step-outcome", text)) announce(text);
  };
  // A slot whose file changes after its image was staged stages it again.
  document.addEventListener("change", (event) => {
    const upload = event.target instanceof HTMLInputElement && event.target.type === "file"
      && event.target.closest("form[data-wizard-upload]");
    if (!upload) return;
    document.querySelector(`form[data-direct-uploads] input[name="bundle_${upload.dataset.wizardUpload}"]`)
      ?.remove();
  });
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)
      || !form.matches("form[data-direct-save]") || !sameOrigin(formUrl(form))) return;
    event.preventDefault();
    // A repeat (Enter pressed twice) while a save is under way is ignored.
    if (reviewing.has(form)) return;
    saveStep(form, event.submitter);
  }, true);

  // Pages and emails' editor's Keep as is and go back (#1404 PR 6b; UX
  // conventions, "Wizards", rule 10): its form (data-keep-item, in the
  // campaign wizard's action row) posts the step and the item to
  // campaign_step_continue without a page load. The server marks the item
  // kept as is and answers with Pages and emails, which then opens with
  // "<label> kept as is." in its status line and focus on the item's row
  // (data-kept-focus), as Save and go back's return does. A refusal (the
  // item changed meanwhile, setup ended, the server unreachable) is said in
  // the row's reserved status line, with nothing marked, and the page stays.
  const KEEP_UNREACHABLE = "The server could not be reached, so nothing was marked. Try again in a moment.";
  const keepItem = async (form, submitter) => {
    reviewing.add(form);
    const line = document.getElementById("campaign-step-status");
    const opener = submitter || form;
    form.setAttribute("aria-busy", "true");
    opener.setAttribute("aria-disabled", "true");
    const finish = (text, link) => {
      form.removeAttribute("aria-busy");
      opener.removeAttribute("aria-disabled");
      reviewing.delete(form);
      if (!line) return;
      line.replaceChildren(text);
      if (link) {
        const anchor = document.createElement("a");
        anchor.href = link.url;
        anchor.textContent = link.label;
        line.append(" ", anchor);
      }
    };
    let answer = null;
    try {
      answer = await readPage(formUrl(form), {method: "POST", body: new URLSearchParams(new FormData(form))});
    } catch { /* no answer: said below */ }
    if (!answer) {
      finish(KEEP_UNREACHABLE);
      return;
    }
    const answered = new URL(answer.response.url || formUrl(form), document.baseURI);
    if (answer.response.ok && answer.response.redirected && sameOrigin(answered.href)) {
      // Anywhere but Pages and emails (data-kept-return; the sign-in page
      // once the session ended) opens without the line: nothing says the
      // item was marked there.
      if (answered.pathname !== new URL(form.dataset.keptReturn || "", document.baseURI).pathname) {
        window.location.assign(answered.href);
        return;
      }
      try {
        window.sessionStorage.setItem(REVIEW_OUTCOME, JSON.stringify(
          {path: answered.pathname, id: "campaign-step-outcome", text: form.dataset.keptText,
            focus: form.dataset.keptFocus, at: Date.now()}));
      } catch { /* No storage: Pages and emails shows no status line. */ }
      window.location.assign(answered.href);
      return;
    }
    finish(refusalText(answer.response.status, answer.text), refusalLink(answer.text));
  };
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)
      || !form.matches("form[data-keep-item]") || !sameOrigin(formUrl(form))) return;
    event.preventDefault();
    if (reviewing.has(form)) return;
    keepItem(form, event.submitter);
  }, true);

  // Basic and Advanced editor modes (#1121; ui-conventions spec, "Basic and
  // Advanced modes"). A settings editor wraps its toggle
  // (components/editor-mode.html) and its two panels in
  // [data-editor-modes="<name>"]:
  //   - [data-editor-mode-panel="basic"], a <fieldset>: the few common
  //     choices. While Advanced is shown it is hidden and disabled, so its
  //     controls are neither sent nor validated.
  //   - [data-editor-mode-panel="advanced"]: the full configuration. It is
  //     only ever hidden, never disabled: its fields carry the configuration
  //     the server validates and stores in either mode (the recorded
  //     exception to conditional fields). An additive editor (#1129) has
  //     no Basic panel and one Advanced panel per extra tool, each off in
  //     Basic; its adapter has only toBasic and clearBasic.
  // The editor's script registers an adapter by the editor's name with
  // ParishEditorMode.register(name, adapter). It is looked up by name when
  // an event fires, so it still applies after an in-place answer redraws
  // the editor as fresh elements. Each method is given the editor's
  // current root element:
  //   - toAdvanced(root): write the chosen Basic choice into the Advanced
  //     fields (nothing when no choice is chosen). Called on every edit in
  //     the Basic panel, so the Advanced fields always hold the result, and
  //     on Basic → Advanced, which is therefore always lossless.
  //   - toBasic(root): choose the Basic choice equal to the Advanced fields
  //     and return true, or return false when there is none. It is called
  //     only while every Advanced field is valid: an unreadable or refused
  //     Advanced value has no Basic equivalent.
  //   - clearBasic(root) (optional): unchoose every Basic choice; by default
  //     every radio in the Basic panel is unticked.
  // Advanced → Basic with no Basic equivalent puts the toggle back and asks
  // in the toggle's components/confirm-dialog.html <dialog> (Keep Advanced,
  // the safe choice, takes focus; Switch to Basic switches with no choice
  // chosen, so the Basic panel's required choice holds Review). Nothing is
  // sent until the reader reviews. The toggle and the Basic panel are
  // marked data-not-a-change, so switching modes or choosing the Basic
  // choice that matches the saved configuration never makes Review
  // available on its own (the change gate above); the mode is still sent
  // with the next save. Focus stays on the toggle, and the new mode is
  // announced. An editor drawn with an error on an Advanced field (a
  // refused Review) is shown in Advanced, where that field and its message
  // can be seen.
  const editorAdapters = new Map(); // editor name → adapter
  const adapterOf = (root) => editorAdapters.get(root.dataset.editorModes || "");
  // The toggle that belongs to root (not one of a nested editor's).
  const modeToggle = (root) => [...root.querySelectorAll("[data-editor-mode]")]
    .find((node) => node.closest("[data-editor-modes]") === root);
  const modeChoice = (root, mode) => modeToggle(root)
    ?.querySelector(`input[data-editor-mode-choice][value="${mode}"]`);
  // root's panels for mode (not a nested editor's). An additive editor
  // (New and Edit scheduled email, #1129) has several Advanced panels, one
  // where each of its tools is drawn; other editors have one of each.
  const modePanels = (root, mode) => [...root.querySelectorAll(
    `[data-editor-mode-panel="${mode}"]`)].filter((node) => node.closest("[data-editor-modes]") === root);
  const modePanel = (root, mode) => modePanels(root, mode)[0];
  // The Advanced fields that are in error: marked by the server or a live
  // check, or invalid in the browser's own terms (an unreadable time). A
  // field only waiting for its group's first choice (data-require-one, such
  // as Parish logos' sizes with no file chosen) is incomplete, not in error.
  const advancedErrors = (root) => modePanels(root, "advanced")
    .flatMap((panel) => [...panel.querySelectorAll("input, select, textarea")])
    .filter((node) => node.getAttribute("aria-invalid") === "true"
      || node.hasAttribute("data-field-error") || node.hasAttribute("data-client-error")
      || (node.willValidate && !node.validity.valid
        && !node.hasAttribute("data-require-one-missing")));
  // Show root's panel for mode and hide the other; true when that changed.
  const showMode = (root, mode) => {
    const changed = root.dataset.editorModeShown !== mode;
    root.dataset.editorModeShown = mode;
    root.querySelectorAll("[data-editor-mode-panel]").forEach((panel) => {
      if (panel.closest("[data-editor-modes]") !== root) return;
      const shown = panel.dataset.editorModePanel === mode;
      panel.hidden = !shown;
      if (panel.dataset.editorModePanel === "basic" && panel instanceof HTMLFieldSetElement) {
        panel.disabled = !shown;
      }
    });
    return changed;
  };
  // Show mode, then let every gate see the new state: a change event from
  // the toggle reaches the form's gates (and returns early below).
  const switchMode = (root, mode) => {
    const choice = modeChoice(root, mode);
    if (choice) choice.checked = true;
    showMode(root, mode);
    choice?.dispatchEvent(new Event("change", {bubbles: true}));
    choice?.focus();
    const toggle = modeToggle(root);
    announce(toggle?.dataset[mode === "basic" ? "announceBasic" : "announceAdvanced"] || "");
  };
  const clearBasic = (root) => {
    const adapter = adapterOf(root);
    if (adapter?.clearBasic) {
      adapter.clearBasic(root);
      return;
    }
    modePanel(root, "basic")?.querySelectorAll("input[type=radio]").forEach((radio) => {
      radio.checked = false;
    });
  };
  // Write the Basic choice into the Advanced fields. The values it replaced
  // are gone, so any error marked on them no longer applies.
  const writeAdvanced = (root) => {
    const adapter = adapterOf(root);
    if (!adapter?.toAdvanced) return;
    const before = new Map(advancedErrors(root).map((node) => [node, node.value]));
    adapter.toAdvanced(root);
    before.forEach((value, node) => {
      if (node.value !== value && (node.hasAttribute("data-field-error")
        || node.getAttribute("aria-invalid") === "true")) clearFieldError(node);
    });
  };
  // Ask before Advanced → Basic when Basic cannot show the configuration.
  const askNoBasic = (root) => {
    const toggle = modeToggle(root);
    const dialog = document.getElementById(toggle?.dataset.editorModeDialog || "");
    if (!(dialog instanceof HTMLDialogElement)) return;
    wireDialog(dialog);
    if (!dialog.parishEditorMode) {
      dialog.parishEditorMode = {root, accepted: false};
      // Switch to Basic switches in its own click, right after closing, so
      // focus goes straight to Basic rather than first to the Advanced
      // choice that closing restores (below).
      dialog.querySelector("[data-confirm-accept]")?.addEventListener("click", () => {
        const state = dialog.parishEditorMode;
        state.accepted = true;
        dialog.close();
        clearBasic(state.root);
        switchMode(state.root, "basic");
      });
      dialog.addEventListener("close", () => {
        const state = dialog.parishEditorMode;
        if (state.accepted) {
          state.accepted = false;
          return;
        }
        // Keep Advanced, Cancel or Escape: nothing changes. Browsers that
        // restore focus when the dialog closes have already put it on
        // Advanced; this event, which comes later, covers those that do not.
        modeChoice(state.root, "advanced")?.focus();
      });
    }
    dialog.parishEditorMode.root = root;
    dialog.querySelector("[data-confirm-title]").textContent = dialog.dataset.titleOne || "";
    // Closing a modal dialog returns focus at once to whatever had it when
    // the dialog opened: here the Basic choice just clicked and put back.
    // Give it to the Advanced choice, which stays chosen, so focus never
    // rests on the unchosen Basic while the close event is pending (#1202).
    modeChoice(root, "advanced")?.focus();
    dialog.showModal();
    // The safe choice takes focus first.
    dialog.querySelector("[data-confirm-cancel]")?.focus();
  };
  // Capture phase, so the Advanced fields are written before the form's own
  // input and change listeners (the gates) look at them.
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    const node = event.target;
    if (!(node instanceof Element)) return;
    const root = node.closest("[data-editor-modes]");
    if (!root) return;
    if (!node.matches("input[data-editor-mode-choice]")) {
      // An edit in the Basic panel rewrites the Advanced fields.
      if (modePanel(root, "basic")?.contains(node)) writeAdvanced(root);
      return;
    }
    if (type !== "change" || !node.checked || root.dataset.editorModeShown === node.value) return;
    const adapter = adapterOf(root);
    if (node.value === "advanced") {
      writeAdvanced(root);
      showMode(root, "advanced");
      announce(modeToggle(root)?.dataset.announceAdvanced || "");
    } else if (!advancedErrors(root).length && adapter?.toBasic?.(root)) {
      showMode(root, "basic");
      announce(modeToggle(root)?.dataset.announceBasic || "");
    } else {
      // No Basic equivalent: the toggle stays on Advanced until the reader
      // confirms, and the gates are not told of a switch that did not happen.
      event.stopPropagation();
      modeChoice(root, "advanced").checked = true;
      askNoBasic(root);
    }
  }, true));
  // The panels follow the toggle on load, when a browser restores the page
  // (and the toggle) from its history, and when an in-place answer redraws
  // the editor; the gates then look again. An Advanced field in error opens
  // Advanced, so a refused Review never hides its own error in Basic.
  const syncModes = (scope = document) => within(scope, "[data-editor-modes]").forEach((root) => {
    let checked = modeToggle(root)?.querySelector("input[data-editor-mode-choice]:checked");
    if (checked?.value === "basic" && advancedErrors(root).some(
      (node) => node.getAttribute("aria-invalid") === "true" || node.hasAttribute("data-field-error"))) {
      checked = modeChoice(root, "advanced");
      checked.checked = true;
    }
    const mode = checked?.value || "advanced";
    if (showMode(root, mode) && root.dataset.editorModeSynced) {
      checked?.dispatchEvent(new Event("change", {bubbles: true}));
    }
    root.dataset.editorModeSynced = "1";
  });
  syncModes();
  window.addEventListener("pageshow", () => syncModes());
  document.addEventListener("parishkit:swap", (event) => {
    if (event.target instanceof Element) syncModes(event.target);
  });
  window.ParishEditorMode = Object.freeze({
    register(name, adapter) { editorAdapters.set(name, adapter); },
  });
  // Parish logos (#1062): Basic is one image, resized for every size;
  // Advanced is each size uploaded separately, where a size left empty
  // keeps its current image. A chosen file has no per-size equivalent the
  // page could write, so the server turns a Basic image into the four
  // sizes (the convention's "the server expands the Basic choice"), and
  // the Advanced fields hold only files chosen size by size. The server
  // marks the editor data-stored-basic="true" when the current logos were
  // made from one image.
  const logoSizes = (root) => [...(modePanel(root, "advanced")
    ?.querySelectorAll("input[type=file]") || [])];
  const basicLogo = (root) => modePanel(root, "basic")?.querySelector("input[type=file]");
  editorAdapters.set("parish-logos", {
    // A Basic image replaces all four sizes, so any size chosen before
    // (kept by Switch to Basic) is emptied rather than sent unseen.
    toAdvanced(root) {
      if (!basicLogo(root)?.files?.length) return;
      logoSizes(root).forEach((input) => { input.value = ""; });
    },
    // Basic can show the configuration only while no size is chosen and
    // the current logos came from one image. A Basic image chosen before a
    // switch to Advanced stays chosen, as the reader left it.
    toBasic(root) {
      return root.dataset.storedBasic === "true"
        && !logoSizes(root).some((input) => input.files?.length);
    },
    // Switch to Basic leaves no image chosen.
    clearBasic(root) {
      const input = basicLogo(root);
      if (input) input.value = "";
    },
  });

  // Repeating reminders (#469). expandRecurrence turns a repeat rule into
  // the calendar dates it produces. Dates are civil days, computed with UTC
  // dates so the browser's own zone never shifts them; the send time is
  // added later per date (the browser's wall-clock time on the date-change
  // review and New scheduled email, #558).
  // The rule: {frequency: "daily" | "weekly" | "monthly", start,
  // end (inclusive, "YYYY-MM-DD"), weekdays: [0 (Monday) … 6] for weekly,
  // and for monthly either {day: 1–31} or {ordinal: 1–4 or -1 (last),
  // weekday}}. A month without the chosen day (the 31st in September) is
  // skipped and listed in ``missing``, not moved. The result is {dates,
  // missing} or {error}. The shared table
  // tests/stewardship/fixtures/recurrence_cases.json pins these rules for the
  // server's expander too (campaigns/recurrence.py), so keep them in step.
  const RECURRENCE_SPAN = 366;
  const civilDay = (text) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text ?? ""));
    if (!match) return null;
    const day = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    return day.toISOString().slice(0, 10) === match[0] ? day : null;
  };
  const isoDay = (day) => day.toISOString().slice(0, 10);
  const monthDays = (day) => new Date(Date.UTC(day.getUTCFullYear(),
    day.getUTCMonth() + 1, 0)).getUTCDate();
  const expandRecurrence = (rule) => {
    const start = civilDay(rule?.start);
    const end = civilDay(rule?.end);
    if (!start || !end) return {error: "dates"};
    if (end < start) return {error: "order"};
    if ((end - start) / 86400000 >= RECURRENCE_SPAN) return {error: "span"};
    // Each member is checked, for the frequency that uses it, exactly as
    // campaigns/recurrence.py checks it, so both refuse the same rules.
    const inRange = (value, low, high) => Number.isInteger(value) && value >= low && value <= high;
    const monthly = rule.monthly && typeof rule.monthly === "object" ? rule.monthly : {};
    if (rule.frequency === "weekly" && !(Array.isArray(rule.weekdays)
      && rule.weekdays.length && rule.weekdays.every((value) => inRange(value, 0, 6)))) {
      return {error: "weekdays"};
    }
    const weekdays = new Set(rule.frequency === "weekly" ? rule.weekdays : []);
    const byDay = inRange(monthly.day, 1, 31);
    const byWeekday = [1, 2, 3, 4, -1].includes(monthly.ordinal) && inRange(monthly.weekday, 0, 6);
    // A day of the month and a weekday of the month together are ambiguous.
    const mixed = "day" in monthly && ("ordinal" in monthly || "weekday" in monthly);
    if (rule.frequency === "monthly" && (mixed || !(byDay || byWeekday))) {
      return {error: "monthly"};
    }
    if (!["daily", "weekly", "monthly"].includes(rule.frequency)) return {error: "frequency"};
    const dates = [];
    const missing = [];
    for (let day = new Date(start); day <= end; day.setUTCDate(day.getUTCDate() + 1)) {
      const date = day.getUTCDate();
      if (rule.frequency === "daily") dates.push(isoDay(day));
      else if (rule.frequency === "weekly") {
        if (weekdays.has(mondayFirst(day.getUTCDay()))) dates.push(isoDay(day));
      } else if (byDay) {
        if (date === monthly.day) dates.push(isoDay(day));
        // The month's last day in range stands for a missing chosen day.
        else if (date === monthDays(day) && monthly.day > date) {
          missing.push(isoDay(day).slice(0, 7));
        }
      } else if (mondayFirst(day.getUTCDay()) === monthly.weekday && (monthly.ordinal === -1
        ? date + 7 > monthDays(day) : Math.ceil(date / 7) === monthly.ordinal)) {
        dates.push(isoDay(day));
      }
    }
    return {dates, missing};
  };
  window.ParishRecurrence = Object.freeze({expand: expandRecurrence});

  // The date-change review's Sends Repeating (#469 slice 1; #1944): its rule
  // lists, as the Admin types, every date it produces and why any of
  // them will not be added; Review changes then posts one ordinary reminder row per date
  // that fits. The checks here only say early what the server will refuse
  // anyway (configuration.validate_campaign_sections stays the authority):
  // inside the campaign dates, strictly after the initial invitation, not at
  // the instant of another Family mailing, at most REPEAT_LIMIT dates per
  // rule and the form's MAX_NUM_FORMS rows in all. One more check is the
  // page's own: a date whose send time has already passed is never offered
  // (#1948), since a missed reminder would send at once; the date-change
  // review's saved rows may be past, so its server cannot refuse that,
  // while New's server refuses it for a repeat (schedule_entry_views).
  // Here every comparison is of date and time text in the browser's zone,
  // which the date-change review's rows use (#558). The campaign-date check compares calendar
  // days, so the server decides a time near either edge (New scheduled
  // email compares moments instead; see repeatDates). A rule the expander
  // refuses is shown at its fields (markRepeatRule, #1915) in
  // REPEAT_ERRORS' words, the server's recurrence.RULE_ERRORS.
  const REPEAT_LIMIT = 30;
  const REPEAT_ERRORS = {
    dates: "Choose the first and last dates to repeat on.",
    order: "The last date is before the first.",
    span: "Repeat over at most a year.",
    weekdays: "Choose at least one day of the week.",
    monthly: "Choose a day of the month from 1 to 31, or a weekday.",
    frequency: "Choose how often to repeat.",
  };
  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  const spokenDay = (text) => {
    const day = civilDay(text);
    return `${WEEKDAY_NAMES[mondayFirst(day.getUTCDay())]}, ${MONTH_NAMES[day.getUTCMonth()]} ${
      day.getUTCDate()}, ${day.getUTCFullYear()}`;
  };
  // The rule a repeat panel's controls describe, for expandRecurrence.
  const repeatRule = (panel) => {
    const control = (name) => panel.querySelector(`[data-repeat="${name}"]`);
    return {
      frequency: control("frequency").value,
      start: control("from").value,
      end: control("until").value,
      weekdays: [...panel.querySelectorAll('[data-repeat="weekday"]:checked')]
        .map((box) => Number(box.value)),
      monthly: control("by").value === "day"
        ? {day: Number(control("day").value)}
        : {ordinal: Number(control("ordinal").value),
          weekday: Number(control("month-weekday").value)},
    };
  };
  // Show only the parts of a repeat rule its frequency (and, monthly, its
  // "Repeat on") uses.
  const showRepeatParts = (panel) => {
    const control = (name) => panel.querySelector(`[data-repeat="${name}"]`);
    const frequency = control("frequency").value;
    panel.querySelectorAll("[data-repeat-when]").forEach((part) => {
      part.hidden = part.dataset.repeatWhen !== frequency;
    });
    panel.querySelectorAll("[data-repeat-by]").forEach((part) => {
      part.hidden = part.dataset.repeatBy !== control("by").value;
    });
  };
  // Every date a repeat rule gives, and why any of them will not be added
  // (shared by the date-change review's New schedule pane and New scheduled
  // email, #878).
  // ``existing`` is the Family mailings already scheduled, as {kind, date,
  // time} campaign-local text; ``first`` and ``last`` the campaign's dates;
  // ``room`` how many more of the ``limit`` schedules fit; ``sendTime`` the canonical send
  // time, or "" when it does not read yet; ``email`` whether an email is
  // chosen and ``emails`` whether any reminder email exists. Returns the
  // dates that fit (``ready``), one {text, reason} item per date or skipped
  // month, the summary line and the list's heading, and ``tally`` when the
  // summary only counts what the heading already says; for a rule
  // expandRecurrence refuses, only its ``error`` (a REPEAT_ERRORS key),
  // which is shown at its fields (markRepeatRule, #1915), not in the
  // summary.
  // New scheduled email takes dates and times in the browser's zone (#558)
  // and passes ``at`` (localMoment), ``bounds`` (the campaign's [start, end)
  // moments) and each existing mailing's ``at`` moment, so the timed checks
  // compare moments, as the server does; ``first`` and ``last`` are then
  // the browser-local days the campaign touches. The New schedule pane
  // passes ``judge`` instead (judgeSendWindow on the pane's window fields,
  // as the send-date check judges a Once date): whether a date at the send
  // time falls on a campaign day in the campaign's zone, or null when it
  // cannot tell, when ``first`` and ``last`` decide as campaign days.
  const repeatDates = ({rule, existing, first, last, room, limit, sendTime, email, emails,
    at, bounds, judge}) => {
    const ready = [];
    const items = [];
    const when = (date) => (at ? at(date, sendTime) : `${date} ${sendTime}`);
    // What the reserved summary line says: the first thing still missing,
    // or how many dates will be added. Without a readable send time the
    // timed checks (after the initial invitation, already scheduled then)
    // cannot run, so no count is offered rather than one that is too high.
    let summary = "";
    let tally = false; // whether the summary is just the count of dates
    const expanded = expandRecurrence(rule);
    if (expanded.error) return {ready, items, summary, heading: "", error: expanded.error};
    const sends = (mailing) => (at ? mailing.at : `${mailing.date} ${mailing.time}`);
    const initial = existing.find((mailing) => mailing.kind === "initial");
    const taken = new Set(existing.map(sends));
    // A browser-local date on the campaign's edge can fall on the day
    // before or after it in the campaign's zone, so the pane's judge
    // decides once the send time reads.
    const outside = (date) => {
      const inside = judge && sendTime ? judge(date, sendTime) : null;
      return inside === null ? date < first || date > last : !inside;
    };
    expanded.dates.forEach((date) => {
      let reason = "";
      if (outside(date)) reason = "outside the campaign dates";
      // Without a send time the remaining checks wait; the date is
      // listed but not counted.
      else if (!sendTime) reason = "";
      // Both panels' dates and times are in the browser's zone, so
      // localMoment gives the moment to compare with the clock (#1948).
      else if (localMoment(date, sendTime) <= Date.now()) reason = "its time has passed";
      else if (bounds && !(when(date) >= bounds.start && when(date) < bounds.end)) {
        reason = "outside the campaign dates";
      } else if (initial && when(date) <= sends(initial)) {
        reason = "not after the initial invitation";
      } else if (taken.has(when(date))) {
        reason = "another email is already scheduled then";
      } else if (ready.length >= REPEAT_LIMIT) {
        reason = `over the limit of ${REPEAT_LIMIT} reminders at a time`;
      } else if (ready.length >= room) {
        reason = `over the limit of ${limit} schedules`;
      }
      if (!reason && sendTime) ready.push(date);
      items.push({text: spokenDay(date), reason});
    });
    expanded.missing.forEach((month) => {
      const [year, number] = month.split("-").map(Number);
      items.push({text: `${MONTH_NAMES[number - 1]} ${year}`,
        reason: `skipped: it has no day ${rule.monthly.day}`});
    });
    const left = items.filter((item) => item.reason).length;
    const plural = (number, word) =>
      `${number.toLocaleString("en-US")} ${word}${number === 1 ? "" : "s"}`;
    if (!items.length) summary = "This rule gives no dates between the first and last date.";
    else if (!sendTime) summary = "Enter a send time to check these dates.";
    else if (!ready.length) summary = "Nothing to add: none of these dates can be used.";
    else if (!email) summary = emails ? "Choose the email to send." : "Save a reminder email first.";
    else {
      summary = `${plural(ready.length, "reminder")} to add${
        left ? `, ${left.toLocaleString("en-US")} not added` : ""}.`;
      tally = true;
    }
    const heading = !sendTime ? "Dates this rule gives:"
      : ready.length
        ? `${ready.length.toLocaleString("en-US")} ${ready.length === 1 ? "reminder" : "reminders"} to add:`
        : "Nothing to add yet:";
    return {ready, items, summary, heading, tally};
  };
  // Draw repeatDates' list: its heading, then one line per date, a date it
  // will not add saying why.
  const showRepeatDates = (list, {items, heading}) => {
    const title = document.createElement("p");
    title.textContent = heading;
    const listing = document.createElement("ul");
    items.forEach((item) => {
      const entry = document.createElement("li");
      entry.textContent = item.reason ? `${item.text}: not added, ${item.reason}` : item.text;
      if (item.reason) entry.className = "help";
      listing.append(entry);
    });
    list.replaceChildren(...(items.length ? [title, listing] : []));
  };
  // A summary line that is a live region is written a moment after typing
  // pauses, and only when its text changes, so a screen reader hears one
  // short sentence rather than every keystroke's result.
  const quietLine = (line) => {
    let timer;
    return (text) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (line.textContent !== text) line.textContent = text;
      }, 300);
    };
  };
  // Where a repeat rule's error is shown (#1915; UX conventions "Field
  // errors and live checks"), as the server's rule_place
  // (schedule_entry_views) places its refusal: the controls it marks and
  // the line (components/repeat-rule-error.html) after the last of them,
  // which says it once. A missing date marks whichever date is empty, or
  // both; the weekdays share the group's line.
  const repeatErrorPlace = (panel, error) => {
    const control = (name) => panel.querySelector(`[data-repeat="${name}"]`);
    const dates = [control("from"), control("until")];
    const empty = dates.filter((field) => !field.value);
    let fields = [control("frequency")];
    if (error === "dates") fields = empty.length ? empty : dates;
    else if (error === "order" || error === "span") fields = dates;
    else if (error === "weekdays") fields = [...panel.querySelectorAll('[data-repeat="weekday"]')];
    else if (error === "monthly") {
      fields = control("by").value === "weekday"
        ? [control("ordinal"), control("month-weekday")] : [control("day")];
    }
    const owner = error === "weekdays" ? "repeat-weekdays" : fields[fields.length - 1].id;
    return {fields, line: panel.querySelector(`#${owner}_error`)};
  };
  // Show a repeat panel's rule ``error`` (a REPEAT_ERRORS key, or "" for
  // none) at its fields: each is marked aria-invalid and described by the
  // line, which holds the words, with the shared field-error styles. As
  // with the browser's own checks, a field is marked only once the reader
  // has edited or left one of the error's fields (``repeatReached``): a
  // fresh rule's missing weekday is not an error yet, and the caller says
  // it in the summary line as what is missing. A new error also waits for
  // ``show`` (typing paused or focus left, which the caller passes), so a
  // half-typed date flashes nothing, as the send-date check waits; one
  // that no longer applies goes at once. The marks are this check's own,
  // not data-field-error: a refusal the server drew (data-field-error)
  // stays until that field's first edit (wireValidity), and the panel's
  // controls re-apply these marks on blur, after wireValidity's own blur
  // check. Returns the error's line (null for none) and whether the error
  // is said at its fields (``atField``) rather than in the summary.
  const repeatMarks = new WeakMap(); // panel -> {fields, line, message} shown
  const repeatReached = new WeakSet(); // rule controls edited or left
  const markRepeatRule = (panel, error, show) => {
    const found = error ? repeatErrorPlace(panel, error) : null;
    const atField = Boolean(found?.line) && found.fields.some((field) => repeatReached.has(field));
    const place = atField ? found : null;
    const message = place ? REPEAT_ERRORS[error] : "";
    const before = repeatMarks.get(panel);
    const same = Boolean(before && place) && before.line === place.line
      && before.message === message && before.fields.length === place.fields.length
      && before.fields.every((field, index) => field === place.fields[index]);
    if (before && !same) {
      const kept = before.fields.filter((field) => field.hasAttribute("data-field-error"));
      before.fields.forEach((field) => {
        if (kept.includes(field)) return;
        field.setAttribute("aria-invalid", "false");
        describeBy(field, before.line.id, false);
      });
      if (!kept.length) {
        before.line.hidden = true;
        before.line.querySelectorAll("li").forEach((item) => { item.textContent = ""; });
      }
      repeatMarks.delete(panel);
    }
    const result = {line: found?.line ?? null, atField};
    if (!place || !(show || same)) return result;
    place.fields.forEach((field) => {
      field.setAttribute("aria-invalid", "true");
      describeBy(field, place.line.id, true);
    });
    // Unchanged words stay untouched, so a screen reader hears them once.
    const item = place.line.querySelector("li") || place.line.appendChild(document.createElement("li"));
    if (place.line.hidden || item.textContent !== message) {
      place.line.hidden = false;
      item.textContent = message;
    }
    repeatMarks.set(panel, {fields: place.fields, line: place.line, message});
    return result;
  };
  // Whether ``event`` is typing in a date or number box, whose new error
  // waits for a pause (markRepeatRule).
  const repeatTyping = (event) => ["input", "change"].includes(event?.type)
    && event.target instanceof HTMLInputElement && ["date", "number"].includes(event.target.type);
  // Note each of ``panel``'s rule controls the reader edits or leaves
  // (repeatReached; one the server marked counts as reached), before the
  // panel's own listeners run, and re-check with ``update`` when one loses
  // focus, which shows a waiting error at once.
  const wireRepeatControls = (panel, update) => {
    panel.querySelectorAll("[data-repeat]").forEach((control) => {
      if (control.getAttribute("aria-invalid") === "true") repeatReached.add(control);
      const reach = () => repeatReached.add(control);
      control.addEventListener("input", reach);
      control.addEventListener("change", reach);
      control.addEventListener("blur", () => {
        reach();
        update();
      });
    });
  };
  // Mail schedule rows: show only the fields the chosen mail type uses (the
  // row's data-schedule-fields is schedule_forms.FIELDS), and offer only
  // emails of that type. A field hidden here is also cleared, so a stale date
  // or weekday is never submitted. When the page first loads, a field the
  // server reported an error on stays visible with its value, so the message
  // can be read and acted on. Without this script every field shows and the
  // server still explains any value that does not apply. A mail type the
  // back/forward cache restored fires no change event, so each row is set
  // again on pageshow (#563); a row still showing the type the server drew
  // keeps its reported errors in view, as on first load.
  let scheduleUpdates = []; // {row, update} for each wired schedule row
  const scheduleRow = (row) => {
    let rules;
    try { rules = JSON.parse(row.dataset.scheduleFields); } catch { return; }
    const kind = row.querySelector('[data-schedule-field="kind"] select');
    const template = row.querySelector('[data-schedule-field="template_version"] select');
    if (!kind || !template || !rules) return;
    // The weekday follows the mail type too: no mail type uses it since
    // report emails replaced the digests (#1786), but it stays a field of
    // the stored record, so it is hidden, not left out.
    const governed = new Set([...Object.values(rules).flat(), "weekday"]);
    const emails = [...template.options];
    const update = (firstLoad) => {
      const wanted = rules[kind.value] || [];
      row.querySelectorAll("[data-schedule-field]").forEach((group) => {
        if (!governed.has(group.dataset.scheduleField)) return;
        // A reserved error line (the send date's, #1787) is drawn even
        // when empty, hidden: only a shown message keeps its field.
        const show = wanted.includes(group.dataset.scheduleField)
          || (firstLoad && group.querySelector(".errorlist:not([hidden])") !== null);
        group.hidden = !show;
        // A checkbox is cleared by unticking it: its value is what it posts
        // when ticked.
        if (!show) group.querySelectorAll("input, select").forEach((control) => {
          if (control.type === "checkbox") control.checked = false;
          else control.value = "";
        });
      });
      const selected = template.value;
      template.replaceChildren(...emails.filter((option) =>
        !option.value || !option.dataset.kind || option.dataset.kind === kind.value));
      template.value = [...template.options].some((option) => option.value === selected)
        ? selected : "";
      gateComplete(row.closest("form")); // As for campaign modules above.
      emailSummary(row, template);
    };
    template.addEventListener("change", () => emailSummary(row, template));
    kind.addEventListener("change", () => update(false));
    // The type the server drew: a cloned blank row's first (empty) option.
    const drawn = ([...kind.options].find((option) => option.defaultSelected)
      || kind.options[0])?.value;
    scheduleUpdates = scheduleUpdates.filter((item) => item.row.isConnected);
    scheduleUpdates.push({row, update: () => update(kind.value === drawn)});
    update(true);
  };
  document.querySelectorAll("[data-schedule-row]").forEach(scheduleRow);
  window.addEventListener("pageshow", () => {
    scheduleUpdates.forEach(({row, update}) => {
      if (row.isConnected) update();
    });
  });

  // Scheduled emails table (#448, schedule-table.html): choosing an editable
  // row (its name button, or anywhere else on the row) opens that schedule's
  // editor in place, marks the row "(editing below)" and moves focus to the
  // editor, without a page load. An editor stays open once opened, so a
  // change never hides while it waits for Preview; closed editors still post
  // their saved values. A schedule that has already sent has no control and
  // its editor (data-schedule-past) never opens. An editor whose fields differ
  // from what the server drew (a value the browser restored on Back) is
  // opened again on pageshow, so a restored change is never posted unseen.
  // Each editor's opener, for the send date's window check (#1787), which
  // opens an editor holding a refused date as a refused Review would.
  const scheduleEditorOpeners = new WeakMap();
  const wireScheduleTable = (table) => {
    const form = table.closest("form");
    const editors = [];
    table.querySelectorAll("tr[data-schedule-choose]").forEach((row) => {
      const button = row.querySelector("[data-schedule-edit]");
      const editor = button && document.getElementById(button.getAttribute("aria-controls"));
      if (!editor || editor.hasAttribute("data-schedule-past")) return;
      const open = (focus) => {
        editor.hidden = false;
        button.setAttribute("aria-expanded", "true");
        // The note's space is always reserved; showing it moves nothing.
        row.querySelector("[data-schedule-editing]").classList.remove("schedule-editing-idle");
        gateComplete(form);
        if (focus) editor.focus();
      };
      button.addEventListener("click", () => open(true));
      row.addEventListener("click", (event) => {
        if (!event.target.closest("a, button, input, select, textarea")) open(true);
      });
      editors.push({editor, open});
      scheduleEditorOpeners.set(editor, open);
    });
    const changed = (editor) => [...editor.querySelectorAll("input, select, textarea")]
      .some((control) => {
        if (control.type === "hidden") return false;
        if (control.type === "checkbox" || control.type === "radio") {
          return control.checked !== control.defaultChecked;
        }
        if (control.tagName === "SELECT") {
          // With no option marked selected, a list shows its first option.
          const drawn = [...control.options].find((option) => option.defaultSelected)
            || control.options[0];
          return control.value !== (drawn ? drawn.value : "");
        }
        return control.value !== control.defaultValue;
      });
    // A schedule that has already run is read-only and its editor never
    // opens, so a value the browser restored into it is put back to what the
    // server drew rather than posted unseen.
    const putBack = (editor) => editor.querySelectorAll("input, select, textarea")
      .forEach((control) => {
        if (control.type === "checkbox" || control.type === "radio") {
          control.checked = control.defaultChecked;
        } else if (control.tagName === "SELECT") {
          [...control.options].forEach((option) => { option.selected = option.defaultSelected; });
          if (![...control.options].some((option) => option.defaultSelected)) control.selectedIndex = 0;
        } else {
          control.value = control.defaultValue;
        }
      });
    const reveal = () => {
      editors.forEach(({editor, open}) => {
        if (editor.hidden && changed(editor)) open(false);
      });
      form?.querySelectorAll("[data-schedule-past]").forEach((editor) => {
        if (changed(editor)) putBack(editor);
      });
    };
    reveal();
    // One pageshow listener serves every table (below); one an Apply's
    // redraw replaced (#1944) is dropped, so its detached form is not kept.
    tableReveals = tableReveals.filter((item) => item.table.isConnected);
    tableReveals.push({table, reveal});
    // A Review refused in the review dialog's flow (#750) leaves this form
    // in place: each editor the refused answer drew open (one with an error
    // or a change, as the server opens them) opens here too, so its marked
    // fields and the summary's links can be seen. A read-only editor with
    // an error is shown as the server shows it.
    form?.addEventListener("review-refused", (event) => {
      const opened = (editor) => event.detail.getElementById(editor.id)?.hidden === false;
      editors.forEach(({editor, open}) => {
        if (editor.hidden && opened(editor)) open(false);
      });
      form.querySelectorAll("[data-schedule-past][hidden]").forEach((editor) => {
        if (opened(editor)) editor.hidden = false;
      });
    });
  };
  let tableReveals = []; // {table, reveal} for each wired schedules table
  document.querySelectorAll("[data-schedule-table]").forEach(wireScheduleTable);
  window.addEventListener("pageshow", () => tableReveals.forEach(({table, reveal}) => {
    if (table.isConnected) reveal();
  }));

  // The date-change review's New schedule pane (#1944; admin-portal spec,
  // "New schedule on the date-change review"; UX conventions, "Schedules":
  // Once or Repeating, one schedule at a time). The pane is the formset's
  // first new row (schedule-fields.html), drawn with only its Mail type
  // shown. For a Reminder it shows Sends (Once | Repeating); Repeating
  // hides and disables the row's send date (keeping its value for Once) and
  // shows the shared repeat rule, and the row's own Send time and Email to
  // send serve both. An Initial invitation is always Once.
  //
  // Review changes covers the pane's one schedule. Once posts the pane's
  // own fields, an ordinary new row. Repeating posts, in the pane's place,
  // one ordinary reminder row per date the rule gives (with the checks of
  // repeatDates): the form's data is rewritten as it is gathered (the
  // formdata event), so the server reviews and applies ordinary reminders
  // and the dialog lists each date. Sends is dropped from the data, and the
  // rule's controls have no names, so neither is ever posted. While the pane
  // holds part of a schedule, Review changes waits (data-pane-gated), so
  // nothing typed there is reviewed unseen or dropped. Once applied, the
  // page is redrawn in place and the fresh pane, empty again, is wired
  // anew (parishkit:swap, below). Everything is worked out again on
  // pageshow, for values the browser restores.
  const paneHints = new WeakMap(); // form -> Review's visually hidden reason
  const INITIAL_TAKEN = "An Initial invitation is already scheduled. Change or delete it from the table instead.";
  const wireSchedulePane = (pane) => {
    const form = pane.closest("form");
    const total = form?.querySelector('input[name="schedules-TOTAL_FORMS"]');
    const saved = form?.querySelector('input[name="schedules-INITIAL_FORMS"]');
    const maximum = Number(form?.querySelector('input[name="schedules-MAX_NUM_FORMS"]')?.value);
    const control = (name) => pane.querySelector(`[data-schedule-field="${name}"] :is(input, select)`);
    const kind = control("kind");
    const date = control("date");
    const time = control("time");
    const email = control("template_version");
    const sends = pane.querySelector("[data-schedule-sends]");
    const rule = pane.querySelector("[data-repeat-panel]");
    const end = pane.querySelector("[data-schedule-pane-line]");
    const summary = pane.querySelector("[data-repeat-summary]");
    const dates = pane.querySelector("[data-repeat-dates]");
    if (!form || !total || !saved || !kind || !date || !time || !email || !sends || !rule
      || !end || !summary || !dates) return;
    const radios = [...sends.querySelectorAll("input[type=radio]")];
    const dateGroup = date.closest("[data-schedule-field]");
    // The pane is row INITIAL_FORMS, the first after the saved ones.
    const index = Number(saved.value);
    const value = (row, name) => row.querySelector(`[name$="-${name}"]`);
    // The saved rows the server counts toward its limit, less those marked
    // Delete; the pane's schedule (or its reminders) comes on top.
    const count = () => index
      - form.querySelectorAll('[data-schedule-rows] [name$="-DELETE"]:checked').length;
    const repeating = () => kind.value === "reminder"
      && radios.find((radio) => radio.checked)?.value === "repeat";
    // The pane's schedule in words: "Reminder, Monday, November 2, 2026 at
    // 9:00 AM: <email>", the email by its name without its "sent by" note.
    const describe = () => {
      const reading = readTimeEntry(time);
      const words = (select) => select?.selectedOptions[0]?.textContent.trim() || "";
      const when = date.value
        ? `${spokenDay(date.value)}${reading.error ? "" : ` at ${twelveHourTime(reading.value)}`}` : "";
      return `${words(kind)}, ${when}: ${words(email).split(" — ")[0]}`;
    };
    // The Family mailings already in the form (the saved rows), as {kind,
    // date, time} browser-local text, for the rule's checks.
    const mailings = () => [...form.querySelectorAll("[data-schedule-row]:not([data-schedule-pane])")]
      .flatMap((row) => {
        const type = value(row, "kind")?.value ?? "";
        const day = value(row, "date")?.value ?? "";
        const reading = parseTime(value(row, "time")?.value ?? "", value(row, "time")?.value ?? "");
        if (value(row, "DELETE")?.checked || !["initial", "reminder"].includes(type) || !day
          || reading.error) return [];
        return [{kind: type, date: day, time: canonicalTime(reading.value)}];
      });
    // Whether a saved row is an Initial invitation not marked Delete, read
    // from its fixed kind alone, so a date being retyped still counts.
    const initialSaved = () => [...form.querySelectorAll("[data-schedule-row]:not([data-schedule-pane])")]
      .some((row) => value(row, "kind")?.value === "initial" && !value(row, "DELETE")?.checked);
    // The campaign dates being proposed, or the drawn ones.
    const windowDay = (name) => form.querySelector(`[name="window-${name}"]`)?.value
      || rule.dataset[`repeat${name === "start_date" ? "Start" : "End"}`];
    const announce = quietLine(summary);
    const gate = {mark: "data-pane-gated", hints: paneHints, suffix: "pane-hint"};
    const wait = pane.querySelector("#schedule-pane-wait")?.textContent.trim() || "";
    let ready = []; // the send dates Review would post: one for Once
    let pause; // the timer that shows an error typed into a rule date
    const update = (event) => {
      window.clearTimeout(pause);
      const chosen = Boolean(kind.value);
      const reminder = kind.value === "reminder";
      const repeat = repeating();
      // Each part shows only once its choice is made (conditional fields);
      // a hidden Sends is disabled.
      sends.hidden = !reminder;
      radios.forEach((radio) => { radio.disabled = !reminder; });
      rule.hidden = !repeat;
      end.hidden = !chosen;
      // The date is the mail type's to show (scheduleRow); Repeating
      // replaces it, keeping its value for Once, and disables it so the
      // send-date check and the post leave it out. With no mail type it is
      // hidden and empty, and disabled too: WebKit can keep a date control
      // emptied by script "invalid" for the form's own checks, which would
      // stop Review over a field the reader cannot see.
      if (chosen) dateGroup.hidden = repeat;
      date.disabled = repeat || !chosen;
      ready = [];
      let text = "";
      let taken = false; // an Initial invitation is already scheduled
      if (repeat) {
        showRepeatParts(rule);
        // The shared time entry's own reading (data-time-entry, #1117).
        const reading = readTimeEntry(time);
        // Each date is judged in the campaign's zone, as the send-date
        // check judges a Once date and the server each posted row.
        const {start, end, zone} = sendWindow(form);
        const result = repeatDates({
          rule: repeatRule(rule),
          existing: mailings(),
          first: windowDay("start_date"),
          last: windowDay("end_date"),
          room: Number.isFinite(maximum) ? maximum - count() : Infinity,
          limit: maximum,
          sendTime: time.value.trim() && !reading.error ? canonicalTime(reading.value) : "",
          email: Boolean(email.value),
          emails: [...email.options].some((option) => option.dataset.kind === "reminder"),
          judge: (day, clock) => judgeSendWindow({date: day, time: clock, start, end, zone}),
        });
        // A rule error shows at its fields (#1915); typed into a date, after
        // a pause. One the reader has not reached is said in the summary.
        const show = !repeatTyping(event);
        const marked = markRepeatRule(rule, result.error || "", show);
        if (!show) pause = window.setTimeout(update, TIME_PAUSE);
        // Once the rule gives reminders to add, the list's heading says how
        // many, so the line does not repeat it (R45); it still says what
        // is missing or wrong.
        text = result.error && !marked.atField ? REPEAT_ERRORS[result.error]
          : result.tally ? "" : result.summary;
        showRepeatDates(dates, result);
        if (email.value) ({ready} = result);
      } else {
        markRepeatRule(rule, "", true);
        dates.replaceChildren();
        if (chosen) {
          const reading = readTimeEntry(time);
          // The first thing the pane still needs, or what Review will add.
          // A campaign has one Initial invitation (the server's one_initial
          // rule, which stays the safeguard), so a second is never offered
          // for Review (prerequisite gates; R45).
          if (kind.value === "initial" && initialSaved()) {
            text = INITIAL_TAKEN;
            taken = true;
          } else if (!date.value) text = "Enter the send date.";
          else if (!date.validity.valid) text = date.validationMessage;
          else if (!time.value.trim() || reading.error) text = "Enter a send time that can be read, for example 9:00 AM.";
          else if (!email.value) text = "Choose the email to send.";
          else {
            text = `Ready to add: ${describe()}.`;
            ready = [date.value];
          }
        }
      }
      announce(text);
      // Review changes waits while the pane holds part of a schedule, or a
      // schedule the campaign cannot have, named by its reason.
      holdFormButtons(form, gate, chosen && !ready.length ? (taken ? text : wait) : "");
    };
    // At once, so a part a choice shows or hides changes with it (the row's
    // own script shows the date for a Reminder), and again after the other
    // listeners of the same event (the send-date check sets the date's
    // validity, the time entry its reading), so the gate reads what they
    // decided.
    let later;
    const soon = (event) => {
      update(event);
      window.clearTimeout(later);
      later = window.setTimeout(() => update(event), 0);
    };
    // Review's data: Sends never goes; for Repeating, the pane's row is
    // replaced by one reminder row per date, numbered on from the pane's
    // index, each with the pane's fields, its own date and the time as read.
    let posted = []; // the dates the last Review posted for Repeating
    form.addEventListener("formdata", (event) => {
      const data = event.formData;
      data.delete("schedule_sends");
      posted = repeating() ? [...ready] : [];
      if (!posted.length) return;
      const prefix = `schedules-${index}-`;
      const fields = [...data.entries()].filter(([name]) => name.startsWith(prefix));
      fields.forEach(([name]) => data.delete(name));
      const sendTime = canonicalTime(readTimeEntry(time).value);
      ready.forEach((day, offset) => {
        const row = `schedules-${index + offset}-`;
        fields.forEach(([name, text]) => data.append(row + name.slice(prefix.length), text));
        data.set(`${row}date`, day);
        data.set(`${row}time`, sendTime);
      });
      data.set(total.name, String(index + ready.length));
    });
    // A refused Review of a Repeating schedule: the server judged the
    // posted reminder rows, which the page never draws (only the pane's
    // row, its send date hidden). Each summary link to one of them is
    // pointed at what the pane shows instead: a send date at the rule's
    // Until, any other field at the pane's own. Each refused date's words,
    // led by that date, show in Until's error line, which marks Until as
    // the server marks a field: until its first edit (wireValidity). The
    // summary is placed under Review changes, with focus (showFieldErrors,
    // data-review-summary-after); nothing else moves the page.
    form.addEventListener("review-refused", () => {
      if (!posted.length) return;
      const until = rule.querySelector('[data-repeat="until"]');
      const line = until && document.getElementById(`${until.id}_error`);
      if (!line) return;
      const words = [];
      document.querySelectorAll('[data-error-summary] a[href^="#id_schedules-"]').forEach((link) => {
        const [, row, name] = link.getAttribute("href").match(/^#id_schedules-(\d+)-(.+)$/) || [];
        const offset = Number(row) - index;
        if (!name || offset < 0 || offset >= posted.length) return;
        const target = name === "date" ? until : pane.querySelector(`#id_schedules-${index}-${name}`);
        if (target) link.setAttribute("href", `#${target.id}`);
        if (name === "date") words.push(`${spokenDay(posted[offset])}: ${link.textContent.trim()}`);
      });
      // The pane's own (hidden) send date keeps no mark of a date it did
      // not post.
      if (date.hasAttribute("data-field-error")) clearFieldError(date);
      if (!words.length) return;
      line.replaceChildren(...words.map((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        return item;
      }));
      line.hidden = false;
      until.setAttribute("aria-invalid", "true");
      until.setAttribute("data-field-error", "");
      describeBy(until, line.id, true);
    });
    // A new Mail type is a new schedule: a refused Review's marks in the
    // pane, and the summary's rules between schedules (no field of their
    // own, such as one Initial invitation only; data-error-scope, from
    // schedule_views._summary), no longer apply, so they go, and the
    // summary with them once it lists nothing (R45).
    kind.addEventListener("change", () => {
      pane.querySelectorAll('[data-field-error=""]').forEach(clearFieldError);
      document.querySelectorAll('[data-error-summary] li[data-error-scope="schedules"]')
        .forEach((item) => item.remove());
      // A Repeating refusal's links were pointed at the pane's fields
      // (review-refused, below), which the page never marked: they go too.
      document.querySelectorAll('[data-error-summary] li a[href^="#"]').forEach((link) => {
        if (pane.contains(document.getElementById(link.hash.slice(1)))) link.closest("li")?.remove();
      });
      document.querySelectorAll("[data-error-summary]").forEach((node) => {
        if (!node.querySelector("li")) node.remove();
      });
    });
    form.addEventListener("input", soon);
    form.addEventListener("change", soon);
    wireRepeatControls(rule, update);
    // The browser can restore the pane's values on Back (#563): one
    // pageshow listener serves every pane (below); one an Apply's redraw
    // replaced is dropped, so its detached form is not kept.
    paneUpdates = paneUpdates.filter((item) => item.pane.isConnected);
    paneUpdates.push({pane, update});
    // Start the rule at the campaign's dates, so its first list is useful.
    rule.querySelector('[data-repeat="from"]').value ||= windowDay("start_date") || "";
    rule.querySelector('[data-repeat="until"]').value ||= windowDay("end_date") || "";
    update();
  };
  let paneUpdates = []; // {pane, update} for each wired New schedule pane
  const wireSchedulePanes = (root) => {
    root.querySelectorAll("[data-schedule-pane]").forEach(wireSchedulePane);
  };
  window.addEventListener("pageshow", () => paneUpdates.forEach(({pane, update}) => {
    if (pane.isConnected) update();
  }));

  // New scheduled email's Repeat (#878, schedule-repeat-entry.html): for a
  // Reminder, a Repeat checkbox swaps the send date for a repeat rule. The
  // page's own send time and Email to send apply to every date. As the Admin
  // types, the dates the rule gives are listed with the same checks as the
  // date-change review's New schedule pane (repeatDates), against the saved
  // invitations and
  // reminders the page carries (data-repeat-mailings) and the schedules
  // still free (data-repeat-room). Dates and the time are this browser's
  // (#558): the saved mailings and the campaign's first and last moments
  // come as UTC instants, and every timed check compares moments
  // (localMoment). The dates that fit are kept as hidden
  // repeat-dates fields, which Review changes posts; the server checks each
  // date as an ordinary reminder. Repeat and its rule show only for a
  // Reminder; with another mail type nothing of the rule is posted as a
  // date, and the server refuses a repeat that is not a reminder anyway.
  const repeatHints = new WeakMap(); // form -> its visually hidden reason
  document.querySelectorAll("[data-repeat-entry]").forEach((panel) => {
    const form = panel.closest("form");
    const kind = form?.querySelector('[data-schedule-field="kind"] select');
    const enabled = panel.querySelector('[data-repeat="enabled"]');
    const rule = panel.querySelector("[data-repeat-rule]");
    const time = form?.querySelector('[data-schedule-field="time"] input');
    const email = form?.querySelector('[data-schedule-field="template_version"] select');
    const summary = form?.querySelector("[data-repeat-summary]");
    const list = form?.querySelector("[data-repeat-dates]");
    const posted = form?.querySelector("[data-repeat-posted]");
    if (!kind || !enabled || !rule || !time || !email || !summary || !list || !posted) return;
    const date = form.querySelector('[data-schedule-field="date"]');
    let existing = [];
    try { existing = JSON.parse(panel.dataset.repeatMailings || "[]"); } catch { existing = []; }
    existing = existing.map((mailing) => ({kind: mailing.kind, at: Date.parse(mailing.at)}))
      .filter((mailing) => !Number.isNaN(mailing.at));
    const bounds = {start: Date.parse(panel.dataset.repeatWindowStart),
      end: Date.parse(panel.dataset.repeatWindowEnd)};
    // The browser-local days the campaign touches: its first moment's day
    // and its last moment's (just before its end). A day outside them is
    // left out at once; the moments decide the days at either edge.
    const first = localParts(bounds.start).date;
    const last = localParts(bounds.end - 1).date;
    const room = Number(panel.dataset.repeatRoom);
    const limit = Number(panel.dataset.repeatLimit);
    const announce = quietLine(summary);
    const control = (name) => panel.querySelector(`[data-repeat="${name}"]`);
    // While the rule gives no date to add, the form's buttons wait
    // (#1915), named by the rule error's words or the summary's.
    const gate = {mark: "data-repeat-gated", hints: repeatHints, suffix: "repeat-hint"};
    let pause; // the timer that shows an error typed into a date
    const update = (event) => {
      window.clearTimeout(pause);
      const reminder = kind.value === "reminder";
      // Repeat is a Reminder's: with another mail type its panel is hidden,
      // so the box is unticked rather than posted hidden (conditional
      // fields), and it does not count as a tool in use for Basic.
      if (!reminder) enabled.checked = false;
      const on = reminder && enabled.checked;
      panel.hidden = !reminder;
      rule.hidden = !on;
      // The date field is the mail type's to show (scheduleRow); a repeat
      // replaces it only while it is on. Its value is kept, so turning
      // Repeat off again brings back what was typed.
      if (reminder && date) date.hidden = on;
      summary.hidden = !on;
      posted.replaceChildren();
      if (!on) {
        summary.textContent = "";
        list.replaceChildren();
        markRepeatRule(panel, "", true);
        holdFormButtons(form, gate, "");
        return;
      }
      showRepeatParts(panel);
      const reading = readTimeEntry(time); // The shared time entry's own reading.
      const result = repeatDates({
        rule: repeatRule(panel),
        existing,
        first,
        last,
        at: localMoment,
        bounds,
        room: Number.isFinite(room) ? room : Infinity,
        // The campaign's limit (the schedule forms' max_num).
        limit,
        sendTime: time.value.trim() && !reading.error ? canonicalTime(reading.value) : "",
        email: Boolean(email.value),
        emails: [...email.options].some((option) => option.dataset.kind === "reminder"),
      });
      // A rule error shows at its fields (#1915); typed into a date, after
      // a pause. One the reader has not reached is said in the summary.
      const show = !repeatTyping(event);
      const {atField} = markRepeatRule(panel, result.error || "", show);
      if (!show) pause = window.setTimeout(update, TIME_PAUSE);
      announce(result.error && !atField ? REPEAT_ERRORS[result.error] : result.summary);
      showRepeatDates(list, result);
      holdFormButtons(form, gate, result.ready.length ? ""
        : REPEAT_ERRORS[result.error] || result.summary);
      result.ready.forEach((day) => {
        const field = document.createElement("input");
        field.type = "hidden";
        field.name = "repeat-dates";
        field.value = day;
        posted.append(field);
      });
    };
    // Prefill the dates with the campaign's own calendar dates (a pure date
    // is never shifted), so the first list is useful.
    control("from").value ||= panel.dataset.repeatStart || "";
    control("until").value ||= panel.dataset.repeatEnd || "";
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    wireRepeatControls(panel, update);
    // The browser can restore the checkbox and the rule on Back.
    window.addEventListener("pageshow", () => update());
    update();
  });

  // Edit of a reminder in a remembered series (#469,
  // schedule-series-scope.html): "This and following" and "The whole series"
  // grey the send date (data-enabled-when) and keep each reminder's own
  // date, so the greyed box shows this reminder's date as drawn, never one
  // typed before the choice, which would not be used. The drawn date is the
  // field's default (filled in this browser's zone above). An input event
  // lets the change gate look again; a greyed field does not count anyway.
  document.querySelectorAll("[data-series-scope]").forEach((group) => {
    const form = group.closest("form");
    const date = form?.querySelector('[data-schedule-field="date"] input');
    if (!date) return;
    const restore = () => {
      const chosen = group.querySelector('input[type="radio"]:checked');
      if (chosen && chosen.value !== "this" && date.value !== date.defaultValue) {
        date.value = date.defaultValue;
        date.dispatchEvent(new Event("input", {bubbles: true}));
      }
    };
    group.addEventListener("change", restore);
    // The browser can restore a typed date and a chosen scope on Back.
    window.addEventListener("pageshow", restore);
  });

  // A send date inside the campaign (#1787). On New and Edit scheduled email
  // and the date-change review, an initial invitation's or reminder's send
  // date and time are typed in this browser's zone, and the server refuses
  // one whose moment falls on no campaign day (ScheduleSet._field_errors,
  // OUTSIDE_WINDOW). This is that rule, judged as the Admin types:
  //   - The moment is the date and time read in this browser's zone by the
  //     one daylight-saving rule (localMoment); its campaign day is the date
  //     it shows in the campaign's zone, which must be from the start date
  //     to the end date. Comparing days, not a date-only min and max, keeps
  //     the edge days exact whatever the two zones are.
  //   - The campaign dates and zone are the window fields where the page
  //     edits them (the date-change review, so a proposed date or zone is
  //     judged as the server judges it), else the form's data-window-start,
  //     -end and -zone. Fields that cannot be read yet (an empty date, an
  //     end before the start, a zone this browser does not know) judge
  //     nothing: the window's own refusal comes first, and the server stays
  //     the authority.
  //   - A saved row posted with the date and time the page showed for it
  //     (its data-saved-at moment) keeps its saved campaign-local date
  //     (schedule_local.to_campaign), so that date (data-saved-date) is
  //     judged then, never the shown one read again in a proposed zone.
  //   - A row with no date to judge (a digest, a deleted row, a Repeat that
  //     replaces the date, a greyed series date) or no readable time (its
  //     own refusal holds the button) is left alone.
  //   - While the window fields hold dates the server's window rules
  //     refuse for another reason (an unconfirmed financial overlap, a live
  //     end already past), the server judges the schedules against the
  //     saved dates (build_preview) and this check against the typed ones;
  //     both agree once the window itself is accepted.
  // A refused date holds the form's buttons at once (data-window-gated),
  // named by the server's words (data-window-message, its {window} the
  // dates as window_text writes them); once typing pauses or focus leaves,
  // the words show in the date's reserved error line, the date is marked
  // invalid, and a refused row in a closed or read-only editor of the
  // date-change review is opened, as the server's refusal opens it. The
  // shared case table
  // (tests/stewardship/fixtures/schedule_window_cases.json) pins judge to
  // the server's formset in every engine.
  const zoneDays = new Map(); // one Intl formatter per campaign zone
  const campaignDay = (moment, zone) => {
    let format = zoneDays.get(zone);
    if (!format) {
      try {
        format = new Intl.DateTimeFormat("en-US", {timeZone: zone, year: "numeric",
          month: "2-digit", day: "2-digit", numberingSystem: "latn"});
      } catch { return null; } // A zone this browser does not know.
      zoneDays.set(zone, format);
    }
    const part = Object.fromEntries(format.formatToParts(new Date(moment))
      .map(({type, value}) => [type, value]));
    return `${part.year.padStart(4, "0")}-${part.month}-${part.day}`;
  };
  const ISO_DAY = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
  // true inside, false outside, null when there is nothing to judge.
  // ``time`` is canonical (canonicalTime); the rest are the strings above.
  const judgeSendWindow = ({date, time, start, end, zone, savedDate, savedAt}) => {
    if (![date, start, end].every((day) => ISO_DAY.test(day || "")) || !time || !zone
      || start > end) return null;
    const shown = savedAt && savedDate ? localParts(Date.parse(savedAt)) : null;
    const day = shown && shown.date === date && shown.time === time
      ? savedDate : campaignDay(localMoment(date, time), zone);
    return day === null ? null : start <= day && day <= end;
  };
  // Shared with the browser tests, which run the case table through it.
  window.ParishSendWindow = Object.freeze({judge: judgeSendWindow});
  const SEND_DATES = "input[data-send-window]";
  const windowRefused = new WeakSet(); // dates whose validity this check set
  const windowShown = new WeakSet(); // dates whose line shows its words
  const windowHints = new WeakMap(); // form -> its visually hidden reason
  const windowTimers = new WeakMap(); // form -> its pending pause timer
  const sendWindow = (form) => {
    const value = (name, key) => {
      const field = form.elements.namedItem(`window-${name}`);
      return field instanceof HTMLElement ? field.value : form.dataset[key] || "";
    };
    return {start: value("start_date", "windowStart"), end: value("end_date", "windowEnd"),
      zone: value("timezone", "windowZone")};
  };
  // Set one date's refusal ("" for none). Its validity, which the gate
  // reads, follows at once. Its words, mark and description change only
  // with ``show`` (typing paused, focus left, or the page loaded), except
  // that a refusal that no longer applies goes at once: a half-typed date
  // or time ("2054-10-0" on the way to "2054-10-28") flashes nothing and
  // announces nothing, as the time entry waits for a pause.
  const markSendDate = (date, message, show) => {
    if (!message && !windowRefused.has(date) && !windowShown.has(date)) return;
    date.setCustomValidity(message);
    if (message) windowRefused.add(date);
    else windowRefused.delete(date);
    if (message && !show) return;
    date.setAttribute("aria-invalid", String(Boolean(message)));
    if (message) windowShown.add(date);
    else windowShown.delete(date);
    const line = date.id && document.getElementById(`${date.id}_error`);
    if (!line) return;
    const ids = (date.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((id) => id && id !== line.id);
    if (message) ids.push(line.id);
    if (ids.length) date.setAttribute("aria-describedby", ids.join(" "));
    else date.removeAttribute("aria-describedby");
    // Unchanged words stay untouched, so a screen reader hears them once.
    const item = line.querySelector("li") || line.appendChild(document.createElement("li"));
    if (line.hidden === !message && item.textContent === message) return;
    line.hidden = !message;
    item.textContent = message;
  };
  // Hold the form's buttons while any date stands refused, named by a
  // visually hidden line with the first refusal's words (the field's own
  // line shows them), as the time-entry gate does.
  const gateSendWindow = (form) => {
    const refused = [...form.querySelectorAll(SEND_DATES)].find((date) => windowRefused.has(date));
    holdFormButtons(form, {mark: "data-window-gated", hints: windowHints, suffix: "window-hint"},
      refused ? refused.validationMessage : "");
  };
  // Judge every send date of ``form`` and gate its buttons. With ``show``
  // the refusals are also shown, and an editor holding one is opened; a
  // value still being typed opens nothing (an end date passing through
  // October 2 on its way to October 28 would open every later editor).
  const checkSendWindows = (form, show = true) => {
    const dates = [...form.querySelectorAll(SEND_DATES)];
    if (!dates.length) return;
    window.clearTimeout(windowTimers.get(form));
    const {start, end, zone} = sendWindow(form);
    const words = `${window.ParishDates?.date(start) ?? start} – ${
      window.ParishDates?.date(end) ?? end}`;
    dates.forEach((date) => {
      const row = date.closest("[data-schedule-row]");
      const time = row?.querySelector('[data-schedule-field="time"] input');
      const reading = time ? readTimeEntry(time) : {error: "empty"};
      const skipped = !row || date.disabled || date.closest("[data-schedule-field]")?.hidden
        || row.querySelector('[name$="-DELETE"]')?.checked || reading.error;
      const inside = skipped ? null : judgeSendWindow({date: date.value,
        time: canonicalTime(reading.value), start, end, zone,
        savedDate: date.dataset.savedDate, savedAt: date.dataset.savedAt});
      markSendDate(date, inside === false
        ? (date.dataset.windowMessage || "").replace("{window}", words) : "", show);
      if (show && inside === false && row.hidden) {
        const open = scheduleEditorOpeners.get(row);
        if (open) open(false);
        else if (row.hasAttribute("data-schedule-past")) row.hidden = false;
      }
    });
    gateSendWindow(form);
    if (!show) windowTimers.set(form, window.setTimeout(() => checkSendWindows(form), TIME_PAUSE));
  };
  const sendWindowForms = () => new Set([...document.querySelectorAll(SEND_DATES)]
    .map((date) => date.form).filter(Boolean));
  const sendWindowForm = (event) => {
    const form = event.target instanceof Element ? event.target.closest("form") : null;
    return form?.querySelector(SEND_DATES) ? form : null;
  };
  // Any edit in such a form may move a verdict: a date or time, a window
  // field, a mail type, Delete or Repeat. Typing is judged at once (the
  // gate) and shown after a pause or once focus leaves the field; a
  // removed new row is shown at once.
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    const form = sendWindowForm(event);
    if (form) checkSendWindows(form, false);
  }));
  ["focusout", "schedules-changed"].forEach((type) => document.addEventListener(type, (event) => {
    const form = sendWindowForm(event);
    if (form) checkSendWindows(form);
  }));
  window.addEventListener("pageshow", () => sendWindowForms().forEach((form) => checkSendWindows(form)));
  // The page's New schedule panes are wired here, once judgeSendWindow and
  // sendWindow (which their rule's dates use) are defined, and just before
  // the send-date checks, the order the swap below keeps.
  wireSchedulePanes(document);
  sendWindowForms().forEach((form) => checkSendWindows(form));
  // The date-change review is redrawn in place once a change is applied
  // (#1944; review dialog, redrawFrom): its fresh form is wired as the
  // page's own was at load, in the same order (the zone, the saved rows'
  // browser-local values, each row's fields, the table's editors, the New
  // schedule pane, the send-date checks). The swap's own enhancement
  // (enhanceSwapped) has already wired its time entries and zone field, so
  // the times localized here are read again for their "Reads as" lines.
  document.addEventListener("parishkit:swap", (event) => {
    const root = event.target;
    if (!(root instanceof Element) || !root.querySelector("form[data-schedule-zoned]")) return;
    gateScheduleZones(root);
    localizeSchedules(root);
    root.querySelectorAll("[data-schedule-local] [data-time-entry]").forEach((field) => {
      if (timeParts.has(field)) checkTimeField(field, {quiet: Boolean(serverTimeError(field))});
    });
    root.querySelectorAll("form[data-schedule-zoned]").forEach((form) => gateTimes(form));
    root.querySelectorAll("[data-schedule-row]").forEach(scheduleRow);
    root.querySelectorAll("[data-schedule-table]").forEach(wireScheduleTable);
    wireSchedulePanes(root);
    root.querySelectorAll("form[data-schedule-zoned]").forEach((form) => checkSendWindows(form));
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
  function liveSource(form, visual, editor, source, removing) {
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
    // sync() and overwrite newer source with the stale pane's content. Once
    // the newest answer is in, the pane stays read-only while "Remove this
    // selected content" is ticked (removing()).
    const busy = (value) => {
      visual.setAttribute("aria-busy", String(value));
      const locked = value || removing();
      editor.contentEditable = locked ? "false" : "true";
      tools.forEach((button) => { button.disabled = locked; });
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
  // into the pane are plain text. It is the one editor of every authored
  // email part (components/html-content-editor.html: Pages and emails, a
  // report email's Introduction), wired wherever that include is drawn in a
  // form whose HTML source is its "html" field.
  document.querySelectorAll("form [data-visual-content]").forEach((visual) => {
    const form = visual.closest("form");
    const editor = visual.querySelector("[data-content-editor]");
    const source = form.querySelector('textarea[name="html"]');
    if (!editor || !source) return;
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
    // While "Remove this selected content" is ticked the HTML source is
    // greyed and not sent (data-enabled-when, content_forms), so the pane
    // that writes it stops accepting edits too, and its formatting buttons
    // are unavailable (#563, #750). It keeps its size: only its colour
    // changes (ui-v1.css), so nothing moves. While the pane waits for the
    // sanitizer (aria-busy) liveSource's busy() decides, with the same rule.
    const clear = form.elements.namedItem("clear");
    const removing = () => clear instanceof HTMLInputElement && clear.checked && !clear.disabled;
    const lock = () => {
      const removed = removing();
      if (removed) editor.setAttribute("aria-disabled", "true");
      else editor.removeAttribute("aria-disabled");
      if (visual.getAttribute("aria-busy") === "true") return;
      editor.contentEditable = removed ? "false" : "true";
      form.querySelectorAll("[data-content-tag]").forEach((button) => { button.disabled = removed; });
    };
    if (clear instanceof HTMLInputElement) {
      clear.addEventListener("change", lock);
      window.addEventListener("pageshow", lock);
    }
    // A Preview refused for the values (the review dialog, #750) marks the
    // HTML source, which is folded away while this pane is in use, so its
    // message is shown here too, as the page draws it on a load (#563):
    // the answer's own list beside the pane, which clears on the next edit.
    form.addEventListener("review-refused", (event) => {
      const fresh = event.detail?.getElementById?.("visual-content-error");
      form.querySelector("#visual-content-error")?.remove();
      editor.removeAttribute("aria-invalid");
      editor.removeAttribute("aria-describedby");
      if (!fresh) return;
      editor.after(document.importNode(fresh, true));
      editor.setAttribute("aria-invalid", "true");
      editor.setAttribute("aria-describedby", "visual-content-error");
    });
    // An edit of the HTML in either place clears the refusal from both: the
    // pane's copy, and the HTML source's mark and error summary item, which
    // the source's own handler never clears when sync() writes its value.
    const clearHtmlRefusal = () => {
      if (source.hasAttribute("data-field-error")) clearFieldError(source);
      if (editor.getAttribute("aria-invalid") !== "true") return;
      form.querySelector("#visual-content-error")?.remove();
      editor.removeAttribute("aria-invalid");
      editor.removeAttribute("aria-describedby");
    };
    editor.addEventListener("input", clearHtmlRefusal);
    source.addEventListener("input", clearHtmlRefusal);
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
    liveSource(form, visual, editor, source, removing);
    lock();
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

  // New and Edit scheduled email's own email (#446, schedule-email.html):
  // ticking "Edit this schedule's email" shows its fields (data-show-when)
  // and fills them from the email chosen in Email to send (empties them
  // while none is, so no other type's text is left to be saved as this
  // type's). Choosing another email, or on New another mail type
  // (whose script above has already narrowed the email list), fills them
  // again, but only while the reader has not edited them, so typed text is
  // never replaced without asking; Reset to the default text fills the
  // built-in default of the mail type shown (Edit's is fixed; the select is
  // disabled but still holds it). A refused page keeps what was posted. The
  // texts are the server's (data-schedule-email-sources); each is set as a
  // field value, never as markup. "Generate plain text from HTML" is the
  // content editors' panel below, told of every fill by its change event.
  // On New both the checkbox and Reset need a mail type (UX conventions,
  // "Prerequisite gates"): while none is chosen they are unavailable, the
  // box is unticked (so the fields close and are not sent), and the reserved
  // reason line (data-schedule-email-reason) says why. Checked on load, on
  // every type change and on pageshow.
  document.querySelectorAll("[data-schedule-email]").forEach((section) => {
    const form = section.closest("form");
    const box = form?.elements.namedItem("edit_email");
    const kind = form?.elements.namedItem("schedule-kind");
    const choice = form?.elements.namedItem("schedule-template_version");
    const [subject, html, text, generate] = ["subject", "html", "text", "generate_text"]
      .map((name) => form?.elements.namedItem(name));
    const reset = section.querySelector("[data-schedule-email-reset]");
    let sources = null;
    try {
      sources = JSON.parse(document.getElementById("schedule-email-sources")?.textContent || "null");
    } catch { sources = null; }
    if (!box || !kind || !choice || !subject || !html || !text || !generate || !reset
      || !sources) return;
    // Posted back after a refusal, the fields hold the reader's own text.
    let edited = box.checked;
    const fill = (value) => {
      if (!value) return;
      subject.value = value.subject || "";
      html.value = value.html;
      text.value = value.text;
      generate.checked = Boolean(value.generate);
      generate.dispatchEvent(new Event("change"));
      edited = false;
    };
    const empty = {subject: "", html: "", text: "", generate: true};
    // An email the page has no text for (none of this type) leaves the
    // fields as drawn.
    const chosen = () => (choice.value ? sources.emails?.[choice.value] : empty);
    [subject, html, text, generate].forEach((field) => {
      ["input", "change"].forEach((type) => field.addEventListener(type, (event) => {
        if (event.isTrusted) edited = true;
      }));
    });
    const refill = () => { if (box.checked && !edited) fill(chosen()); };
    [box, kind, choice].forEach((control) => control.addEventListener("change", refill));
    const reason = section.querySelector("[data-schedule-email-reason]");
    const gate = () => {
      const ready = Boolean(kind.value);
      if (!ready && box.checked) {
        box.checked = false;
        box.dispatchEvent(new Event("change"));
      }
      [box, reset].forEach((control) => {
        control.disabled = !ready;
        const described = (control.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter((id) => id && id !== reason?.id);
        if (!ready && reason?.id) described.push(reason.id);
        if (described.length) control.setAttribute("aria-describedby", described.join(" "));
        else control.removeAttribute("aria-describedby");
      });
      reason?.toggleAttribute("data-idle", ready);
    };
    kind.addEventListener("change", gate);
    window.addEventListener("pageshow", gate);
    gate();
    reset.addEventListener("click", () => {
      const value = sources.defaults?.[kind.value];
      if (!value) return;
      fill(value);
      // The default is now the reader's choice; another email chosen above
      // does not replace it.
      edited = true;
    });
  });

  // New and Edit scheduled email's Basic and Advanced modes (#1129; UX
  // conventions, "Basic and Advanced modes", additive editors). Both modes
  // show the schedule's fields; Advanced adds Repeat and "Edit this
  // schedule's email", each an Advanced panel where it is drawn. Basic is
  // the page with both off, so Advanced → Basic switches at once while
  // they are off and otherwise asks. Switch to Basic unticks them through
  // their own change events, so the repeat rule and the email fields close
  // as if the reader had unticked them, and nothing hidden is posted.
  // A Repeat ticked under another mail type is unticked as it hides (the
  // repeat script), so a tool in use is always one on screen.
  const scheduleTools = (root) => [...root.querySelectorAll(
    'input[data-repeat="enabled"], input[name="edit_email"]')];
  window.ParishEditorMode.register("schedule-entry", {
    toBasic: (root) => scheduleTools(root).every((box) => !box.checked),
    clearBasic(root) {
      scheduleTools(root).filter((box) => box.checked).forEach((box) => {
        box.checked = false;
        box.dispatchEvent(new Event("change", {bubbles: true}));
      });
    },
  });

  // "Send a test of this text" under a scheduled email's own email (#446,
  // schedule-email.html): posts the email fields as they are now, with the
  // email chosen in Email to send, to schedule_email_test without a page
  // load. Nothing is saved; the server checks the text as a save does and
  // sends the fictional test Preview and test email sends, to the Testing
  // recipient only. The FormData is built from the page's form, so its
  // formdata listeners apply (generated plain text is left out, as a Review
  // leaves it out). The reserved status line after the button
  // (UX conventions, "Prerequisite gates" and "No layout shift") says why
  // the button is unavailable (the campaign admits no test mail, which the
  // server drew in data-closed; no email chosen; an uncertain earlier test
  // not acknowledged) or what the send did, with the time in this browser's
  // zone. A reason that appears or clears replaces the line; an answer stays
  // until then. The acknowledgment has no name, so a Review never posts it;
  // it shows when the page was drawn with an uncertain test or a send is
  // refused for one. A send that gets no answer may have been queued, so it
  // is never repeated by itself, and the line says to check the mailbox.
  document.querySelectorAll("[data-schedule-email-test]").forEach((panel) => {
    const form = panel.closest("form");
    const send = panel.querySelector("[data-schedule-email-test-send]");
    const status = panel.querySelector("[data-schedule-email-test-status]");
    const acknowledgment = panel.querySelector("[data-schedule-email-test-acknowledge]");
    const box = acknowledgment?.querySelector("input[type=checkbox]");
    const choice = form?.elements.namedItem("schedule-template_version");
    const csrf = form?.querySelector('input[name="csrfmiddlewaretoken"]');
    if (!form || !send || !status || !acknowledgment || !box || !choice || !csrf) return;
    const reason = () => panel.dataset.closed
      || (!choice.value ? panel.dataset.noEmail || "Choose an email to send first." : "")
      || (!acknowledgment.hidden && !box.checked
        ? "Tick the confirmation below to send another test." : "");
    let sending = false;
    let shown = null; // the reason the line last showed ("" for none)
    const say = (text, error) => {
      status.textContent = text;
      status.classList.toggle("is-error", Boolean(error));
    };
    const gate = () => {
      const why = reason();
      send.disabled = Boolean(why);
      if (why !== shown) {
        if (why || shown) say(why, false);
        shown = why;
      }
    };
    // Any change on the form, after the control's own handlers: choosing
    // another mail type on New empties Email to send from script
    // (scheduleRow) without a change event of its own, and the bubbling
    // change of the mail type reaches the form after that reset. pageshow
    // runs after scheduleRow's own pageshow update, registered earlier.
    form.addEventListener("change", gate);
    window.addEventListener("pageshow", gate);
    gate();
    send.addEventListener("click", async () => {
      if (sending || reason()) return;
      sending = true;
      send.setAttribute("aria-disabled", "true");
      send.classList.add("is-busy");
      say("Sending the test…", false);
      const data = new FormData(form);
      const body = new URLSearchParams({template_version: choice.value});
      ["subject", "html", "generate_text", "text"].forEach((name) => {
        if (data.has(name)) body.set(name, data.get(name));
      });
      if (!acknowledgment.hidden && box.checked) body.set("acknowledge_unknown", "on");
      let text = "The server could not be reached, so a test may or may not be on its way. "
        + "Check the Testing mailbox before sending another.";
      let error = true;
      let unknown = false;
      try {
        const response = await fetch(panel.dataset.url, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"}, body
        });
        const raw = await response.text();
        let answer = null;
        try { answer = JSON.parse(raw); } catch { answer = null; }
        const problems = Array.isArray(answer?.errors)
          ? answer.errors.filter((item) => typeof item?.message === "string") : [];
        if (response.ok && answer?.queued === true) {
          // The shared clock (date-format-v1.js); without it the time is left out.
          const time = clockTime();
          text = time
            ? fillIn("Test queued at {time} for the Testing recipient. Check the Testing mailbox.",
              {time})
            : "Test queued for the Testing recipient. Check the Testing mailbox.";
          error = false;
        } else if (response.status === 400 && problems.length) {
          text = "This text can't be sent as a test. " + problems
            .map((item) => (item.field ? `${item.field}: ${item.message}` : item.message)).join(" ");
        } else {
          unknown = response.status === 409 && answer?.unknown === true;
          text = refusalText(response.status, raw);
        }
      } catch { /* no answer: said above */ }
      sending = false;
      send.removeAttribute("aria-disabled");
      send.classList.remove("is-busy");
      if (unknown) {
        acknowledgment.hidden = false;
        box.checked = false;
      }
      say(text, error);
      // The answer stays; only a reason that changes from here replaces it.
      shown = reason();
      send.disabled = Boolean(shown);
      if (unknown) box.focus();
    });
  });

  // "Generate plain text from HTML": while it is checked the plain-text field
  // is read-only and shows the server's own generated text for the current
  // HTML, so the Admin sees exactly what a save stores. "Edit plain text" (or
  // unchecking the box) keeps that text and makes it editable; typed text is
  // never replaced unless the Admin checks the box again. A checked box
  // submits no plain text (it is left out of the form's data), so
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
      // Generated text is not sent, so the change gate does not count it.
      text.toggleAttribute("data-change-ignore", box.checked);
      note.hidden = !box.checked;
      if (!box.checked) unavailable.hidden = true;
    };
    const edit = () => {
      box.checked = false;
      show();
      text.focus();
    };
    box.addEventListener("change", () => { show(); if (box.checked) refresh(); });
    const editButton = panel.querySelector("[data-edit-plain-text]");
    editButton?.addEventListener("click", edit);
    // While "Remove this selected content" is ticked the generated-text box
    // and the plain text are greyed (data-enabled-when), so "Edit plain
    // text" is unavailable with them; unticking gives it back (#563, #750).
    const clear = form.elements.namedItem("clear");
    if (editButton && clear instanceof HTMLInputElement) {
      const lockEdit = () => { editButton.disabled = clear.checked && !clear.disabled; };
      clear.addEventListener("change", lockEdit);
      window.addEventListener("pageshow", lockEdit);
      lockEdit();
    }
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
    // Leave the generated text out of whatever the form sends. The formdata
    // event fires for a navigating submission and for every FormData built
    // from the form, so a review sent in place (the review dialog, #750,
    // which reads the form before its own submit listeners run) leaves it
    // out too, and the field itself is never disabled, so nothing has to
    // re-enable it afterwards.
    form.addEventListener("formdata", (event) => {
      if (box.checked) event.formData.delete(text.name);
    });
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
  // The indicators carry their poll URLs (data-*-url, rendered from the URL
  // names), so moving an Admin route never leaves a stale path here.
  // Timers skip hidden tabs and never overlap requests or catch up missed ticks.
  const presenceIndicator = document.querySelector("[data-presence-indicator]");
  let presencePending = false;
  async function refreshPresence() {
    if (document.hidden || presencePending || !presenceIndicator) return;
    presencePending = true;
    const unavailable = document.querySelector("[data-presence-unavailable]");
    try {
      const response = await fetch(presenceIndicator.dataset.presenceUrl, {
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
      const response = await fetch(backgroundIndicator.dataset.backgroundUrl, {
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
      // The operating mode's uncertain emails and the other mode's, each
      // behind its own link to Sent mail in that mode (#1576).
      const uncertain = [result.delivery_unknown, result.delivery_unknown_other];
      if (deliveryWarning && uncertain.some((value) => !Number.isSafeInteger(value) || value < 0)) throw new Error("Invalid delivery count");
      backgroundIndicator.querySelector("[data-background-total]").textContent = total.toLocaleString("en-US");
      backgroundIndicator.querySelector("[data-background-running]").textContent = values[4].toLocaleString("en-US");
      if (deliveryWarning) {
        [["[data-delivery-here]", "[data-delivery-unknown]"],
          ["[data-delivery-other]", "[data-delivery-unknown-other]"]].forEach(([link, number], index) => {
          const shown = deliveryWarning.querySelector(link);
          const count = deliveryWarning.querySelector(number);
          const formatted = uncertain[index].toLocaleString("en-US");
          if (count.textContent !== formatted) count.textContent = formatted;
          if (shown.hidden !== (uncertain[index] === 0)) shown.hidden = uncertain[index] === 0;
        });
        const hidden = uncertain.every((value) => value === 0);
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
// next; on screen it floats over the page beside its button and never moves
// anything. The button's name is generic on purpose: naming it after its label
// ("More about Phone") would make it a second match for that label's text,
// both for voice control and for tests that find fields by label. Its
// description (the label, when given an id) says which field it explains. One document-level listener serves every toggletip, including ones
// page scripts add later. Only one bubble is open at a time; Escape, a click
// elsewhere or a click on the bubble itself closes it. Without JavaScript the bubble stays hidden and
// the button does nothing, so keep anything essential in visible text.
(() => {
  let count = 0;
  function close(tip, returnFocus = false) {
    tip.classList.remove("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "false");
    tip.querySelector(".toggletip-bubble").hidden = true;
    if (returnFocus) tip.querySelector(".toggletip-button").focus();
  }
  function place(tip) {
    // Put the bubble under its button, or above it when only there it
    // fits, aligned with the button's start and kept inside the viewport
    // (its CSS max-width already fits a phone). ui-v1.css fixes every
    // bubble to the viewport, so it sits outside every box's flow and
    // overflow: opening it never moves a control, grows a field or a
    // table's .table-scroll box, or scrolls anything (#1289). Rerun on
    // scroll and resize, since a fixed bubble does not move with its
    // button by itself.
    const bubble = tip.querySelector(".toggletip-bubble");
    const anchor = tip.querySelector(".toggletip-button").getBoundingClientRect();
    const edge = 8, gap = 6, box = bubble.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    const start = getComputedStyle(bubble).direction === "rtl" ? anchor.right - box.width : anchor.left;
    const below = anchor.bottom + gap, above = anchor.top - gap - box.height;
    const fitsBelow = below + box.height <= height - edge;
    bubble.style.left = Math.max(edge, Math.min(start, width - edge - box.width)) + "px";
    bubble.style.top = (fitsBelow || above < edge ? below : above) + "px";
  }
  function scrolledOut(tip) {
    // Whether the tip's button has left its .table-scroll box's visible
    // area. Fixed to the viewport, the bubble would otherwise stay drawn
    // over the page, pointing at a button the box no longer shows.
    const box = tip.closest(".table-scroll")?.getBoundingClientRect();
    if (!box) return false;
    const anchor = tip.querySelector(".toggletip-button").getBoundingClientRect();
    return anchor.right <= box.left || anchor.left >= box.right
      || anchor.bottom <= box.top || anchor.top >= box.bottom;
  }
  function follow() {
    // Keep an open bubble with its button as the page or a table's box
    // scrolls, and as the window changes size; close it once the box
    // scrolls its button out of view.
    const tip = document.querySelector(".toggletip-open");
    if (!tip) return;
    if (scrolledOut(tip)) close(tip);
    else place(tip);
  }
  document.addEventListener("scroll", follow, true);
  window.addEventListener("resize", follow);
  function open(tip) {
    document.querySelectorAll(".toggletip-open").forEach((other) => close(other));
    tip.classList.add("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "true");
    tip.querySelector(".toggletip-bubble").hidden = false;
    place(tip);
  }
  function inBubbleTap(event) {
    // A click or tap on the open bubble itself closes it, as in the usual
    // toggletip pattern (Inclusive Components): the bubble holds only plain
    // text, and since it floats it may cover the next field or "i", so a tap
    // on it is how someone gets to what lies underneath (#1289). A drag that
    // selects some of its text (to copy it) keeps it open. Focus stays put:
    // the bubble itself is not focusable.
    return Boolean(event.target.closest?.(".toggletip-bubble"))
      && document.getSelection().isCollapsed;
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".toggletip-button");
    const openTip = document.querySelector(".toggletip-open");
    if (button) {
      const tip = button.closest(".toggletip");
      if (tip === openTip) close(tip);
      else open(tip);
    } else if (openTip && (!openTip.contains(event.target) || inBubbleTap(event))) {
      close(openTip);
    }
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

  // A step bar segment whose step cannot open yet (stepper-steps.html,
  // #1402) is focusable but has no address. Clicking or tapping it only
  // focuses it, so its floating tooltip shows why it is unavailable: Safari
  // does not focus on click, and a phone tap neither hovers nor focuses.
  // Delegated, so the Campaign setup stepper's in-place redraws keep it.
  document.addEventListener("click", (event) => {
    const segment = event.target.closest?.("[data-step-unavailable]");
    if (!segment) return;
    event.preventDefault();
    segment.focus();
  });

  // Secret fields (SecretInput, accounts/secret_input.py, #1475): the
  // server draws a Show/Hide button beside each masked key or token, and
  // this switches its field between password and text. Only the type
  // changes: the value is never read, logged, stored or sent from here, so
  // the form posts exactly what it would while masked. data-shown carries
  // the state and CSS swaps the visible word in a reserved width, which
  // also changes the accessible name ("Show/Hide the Slack bot token"); a
  // name change on the focused button is not reliably read out, so the
  // polite status line beside it says the new state. Delegated, so a field
  // an in-place redraw brings in (drawn masked by the server) works at once.
  const showSecret = (button, shown) => {
    const field = document.getElementById(button.getAttribute("aria-controls"));
    if (!field) return;
    field.type = shown ? "text" : "password";
    button.dataset.shown = String(shown);
    const status = button.parentElement.querySelector("[data-secret-status]");
    if (status) status.textContent = shown ? button.dataset.shownStatus : button.dataset.hiddenStatus;
  };
  // Mask every shown secret under root again.
  const maskSecrets = (root) => {
    root.querySelectorAll?.('[data-secret-reveal][data-shown="true"]')
      .forEach((button) => showSecret(button, false));
  };
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-secret-reveal]");
    if (button) showSecret(button, button.dataset.shown !== "true");
  });
  // Mask again before the request leaves (capture, ahead of handlers that
  // post in place), so a page the browser keeps in its history is masked,
  // and on leaving or returning to the page, including a page restored from
  // the back/forward cache.
  document.addEventListener("submit", (event) => maskSecrets(event.target), true);
  window.addEventListener("pagehide", () => maskSecrets(document));
  window.addEventListener("pageshow", () => maskSecrets(document));
})();
