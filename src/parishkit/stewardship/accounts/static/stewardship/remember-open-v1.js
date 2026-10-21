"use strict";

// A disclosure that each browser remembers open (#1653; ui-conventions
// "Disclosures"): Ministry requests' By Ministry counts. The page loads this
// file without defer straight after the details element it serves, marked
// data-remember-open with its storage key, so the details opens while the
// page is still being parsed, before what follows it is drawn: a remembered
// open state never moves the page after it shows (no layout shift). Closed
// is the default, so only an "open" marker is stored, and closing removes
// it. Storage can be unavailable (a private window, blocked site data); every
// access is guarded and the disclosure then just starts closed.
(() => {
  const panel = document.currentScript?.previousElementSibling;
  if (!(panel instanceof HTMLDetailsElement) || !panel.dataset.rememberOpen) return;
  const key = `pk-remember-open:${panel.dataset.rememberOpen}`;
  try {
    if (window.localStorage.getItem(key) === "open") panel.open = true;
  } catch (error) { /* Keep it closed. */ }
  panel.addEventListener("toggle", () => {
    try {
      if (panel.open) window.localStorage.setItem(key, "open");
      else window.localStorage.removeItem(key);
    } catch (error) { /* Nothing to remember without storage. */ }
  });
})();
