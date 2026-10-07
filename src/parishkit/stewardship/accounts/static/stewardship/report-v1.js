/* Resolve the browser's time zone; later choices keep it in the URL.

   A report whose address has no timezone shows the server's fallback (UTC).
   When this browser's zone is one of the choices and differs from the zone
   the page shows, the same address with the zone added (every other
   parameter, such as the daily table's page and sort, and the fragment
   kept) is loaded in place (#519): ui-v1.js refreshes the statistics, chart
   and export panels and replaces the address, without a reload. The
   address is followed through a temporary a[data-in-place] link marked
   data-in-place-quiet, so focus stays where it is and nothing is announced:
   the reader did not act.

   It runs as the page loads and again after any in-place refresh. A daily
   table link the reader follows before the zone is applied carries no zone
   (it cancels the request that was applying it), so the page it brings
   would be back in UTC; the zone is applied to that page in turn. */
"use strict";
(() => {
  const form = document.querySelector("form[data-report-options]");
  const select = form?.querySelector("select[name=timezone]");
  if (!select) return;
  let zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) { return; }
  if (!zone || !Array.from(select.options).some(option => option.value === zone)) return;
  // The zone the panels were rendered for: the export panel's, once a
  // refresh has replaced it, else the option the page was rendered with.
  const shown = () => document.querySelector("input[name=browser_timezone]")?.value
    ?? select.querySelector("option[selected]")?.value ?? select.options[0]?.value;
  const apply = () => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("timezone") || shown() === zone) return;
    url.searchParams.set("timezone", zone);
    url.hash = "participation-statistics";
    select.value = zone;
    const link = document.createElement("a");
    link.href = url.href;
    link.hidden = true;
    link.setAttribute("data-in-place", "");
    link.setAttribute("data-in-place-quiet", "");
    form.append(link);
    try {
      link.click();
    } finally {
      link.remove();
    }
  };
  apply();
  // After a refresh: once ui-v1.js has also replaced the address. Several
  // regions are swapped per refresh, so the check runs once for them all.
  let pending = false;
  document.addEventListener("parishkit:swap", () => {
    if (pending) return;
    pending = true;
    window.setTimeout(() => { pending = false; apply(); }, 0);
  });
})();
