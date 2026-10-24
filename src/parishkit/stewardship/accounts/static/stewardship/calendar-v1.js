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
// replaced, so Refresh and Back return to it. The grid's caption names the
// month; nothing beside the buttons repeats it (PR #2095). At an end the
// button stays in place but is unavailable (aria-disabled, so it keeps
// focus), and the reserved line after the buttons says why. Every month
// has six week rows, so a month change never changes the grid's row
// count.
//
// Each day of the campaign's dates that is today or later (browser-local)
// also gets a (+) Add link (#1521) that opens New scheduled email with that
// day as its send date, and every Edit link (grid and table) carries the
// month or week shown (?calendar=), so Edit returns to it. The table's Show
// and sort links carry the month or week shown too (?month=, or
// ?view=week&week=), so the address they leave keeps it; both are set again
// after each in-place swap of the table (parishkit:swap).
//
// Move… (#1521): each movable send (data-move-url) gets a move icon button
// after its link, which opens the Move dialog on that send: its title and
// sentence filled in, its form posting to the send's Edit page, its date the
// send's local day. Review the move is the shared review dialog's
// (ui-v1.js, data-review-open), so the server draws Edit's own review and
// nothing changes before Apply. Its change gate's version is set per send,
// so the gate takes the send's day as the saved value. On a fine pointer, a
// movable send's link or its Move… button can also be dragged (pointer
// events, a few pixels before a drag starts, so a click still follows the
// link or opens Move…) onto another day, which opens the same dialog with
// that day filled in; Escape cancels a drag, and the drop itself changes
// nothing (UX conventions, dragging rule). Enter or Space on Move… is a
// click, never a drag.
// Only a day the send can move to is a target (PR #2095): from the first
// day whose kept time of day is still ahead (and not before the campaign
// opens) through the campaign's last day, as Edit allows no other. While a
// drag is active every other day looks unavailable and takes no drop, and
// Move…'s date field holds the same range (min and max), its Review held
// with the reason in the dialog's reserved line while the date is outside
// it. The server refuses such a day all the same.
// Apply redraws the list of sends with the dialog (one region), so the grid
// is drawn again from the new list on the same month.
//
// View (#1808) is a binary choice before Previous and Next: Month (the
// default) or Week, one row of seven days, Sunday first, drawn from the same
// list with the same day cells, so Add, Move… and drag work the same in it.
// Its segments are links (the shared segmented choice), which this script
// follows itself with no request, before ui-v1.js would fetch them, and sets
// again after each draw to the month or week each would show. The buttons'
// words and reasons follow the view. The week view's address is
// ?view=week&week=<its Sunday>, without ?month=, and its Edit and Add links
// carry ?calendar=<that Sunday>, so New and Edit return to the week. A
// week's first day of a month shows its month ("Oct 1", Administrator
// 2026-10-23), as the row can cross into it.
(() => {
  const root = document.querySelector("[data-calendar]");
  const template = document.getElementById("calendar-month");
  // Pixels the pointer moves before a press on a send becomes a drag.
  const DRAG_THRESHOLD = 5;
  const finePointer = window.matchMedia("(pointer: fine)");
  const dates = window.ParishDates;
  const reason = document.querySelector("[data-calendar-reason]");
  const said = document.querySelector("[data-calendar-said]");
  const steps = Array.from(document.querySelectorAll("[data-calendar-step]"));
  if (!root || !template || !dates || !reason || !said) return;

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
  // An ISO day as the address names one (``2054-10-11``).
  const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
  // A day as one number (days since 1970-01-01, a Thursday), counted in UTC
  // so a daylight-saving change never makes a day 23 or 25 hours long.
  const dayNumber = (key) => {
    const [year, month, day] = key.split("-").map(Number);
    return Date.UTC(year, month - 1, day) / 86400000;
  };
  const numberKey = (number) => {
    const day = new Date(number * 86400000);
    return dayKey(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate());
  };
  // A week (#1808) as one number, Sunday to Saturday (day 3 was a Sunday, so
  // adding 4 puts each Sunday at a multiple of 7), and its Sunday's day.
  const weekIndex = (key) => Math.floor((dayNumber(key) + 4) / 7);
  const weekKey = (index) => numberKey(index * 7 - 4);
  // A translated pattern with {name}-style slots filled in as text (a
  // replacement function, so a "$" in a name is never a pattern).
  const fillIn = (pattern, values) => pattern.replace(/\{(\w+)\}/g,
    (whole, key) => (key in values ? values[key] : whole));
  // The grid's link and Move… button of a movable send (its drag handles),
  // to its send's item in the list.
  const sources = new WeakMap();

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

  // The view shown ("month" or "week", #1808) and its month or week
  // (monthIndex's or weekIndex's number), set by draw.
  let view = "month";
  let shown = null;
  // The month or week shown as Edit's ?calendar= names it: a month
  // (``2054-11``) or the week's Sunday (``2054-10-11``).
  const placeKey = () => (view === "week" ? weekKey(shown) : monthKey(shown));

  // An Edit page's address with the month or week shown, so its Cancel,
  // Apply and Delete return to it (#1656, #1808).
  function editUrl(edit) {
    const url = new URL(edit, document.baseURI);
    url.searchParams.set("calendar", placeKey());
    return url.pathname + url.search;
  }

  // A movable send's move icon (two arrows, drawn inline like the table
  // action icons): decoration, as the button carries the name.
  function moveIcon() {
    const icon = document.createElementNS(SVG, "svg");
    icon.setAttribute("class", "action-icon");
    icon.setAttribute("viewBox", "0 0 16 16");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", "M2 8h12M5 5 2 8l3 3M11 5l3 3-3 3");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.6");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    icon.append(path);
    return icon;
  }

  // A movable send's Move… button, named and titled with the send ("Move
  // Reminder 2…"), so a keyboard, voice control and screen reader user
  // hears which send it moves.
  function moveButton(row) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "calendar-move";
    const label = fillIn(root.dataset.moveName, {name: row.dataset.label});
    button.setAttribute("aria-label", label);
    button.title = label;
    button.append(moveIcon());
    button.addEventListener("click", () => openMove(row, null, button));
    sources.set(button, row);
    return button;
  }

  // One send in a day: its local time and schedule name, a link to its Edit
  // page when it has one, Move… when it can move, and its status for screen
  // readers (the table shows it to everyone). ``row`` is the send's item in
  // the hidden list.
  function sendItem({when, row}) {
    const item = document.createElement("li");
    if (row.classList.contains("schedule-past")) item.className = "schedule-past";
    const edit = row.dataset.editUrl;
    const target = document.createElement(edit ? "a" : "span");
    if (edit) target.href = editUrl(edit);
    const time = document.createElement("time");
    time.dateTime = when.toISOString();
    time.textContent = dates.time(when);
    target.append(time, ` ${row.dataset.label}`);
    const status = document.createElement("span");
    status.className = "visually-hidden";
    status.textContent = `, ${row.dataset.status}`;
    item.append(target, status);
    if (edit && row.dataset.moveUrl && root.dataset.moveName) {
      // The link drags on a fine pointer; the browser's own link drag
      // would take the pointer away from the page's drag.
      target.draggable = false;
      sources.set(target, row);
      item.classList.add("calendar-movable");
      item.append(moveButton(row));
    }
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
  // grid hear which day it adds to. In the week view it also names the week
  // (?calendar=, #1808), so New returns to it rather than to the day's month.
  function addLink(key) {
    const link = document.createElement("a");
    const url = new URL(root.dataset.addUrl, document.baseURI);
    url.searchParams.set("date", key);
    if (view === "week") url.searchParams.set("calendar", placeKey());
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

  // One day's cell: its number (``day``, the text shown: "12", or "Oct 1"
  // in a week), with Add on a campaign day, its notes and its sends.
  function dayCell(key, day, days, today) {
    const cell = document.createElement("td");
    // The day a send dropped here moves to (#1521).
    cell.dataset.day = key;
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
      sends.forEach((send) => list.append(sendItem(send)));
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
        : dayCell(dayKey(year, month, day), day, days, today));
    }
    return region;
  }

  // One week (``index``, weekIndex's, #1808) as the month grid's table with
  // one row of seven days, Sunday first, captioned "Week of" its Sunday. A
  // month's first day shows its month ("Oct 1"), so a week that crosses
  // into it says so.
  function weekGrid(index, days, today) {
    const sunday = weekKey(index);
    const region = template.content.firstElementChild.cloneNode(true);
    region.querySelector("table").classList.add("calendar-week");
    const caption = region.querySelector("caption");
    caption.id = `calendar-week-${sunday}`;
    caption.textContent = fillIn(root.dataset.weekName, {date: dates.date(sunday)});
    region.setAttribute("aria-labelledby", caption.id);
    const row = document.createElement("tr");
    for (let offset = 0; offset < 7; offset += 1) {
      const key = numberKey(dayNumber(sunday) + offset);
      const day = Number(key.slice(8));
      const shown = day === 1 ? dates.monthDay(Number(key.slice(5, 7)), day) : day;
      row.append(dayCell(key, shown, days, today));
    }
    region.querySelector("tbody").append(row);
    return region;
  }

  // The sends by day and the first and last months and weeks (#1808), read
  // from the list; read again when Apply redraws the list (#1521).
  let days = new Map();
  const ends = {month: [0, 0], week: [0, 0]};
  function readSends() {
    days = sendsByDay();
    const keys = [...days.keys(), root.dataset.start, root.dataset.end].filter(Boolean).sort();
    if (!keys.length) return false;
    const [earliest, latest] = [keys[0], keys[keys.length - 1]];
    ends.month = [monthIndex(earliest), monthIndex(latest)];
    ends.week = [weekIndex(earliest), weekIndex(latest)];
    return true;
  }
  // ``index`` kept within the campaign's months or weeks (``kind``).
  const within = (kind, index) => Math.min(ends[kind][1], Math.max(ends[kind][0], index));
  if (!readSends()) return;

  // ``url`` naming ``kind``'s ``index`` as the calendar's address does
  // (#1808): a month as ?month=, a week as ?view=week&week=<its Sunday>,
  // each without the other's parameters.
  function place(url, kind, index) {
    if (kind === "week") {
      url.searchParams.delete("month");
      url.searchParams.set("view", "week");
      url.searchParams.set("week", weekKey(index));
    } else {
      url.searchParams.delete("view");
      url.searchParams.delete("week");
      url.searchParams.set("month", monthKey(index));
    }
    return url;
  }

  // The table's links with the month or week shown: each Edit link's
  // ?calendar=, and its Show segments' and sort headings' place. The table
  // is swapped in place, so they are looked up afresh each time.
  function linkTable() {
    document.querySelectorAll("tr[data-calendar-row][data-edit-url] .table-actions a[href]")
      .forEach((link) => {
        link.href = editUrl(link.closest("tr").dataset.editUrl);
      });
    document.querySelectorAll("[data-table-region] a[data-in-place][href], [data-table-region] th a.sort-link[href]")
      .forEach((link) => {
        const url = place(new URL(link.href, document.baseURI), view, shown);
        link.href = url.pathname + url.search + url.hash;
      });
  }

  // The week Week opens on from month ``index``: this week when today is in
  // that month, else the week of its first day; and the month Month opens on
  // from week ``index``: today's when the week holds today, else its
  // Sunday's. Both are kept within the campaign's range.
  const today = localKey(new Date());
  const weekOf = (index) => within("week",
    monthIndex(today) === index ? weekIndex(today) : weekIndex(`${monthKey(index)}-01`));
  const monthOf = (index) => within("month",
    monthIndex(weekIndex(today) === index ? today : weekKey(index)));

  // The View's segments (#1808), each linking to the view it shows with the
  // month or week it would open on and the address's other choices (the
  // table's Show and sort), the one shown marked current.
  const segments = () => Array.from(document.querySelectorAll("#calendar-view a[data-in-place]"));
  const segmentView = (segment) => (segment.dataset.inPlace === "view-week" ? "week" : "month");
  function linkViews() {
    segments().forEach((segment) => {
      const kind = segmentView(segment);
      const index = kind === view ? shown : (kind === "week" ? weekOf(shown) : monthOf(shown));
      const url = place(new URL(window.location.href), kind, index);
      url.hash = "calendar-source";
      segment.href = url.pathname + url.search + url.hash;
      if (kind === view) segment.setAttribute("aria-current", "page");
      else segment.removeAttribute("aria-current");
    });
  }

  // Draw ``index`` of ``kind`` (a month or a week, #1808): the grid, its
  // name after the buttons, the buttons' words and each available or not
  // (with the reason line), and the table's and the View's links.
  // ``announce`` says the name in the status region, after a button or a
  // segment changed it (``prefix``, the segment's word, comes first), not as
  // the page loads.
  function draw(kind, index, announce, prefix) {
    view = kind;
    shown = index;
    const week = kind === "week";
    root.replaceChildren(week ? weekGrid(index, days, today) : monthGrid(index, days, today));
    // The name announced; the grid's caption shows the same words.
    const label = week
      ? fillIn(root.dataset.weekName, {date: dates.date(weekKey(index))})
      : dates.month(Math.floor(index / 12), (index % 12) + 1);
    const [first, last] = ends[kind];
    steps.forEach((button) => {
      button.textContent = week ? button.dataset.weekLabel : button.dataset.monthLabel;
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
    if (index === first) why.push(week ? reason.dataset.firstWeek : reason.dataset.first);
    if (index === last) why.push(week ? reason.dataset.lastWeek : reason.dataset.last);
    reason.textContent = why.join(" ");
    linkTable();
    linkViews();
    if (announce) said.textContent = prefix ? `${prefix}: ${label}` : label;
  }

  // The address's month or week replaced (never added to the history),
  // keeping the rest of the address; the View's links then follow it.
  function remember() {
    const url = place(new URL(window.location.href), view, shown);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    linkViews();
  }

  // The view and its month or week to open on: the address's, else this
  // browser's current one when it is one of the campaign's, else the first;
  // one outside them opens the nearer end, which the address then names. A
  // week may be named by any of its days; the address then names its
  // Sunday.
  const address = new URLSearchParams(window.location.search);
  const kind = address.get("view") === "week" ? "week" : "month";
  const asked = address.get(kind);
  const valid = Boolean(asked) && (kind === "week" ? ISO_DAY.test(asked) : YEAR_MONTH.test(asked));
  const indexOf = kind === "week" ? weekIndex : monthIndex;
  let start = indexOf(valid ? asked : today);
  if (!valid && start !== within(kind, start)) start = ends[kind][0];
  start = within(kind, start);
  draw(kind, start, false);
  const named = kind === "week" ? weekKey(start) : monthKey(start);
  // The other view's parameters are dropped from the address.
  const stray = kind === "week"
    ? address.has("month")
    : address.has("week") || address.has("view");
  if ((asked && asked !== named) || stray) remember();

  steps.forEach((button) => button.addEventListener("click", () => {
    const next = shown + Number(button.dataset.calendarStep);
    if (button.getAttribute("aria-disabled") === "true" || next !== within(view, next)) return;
    draw(view, next, true);
    remember();
  }));

  // Choosing a View segment (#1808) redraws the grid in place, with no
  // request: capture phase, so ui-v1.js's in-place link handler (bubble
  // phase, which skips a click already handled) never fetches it. A
  // modified click (a new tab or window) keeps its ordinary meaning. Focus
  // stays on the segment, which is never redrawn.
  document.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        || !(event.target instanceof Element)) return;
    const segment = event.target.closest("#calendar-view a[data-in-place]");
    if (!segment) return;
    event.preventDefault();
    const kind = segmentView(segment);
    if (kind === view) return;
    draw(kind, kind === "week" ? weekOf(shown) : monthOf(shown), true, segment.textContent.trim());
    remember();
  }, true);
  // The Move dialog (#1521), looked up afresh: Apply redraws it with the
  // list of sends. ``opener`` takes focus back when it closes unapplied.
  let moveOpener = null;
  const moveDialog = () => document.getElementById("calendar-move");

  // The days the send ``row`` (its item in the list) can move to (PR
  // #2095), as ISO days that compare as text: ``first``, the first day on
  // which its kept browser-local time of day is still ahead (today while
  // that time is to come, else tomorrow), or the campaign's first day if
  // later; and ``last``, the campaign's last day, the bound Edit enforces
  // for an invitation or reminder. Read afresh each time, as the time
  // passes while the page is open.
  function moveRange(row) {
    const when = new Date(row.querySelector("time[datetime]").dateTime);
    const now = new Date();
    const kept = new Date(now.getFullYear(), now.getMonth(), now.getDate(),
      when.getHours(), when.getMinutes(), when.getSeconds());
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + (kept > now ? 0 : 1));
    const start = root.dataset.start || "";
    const ahead = localKey(day);
    return {first: start > ahead ? start : ahead, last: root.dataset.end || ""};
  }
  const canMoveTo = (range, key) => range.first <= key && (!range.last || key <= range.last);

  // Why the Move dialog's date can't be reviewed, or "" when it can: none
  // chosen, before the range (the campaign not yet open, or the kept time
  // already past that day) or after the campaign's last day.
  function moveRefusal(field) {
    const dialog = moveDialog();
    const value = field.value;
    if (!value) return dialog.dataset.moveEmpty;
    const range = {first: field.min, last: field.max};
    const words = {first: dates.date(range.first), last: dates.date(range.last)};
    if (value < range.first) {
      return fillIn(value < (root.dataset.start || "") ? dialog.dataset.moveBefore
        : dialog.dataset.movePassed, words);
    }
    if (range.last && value > range.last) return fillIn(dialog.dataset.moveAfter, words);
    return "";
  }

  // Check the Move dialog's date as it changes (capture phase, so its
  // validity is set before ui-v1.js's complete gate reads it): a date
  // outside the range holds Review the move, the reason said in the
  // dialog's reserved line (which keeps its height, so nothing moves) and
  // the field marked and described by it. A server refusal in that line
  // (data-refusal) is left to ui-v1.js.
  function checkMoveDate(event) {
    const field = event.target;
    if (!(field instanceof HTMLInputElement) || !field.matches("#calendar-move [name='schedule-date']")) return;
    const message = moveRefusal(field);
    field.setCustomValidity(message);
    const line = document.getElementById(field.form.dataset.reviewRefused);
    if (!line || line.hasAttribute("data-refusal")) return;
    if (!message && !line.hasAttribute("data-client-refusal")) return;
    line.textContent = message;
    line.toggleAttribute("data-client-refusal", Boolean(message));
    field.setAttribute("aria-invalid", String(Boolean(message)));
    if (message) field.setAttribute("aria-describedby", line.id);
    else field.removeAttribute("aria-describedby");
  }
  document.addEventListener("input", checkMoveDate, true);
  document.addEventListener("change", checkMoveDate, true);

  // Open the Move dialog on the send ``row`` (its item in the list), its
  // date ``day`` when a drop chose one, else the send's own local day. The
  // date field takes the days it can move to (min and max, moveRange).
  function openMove(row, day, opener) {
    const dialog = moveDialog();
    const form = dialog?.querySelector("form");
    const field = form?.elements.namedItem("schedule-date");
    const version = form?.querySelector("#calendar-move-version");
    const node = row.querySelector("time[datetime]");
    if (!field || !version || !node || dialog.open) return;
    const when = new Date(node.dateTime);
    const name = row.dataset.label;
    form.setAttribute("action", row.dataset.moveUrl);
    dialog.querySelector("[data-move-title]").textContent = fillIn(root.dataset.moveName, {name});
    dialog.querySelector("[data-move-text]").textContent = fillIn(dialog.dataset.moveWhat,
      {name, time: dates.time(when), email: row.dataset.email || ""});
    // The send's day is the saved value the change gate compares with: a
    // new version (this configuration and this send) makes it read the
    // field's default again.
    const own = localKey(when);
    field.defaultValue = own;
    const range = moveRange(row);
    field.min = range.first;
    if (range.last) field.max = range.last;
    else field.removeAttribute("max");
    field.value = day || own;
    version.value = `${form.elements.namedItem("base_digest").value} ${row.dataset.moveUrl}`;
    // A refusal from an earlier Move no longer applies.
    const line = document.getElementById(form.dataset.reviewRefused);
    if (line) {
      line.textContent = "";
      line.removeAttribute("title");
      line.removeAttribute("data-refusal");
      line.removeAttribute("data-client-refusal");
    }
    field.removeAttribute("aria-describedby");
    field.removeAttribute("data-field-error");
    field.setAttribute("aria-invalid", "false");
    moveOpener = opener;
    dialog.showModal();
    field.focus();
    // The gate answers on change (ui-v1.js), as it does for an edit.
    field.dispatchEvent(new Event("change", {bubbles: true}));
  }

  // Cancel closes the Move dialog; Escape closes it natively, but not while
  // its Review is being prepared (the form is busy then).
  document.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("#calendar-move [data-move-cancel]")) {
      moveDialog()?.close();
    }
  });
  document.addEventListener("cancel", (event) => {
    if (event.target === moveDialog() && event.target.querySelector("form[aria-busy=true]")) {
      event.preventDefault();
    }
  }, true);
  // Closed unapplied: focus goes back to what opened it.
  document.addEventListener("close", (event) => {
    if (event.target !== moveDialog()) return;
    if (moveOpener?.isConnected) moveOpener.focus();
    moveOpener = null;
  }, true);

  // Drag to move (#1521; UX conventions, dragging rule), fine pointers only.
  let drag = null;
  let swallowClick = false;
  // Clear a drag's marks. The click that ends an active drag never follows
  // the link or opens Move…: it is swallowed until the next press or key,
  // so an Escape before the mouse button is released (which ends the drag
  // early) is covered.
  function endDrag() {
    if (!drag) return;
    drag.item.classList.remove("calendar-dragging");
    drag.over?.classList.remove("calendar-drop-target");
    root.querySelectorAll("td.calendar-drop-unavailable")
      .forEach((cell) => cell.classList.remove("calendar-drop-unavailable"));
    document.documentElement.classList.remove("calendar-drag-active");
    if (drag.active) swallowClick = true;
    drag = null;
  }
  // A new press or key (other than the Escape that cancels a drag) means
  // the drag's click is past: nothing else is ever swallowed.
  document.addEventListener("pointerdown", () => { swallowClick = false; }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") swallowClick = false;
  }, true);
  root.addEventListener("pointerdown", (event) => {
    if (!finePointer.matches || event.pointerType === "touch" || event.button !== 0) return;
    // A drag starts from a movable send's link or its Move… button.
    const handle = event.target instanceof Element
      ? event.target.closest("a, .calendar-move") : null;
    const row = handle && sources.get(handle);
    if (!row) return;
    drag = {handle, row, item: handle.closest("li"), x: event.clientX, y: event.clientY,
      id: event.pointerId, active: false, over: null, range: null};
  });
  document.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.active) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_THRESHOLD) return;
      drag.active = true;
      // A selection begun in the first few pixels goes with the drag.
      window.getSelection()?.removeAllRanges();
      drag.item.classList.add("calendar-dragging");
      document.documentElement.classList.add("calendar-drag-active");
      // Every day the send can't move to looks unavailable (a background
      // and the not-allowed pointer, so nothing moves) and takes no drop.
      drag.range = moveRange(drag.row);
      root.querySelectorAll("td[data-day]").forEach((cell) => {
        if (!canMoveTo(drag.range, cell.dataset.day)) cell.classList.add("calendar-drop-unavailable");
      });
    }
    event.preventDefault();
    const under = document.elementFromPoint(event.clientX, event.clientY);
    const cell = under instanceof Element ? under.closest("td[data-day]") : null;
    const over = cell && root.contains(cell) && canMoveTo(drag.range, cell.dataset.day)
      ? cell : null;
    if (over === drag.over) return;
    drag.over?.classList.remove("calendar-drop-target");
    over?.classList.add("calendar-drop-target");
    drag.over = over;
  });
  document.addEventListener("pointerup", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const {active, over, row, handle} = drag;
    const own = localKey(new Date(row.querySelector("time[datetime]").dateTime));
    endDrag();
    // Dropped on another day: the Move dialog with that day, nothing more.
    if (active && over && over.dataset.day !== own) openMove(row, over.dataset.day, handle);
  });
  document.addEventListener("pointercancel", (event) => {
    if (drag && event.pointerId === drag.id) endDrag();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !drag?.active) return;
    event.preventDefault();
    endDrag();
  }, true);
  document.addEventListener("click", (event) => {
    if (!swallowClick) return;
    swallowClick = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  document.addEventListener("parishkit:swap", (event) => {
    // Apply redrew the list of sends (#1521): draw the grid again from it,
    // on the month shown (or the nearer end, if the campaign's months
    // changed), without announcing a month the reader did not choose.
    if (event.target instanceof Element && event.target.id === "calendar-source" && readSends()) {
      const index = within(view, shown);
      const moved = index !== shown;
      draw(view, index, false);
      if (moved) remember();
    }
    linkTable();
    // A table swap replaced the address's Show or sort: the View keeps them.
    linkViews();
  });
})();
