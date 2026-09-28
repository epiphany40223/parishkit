"use strict";

// The browser half of the parish date format (web/dates.py). The page names
// the Admin-chosen style in <body data-date-format>; every script that shows a
// date or time calls ParishDates.instant() or ParishDates.date() so pages,
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

  // A Date shown in this browser's time zone with the style's paired clock:
  // 12-hour for US styles, 24-hour otherwise. Compact drops the zone name.
  function instant(date, options) {
    const compact = Boolean(options && options.compact);
    const code = style();
    const text = fields(date.getFullYear(), date.getMonth() + 1, date.getDate(), compact);
    const hours = date.getHours(), minutes = two(date.getMinutes());
    const time = code.startsWith("us_")
      ? `${hours % 12 || 12}:${minutes} ${hours < 12 ? "AM" : "PM"}`
      : `${two(hours)}:${minutes}`;
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

  // Rewrite every <time data-local-instant> in scope; data-compact marks the
  // dense-table cells (logs, background work, deliveries).
  function localize(scope) {
    (scope || document).querySelectorAll("time[data-local-instant]").forEach((node) => {
      const date = new Date(node.dateTime);
      if (Number.isFinite(date.getTime())) {
        node.textContent = instant(date, {compact: node.hasAttribute("data-compact")});
      }
    });
  }

  window.ParishDates = Object.freeze({instant, date: calendar, localize, style});
})();
