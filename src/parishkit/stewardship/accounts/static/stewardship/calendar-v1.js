"use strict";

// Campaign calendar (#469): draws the page's table of sends as month grids.
// The table (schedule-calendar.html) is both the source and the grids' text
// equivalent: each row names its send time, schedule and status and, while
// the schedule can still change, its Edit page. Each send goes into the cell
// of its day in this browser's time zone, as every Admin time is
// browser-local; the campaign's first and last days are calendar dates and
// are never shifted. The grids are drawn once, as the page loads and before
// anything can be chosen, so nothing moves under the pointer later. Text is
// only ever set as text: nothing read from the page is parsed as markup.
// Each day of the campaign's dates also gets an Add link (#1521) that opens
// New scheduled email with that day as its send date; the page itself never
// changes anything.
(() => {
  const root = document.querySelector("[data-calendar]");
  const template = document.getElementById("calendar-month");
  const dates = window.ParishDates;
  if (!root || !template || !dates) return;

  const two = (value) => String(value).padStart(2, "0");
  const dayKey = (year, month, day) => `${year}-${two(month)}-${two(day)}`;
  const localKey = (date) => dayKey(date.getFullYear(), date.getMonth() + 1, date.getDate());

  // The table's sends grouped by local day ("2054-10-05"), each day's in the
  // table's sending order.
  function sendsByDay() {
    const days = new Map();
    document.querySelectorAll("tr[data-calendar-send]").forEach((row) => {
      const node = row.querySelector("time[datetime]");
      const when = node ? new Date(node.dateTime) : null;
      if (!when || !Number.isFinite(when.getTime())) return;
      const key = localKey(when);
      if (!days.has(key)) days.set(key, []);
      days.get(key).push({when, row});
    });
    return days;
  }

  // One send in a day: its local time and schedule name, a link to its Edit
  // page when it has one, and its status for screen readers (the table shows
  // it to everyone).
  function sendItem({when, row}) {
    const item = document.createElement("li");
    if (row.classList.contains("schedule-past")) item.className = "schedule-past";
    const edit = row.dataset.editUrl;
    const target = document.createElement(edit ? "a" : "span");
    if (edit) target.href = edit;
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

  // A day's Add link (#1521): New scheduled email with ``key`` (this
  // browser's local day, which the send date field is typed in) as its send
  // date. Its visible word is short; its name carries the whole date ("Add
  // an email on October 12, 2054"), so a screen reader, voice control and a
  // keyboard user tabbing through the grid hear which day it adds to.
  function addLink(key) {
    const link = document.createElement("a");
    const url = new URL(root.dataset.addUrl, document.baseURI);
    url.searchParams.set("date", key);
    link.href = url.pathname + url.search;
    link.className = "calendar-add";
    link.textContent = root.dataset.addText;
    link.setAttribute("aria-label", root.dataset.addName.replace("{date}", dates.date(key)));
    return link;
  }

  // Whether ``key`` is one of the campaign's dates (ISO days compare as
  // text); only those get an Add.
  const inCampaign = (key) => Boolean(root.dataset.start && root.dataset.end)
    && root.dataset.start <= key && key <= root.dataset.end;

  // A visible note in a day: Today, Campaign opens or Campaign closes.
  function marker(text) {
    const note = document.createElement("span");
    note.className = "calendar-marker";
    note.textContent = text;
    return note;
  }

  // One day's cell: its number (with Add on a campaign day), its notes and
  // its sends.
  function dayCell(key, day, days, today) {
    const cell = document.createElement("td");
    const head = document.createElement("div");
    head.className = "calendar-day-head";
    const number = document.createElement("span");
    number.className = "calendar-day";
    number.textContent = String(day);
    head.append(number);
    if (root.dataset.addUrl && inCampaign(key)) head.append(addLink(key));
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
      sends.forEach((send) => list.append(sendItem(send)));
      cell.append(list);
    }
    return cell;
  }

  // One month as a table, Sunday first, from the page's template (whose
  // weekday headings are translated); ``month`` is 1-based.
  function monthGrid(year, month, days, today) {
    const region = template.content.firstElementChild.cloneNode(true);
    const caption = region.querySelector("caption");
    caption.id = `calendar-${dayKey(year, month, 1)}`;
    caption.textContent = dates.month(year, month);
    region.setAttribute("aria-labelledby", caption.id);
    const body = region.querySelector("tbody");
    const blank = new Date(year, month - 1, 1).getDay();
    const length = new Date(year, month, 0).getDate();
    let row = null;
    for (let slot = 0; slot < blank + length || slot % 7; slot += 1) {
      if (slot % 7 === 0) {
        row = document.createElement("tr");
        body.append(row);
      }
      const day = slot - blank + 1;
      row.append(day < 1 || day > length
        ? document.createElement("td")
        : dayCell(dayKey(year, month, day), day, days, today));
    }
    return region;
  }

  // Every month from the earliest to the latest of the campaign's dates and
  // its sends: a send late on the last day can fall on the next day here.
  const days = sendsByDay();
  const keys = [...days.keys(), root.dataset.start, root.dataset.end].filter(Boolean).sort();
  if (!keys.length) return;
  const today = localKey(new Date());
  let [year, month] = keys[0].split("-").map(Number);
  const [lastYear, lastMonth] = keys[keys.length - 1].split("-").map(Number);
  while (year < lastYear || (year === lastYear && month <= lastMonth)) {
    root.append(monthGrid(year, month, days, today));
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
  }
})();
