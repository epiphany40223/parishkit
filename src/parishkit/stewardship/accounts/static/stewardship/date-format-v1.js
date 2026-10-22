"use strict";

// The browser half of the parish date format (web/dates.py). The page names
// the Admin-chosen style in <body data-date-format>; every script that shows a
// date or time calls ParishDates.instant(), .date() or .time() so pages,
// emails and reports all read alike. Keep the table in step with dates.py.
(() => {
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  // code: [full date pattern, compact date pattern]; see dates.py for tokens.
  const STYLES = {
    us_long: ["{month} {day}, {yyyy}", "{mon} {day}, {yyyy}"],
    us_medium: ["{mon} {day}, {yyyy}", "{mon} {day}, {yyyy}"],
    us_numeric: ["{mm}/{dd}/{yyyy}", "{mm}/{dd}/{yy}"],
    eu_long: ["{day} {month} {yyyy}", "{day} {mon} {yyyy}"],
    eu_medium: ["{day} {mon} {yyyy}", "{day} {mon} {yyyy}"],
    eu_numeric: ["{dd}/{mm}/{yyyy}", "{dd}/{mm}/{yy}"],
    eu_dot: ["{dd}.{mm}.{yyyy}", "{dd}.{mm}.{yy}"],
    iso: ["{yyyy}-{mm}-{dd}", "{yyyy}-{mm}-{dd}"]
  };
  // Date.getDay() order, for data-day-time's weekday.
  const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday"];
  const two = (value) => String(value).padStart(2, "0");

  function style() {
    // An unknown or missing code (bootstrap pages) means the default.
    const code = document.body && document.body.dataset.dateFormat;
    return Object.prototype.hasOwnProperty.call(STYLES, code) ? code : "us_long";
  }

  // Calendar fields -> text. Month is 1-based, as in an ISO date.
  function fields(year, month, day, compact) {
    const values = {
      month: MONTHS[month - 1], mon: MONTHS[month - 1].slice(0, 3), day: String(day),
      dd: two(day), mm: two(month), yyyy: String(year).padStart(4, "0"), yy: two(year % 100)
    };
    return STYLES[style()][compact ? 1 : 0].replace(/\{(\w+)\}/g, (_, name) => values[name]);
  }

  function zoneName(date) {
    // The browser's short zone name, e.g. "EST"; omitted when Intl lacks it.
    if (typeof Intl === "undefined") return "";
    const part = new Intl.DateTimeFormat("en-US", {timeZoneName: "short"})
      .formatToParts(date).find((item) => item.type === "timeZoneName");
    return part ? part.value : "";
  }

  // The style's paired clock alone: 12-hour for US styles, 24-hour otherwise.
  // Seconds are added only where exact order matters (System logs).
  function clock(date, seconds) {
    const hours = date.getHours();
    const minutes = two(date.getMinutes()) + (seconds ? `:${two(date.getSeconds())}` : "");
    return style().startsWith("us_")
      ? `${hours % 12 || 12}:${minutes} ${hours < 12 ? "AM" : "PM"}`
      : `${two(hours)}:${minutes}`;
  }

  // A Date shown in this browser's time zone with the style's paired clock.
  // Compact drops the zone name. Precise (System logs) adds seconds and the
  // zone name, since log times are compared exactly; the zone name alone
  // tells the repeated hour apart when clocks fall back ("PDT", then "PST"),
  // so no UTC offset is shown (#635).
  function instant(date, options) {
    const compact = Boolean(options && options.compact);
    const code = style();
    const text = fields(date.getFullYear(), date.getMonth() + 1, date.getDate(), compact);
    if (options && options.precise) {
      const zone = zoneName(date);
      return `${text} ${clock(date, true)}${zone ? " " + zone : ""}`;
    }
    const time = clock(date);
    if (compact) return `${text} ${time}`;
    const worded = code.endsWith("_long") || code.endsWith("_medium");
    const separator = worded ? (code.startsWith("us_") ? " at " : ", ") : " ";
    const zone = zoneName(date);
    return `${text}${separator}${time}${zone ? " " + zone : ""}`;
  }

  // A campaign calendar date ("2027-01-31") is never shifted into a time zone.
  function calendar(value, options) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
    if (!match) return String(value);
    return fields(Number(match[1]), Number(match[2]), Number(match[3]),
      Boolean(options && options.compact));
  }

  // A clock time within the next day, saying "tomorrow" when it falls on
  // this browser's next calendar day ("3:10 PM tomorrow"); any other day
  // gets the full instant, so the clock time alone is never ambiguous.
  function dayTime(date, now) {
    const day = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate());
    const next = day(now);
    next.setDate(next.getDate() + 1);
    const target = day(date).getTime();
    if (target === day(now).getTime()) return clock(date);
    if (target === next.getTime()) return `${clock(date)} tomorrow`;
    return instant(date);
  }

  // This browser's IANA zone, or "" when Intl cannot say.
  const browserZone = (() => {
    try {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
      return zone === "Etc/Unknown" ? "" : zone;
    } catch {
      return "";
    }
  })();

  // A schedule-zone list (components/occurrences.html: the refresh
  // schedule's seven-day list) writes each wall time on the server and marks
  // its instant <time data-other-zone-time datetime=… data-zone=…
  // data-text="({time} your time)">. When this browser is in another zone
  // the words are written with the time in this browser's clock; otherwise
  // the element stays empty (#1129). The list's wall times are 24-hour
  // HH:MM, so this time is too, rather than the style's clock, and the two
  // read alike side by side.
  function otherZoneTimes(scope) {
    (scope || document).querySelectorAll("time[data-other-zone-time]").forEach((node) => {
      const date = new Date(node.dateTime);
      const other = browserZone && browserZone !== node.dataset.zone;
      node.textContent = other && Number.isFinite(date.getTime())
        ? ` ${String(node.dataset.text || "").replace("{time}",
          `${two(date.getHours())}:${two(date.getMinutes())}`)}`
        : "";
    });
  }

  // Rewrite every <time data-local-instant> in scope (and fill every
  // data-other-zone-time, above); data-compact marks
  // Admin table cells (ui-conventions#date-format), data-precise adds
  // seconds and the zone name (System logs), data-time-only a recent
  // instant shown as its clock time ("started 10:00 PM"), data-time-day
  // with it a coming instant that may fall tomorrow (dayTime), and
  // data-day-only an instant's day alone, in this browser's time zone and
  // compact when data-compact is set (the Financial report's Latest
  // response, #1544), and data-day-time a send in the shared schedule
  // preview's words: its weekday, full date and clock time, with no zone
  // name ("Thursday, October 22, 2026, 9:00 AM"; the Pages and emails
  // review's schedule list, #1949).
  function localize(scope) {
    otherZoneTimes(scope);
    (scope || document).querySelectorAll("time[data-local-instant]").forEach((node) => {
      const date = new Date(node.dateTime);
      if (Number.isFinite(date.getTime())) {
        node.textContent = node.hasAttribute("data-day-only")
          ? fields(date.getFullYear(), date.getMonth() + 1, date.getDate(),
            node.hasAttribute("data-compact"))
          : node.hasAttribute("data-day-time")
          ? `${WEEKDAYS[date.getDay()]}, ${fields(date.getFullYear(), date.getMonth() + 1,
            date.getDate(), false)}, ${clock(date)}`
          : node.hasAttribute("data-time-only")
          ? (node.hasAttribute("data-time-day") ? dayTime(date, new Date()) : clock(date))
          : instant(date, {
            compact: node.hasAttribute("data-compact"),
            precise: node.hasAttribute("data-precise")
          });
      }
    });
    // A control whose name carries an instant's day (Roster changes to
    // enter names a change listed twice "…, resolved Oct 5, 2054") cannot
    // hold a <time>: its name is attribute text. The server writes the UTC
    // instant into those attributes and into data-local-day, and it is
    // replaced here with the compact day in this browser's time zone (#558).
    // Once replaced the instant is gone, so running again changes nothing.
    // Only the attributes listed below are rewritten: a new user of
    // data-local-day that puts the instant in another attribute must add
    // that attribute to the list.
    (scope || document).querySelectorAll("[data-local-day]").forEach((node) => {
      const stamp = node.dataset.localDay;
      const date = new Date(stamp);
      if (!stamp || !Number.isFinite(date.getTime())) return;
      const day = fields(date.getFullYear(), date.getMonth() + 1, date.getDate(), true);
      ["aria-label", "title", "data-confirm-name"].forEach((name) => {
        const text = node.getAttribute(name);
        if (text && text.includes(stamp)) node.setAttribute(name, text.split(stamp).join(day));
      });
    });
  }

  // A Date's clock time alone, in this browser's time zone ("Applied at
  // 10:05 PM", the review dialog's status line).
  const time = (date) => clock(date);

  // A month and year, such as a month grid's caption ("October 2054"); month
  // is 1-based. The same in every style, since none abbreviates a lone month.
  const month = (year, number) => `${MONTHS[number - 1]} ${year}`;

  window.ParishDates = Object.freeze({
    instant, date: calendar, time, dayTime, month, localize, style
  });
})();
