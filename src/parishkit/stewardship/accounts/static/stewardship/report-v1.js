/* Resolve browser timezone once; subsequent links retain explicit URL state. */
"use strict";
(() => {
  const form = document.querySelector("[data-report-options]");
  const select = form?.querySelector("select[name=timezone]");
  if (!select) return;
  const url = new URL(window.location.href);
  if (url.searchParams.has("timezone")) return;
  let zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) { return; }
  if (!zone || zone === select.value ||
      !Array.from(select.options).some(option => option.value === zone)) return;
  url.searchParams.set("timezone", zone);
  window.location.replace(url.href);
})();
