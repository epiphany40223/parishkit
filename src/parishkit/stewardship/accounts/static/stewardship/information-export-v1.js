/* Select a display timezone in-place; never redirect or serialize private filters. */
"use strict";
(() => {
  const select = document.querySelector("[data-information-timezone]");
  if (!select) return;
  let zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) { return; }
  if (Array.from(select.options).some(option => option.value === zone)) select.value = zone;
})();
