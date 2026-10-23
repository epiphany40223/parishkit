// New and Edit report email (#1786, delivery plan item 6; report-emails spec,
// "New and Edit report email"). The page's own controls, on top of
// ui-v1.js's shared ones (the review dialog, the change and complete gates,
// conditional fields, time entry, the browser's time zone, Basic and
// Advanced modes):
//   - the recipients picker: a text box whose suggestion list holds the
//     eligible users and groups; Add (or Enter) makes a chip holding a hidden
//     recipient field. A listed user is matched by name or by address (in
//     any letter case), so a user's own address adds that user; any other
//     valid address becomes an outside address, with the spec's one-line
//     warning;
//   - the Blocks list: Add a block, Move up, Move down and Remove act in
//     place, keep focus on the moved row's control (or the next one) and say
//     what happened; each row holds its hidden block field, in order;
//   - the repeating schedule's Basic choices, which write the Advanced fields
//     (ParishEditorMode's adapter);
//   - the live Next sends table, read from the server as the fields change
//     and redrawn in its region, which keeps its height; on Edit its boxes
//     feed Skip selected sends and Send selected sends (the shared
//     selectable table, wired again after each redraw);
//   - the line saying a repeating report's days are the campaign's, shown
//     when this browser's zone is not the campaign's;
//   - New's Start from (#2037): Blank, Daily report or Weekly report fills
//     every field in place from the starting points the server embeds
//     (report-starting-points, drawn from campaigns/report_presets.py), the
//     time left empty, asking first when the form holds typing it would
//     replace.
// The review dialog's "Send after the campaign ends" tick needs no code
// here: it is the Apply form's own field, held by the shared review gate.
// The server reads and checks every value again; nothing here decides one.
(() => {
  "use strict";

  const form = document.querySelector("form[data-report-editor]");
  if (!form) return;
  const fill = (text, values) => Object.entries(values).reduce(
    (result, [key, value]) => result.split(`{${key}}`).join(value), text || "");
  // A change made by script tells the shared gates, as typing would.
  const changed = (node) => node.dispatchEvent(new Event("change", {bubbles: true}));
  const csrf = () => form.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";

  // ---------------------------------------------------------- recipients
  const input = form.querySelector("[data-recipient-input]");
  const chips = form.querySelector("[data-recipient-chips]");
  const note = form.querySelector("[data-recipient-note]");
  const choices = [...document.querySelectorAll("#report-recipient-choices option")];
  // Ministry leaders' addresses (decision 2): never offered, and refused as
  // typed, so one is never taken for an outside address.
  let leaders = new Set();
  try {
    leaders = new Set(JSON.parse(document.getElementById("report-leader-addresses")?.textContent || "[]"));
  } catch { /* none listed: the server still refuses them */ }
  const say = (text) => { if (note) note.textContent = text; };
  const chosen = () => [...chips.querySelectorAll('input[name="recipient"]')].map((box) => box.value);
  // A simple shape check only; the server checks every address properly.
  const ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const removeChip = (chip) => {
    const label = chip.querySelector("span")?.textContent || "";
    const next = chip.nextElementSibling?.querySelector("[data-chip-remove]");
    chip.remove();
    changed(form);
    (next || input).focus();
    say(fill(form.dataset.recipientRemoved, {name: label}));
  };
  const addChip = (value, label) => {
    const chip = document.createElement("li");
    chip.setAttribute("data-chip", "");
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "recipient";
    hidden.value = value;
    // A saved setting the page shows only as a chip: the change gate counts
    // it (data-change-setting), so adding or removing one is a change.
    hidden.setAttribute("data-change-setting", "");
    const text = document.createElement("span");
    text.textContent = label;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button-secondary";
    remove.setAttribute("data-chip-remove", "");
    remove.setAttribute("aria-label", fill(form.dataset.removeLabel, {name: label}));
    remove.textContent = form.dataset.removeText || "Remove";
    chip.append(hidden, text, " ", remove);
    chips.append(chip);
    changed(hidden);
  };
  const add = () => {
    const typed = (input.value || "").trim();
    if (!typed) return;
    // The option's text is a user's address; the server makes a user's
    // address that user's entry too, so the two agree.
    const folded = typed.toLowerCase();
    const match = choices.find((option) => option.value === typed
      || (option.textContent.trim() && option.textContent.trim().toLowerCase() === folded));
    let value = "";
    let label = typed;
    let outside = false;
    if (match) {
      value = match.dataset.value;
      label = match.value;
    } else if (leaders.has(folded)) {
      say(fill(form.dataset.recipientLeader, {address: folded}));
      return;
    } else if (ADDRESS.test(typed)) {
      value = `address:${typed.toLowerCase()}`;
      label = fill(form.dataset.chipOutside, {address: typed.toLowerCase()});
      outside = true;
    } else {
      say(form.dataset.recipientUnknown);
      return;
    }
    if (chosen().includes(value)) {
      say(form.dataset.recipientTwice);
      return;
    }
    addChip(value, label);
    input.value = "";
    say(outside ? form.dataset.outsideNote : fill(form.dataset.recipientAdded, {name: label}));
    input.focus();
  };
  form.querySelector("[data-recipient-add]")?.addEventListener("click", add);
  input?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    // Enter adds the recipient; it never submits the whole form.
    event.preventDefault();
    add();
  });
  chips?.addEventListener("click", (event) => {
    const button = event.target instanceof Element && event.target.closest("[data-chip-remove]");
    if (button) removeChip(button.closest("[data-chip]"));
  });

  // -------------------------------------------------------------- blocks
  const list = form.querySelector("[data-block-list]");
  const select = form.querySelector("[data-block-choice]");
  const addButton = form.querySelector("[data-block-add]");
  const help = form.querySelector("[data-block-help]");
  const reason = form.querySelector("[data-block-reason]");
  const templates = document.getElementById("report-block-templates");
  const labelOf = (row) => row.querySelector(".report-block-label")?.textContent.trim() || "";
  const rows = () => [...list.querySelectorAll(":scope > [data-block]")];
  const announce = (text) => { if (reason) reason.textContent = text; };
  // Move up on the first row and Move down on the last (or before a block
  // that stays last) are unavailable, saying why in the row's own line.
  const refresh = () => {
    const all = rows();
    const movable = all.filter((row) => !row.hasAttribute("data-block-last"));
    all.forEach((row) => {
      const at = movable.indexOf(row);
      const up = row.querySelector("[data-block-up]");
      const down = row.querySelector("[data-block-down]");
      const line = row.querySelector("[data-block-reason]");
      const first = at === 0;
      const last = at === movable.length - 1;
      up?.setAttribute("aria-disabled", String(first));
      down?.setAttribute("aria-disabled", String(last));
      if (line && !row.hasAttribute("data-block-last")) {
        line.textContent = first && last ? form.dataset.onlyBlock
          : first ? form.dataset.firstBlock
            : last ? form.dataset.lastBlock : "";
      }
    });
    const empty = select.options.length === 0;
    addButton.setAttribute("aria-disabled", String(empty));
    select.disabled = empty;
    if (help) help.textContent = select.selectedOptions[0]?.dataset.help || "";
    if (empty && reason) reason.textContent = reason.dataset.allIn;
  };
  const position = (row) => rows().indexOf(row) + 1;
  // Put block ``key``'s row in the list (before a block that stays last)
  // and take it out of Add a block; the row, or null for an unknown key.
  const listBlock = (key) => {
    const source = templates?.content.querySelector(`[data-block="${CSS.escape(key)}"]`);
    if (!source) return null;
    const row = source.cloneNode(true);
    const stays = rows().find((item) => item.hasAttribute("data-block-last"));
    if (stays && !row.hasAttribute("data-block-last")) list.insertBefore(row, stays);
    else list.append(row);
    select.querySelector(`option[value="${CSS.escape(key)}"]`)?.remove();
    return row;
  };
  // Take a row out of the list and offer its block in Add a block again.
  const unlistBlock = (row) => {
    const option = document.createElement("option");
    option.value = row.dataset.block;
    option.textContent = labelOf(row);
    option.dataset.help = row.querySelector(".help")?.textContent || "";
    select.append(option);
    row.remove();
  };
  select?.addEventListener("change", refresh);
  addButton?.addEventListener("click", () => {
    if (addButton.getAttribute("aria-disabled") === "true") return;
    const row = listBlock(select.value);
    if (!row) return;
    refresh();
    changed(row.querySelector('input[name="block"]'));
    (row.querySelector("[data-block-remove]") || row).focus();
    announce(fill(form.dataset.added, {name: labelOf(row)}));
  });
  list?.addEventListener("click", (event) => {
    const button = event.target instanceof Element && event.target.closest("button");
    const row = button?.closest("[data-block]");
    if (!row || button.getAttribute("aria-disabled") === "true") return;
    const name = labelOf(row);
    if (button.hasAttribute("data-block-remove")) {
      const next = row.nextElementSibling?.querySelector("[data-block-remove]");
      unlistBlock(row);
      refresh();
      changed(form);
      (next || select).focus();
      announce(fill(form.dataset.removed, {name}));
      return;
    }
    const up = button.hasAttribute("data-block-up");
    const other = up ? row.previousElementSibling : row.nextElementSibling;
    if (!other || other.hasAttribute("data-block-last")) return;
    if (up) list.insertBefore(row, other);
    else list.insertBefore(other, row);
    refresh();
    changed(row.querySelector('input[name="block"]'));
    button.focus();
    announce(fill(form.dataset.moved, {name, position: position(row), count: rows().length}));
  });
  if (list && select) refresh();

  // ------------------------------------------- Basic and Advanced modes
  // Each Basic choice is a rule of the Advanced fields.
  const field = (name) => form.elements.namedItem(name);
  const PRESETS = {
    daily: {frequency: "daily", from_kind: "day_after_campaign_start",
      until_kind: "day_after_campaign_end"},
    weekly: {frequency: "weekly", from_kind: "campaign_start",
      until_kind: "first_send_after_campaign_end"},
  };
  // Frequency is a pair of radios (a RadioNodeList, which takes a value
  // but fires no event), the other fields selects; the change event goes
  // to the control now chosen.
  const setValue = (name, value) => {
    const node = field(name);
    if (node && node.value !== value) {
      node.value = value;
      const target = node instanceof RadioNodeList
        ? [...node].find((radio) => radio.checked) : node;
      if (target) changed(target);
    }
  };
  const weekdayBoxes = () => [...form.querySelectorAll('input[name="weekdays"]')];
  window.ParishEditorMode?.register("report-schedule", {
    toAdvanced() {
      const choice = form.querySelector('input[name="preset"]:checked')?.value;
      const preset = PRESETS[choice];
      if (!preset) return;
      Object.entries(preset).forEach(([name, value]) => setValue(name, value));
      if (choice === "weekly") {
        const day = field("preset_weekday")?.value;
        weekdayBoxes().forEach((box) => { box.checked = box.value === day; });
      }
    },
    toBasic() {
      const frequency = field("frequency")?.value;
      const days = weekdayBoxes().filter((box) => box.checked).map((box) => box.value);
      const match = Object.entries(PRESETS).find(([key, preset]) =>
        Object.entries(preset).every(([name, value]) => field(name)?.value === value)
        && (key !== "weekly" || days.length === 1));
      if (!match || (frequency === "weekly" && days.length !== 1)) return false;
      const [key] = match;
      const radio = form.querySelector(`input[name="preset"][value="${key}"]`);
      if (!radio) return false;
      radio.checked = true;
      if (key === "weekly") setValue("preset_weekday", days[0]);
      return true;
    },
  });

  // --------------------------------------------------- Next sends table
  // Looked up each time: Skip and Send selected sends' Apply redraws the
  // region in place from the page (ui-v1.js), replacing the element.
  const PREVIEW = "report-schedule-preview";
  const preview = () => document.getElementById(PREVIEW);
  const SCHEDULE = new Set(["sends", "once_date", "time", "editor_mode", "preset",
    "preset_weekday", "frequency", "weekdays", "from_kind", "from_date", "until_kind",
    "until_date", "as_of", "name", "zone"]);
  let timer = null;
  let request = null;
  const redraw = async () => {
    request?.abort();
    request = new AbortController();
    const mine = request;
    const body = new URLSearchParams(new FormData(form));
    if (form.dataset.reportId) body.set("report", form.dataset.reportId);
    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf(), "X-Requested-With": "fetch",
          "X-ParishKit-Read": "passive"},
        body, signal: mine.signal,
      });
      const region = preview();
      if (!response.ok || mine !== request || !region) return;
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      region.replaceChildren(...[...parsed.body.childNodes].map((node) => document.importNode(node, true)));
      // The shared enhancement wires the table's selection and writes its
      // times in this browser's zone, as for any region swapped in.
      if (window.ParishRegions) window.ParishRegions.enhance(region);
      else window.ParishDates?.localize(region);
    } catch { /* The preview stays as it was; Review checks the schedule. */ }
  };
  form.addEventListener("change", (event) => {
    const name = event.target?.name;
    if (!preview() || !SCHEDULE.has(name)) return;
    window.clearTimeout(timer);
    timer = window.setTimeout(redraw, 400);
  });

  // The Campaign calendar links a send as ?send=<slot key>: its box in
  // Next sends is ticked and focused, so the reader lands on the send they
  // chose with Skip selected sends (or, for a skipped send, Send selected
  // sends) one step away. A send with no box (paused or being prepared)
  // ticks nothing.
  const named = new URLSearchParams(window.location.search).get("send");
  if (named) {
    const box = [...(preview()?.querySelectorAll('input[name="send"][data-select-row]') || [])]
      .find((item) => item.value === named);
    if (box) {
      box.checked = true;
      changed(box);
      box.focus();
    }
  }

  // The redrawn table carries the configuration version the skip made
  // (data-base-digest). The editor takes it, so its next Review is read
  // against the configuration that now holds the skip. The reader's typing
  // is kept; Review's change gate then compares it with the saved values.
  document.addEventListener("parishkit:swap", (event) => {
    if (event.target?.id !== PREVIEW) return;
    const version = form.elements.namedItem("base_digest");
    if (version && event.target.dataset.baseDigest) version.value = event.target.dataset.baseDigest;
  });

  // ---------------------------------------------------------- Start from
  // New's Start from (report-emails spec, "New and Edit report email"):
  // three radios above the form (never posted), each filling every field
  // from one starting point the server embeds, in initial()'s field form.
  // The time is left empty, so the complete gate asks for it. When the form
  // differs from what the page drew or Start from last filled, the shared
  // confirmation dialog asks first; Keep what I entered puts the previous
  // choice back.
  const start = document.querySelector("[data-start-from]");
  let points = [];
  try {
    points = JSON.parse(document.getElementById("report-starting-points")?.textContent || "[]");
  } catch { /* none embedded: Start from does nothing */ }
  if (start && points.length) {
    const radios = [...start.querySelectorAll('input[name="start_from"]')];
    const startNote = start.querySelector("[data-start-note]");
    const dialog = document.getElementById("report-start-replace");
    // The form as it would post, less the fields no reader types (the CSRF
    // token, the browser's zone and its drawn moment), the mode and the
    // Basic panel's choice: the Advanced fields always hold the schedule
    // (ParishEditorMode), and the Basic panel posts nothing while Advanced
    // is shown, so switching modes alone is never typing to replace.
    const UNTYPED = new Set(["csrfmiddlewaretoken", "zone", "drawn", "editor_mode",
      "preset", "preset_weekday"]);
    const snapshot = () => [...new FormData(form)]
      .filter(([name]) => !UNTYPED.has(name))
      .map(([name, value]) => `${name}=${value}`).join("&");
    // A refused Add's page holds what was typed: it counts as changed.
    let filled = start.hasAttribute("data-start-changed") ? null : snapshot();
    let previous = radios.find((radio) => radio.checked) || null;
    // The introduction's shared editor: its pane, HTML source and text.
    const pane = form.querySelector("[data-content-editor]");
    const generate = field("generate_text");
    const generateDrawn = generate instanceof HTMLInputElement && generate.checked;
    // A field's value from the starting point: radios and checkbox groups
    // tick the listed values, a lone checkbox takes a flag, anything else
    // its text. A change event then tells the gates, the conditional
    // fields and Next sends, as typing would; a text field gets an input
    // event too, so the shared live checks clear any mark on what it held.
    const setField = (name, value) => {
      const node = field(name);
      if (!node) return;
      if (node instanceof RadioNodeList) {
        const wanted = (Array.isArray(value) ? value : [value]).map(String);
        [...node].forEach((box) => { box.checked = wanted.includes(box.value); });
        const target = [...node].find((box) => box.checked) || node[0];
        if (target) changed(target);
        return;
      }
      if (node.type === "checkbox") node.checked = Boolean(value);
      else {
        node.value = value === null || value === undefined ? "" : String(value);
        node.dispatchEvent(new Event("input", {bubbles: true}));
      }
      changed(node);
    };
    const clearIntroduction = () => {
      if (pane && pane.textContent.trim() !== "") {
        pane.replaceChildren();
        // The pane's own input listener writes the (empty) HTML source.
        pane.dispatchEvent(new Event("input", {bubbles: true}));
      }
      const source = field("html");
      if (source && source.value !== "") setField("html", "");
      if (generate instanceof HTMLInputElement && generate.checked !== generateDrawn) {
        setField("generate_text", generateDrawn);
      }
      if (field("text") && field("text").value !== "") setField("text", "");
    };
    const fillFrom = (point) => {
      // Recipients: the starting point's chips only.
      chips.querySelectorAll("[data-chip]").forEach((chip) => chip.remove());
      point.recipients.forEach(({value, label}) => addChip(value, label));
      if (input) input.value = "";
      say("");
      // Blocks: the starting point's rows, in order, and Add a block offers
      // the others in the registry's order (the templates').
      rows().forEach((row) => row.remove());
      select.replaceChildren();
      [...(templates?.content.querySelectorAll(":scope > [data-block]") || [])].forEach((row) => {
        const option = document.createElement("option");
        option.value = row.dataset.block;
        option.textContent = labelOf(row);
        option.dataset.help = row.dataset.help || "";
        select.append(option);
      });
      point.blocks.forEach(listBlock);
      announce("");
      refresh();
      changed(form);
      // The Basic choice first: an edit in the Basic panel rewrites the
      // Advanced fields from the choice it holds (ParishEditorMode), so the
      // choice and its day are set before the Advanced fields, which are
      // then written as the starting point has them; the mode last. The
      // listed rows' options (Day by day's days, the charts) are among them.
      const {editor_mode: mode, preset, preset_weekday: weekday, ...rest} = point.fields;
      setField("preset", preset);
      setField("preset_weekday", weekday);
      Object.entries(rest).forEach(([name, value]) => setField(name, value));
      setField("charts", point.charts);
      const modes = form.querySelector('[data-editor-modes="report-schedule"]');
      if (modes) window.ParishEditorMode?.show(modes, mode);
      clearIntroduction();
      filled = snapshot();
    };
    // Choose ``radio``'s starting point: fill the form, keep focus on the
    // radio and say what happened.
    const choose = (radio) => {
      const point = points.find((item) => item.key === radio.value);
      if (!point) return;
      fillFrom(point);
      previous = radio;
      radio.checked = true;
      radio.focus();
      if (startNote) {
        startNote.textContent = point.key === "blank" ? start.dataset.blankNote
          : fill(start.dataset.filledNote, {name: point.label});
      }
    };
    let pending = null;
    dialog?.querySelector("[data-confirm-accept]")?.addEventListener("click", () => {
      const radio = pending;
      pending = null;
      dialog.close();
      if (radio) choose(radio);
    });
    // Keep what I entered, Escape or closing: nothing changes, and Start
    // from goes back to its previous choice, with focus on it. On a refused
    // Add there is none: nothing is checked and focus returns to the
    // segment just chosen, the control that opened the dialog. Keep puts
    // it back at once; Escape's close event, which comes a moment after
    // the dialog shuts, does the same.
    const keep = () => {
      const chosen = pending;
      if (!chosen) return;
      pending = null;
      radios.forEach((radio) => { radio.checked = radio === previous; });
      (previous || chosen).focus();
    };
    dialog?.querySelector("[data-confirm-cancel]")?.addEventListener("click", () => {
      dialog.close();
      keep();
    });
    dialog?.addEventListener("close", keep);
    start.addEventListener("change", (event) => {
      const radio = event.target;
      if (!(radio instanceof HTMLInputElement) || !radio.checked || !radios.includes(radio)) return;
      if (filled !== null && snapshot() === filled) {
        choose(radio);
        return;
      }
      if (!(dialog instanceof HTMLDialogElement)) return;
      pending = radio;
      // The shared confirmation dialog's Tab wrap and Escape (ui-v1.js).
      window.ParishConfirm?.wire(dialog);
      dialog.showModal();
      // The safe choice takes focus first.
      dialog.querySelector("[data-confirm-cancel]")?.focus();
    });
  }

  // ------------------------------------------ the campaign's weekdays
  // A repeating report's days are the campaign's days; only its time is
  // read in this browser's zone. Where the two zones keep different clocks
  // the page says so under the day choices; it is decided once, as the page
  // loads. The zones are compared by their clocks, not their names: two
  // names for the same rules (America/Louisville and America/New_York) read
  // every instant alike, so they get no note (#1973), without a table of
  // aliases to keep up. The clocks are compared now and every hour from
  // midnight of the campaign's first day, in the campaign's zone, to two
  // days after its last (a repeating report's last send), so a
  // daylight-saving change on one side only is seen, even one that keeps
  // the clocks apart for a single hour (America/Havana and New York, 05:00
  // to 06:00 UTC on 2026-11-01).
  const HOUR = 3600000;
  const clocksDiffer = (zone, other) => {
    if (zone === other) return false;
    const options = {year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"};
    let mine, theirs;
    try {
      mine = new Intl.DateTimeFormat("en-US", {...options, timeZone: zone});
      theirs = new Intl.DateTimeFormat("en-US", {...options, timeZone: other});
    } catch { return true; } // A zone this browser can't read: say so.
    const now = Date.now();
    if (mine.format(now) !== theirs.format(now)) return true;
    // Midnight starting a campaign day in the campaign's zone: 00:00 UTC of
    // that date, moved by the zone's offset from UTC there (read from its
    // clock), so a zone east of UTC has its first hours sampled too.
    const day = (text) => {
      const at = Date.parse(`${text}T00:00:00Z`);
      if (Number.isNaN(at)) return now;
      const [m, d, y, h, min] = theirs.format(at).match(/\d+/g).map(Number);
      return at - (Date.UTC(y, m - 1, d, h, min) - at);
    };
    const first = day(form.dataset.campaignStart);
    const last = day(form.dataset.campaignEnd) + 48 * HOUR;
    // A campaign is months long; the bound (over a year of hours) only
    // guards against odd dates.
    for (let at = first, step = 0; at <= last && step < 10000; at += HOUR, step += 1) {
      if (mine.format(at) !== theirs.format(at)) return true;
    }
    return false;
  };
  let zone = "";
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* none */ }
  if (zone && form.dataset.campaignZone && clocksDiffer(zone, form.dataset.campaignZone)) {
    form.querySelectorAll("[data-campaign-days]").forEach((line) => { line.hidden = false; });
  }
})();
