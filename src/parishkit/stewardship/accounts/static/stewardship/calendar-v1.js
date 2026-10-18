"use strict";

// Campaign calendar (#469): draws the page's list of sends as a month grid,
// one month at a time (#1656). The hidden list (schedule-calendar.html,
// data-calendar-source) holds every send in sending order: each item names
// its send time, schedule and status and, while the schedule can still
// change, its Edit page. The table below is the grid's text equivalent with
// its own Show and sort, refreshed in place by ui-v1.js, so the grid reads
// the list, never the table. Each send goes into the cell of its day in this browser's time zone,
// as every Admin time is browser-local; the campaign's first and last days
// are calendar dates and are never shifted. Text is only ever set as text:
// nothing read from the page is parsed as markup.
//
// The months run from the earliest to the latest of the campaign's dates and
// its sends (a send late on the last day can fall on the next day here).
// Previous month and Next month redraw the grid in place, with no request
// (UX conventions, "In-place controls", views the page script draws): focus
// stays on the button, the month is announced, and the address's ?month= is
// replaced, so Refresh and Back return to it. At an end the button stays in
// place but is unavailable (aria-disabled, so it keeps focus), and the
// reserved line after the buttons says why. Every month has six week rows,
// so a month change never changes the grid's row count.
//
// Each day of the campaign's dates that is today or later (browser-local)
// also gets a (+) Add link (#1521) that opens New scheduled email with that
// day as its send date, and every Edit link (grid and table) carries the
// month shown (?calendar=), so Edit returns to it; the page itself never
// changes anything. The table's Show and sort links carry the month shown
// too (?month=), so the address they leave keeps it; both are set again
// after each in-place swap of the table (parishkit:swap).
(() => {
  const root = document.querySelector("[data-calendar]");
  const template = document.getElementById("calendar-month");
  const dates = window.ParishDates;
  const name = document.querySelector("[data-calendar-name]");
  const reason = document.querySelector("[data-calendar-reason]");
  const said = document.querySelector("[data-calendar-said]");
  const steps = Array.from(document.querySelectorAll("[data-calendar-step]"));
  if (!root || !template || !dates || !name || !reason || !said) return;

  const SVG = "http://www.w3.org/2000/svg";
  // A year and month as the address names one (``2054-11``).
  const YEAR_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
  const two = (value) => String(value).padStart(2, "0");
  const dayKey = (year, month, day) => `${year}-${two(month)}-${two(day)}`;
  const localKey = (date) => dayKey(date.getFullYear(), date.getMonth() + 1, date.getDate());
  // A month as one number (year * 12 + zero-based month), so stepping is
  // arithmetic, and back to the address's form.
  const monthIndex = (key) => {
    const [year, month] = key.split("-").map(Number);
    return year * 12 + month - 1;
  };
  const monthKey = (index) => `${Math.floor(index / 12)}-${two((index % 12) + 1)}`;

  // The list's sends grouped by local day ("2054-10-05"), each day's in
  // sending order.
  function sendsByDay() {
    const days = new Map();
    document.querySelectorAll("[data-calendar-source] [data-calendar-send]").forEach((row) => {
      const node = row.querySelector("time[datetime]");
      const when = node ? new Date(node.dateTime) : null;
      if (!when || !Number.isFinite(when.getTime())) return;
      const key = localKey(when);
      if (!days.has(key)) days.set(key, []);
      days.get(key).push({when, row});
    });
    return days;
  }

  // An Edit page's address with the month shown, so its Cancel, Apply and
  // Delete return to this month of the calendar (#1656).
  function editUrl(edit, index) {
    const url = new URL(edit, document.baseURI);
    url.searchParams.set("calendar", monthKey(index));
    return url.pathname + url.search;
  }

  // One send in a day: its local time and schedule name, a link to its Edit
  // page when it has one, and its status for screen readers (the table shows
  // it to everyone). ``row`` is the send's item in the hidden list.
  function sendItem({when, row}, index) {
    const item = document.createElement("li");
    if (row.classList.contains("schedule-past")) item.className = "schedule-past";
    const edit = row.dataset.editUrl;
    const target = document.createElement(edit ? "a" : "span");
    if (edit) target.href = editUrl(edit, index);
    const time = document.createElement("time");
    time.dateTime = when.toISOString();
    time.textContent = dates.time(when);
    target.append(time, ` ${row.dataset.label}`);
    const status = document.createElement("span");
    status.className = "visually-hidden";
    status.textContent = `, ${row.dataset.status}`;
    item.append(target, status);
    return item;
  }

  // The (+) icon of a day's Add, drawn inline like the table action icons
  // (components/action-icon.html): decoration, as the link carries the name.
  function plusIcon() {
    const icon = document.createElementNS(SVG, "svg");
    icon.setAttribute("class", "action-icon");
    icon.setAttribute("viewBox", "0 0 16 16");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", "M8 3v10M3 8h10");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.8");
    path.setAttribute("stroke-linecap", "round");
    icon.append(path);
    return icon;
  }

  // A day's Add link (#1521): New scheduled email with ``key`` (this
  // browser's local day, which the send date field is typed in) as its send
  // date. It shows only a (+) icon (#1656); its name and matching tooltip
  // carry the whole date ("Add a scheduled email on October 12, 2054"), so
  // a screen reader, voice control and a keyboard user tabbing through the
  // grid hear which day it adds to.
  function addLink(key) {
    const link = document.createElement("a");
    const url = new URL(root.dataset.addUrl, document.baseURI);
    url.searchParams.set("date", key);
    link.href = url.pathname + url.search;
    link.className = "calendar-add";
    const label = root.dataset.addName.replace("{date}", dates.date(key));
    link.setAttribute("aria-label", label);
    link.title = label;
    link.append(plusIcon());
    return link;
  }

  // Whether ``key`` is one of the campaign's dates and not already past
  // (ISO days compare as text); only those get an Add, since New would
  // refuse a passed time.
  const canAdd = (key, today) => Boolean(root.dataset.start && root.dataset.end)
    && root.dataset.start <= key && key <= root.dataset.end && key >= today;

  // A visible note in a day: Today, Campaign opens or Campaign closes.
  function marker(text) {
    const note = document.createElement("span");
    note.className = "calendar-marker";
    note.textContent = text;
    return note;
  }

  // One day's cell: its number (with Add on a campaign day), its notes and
  // its sends.
  function dayCell(key, day, days, today, index) {
    const cell = document.createElement("td");
    const head = document.createElement("div");
    head.className = "calendar-day-head";
    const number = document.createElement("span");
    number.className = "calendar-day";
    number.textContent = String(day);
    head.append(number);
    if (root.dataset.addUrl && canAdd(key, today)) head.append(addLink(key));
    cell.append(head);
    if (key === today) {
      cell.setAttribute("aria-current", "date");
      cell.append(marker(root.dataset.today));
    }
    if (key === root.dataset.start) cell.append(marker(root.dataset.opens));
    if (key === root.dataset.end) cell.append(marker(root.dataset.closes));
    const sends = days.get(key);
    if (sends) {
      const list = document.createElement("ul");
      sends.forEach((send) => list.append(sendItem(send, index)));
      cell.append(list);
    }
    return cell;
  }

  // One month (``index``, monthIndex's) as a table, Sunday first, from the
  // page's template (whose weekday headings are translated), always six
  // week rows long so every month's grid has the same rows.
  function monthGrid(index, days, today) {
    const year = Math.floor(index / 12);
    const month = (index % 12) + 1;
    const region = template.content.firstElementChild.cloneNode(true);
    const caption = region.querySelector("caption");
    caption.id = `calendar-${dayKey(year, month, 1)}`;
    caption.textContent = dates.month(year, month);
    region.setAttribute("aria-labelledby", caption.id);
    const body = region.querySelector("tbody");
    const blank = new Date(year, month - 1, 1).getDay();
    const length = new Date(year, month, 0).getDate();
    let row = null;
    for (let slot = 0; slot < 42; slot += 1) {
      if (slot % 7 === 0) {
        row = document.createElement("tr");
        body.append(row);
      }
      const day = slot - blank + 1;
      row.append(day < 1 || day > length
        ? document.createElement("td")
        : dayCell(dayKey(year, month, day), day, days, today, index));
    }
    return region;
  }

  const days = sendsByDay();
  const keys = [...days.keys(), root.dataset.start, root.dataset.end].filter(Boolean).sort();
  if (!keys.length) return;
  const today = localKey(new Date());
  const first = monthIndex(keys[0]);
  const last = monthIndex(keys[keys.length - 1]);

  // The table's links with the month shown: each Edit link's ?calendar=,
  // and its Show segments' and sort headings' ?month=. The table is
  // swapped in place, so they are looked up afresh each time.
  function linkTable(index) {
    document.querySelectorAll("tr[data-calendar-row][data-edit-url] .table-actions a[href]")
      .forEach((link) => {
        link.href = editUrl(link.closest("tr").dataset.editUrl, index);
      });
    document.querySelectorAll("[data-table-region] a[data-in-place][href], [data-table-region] th a.sort-link[href]")
      .forEach((link) => {
        const url = new URL(link.href, document.baseURI);
        url.searchParams.set("month", monthKey(index));
        link.href = url.pathname + url.search + url.hash;
      });
  }

  // Draw month ``index``: the grid, the month's name after the buttons, each
  // button available or not (with the reason line), and the table's Edit
  // links with this month. ``announce`` says the month in the status region,
  // only after a button changed it, not as the page loads.
  let shown = null;
  function draw(index, announce) {
    shown = index;
    root.replaceChildren(monthGrid(index, days, today));
    const label = dates.month(Math.floor(index / 12), (index % 12) + 1);
    name.textContent = label;
    steps.forEach((button) => {
      const next = index + Number(button.dataset.calendarStep);
      if (next < first || next > last) {
        button.setAttribute("aria-disabled", "true");
        button.setAttribute("aria-describedby", reason.id);
      } else {
        button.removeAttribute("aria-disabled");
        button.removeAttribute("aria-describedby");
      }
    });
    const why = [];
    if (index === first) why.push(reason.dataset.first);
    if (index === last) why.push(reason.dataset.last);
    reason.textContent = why.join(" ");
    linkTable(index);
    if (announce) said.textContent = label;
  }

  // The address's month replaced (never added to the history), keeping the
  // rest of the address.
  function remember(index) {
    const url = new URL(window.location.href);
    url.searchParams.set("month", monthKey(index));
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }

  // The month to open on: the address's, else this browser's current month
  // when it is one of the campaign's, else the first; a month outside them
  // opens the nearer end, which the address then names.
  const asked = new URLSearchParams(window.location.search).get("month");
  let start = asked && YEAR_MONTH.test(asked) ? monthIndex(asked) : monthIndex(today);
  if (!asked && (start < first || start > last)) start = first;
  start = Math.min(last, Math.max(first, start));
  draw(start, false);
  if (asked && asked !== monthKey(start)) remember(start);

  steps.forEach((button) => button.addEventListener("click", () => {
    const next = shown + Number(button.dataset.calendarStep);
    if (button.getAttribute("aria-disabled") === "true" || next < first || next > last) return;
    draw(next, true);
    remember(next);
  }));
  document.addEventListener("parishkit:swap", () => linkTable(shown));
})();
