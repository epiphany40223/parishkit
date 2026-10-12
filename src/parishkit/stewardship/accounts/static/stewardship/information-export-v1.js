/* Select a display timezone in-place; never redirect or serialize private filters. */
"use strict";
(() => {
  let zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) { return; }
  // Each export's time zone menu starts on this browser's zone when it offers
  // it. An in-place refresh (ui-v1.js's parishkit:swap) draws a fresh menu,
  // as the Family timeline's export form inside its table region is, so the
  // fresh one is preselected too.
  const choose = (root) => root.querySelectorAll("[data-information-timezone]").forEach((select) => {
    if (Array.from(select.options).some(option => option.value === zone)) select.value = zone;
  });
  // A form marked data-keep-choices (the Family timeline's export, which an
  // in-place sort or mode change redraws) keeps what the user picked: each
  // menu's last choice, by id, is put back on the fresh menu when offered.
  const kept = new Map();
  document.addEventListener("change", (event) => {
    const select = event.target;
    if (select instanceof HTMLSelectElement && select.id && select.closest("form[data-keep-choices]")) kept.set(select.id, select.value);
  });
  const restore = (root) => kept.forEach((value, id) => {
    const select = root.querySelector(`form[data-keep-choices] select#${CSS.escape(id)}`);
    if (select && Array.from(select.options).some(option => option.value === value)) select.value = value;
  });
  choose(document);
  document.addEventListener("parishkit:swap", (event) => { choose(event.target); restore(event.target); });
})();
